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
    Announcement,
    AnnouncementScope,
    AnnouncementStatus,
    Course,
    DiscussionTopic,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Lesson,
    LessonStatus,
    Level,
    Material,
    Message,
    MessageThread,
    MessageThreadMember,
    Notification,
    NotificationKind,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.services.notification_targets import (
    message_thread_target,
    student_discussion_topic_target,
    role_announcement_target,
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
_CENTER_SCOPE = AnnouncementScope.CENTER.value
_COURSE_SCOPE = AnnouncementScope.COURSE.value
_GROUP_SCOPE = AnnouncementScope.GROUP.value
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


def _announcement_context(announcement_id):
    """One bounded read: what a published announcement *is*, or ``None``.

    Returns the scope, the public id, the title, and the display name of
    whichever target the scope has -- the Course title for a course-scoped
    notice, the Group name for a group-scoped one, both resolved by
    ``LEFT JOIN`` on a nullable foreign key so neither can multiply the
    row. A **draft** or **withdrawn** announcement returns ``None`` here,
    so an announcement that is not actually published can produce no
    notification at all even if a caller asked for one.

    The announcement's own ``body`` is deliberately not selected: a
    notification names an announcement, it does not carry its text.
    """
    row = (
        db.session.query(
            Announcement.public_id,
            Announcement.scope,
            Announcement.title,
            Course.title,
            Group.name,
        )
        .select_from(Announcement)
        .outerjoin(Course, Announcement.course_id == Course.id)
        .outerjoin(Group, Announcement.group_id == Group.id)
        .filter(
            Announcement.id == announcement_id,
            Announcement.status == AnnouncementStatus.PUBLISHED.value,
        )
        .first()
    )
    if row is None:
        return None
    public_id, scope, title, course_title, group_name = row
    return {
        "public_id": public_id,
        "scope": scope,
        "title": title,
        "target_name": course_title if scope == _COURSE_SCOPE else group_name,
    }


def _announcement_recipients(scope, course_id, group_id):
    """Every currently eligible Student and Teacher of one announcement's
    scope, as ``[(user_id, role)]`` ascending by ``user_id``.

    Exactly the M09 visibility rule, expressed in the ``WHERE`` clause and
    never in Python:

    - **center** -- every active Student and every active Teacher account.
      No enrollment and no assignment is required, because none is
      required to *read* a center announcement either.
    - **course** -- everyone holding an ``active`` Enrollment (Student) or
      an ``active`` GroupTeacherAssignment (Teacher) in an **operational**
      Group of that Course.
    - **group** -- the same, for that exact Group.

    Two bounded queries, one per role, each ``DISTINCT``: a Student
    enrolled in three Groups of one Course is **one** recipient, not
    three, and a Teacher assigned to two Groups of one Course likewise.
    A user cannot satisfy both role filters, so the merged list is
    de-duplicated by construction. Sorted so the rows produced for one
    publication are deterministic.

    **No Administrator can appear here**, and not because they are
    filtered out afterwards: the role filter selects only ``student`` and
    ``teacher``, and the notification inbox itself is restricted to those
    two roles. An Administrator who publishes a center announcement
    therefore receives nothing.
    """
    if scope == _CENTER_SCOPE:
        students = (
            db.session.query(User.id)
            .filter(User.role == _STUDENT, User.status == _USER_ACTIVE)
            .distinct()
            .all()
        )
        teachers = (
            db.session.query(User.id)
            .filter(User.role == _TEACHER, User.status == _USER_ACTIVE)
            .distinct()
            .all()
        )
    else:
        if scope == _COURSE_SCOPE:
            target = Group.course_id == course_id
        elif scope == _GROUP_SCOPE:
            target = Group.id == group_id
        else:  # pragma: no cover -- the scope CHECK allows only three values
            return []
        students = (
            db.session.query(User.id)
            .select_from(Enrollment)
            .join(User, Enrollment.student_id == User.id)
            .join(Group, Enrollment.group_id == Group.id)
            .join(Course, Group.course_id == Course.id)
            .join(Level, Course.level_id == Level.id)
            .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
            .filter(
                target,
                Enrollment.status == _ENROLLMENT_ACTIVE,
                Group.status == _ACTIVE,
                Course.status == _ACTIVE,
                Level.status == _ACTIVE,
                AcademicTerm.status == _ACTIVE,
                User.role == _STUDENT,
                User.status == _USER_ACTIVE,
            )
            .distinct()
            .all()
        )
        teachers = (
            db.session.query(User.id)
            .select_from(GroupTeacherAssignment)
            .join(User, GroupTeacherAssignment.teacher_id == User.id)
            .join(Group, GroupTeacherAssignment.group_id == Group.id)
            .join(Course, Group.course_id == Course.id)
            .join(Level, Course.level_id == Level.id)
            .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
            .filter(
                target,
                GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
                Group.status == _ACTIVE,
                Course.status == _ACTIVE,
                Level.status == _ACTIVE,
                AcademicTerm.status == _ACTIVE,
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


#: How each scope names itself in a notification message. Server-authored
#: plain text, declared once, and deliberately naming only what the
#: recipient can already see for themselves.
_ANNOUNCEMENT_PHRASES = {
    _CENTER_SCOPE: "A new center announcement was published",
    _COURSE_SCOPE: "A new announcement was published for the course '{target}'",
    _GROUP_SCOPE: "A new announcement was published for the group '{target}'",
}


def notify_announcement_published(announcement_id, scope, course_id, group_id):
    """Event 8 (Phase 4 / M09) -- an Announcement was published for the
    **first and only** time.

    Called once, by the publish route, *after* the publication has already
    committed. Recipients are everyone the announcement's scope currently
    reaches, Students and Teachers only, each sent to their own role's
    authorized detail page for it.

    **Exactly one delivery per announcement, ever.** The publish route is
    the only caller, and it can only reach this line on the one request
    that moved a ``draft`` to ``published`` under the lock -- a retry, a
    refresh, a second submission, a stale-token rejection, a withdrawal
    and every later edit attempt all return before it. There is no
    re-publication in M09 to produce a second one: ``published`` is a
    one-way door, and ``withdrawn`` is terminal.

    The message is server-authored plain text naming the announcement's
    title and its scope, and nothing else: no body text, no author, no
    recipient information, no internal id, and no count of who else was
    told. The target is built by
    ``app/services/notification_targets.py`` from the public id alone and
    validated before it is stored.

    Delivery is best-effort and fault-isolated, exactly like every other
    producer here: the announcement is already committed and its response
    already decided, so a failure is logged and swallowed rather than
    turning a successful publication into an error.
    """

    def build():
        context = _announcement_context(announcement_id)
        if context is None:
            return []
        phrase = _ANNOUNCEMENT_PHRASES.get(scope)
        if phrase is None:  # pragma: no cover -- the scope CHECK bounds this
            return []
        title = context["title"]
        message = (
            phrase.format(target=context["target_name"] or "") + f": '{title}'."
        )
        entries = []
        for recipient_id, role in _announcement_recipients(scope, course_id, group_id):
            target = role_announcement_target(role, context["public_id"])
            if target is None:  # pragma: no cover -- only these two roles are selected
                continue
            entries.append(
                _entry(
                    recipient_id,
                    NotificationKind.ANNOUNCEMENT_PUBLISHED.value,
                    "New announcement",
                    message,
                    target,
                )
            )
        return entries

    return _deliver(f"announcement publication ({scope})", build)


#: How much of a thread subject a message notification quotes.
_MESSAGE_SUBJECT_QUOTE_LIMIT = 80


def notify_message_received(message_id):
    """Event 9 (Phase 4 / M11) -- a private message was committed, either
    as the first message of a new thread or as a reply.

    Called once, by the messaging route, only on the request whose own
    transaction inserted `message_id`; a duplicate submission, a replay, a
    rejected or read-only send never reaches it. The recipient is the
    thread's **other** member only -- re-read here, with role and active
    account re-checked in SQL -- and never the sender. A thread that does
    not have exactly one such other member produces nothing.

    The row names the sender's display name and a clipped subject, and
    nothing else: **the message body is never selected**, so it cannot
    reach ``title``, ``message`` or the logs. The target is the canonical
    thread page, built and validated by
    ``app/services/notification_targets.py``; opening it re-proves
    membership.

    Best-effort and fault-isolated like every producer here: the message
    is already committed, so a failure is logged without its content and
    swallowed.
    """

    def build():
        context = (
            db.session.query(
                Message.thread_id,
                Message.sender_id,
                MessageThread.public_id,
                MessageThread.subject,
                User.full_name,
            )
            .select_from(Message)
            .join(MessageThread, MessageThread.id == Message.thread_id)
            .join(User, User.id == Message.sender_id)
            .filter(Message.id == message_id)
            .first()
        )
        if context is None:
            return []
        thread_id, sender_id, thread_public_id, subject, sender_name = context
        recipients = (
            db.session.query(User.id, User.role)
            .join(MessageThreadMember, MessageThreadMember.user_id == User.id)
            .filter(
                MessageThreadMember.thread_id == thread_id,
                User.id != sender_id,
                User.role.in_((_STUDENT, _TEACHER)),
                User.status == _USER_ACTIVE,
            )
            .order_by(User.id)
            .limit(2)
            .all()
        )
        if len(recipients) != 1:
            return []
        recipient_id, role = recipients[0]
        return [
            _entry(
                recipient_id,
                NotificationKind.MESSAGE_RECEIVED.value,
                "New message",
                f"{sender_name} sent you a message in the conversation "
                f"'{_clip(subject, _MESSAGE_SUBJECT_QUOTE_LIMIT)}'.",
                message_thread_target(role, thread_public_id),
            )
        ]

    return _deliver("private message", build)


def notify_discussion_topic_created(topic_id):
    """Event 10 (Phase 4 / M12) -- a Teacher created a Group discussion
    topic.

    Called once, by the Teacher creation route, only on the request whose
    own transaction inserted `topic_id`, and only *after* that commit. A
    replayed or double-submitted form, a rejected creation, a reply, a lock
    and a reopen never reach it, so each topic produces at most one
    delivery.

    Recipients are re-selected here, in SQL, from current state: every
    **active** Student account with an **active** Enrollment in the topic's
    exact Group, while that Group, its Course, Level and AcademicTerm are
    all active -- each once, ascending by id. The creating Teacher is never
    a recipient (the role filter excludes every Teacher, and the author is
    excluded by id as well), and no Teacher, Administrator or Researcher is
    notified.

    The row names the Group and the topic title, and nothing else: **the
    topic body is never selected**, so it cannot reach ``title``,
    ``message`` or the logs. The target is the exact Student topic page,
    built and validated by ``app/services/notification_targets.py``;
    opening it re-proves the Student's current access.

    Best-effort and fault-isolated like every producer here: the topic is
    already committed, so a failure is logged without its content and
    swallowed.
    """

    def build():
        context = (
            db.session.query(
                DiscussionTopic.public_id,
                DiscussionTopic.title,
                DiscussionTopic.author_id,
                Group.id,
                Group.public_id,
                Group.name,
            )
            .select_from(DiscussionTopic)
            .join(Group, DiscussionTopic.group_id == Group.id)
            .join(Course, Group.course_id == Course.id)
            .join(Level, Course.level_id == Level.id)
            .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
            .filter(
                DiscussionTopic.id == topic_id,
                Group.status == _ACTIVE,
                Course.status == _ACTIVE,
                Level.status == _ACTIVE,
                AcademicTerm.status == _ACTIVE,
            )
            .first()
        )
        if context is None:
            return []
        topic_public_id, title, author_id, group_id, group_public_id, group_name = context
        students = (
            db.session.query(User.id)
            .join(Enrollment, Enrollment.student_id == User.id)
            .filter(
                Enrollment.group_id == group_id,
                Enrollment.status == _ENROLLMENT_ACTIVE,
                User.role == _STUDENT,
                User.status == _USER_ACTIVE,
                User.id != author_id,
            )
            .distinct()
            .order_by(User.id)
            .all()
        )
        if not students:
            return []
        target = student_discussion_topic_target(group_public_id, topic_public_id)
        message = f"A new discussion topic was started in the group '{group_name}': '{title}'."
        return [
            _entry(
                row[0],
                NotificationKind.DISCUSSION_TOPIC_CREATED.value,
                "New discussion topic",
                message,
                target,
            )
            for row in students
        ]

    return _deliver("discussion topic creation", build)
