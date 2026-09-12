"""Signed, expiring form-state tokens for private messaging
(Phase 4 / M11).

Two purposes, each under its own salt:

- ``message-create`` binds the **acting user**, the **selected recipient**
  and a server-minted **nonce**;
- ``message-reply`` binds the **acting user**, the **thread** and a
  server-minted **nonce**.

A token minted under any other salt in this project -- every earlier
milestone's, or the other messaging purpose's -- fails signature
verification here even though all are signed with the same
``SECRET_KEY``; a token for another user, another recipient or another
thread fails the binding check; and a token older than
:data:`TOKEN_MAX_AGE_SECONDS` fails the timestamp check. Every failure is
the same ``None``: rejected, never trusted, never repaired.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, a purpose and a random nonce only -- no subject, no body, no
name and no internal database id.

**The nonce is duplicate-submission defense, not authorization.** It is
stored as the UNIQUE ``creation_nonce`` of the thread / message the
submission creates, so a double click, a browser retry or a replay of the
same form creates at most one row. Who may send is decided separately,
from locked rows, on every POST. Because the next rendered form always
carries a fresh nonce, state rotates after every successful POST.
"""

import secrets

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.models.message_thread import valid_creation_nonce

PURPOSE_CREATE = "message-create"
PURPOSE_REPLY = "message-reply"
PURPOSES = (PURPOSE_CREATE, PURPOSE_REPLY)

#: The version suffix is part of the salt so a future payload change
#: invalidates every token in flight instead of reinterpreting one.
_SALTS = {
    PURPOSE_CREATE: "messages.thread-create.phase4-m11.v1",
    PURPOSE_REPLY: "messages.thread-reply.phase4-m11.v1",
}

#: The one bound target field per purpose.
_TARGET_FIELD = {
    PURPOSE_CREATE: "recipient_public_id",
    PURPOSE_REPLY: "thread_public_id",
}

_FIELDS = {
    purpose: ("purpose", "actor_public_id", _TARGET_FIELD[purpose], "nonce")
    for purpose in PURPOSES
}

#: How long a rendered compose or reply form stays usable. Long enough to
#: write a careful message; an expired form is re-rendered with the typed
#: text preserved and a fresh token, never accepted.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

#: Anything longer is not a token this module minted.
_MAX_TOKEN_LENGTH = 1024


def new_nonce():
    """A fresh 64-character hexadecimal creation nonce."""
    return secrets.token_hex(32)


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def make_token(purpose, actor_public_id, target_public_id, nonce=None):
    """Sign one exact-shape token for `purpose`. A missing `nonce` is
    minted here; a re-rendered form that must keep its original nonce
    passes it back explicitly."""
    body = {
        "purpose": purpose,
        "actor_public_id": actor_public_id,
        _TARGET_FIELD[purpose]: target_public_id,
        "nonce": nonce or new_nonce(),
    }
    return _serializer(purpose).dumps(body)


def load_token(purpose, token, actor_public_id, target_public_id):
    """The payload when `token` is a genuine, unexpired `purpose` token
    for exactly this actor and target -- else ``None``.

    ``None`` covers a missing, oversized, malformed, tampered,
    wrong-salt, wrong-purpose, expired, wrong-shape, cross-user,
    cross-recipient and cross-thread token alike.
    """
    if purpose not in _SALTS:
        return None
    if not token or not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    try:
        payload = _serializer(purpose).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except BadData:
        return None
    fields = _FIELDS[purpose]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return None
    if any(not isinstance(payload[field], str) for field in fields):
        return None
    if payload["purpose"] != purpose:
        return None
    if not valid_creation_nonce(payload["nonce"]):
        return None
    if payload["actor_public_id"] != actor_public_id:
        return None
    if payload[_TARGET_FIELD[purpose]] != target_public_id:
        return None
    return payload
