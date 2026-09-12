"""The authoritative write transactions for private messaging
(Phase 4 / M11).

Flask-independent: no ``abort``, ``flash``, ``redirect`` or template. Each
send function returns a :class:`SendOutcome` and the route decides the
response.

**One lock chain for both writes**::

    AcademicTerm -> Level -> Course    (lock_academic_hierarchy, which owns
                                        the one deliberate reset)
    -> Group
    -> the Student and Teacher User rows, ascending internal id
    -> the Teacher's GroupTeacherAssignment
    -> the Student's Enrollment
    -> MessageThread                   (reply only)
    -> its MessageThreadMember rows    (reply only)

This is the shared academic/Group prefix every Group-affecting mutation
already takes, followed by "the involved User rows in ascending internal
id" -- the project-wide rule the Administrator membership paths and the
M03 / M06 feedback chains follow. The User order depends on ids, never on
who is sending, so a Student and a Teacher writing to each other at the
same moment request the two User locks in the same order. The
relationship rows are then locked in a fixed order, and the messaging
tables -- which no other writer locks -- come last.

A send therefore serializes against an Enrollment withdrawal, a teacher
assignment removal, a Group or ancestor archive and a Group retarget on
the same chain: whichever commits first, the other decides against its
result. An account suspension updates the User row this chain locks, so
it serializes too.

**Preview, then lock, then prove.** The route's non-locking preview only
decides *which* relationship to lock. After the locks, every condition is
proved again from the locked rows (:func:`relationship_proof_error`,
:func:`thread_proof_error`); a proof failure rolls back. Because a pair
may share several Groups, a failure caused by the previewed Group ending
is followed by a fresh preview -- bounded by :data:`MAX_ATTEMPTS`, and
only when that preview names a *different* relationship -- so another
still-valid shared Group can serve, and a pair with none is rejected
without a write.

**Nothing is written until everything is proved**, and the thread, both
members and the first message are one commit. ``IntegrityError`` rolls
back first, then decides only from a fresh read scoped to the acting user
and nonce -- never from the constraint name.

SQLite (the test backend) honours neither ``FOR UPDATE`` nor REPEATABLE
READ. Tests can assert the *requested* lock set and order; they prove
nothing about real InnoDB blocking.
"""

import uuid
from collections import namedtuple

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AcademicStatus,
    Enrollment,
    EnrollmentStatus,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Message,
    MessageThread,
    MessageThreadMember,
    User,
    UserRole,
    UserStatus,
)
from app.models.message_thread import utc_whole_second_now
from app.services import message_queries
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value

#: Previews tried before a send is rejected as having no current
#: relationship. Each retry needs a relationship different from the one
#: that just failed, so this bounds work without ever looping on one.
MAX_ATTEMPTS = 3

SENT = "sent"
DUPLICATE = "duplicate"
UNAVAILABLE = "unavailable"
CONFLICT = "conflict"

#: ``status`` is one of the four constants above. ``thread_public_id`` is
#: set for a sent or duplicate creation; ``message_id`` for a sent message.
SendOutcome = namedtuple("SendOutcome", "status thread_public_id message_id")


class MessageLocks:
    """The rows one messaging lock chain returned. Any value may be
    ``None`` (or missing from ``users``); every such case is a rejection.
    ``__slots__`` so a mistyped attribute raises instead of reading
    ``None``."""

    __slots__ = ("hierarchy", "group", "users", "teacher_assignment", "enrollment",
                 "thread", "members")

    def __init__(self, hierarchy, group, users, teacher_assignment, enrollment,
                 thread=None, members=()):
        self.hierarchy = hierarchy
        self.group = group
        self.users = users
        self.teacher_assignment = teacher_assignment
        self.enrollment = enrollment
        self.thread = thread
        self.members = list(members)


def lock_message_chain(relationship, user_ids, thread_id=None):
    """Take the M11 lock order in one fresh transaction and return a
    :class:`MessageLocks`.

    `relationship` is a :class:`~app.services.message_queries.Relationship`
    from a non-locking preview; it only names the rows to lock.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[relationship.term_id],
        level_ids=[relationship.level_id],
        course_ids=[relationship.course_id],
    )
    group = lock_group_in_open_transaction(relationship.group_public_id)
    users = {}
    for user_id in sorted({uid for uid in user_ids if uid is not None}):
        users[user_id] = User.query.filter_by(id=user_id).with_for_update().first()
    teacher_assignment = (
        GroupTeacherAssignment.query.filter_by(id=relationship.assignment_id)
        .with_for_update()
        .first()
    )
    enrollment = (
        Enrollment.query.filter_by(id=relationship.enrollment_id).with_for_update().first()
    )
    thread = None
    members = []
    if thread_id is not None:
        thread = MessageThread.query.filter_by(id=thread_id).with_for_update().first()
        members = (
            MessageThreadMember.query.filter_by(thread_id=thread_id)
            .order_by(MessageThreadMember.id)
            .with_for_update()
            .all()
        )
    return MessageLocks(
        hierarchy, group, users, teacher_assignment, enrollment,
        thread=thread, members=members,
    )


def relationship_proof_error(locks, relationship, student_id, teacher_id):
    """``None`` when the **locked** rows still prove a current
    Student-Teacher relationship through `relationship`'s Group, else a
    short internal reason (logged nowhere and never shown)."""
    group = locks.group
    term = locks.hierarchy.term(relationship.term_id)
    level = locks.hierarchy.level(relationship.level_id)
    course = locks.hierarchy.course(relationship.course_id)
    if group is None or term is None or level is None or course is None:
        return "missing"
    if (
        group.id != relationship.group_id
        or group.course_id != course.id
        or group.academic_term_id != term.id
        or course.level_id != level.id
    ):
        return "moved"
    if any(row.status != _ACTIVE for row in (term, level, course, group)):
        return "not_operational"
    student = locks.users.get(student_id)
    teacher = locks.users.get(teacher_id)
    if student is None or teacher is None:
        return "account"
    if student.role != _STUDENT or student.status != _USER_ACTIVE:
        return "account"
    if teacher.role != _TEACHER or teacher.status != _USER_ACTIVE:
        return "account"
    assignment = locks.teacher_assignment
    if (
        assignment is None
        or assignment.group_id != group.id
        or assignment.teacher_id != teacher.id
        or assignment.status != _ASSIGNMENT_ACTIVE
    ):
        return "assignment"
    enrollment = locks.enrollment
    if (
        enrollment is None
        or enrollment.group_id != group.id
        or enrollment.student_id != student.id
        or enrollment.status != _ENROLLMENT_ACTIVE
    ):
        return "enrollment"
    return None


def thread_proof_error(locks, thread_id, sender_id, other_id):
    """``None`` when the locked thread still has exactly the two expected
    members -- the sender and the intended other user -- else a reason."""
    thread = locks.thread
    if thread is None or thread.id != thread_id:
        return "thread"
    if len(locks.members) != 2:
        return "members"
    if any(member.thread_id != thread_id for member in locks.members):
        return "members"
    if sorted(member.user_id for member in locks.members) != sorted((sender_id, other_id)):
        return "members"
    return None


def _now():
    """The authoritative write moment, read only after every lock."""
    return utc_whole_second_now()


def _nonce_used_by_thread(nonce):
    return (
        db.session.query(MessageThread.id).filter(MessageThread.creation_nonce == nonce).first()
        is not None
    )


def _nonce_used_by_message(nonce):
    return (
        db.session.query(Message.id).filter(Message.creation_nonce == nonce).first()
        is not None
    )


def _locked_relationship(student_id, teacher_id, thread_id=None):
    """Preview a relationship, lock its chain, and prove it; retry with a
    *different* previewed relationship at most :data:`MAX_ATTEMPTS` times.

    Returns ``(relationship, locks)`` with the transaction still open and
    every lock held, or ``(None, None)`` after rolling back.
    """
    previous = None
    for _ in range(MAX_ATTEMPTS):
        relationship = message_queries.shared_relationship(student_id, teacher_id)
        if relationship is None or relationship == previous:
            break
        previous = relationship
        locks = lock_message_chain(relationship, (student_id, teacher_id), thread_id)
        if relationship_proof_error(locks, relationship, student_id, teacher_id) is None:
            return relationship, locks
        db.session.rollback()
    db.session.rollback()
    return None, None


def send_new_thread(sender_id, recipient_id, student_id, teacher_id, subject, body, nonce):
    """Create one thread, its two members and its first message.

    `student_id` / `teacher_id` are the oriented pair; `sender_id` and
    `recipient_id` must be exactly those two. `subject` and `body` are
    already normalised; `nonce` came from a verified create token.
    """
    if sorted((sender_id, recipient_id)) != sorted((student_id, teacher_id)):
        return SendOutcome(UNAVAILABLE, None, None)
    _relationship, locks = _locked_relationship(student_id, teacher_id)
    if locks is None:
        return SendOutcome(UNAVAILABLE, None, None)

    if _nonce_used_by_thread(nonce):
        db.session.rollback()
        return _creation_replay(sender_id, nonce)

    now = _now()
    thread_public_id = str(uuid.uuid4())
    thread = MessageThread(
        public_id=thread_public_id,
        created_by_id=sender_id,
        subject=subject,
        creation_nonce=nonce,
        created_at=now,
    )
    db.session.add(thread)
    try:
        db.session.flush()
        db.session.add_all(
            [
                MessageThreadMember(thread_id=thread.id, user_id=user_id, joined_at=now)
                for user_id in sorted((student_id, teacher_id))
            ]
        )
        message = Message(
            public_id=str(uuid.uuid4()),
            thread_id=thread.id,
            sender_id=sender_id,
            body=body,
            creation_nonce=nonce,
            created_at=now,
        )
        db.session.add(message)
        db.session.flush()
        message_id = message.id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _creation_replay(sender_id, nonce)
    return SendOutcome(SENT, thread_public_id, message_id)


def _creation_replay(sender_id, nonce):
    existing = message_queries.thread_created_with_nonce(sender_id, nonce)
    if existing is not None:
        return SendOutcome(DUPLICATE, existing, None)
    return SendOutcome(CONFLICT, None, None)


def send_reply(sender_id, other_id, student_id, teacher_id, thread_id, body, nonce):
    """Append one reply to `thread_id`.

    `sender_id` and `other_id` must be the oriented pair's two users; the
    locked thread must still have exactly those two members.
    """
    if sorted((sender_id, other_id)) != sorted((student_id, teacher_id)):
        return SendOutcome(UNAVAILABLE, None, None)
    _relationship, locks = _locked_relationship(student_id, teacher_id, thread_id)
    if locks is None:
        return SendOutcome(UNAVAILABLE, None, None)
    if thread_proof_error(locks, thread_id, sender_id, other_id) is not None:
        db.session.rollback()
        return SendOutcome(UNAVAILABLE, None, None)

    if _nonce_used_by_message(nonce):
        db.session.rollback()
        return _reply_replay(sender_id, thread_id, nonce)

    message = Message(
        public_id=str(uuid.uuid4()),
        thread_id=thread_id,
        sender_id=sender_id,
        body=body,
        creation_nonce=nonce,
        created_at=_now(),
    )
    db.session.add(message)
    try:
        db.session.flush()
        message_id = message.id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply_replay(sender_id, thread_id, nonce)
    return SendOutcome(SENT, None, message_id)


def _reply_replay(sender_id, thread_id, nonce):
    if message_queries.reply_sent_with_nonce(sender_id, thread_id, nonce):
        return SendOutcome(DUPLICATE, None, None)
    return SendOutcome(CONFLICT, None, None)
