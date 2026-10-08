"""Append-only display changes; original correspondence is retained."""
import uuid

from sqlalchemy import event

from app.extensions import db
from app.models.message import MESSAGE_BODY_MAX_LENGTH
from app.models.message_thread import MESSAGE_NONCE_LENGTH, utc_whole_second_now


class MessageChange(db.Model):
    """Only the original sender may append an edit or a terminal hide.

    Authorship is derived from Message.sender_id, rather than duplicated.
    A hide removes the message from both participants' rendered views.
    """
    __tablename__ = "message_changes"
    __table_args__ = (
        db.CheckConstraint("(kind = 'edit' AND body IS NOT NULL AND LENGTH(TRIM(body)) > 0) OR (kind = 'hide' AND body IS NULL)", name="ck_message_changes_payload"),
        db.Index("ix_message_changes_message_id", "message_id", "id"),
    )
    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    message_id = db.Column(db.BigInteger(), db.ForeignKey("messages.id"), nullable=False)
    kind = db.Column(db.String(8, collation="utf8mb4_0900_bin"), nullable=False)
    body = db.Column(db.String(MESSAGE_BODY_MAX_LENGTH), nullable=True)
    creation_nonce = db.Column(db.String(MESSAGE_NONCE_LENGTH), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_whole_second_now)


class MessageThreadClear(db.Model):
    """One participant's clear through a locked message watermark.

    Future replies become visible again. The other member keeps their history.
    """
    __tablename__ = "message_thread_clears"
    __table_args__ = (
        db.Index("ix_message_thread_clears_member_id", "member_id", "id"),
        db.Index("ix_message_thread_clears_message", "through_message_id"),
    )
    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    member_id = db.Column(db.BigInteger(), db.ForeignKey("message_thread_members.id"), nullable=False)
    through_message_id = db.Column(db.BigInteger(), db.ForeignKey("messages.id"), nullable=False)
    creation_nonce = db.Column(db.String(MESSAGE_NONCE_LENGTH), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_whole_second_now)


def _immutable(_mapper, _connection, _target):
    raise ValueError("Message display history is append-only.")


for _model in (MessageChange, MessageThreadClear):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
