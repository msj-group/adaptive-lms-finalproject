"""Phase 4 / M11 -- the messaging routes.

Access control, the relationship rule, recipient selection, thread
creation, replies, historical read-only access, signed state and replay
defence, rendering safety, bounded pagination, navigation, dashboards and
the private-page response headers.
"""

import re
import time
from datetime import timedelta

import pytest
from flask import render_template
from flask_login import login_user
from itsdangerous.timed import TimestampSigner
from sqlalchemy import event

import tests.message_fixtures as fx
from app.extensions import db
from app.models import (
    AcademicStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Message,
    MessageThread,
    MessageThreadMember,
    User,
    UserStatus,
)
from app.services import message_tokens

_UNVERIFIED = "This form could not be verified"
_SUBJECT_LINK = re.compile(r'href="/messages/threads/[0-9a-f-]{36}">([^<]*)</a>')
_BODY_BLOCK = re.compile(r'white-space: pre-wrap; word-break: break-word">([^<]*)</div>')
_RECIPIENT_ROW = re.compile(r"<td>([^<]*)</td>\s*<td>(Teacher|Student)</td>")


def _html(response):
    return response.get_data(as_text=True)


def _subjects(html):
    return _SUBJECT_LINK.findall(html)


def _bodies(html):
    return _BODY_BLOCK.findall(html)


def _portal_nav(html):
    start = html.index('<nav class="portal-nav"')
    return html[start : html.index("</nav>", start)]


def _standard_pair():
    student, teacher, group = fx.pair(student_name="Sam Student", teacher_name="Tina Teacher")
    return student, teacher, group


# ===========================================================================
# Access control
# ===========================================================================


def test_anonymous_visitors_follow_the_login_redirect(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid = fx.thread(student, teacher).public_id
    for url in (fx.INBOX, fx.NEW, fx.thread_url(pid)):
        response = client.get(url)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
    for url in (fx.NEW, fx.reply_url(pid)):
        response = client.post(url, data={"body": "x", "subject": "x"})
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
    with app.app_context():
        assert fx.counts()["messages"] == 1


@pytest.mark.parametrize("role", [fx.ADMIN, fx.RESEARCHER])
def test_administrators_and_researchers_are_forbidden_everywhere(app, client, role):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        row = fx.thread(student, teacher, subject="Private subject", body="Private body")
        pid, teacher_pid = row.public_id, teacher.public_id
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    for url in (fx.INBOX, fx.NEW, fx.compose_url(teacher_pid), fx.thread_url(pid)):
        response = client.get(url)
        assert response.status_code == 403, url
        assert "Private subject" not in _html(response)
        assert "Private body" not in _html(response)
    for url, data in (
        (fx.NEW, {"recipient": teacher_pid, "subject": "x", "body": "x"}),
        (fx.reply_url(pid), {"body": "x"}),
    ):
        assert client.post(url, data=data).status_code == 403
    with app.app_context():
        assert fx.counts() == {"threads": 1, "members": 2, "messages": 1, "notifications": 0}


@pytest.mark.parametrize("email", ["student@example.com", "teacher@example.com"])
def test_students_and_teachers_reach_the_module_and_members_open_the_thread(app, client, email):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid = fx.thread(student, teacher, body="Visible body").public_id
    fx.login_as(client, email)
    assert client.get(fx.INBOX).status_code == 200
    assert client.get(fx.NEW).status_code == 200
    response = client.get(fx.thread_url(pid))
    assert response.status_code == 200
    assert "Visible body" in _html(response)


def test_a_non_member_receives_a_non_disclosing_404(app, client):
    with app.app_context():
        student, teacher, group = _standard_pair()
        peer = fx.user("peer@example.com", fx.STUDENT, name="Peer Student")
        co_teacher = fx.user("co@example.com", fx.TEACHER, name="Co Teacher")
        fx.enroll(group, peer)
        fx.assign(group, co_teacher)
        pid = fx.thread(student, teacher, subject="Secret subject", body="Secret body").public_id
    for email in ("peer@example.com", "co@example.com"):
        fx.login_as(client, email)
        response = client.get(fx.thread_url(pid))
        assert response.status_code == 404
        html = _html(response)
        for private in ("Secret subject", "Secret body", "Sam Student", "Tina Teacher"):
            assert private not in html
        reply = client.post(fx.reply_url(pid), data={"body": "intrusion"})
        assert reply.status_code == 404
    with app.app_context():
        assert fx.counts()["messages"] == 1


@pytest.mark.parametrize(
    "candidate",
    [
        "not-a-uuid",
        "43f44f1a-e67f-4df1-9465-7a95fcedcd2",
        "43F44F1A-E67F-4DF1-9465-7A95FCEDCD2B",
        "00000000-0000-4000-8000-000000000000",
        "1",
        "%00",
    ],
)
def test_random_and_malformed_thread_ids_are_404(app, client, candidate):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        fx.thread(student, teacher)
    fx.login_as(client, "student@example.com")
    assert client.get(fx.thread_url(candidate)).status_code == 404
    assert client.post(fx.reply_url(candidate), data={"body": "x"}).status_code == 404


def test_an_upper_cased_real_thread_id_is_not_an_alias(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid = fx.thread(student, teacher).public_id
    fx.login_as(client, "student@example.com")
    assert client.get(fx.thread_url(pid.upper())).status_code == 404


# ===========================================================================
# Recipient selection
# ===========================================================================


def _world():
    g1, g2, g_other = fx.hierarchy("One"), fx.hierarchy("Two"), fx.hierarchy("Other")
    student = fx.user("student@example.com", fx.STUDENT, name="Stu Dent")
    peer = fx.user("peer@example.com", fx.STUDENT, name="Pat Peer")
    both = fx.user("both@example.com", fx.TEACHER, name="Ada Both")
    one = fx.user("one@example.com", fx.TEACHER, name="Ben One")
    other = fx.user("other@example.com", fx.TEACHER, name="Cy Other")
    stranger = fx.user("stranger@example.com", fx.STUDENT, name="Sue Stranger")
    fx.enroll(g1, student)
    fx.enroll(g2, student)
    fx.enroll(g1, peer)
    fx.enroll(g_other, stranger)
    fx.assign(g1, both)
    fx.assign(g2, both)
    fx.assign(g1, one)
    fx.assign(g_other, other)
    return g1, g2


def test_a_student_sees_only_currently_permitted_teachers_once_each(app, client):
    with app.app_context():
        _world()
    fx.login_as(client, "student@example.com")
    html = _html(client.get(fx.NEW))
    assert _RECIPIENT_ROW.findall(html) == [("Ada Both", "Teacher"), ("Ben One", "Teacher")]
    for absent in ("Pat Peer", "Cy Other", "Sue Stranger", "@example.com"):
        assert absent not in html


def test_a_teacher_sees_only_currently_permitted_students_once_each(app, client):
    with app.app_context():
        _world()
    fx.login_as(client, "both@example.com")
    html = _html(client.get(fx.NEW))
    assert _RECIPIENT_ROW.findall(html) == [("Pat Peer", "Student"), ("Stu Dent", "Student")]
    for absent in ("Ben One", "Cy Other", "Sue Stranger", "@example.com"):
        assert absent not in html


def _break(kind, student, teacher, group):
    term, level, course = fx.ancestors(group)
    if kind == "enrollment":
        fx.set_status(fx.enrollment_of(group, student), EnrollmentStatus.WITHDRAWN.value)
    elif kind == "assignment":
        fx.set_status(fx.assignment_of(group, teacher), GroupTeacherAssignmentStatus.REMOVED.value)
    elif kind in ("group", "course", "level", "term"):
        fx.set_status({"group": group, "course": course, "level": level, "term": term}[kind],
                      fx.ARCHIVED)
    elif kind == "teacher_account":
        fx.set_status(teacher, UserStatus.SUSPENDED.value)
    elif kind == "teacher_role":
        teacher.role = fx.RESEARCHER
        db.session.commit()
    else:  # pragma: no cover
        raise AssertionError(kind)


_BREAKS = ["enrollment", "assignment", "group", "course", "level", "term", "teacher_account",
           "teacher_role"]


@pytest.mark.parametrize("kind", _BREAKS)
def test_a_broken_relationship_removes_the_recipient_and_blocks_compose(app, client, kind):
    with app.app_context():
        student, teacher, group = _standard_pair()
        teacher_pid = teacher.public_id
        _break(kind, student, teacher, group)
    fx.login_as(client, "student@example.com")
    assert _RECIPIENT_ROW.findall(_html(client.get(fx.NEW))) == []
    response = client.get(fx.compose_url(teacher_pid))
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.NEW)
    assert "not available" in _html(client.get(fx.NEW))


def test_a_suspended_student_has_no_permitted_recipients(app):
    from app.services.message_queries import permitted_recipients

    with app.app_context():
        student, teacher, _ = _standard_pair()
        assert [r["full_name"] for r in permitted_recipients(student.id, fx.STUDENT)] == [
            "Tina Teacher"
        ]
        fx.set_status(student, UserStatus.SUSPENDED.value)
        assert permitted_recipients(student.id, fx.STUDENT) == []
        assert permitted_recipients(teacher.id, fx.TEACHER) == []
        assert permitted_recipients(teacher.id, fx.ADMIN) == []


def test_the_empty_picker_is_helpful_and_leaks_nobody(app, client):
    with app.app_context():
        fx.user("lonely@example.com", fx.STUDENT)
        _world()
    fx.login_as(client, "lonely@example.com")
    html = _html(client.get(fx.NEW))
    assert "No one to message yet" in html
    for name in ("Ada Both", "Ben One", "Cy Other", "Stu Dent"):
        assert name not in html


def test_recipient_search_matches_names_literally_and_is_capped(app, client):
    with app.app_context():
        group = fx.hierarchy("S")
        student = fx.user("student@example.com", fx.STUDENT)
        fx.enroll(group, student)
        for email, name in (("a@example.com", "Alice Smith"), ("b@example.com", "Bob Jones"),
                            ("c@example.com", "100% Sure")):
            fx.assign(group, fx.user(email, fx.TEACHER, name=name))
    fx.login_as(client, "student@example.com")
    names = lambda q: [n for n, _ in _RECIPIENT_ROW.findall(_html(client.get(fx.NEW, query_string={"q": q})))]
    assert names("smith") == ["Alice Smith"]
    assert names("SMITH") == ["Alice Smith"]
    assert names("%") == ["100% Sure"]
    assert names("_") == []
    assert names("nobody") == []
    assert "No matches" in _html(client.get(fx.NEW, query_string={"q": "nobody"}))
    html = _html(client.get(fx.NEW, query_string={"q": "Alice" + " " * 3 + "x" * 200}))
    value = re.search(r'id="recipient-q" name="q" type="search"\s+maxlength="64" value="([^"]*)"', html)
    assert value is not None and len(value.group(1)) == 64


def test_the_recipient_list_is_bounded_to_fifty(app, client):
    with app.app_context():
        group = fx.hierarchy("Big")
        student = fx.user("student@example.com", fx.STUDENT)
        fx.enroll(group, student)
        password_hash = student.password_hash
        teachers = [
            User(email=f"t{i:02d}@example.com", password_hash=password_hash,
                 full_name=f"Teacher {i:02d}", role=fx.TEACHER, status=UserStatus.ACTIVE.value)
            for i in range(55)
        ]
        db.session.add_all(teachers)
        db.session.commit()
        for teacher in teachers:
            db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
        db.session.commit()
    fx.login_as(client, "student@example.com")
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        html = _html(client.get(fx.NEW))
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    rows = _RECIPIENT_ROW.findall(html)
    assert len(rows) == 50
    assert rows[0][0] == "Teacher 00" and rows[-1][0] == "Teacher 49"
    assert "Showing the first 50" in html
    recipient_selects = [p for s, p in statements if "DISTINCT" in s and "group_teacher_assignments" in s]
    assert recipient_selects and all(50 in tuple(p) for p in recipient_selects)


# ===========================================================================
# Creating a thread
# ===========================================================================


@pytest.mark.parametrize("sender", ["student", "teacher"])
def test_creating_a_thread_writes_one_thread_two_members_and_one_message(app, client, sender):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        ids = {"student": student.id, "teacher": teacher.id}
        pids = {"student": student.public_id, "teacher": teacher.public_id}
    recipient = "teacher" if sender == "student" else "student"
    fx.login_as(client, f"{sender}@example.com")
    response = fx.create_via_route(
        client, pids[recipient], subject="  Grammar    question  ", body="  Line one\r\nLine two  "
    )
    assert response.status_code == 302
    pid = fx.location_public_id(response)
    assert response.headers["Location"].endswith(fx.thread_url(pid))
    with app.app_context():
        thread = MessageThread.query.one()
        assert thread.public_id == pid
        assert thread.subject == "Grammar question"
        assert thread.created_by_id == ids[sender]
        assert thread.created_at.microsecond == 0
        members = MessageThreadMember.query.filter_by(thread_id=thread.id).all()
        assert sorted(m.user_id for m in members) == sorted(ids.values())
        roles = sorted(db.session.get(User, m.user_id).role for m in members)
        assert roles == [fx.STUDENT, fx.TEACHER]
        message = Message.query.one()
        assert message.thread_id == thread.id
        assert message.sender_id == ids[sender]
        assert message.body == "Line one\nLine two"
        assert message.created_at == thread.created_at
    follow = client.get(response.headers["Location"])
    assert "Message sent." in _html(follow)


def test_the_same_pair_may_hold_several_separate_threads(app, client):
    with app.app_context():
        _, teacher, _ = _standard_pair()
        teacher_pid = teacher.public_id
    fx.login_as(client, "student@example.com")
    first = fx.location_public_id(fx.create_via_route(client, teacher_pid, subject="One"))
    second = fx.location_public_id(fx.create_via_route(client, teacher_pid, subject="Two"))
    assert first != second
    with app.app_context():
        assert fx.counts()["threads"] == 2
        assert fx.counts()["members"] == 4


def test_a_student_to_student_or_teacher_to_teacher_thread_is_refused(app, client):
    with app.app_context():
        student, teacher, group = _standard_pair()
        peer = fx.user("peer@example.com", fx.STUDENT)
        co_teacher = fx.user("co@example.com", fx.TEACHER)
        admin = fx.user("admin@example.com", fx.ADMIN)
        fx.enroll(group, peer)
        fx.assign(group, co_teacher)
        cases = (
            ("student@example.com", student.public_id, peer.public_id),
            ("teacher@example.com", teacher.public_id, co_teacher.public_id),
            ("student@example.com", student.public_id, admin.public_id),
        )
    for email, actor_pid, target_pid in cases:
        fx.login_as(client, email)
        assert client.get(fx.compose_url(target_pid)).status_code == 302
        with app.app_context():
            # Even a correctly signed token for that pair changes nothing.
            token = message_tokens.make_token(message_tokens.PURPOSE_CREATE, actor_pid, target_pid)
        response = fx.create_via_route(client, target_pid, token=token)
        assert response.status_code == 302
        assert response.headers["Location"].endswith(fx.NEW)
    with app.app_context():
        assert fx.counts() == {"threads": 0, "members": 0, "messages": 0, "notifications": 0}


def test_a_recipient_whose_account_was_suspended_after_the_form_opened_is_refused(app, client):
    with app.app_context():
        _, teacher, _ = _standard_pair()
        teacher_pid, teacher_id = teacher.public_id, teacher.id
    fx.login_as(client, "student@example.com")
    token = fx.compose_state(client, teacher_pid)
    with app.app_context():
        fx.set_status(db.session.get(User, teacher_id), UserStatus.SUSPENDED.value)
    response = fx.create_via_route(client, teacher_pid, token=token)
    assert response.status_code == 302
    with app.app_context():
        assert fx.counts()["threads"] == 0


def test_text_errors_re_render_with_the_typed_text_and_the_same_nonce(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        student_pid, teacher_pid = student.public_id, teacher.public_id
    fx.login_as(client, "student@example.com")
    token = fx.compose_state(client, teacher_pid)
    response = fx.create_via_route(client, teacher_pid, subject="   ", body="Keep <this> text",
                                   token=token)
    assert response.status_code == 200
    html = _html(response)
    assert "Enter a subject." in html
    assert "Keep &lt;this&gt; text" in html
    rerendered = fx.state_from(html)
    with app.app_context():
        original = message_tokens.load_token(
            message_tokens.PURPOSE_CREATE, token, student_pid, teacher_pid)
        kept = message_tokens.load_token(
            message_tokens.PURPOSE_CREATE, rerendered, student_pid, teacher_pid)
        assert original["nonce"] == kept["nonce"]
        assert fx.counts()["threads"] == 0
    for subject, body, error in (
        ("x" * 151, "ok", "at most 150 characters"),
        ("ok", "x" * 5001, "at most 5000 characters"),
        ("ok", "   \n  ", "Enter a message."),
        ("bad\x01", "ok", "cannot be used"),
    ):
        html = _html(fx.create_via_route(client, teacher_pid, subject=subject, body=body,
                                         token=token))
        assert error in html
    ok = fx.create_via_route(client, teacher_pid, subject="x" * 150, body="y" * 5000,
                             token=rerendered)
    assert ok.status_code == 302
    # The original token carries the same nonce, so it is now a replay.
    replay = fx.create_via_route(client, teacher_pid, subject="Other", body="Other", token=token)
    assert replay.status_code == 302
    assert fx.location_public_id(replay) == fx.location_public_id(ok)
    with app.app_context():
        assert fx.counts()["threads"] == 1


def test_state_rotates_after_a_successful_create(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        student_pid, teacher_pid = student.public_id, teacher.public_id
    fx.login_as(client, "student@example.com")
    token = fx.compose_state(client, teacher_pid)
    assert fx.create_via_route(client, teacher_pid, token=token).status_code == 302
    fresh = fx.compose_state(client, teacher_pid)
    with app.app_context():
        used = message_tokens.load_token(message_tokens.PURPOSE_CREATE, token, student_pid, teacher_pid)
        new = message_tokens.load_token(message_tokens.PURPOSE_CREATE, fresh, student_pid, teacher_pid)
        assert used["nonce"] != new["nonce"]


# ===========================================================================
# Signed state on create
# ===========================================================================


def _assert_create_rejected(app, client, recipient_pid, token, body="Draft body"):
    response = fx.create_via_route(client, recipient_pid, subject="Draft subject", body=body,
                                   token=token)
    assert response.status_code == 200
    html = _html(response)
    assert _UNVERIFIED in html
    assert "Draft body" in html
    assert fx.state_from(html) not in (None, token)
    with app.app_context():
        assert fx.counts()["threads"] == 0
        assert fx.counts()["notifications"] == 0


def test_a_missing_or_tampered_create_token_is_rejected(app, client):
    with app.app_context():
        _, teacher, _ = _standard_pair()
        teacher_pid = teacher.public_id
    fx.login_as(client, "student@example.com")
    _assert_create_rejected(app, client, teacher_pid, "")
    token = fx.compose_state(client, teacher_pid)
    _assert_create_rejected(app, client, teacher_pid, token[:-3] + "abc")


def test_an_expired_create_token_is_rejected(app, client, monkeypatch):
    with app.app_context():
        _, teacher, _ = _standard_pair()
        teacher_pid = teacher.public_id
    fx.login_as(client, "student@example.com")
    past = int(time.time()) - message_tokens.TOKEN_MAX_AGE_SECONDS - 60
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: past)
    token = fx.compose_state(client, teacher_pid)
    monkeypatch.undo()
    _assert_create_rejected(app, client, teacher_pid, token)


def test_a_create_token_from_another_user_is_rejected(app, client):
    with app.app_context():
        _, teacher, group = _standard_pair()
        fx.enroll(group, fx.user("peer@example.com", fx.STUDENT))
        teacher_pid = teacher.public_id
    fx.login_as(client, "peer@example.com")
    token = fx.compose_state(client, teacher_pid)
    fx.login_as(client, "student@example.com")
    _assert_create_rejected(app, client, teacher_pid, token)


def test_a_reply_token_cannot_create_a_thread(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid, teacher_pid = fx.thread(student, teacher).public_id, teacher.public_id
        before = fx.counts()
    fx.login_as(client, "student@example.com")
    token = fx.reply_state(client, pid)
    response = fx.create_via_route(client, teacher_pid, token=token)
    assert response.status_code == 200 and _UNVERIFIED in _html(response)
    with app.app_context():
        assert fx.counts() == before


def test_a_create_token_for_a_different_recipient_is_rejected(app, client):
    with app.app_context():
        _, teacher, group = _standard_pair()
        second = fx.user("second@example.com", fx.TEACHER, name="Second Teacher")
        fx.assign(group, second)
        teacher_pid, second_pid = teacher.public_id, second.public_id
    fx.login_as(client, "student@example.com")
    token = fx.compose_state(client, teacher_pid)
    response = fx.create_via_route(client, second_pid, subject="S", body="Draft body", token=token)
    html = _html(response)
    assert response.status_code == 200 and _UNVERIFIED in html
    assert "Second Teacher" in html
    with app.app_context():
        assert fx.counts()["threads"] == 0


def test_a_duplicate_create_submission_creates_one_thread_and_one_notification(app, client):
    with app.app_context():
        _, teacher, _ = _standard_pair()
        teacher_pid = teacher.public_id
    fx.login_as(client, "student@example.com")
    token = fx.compose_state(client, teacher_pid)
    first = fx.create_via_route(client, teacher_pid, token=token)
    second = fx.create_via_route(client, teacher_pid, token=token)
    assert first.status_code == second.status_code == 302
    assert first.headers["Location"] == second.headers["Location"]
    assert "already sent" in _html(client.get(second.headers["Location"]))
    with app.app_context():
        assert fx.counts() == {"threads": 1, "members": 2, "messages": 1, "notifications": 1}


# ===========================================================================
# Replies, read-only history and restoration
# ===========================================================================


def test_a_member_replies_while_the_relationship_holds(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid, teacher_id = fx.thread(student, teacher).public_id, teacher.id
    fx.login_as(client, "teacher@example.com")
    response = fx.reply_via_route(client, pid, body="  Answer\r\nwith two lines ")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.thread_url(pid))
    with app.app_context():
        reply = Message.query.order_by(Message.id.desc()).first()
        assert reply.sender_id == teacher_id
        assert reply.body == "Answer\nwith two lines"
        assert fx.counts()["messages"] == 2
    assert "Reply sent." in _html(client.get(fx.thread_url(pid)))


def test_ending_the_relationship_keeps_history_readable_but_read_only(app, client):
    with app.app_context():
        student, teacher, group = _standard_pair()
        row = fx.thread(student, teacher, subject="History", body="Old message")
        pid, student_id, group_id = row.public_id, student.id, group.id
    fx.login_as(client, "student@example.com")
    token = fx.reply_state(client, pid)
    assert token is not None
    with app.app_context():
        fx.set_status(
            Enrollment.query.filter_by(group_id=group_id, student_id=student_id).one(),
            EnrollmentStatus.WITHDRAWN.value,
        )
        before = fx.counts()
    for email in ("student@example.com", "teacher@example.com"):
        fx.login_as(client, email)
        html = _html(client.get(fx.thread_url(pid)))
        assert "Old message" in html
        assert "read-only" in html
        assert 'name="body"' not in html
        assert 'name="message_state"' not in html
        assert "History" in _html(client.get(fx.INBOX))
    fx.login_as(client, "student@example.com")
    response = fx.reply_via_route(client, pid, body="Too late", token=token)
    assert response.status_code == 302
    assert "no longer reply" in _html(client.get(fx.thread_url(pid)))
    with app.app_context():
        assert fx.counts() == before


def test_restoring_a_valid_relationship_permits_replies_again(app, client):
    with app.app_context():
        student, teacher, group = _standard_pair()
        pid = fx.thread(student, teacher).public_id
        fx.set_status(fx.assignment_of(group, teacher), GroupTeacherAssignmentStatus.REMOVED.value)
        group_id, teacher_id = group.id, teacher.id
    fx.login_as(client, "teacher@example.com")
    assert 'name="body"' not in _html(client.get(fx.thread_url(pid)))
    with app.app_context():
        fx.set_status(
            GroupTeacherAssignment.query.filter_by(group_id=group_id, teacher_id=teacher_id).one(),
            GroupTeacherAssignmentStatus.ACTIVE.value,
        )
    assert fx.reply_via_route(client, pid, body="Back again").status_code == 302
    with app.app_context():
        assert fx.counts()["messages"] == 2


def test_another_still_shared_group_keeps_replies_open(app, client):
    with app.app_context():
        student, teacher, group = _standard_pair()
        second = fx.hierarchy("Second")
        fx.enroll(second, student)
        fx.assign(second, teacher)
        pid = fx.thread(student, teacher).public_id
        fx.set_status(group, fx.ARCHIVED)
    fx.login_as(client, "student@example.com")
    assert fx.reply_via_route(client, pid, body="Still here").status_code == 302
    with app.app_context():
        assert fx.counts()["messages"] == 2


def test_reply_body_errors_re_render_the_conversation_with_the_draft(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid = fx.thread(student, teacher).public_id
    fx.login_as(client, "student@example.com")
    token = fx.reply_state(client, pid)
    response = fx.reply_via_route(client, pid, body="   ", token=token)
    assert response.status_code == 200
    assert "Enter a message." in _html(response)
    response = fx.reply_via_route(client, pid, body="x" * 5001, token=token)
    assert "at most 5000 characters" in _html(response)
    with app.app_context():
        assert fx.counts()["messages"] == 1


# ===========================================================================
# Signed state on reply
# ===========================================================================


def _assert_reply_rejected(app, client, pid, token):
    response = fx.reply_via_route(client, pid, body="Draft reply", token=token)
    assert response.status_code == 200
    html = _html(response)
    assert _UNVERIFIED in html
    assert "Draft reply" in html
    with app.app_context():
        assert fx.counts()["messages"] == 1
        assert fx.counts()["notifications"] == 0


def test_missing_tampered_and_expired_reply_tokens_are_rejected(app, client, monkeypatch):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid = fx.thread(student, teacher).public_id
    fx.login_as(client, "student@example.com")
    _assert_reply_rejected(app, client, pid, "")
    token = fx.reply_state(client, pid)
    _assert_reply_rejected(app, client, pid, "x" + token[1:])
    past = int(time.time()) - message_tokens.TOKEN_MAX_AGE_SECONDS - 60
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: past)
    expired = fx.reply_state(client, pid)
    monkeypatch.undo()
    _assert_reply_rejected(app, client, pid, expired)


def test_cross_user_cross_purpose_and_cross_thread_reply_tokens_are_rejected(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        first = fx.thread(student, teacher, subject="First").public_id
        teacher_pid = teacher.public_id
    fx.login_as(client, "teacher@example.com")
    teacher_token = fx.reply_state(client, first)
    fx.login_as(client, "student@example.com")
    _assert_reply_rejected(app, client, first, teacher_token)
    create_token = fx.compose_state(client, teacher_pid)
    _assert_reply_rejected(app, client, first, create_token)
    with app.app_context():
        student = User.query.filter_by(email="student@example.com").one()
        teacher = User.query.filter_by(email="teacher@example.com").one()
        second = fx.thread(teacher, student, subject="Second", created_at=fx.LATER).public_id
    second_token = fx.reply_state(client, second)
    response = fx.reply_via_route(client, first, body="Draft reply", token=second_token)
    assert response.status_code == 200 and _UNVERIFIED in _html(response)
    with app.app_context():
        assert fx.counts()["messages"] == 2


def test_a_duplicate_reply_submission_appends_one_message_and_one_notification(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid = fx.thread(student, teacher).public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.reply_state(client, pid)
    assert fx.reply_via_route(client, pid, body="Once", token=token).status_code == 302
    assert fx.reply_via_route(client, pid, body="Once", token=token).status_code == 302
    assert "already sent" in _html(client.get(fx.thread_url(pid)))
    with app.app_context():
        assert fx.counts()["messages"] == 2
        assert fx.counts()["notifications"] == 1
    assert fx.reply_state(client, pid) != token


# ===========================================================================
# Reads never write, and there is nothing to edit or delete
# ===========================================================================


def test_get_requests_never_write(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        row = fx.thread(student, teacher)
        fx.add_message(row, teacher)
        pid, teacher_pid = row.public_id, teacher.public_id
        snapshot = [(m.id, m.body, m.created_at) for m in Message.query.order_by(Message.id)]
        thread_snapshot = (row.subject, row.created_at)
    fx.login_as(client, "student@example.com")
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        for url in (fx.INBOX, fx.NEW, fx.compose_url(teacher_pid), fx.thread_url(pid),
                    fx.thread_url(pid) + "?page=3", "/student/dashboard"):
            assert client.get(url).status_code == 200, url
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    writes = [s for s in statements if s.strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == []
    with app.app_context():
        assert [(m.id, m.body, m.created_at) for m in Message.query.order_by(Message.id)] == snapshot
        row = MessageThread.query.one()
        assert (row.subject, row.created_at) == thread_snapshot


def test_there_are_no_edit_delete_or_archive_endpoints(app, client):
    rules = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/messages")
    }
    assert rules == {
        ("/messages", frozenset({"GET"})),
        ("/messages/new", frozenset({"GET"})),
        ("/messages/new", frozenset({"POST"})),
        ("/messages/threads/<thread_public_id>", frozenset({"GET"})),
        ("/messages/threads/<thread_public_id>/reply", frozenset({"POST"})),
    }
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid = fx.thread(student, teacher).public_id
    fx.login_as(client, "student@example.com")
    for method in ("PUT", "PATCH", "DELETE", "POST"):
        assert client.open(fx.thread_url(pid), method=method).status_code == 405
    for suffix in ("/edit", "/delete", "/archive", "/messages"):
        assert client.post(fx.thread_url(pid) + suffix).status_code in (404, 405)
    with app.app_context():
        assert fx.counts()["messages"] == 1


# ===========================================================================
# Inbox and conversation queries
# ===========================================================================


def test_the_inbox_lists_only_own_threads_ordered_by_latest_message(app, client):
    with app.app_context():
        student, teacher, group = _standard_pair()
        peer = fx.user("peer@example.com", fx.STUDENT)
        fx.enroll(group, peer)
        old = fx.thread(student, teacher, subject="Old thread", created_at=fx.NOW)
        fx.thread(teacher, student, subject="Newer thread", created_at=fx.LATER)
        fx.thread(peer, teacher, subject="Not mine", created_at=fx.LATEST)
    fx.login_as(client, "student@example.com")
    assert _subjects(_html(client.get(fx.INBOX))) == ["Newer thread", "Old thread"]
    with app.app_context():
        teacher = User.query.filter_by(email="teacher@example.com").one()
        fx.add_message(MessageThread.query.filter_by(subject="Old thread").one(), teacher,
                       body="Fresh reply", created_at=fx.LATEST)
    html = _html(client.get(fx.INBOX))
    assert _subjects(html) == ["Old thread", "Newer thread"]
    assert "Not mine" not in html
    assert "Fresh reply" in html
    fx.login_as(client, "teacher@example.com")
    # "Old thread"'s reply and "Not mine" share a timestamp; the reply was
    # inserted later, so its higher id breaks the tie.
    assert _subjects(_html(client.get(fx.INBOX))) == ["Old thread", "Not mine", "Newer thread"]


def test_inbox_ties_break_deterministically_by_newest_id(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        fx.thread(student, teacher, subject="First created", created_at=fx.NOW)
        fx.thread(student, teacher, subject="Second created", created_at=fx.NOW)
    fx.login_as(client, "student@example.com")
    for _ in range(3):
        assert _subjects(_html(client.get(fx.INBOX))) == ["Second created", "First created"]


def test_the_inbox_pages_twenty_at_a_time_with_one_extra_row(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        for i in range(25):
            fx.thread(student, teacher, subject=f"Thread {i:02d}",
                      created_at=fx.NOW + timedelta(minutes=i))
    fx.login_as(client, "student@example.com")
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        page_one = _html(client.get(fx.INBOX))
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    assert _subjects(page_one) == [f"Thread {i:02d}" for i in range(24, 4, -1)]
    assert "Older conversations" in page_one and "Newer conversations" not in page_one
    inbox_selects = [p for s, p in statements if "max(messages.id)" in s]
    assert len(inbox_selects) == 1 and tuple(inbox_selects[0])[-2:] == (21, 0)
    page_two = _html(client.get(fx.INBOX, query_string={"page": 2}))
    assert _subjects(page_two) == [f"Thread {i:02d}" for i in range(4, -1, -1)]
    assert "Newer conversations" in page_two and "Older conversations" not in page_two
    for bad in ("99", "abc", "-1", "0"):
        assert len(_subjects(_html(client.get(fx.INBOX, query_string={"page": bad})))) == 20


def test_the_conversation_pages_fifty_at_a_time_in_reading_order(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        row = fx.thread(student, teacher, body="Message 00", created_at=fx.NOW)
        for i in range(1, 60):
            fx.add_message(row, teacher if i % 2 else student, body=f"Message {i:02d}",
                           created_at=fx.NOW + timedelta(minutes=i))
        pid = row.public_id
    fx.login_as(client, "student@example.com")
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        page_one = _html(client.get(fx.thread_url(pid)))
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    assert _bodies(page_one) == [f"Message {i:02d}" for i in range(10, 60)]
    assert "Older messages" in page_one and "Newer messages" not in page_one
    page_selects = [p for s, p in statements if "FROM messages JOIN users" in " ".join(s.split())]
    assert len(page_selects) == 1 and tuple(page_selects[0])[-2:] == (51, 0)
    page_two = _html(client.get(fx.thread_url(pid), query_string={"page": 2}))
    assert _bodies(page_two) == [f"Message {i:02d}" for i in range(0, 10)]
    assert "Newer messages" in page_two and "Older messages" not in page_two


def test_a_conversation_shows_only_its_own_messages_in_stable_order(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        first = fx.thread(student, teacher, subject="A", body="A1", created_at=fx.NOW)
        second = fx.thread(teacher, student, subject="B", body="B1", created_at=fx.NOW)
        fx.add_message(first, teacher, body="A2", created_at=fx.NOW)
        fx.add_message(first, student, body="A3", created_at=fx.NOW)
        fx.add_message(second, student, body="B2", created_at=fx.LATER)
        first_pid, second_pid = first.public_id, second.public_id
    fx.login_as(client, "student@example.com")
    for _ in range(2):
        assert _bodies(_html(client.get(fx.thread_url(first_pid)))) == ["A1", "A2", "A3"]
        assert _bodies(_html(client.get(fx.thread_url(second_pid)))) == ["B1", "B2"]


def _select_count(client, url):
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(url).status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    return len([s for s in statements if s.strip().upper().startswith("SELECT")])


def test_query_counts_do_not_grow_with_threads_or_messages(app, client):
    with app.app_context():
        student, teacher, group = _standard_pair()
        row = fx.thread(student, teacher, created_at=fx.NOW)
        pid = row.public_id
    fx.login_as(client, "student@example.com")
    small = {url: _select_count(client, url) for url in
             (fx.INBOX, fx.thread_url(pid), "/student/dashboard", fx.NEW)}
    with app.app_context():
        student = User.query.filter_by(email="student@example.com").one()
        teacher = User.query.filter_by(email="teacher@example.com").one()
        row = MessageThread.query.one()
        for i in range(14):
            other = fx.user(f"t{i}@example.com", fx.TEACHER)
            shared = fx.hierarchy(f"H{i}")
            fx.assign(shared, other)
            fx.enroll(shared, student)
            fx.thread(student, other, created_at=fx.NOW + timedelta(minutes=i))
            fx.add_message(row, teacher, body=f"More {i}", created_at=fx.LATER + timedelta(minutes=i))
    large = {url: _select_count(client, url) for url in small}
    assert large == small


# ===========================================================================
# Rendering safety and privacy
# ===========================================================================


def test_subjects_names_and_bodies_are_escaped_everywhere(app, client):
    with app.app_context():
        group = fx.hierarchy("X")
        student = fx.user("student@example.com", fx.STUDENT, name="<i>Evil</i> Student")
        teacher = fx.user("teacher@example.com", fx.TEACHER, name="<b>Bold</b> Teacher")
        fx.enroll(group, student)
        fx.assign(group, teacher)
        teacher_pid = teacher.public_id
    fx.login_as(client, "student@example.com")
    response = fx.create_via_route(
        client, teacher_pid, subject="<script>alert('s')</script>",
        body="<img src=x onerror=alert(1)>\nSecond <b>line</b>",
    )
    pid = fx.location_public_id(response)
    pages = [_html(client.get(url)) for url in (fx.thread_url(pid), fx.INBOX, "/student/dashboard", fx.NEW)]
    fx.login_as(client, "teacher@example.com")
    pages += [_html(client.get(url)) for url in (fx.thread_url(pid), fx.INBOX, "/teacher/dashboard", "/notifications")]
    for html in pages:
        assert "<script>alert" not in html
        assert "<img src=x" not in html
        assert "<b>Bold</b>" not in html
        assert "<i>Evil</i>" not in html
    thread_html = pages[0]
    assert "&lt;script&gt;alert(&#39;s&#39;)&lt;/script&gt;" in thread_html
    assert "&lt;img src=x onerror=alert(1)&gt;\nSecond &lt;b&gt;line&lt;/b&gt;" in _bodies(thread_html)


def test_multiline_plain_text_keeps_its_line_breaks(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid = fx.thread(student, teacher, body="First line\n\n\tIndented third").public_id
    fx.login_as(client, "student@example.com")
    assert _bodies(_html(client.get(fx.thread_url(pid)))) == ["First line\n\n\tIndented third"]


def test_no_internal_id_email_or_password_hash_reaches_the_markup(app, client):
    with app.app_context():
        fillers = [fx.user(f"filler{i}@example.com", fx.ADMIN) for i in range(6)]
        for i in range(5):
            fx.thread(fillers[0], fillers[1], subject=f"Filler {i}")
        student, teacher, _ = _standard_pair()
        row = fx.thread(student, teacher)
        for i in range(7):
            fx.add_message(row, teacher, body=f"Filler {i}")
        ids = {student.id, teacher.id, row.id}
        pid, teacher_pid, password_hash = row.public_id, teacher.public_id, student.password_hash
        assert min(ids) > 3
    fx.login_as(client, "student@example.com")
    for url in (fx.thread_url(pid), fx.INBOX, fx.NEW, fx.compose_url(teacher_pid), "/student/dashboard"):
        html = _html(client.get(url))
        assert "@example.com" not in html, url
        assert password_hash not in html
        for value in re.findall(r'(?:href|value|action)="([^"]*)"', html):
            segments = re.split(r"[/?=&#]", value)
            for internal in ids:
                assert str(internal) not in segments, (url, value)


# ===========================================================================
# Navigation, dashboards and response headers
# ===========================================================================


@pytest.mark.parametrize(
    "email, paths",
    [
        ("student@example.com", ["/student/dashboard", "/student/calendar", "/student/search",
                                 "/notifications", "/messages", "/messages/new"]),
        ("teacher@example.com", ["/teacher/dashboard", "/teacher/calendar",
                                 "/teacher/announcements", "/notifications", "/messages"]),
    ],
)
def test_messages_appears_exactly_once_in_portal_navigation(app, client, email, paths):
    with app.app_context():
        _standard_pair()
    fx.login_as(client, email)
    for path in paths:
        nav = _portal_nav(_html(client.get(path)))
        assert nav.count('href="/messages"') == 1, path
        assert nav.count(">Messages</a>") == 1, path
        active = re.findall(r'portal-nav__link--active"\s+href="([^"]+)"', nav)
        if path.startswith("/messages"):
            assert active == ["/messages"], path
        else:
            assert "/messages" not in active, path


@pytest.mark.parametrize("role, expected", [(fx.STUDENT, 1), (fx.TEACHER, 1), (fx.ADMIN, 0),
                                            (fx.RESEARCHER, 0)])
def test_the_shared_portal_header_offers_messages_only_to_students_and_teachers(app, role, expected):
    with app.app_context():
        user_id = fx.user("someone@example.com", role).id
    with app.test_request_context("/"):
        login_user(db.session.get(User, user_id))
        html = render_template("layouts/portal_base.html")
    assert html.count('href="/messages"') == expected


def test_the_administrator_dashboard_gains_no_messages_link(app, client):
    with app.app_context():
        fx.user("admin@example.com", fx.ADMIN)
    fx.login_as(client, "admin@example.com")
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "/messages" not in _html(response)


@pytest.mark.parametrize("email, dashboard, other_prefix", [
    ("student@example.com", "/student/dashboard", "teacher"),
    ("teacher@example.com", "/teacher/dashboard", "student"),
])
def test_dashboards_show_at_most_five_recent_conversations_including_history(
        app, client, email, dashboard, other_prefix):
    with app.app_context():
        student, teacher, group = _standard_pair()
        for i in range(7):
            fx.thread(student, teacher, subject=f"Recent {i}",
                      created_at=fx.NOW + timedelta(minutes=i))
        # The relationship ends; history must still be listed.
        fx.set_status(fx.enrollment_of(group, student), EnrollmentStatus.WITHDRAWN.value)
        fx.set_status(group, fx.ARCHIVED)
    fx.login_as(client, email)
    html = _html(client.get(dashboard))
    section = html[html.index("<h2>Recent conversations</h2>"):]
    section = section[: section.index("</div>\n\n") if "</div>\n\n" in section else len(section)]
    assert _subjects(html) == [f"Recent {i}" for i in range(6, 1, -1)]
    assert "The 5 most recent at most" in html


@pytest.mark.parametrize("email, dashboard", [
    ("lonely-student@example.com", "/student/dashboard"),
    ("lonely-teacher@example.com", "/teacher/dashboard"),
])
def test_dashboards_render_an_empty_conversation_state(app, client, email, dashboard):
    with app.app_context():
        fx.user(email, fx.STUDENT if "student" in email else fx.TEACHER)
    fx.login_as(client, email)
    html = _html(client.get(dashboard))
    assert "<h2>Recent conversations</h2>" in html
    assert "No conversations yet" in html


def test_every_messaging_page_and_dashboard_carries_the_private_headers(app, client):
    with app.app_context():
        student, teacher, _ = _standard_pair()
        pid, teacher_pid = fx.thread(student, teacher).public_id, teacher.public_id
    fx.login_as(client, "student@example.com")
    responses = [client.get(url) for url in (fx.INBOX, fx.NEW, fx.compose_url(teacher_pid),
                                             fx.thread_url(pid), "/student/dashboard")]
    responses.append(fx.create_via_route(client, teacher_pid, token=""))
    responses.append(fx.reply_via_route(client, pid, token=""))
    fx.login_as(client, "teacher@example.com")
    responses += [client.get(url) for url in (fx.INBOX, fx.thread_url(pid), "/teacher/dashboard")]
    for response in responses:
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        assert "Cookie" in response.headers.get("Vary", "")
