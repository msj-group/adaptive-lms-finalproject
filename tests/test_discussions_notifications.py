"""Phase 4 / M12 -- the one in-app notification Group discussions produce.

Exactly one delivery, on a topic's original creation, to the active
Students currently enrolled in that operational Group; the Group name and
the title only; an exact Student topic target that re-authorizes on open;
nothing for replies, locks, reopens or replays; and fault isolation that
keeps a delivery failure from undoing a created topic.
"""

import logging
import uuid

import pytest

import tests.discussion_fixtures as fx
from app.extensions import db
from app.models import (
    DiscussionTopic,
    EnrollmentStatus,
    Notification,
    NotificationKind,
    UserStatus,
)
from app.services import notification_delivery
from app.services.notification_delivery import notify_discussion_topic_created
from app.services.notification_targets import (
    student_discussion_topic_target,
    validate_discussion_topic_target,
    validate_notification_target,
)

_KIND = NotificationKind.DISCUSSION_TOPIC_CREATED.value


def _recipients():
    return sorted(row.recipient_id for row in Notification.query.all())


def test_the_inbox_labels_the_new_kind():
    from app.services.notification_queries import KIND_LABELS

    assert KIND_LABELS[_KIND] == "Discussion"
    assert set(KIND_LABELS) == {kind.value for kind in NotificationKind}


def test_creation_notifies_exactly_the_active_students_of_that_group(app, client):
    with app.app_context():
        teacher, student, group = fx.classroom(label="Blue")
        co_teacher = fx.user("co@example.com", fx.TEACHER)
        fx.assign(group, co_teacher)
        second = fx.user("second@example.com", fx.STUDENT)
        fx.enroll(group, second)
        withdrawn = fx.user("withdrawn@example.com", fx.STUDENT)
        fx.enroll(group, withdrawn, status=EnrollmentStatus.WITHDRAWN.value)
        suspended = fx.user("suspended@example.com", fx.STUDENT)
        fx.enroll(group, suspended)
        fx.set_status(suspended, UserStatus.SUSPENDED.value)
        elsewhere = fx.user("elsewhere@example.com", fx.STUDENT)
        fx.enroll(fx.hierarchy("Other"), elsewhere)
        fx.user("admin@example.com", fx.ADMIN)
        expected = sorted([student.id, second.id])
        gp = group.public_id
    fx.login_as(client, "teacher@example.com")
    response = fx.create_via_route(client, gp, title="Our class trip",
                                   body="SECRET-TOPIC-BODY where should we go?")
    assert response.status_code == 302
    tp = fx.location_public_id(response)
    with app.app_context():
        assert _recipients() == expected
        for row in Notification.query.all():
            assert row.kind == _KIND
            assert row.title == "New discussion topic"
            assert row.message == (
                "A new discussion topic was started in the group 'Group Blue': 'Our class trip'."
            )
            assert "SECRET-TOPIC-BODY" not in row.title + row.message + row.target_path
            assert row.target_path == fx.student_topic(gp, tp)
            assert validate_notification_target(fx.STUDENT, row.target_path) == row.target_path
            assert row.read_at is None


def test_a_student_in_the_group_through_one_enrollment_is_notified_once(app):
    with app.app_context():
        teacher, student, group = fx.classroom()
        row = fx.topic(group, teacher)
        assert notify_discussion_topic_created(row.id) == 1
        assert _recipients() == [student.id]


def test_replies_locks_reopens_and_replays_never_notify(app, client):
    with app.app_context():
        _, _, group = fx.classroom()
        gp = group.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    first = fx.create_via_route(client, gp, token=token)
    tp = fx.location_public_id(first)
    assert fx.create_via_route(client, gp, token=token).headers["Location"] == first.headers[
        "Location"]
    with app.app_context():
        assert Notification.query.count() == 1
    assert fx.reply_via_route(client, fx.TEACHER, gp, tp).status_code == 302
    assert fx.moderate_via_route(client, gp, tp, "lock").status_code == 302
    assert fx.moderate_via_route(client, gp, tp, "reopen").status_code == 302
    fx.login_as(client, "student@example.com")
    assert fx.reply_via_route(client, fx.STUDENT, gp, tp).status_code == 302
    with app.app_context():
        assert Notification.query.count() == 1
        assert DiscussionTopic.query.count() == 1


def test_an_archived_chain_or_unknown_topic_produces_nothing(app):
    with app.app_context():
        teacher, _, group = fx.classroom()
        row = fx.topic(group, teacher)
        _, _, course = fx.ancestors(group)
        fx.set_status(course, fx.ARCHIVED)
        assert notify_discussion_topic_created(row.id) == 0
        assert notify_discussion_topic_created(999999) == 0
        assert Notification.query.count() == 0


def test_a_group_without_active_students_produces_nothing(app):
    with app.app_context():
        teacher, student, group = fx.classroom()
        fx.set_status(fx.enrollment_of(group, student), EnrollmentStatus.WITHDRAWN.value)
        row = fx.topic(group, teacher)
        assert notify_discussion_topic_created(row.id) == 0


def test_a_delivery_failure_never_undoes_the_topic(app, client, monkeypatch, caplog):
    with app.app_context():
        _, _, group = fx.classroom()
        gp = group.public_id

    def _boom(*args, **kwargs):
        raise RuntimeError("target builder exploded")

    monkeypatch.setattr(notification_delivery, "student_discussion_topic_target", _boom)
    fx.login_as(client, "teacher@example.com")
    with caplog.at_level(logging.ERROR):
        response = fx.create_via_route(client, gp, title="Kept", body="SECRET-BODY-NOT-LOGGED")
    assert response.status_code == 302
    tp = fx.location_public_id(response)
    assert client.get(fx.teacher_topic(gp, tp)).status_code == 200
    with app.app_context():
        assert fx.counts() == {"topics": 1, "replies": 0, "notifications": 0}
    assert "discussion topic creation" in caplog.text
    assert "SECRET-BODY-NOT-LOGGED" not in caplog.text


def test_opening_the_notification_lands_on_the_authorized_topic(app, client):
    with app.app_context():
        _, _, group = fx.classroom()
        gp = group.public_id
    fx.login_as(client, "teacher@example.com")
    tp = fx.location_public_id(fx.create_via_route(client, gp, title="Open me"))
    with app.app_context():
        notification_pid = Notification.query.one().public_id
    fx.login_as(client, "student@example.com")
    response = client.post(f"/notifications/{notification_pid}/open")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.student_topic(gp, tp))
    page = client.get(response.headers["Location"])
    assert page.status_code == 200 and "Open me" in page.get_data(as_text=True)
    with app.app_context():
        assert Notification.query.one().read_at is not None


@pytest.mark.parametrize("ending", ["withdrawn", "archived"])
def test_a_historical_notification_grants_no_access(app, client, ending):
    with app.app_context():
        teacher, student, group = fx.classroom()
        row = fx.topic(group, teacher, body="Current members only")
        assert notify_discussion_topic_created(row.id) == 1
        notification_pid = Notification.query.one().public_id
        if ending == "withdrawn":
            fx.set_status(fx.enrollment_of(group, student), EnrollmentStatus.WITHDRAWN.value)
        else:
            fx.set_status(group, fx.ARCHIVED)
    fx.login_as(client, "student@example.com")
    response = client.post(f"/notifications/{notification_pid}/open")
    assert response.status_code == 302
    page = client.get(response.headers["Location"])
    assert page.status_code == 404
    assert "Current members only" not in page.get_data(as_text=True)


def test_a_planted_target_for_another_group_cannot_bypass_enrollment(app, client):
    with app.app_context():
        teacher, _, group = fx.classroom()
        row = fx.topic(group, teacher, body="Members only")
        outsider = fx.user("outsider@example.com", fx.STUDENT)
        fx.enroll(fx.hierarchy("Outside"), outsider)
        planted = Notification(
            recipient_id=outsider.id, kind=_KIND, title="New discussion topic",
            message="Planted", target_path=fx.student_topic(group.public_id, row.public_id),
        )
        db.session.add(planted)
        db.session.commit()
        planted_pid = planted.public_id
    fx.login_as(client, "outsider@example.com")
    response = client.post(f"/notifications/{planted_pid}/open")
    assert response.status_code == 302
    page = client.get(response.headers["Location"])
    assert page.status_code == 404
    assert "Members only" not in page.get_data(as_text=True)


def test_an_unsafe_stored_discussion_target_falls_back_to_the_inbox(app, client):
    with app.app_context():
        teacher, student, group = fx.classroom()
        row = fx.topic(group, teacher)
        gp, tp = group.public_id, row.public_id
        planted = []
        for target in (fx.teacher_topic(gp, tp), fx.student_list(gp), fx.student_reply(gp, tp),
                       fx.student_topic(gp, tp) + "?page=2", fx.student_topic(gp, tp) + "#reply"):
            notification = Notification(recipient_id=student.id, kind=_KIND, title="T",
                                        message="M", target_path=target)
            db.session.add(notification)
            db.session.commit()
            planted.append(notification.public_id)
    fx.login_as(client, "student@example.com")
    for notification_pid in planted:
        response = client.post(f"/notifications/{notification_pid}/open")
        assert response.status_code == 302
        location = response.headers["Location"]
        assert "/notifications" in location and "/discussions" not in location


# ===========================================================================
# The exact-shape target rule
# ===========================================================================


_G = str(uuid.uuid4())
_T = str(uuid.uuid4())


def test_the_builder_produces_the_exact_student_topic_path(app):
    with app.test_request_context("/"):
        target = student_discussion_topic_target(_G, _T)
        assert target == f"/student/groups/{_G}/discussions/{_T}"
        assert validate_discussion_topic_target(fx.STUDENT, target) == target
        for bad in ("not-a-uuid", _G.upper(), "../../teacher"):
            with pytest.raises(ValueError):
                student_discussion_topic_target(bad, _T)
            with pytest.raises(ValueError):
                student_discussion_topic_target(_G, bad)


@pytest.mark.parametrize("candidate", [
    f"/teacher/groups/{_G}/discussions/{_T}",
    "/student/discussions",
    f"/student/groups/{_G}/discussions",
    f"/student/groups/{_G}/discussions/new",
    f"/student/groups/{_G}/discussions/{_T}/",
    f"/student/groups/{_G}/discussions/{_T}/reply",
    f"/student/groups/{_G}/discussions/{_T}?page=2",
    f"/student/groups/{_G}/discussions/{_T}#reply-{_T}",
    f"/student/groups/{_G.upper()}/discussions/{_T}",
    f"/student/groups/{_G}/discussions/{_T.upper()}",
    f"/student/groups/{_G}/discussions/{_T}/../../../teacher/dashboard",
    f"/student/groups/{_G}/units/{_T}/discussions/{_T}",
    f"//evil.example/student/groups/{_G}/discussions/{_T}",
    f"https://evil.example/student/groups/{_G}/discussions/{_T}",
    f"/student/groups/{_G}/discussions/{_T}\n",
    f"/student/groups/{_G}\\discussions/{_T}",
    f"/studentx/groups/{_G}/discussions/{_T}",
])
def test_every_other_discussion_shaped_value_is_rejected(candidate):
    assert validate_notification_target(fx.STUDENT, candidate) is None
    assert validate_discussion_topic_target(fx.STUDENT, candidate) is None


@pytest.mark.parametrize("role", [fx.TEACHER, fx.ADMIN, fx.RESEARCHER, "unknown"])
def test_only_a_student_may_hold_a_discussion_target(role):
    exact = f"/student/groups/{_G}/discussions/{_T}"
    assert validate_notification_target(fx.STUDENT, exact) == exact
    assert validate_notification_target(role, exact) is None


def test_the_discussion_rule_does_not_change_other_targets():
    assert validate_notification_target(fx.STUDENT, "/student/dashboard") == "/student/dashboard"
    assert validate_notification_target(fx.TEACHER, "/teacher/dashboard") == "/teacher/dashboard"
    assert validate_notification_target(
        fx.STUDENT, f"/messages/threads/{_T}") == f"/messages/threads/{_T}"
