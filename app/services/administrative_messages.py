"""Private administrative sends; the academic message lock chain stays separate.

At least one freshly locked participant must be an active Administrator.
Lock order: User rows by id -> Thread -> its Members. There are no academic
locks or invented Student/Teacher identities in this path. Reads and signed
token verification remain with the existing membership-scoped HTTP workflow.
"""
import uuid
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Message, MessageThread, MessageThreadMember, User, UserStatus
from app.services import message_queries, message_transactions as sends


def _lock_pair(sender_id, other_id, thread_id=None):
    db.session.rollback()
    if sender_id is None or other_id is None or sender_id == other_id:
        return None
    users = {}
    for user_id in sorted((sender_id, other_id)):
        users[user_id] = User.query.filter_by(id=user_id).with_for_update().first()
    sender, other = users[sender_id], users[other_id]
    if sender is None or other is None or any(user.status != UserStatus.ACTIVE.value for user in (sender, other)):
        db.session.rollback()
        return None
    if message_queries.administrative_pair(sender.id, sender.role, other.id, other.role) is None:
        db.session.rollback()
        return None
    if thread_id is not None:
        thread = MessageThread.query.filter_by(id=thread_id).with_for_update().first()
        members = MessageThreadMember.query.filter_by(thread_id=thread_id).order_by(
            MessageThreadMember.id).limit(3).with_for_update().all()
        if sends.thread_proof_error(SimpleNamespace(thread=thread, members=members), thread_id, sender_id, other_id):
            db.session.rollback()
            return None
    return users


def send_new_thread(sender_id, recipient_id, subject, body, nonce):
    """One atomic thread/member/message commit, after current-role proof."""
    if _lock_pair(sender_id, recipient_id) is None:
        return sends.SendOutcome(sends.UNAVAILABLE, None, None)
    if sends._nonce_used_by_thread(nonce):
        db.session.rollback()
        return sends._creation_replay(sender_id, nonce)
    now = sends._now()
    public_id = str(uuid.uuid4())
    thread = MessageThread(public_id=public_id, created_by_id=sender_id,
                           subject=subject, creation_nonce=nonce, created_at=now)
    try:
        db.session.add(thread)
        db.session.flush()
        db.session.add_all([MessageThreadMember(thread_id=thread.id, user_id=user_id, joined_at=now)
                            for user_id in sorted((sender_id, recipient_id))])
        message = Message(public_id=str(uuid.uuid4()), thread_id=thread.id, sender_id=sender_id,
                          body=body, creation_nonce=nonce, created_at=now)
        db.session.add(message)
        db.session.flush()
        message_id = message.id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return sends._creation_replay(sender_id, nonce)
    return sends.SendOutcome(sends.SENT, public_id, message_id)


def send_reply(sender_id, other_id, thread_id, body, nonce):
    """A reply requires exactly these two locked members and an active admin pair."""
    if _lock_pair(sender_id, other_id, thread_id) is None:
        return sends.SendOutcome(sends.UNAVAILABLE, None, None)
    if sends._nonce_used_by_message(nonce):
        db.session.rollback()
        return sends._reply_replay(sender_id, thread_id, nonce)
    message = Message(public_id=str(uuid.uuid4()), thread_id=thread_id, sender_id=sender_id,
                      body=body, creation_nonce=nonce, created_at=sends._now())
    try:
        db.session.add(message)
        db.session.flush()
        message_id = message.id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return sends._reply_replay(sender_id, thread_id, nonce)
    return sends.SendOutcome(sends.SENT, None, message_id)
