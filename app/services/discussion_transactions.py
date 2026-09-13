"""The authoritative write transactions for Group discussions
(Phase 4 / M12).

Flask-independent: no ``abort``, ``flash``, ``redirect`` or template. Each
write returns an :class:`Outcome` and the route decides the response.

**Three route-specific lock chains, one shared prefix**::

    create topic   AcademicTerm -> Level -> Course -> Group
                   -> acting Teacher User -> GroupTeacherAssignment

    reply          AcademicTerm -> Level -> Course -> Group
                   -> acting User -> Enrollment (Student)
                                     or GroupTeacherAssignment (Teacher)
                   -> DiscussionTopic

    lock / reopen  AcademicTerm -> Level -> Course -> Group
                   -> acting Teacher User -> GroupTeacherAssignment
                   -> DiscussionTopic

``lock_academic_hierarchy`` owns the one deliberate transaction reset and
takes the ancestor locks; the Group lock then serializes a discussion
write against every Group lifecycle, Enrollment and assignment change,
which take the same Group row first. An account suspension or a role
change updates the User row this chain locks, so it serializes too. The
discussion tables come last because no other writer locks them. **The
topic row is the serialization point** for replies and moderation: a
reply racing a lock is decided by whichever takes the topic lock first,
and a reply is inserted only if the locked topic is ``open``.

**Preview, then lock, then prove.** A non-locking preview
(:func:`~app.services.discussion_queries.lock_target`) only names the
rows. After the locks, every condition -- the unmoved academic chain,
every link active, the acting account's role and status, the current
relationship row, the topic's Group, its state and its version -- is
proved again from the locked rows; a failure rolls back without a write.
A Group retargeted between the preview and the locks is previewed once
more (bounded by :data:`MAX_ATTEMPTS`, and only when the new preview
names different rows), so a Group that is still operational under its new
parent is judged on its current chain rather than refused by accident.

**Nothing is written until everything is proved.** ``IntegrityError``
rolls back first, re-authorizes from current state, and then decides only
from a fresh read scoped to the acting user, the Group or topic, and the
nonce -- never from the constraint name.

No lock is held while a page renders, a list is queried or a notification
is delivered: every function here commits or rolls back before returning.

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
    DiscussionReply,
    DiscussionTopic,
    DiscussionTopicStatus,
    Enrollment,
    EnrollmentStatus,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    User,
    UserRole,
    UserStatus,
)
from app.models.discussion_topic import discussion_now
from app.services import discussion_queries
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.discussion_tokens import ACTION_LOCK, ACTION_REOPEN
from app.services.group_transactions import lock_group_in_open_transaction

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value
_OPEN = DiscussionTopicStatus.OPEN.value
_LOCKED = DiscussionTopicStatus.LOCKED.value

#: Previews tried before a write is rejected. A retry happens only after
#: the locked chain proved the Group moved, and only for different rows.
MAX_ATTEMPTS = 2

CREATED = "created"
CHANGED = "changed"
DUPLICATE = "duplicate"
ALREADY = "already"
TOPIC_LOCKED = "topic_locked"
STALE = "stale"
UNAVAILABLE = "unavailable"
CONFLICT = "conflict"

#: ``status`` is one of the constants above. ``public_id`` is the created
#: or duplicate topic / reply; ``object_id`` the internal id of a row this
#: request itself created or changed (never rendered).
Outcome = namedtuple("Outcome", "status public_id object_id")

_NOTHING = (None, None)

_ACTION_TARGET_STATUS = {ACTION_LOCK: _LOCKED, ACTION_REOPEN: _OPEN}


class DiscussionLocks:
    """The rows one discussion lock chain returned. Any value may be
    ``None``; every such case is a rejection. ``__slots__`` so a mistyped
    attribute raises instead of reading ``None``."""

    __slots__ = ("hierarchy", "group", "actor", "relationship", "topic")

    def __init__(self, hierarchy, group, actor, relationship, topic=None):
        self.hierarchy = hierarchy
        self.group = group
        self.actor = actor
        self.relationship = relationship
        self.topic = topic


def lock_discussion_chain(target, actor_id, role, topic_id=None):
    """Take the M12 lock order for `role` in one fresh transaction and
    return a :class:`DiscussionLocks`.

    `target` is a :class:`~app.services.discussion_queries.LockTarget` from
    a non-locking preview; it only names the rows to lock. The relationship
    row is the acting user's own ``Enrollment`` (Student) or
    ``GroupTeacherAssignment`` (Teacher) for the locked Group.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[target.term_id],
        level_ids=[target.level_id],
        course_ids=[target.course_id],
    )
    group = lock_group_in_open_transaction(target.group_public_id)
    actor = User.query.filter_by(id=actor_id).with_for_update().first()
    relationship = None
    if group is not None:
        if role == _TEACHER:
            relationship = (
                GroupTeacherAssignment.query.filter_by(group_id=group.id, teacher_id=actor_id)
                .with_for_update()
                .first()
            )
        elif role == _STUDENT:
            relationship = (
                Enrollment.query.filter_by(group_id=group.id, student_id=actor_id)
                .with_for_update()
                .first()
            )
    topic = None
    if topic_id is not None:
        topic = DiscussionTopic.query.filter_by(id=topic_id).with_for_update().first()
    return DiscussionLocks(hierarchy, group, actor, relationship, topic)


def access_proof_error(locks, target, actor_id, role):
    """``None`` when the **locked** rows still prove that `actor_id` may
    write in `target`'s Group as `role`, else a short internal reason
    (logged nowhere and never shown)."""
    group = locks.group
    term = locks.hierarchy.term(target.term_id)
    level = locks.hierarchy.level(target.level_id)
    course = locks.hierarchy.course(target.course_id)
    if group is None or term is None or level is None or course is None:
        return "missing"
    if (
        group.id != target.group_id
        or group.course_id != course.id
        or group.academic_term_id != term.id
        or course.level_id != level.id
    ):
        return "moved"
    if any(row.status != _ACTIVE for row in (term, level, course, group)):
        return "not_operational"
    actor = locks.actor
    if actor is None or actor.id != actor_id or actor.role != role:
        return "account"
    if actor.status != _USER_ACTIVE:
        return "account"
    relationship = locks.relationship
    if role == _TEACHER:
        proved = (
            relationship is not None
            and relationship.group_id == group.id
            and relationship.teacher_id == actor_id
            and relationship.status == _ASSIGNMENT_ACTIVE
        )
    elif role == _STUDENT:
        proved = (
            relationship is not None
            and relationship.group_id == group.id
            and relationship.student_id == actor_id
            and relationship.status == _ENROLLMENT_ACTIVE
        )
    else:
        proved = False
    if not proved:
        return "relationship"
    return None


def topic_proof_error(locks, topic_id):
    """``None`` when the locked topic is `topic_id` and belongs to the
    locked Group, else a reason."""
    topic = locks.topic
    if topic is None or topic.id != topic_id or locks.group is None:
        return "topic"
    if topic.group_id != locks.group.id:
        return "topic"
    return None


def _locked_access(actor_id, role, group_public_id, topic_id=None):
    """Preview, lock and prove; return the :class:`DiscussionLocks` with
    the transaction still open and every lock held, or ``None`` after
    rolling back."""
    previous = None
    for _ in range(MAX_ATTEMPTS):
        target = discussion_queries.lock_target(group_public_id)
        if target is None or target == previous:
            break
        previous = target
        locks = lock_discussion_chain(target, actor_id, role, topic_id)
        error = access_proof_error(locks, target, actor_id, role)
        if error is None:
            return locks
        db.session.rollback()
        if error != "moved":
            break
    db.session.rollback()
    return None


def _topic_nonce_used(nonce):
    return (
        db.session.query(DiscussionTopic.id)
        .filter(DiscussionTopic.creation_nonce == nonce)
        .first()
        is not None
    )


def _reply_nonce_used(nonce):
    return (
        db.session.query(DiscussionReply.id)
        .filter(DiscussionReply.creation_nonce == nonce)
        .first()
        is not None
    )


# ---------------------------------------------------------------------------
# Topic creation
# ---------------------------------------------------------------------------


def create_topic(actor_id, group_public_id, title, body, nonce):
    """Create one ``open`` topic, version 1, in the Group.

    `title` and `body` are already normalised; `nonce` came from a
    verified creation token bound to this Teacher and this Group.
    """
    locks = _locked_access(actor_id, _TEACHER, group_public_id)
    if locks is None:
        return Outcome(UNAVAILABLE, *_NOTHING)

    if _topic_nonce_used(nonce):
        db.session.rollback()
        return _topic_replay(actor_id, group_public_id, nonce)

    now = discussion_now()
    public_id = str(uuid.uuid4())
    topic = DiscussionTopic(
        public_id=public_id,
        group_id=locks.group.id,
        author_id=actor_id,
        title=title,
        body=body,
        status=_OPEN,
        version=1,
        creation_nonce=nonce,
        created_at=now,
        updated_at=now,
    )
    db.session.add(topic)
    try:
        db.session.flush()
        topic_id = topic.id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _topic_replay(actor_id, group_public_id, nonce)
    return Outcome(CREATED, public_id, topic_id)


def _topic_replay(actor_id, group_public_id, nonce):
    """After a rollback: re-authorize from current state, then resolve an
    already-used nonce to this Teacher's own topic in this Group."""
    group = discussion_queries.member_group(actor_id, _TEACHER, group_public_id)
    if group is None:
        return Outcome(UNAVAILABLE, *_NOTHING)
    existing = discussion_queries.topic_created_with_nonce(actor_id, group["id"], nonce)
    if existing is not None:
        return Outcome(DUPLICATE, existing, None)
    return Outcome(CONFLICT, *_NOTHING)


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


def create_reply(actor_id, role, group_public_id, topic_id, expected_version, body, nonce):
    """Append one reply to `topic_id`, only while the locked topic is
    ``open`` and still at `expected_version`.

    `role` is the acting route's role (``student`` or ``teacher``) and is
    re-proved against the locked User row. `body` is already normalised;
    `nonce` and `expected_version` came from a verified reply token.
    """
    locks = _locked_access(actor_id, role, group_public_id, topic_id)
    if locks is None:
        return Outcome(UNAVAILABLE, *_NOTHING)
    if topic_proof_error(locks, topic_id) is not None:
        db.session.rollback()
        return Outcome(UNAVAILABLE, *_NOTHING)

    # The nonce is decided before the topic state, so a replay of a reply
    # that was posted just before a lock still resolves to that reply.
    if _reply_nonce_used(nonce):
        db.session.rollback()
        return _reply_replay(actor_id, role, group_public_id, topic_id, nonce)
    if locks.topic.status != _OPEN:
        db.session.rollback()
        return Outcome(TOPIC_LOCKED, *_NOTHING)
    if locks.topic.version != expected_version:
        db.session.rollback()
        return Outcome(STALE, *_NOTHING)

    public_id = str(uuid.uuid4())
    reply = DiscussionReply(
        public_id=public_id,
        topic_id=topic_id,
        author_id=actor_id,
        body=body,
        creation_nonce=nonce,
        created_at=discussion_now(),
    )
    db.session.add(reply)
    try:
        db.session.flush()
        reply_id = reply.id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply_replay(actor_id, role, group_public_id, topic_id, nonce)
    return Outcome(CREATED, public_id, reply_id)


def _reply_replay(actor_id, role, group_public_id, topic_id, nonce):
    """After a rollback: re-authorize from current state, re-prove the
    topic's Group, then resolve an already-used nonce to this user's own
    reply on this topic."""
    group = discussion_queries.member_group(actor_id, role, group_public_id)
    if group is None or not discussion_queries.topic_in_group(topic_id, group["id"]):
        return Outcome(UNAVAILABLE, *_NOTHING)
    existing = discussion_queries.reply_created_with_nonce(actor_id, topic_id, nonce)
    if existing is not None:
        return Outcome(DUPLICATE, existing, None)
    return Outcome(CONFLICT, *_NOTHING)


# ---------------------------------------------------------------------------
# Lock and reopen
# ---------------------------------------------------------------------------


def moderate_topic(actor_id, group_public_id, topic_id, action, expected_version):
    """Lock or reopen `topic_id` for an actively assigned Teacher.

    A topic already in the requested state is an authorized no-op
    (:data:`ALREADY`) whatever version the form carried; otherwise the
    locked version must equal `expected_version`, and ``status``,
    ``version`` and ``updated_at`` then change together in one commit.
    """
    target_status = _ACTION_TARGET_STATUS.get(action)
    if target_status is None:
        return Outcome(UNAVAILABLE, *_NOTHING)
    locks = _locked_access(actor_id, _TEACHER, group_public_id, topic_id)
    if locks is None:
        return Outcome(UNAVAILABLE, *_NOTHING)
    if topic_proof_error(locks, topic_id) is not None:
        db.session.rollback()
        return Outcome(UNAVAILABLE, *_NOTHING)

    topic = locks.topic
    if topic.status == target_status:
        db.session.rollback()
        return Outcome(ALREADY, *_NOTHING)
    if topic.version != expected_version:
        db.session.rollback()
        return Outcome(STALE, *_NOTHING)

    topic.status = target_status
    topic.version = topic.version + 1
    topic.updated_at = discussion_now()
    try:
        db.session.flush()
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return Outcome(CONFLICT, *_NOTHING)
    return Outcome(CHANGED, None, topic_id)
