import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db

#: Hard limit for a thread subject, after normalisation
#: (``app/services/message_text.py``).
MESSAGE_SUBJECT_MAX_LENGTH = 150

#: A creation nonce is a server-minted ``secrets.token_hex(32)`` value.
MESSAGE_NONCE_LENGTH = 64

_NONCE_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")


def utc_whole_second_now():
    """The current moment as naive UTC truncated to the whole second --
    the precision every messaging timestamp column holds."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def valid_creation_nonce(value):
    """``True`` for exactly 64 lower-case hexadecimal characters."""
    return isinstance(value, str) and bool(_NONCE_PATTERN.match(value))


class MessageThread(db.Model):
    """One private conversation between exactly one Student and exactly
    one Teacher (Phase 4 / M11).

    **Append-only by construction.** A thread row is inserted once, in
    the same transaction as its two :class:`MessageThreadMember` rows and
    its initial :class:`~app.models.message.Message`, and is never
    updated or deleted afterwards: there is no edit, archive, delete or
    "last activity" column to rewrite. The inbox derives recency from the
    newest message instead, so a ``GET`` has nothing it could mutate.

    **No ORM relationship is declared in either direction**, deliberately
    -- so no cascade, no delete-orphan ownership and no template lazy load
    can exist. Every read goes through the explicit, bounded, column-
    projected queries in ``app/services/message_queries.py``.

    **Invariants a CHECK cannot express** -- exactly two members, one of
    them a Student and the other a Teacher, the creator being one of them,
    and a currently shared operational Group at the moment of creation --
    are proved by ``app/services/message_transactions.py`` against locked
    rows before the insert. ``created_by_id`` is a plain foreign key into
    ``users``: it proves the row exists, never its role or status.

    ``creation_nonce`` is the duplicate-submission defense: a random value
    minted by the server into the signed compose token, UNIQUE here, so a
    replayed or double-clicked submission can create at most one thread.
    It is never an authorization mechanism.

    Indexes: ``public_id`` and ``creation_nonce`` are UNIQUE, and
    ``ix_message_threads_created_by_created_id`` is the leftmost-prefix
    index InnoDB requires for the ``created_by_id`` foreign key.
    """

    __tablename__ = "message_threads"
    __table_args__ = (
        db.CheckConstraint(
            "LENGTH(TRIM(subject)) > 0", name="ck_message_threads_subject_not_blank"
        ),
        db.Index(
            "ix_message_threads_created_by_created_id",
            "created_by_id",
            "created_at",
            "id",
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    created_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    subject = db.Column(db.String(MESSAGE_SUBJECT_MAX_LENGTH), nullable=False)
    creation_nonce = db.Column(db.String(MESSAGE_NONCE_LENGTH), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_whole_second_now)

    @validates("subject")
    def validate_subject(self, _key, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A message subject must not be blank.")
        if len(value) > MESSAGE_SUBJECT_MAX_LENGTH:
            raise ValueError("A message subject is too long.")
        return value

    @validates("creation_nonce")
    def validate_creation_nonce(self, _key, value):
        if not valid_creation_nonce(value):
            raise ValueError("Invalid creation nonce.")
        return value


class MessageThreadMember(db.Model):
    """One participant of one :class:`MessageThread` (Phase 4 / M11).

    Exactly two rows exist per thread, both inserted with the thread and
    never changed afterwards. **Historical access depends on this row
    alone**: a member keeps reading the conversation after the academic
    relationship that allowed it has ended, while *sending* is re-proved
    from the current, locked relationship on every POST.

    ``uq_message_thread_members_thread_user`` prevents a user appearing
    twice in one thread and is the leftmost-prefix index for the
    ``thread_id`` foreign key; ``ix_message_thread_members_user_thread``
    serves the inbox membership lookup and the ``user_id`` foreign key.
    No ORM relationship and no cascade, for the reasons given on
    :class:`MessageThread`.
    """

    __tablename__ = "message_thread_members"
    __table_args__ = (
        db.UniqueConstraint(
            "thread_id", "user_id", name="uq_message_thread_members_thread_user"
        ),
        db.Index("ix_message_thread_members_user_thread", "user_id", "thread_id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    thread_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("message_threads.id"),
        nullable=False,
    )
    user_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    joined_at = db.Column(db.DateTime, nullable=False, default=utc_whole_second_now)
