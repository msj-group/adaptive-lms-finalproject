"""Shared domain builders for the M14 notification tests.

Deliberately mirrors the small ad-hoc builders already used by the M09 /
M11 / M12 / M13 test modules (a term -> level -> course -> group chain,
plus memberships and content) rather than inventing a new fixture style.
Every builder commits, so a test can hand plain ids to a producer exactly
the way a route does.
"""

from datetime import date, datetime, time, timedelta, timezone

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
    Lesson,
    LessonStatus,
    Level,
    Material,
    MaterialKind,
    Notification,
    NotificationKind,
    Schedule,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password

PW = "Sup3rSecret!123"

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


def user(email, role, status=UserStatus.ACTIVE.value, full_name=None):
    row = User(
        email=email,
        password_hash=hash_password(PW),
        full_name=full_name or email.split("@")[0],
        role=role,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def hierarchy(
    group_name="Group A",
    course_title="English",
    term_status=AcademicStatus.ACTIVE.value,
    level_status=AcademicStatus.ACTIVE.value,
    course_status=AcademicStatus.ACTIVE.value,
    group_status=AcademicStatus.ACTIVE.value,
    capacity=20,
):
    tag = _next()
    term = AcademicTerm(
        name=f"Term {tag}",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=term_status,
    )
    level = Level(name=f"Level {tag}", display_order=tag, status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(
        title=course_title, level_id=level.id, display_order=tag, status=course_status
    )
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id,
        course_id=course.id,
        name=group_name,
        capacity=capacity,
        status=group_status,
    )
    db.session.add(group)
    db.session.commit()
    return group


def enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    row = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(
        group_id=group.id, teacher_id=teacher.id, status=status
    )
    db.session.add(row)
    db.session.commit()
    return row


def schedule(group, day=0, status=AcademicStatus.ACTIVE.value):
    row = Schedule(
        group_id=group.id,
        day_of_week=day,
        start_time=time(9, 0),
        end_time=time(10, 30),
        effective_start_date=date(2026, 1, 5),
        effective_end_date=date(2026, 6, 30),
        location="Room 1",
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def unit(group, title=None, status=AcademicStatus.ACTIVE.value, display_order=0):
    row = Unit(
        group_id=group.id,
        title=title or f"Unit {_next()}",
        display_order=display_order,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def lesson(owning_unit, title=None, status=LessonStatus.DRAFT.value, display_order=0):
    published_at = (
        datetime.now(timezone.utc) if status == LessonStatus.PUBLISHED.value else None
    )
    row = Lesson(
        unit_id=owning_unit.id,
        title=title or f"Lesson {_next()}",
        display_order=display_order,
        status=status,
        published_at=published_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def material(
    owning_lesson,
    title=None,
    status=AcademicStatus.ACTIVE.value,
    kind=MaterialKind.RICH_TEXT.value,
    display_order=1,
):
    tag = _next()
    row = Material(
        lesson_id=owning_lesson.id,
        title=title or f"Material {tag}",
        kind=kind,
        content_html="<p>body</p>" if kind == MaterialKind.RICH_TEXT.value else None,
        external_url=(
            "https://example.org/x" if kind == MaterialKind.EXTERNAL_LINK.value else None
        ),
        status=status,
        display_order=display_order,
        creation_nonce=f"nonce-{tag}",
    )
    db.session.add(row)
    db.session.commit()
    return row


def notification(
    recipient,
    kind=NotificationKind.ENROLLMENT_ACTIVATED.value,
    title="Enrollment activated",
    message="You are now enrolled.",
    target_path="/student/dashboard",
    created_at=None,
    read_at=None,
):
    row = Notification(
        recipient_id=recipient.id,
        kind=kind,
        title=title,
        message=message,
        target_path=target_path,
        created_at=created_at or datetime(2026, 5, 1, 12, 0, 0),
        read_at=read_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def many_notifications(recipient, count, base=None, read=False):
    """`count` notifications with strictly decreasing `created_at`, so
    newest-first ordering and pagination are deterministic.
    """
    base = base or datetime(2026, 5, 1, 12, 0, 0)
    rows = []
    for i in range(count):
        rows.append(
            Notification(
                recipient_id=recipient.id,
                kind=NotificationKind.SCHEDULE_CHANGED.value,
                title=f"Notice {i:03d}",
                message=f"Message {i:03d}",
                target_path="/student/dashboard",
                created_at=base - timedelta(minutes=i),
                read_at=base if read else None,
            )
        )
    db.session.add_all(rows)
    db.session.commit()
    return rows


def kinds():
    return [k.value for k in NotificationKind]


ROLES = {
    "student": UserRole.STUDENT.value,
    "teacher": UserRole.TEACHER.value,
    "administrator": UserRole.ADMINISTRATOR.value,
    "researcher": UserRole.RESEARCHER.value,
}
