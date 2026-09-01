"""Read-only query helpers backing the role dashboards (M09).

Flask-independent: plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring the other ``app/services``
modules. **Every function here is read-only** -- no locks, no writes.

Design rules (Part M09 sec. 7):

- all Student/Teacher ownership scoping is expressed in SQL, never by
  filtering an unscoped result later;
- ``Group -> Course -> Level`` and ``Group -> AcademicTerm`` are always
  eager-loaded;
- per-row membership / schedule lookups are replaced by grouped
  aggregates and a single batched schedule fetch;
- a Schedule occurrence is *operational* only when the Schedule, its
  Group, and that Group's AcademicTerm, Course, and Level are all active;
  a legacy inconsistent ancestor status is treated honestly as
  non-operational;
- an "eligible" Student/Teacher count requires the expected ``User.role``
  **and** an active ``User.status``.
"""

from sqlalchemy import func
from sqlalchemy.orm import joinedload

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
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.services.schedule_occurrences import (
    current_or_next_occurrence,
    earliest_occurrence,
    slot_spec,
    upcoming_occurrences,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _schedule_context(schedule, group):
    """A plain dict of everything a dashboard template needs to describe
    one class occurrence -- so templates never touch the ORM. `group`
    must be an already eager-loaded row (``group.course.level`` and
    ``group.academic_term`` resolved)."""
    return {
        "group_name": group.name,
        "group_public_id": group.public_id,
        "course_title": group.course.title,
        "level_name": group.course.level.name,
        "term_name": group.academic_term.name,
        "location": schedule.location,
        "day_of_week": schedule.day_of_week,
        "start_time": schedule.start_time,
        "end_time": schedule.end_time,
    }


def _group_is_operational(group):
    return (
        group.status == _ACTIVE
        and group.academic_term.status == _ACTIVE
        and group.course.status == _ACTIVE
        and group.course.level.status == _ACTIVE
    )


def _archived_ancestor_labels(group):
    return [
        label
        for label, ok in (
            ("Academic Term", group.academic_term.status == _ACTIVE),
            ("Course", group.course.status == _ACTIVE),
            ("Level", group.course.level.status == _ACTIVE),
            ("Group", group.status == _ACTIVE),
        )
        if not ok
    ]


def _active_schedules_by_group(group_ids):
    """`{group_id: [Schedule, ...]}` for every ACTIVE Schedule of the
    given groups, one query, ordered for display."""
    out = {}
    if not group_ids:
        return out
    rows = (
        Schedule.query.filter(
            Schedule.group_id.in_(group_ids), Schedule.status == _ACTIVE
        )
        .order_by(Schedule.day_of_week, Schedule.start_time, Schedule.id)
        .all()
    )
    for row in rows:
        out.setdefault(row.group_id, []).append(row)
    return out


def _eligible_student_counts(group_ids):
    """`{group_id: count}` of ACTIVE Enrollment rows whose Student has
    role=student AND an active account -- one grouped aggregate."""
    if not group_ids:
        return {}
    rows = (
        db.session.query(Enrollment.group_id, func.count(Enrollment.id))
        .join(User, Enrollment.student_id == User.id)
        .filter(
            Enrollment.group_id.in_(group_ids),
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == UserRole.STUDENT.value,
            User.status == _USER_ACTIVE,
        )
        .group_by(Enrollment.group_id)
        .all()
    )
    return {gid: count for gid, count in rows}


# ---------------------------------------------------------------------------
# Administrator dashboard
# ---------------------------------------------------------------------------


def _role_counts(role):
    total = db.session.query(func.count(User.id)).filter(User.role == role).scalar()
    active = (
        db.session.query(func.count(User.id))
        .filter(User.role == role, User.status == _USER_ACTIVE)
        .scalar()
    )
    return {"total": total, "active": active, "suspended": total - active}


def admin_dashboard_stats():
    def _count(model, only_active=False):
        q = db.session.query(func.count(model.id))
        if only_active:
            q = q.filter(model.status == _ACTIVE)
        return q.scalar()

    return {
        # preserved keys (existing dashboard + test contract)
        "academic_terms_total": _count(AcademicTerm),
        "academic_terms_active": _count(AcademicTerm, only_active=True),
        "levels_total": _count(Level),
        "levels_active": _count(Level, only_active=True),
        "courses_total": _count(Course),
        "courses_active": _count(Course, only_active=True),
        # new in M09
        "groups_total": _count(Group),
        "groups_active": _count(Group, only_active=True),
        "students": _role_counts(UserRole.STUDENT.value),
        "teachers": _role_counts(UserRole.TEACHER.value),
        "active_enrollment_rows": db.session.query(func.count(Enrollment.id))
        .filter(Enrollment.status == _ENROLLMENT_ACTIVE)
        .scalar(),
        "active_teacher_assignment_rows": db.session.query(func.count(GroupTeacherAssignment.id))
        .filter(GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE)
        .scalar(),
    }


def _operational_schedules():
    """Every ACTIVE Schedule whose Group + AcademicTerm + Course + Level
    are all active, eager-loaded for display. Ownership: none (admin sees
    the whole center)."""
    return (
        Schedule.query.join(Group, Schedule.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .options(
            joinedload(Schedule.group).joinedload(Group.course).joinedload(Course.level),
            joinedload(Schedule.group).joinedload(Group.academic_term),
        )
        .filter(
            Schedule.status == _ACTIVE,
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
        )
        .order_by(Schedule.id)
        .all()
    )


def admin_operational_classes(now, cap=12):
    specs = [slot_spec(s, ref=_schedule_context(s, s.group)) for s in _operational_schedules()]
    return upcoming_occurrences(specs, now, cap=cap)


def admin_setup_indicators():
    """Setup health of the *operational* active Groups (Group + Course +
    Level + AcademicTerm all active):

    - ``operational_group_count``: how many such Groups exist -- so the
      dashboard can tell "an empty center" apart from "every group is
      fully configured";
    - ``missing_teacher``: those with no ACTIVE assignment to an active
      teacher-role account;
    - ``missing_schedule``: those with no ACTIVE Schedule row.

    At most three queries, independent of Group count.
    """
    groups = (
        db.session.query(Group.id, Group.public_id, Group.name, Course.title.label("course_title"))
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .filter(
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
        )
        .order_by(Group.name, Group.id)
        .all()
    )
    if not groups:
        return {"operational_group_count": 0, "missing_teacher": [], "missing_schedule": []}

    group_ids = [g.id for g in groups]
    with_eligible_teacher = {
        row[0]
        for row in db.session.query(GroupTeacherAssignment.group_id)
        .join(User, GroupTeacherAssignment.teacher_id == User.id)
        .filter(
            GroupTeacherAssignment.group_id.in_(group_ids),
            GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
            User.role == UserRole.TEACHER.value,
            User.status == _USER_ACTIVE,
        )
        .distinct()
        .all()
    }
    with_active_schedule = {
        row[0]
        for row in db.session.query(Schedule.group_id)
        .filter(Schedule.group_id.in_(group_ids), Schedule.status == _ACTIVE)
        .distinct()
        .all()
    }
    return {
        "operational_group_count": len(groups),
        "missing_teacher": [g for g in groups if g.id not in with_eligible_teacher],
        "missing_schedule": [g for g in groups if g.id not in with_active_schedule],
    }


def admin_dashboard(now):
    return {
        "stats": admin_dashboard_stats(),
        "operational_classes": admin_operational_classes(now),
        "setup": admin_setup_indicators(),
    }


# ---------------------------------------------------------------------------
# Teacher dashboard -- scoped to the Teacher's own ACTIVE assignments
# ---------------------------------------------------------------------------


def teacher_dashboard(teacher_id, now):
    assignments = (
        GroupTeacherAssignment.query.join(Group, GroupTeacherAssignment.group_id == Group.id)
        .options(
            joinedload(GroupTeacherAssignment.group)
            .joinedload(Group.course)
            .joinedload(Course.level),
            joinedload(GroupTeacherAssignment.group).joinedload(Group.academic_term),
        )
        .filter(
            GroupTeacherAssignment.teacher_id == teacher_id,
            GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
        )
        .order_by(Group.name, GroupTeacherAssignment.id)
        .all()
    )
    group_ids = [a.group_id for a in assignments]
    schedules_by_group = _active_schedules_by_group(group_ids)
    student_counts = _eligible_student_counts(group_ids)

    cards, operational_specs = _build_membership_cards(
        assignments,
        group_of=lambda a: a.group,
        now=now,
        schedules_by_group=schedules_by_group,
        extra=lambda a: {"student_count": student_counts.get(a.group_id, 0)},
    )
    return {
        "cards": cards,
        "next_class": earliest_occurrence(operational_specs, now),
        "upcoming": upcoming_occurrences(operational_specs, now),
    }


# ---------------------------------------------------------------------------
# Student dashboard -- scoped to the Student's own ACTIVE enrollments
# ---------------------------------------------------------------------------


def student_dashboard(student_id, now):
    enrollments = (
        Enrollment.query.join(Group, Enrollment.group_id == Group.id)
        .options(
            joinedload(Enrollment.group).joinedload(Group.course).joinedload(Course.level),
            joinedload(Enrollment.group).joinedload(Group.academic_term),
        )
        .filter(
            Enrollment.student_id == student_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
        )
        .order_by(Group.name, Enrollment.id)
        .all()
    )
    group_ids = [e.group_id for e in enrollments]
    schedules_by_group = _active_schedules_by_group(group_ids)

    cards, operational_specs = _build_membership_cards(
        enrollments,
        group_of=lambda e: e.group,
        now=now,
        schedules_by_group=schedules_by_group,
        extra=lambda e: {},
    )
    return {
        "cards": cards,
        "next_class": earliest_occurrence(operational_specs, now),
        "upcoming": upcoming_occurrences(operational_specs, now),
    }


def _build_membership_cards(rows, group_of, now, schedules_by_group, extra):
    """Shared assembly for the Teacher/Student per-membership cards.

    Returns ``(cards, operational_specs)`` where each card is a plain
    dict (no ORM objects reachable from the template beyond the display
    strings it already carries), and ``operational_specs`` is the flat
    list of :class:`SlotSpec` for every operational schedule, used for
    the combined next/upcoming lists.
    """
    cards = []
    operational_specs = []
    for row in rows:
        group = group_of(row)
        operational = _group_is_operational(group)
        group_schedules = schedules_by_group.get(group.id, [])
        specs = (
            [slot_spec(s, ref=_schedule_context(s, group)) for s in group_schedules]
            if operational
            else []
        )
        operational_specs.extend(specs)
        card = {
            "group_name": group.name,
            "group_public_id": group.public_id,
            "course_title": group.course.title,
            "level_name": group.course.level.name,
            "term_name": group.academic_term.name,
            "term_start": group.academic_term.start_date,
            "term_end": group.academic_term.end_date,
            "operational": operational,
            "archived_labels": [] if operational else _archived_ancestor_labels(group),
            "schedules": [
                {
                    "day_of_week": s.day_of_week,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "location": s.location,
                }
                for s in group_schedules
            ],
            "next_class": earliest_occurrence(specs, now) if operational else None,
        }
        card.update(extra(row))
        cards.append(card)
    return cards, operational_specs
