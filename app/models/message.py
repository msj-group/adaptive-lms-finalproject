import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.message_thread import (
    MESSAGE_NONCE_LENGTH,
    utc_whole_second_now,
    valid_creation_nonce,
)

#: Hard limit for a message body, after normalisation
#: (``app/services/message_text.py``).
MESSAGE_BODY_MAX_LENGTH = 5000


class Message(db.Model):
    """One immutable plain-text message in a
    :class:`~app.models.message_thread.MessageThread` (Phase 4 / M11).

    **Original messages are append-only.** Sender-authorized display edits
    and hides are recorded separately in MessageChange; clearing history
    is scoped to a MessageThreadMember. This original row is retained. ``body`` is
    plain text: it is stored exactly as normalised and always rendered
    escaped, never through ``| safe``.

    ``sender_id`` is a plain foreign key into ``users``; that the sender is
    a member of the thread, holds the Student or Teacher role, has an
    active account and still shares an operational Group with the other
    member is proved against locked rows by
    ``app/services/message_transactions.py`` before the insert.

    ``creation_nonce`` is UNIQUE and comes from the signed compose or reply
    token, so a double-clicked or replayed submission appends at most one
    message. It is never an authorization mechanism.

    Indexes, one per real query path:

    - ``ix_messages_thread_created_id`` (``thread_id``, ``created_at``,
      ``id``) -- the conversation page's ``ORDER BY created_at, id`` and
      the inbox's newest-message lookup per thread; also the leftmost
      prefix InnoDB requires for the ``thread_id`` foreign key;
    - ``ix_messages_sender_created_id`` (``sender_id``, ``created_at``,
      ``id``) -- the index the ``sender_id`` foreign key requires.

    No ORM relationship and no cascade, for the reasons given on
    :class:`~app.models.message_thread.MessageThread`.
    """

    __tablename__ = "messages"
    __table_args__ = (
        db.CheckConstraint("LENGTH(TRIM(body)) > 0", name="ck_messages_body_not_blank"),
        db.Index("ix_messages_thread_created_id", "thread_id", "created_at", "id"),
        db.Index("ix_messages_sender_created_id", "sender_id", "created_at", "id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    thread_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("message_threads.id"),
        nullable=False,
    )
    sender_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    body = db.Column(db.String(MESSAGE_BODY_MAX_LENGTH), nullable=False)
    creation_nonce = db.Column(db.String(MESSAGE_NONCE_LENGTH), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_whole_second_now)

    @validates("body")
    def validate_body(self, _key, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A message body must not be blank.")
        if len(value) > MESSAGE_BODY_MAX_LENGTH:
            raise ValueError("A message body is too long.")
        return value

    @validates("creation_nonce")
    def validate_creation_nonce(self, _key, value):
        if not valid_creation_nonce(value):
            raise ValueError("Invalid creation nonce.")
        return value
