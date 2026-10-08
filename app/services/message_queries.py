"""Read-only, bounded queries for private Teacher-Student messaging
(Phase 4 / M11).

Flask-independent: no ``request``, ``flash`` or template here.

**Two different questions, answered separately.**

- *May this user read this thread?* -- membership, and nothing else. A
  thread is found only through a ``message_thread_members`` row for the
  acting user in the same SQL ``WHERE`` clause, so a missing thread and
  another pair's thread are indistinguishable. Historical access survives
  the end of the academic relationship on purpose.
- *May these two users exchange a new message right now?* -- the current
  academic relationship: both accounts active with the expected roles,
  an active Enrollment of the Student and an active GroupTeacherAssignment
  of the Teacher in one shared Group whose Group, Course, Level and
  AcademicTerm are all active. That is answered by
  :func:`shared_relationship`, and re-proved against locked rows by
  ``app/services/message_transactions.py`` before any insert.

**Every query is bounded.** The inbox reads ``PAGE_SIZE + 1`` rows, the
conversation ``CONVERSATION_PAGE_SIZE + 1``, the recipient list at most
:data:`RECIPIENT_LIMIT`, and the dashboard :data:`DASHBOARD_RECENT_CAP`.
No query loads every message to find the newest: the inbox groups on
``MAX(messages.id)`` per thread over the covering
``ix_messages_thread_created_id`` index.

**Recency.** Inside one thread, replies are serialized by the thread row
lock and stamped after it, so the highest message id of a thread is also
its newest by ``(created_at, id)``. The inbox orders threads by that
newest message's ``(created_at, id)`` -- deterministic, with the id as the
tie-breaker.

Templates receive plain dicts built here, never ORM rows, and no model in
this module declares a relationship, so no page can lazy-load.
"""

import re
from collections import namedtuple

from sqlalchemy import and_, func, literal, or_
from sqlalchemy.orm import aliased

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
    User,
    UserRole,
    UserStatus,
)
from app.services.message_text import PREVIEW_LENGTH, preview_text
from app.services.schedule_occurrences import to_app_local
from app.services.search_terms import escape_like

#: Threads per inbox page.
PAGE_SIZE = 20
#: Messages per conversation page.
CONVERSATION_PAGE_SIZE = 50
#: Most recipients the compose picker ever lists.
RECIPIENT_LIMIT = 50
#: Recent conversations on a dashboard.
DASHBOARD_RECENT_CAP = 5

_MAX_PAGE = 10000
_PREVIEW_FETCH = PREVIEW_LENGTH * 2

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value

#: The only roles that take part in messaging, and each one's counterpart.
OPPOSITE_ROLE = {_STUDENT: _TEACHER, _TEACHER: _STUDENT}

ROLE_LABELS = {_STUDENT: "Student", _TEACHER: "Teacher"}

_PUBLIC_ID_PATTERN = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)

#: One deterministic proof that a Student and a Teacher currently share an
#: operational Group. Internal ids only -- never rendered.
Relationship = namedtuple(
    "Relationship",
    "group_id group_public_id term_id level_id course_id enrollment_id assignment_id",
)

#: What the conversation page and the reply route know about replying.
ReplyState = namedtuple("ReplyState", "other student_id teacher_id relationship")


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def normalize_page(value):
    """A positive page number; anything unusable becomes page 1."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > _MAX_PAGE:
        return 1
    return page


def canonical_public_id(value):
    """`value` when it is a canonical lower-case UUID string, else
    ``None``. A malformed identifier is answered without a query."""
    if isinstance(value, str) and _PUBLIC_ID_PATTERN.match(value):
        return value
    return None


def orient_pair(first_id, first_role, second_id, second_role):
    """``(student_id, teacher_id)`` for one Student and one Teacher, in
    either order -- else ``None`` (two Students, two Teachers, or any other
    role)."""
    if first_role == _STUDENT and second_role == _TEACHER:
        return first_id, second_id
    if first_role == _TEACHER and second_role == _STUDENT:
        return second_id, first_id
    return None


# ---------------------------------------------------------------------------
# The current academic relationship
# ---------------------------------------------------------------------------


def _operational(query):
    return query.filter(
        Group.status == _ACTIVE,
        Course.status == _ACTIVE,
        Level.status == _ACTIVE,
        AcademicTerm.status == _ACTIVE,
    )


def shared_relationship(student_id, teacher_id):
    """The lowest-``Group.id`` operational Group this Student and Teacher
    currently share, as a :class:`Relationship` -- else ``None``.

    One bounded query. Both accounts must exist, hold exactly the Student
    and Teacher roles, and be active; the Enrollment and the assignment
    must both be active and in the same Group; Group, Course, Level and
    AcademicTerm must all be active. Several shared Groups resolve to the
    same deterministic choice every time, which is the Group the locked
    proof then locks.
    """
    if student_id is None or teacher_id is None:
        return None
    student = aliased(User)
    teacher = aliased(User)
    row = (
        _operational(
            db.session.query(
                Group.id,
                Group.public_id,
                Group.academic_term_id,
                Course.level_id,
                Group.course_id,
                Enrollment.id,
                GroupTeacherAssignment.id,
            )
            .select_from(Enrollment)
            .join(Group, Enrollment.group_id == Group.id)
            .join(GroupTeacherAssignment, GroupTeacherAssignment.group_id == Group.id)
            .join(Course, Group.course_id == Course.id)
            .join(Level, Course.level_id == Level.id)
            .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
            .join(student, Enrollment.student_id == student.id)
            .join(teacher, GroupTeacherAssignment.teacher_id == teacher.id)
        )
        .filter(
            Enrollment.student_id == student_id,
            GroupTeacherAssignment.teacher_id == teacher_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
            student.role == _STUDENT,
            student.status == _USER_ACTIVE,
            teacher.role == _TEACHER,
            teacher.status == _USER_ACTIVE,
        )
        .order_by(Group.id)
        .first()
    )
    return Relationship(*row) if row is not None else None


def _recipient_query(actor_id, actor_role):
    """The permitted-recipient ``SELECT`` for an acting Student or
    Teacher, or ``None`` for any other role. Every link of the shared
    operational-Group rule is in the ``WHERE`` clause, and the acting
    account's own role and status are re-checked there too."""
    actor = aliased(User)
    recipient = aliased(User)
    columns = (recipient.id, recipient.public_id, recipient.full_name, recipient.role)
    if actor_role == _STUDENT:
        query = (
            db.session.query(*columns)
            .select_from(Enrollment)
            .join(actor, Enrollment.student_id == actor.id)
            .join(Group, Enrollment.group_id == Group.id)
            .join(GroupTeacherAssignment, GroupTeacherAssignment.group_id == Group.id)
            .join(recipient, GroupTeacherAssignment.teacher_id == recipient.id)
        )
        actor_filter = Enrollment.student_id == actor_id
    elif actor_role == _TEACHER:
        query = (
            db.session.query(*columns)
            .select_from(GroupTeacherAssignment)
            .join(actor, GroupTeacherAssignment.teacher_id == actor.id)
            .join(Group, GroupTeacherAssignment.group_id == Group.id)
            .join(Enrollment, Enrollment.group_id == Group.id)
            .join(recipient, Enrollment.student_id == recipient.id)
        )
        actor_filter = GroupTeacherAssignment.teacher_id == actor_id
    else:
        return None, None
    query = _operational(
        query.join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
    ).filter(
        actor_filter,
        actor.role == actor_role,
        actor.status == _USER_ACTIVE,
        Enrollment.status == _ENROLLMENT_ACTIVE,
        GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
        recipient.role == OPPOSITE_ROLE[actor_role],
        recipient.status == _USER_ACTIVE,
    )
    return query, recipient


def permitted_recipients(actor_id, actor_role, search_text=""):
    """At most :data:`RECIPIENT_LIMIT` distinct permitted recipients,
    ordered by display name then internal id.

    A recipient reachable through several shared Groups appears once.
    `search_text` (already normalised) matches the display name only,
    literally -- ``%`` and ``_`` are escaped. Returns plain dicts carrying
    the public id and display name only.
    """
    query, recipient = _recipient_query(actor_id, actor_role)
    if query is None:
        return []
    if search_text:
        query = query.filter(
            recipient.full_name.ilike(f"%{escape_like(search_text)}%", escape="\\")
        )
    rows = (
        query.distinct()
        .order_by(recipient.full_name, recipient.id)
        .limit(RECIPIENT_LIMIT)
        .all()
    )
    return [
        {"public_id": row[1], "full_name": row[2], "role_label": ROLE_LABELS[row[3]]}
        for row in rows
    ]


def permitted_recipient(actor_id, actor_role, recipient_public_id):
    """``{id, public_id, full_name, role}`` for one currently permitted
    recipient, else ``None`` -- identical for an unknown public id, a
    same-role user, a suspended account and a user who shares no
    operational Group, so nothing is disclosed about which it was."""
    public_id = canonical_public_id(recipient_public_id)
    if public_id is None:
        return None
    query, recipient = _recipient_query(actor_id, actor_role)
    if query is None:
        return None
    row = query.filter(recipient.public_id == public_id).first()
    if row is None:
        return None
    return {"id": row[0], "public_id": row[1], "full_name": row[2], "role": row[3]}


# ---------------------------------------------------------------------------
# Membership-scoped thread reads
# ---------------------------------------------------------------------------


def member_thread(user_id, thread_public_id):
    """``{id, public_id, subject}`` for a thread `user_id` is a member of,
    else ``None`` -- for a malformed id, a missing thread and somebody
    else's thread alike. Membership is proved in the query itself."""
    public_id = canonical_public_id(thread_public_id)
    if public_id is None:
        return None
    row = (
        db.session.query(MessageThread.id, MessageThread.public_id, MessageThread.subject)
        .join(MessageThreadMember, MessageThreadMember.thread_id == MessageThread.id)
        .filter(
            MessageThread.public_id == public_id,
            MessageThreadMember.user_id == user_id,
        )
        .first()
    )
    if row is None:
        return None
    return {"id": row[0], "public_id": row[1], "subject": row[2]}


def latest_thread_for_pair(user_id, other_id):
    """Contact selection continues the latest existing conversation.

    This returns only a member-owned thread UUID, including a cleared thread
    whose old messages will stay excluded by the effective-message query.
    """
    me = aliased(MessageThreadMember)
    other = aliased(MessageThreadMember)
    return (
        db.session.query(MessageThread.public_id)
        .join(me, me.thread_id == MessageThread.id)
        .join(other, other.thread_id == MessageThread.id)
        .outerjoin(Message, Message.thread_id == MessageThread.id)
        .filter(me.user_id == user_id, other.user_id == other_id, other.user_id != user_id)
        .group_by(MessageThread.id, MessageThread.public_id)
        .order_by(func.max(Message.id).desc(), MessageThread.id.desc())
        .limit(1).scalar()
    )


def thread_members(thread_id):
    """The members of one thread with their current account facts,
    ascending by user id. Bounded at three rows: a well-formed thread has
    exactly two, and a third is enough to know it is not well formed."""
    rows = (
        db.session.query(
            MessageThreadMember.user_id, User.full_name, User.role, User.status
        )
        .join(User, User.id == MessageThreadMember.user_id)
        .filter(MessageThreadMember.thread_id == thread_id)
        .order_by(MessageThreadMember.user_id)
        .limit(3)
        .all()
    )
    return [
        {"user_id": row[0], "full_name": row[1], "role": row[2], "status": row[3]}
        for row in rows
    ]


def reply_state(thread_id, viewer_id):
    """Whether `viewer_id` may add a reply to this thread right now.

    Returns a :class:`ReplyState`. ``other`` is the other member (or
    ``None`` for a thread that is not exactly one Student plus one Teacher
    including the viewer); ``relationship`` is the current shared
    operational Group, or ``None`` -- which makes the conversation
    read-only. This is a preview for the page; the reply route re-proves
    it under locks.
    """
    members = thread_members(thread_id)
    if len(members) != 2 or viewer_id not in {m["user_id"] for m in members}:
        return ReplyState(None, None, None, None)
    viewer = next(m for m in members if m["user_id"] == viewer_id)
    other = next(m for m in members if m["user_id"] != viewer_id)
    pair = orient_pair(viewer["user_id"], viewer["role"], other["user_id"], other["role"])
    if pair is None:
        return ReplyState(other, None, None, None)
    student_id, teacher_id = pair
    return ReplyState(other, student_id, teacher_id, shared_relationship(student_id, teacher_id))


def _display_query(user_id, thread_id=None):
    """Membership scoped effective messages; hidden/cleared rows never render.

    Both aggregation paths use indexed foreign keys and the viewer's threads.
    Existing schema remains usable while the additive upgrade awaits approval.
    """
    from app.services.message_management import available
    from app.models.message_change import MessageChange, MessageThreadClear

    member = aliased(MessageThreadMember)
    query = (
        db.session.query(Message)
        .join(member, member.thread_id == Message.thread_id)
        .join(User, User.id == Message.sender_id)
        .filter(member.user_id == user_id)
    )
    body, revision_id, edited = Message.body, literal("original"), literal(False)
    if available():
        latest = (
            db.session.query(MessageChange.message_id.label("message_id"),
                             func.max(MessageChange.id).label("revision_id"))
            .join(Message, Message.id == MessageChange.message_id)
            .join(MessageThreadMember, MessageThreadMember.thread_id == Message.thread_id)
            .filter(MessageThreadMember.user_id == user_id)
        )
        if thread_id is not None:
            latest = latest.filter(Message.thread_id == thread_id)
        latest = latest.group_by(MessageChange.message_id).subquery()
        change = aliased(MessageChange)
        clears = (
            db.session.query(MessageThreadClear.member_id.label("member_id"),
                             func.max(MessageThreadClear.through_message_id).label("through_id"))
            .join(MessageThreadMember, MessageThreadMember.id == MessageThreadClear.member_id)
            .filter(MessageThreadMember.user_id == user_id)
            .group_by(MessageThreadClear.member_id).subquery()
        )
        query = (
            query.outerjoin(latest, latest.c.message_id == Message.id)
            .outerjoin(change, change.id == latest.c.revision_id)
            .outerjoin(clears, clears.c.member_id == member.id)
            .filter(or_(change.id.is_(None), change.kind != "hide"),
                    Message.id > func.coalesce(clears.c.through_id, 0))
        )
        body = func.coalesce(change.body, Message.body)
        revision_id = func.coalesce(change.public_id, "original")
        edited = func.coalesce(change.kind == "edit", False)
    if thread_id is not None:
        query = query.filter(Message.thread_id == thread_id)
    return query.with_entities(
        Message.public_id.label("public_id"), body.label("body"),
        Message.created_at.label("created_at"), Message.sender_id.label("sender_id"),
        User.full_name.label("sender_name"), revision_id.label("revision"),
        edited.label("edited"), Message.id.label("id"), Message.thread_id.label("thread_id"),
    )


def conversation_page(thread_id, page, viewer_id):
    """One page of a thread's messages: ``(rows, has_older)``.

    Page 1 is the newest :data:`CONVERSATION_PAGE_SIZE`; page 2 the ones
    before those, and so on. Selected newest-first with the deterministic
    ``(created_at, id)`` order and ``LIMIT CONVERSATION_PAGE_SIZE + 1``,
    then reversed so the visible page reads chronologically.
    """
    rows = (
        _display_query(viewer_id, thread_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .offset((page - 1) * CONVERSATION_PAGE_SIZE)
        .limit(CONVERSATION_PAGE_SIZE + 1)
        .all()
    )
    has_older = len(rows) > CONVERSATION_PAGE_SIZE
    visible = list(rows[:CONVERSATION_PAGE_SIZE])
    visible.reverse()
    return visible, has_older


def build_conversation_view(rows, viewer_id, tz_name):
    return [
        {
            "public_id": row[0],
            "body": row[1],
            "created_local": to_app_local(tz_name, row[2]),
            "from_viewer": row[3] == viewer_id,
            "sender_name": row[4],
            "revision": row[5],
            "edited": bool(row[6]),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Inbox and dashboard
# ---------------------------------------------------------------------------


def _inbox_query(user_id, search_text=""):
    """Every thread `user_id` belongs to, newest message first.

    One statement: the viewer's membership rows, the thread, the newest
    message per thread (``MAX(messages.id)`` grouped over the viewer's own
    threads only), that message's time, sender and a bounded ``SUBSTR`` of
    its body, and the other member's display name and role.
    Optional normalized search matches names/subjects with escaped LIKE;
    effective hidden/cleared messages are excluded before the inbox filter.
    """
    other = aliased(MessageThreadMember)
    other_user = aliased(User)
    visible = _display_query(user_id).subquery()
    latest = db.session.query(visible.c.thread_id, func.max(visible.c.id).label("last_id")).group_by(visible.c.thread_id).subquery()
    query = (
        db.session.query(MessageThread.public_id, MessageThread.subject,
                         visible.c.created_at, visible.c.sender_id,
                         func.substr(visible.c.body, 1, _PREVIEW_FETCH),
                         other_user.full_name, other_user.role)
        .select_from(MessageThread)
        .join(latest, latest.c.thread_id == MessageThread.id)
        .join(visible, visible.c.id == latest.c.last_id)
        .join(other, and_(other.thread_id == MessageThread.id, other.user_id != user_id))
        .join(other_user, other_user.id == other.user_id)
        .order_by(visible.c.created_at.desc(), visible.c.id.desc())
    )
    if search_text:
        pattern = f"%{escape_like(search_text)}%"
        query = query.filter(or_(MessageThread.subject.ilike(pattern, escape="\\"),
                                 other_user.full_name.ilike(pattern, escape="\\")))
    return query


def inbox_page(user_id, page, search_text=""):
    """``(rows, has_next)`` -- one page of the inbox, reading
    ``PAGE_SIZE + 1`` rows so "is there another page" costs no COUNT."""
    rows = (
        _inbox_query(user_id, search_text)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def recent_conversations(user_id, cap=DASHBOARD_RECENT_CAP):
    """The `cap` most recent threads for a dashboard. Membership only: a
    thread whose academic relationship has ended still appears."""
    return _inbox_query(user_id).limit(cap).all()


def build_inbox_view(rows, viewer_id, tz_name):
    return [
        {
            "thread_public_id": row[0],
            "subject": row[1],
            "last_local": to_app_local(tz_name, row[2]),
            "last_from_viewer": row[3] == viewer_id,
            "preview": preview_text(row[4]),
            "other_name": row[5],
            "other_role_label": ROLE_LABELS.get(row[6], "Member"),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Duplicate-submission lookups
# ---------------------------------------------------------------------------


def thread_created_with_nonce(creator_id, nonce):
    """The public id of the thread `creator_id` already created with
    `nonce`, else ``None``. Scoped to the creator, so a nonce can never
    lead anyone to another user's thread."""
    return (
        db.session.query(MessageThread.public_id)
        .filter(
            MessageThread.creation_nonce == nonce,
            MessageThread.created_by_id == creator_id,
        )
        .scalar()
    )


def reply_sent_with_nonce(sender_id, thread_id, nonce):
    """``True`` when `sender_id` already appended a message to
    `thread_id` with `nonce`."""
    return (
        db.session.query(Message.id)
        .filter(
            Message.creation_nonce == nonce,
            Message.sender_id == sender_id,
            Message.thread_id == thread_id,
        )
        .first()
        is not None
    )
