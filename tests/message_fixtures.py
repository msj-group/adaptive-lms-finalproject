"""Shared fixtures for the Phase 4 / M11 private messaging test modules.

Kept in one module -- like ``tests/announcement_fixtures.py`` and
``tests/calendar_fixtures.py`` -- so the model, query, transaction, route,
notification and migration suites all build the same academic chain and
the same threads.

Most helpers write rows **directly**, so a test about (say) historical
access is not also a test of the compose form. The route-driven helpers
at the bottom read the signed state token out of the page the server
actually rendered rather than minting one in the test, so they keep
proving that the page carries a usable token.
"""

import re
import secrets
from datetime import date, datetime

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Message,
    MessageThread,
    MessageThreadMember,
    Notification,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password

PW = "Sup3rSecret!123"

STUDENT = UserRole.STUDENT.value
TEACHER = UserRole.TEACHER.value
ADMIN = UserRole.ADMINISTRATOR.value
RESEARCHER = UserRole.RESEARCHER.value
ACTIVE = AcademicStatus.ACTIVE.value
ARCHIVED = AcademicStatus.ARCHIVED.value

INBOX = "/messages"
NEW = "/messages/new"

#: Whole-second naive-UTC reference moments.
NOW = datetime(2026, 5, 13, 9, 0, 0)
LATER = datetime(2026, 5, 13, 10, 30, 0)
LATEST = datetime(2026, 5, 13, 12, 0, 0)

_SEQUENCE = {"n": 0}

_STATE = re.compile(r'name="message_state" value="([^"]+)"')


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


def thread_url(public_id):
    return f"/messages/threads/{public_id}"


def reply_url(public_id):
    return f"/messages/threads/{public_id}/reply"


def compose_url(recipient_public_id):
    return f"/messages/new?to={recipient_public_id}"


def nonce():
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def fresh_identity():
    """Drop Flask-Login's per-request user cache so a second login in the
    same app context really switches accounts."""
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def login_as(client, email, password=PW):
    fresh_identity()
    client.post("/auth/logout", follow_redirects=True)
    fresh_identity()
    return client.post(
        "/auth/login", data={"email": email, "password": password}, follow_redirects=True
    )


def logout(client):
    fresh_identity()
    return client.post("/auth/logout", follow_redirects=True)


# ---------------------------------------------------------------------------
# People and the academic chain
# ---------------------------------------------------------------------------


def user(email, role, status=UserStatus.ACTIVE.value, name=None):
    row = User(
        email=email.strip().lower(),
        password_hash=hash_password(PW),
        full_name=name or email.split("@")[0],
        role=role,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def hierarchy(label=None, term_status=ACTIVE, level_status=ACTIVE, course_status=ACTIVE,
              group_status=ACTIVE):
    """One complete ``Term -> Level -> Course -> Group`` chain, returned as
    the Group; each link's status can be set independently."""
    tag = label or str(_next())
    term = AcademicTerm(
        name=f"Term {tag}-{_next()}",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=term_status,
    )
    level = Level(name=f"Level {tag}-{_next()}", display_order=_next(), status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(
        title=f"Course {tag}", level_id=level.id, display_order=_next(), status=course_status
    )
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id,
        course_id=course.id,
        name=f"Group {tag}",
        capacity=30,
        status=group_status,
    )
    db.session.add(group)
    db.session.commit()
    return group


def enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    row = Enrollment(group_id=group.id, student_id=student.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def pair(label="A", student_email="student@example.com", teacher_email="teacher@example.com",
         student_name=None, teacher_name=None):
    """A Student and a Teacher who share one operational Group."""
    group = hierarchy(label)
    student = user(student_email, STUDENT, name=student_name)
    teacher = user(teacher_email, TEACHER, name=teacher_name)
    enroll(group, student)
    assign(group, teacher)
    return student, teacher, group


def set_status(row, status):
    row.status = status
    db.session.commit()
    return row


def enrollment_of(group, student):
    return Enrollment.query.filter_by(group_id=group.id, student_id=student.id).one()


def assignment_of(group, teacher):
    return GroupTeacherAssignment.query.filter_by(group_id=group.id, teacher_id=teacher.id).one()


def ancestors(group):
    """``(term, level, course)`` of a Group."""
    course = db.session.get(Course, group.course_id)
    return (
        db.session.get(AcademicTerm, group.academic_term_id),
        db.session.get(Level, course.level_id),
        course,
    )


# ---------------------------------------------------------------------------
# Threads, written directly
# ---------------------------------------------------------------------------


def thread(creator, other, subject="Homework question", body="Hello, I have a question.",
           created_at=NOW):
    """One well-formed thread with both members and its first message."""
    row = MessageThread(
        created_by_id=creator.id,
        subject=subject,
        creation_nonce=nonce(),
        created_at=created_at,
    )
    db.session.add(row)
    db.session.flush()
    for user_id in sorted((creator.id, other.id)):
        db.session.add(
            MessageThreadMember(thread_id=row.id, user_id=user_id, joined_at=created_at)
        )
    db.session.add(
        Message(
            thread_id=row.id,
            sender_id=creator.id,
            body=body,
            creation_nonce=nonce(),
            created_at=created_at,
        )
    )
    db.session.commit()
    return row


def add_message(thread_row, sender, body="A reply.", created_at=LATER):
    row = Message(
        thread_id=thread_row.id,
        sender_id=sender.id,
        body=body,
        creation_nonce=nonce(),
        created_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def counts():
    return {
        "threads": MessageThread.query.count(),
        "members": MessageThreadMember.query.count(),
        "messages": Message.query.count(),
        "notifications": Notification.query.count(),
    }


# ---------------------------------------------------------------------------
# Through the application's own routes
# ---------------------------------------------------------------------------


def state_from(html):
    """The signed state token the rendered page carries, or ``None``."""
    match = _STATE.search(html)
    return match.group(1) if match else None


def compose_state(client, recipient_public_id):
    return state_from(client.get(compose_url(recipient_public_id)).get_data(as_text=True))


def create_via_route(client, recipient_public_id, subject="Question", body="Hello there.",
                     token=None):
    """POST the compose form with the token from the rendered page."""
    if token is None:
        token = compose_state(client, recipient_public_id)
    return client.post(
        NEW,
        data={
            "recipient": recipient_public_id,
            "subject": subject,
            "body": body,
            "message_state": token,
        },
    )


def reply_state(client, thread_public_id):
    return state_from(client.get(thread_url(thread_public_id)).get_data(as_text=True))


def reply_via_route(client, thread_public_id, body="A reply.", token=None):
    if token is None:
        token = reply_state(client, thread_public_id)
    return client.post(
        reply_url(thread_public_id), data={"body": body, "message_state": token}
    )


def location_public_id(response):
    return response.headers["Location"].rstrip("/").split("/")[-1].split("?")[0]
