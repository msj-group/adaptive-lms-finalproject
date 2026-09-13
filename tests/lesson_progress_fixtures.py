"""Shared fixtures for the Phase 4 / M13 lesson progress test modules.

Kept in one module -- like ``tests/discussion_fixtures.py`` -- so the model,
transaction, route, dashboard, Teacher and migration suites all build the
same classroom. The academic-chain, account and session helpers are the
generic ones M11 already proved, re-exported here.

Most helpers write rows **directly**, so a test about (say) a withdrawn
Enrollment is not also a test of the completion form. The route helpers read
the signed completion token out of the page the server actually rendered
rather than minting one in the test, so they keep proving that the Lesson
page carries a usable token.
"""

import re
from datetime import datetime

from sqlalchemy import event

from app.extensions import db
from app.models import Lesson, LessonProgress, LessonStatus, Unit
from tests.message_fixtures import (  # noqa: F401 -- re-exported for the M13 suites
    ACTIVE,
    ADMIN,
    ARCHIVED,
    PW,
    RESEARCHER,
    STUDENT,
    TEACHER,
    ancestors,
    assign,
    assignment_of,
    enroll,
    enrollment_of,
    fresh_identity,
    hierarchy,
    login_as,
    logout,
    set_status,
    user,
)

PUBLISHED = LessonStatus.PUBLISHED.value
DRAFT = LessonStatus.DRAFT.value
SUSPENDED = "suspended"
WITHDRAWN = "withdrawn"
REMOVED = "removed"

#: Whole-second naive-UTC reference moments.
NOW = datetime(2026, 5, 13, 9, 0, 0)
LATER = datetime(2026, 5, 13, 10, 30, 0)
LATEST = datetime(2026, 5, 13, 12, 0, 0)

STUDENT_DASHBOARD = "/student/dashboard"
TEACHER_DASHBOARD = "/teacher/dashboard"

_STATE = re.compile(r'name="progress_state" value="([^"]+)"')


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def outline_url(group_public_id):
    return f"/student/groups/{group_public_id}/units"


def lesson_url(group_public_id, unit_public_id, lesson_public_id):
    return f"/student/groups/{group_public_id}/units/{unit_public_id}/lessons/{lesson_public_id}"


def complete_url(group_public_id, unit_public_id, lesson_public_id):
    return lesson_url(group_public_id, unit_public_id, lesson_public_id) + "/complete"


def undo_url(group_public_id, unit_public_id, lesson_public_id):
    return lesson_url(group_public_id, unit_public_id, lesson_public_id) + "/undo-complete"


def action_url(action, group_public_id, unit_public_id, lesson_public_id):
    builder = complete_url if action == "complete" else undo_url
    return builder(group_public_id, unit_public_id, lesson_public_id)


def teacher_url(group_public_id):
    return f"/teacher/groups/{group_public_id}/progress"


# ---------------------------------------------------------------------------
# The classroom and its rows, written directly
# ---------------------------------------------------------------------------


def classroom(label="A", student_email="student@example.com", teacher_email="teacher@example.com",
              student_name=None, teacher_name=None):
    """An operational Group with one actively enrolled Student and one
    actively assigned Teacher: ``(student, teacher, group)``."""
    group = hierarchy(label)
    student = user(student_email, STUDENT, name=student_name)
    teacher = user(teacher_email, TEACHER, name=teacher_name)
    enroll(group, student)
    assign(group, teacher)
    return student, teacher, group


def unit(group, title="Unit 1", display_order=0, status=ACTIVE):
    row = Unit(group_id=group.id, title=title, display_order=display_order, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def lesson(unit_row, title="Lesson 1", display_order=0, status=PUBLISHED):
    row = Lesson(
        unit_id=unit_row.id,
        title=title,
        display_order=display_order,
        status=status,
        published_at=NOW if status == PUBLISHED else None,
    )
    db.session.add(row)
    db.session.commit()
    return row


def unpublish(lesson_row):
    lesson_row.status = DRAFT
    lesson_row.published_at = None
    db.session.commit()
    return lesson_row


def progress(student, group, lesson_row, completed_at=None, last_opened_at=None,
             created_at=NOW, version=None):
    """One progress row. The version follows the model's rule unless given:
    1 for a never-completed row, 2 for a row completed once."""
    if version is None:
        version = 2 if completed_at is not None else 1
    row = LessonProgress(
        student_id=student.id,
        group_id=group.id,
        lesson_id=lesson_row.id,
        created_at=created_at,
        completed_at=completed_at,
        last_opened_at=last_opened_at,
        version=version,
    )
    db.session.add(row)
    db.session.commit()
    return row


def progress_of(student_id, group_id, lesson_id):
    db.session.expire_all()
    return LessonProgress.query.filter_by(
        student_id=student_id, group_id=group_id, lesson_id=lesson_id
    ).one_or_none()


def rows():
    """Every progress row as a plain tuple in id order -- for "nothing
    changed" assertions."""
    db.session.expire_all()
    return [
        (row.id, row.student_id, row.group_id, row.lesson_id, row.created_at, row.completed_at,
         row.last_opened_at, row.version)
        for row in LessonProgress.query.order_by(LessonProgress.id).all()
    ]


def lesson_ids(student, group, unit_row, lesson_row):
    """Plain identifiers for one Student's view of one Lesson, safe to use
    after the ORM rows expire."""
    return {
        "student": student.id,
        "student_public_id": student.public_id,
        "group": group.id,
        "gp": group.public_id,
        "unit": unit_row.id,
        "up": unit_row.public_id,
        "lesson": lesson_row.id,
        "lp": lesson_row.public_id,
    }


# ---------------------------------------------------------------------------
# Through the application's own routes
# ---------------------------------------------------------------------------


def state_from(html):
    """The signed completion token the rendered page carries, or ``None``."""
    match = _STATE.search(html)
    return match.group(1) if match else None


def page_state(client, ids):
    return state_from(client.get(lesson_url(ids["gp"], ids["up"], ids["lp"])).get_data(as_text=True))


def post_action(client, action, ids, token=None):
    if token is None:
        token = page_state(client, ids)
    data = {} if token is False else {"progress_state": token}
    return client.post(action_url(action, ids["gp"], ids["up"], ids["lp"]), data=data)


def select_count(client, url, method="get"):
    """``(response, number of SELECT statements)`` for one request."""
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        response = getattr(client, method)(url)
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    writes = [s for s in statements if s.strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    return response, len(selects), writes
