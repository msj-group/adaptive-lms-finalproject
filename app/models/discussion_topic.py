from app.models.code_types import CODE_COLLATION
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import event, inspect
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import DiscussionTopicStatus

#: Hard limit for a topic title, after normalisation
#: (``app/services/discussion_text.py``).
DISCUSSION_TITLE_MAX_LENGTH = 150

#: Hard limit for a topic body and for a reply body, after normalisation.
DISCUSSION_BODY_MAX_LENGTH = 5000

#: A creation nonce is a server-minted ``secrets.token_hex(32)`` value.
DISCUSSION_NONCE_LENGTH = 64

_NONCE_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")

_STATUS_VALUES = tuple(status.value for status in DiscussionTopicStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{value}'" for value in _STATUS_VALUES) + ")"

#: The only topic columns that may change after insertion -- and only
#: through the Teacher lock / reopen transaction.
TOPIC_MUTABLE_COLUMNS = frozenset({"status", "version", "updated_at"})


def discussion_now():
    """The current moment as naive UTC truncated to the whole second --
    the precision every discussion timestamp column holds."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def valid_discussion_nonce(value):
    """``True`` for exactly 64 lower-case hexadecimal characters."""
    return isinstance(value, str) and bool(_NONCE_PATTERN.match(value))


def _same_moment_as_created_at(context):
    """``updated_at``'s insert default: the row's own ``created_at``, so a
    topic that was never locked or reopened carries one moment, not two."""
    return context.get_current_parameters().get("created_at") or discussion_now()


class DiscussionTopic(db.Model):
    """One classroom discussion topic owned by exactly one Group
    (Phase 4 / M12).

    **Created by a Teacher, read and answered by the Group.** An actively
    assigned Teacher creates a topic; the Group's actively assigned
    Teachers and actively enrolled Students may read it and reply while it
    is ``open``. Reading requires the **current** relationship: unlike a
    private message thread, nothing here survives a withdrawn Enrollment,
    a removed assignment, a suspended account or an archived Group or
    ancestor -- the rows stay, access does not.

    **Immutable content.** ``group_id``, ``author_id``, ``title``, ``body``,
    ``creation_nonce`` and ``created_at`` never change after insertion,
    and a topic is never deleted; the mapper listeners below refuse both
    at flush time. The only mutable state is ``status``, ``version`` and
    ``updated_at``, changed together by the Teacher lock / reopen
    transaction in ``app/services/discussion_transactions.py``. ``version``
    starts at 1 and moves only when ``status`` moves, so a form rendered
    against one lock state can be recognised as stale against another.

    **No ORM relationship is declared in either direction**, deliberately
    -- so no cascade, no delete-orphan ownership and no template lazy load
    of a Group's or a topic's unbounded history can exist. Every read goes
    through the bounded, column-projected queries in
    ``app/services/discussion_queries.py``.

    ``group_id`` and ``author_id`` are plain foreign keys: they prove the
    rows exist, never that the author is a Teacher, that the account is
    active or that the assignment is current. Those are proved against
    locked rows before the insert.

    ``creation_nonce`` is the duplicate-submission defense: a random value
    minted by the server into the signed creation token, UNIQUE here, so a
    replayed or double-clicked submission can create at most one topic. It
    is never an authorization mechanism.

    Indexes, one per real query path:

    - ``ix_discussion_topics_group_created_id`` (``group_id``,
      ``created_at``, ``id``) -- the Group topic list's deterministic
      ``ORDER BY created_at DESC, id DESC``; also the leftmost prefix
      InnoDB requires for the ``group_id`` foreign key;
    - ``ix_discussion_topics_author_created_id`` (``author_id``,
      ``created_at``, ``id``) -- the index the ``author_id`` foreign key
      requires.
    """

    __tablename__ = "discussion_topics"
    __table_args__ = (
        db.CheckConstraint(
            "LENGTH(TRIM(title)) > 0", name="ck_discussion_topics_title_not_blank"
        ),
        db.CheckConstraint(
            "LENGTH(TRIM(body)) > 0", name="ck_discussion_topics_body_not_blank"
        ),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_discussion_topics_status_valid"),
        db.CheckConstraint("version > 0", name="ck_discussion_topics_version_positive"),
        db.Index("ix_discussion_topics_group_created_id", "group_id", "created_at", "id"),
        db.Index("ix_discussion_topics_author_created_id", "author_id", "created_at", "id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    group_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("groups.id"),
        nullable=False,
    )
    author_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    title = db.Column(db.String(DISCUSSION_TITLE_MAX_LENGTH), nullable=False)
    body = db.Column(db.String(DISCUSSION_BODY_MAX_LENGTH), nullable=False)
    status = db.Column(
        db.String(16, collation=CODE_COLLATION), nullable=False, default=DiscussionTopicStatus.OPEN.value
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    creation_nonce = db.Column(db.String(DISCUSSION_NONCE_LENGTH), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=discussion_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_same_moment_as_created_at)

    @validates("title")
    def validate_title(self, _key, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A discussion topic title must not be blank.")
        if len(value) > DISCUSSION_TITLE_MAX_LENGTH:
            raise ValueError("A discussion topic title is too long.")
        return value

    @validates("body")
    def validate_body(self, _key, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A discussion topic body must not be blank.")
        if len(value) > DISCUSSION_BODY_MAX_LENGTH:
            raise ValueError("A discussion topic body is too long.")
        return value

    @validates("status")
    def validate_status(self, _key, value):
        if not isinstance(value, str) or value not in _STATUS_VALUES:
            raise ValueError(f"Invalid discussion topic status: {value!r}")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("A discussion topic version must be a positive integer.")
        return value

    @validates("creation_nonce")
    def validate_creation_nonce(self, _key, value):
        if not valid_discussion_nonce(value):
            raise ValueError("Invalid creation nonce.")
        return value


@event.listens_for(DiscussionTopic, "before_update")
def _topic_content_is_immutable(_mapper, _connection, target):
    """Refuse a flush that would change anything but the lock state."""
    state = inspect(target)
    changed = sorted(
        attr.key
        for attr in state.mapper.column_attrs
        if attr.key not in TOPIC_MUTABLE_COLUMNS
        and state.attrs[attr.key].history.has_changes()
    )
    if changed:
        raise ValueError(
            "A discussion topic's " + ", ".join(changed) + " cannot change after it is created."
        )


@event.listens_for(DiscussionTopic, "before_delete")
def _topic_is_never_deleted(_mapper, _connection, _target):
    raise ValueError("A discussion topic is never deleted.")
