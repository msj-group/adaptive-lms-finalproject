"""Membership/ownership checked, serialized message display operations.

No DDL is executed here. Until the separately approved schema upgrade exists,
ordinary messaging uses its existing queries and management is unavailable.
"""
import time

from flask import current_app
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Message, MessageThread, MessageThreadMember, User, UserRole, UserStatus
from app.models.message_change import MessageChange, MessageThreadClear


def available():
    """Read-only, short-lived schema capability check; never create tables."""
    cache = current_app.extensions.get("message_management_schema")
    now = time.monotonic()
    if cache and cache[0] is db.engine and now - cache[1] < 10:
        return cache[2]
    inspector = inspect(db.engine)
    ready = inspector.has_table("message_changes") and inspector.has_table("message_thread_clears")
    current_app.extensions["message_management_schema"] = (db.engine, now, ready)
    return ready


def _lock_member(actor_id, thread_id):
    # User -> Thread -> Members -> Message. Same suffix order as sending,
    # with no academic locks because historical ownership is sufficient.
    db.session.rollback()
    actor = db.session.query(User).filter_by(id=actor_id).with_for_update().first()
    if actor is None or actor.status != UserStatus.ACTIVE.value or actor.role not in (UserRole.STUDENT.value, UserRole.TEACHER.value):
        return None
    thread = db.session.query(MessageThread).filter_by(id=thread_id).with_for_update().first()
    if thread is None:
        return None
    members = db.session.query(MessageThreadMember).filter_by(thread_id=thread_id).order_by(MessageThreadMember.id).limit(3).with_for_update().all()
    if len(members) != 2:
        return None
    return next((member for member in members if member.user_id == actor_id), None)


def latest_message_public_id(thread_id):
    return db.session.query(Message.public_id).filter_by(thread_id=thread_id).order_by(Message.id.desc()).limit(1).scalar()


def change_message(actor_id, thread_id, message_public_id, kind, body, expected_revision, nonce):
    """Return saved/duplicate/stale/unavailable/conflict, without partial writes."""
    if kind not in ("edit", "hide") or (kind == "edit" and (not isinstance(body, str) or not body.strip() or len(body) > 5000)):
        return "unavailable"
    try:
        member = _lock_member(actor_id, thread_id)
        if member is None:
            db.session.rollback()
            return "unavailable"
        message = db.session.query(Message).filter_by(public_id=message_public_id, thread_id=thread_id, sender_id=actor_id).with_for_update().first()
        if message is None:
            db.session.rollback()
            return "unavailable"
        replay = db.session.query(MessageChange.id).filter_by(message_id=message.id, creation_nonce=nonce, kind=kind).first()
        if replay:
            db.session.rollback()
            return "duplicate"
        latest = db.session.query(MessageChange).filter_by(message_id=message.id).order_by(MessageChange.id.desc()).first()
        cleared_through = db.session.query(db.func.max(MessageThreadClear.through_message_id)).filter_by(member_id=member.id).scalar() or 0
        if message.id <= cleared_through or (latest and latest.kind == "hide"):
            db.session.rollback()
            return "unavailable"
        if (latest.public_id if latest else "original") != expected_revision:
            db.session.rollback()
            return "stale"
        db.session.add(MessageChange(message_id=message.id, kind=kind, body=body if kind == "edit" else None, creation_nonce=nonce))
        db.session.commit()
        return "saved"
    except IntegrityError:
        db.session.rollback()
        return "conflict"


def clear_thread(actor_id, thread_id, expected_latest, nonce):
    """Clear only this member's history, refusing to discard a newer reply."""
    try:
        member = _lock_member(actor_id, thread_id)
        if member is None:
            db.session.rollback()
            return "unavailable"
        replay = db.session.query(MessageThreadClear.id).filter_by(member_id=member.id, creation_nonce=nonce).first()
        if replay:
            db.session.rollback()
            return "duplicate"
        latest = db.session.query(Message).filter_by(thread_id=thread_id).order_by(Message.id.desc()).first()
        if latest is None or latest.public_id != expected_latest:
            db.session.rollback()
            return "stale"
        db.session.add(MessageThreadClear(member_id=member.id, through_message_id=latest.id, creation_nonce=nonce))
        db.session.commit()
        return "saved"
    except IntegrityError:
        db.session.rollback()
        return "conflict"
