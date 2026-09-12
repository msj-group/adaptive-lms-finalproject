"""Phase 4 / M11 -- in-app notifications for private messages.

Exactly one notification for the other member only, never the sender,
never the body; a canonical target that still re-authorizes on open; and
fault isolation that keeps a delivery failure from undoing a sent message.
"""

import logging

import tests.message_fixtures as fx
from app.extensions import db
from app.models import Message, Notification, NotificationKind, User, UserStatus
from app.services import notification_delivery
from app.services.notification_delivery import notify_message_received
from app.services.notification_targets import validate_notification_target

_KIND = NotificationKind.MESSAGE_RECEIVED.value


def test_the_inbox_labels_the_new_kind():
    from app.services.notification_queries import KIND_LABELS

    assert KIND_LABELS[_KIND] == "Message"
    assert set(KIND_LABELS) == {kind.value for kind in NotificationKind}


def test_an_initial_message_notifies_only_the_recipient_without_the_body(app, client):
    with app.app_context():
        student, teacher, _ = fx.pair(student_name="Sam Student", teacher_name="Tina Teacher")
        teacher_pid, teacher_id = teacher.public_id, teacher.id
    fx.login_as(client, "student@example.com")
    response = fx.create_via_route(client, teacher_pid, subject="Essay feedback",
                                   body="SECRET-BODY-CONTENT please check")
    pid = fx.location_public_id(response)
    with app.app_context():
        rows = Notification.query.all()
        assert len(rows) == 1
        row = rows[0]
        assert row.recipient_id == teacher_id
        assert row.kind == _KIND
        assert row.title == "New message"
        assert row.message == "Sam Student sent you a message in the conversation 'Essay feedback'."
        assert "SECRET-BODY-CONTENT" not in row.title + row.message + row.target_path
        assert row.target_path == fx.thread_url(pid)
        assert validate_notification_target(fx.TEACHER, row.target_path) == row.target_path
        assert row.read_at is None


def test_a_reply_notifies_only_the_other_member(app, client):
    with app.app_context():
        student, teacher, _ = fx.pair(teacher_name="Tina Teacher")
        pid, student_id = fx.thread(student, teacher, subject="Question").public_id, student.id
    fx.login_as(client, "teacher@example.com")
    assert fx.reply_via_route(client, pid, body="SECRET-REPLY").status_code == 302
    with app.app_context():
        rows = Notification.query.all()
        assert [(r.recipient_id, r.kind) for r in rows] == [(student_id, _KIND)]
        assert rows[0].message == "Tina Teacher sent you a message in the conversation 'Question'."
        assert rows[0].target_path == fx.thread_url(pid)
        assert validate_notification_target(fx.STUDENT, rows[0].target_path) == rows[0].target_path


def test_a_long_subject_is_clipped_in_the_notification(app):
    with app.app_context():
        student, teacher, _ = fx.pair()
        row = fx.thread(student, teacher, subject="S" * 150)
        message_id = Message.query.filter_by(thread_id=row.id).one().id
        assert notify_message_received(message_id) == 1
        stored = Notification.query.one()
        assert len(stored.message) <= 500
        assert "…" in stored.message
        assert "S" * 80 not in stored.message


def test_a_suspended_recipient_or_an_unknown_message_produces_nothing(app):
    with app.app_context():
        student, teacher, _ = fx.pair()
        row = fx.thread(student, teacher)
        message_id = Message.query.filter_by(thread_id=row.id).one().id
        fx.set_status(teacher, UserStatus.SUSPENDED.value)
        assert notify_message_received(message_id) == 0
        assert notify_message_received(999999) == 0
        assert Notification.query.count() == 0


def test_a_malformed_thread_produces_no_notification(app):
    from app.models import MessageThreadMember

    with app.app_context():
        student, teacher, group = fx.pair()
        extra = fx.user("extra@example.com", fx.TEACHER)
        row = fx.thread(student, teacher)
        db.session.add(MessageThreadMember(thread_id=row.id, user_id=extra.id))
        db.session.commit()
        message_id = Message.query.filter_by(thread_id=row.id).one().id
        assert notify_message_received(message_id) == 0


def test_duplicate_posts_never_duplicate_notifications(app, client):
    with app.app_context():
        student, teacher, _ = fx.pair()
        teacher_pid = teacher.public_id
    fx.login_as(client, "student@example.com")
    token = fx.compose_state(client, teacher_pid)
    pid = fx.location_public_id(fx.create_via_route(client, teacher_pid, token=token))
    fx.create_via_route(client, teacher_pid, token=token)
    reply_token = fx.reply_state(client, pid)
    fx.reply_via_route(client, pid, token=reply_token)
    fx.reply_via_route(client, pid, token=reply_token)
    with app.app_context():
        assert Notification.query.count() == 2
        assert Message.query.count() == 2


def test_a_delivery_failure_never_undoes_the_message(app, client, monkeypatch, caplog):
    with app.app_context():
        student, teacher, _ = fx.pair()
        teacher_pid = teacher.public_id

    def _boom(*args, **kwargs):
        raise RuntimeError("target builder exploded")

    monkeypatch.setattr(notification_delivery, "message_thread_target", _boom)
    fx.login_as(client, "student@example.com")
    with caplog.at_level(logging.ERROR):
        response = fx.create_via_route(client, teacher_pid, subject="Kept",
                                       body="SECRET-BODY-NOT-LOGGED")
    assert response.status_code == 302
    pid = fx.location_public_id(response)
    assert client.get(fx.thread_url(pid)).status_code == 200
    with app.app_context():
        assert fx.counts() == {"threads": 1, "members": 2, "messages": 1, "notifications": 0}
    assert "private message" in caplog.text
    assert "SECRET-BODY-NOT-LOGGED" not in caplog.text


def test_opening_the_notification_lands_on_the_authorized_conversation(app, client):
    with app.app_context():
        student, teacher, _ = fx.pair()
        teacher_pid = teacher.public_id
    fx.login_as(client, "student@example.com")
    pid = fx.location_public_id(fx.create_via_route(client, teacher_pid, body="Open me"))
    with app.app_context():
        notification_pid = Notification.query.one().public_id
    fx.login_as(client, "teacher@example.com")
    response = client.post(f"/notifications/{notification_pid}/open")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.thread_url(pid))
    page = client.get(response.headers["Location"])
    assert page.status_code == 200 and "Open me" in page.get_data(as_text=True)
    with app.app_context():
        assert Notification.query.one().read_at is not None


def test_a_stored_target_can_never_bypass_thread_membership(app, client):
    with app.app_context():
        student, teacher, group = fx.pair()
        outsider = fx.user("outsider@example.com", fx.STUDENT)
        fx.enroll(group, outsider)
        pid = fx.thread(student, teacher, body="Members only").public_id
        planted = Notification(
            recipient_id=outsider.id, kind=_KIND, title="New message",
            message="Planted", target_path=fx.thread_url(pid),
        )
        db.session.add(planted)
        db.session.commit()
        planted_pid = planted.public_id
    fx.login_as(client, "outsider@example.com")
    response = client.post(f"/notifications/{planted_pid}/open")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.thread_url(pid))
    page = client.get(response.headers["Location"])
    assert page.status_code == 404
    assert "Members only" not in page.get_data(as_text=True)


def test_an_arbitrary_stored_messages_path_falls_back_to_the_inbox(app, client):
    with app.app_context():
        student, teacher, _ = fx.pair()
        pid = fx.thread(student, teacher).public_id
        planted = []
        for target in ("/messages/new", f"/messages/threads/{pid}?page=2",
                       f"/messages/threads/{pid}#reply", f"/messages/threads/{pid}/reply",
                       "https://evil.example/messages", "/messages/threads/../../admin"):
            row = Notification(recipient_id=student.id, kind=_KIND, title="T", message="M",
                               target_path=target)
            db.session.add(row)
            db.session.commit()
            planted.append(row.public_id)
    fx.login_as(client, "student@example.com")
    for notification_pid in planted:
        response = client.post(f"/notifications/{notification_pid}/open")
        assert response.status_code == 302
        assert "/notifications" in response.headers["Location"]
