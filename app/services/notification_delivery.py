"""Post-commit, best-effort delivery of stored in-app notifications (M14).

Every function here is a **producer**: it runs *after* a domain mutation
has already been committed by its own route, using only plain scalar
values that route captured, and it writes into a **separate notification
transaction** that takes no domain locks.

The contract each producer honours, in order:

1. the domain mutation has already committed with its own existing
   locking, validation, stale-write and error handling -- nothing here
   changes, widens, or reorders any of that;
2. the caller passes scalars only (ids, public ids, display names), never
   an ORM row carried across the transaction boundary;
3. recipient selection is bounded, deterministic, de-duplicated SQL that
   re-checks role, active account, active membership, and the effective
   visibility chain the event requires -- it never trusts the producer's
   caller for any of that;
4. any failure -- a missing table, a broken query, a database error, an
   unexpected programming error -- is caught here, rolled back, logged
   server-side, and swallowed. :func:`_deliver` never raises.

**This is deliberately not exactly-once.** Delivery happens synchronously
after the domain commit, so a process crash, a lost connection, or a
database failure in the window between the two commits loses that
notification permanently, and there is no retry, outbox, dead-letter, or
reconciliation job. That is an accepted trade: the alternative -- writing
notifications inside the domain transaction -- would let an optional
convenience feature roll back a successful enrollment, assignment,
schedule change, lesson publication, or file upload, which is strictly
worse. A durable outbox is explicitly deferred (see ``docs/DECISIONS.md``,
Part M14).

Flask-independent apart from ``current_app`` (the logger, and the URL map
used by ``app.services.notification_targets``) -- there is no ``request``,
``flash``, ``redirect``, or route decorator here, and nothing in this
module needs an active request context.
"""

from flask import current_app

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
    Notification,
    NotificationKind,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.services.notification_targets import (
    role_dashboard_target,
    student_dashboard_target,
    student_lesson_target,
    student_material_target,
    teacher_dashboard_target,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_PUBLISHED = LessonStatus.PUBLISHED.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value

#: Hard limits mirroring ``notifications.title`` / ``notifications.message``.
_TITLE_LIMIT = 150
_MESSAGE_LIMIT = 500


def _clip(text, limit):
    """Bound a rendered string to its column width.

    Object names are user-authored and individually bounded (a Group name
    is 100 characters, a Lesson / Material title 150), but a message that
    quotes two of them plus boilerplate could still exceed 500. Clipping
    here keeps the write valid under MySQL strict mode instead of turning
    a long title into a delivery failure.
    """
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _entry(recipient_id, kind, title, message, target_path):
    return {
        "recipient_id": recipient_id,
        "kind": kind,
        "title": _clip(title, _TITLE_LIMIT),
        "message": _clip(message, _MESSAGE_LIMIT),
        "target_path": target_path,
    }


def _deliver(event_label, build_entries):
    """Run `build_entries` and commit whatever it returns in a **new**
    notification transaction. Returns the number of rows written; returns
    ``0`` and never raises on any failure.

    `build_entries` is a zero-argument callable rather than a ready list
    so that recipient selection -- the part most likely to fail -- also
    runs inside this guard.
    """
    try:
        entries = build_entries() or []
        if not entries:
            # Nothing to write. The read-only transaction the recipient
            # query opened is deliberately left to ordinary request
            # teardown, exactly like every other read-only path in this
            # application: an explicit rollback here would add a second
            # transaction reset to the request, and the M08/M11/M12
            # lock-order regression tests assert that a mutation request
            # performs exactly **one** deliberate reset (the one owned by
            # `lock_academic_hierarchy` / `lock_group_for_write`). No lock
            # is held at this point -- the domain mutation has already
            # committed -- so there is nothing to release early.
            return 0
        db.session.add_all([Notification(**entry) for entry in entries])
        db.session.commit()
        return len(entries)
    except Exception:
        # The domain mutation is already committed and its response is
        # already decided -- a notification failure must never turn that
        # success into an error, so this is logged and swallowed.
        try:
            db.session.rollback()
        except Exception:  # pragma: no cover - rollback of an unusable session
            pass
        current_app.logger.exception(
            "Notification delivery failed for %s; the domain change itself is committed "
            "and unaffected, but this notification is lost (delivery is best-effort, not "
            "exactly-once)",
            event_label,
        )
        return 0


# ----------------------------------------------------------------------
# Recipient selection -- bounded, deterministic, de-duplicated SQL
# ----------------------------------------------------------------------


def _eligible_recipient_id(user_id, role):
    """`user_id` if that User currently exists, has exactly `role`, and
    holds an ACTIVE account -- else ``None``.

    A foreign key to ``users`` proves existence only, so this re-check is
    what keeps a suspended account, a wrong-role account, or a stale id
    from receiving a notification.
    """
    return (
        db.session.query(User.id)
        .filter(User.id == user_id, User.role == role, User.status == _USER_ACTIVE)
        .scalar()
    )


def _group_member_recipients(group_id):
    """Every currently eligible Student and Teacher of `group_id`, as
    ``[(user_id, role)]`` ascending by ``user_id``.

    Two bounded queries (one per role), each requiring an ACTIVE
    membership row **and** the matching role **and** an ACTIVE account --
    the same "valid active seat / eligible teacher" definitions used by
    ``app.services.group_memberships``. ``(student_id, group_id)`` and
    ``(group_id, teacher_id)`` are both UNIQUE, so a user cannot appear
    twice for one role, and a user cannot satisfy both role filters, so
    the merged list is de-duplicated by construction. Sorted so the rows
    produced for one event are deterministic.
    """
    students = (
        db.session.query(User.id)
        .join(Enrollment, Enrollment.student_id == User.id)
        .filter(
            Enrollment.group_id == group_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
        )
        .distinct()
        .all()
    )
    teachers = (
        db.session.query(User.id)
        .join(GroupTeacherAssignment, GroupTeacherAssignment.teacher_id == User.id)
        .filter(
            GroupTeacherAssignment.group_id == group_id,
            GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
            User.role == _TEACHER,
            User.status == _USER_ACTIVE,
        )
        .distinct()
        .all()
    )
    recipients = [(row[0], _STUDENT) for row in students]
    recipients += [(row[0], _TEACHER) for row in teachers]
    recipients.sort(key=lambda pair: (pair[0], pair[1]))
    return recipients


def _published_lesson_audience(lesson_id):
    """One query: ``(student_ids, context)`` for a Lesson that is
    *currently* student-visible, or ``([], None)``.

    ``student_ids`` are every Student who can effectively reach the Lesson
    right now -- own ACTIVE Enrollment for the owning Group, ACTIVE
    AcademicTerm / Level / Course / Group / Unit, ``published`` Lesson,
    Student role, ACTIVE account. This is exactly the M11/M12/M13
    effective-visibility formula, expressed in the ``WHERE`` clause; it is
    never "load the Lesson, then filter in Python".

    ``context`` carries the public ids and display names the message and
    target need. If the Lesson is a draft, or any link of the chain is
    archived, the query returns no row at all -- so an invisible Lesson
    silently produces no notification.
    """
    rows = (
        db.session.query(
            User.id,
            Group.public_id,
            Unit.public_id,
            Lesson.public_id,
            Lesson.title,
            Group.name,
        )
        .select_from(Lesson)
        .join(Unit, Lesson.unit_id == Unit.id)
        .join(Group, Unit.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .join(User, Enrollment.student_id == User.id)
        .filter(
            Lesson.id == lesson_id,
            Lesson.status == _PUBLISHED,
            Unit.status == _ACTIVE,
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
        )
        .distinct()
        .order_by(User.id)
        .all()
    )
    if not rows:
        return [], None
    first = rows[0]
    context = {
        "group_public_id": first[1],
        "unit_public_id": first[2],
        "lesson_public_id": first[3],
        "lesson_title": first[4],
        "group_name": first[5],
    }
    return [row[0] for row in rows], context


def _visible_material_audience(material_id):
    """One query: ``(student_ids, context)`` for a Material that is
    *currently* student-visible, or ``([], None)``.

    Adds ``Material.status == active`` and the same published-Lesson +
    fully-active chain used by :func:`_published_lesson_audience`, i.e.
    exactly ``app.services.student_lessons``'s Student rules. A Material
    on a **draft** Lesson, an archived Material, or any archived ancestor
    yields no row -- which is how "notify Students only when the owning
    Lesson is published and the complete student visibility chain is
    currently active" is enforced, rather than by an ``if`` in a route.
    """
    rows = (
        db.session.query(
            User.id,
            Group.public_id,
            Unit.public_id,
            Lesson.public_id,
            Material.public_id,
            Material.title,
            Lesson.title,
            Group.name,
        )
        .select_from(Material)
        .join(Lesson, Material.lesson_id == Lesson.id)
        .join(Unit, Lesson.unit_id == Unit.id)
        .join(Group, Unit.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .join(User, Enrollment.student_id == User.id)
        .filter(
            Material.id == material_id,
            Material.status == _ACTIVE,
            Lesson.status == _PUBLISHED,
            Unit.status == _ACTIVE,
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
        )
        .distinct()
        .order_by(User.id)
        .all()
    )
    if not rows:
        return [], None
    first = rows[0]
    context = {
        "group_public_id": first[1],
        "unit_public_id": first[2],
        "lesson_public_id": first[3],
        "material_public_id": first[4],
        "material_title": first[5],
        "lesson_title": first[6],
        "group_name": first[7],
    }
    return [row[0] for row in rows], context


# ----------------------------------------------------------------------
# Producers -- one per row of the M14 event matrix
# ----------------------------------------------------------------------


def notify_enrollment_activated(student_id, group_name):
    """Event 1 -- an Enrollment was created or reactivated as ACTIVE."""

    def build():
        recipient_id = _eligible_recipient_id(student_id, _STUDENT)
        if recipient_id is None:
            return []
        return [
            _entry(
                recipient_id,
                NotificationKind.ENROLLMENT_ACTIVATED.value,
                "Enrollment activated",
                f"You are now enrolled in the group '{group_name}'. "
                "Your classes and learning content are available from your dashboard.",
                student_dashboard_target(),
            )
        ]

    return _deliver("enrollment activation", build)


def notify_enrollment_withdrawn(student_id, group_name):
    """Event 2 -- an ACTIVE Enrollment was withdrawn."""

    def build():
        recipient_id = _eligible_recipient_id(student_id, _STUDENT)
        if recipient_id is None:
            return []
        return [
            _entry(
                recipient_id,
                NotificationKind.ENROLLMENT_WITHDRAWN.value,
                "Enrollment withdrawn",
                f"Your enrollment in the group '{group_name}' has been withdrawn. "
                "Its classes and learning content are no longer available to you.",
                student_dashboard_target(),
            )
        ]

    return _deliver("enrollment withdrawal", build)


def notify_teacher_assignment_activated(teacher_id, group_name):
    """Event 3 -- a GroupTeacherAssignment was created or reactivated."""

    def build():
        recipient_id = _eligible_recipient_id(teacher_id, _TEACHER)
        if recipient_id is None:
            return []
        return [
            _entry(
                recipient_id,
                NotificationKind.TEACHER_ASSIGNMENT_ACTIVATED.value,
                "Teaching assignment activated",
                f"You are now assigned to teach the group '{group_name}'. "
                "Its classes and content are available from your dashboard.",
                teacher_dashboard_target(),
            )
        ]

    return _deliver("teacher assignment activation", build)


def notify_teacher_assignment_removed(teacher_id, group_name):
    """Event 4 -- an ACTIVE GroupTeacherAssignment was removed."""

    def build():
        recipient_id = _eligible_recipient_id(teacher_id, _TEACHER)
        if recipient_id is None:
            return []
        return [
            _entry(
                recipient_id,
                NotificationKind.TEACHER_ASSIGNMENT_REMOVED.value,
                "Teaching assignment removed",
                f"You are no longer assigned to teach the group '{group_name}'.",
                teacher_dashboard_target(),
            )
        ]

    return _deliver("teacher assignment removal", build)


#: What happened to the Schedule, as a short recipient-facing phrase.
#: Deliberately not the weekday / time / location detail: the dashboard is
#: the single source of truth for the current timetable, and a stored
#: message must not become a stale second copy of it.
SCHEDULE_CHANGE_PHRASES = {
    "created": "A class time was added",
    "updated": "A class time was changed",
    "archived": "A class time was removed",
    "reactivated": "A class time was restored",
}


def notify_schedule_changed(group_id, group_name, change):
    """Event 5 -- a Schedule of this Group was created, edited, archived,
    or reactivated. Recipients are every currently active, eligible
    Student **and** Teacher of the Group, each sent to their own role
    dashboard.
    """
    phrase = SCHEDULE_CHANGE_PHRASES.get(change, "The class schedule was changed")

    def build():
        entries = []
        for recipient_id, role in _group_member_recipients(group_id):
            target = role_dashboard_target(role)
            if target is None:  # defensive -- the query yields only these two roles
                continue
            entries.append(
                _entry(
                    recipient_id,
                    NotificationKind.SCHEDULE_CHANGED.value,
                    "Class schedule changed",
                    f"{phrase} for the group '{group_name}'. "
                    "Check your dashboard for the current class times.",
                    target,
                )
            )
        return entries

    return _deliver(f"schedule change ({change})", build)


def notify_lesson_published(lesson_id):
    """Event 6 -- a Lesson was published or republished. Recipients are
    the Students who can effectively reach it right now.
    """

    def build():
        student_ids, context = _published_lesson_audience(lesson_id)
        if not student_ids:
            return []
        target = student_lesson_target(
            context["group_public_id"],
            context["unit_public_id"],
            context["lesson_public_id"],
        )
        message = (
            f"The lesson '{context['lesson_title']}' is now available in the group "
            f"'{context['group_name']}'."
        )
        return [
            _entry(
                student_id,
                NotificationKind.LESSON_PUBLISHED.value,
                "New lesson published",
                message,
                target,
            )
            for student_id in student_ids
        ]

    return _deliver("lesson publication", build)


def notify_material_available(material_id):
    """Event 7 -- a Material was created or reactivated. Recipients are
    the Students who can effectively see it right now, which requires the
    owning Lesson to be published and the whole chain active.
    """

    def build():
        student_ids, context = _visible_material_audience(material_id)
        if not student_ids:
            return []
        target = student_material_target(
            context["group_public_id"],
            context["unit_public_id"],
            context["lesson_public_id"],
            context["material_public_id"],
        )
        message = (
            f"The material '{context['material_title']}' was added to the lesson "
            f"'{context['lesson_title']}' in the group '{context['group_name']}'."
        )
        return [
            _entry(
                student_id,
                NotificationKind.MATERIAL_AVAILABLE.value,
                "New material available",
                message,
                target,
            )
            for student_id in student_ids
        ]

    return _deliver("material availability", build)
