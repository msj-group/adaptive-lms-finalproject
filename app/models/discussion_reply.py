import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.discussion_topic import (
    DISCUSSION_BODY_MAX_LENGTH,
    DISCUSSION_NONCE_LENGTH,
    discussion_now,
    valid_discussion_nonce,
)


class DiscussionReply(db.Model):
    """One immutable plain-text contribution to a
    :class:`~app.models.discussion_topic.DiscussionTopic` (Phase 4 / M12).

    **Replies are append-only.** A row is inserted once and never edited,
    hidden, restored or deleted -- there is no route for any of those, and
    the mapper listeners below refuse an update or a delete at flush time.
    ``body`` is plain text: it is stored exactly as normalised and always
    rendered escaped, never through ``| safe``.

    ``author_id`` is a plain foreign key into ``users``; that the author is
    an active Student enrolled in, or an active Teacher assigned to, the
    topic's operational Group -- and that the locked topic is ``open`` --
    is proved against locked rows by
    ``app/services/discussion_transactions.py`` before the insert.

    ``creation_nonce`` is UNIQUE and comes from the signed reply token, so a
    double-clicked or replayed submission appends at most one reply. It is
    never an authorization mechanism.

    Indexes, one per real query path:

    - ``ix_discussion_replies_topic_created_id`` (``topic_id``,
      ``created_at``, ``id``) -- the topic timeline's deterministic
      ``ORDER BY created_at, id``; also the leftmost prefix InnoDB requires
      for the ``topic_id`` foreign key;
    - ``ix_discussion_replies_author_created_id`` (``author_id``,
      ``created_at``, ``id``) -- the index the ``author_id`` foreign key
      requires.

    No ORM relationship and no cascade, for the reasons given on
    :class:`~app.models.discussion_topic.DiscussionTopic`.
    """

    __tablename__ = "discussion_replies"
    __table_args__ = (
        db.CheckConstraint(
            "LENGTH(TRIM(body)) > 0", name="ck_discussion_replies_body_not_blank"
        ),
        db.Index("ix_discussion_replies_topic_created_id", "topic_id", "created_at", "id"),
        db.Index("ix_discussion_replies_author_created_id", "author_id", "created_at", "id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    topic_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("discussion_topics.id"),
        nullable=False,
    )
    author_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    body = db.Column(db.String(DISCUSSION_BODY_MAX_LENGTH), nullable=False)
    creation_nonce = db.Column(db.String(DISCUSSION_NONCE_LENGTH), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=discussion_now)

    @validates("body")
    def validate_body(self, _key, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A discussion reply must not be blank.")
        if len(value) > DISCUSSION_BODY_MAX_LENGTH:
            raise ValueError("A discussion reply is too long.")
        return value

    @validates("creation_nonce")
    def validate_creation_nonce(self, _key, value):
        if not valid_discussion_nonce(value):
            raise ValueError("Invalid creation nonce.")
        return value


@event.listens_for(DiscussionReply, "before_update")
def _reply_is_immutable(_mapper, _connection, target):
    state = inspect(target)
    changed = sorted(
        attr.key
        for attr in state.mapper.column_attrs
        if state.attrs[attr.key].history.has_changes()
    )
    if changed:
        raise ValueError("A discussion reply cannot change after it is posted.")


@event.listens_for(DiscussionReply, "before_delete")
def _reply_is_never_deleted(_mapper, _connection, _target):
    raise ValueError("A discussion reply is never deleted.")
