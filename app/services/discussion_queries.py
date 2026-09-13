"""Read-only, bounded queries for Group discussions (Phase 4 / M12).

Flask-independent: no ``request``, ``flash`` or template here, and no
lock -- every write-side proof lives in
``app/services/discussion_transactions.py``.

**Access is the current Group relationship, proved in SQL.** A Group is
found only through the acting user's own row in the same ``WHERE``
clause: an **active** ``GroupTeacherAssignment`` for a Teacher or an
**active** ``Enrollment`` for a Student, the acting account's exact role
and ``active`` status, and an active Group, Course, Level and AcademicTerm.
A missing Group, a malformed identifier, somebody else's Group, an ended
relationship, a suspended account and an archived link are therefore all
the same ``None``. A topic is then found only with that Group's internal
id in its ``WHERE`` clause, so a topic of another Group is the same
``None`` too. Nothing historical grants access: this is a classroom, not a
private correspondence.

**Every query is bounded and column-projected.** A topic list reads
``TOPIC_PAGE_SIZE + 1`` rows and one grouped reply count for that page; a
timeline reads ``REPLY_PAGE_SIZE + 1`` rows; an overview reads at most
``GROUP_LIMIT + 1`` Groups and one grouped topic count. No query's cost
grows with the number of rows a page does not show, and no model here
declares a relationship, so no template can lazy-load.

Templates receive plain dicts built here, never ORM rows, and the dicts
handed to templates carry public identifiers only.
"""

import re
from collections import namedtuple

from sqlalchemy import and_, case, func, or_

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    DiscussionReply,
    DiscussionTopic,
    DiscussionTopicStatus,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    User,
    UserRole,
    UserStatus,
)
from app.services.discussion_text import PREVIEW_LENGTH, preview_text
from app.services.schedule_occurrences import to_app_local

#: Topics per Group topic list page.
TOPIC_PAGE_SIZE = 20
#: Replies per topic timeline page.
REPLY_PAGE_SIZE = 50
#: Most Groups an overview page lists.
GROUP_LIMIT = 50

_MAX_PAGE = 10000
_PREVIEW_FETCH = PREVIEW_LENGTH * 2

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value
_OPEN = DiscussionTopicStatus.OPEN.value
_LOCKED = DiscussionTopicStatus.LOCKED.value

ROLE_LABELS = {_STUDENT: "Student", _TEACHER: "Teacher"}

_PUBLIC_ID_PATTERN = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)

#: The rows one discussion lock chain must lock, from a non-locking
#: preview. Internal ids only -- never rendered.
LockTarget = namedtuple("LockTarget", "group_id group_public_id term_id level_id course_id")


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
    ``None``. A malformed identifier is answered without a query, and an
    upper-cased id is never an alias for the real one."""
    if isinstance(value, str) and _PUBLIC_ID_PATTERN.match(value):
        return value
    return None


# ---------------------------------------------------------------------------
# Current Group access
# ---------------------------------------------------------------------------


_GROUP_COLUMNS = (
    Group.id,
    Group.public_id,
    Group.name,
    Course.title,
    Level.name,
    AcademicTerm.name,
)


def _member_group_query(actor_id, role):
    """``SELECT`` the Groups `actor_id` may currently discuss in as
    `role`, or ``None`` for a role with no discussion access."""
    query = (
        db.session.query(*_GROUP_COLUMNS)
        .select_from(Group)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
    )
    if role == _TEACHER:
        query = (
            query.join(GroupTeacherAssignment, GroupTeacherAssignment.group_id == Group.id)
            .join(User, User.id == GroupTeacherAssignment.teacher_id)
            .filter(
                GroupTeacherAssignment.teacher_id == actor_id,
                GroupTeacherAssignment.status == _ASSIGNMENT_ACTIVE,
            )
        )
    elif role == _STUDENT:
        query = (
            query.join(Enrollment, Enrollment.group_id == Group.id)
            .join(User, User.id == Enrollment.student_id)
            .filter(
                Enrollment.student_id == actor_id,
                Enrollment.status == _ENROLLMENT_ACTIVE,
            )
        )
    else:
        return None
    return query.filter(
        User.role == role,
        User.status == _USER_ACTIVE,
        Group.status == _ACTIVE,
        Course.status == _ACTIVE,
        Level.status == _ACTIVE,
        AcademicTerm.status == _ACTIVE,
    )


def _group_dict(row):
    return {
        "id": row[0],
        "public_id": row[1],
        "name": row[2],
        "course_title": row[3],
        "level_name": row[4],
        "term_name": row[5],
    }


def member_group(actor_id, role, group_public_id):
    """``{id, public_id, name, course_title, level_name, term_name}`` for
    a Group `actor_id` may discuss in right now as `role`, else ``None`` --
    identical for every reason access is missing."""
    public_id = canonical_public_id(group_public_id)
    if public_id is None:
        return None
    query = _member_group_query(actor_id, role)
    if query is None:
        return None
    row = query.filter(Group.public_id == public_id).first()
    return _group_dict(row) if row is not None else None


def member_groups(actor_id, role, limit=GROUP_LIMIT):
    """``(groups, truncated)`` -- at most `limit` Groups `actor_id` may
    currently discuss in, ordered by name then internal id."""
    query = _member_group_query(actor_id, role)
    if query is None:
        return [], False
    rows = query.order_by(Group.name, Group.id).limit(limit + 1).all()
    return [_group_dict(row) for row in rows[:limit]], len(rows) > limit


def public_group(group):
    """The template-safe form of a Group dict: no internal id."""
    return {key: value for key, value in group.items() if key != "id"}


def topic_counts(group_ids):
    """``{group_id: (total, open)}`` for the given Groups, in one grouped
    query."""
    if not group_ids:
        return {}
    rows = (
        db.session.query(
            DiscussionTopic.group_id,
            func.count(DiscussionTopic.id),
            func.sum(case((DiscussionTopic.status == _OPEN, 1), else_=0)),
        )
        .filter(DiscussionTopic.group_id.in_(list(group_ids)))
        .group_by(DiscussionTopic.group_id)
        .all()
    )
    return {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in rows}


def build_group_cards(groups, counts):
    cards = []
    for group in groups:
        total, open_count = counts.get(group["id"], (0, 0))
        card = public_group(group)
        card["topic_count"] = total
        card["open_count"] = open_count
        cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


def topics_page(group_id, page):
    """``(rows, has_next)`` -- one page of a Group's topics, newest first
    by ``(created_at, id)``, reading ``TOPIC_PAGE_SIZE + 1`` rows so "is
    there another page" costs no COUNT."""
    rows = (
        db.session.query(
            DiscussionTopic.id,
            DiscussionTopic.public_id,
            DiscussionTopic.title,
            func.substr(DiscussionTopic.body, 1, _PREVIEW_FETCH),
            DiscussionTopic.status,
            DiscussionTopic.created_at,
            User.full_name,
            User.role,
        )
        .join(User, User.id == DiscussionTopic.author_id)
        .filter(DiscussionTopic.group_id == group_id)
        .order_by(DiscussionTopic.created_at.desc(), DiscussionTopic.id.desc())
        .offset((page - 1) * TOPIC_PAGE_SIZE)
        .limit(TOPIC_PAGE_SIZE + 1)
        .all()
    )
    return rows[:TOPIC_PAGE_SIZE], len(rows) > TOPIC_PAGE_SIZE


def reply_counts(topic_ids):
    """``{topic_id: count}`` for the given topics, in one grouped query."""
    if not topic_ids:
        return {}
    rows = (
        db.session.query(DiscussionReply.topic_id, func.count(DiscussionReply.id))
        .filter(DiscussionReply.topic_id.in_(list(topic_ids)))
        .group_by(DiscussionReply.topic_id)
        .all()
    )
    return {row[0]: int(row[1]) for row in rows}


def build_topic_list_view(rows, counts, tz_name):
    return [
        {
            "public_id": row[1],
            "title": row[2],
            "preview": preview_text(row[3]),
            "is_locked": row[4] == _LOCKED,
            "created_local": to_app_local(tz_name, row[5]),
            "author_name": row[6],
            "author_role_label": ROLE_LABELS.get(row[7], "Member"),
            "reply_count": counts.get(row[0], 0),
        }
        for row in rows
    ]


def group_topic(group_id, topic_public_id):
    """One topic of exactly `group_id`, else ``None`` -- for a malformed
    id, a missing topic and another Group's topic alike."""
    public_id = canonical_public_id(topic_public_id)
    if public_id is None:
        return None
    row = (
        db.session.query(
            DiscussionTopic.id,
            DiscussionTopic.public_id,
            DiscussionTopic.title,
            DiscussionTopic.body,
            DiscussionTopic.status,
            DiscussionTopic.version,
            DiscussionTopic.created_at,
            DiscussionTopic.updated_at,
            User.full_name,
            User.role,
        )
        .join(User, User.id == DiscussionTopic.author_id)
        .filter(
            DiscussionTopic.public_id == public_id,
            DiscussionTopic.group_id == group_id,
        )
        .first()
    )
    if row is None:
        return None
    return {
        "id": row[0],
        "public_id": row[1],
        "title": row[2],
        "body": row[3],
        "status": row[4],
        "version": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "author_name": row[8],
        "author_role": row[9],
    }


def public_topic(topic, tz_name):
    """The template-safe form of a topic: no internal id and no version."""
    return {
        "public_id": topic["public_id"],
        "title": topic["title"],
        "body": topic["body"],
        "is_locked": topic["status"] == _LOCKED,
        "author_name": topic["author_name"],
        "author_role_label": ROLE_LABELS.get(topic["author_role"], "Member"),
        "created_local": to_app_local(tz_name, topic["created_at"]),
        "updated_local": to_app_local(tz_name, topic["updated_at"]),
        "was_moderated": topic["updated_at"] != topic["created_at"],
    }


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


def replies_page(topic_id, page):
    """``(rows, has_next)`` -- one page of a topic's replies in reading
    order by ``(created_at, id)``, reading ``REPLY_PAGE_SIZE + 1`` rows."""
    rows = (
        db.session.query(
            DiscussionReply.public_id,
            DiscussionReply.body,
            DiscussionReply.created_at,
            DiscussionReply.author_id,
            User.full_name,
            User.role,
        )
        .join(User, User.id == DiscussionReply.author_id)
        .filter(DiscussionReply.topic_id == topic_id)
        .order_by(DiscussionReply.created_at.asc(), DiscussionReply.id.asc())
        .offset((page - 1) * REPLY_PAGE_SIZE)
        .limit(REPLY_PAGE_SIZE + 1)
        .all()
    )
    return rows[:REPLY_PAGE_SIZE], len(rows) > REPLY_PAGE_SIZE


def build_reply_view(rows, viewer_id, tz_name):
    return [
        {
            "public_id": row[0],
            "body": row[1],
            "created_local": to_app_local(tz_name, row[2]),
            "from_viewer": row[3] == viewer_id,
            "author_name": row[4],
            "author_role_label": ROLE_LABELS.get(row[5], "Member"),
        }
        for row in rows
    ]


def reply_page_number(topic_id, reply_public_id):
    """The timeline page that shows one reply of `topic_id` -- two bounded
    queries -- or page 1 when there is no such reply."""
    row = (
        db.session.query(DiscussionReply.id, DiscussionReply.created_at)
        .filter(
            DiscussionReply.topic_id == topic_id,
            DiscussionReply.public_id == reply_public_id,
        )
        .first()
    )
    if row is None:
        return 1
    reply_id, created_at = row
    position = (
        db.session.query(func.count(DiscussionReply.id))
        .filter(
            DiscussionReply.topic_id == topic_id,
            or_(
                DiscussionReply.created_at < created_at,
                and_(DiscussionReply.created_at == created_at, DiscussionReply.id <= reply_id),
            ),
        )
        .scalar()
    )
    return max(1, (int(position or 1) - 1) // REPLY_PAGE_SIZE + 1)


# ---------------------------------------------------------------------------
# Lock preview and duplicate-submission lookups
# ---------------------------------------------------------------------------


def lock_target(group_public_id):
    """The :class:`LockTarget` naming the rows a discussion write must
    lock for this Group, else ``None``. **Not an authorization decision**:
    it only names rows, and everything is proved again after the locks."""
    public_id = canonical_public_id(group_public_id)
    if public_id is None:
        return None
    row = (
        db.session.query(
            Group.id,
            Group.public_id,
            Group.academic_term_id,
            Course.level_id,
            Group.course_id,
        )
        .join(Course, Group.course_id == Course.id)
        .filter(Group.public_id == public_id)
        .first()
    )
    return LockTarget(*row) if row is not None else None


def topic_created_with_nonce(author_id, group_id, nonce):
    """The public id of the topic `author_id` already created in
    `group_id` with `nonce`, else ``None``. Scoped to the author and the
    Group, so a nonce can never lead anyone anywhere else."""
    return (
        db.session.query(DiscussionTopic.public_id)
        .filter(
            DiscussionTopic.creation_nonce == nonce,
            DiscussionTopic.author_id == author_id,
            DiscussionTopic.group_id == group_id,
        )
        .scalar()
    )


def reply_created_with_nonce(author_id, topic_id, nonce):
    """The public id of the reply `author_id` already posted to
    `topic_id` with `nonce`, else ``None``."""
    return (
        db.session.query(DiscussionReply.public_id)
        .filter(
            DiscussionReply.creation_nonce == nonce,
            DiscussionReply.author_id == author_id,
            DiscussionReply.topic_id == topic_id,
        )
        .scalar()
    )


def topic_in_group(topic_id, group_id):
    """``True`` when `topic_id` belongs to `group_id`."""
    return (
        db.session.query(DiscussionTopic.id)
        .filter(DiscussionTopic.id == topic_id, DiscussionTopic.group_id == group_id)
        .first()
        is not None
    )
