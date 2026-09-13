"""Signed, expiring form-state tokens for Group discussions
(Phase 4 / M12).

Three purposes, each under its own salt:

- ``discussion-topic-create`` binds the **acting Teacher**, the **Group**
  and a server-minted **nonce**;
- ``discussion-reply`` binds the **acting user**, the **Group**, the
  **topic**, the topic's **version** when the page was rendered, and a
  server-minted **nonce**;
- ``discussion-moderate`` binds the **acting Teacher**, the **Group**, the
  **topic**, the topic's **version** and the intended **action**
  (``lock`` or ``reopen``).

A token minted under any other salt in this project -- every earlier
milestone's, or another discussion purpose's -- fails signature
verification here even though all are signed with the same
``SECRET_KEY``; a token for another user, another Group, another topic or
another action fails the binding check; a token whose payload is not
exactly this purpose's shape is refused; and a token older than
:data:`TOKEN_MAX_AGE_SECONDS` fails the timestamp check. Every failure is
the same ``None``: rejected, never trusted, never repaired.

**The version is returned, not compared, by the reply and moderation
loaders.** A token for an older version is still a genuine token; whether
the topic has changed since is a question about current state, which the
route previews and the transaction decides again under the topic lock.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, a purpose, a version counter, an action and a random nonce
only -- no title, no body, no name, no roster and no internal database id.

**The nonce is duplicate-submission defense, not authorization.** It is
stored as the UNIQUE ``creation_nonce`` of the topic or reply the
submission creates, so a double click, a browser retry or a replay of the
same form creates at most one row. Who may write is decided separately,
from locked rows, on every POST.
"""

import secrets

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.models.discussion_topic import valid_discussion_nonce

PURPOSE_CREATE = "discussion-topic-create"
PURPOSE_REPLY = "discussion-reply"
PURPOSE_MODERATE = "discussion-moderate"
PURPOSES = (PURPOSE_CREATE, PURPOSE_REPLY, PURPOSE_MODERATE)

ACTION_LOCK = "lock"
ACTION_REOPEN = "reopen"
ACTIONS = (ACTION_LOCK, ACTION_REOPEN)

#: The version suffix is part of the salt so a future payload change
#: invalidates every token in flight instead of reinterpreting one.
_SALTS = {
    PURPOSE_CREATE: "discussions.topic-create.phase4-m12.v1",
    PURPOSE_REPLY: "discussions.reply.phase4-m12.v1",
    PURPOSE_MODERATE: "discussions.moderate.phase4-m12.v1",
}

#: The exact key set of each purpose's payload.
_FIELDS = {
    PURPOSE_CREATE: frozenset({"purpose", "actor_public_id", "group_public_id", "nonce"}),
    PURPOSE_REPLY: frozenset(
        {"purpose", "actor_public_id", "group_public_id", "topic_public_id",
         "topic_version", "nonce"}
    ),
    PURPOSE_MODERATE: frozenset(
        {"purpose", "actor_public_id", "group_public_id", "topic_public_id",
         "topic_version", "action"}
    ),
}

#: How long a rendered form stays usable. Long enough to write a careful
#: post; an expired form is re-rendered with the typed text preserved and
#: a fresh token, never accepted.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

#: Anything longer is not a token this module minted.
_MAX_TOKEN_LENGTH = 1024

#: The largest version a token may carry -- the ``INTEGER`` column bound.
_MAX_VERSION = 2**31 - 1


def new_nonce():
    """A fresh 64-character hexadecimal creation nonce."""
    return secrets.token_hex(32)


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def make_create_token(actor_public_id, group_public_id, nonce=None):
    """A topic-creation token. A missing `nonce` is minted here; a form
    re-rendered after a text error passes its original nonce back."""
    return _serializer(PURPOSE_CREATE).dumps(
        {
            "purpose": PURPOSE_CREATE,
            "actor_public_id": actor_public_id,
            "group_public_id": group_public_id,
            "nonce": nonce or new_nonce(),
        }
    )


def make_reply_token(actor_public_id, group_public_id, topic_public_id, topic_version,
                     nonce=None):
    """A reply token bound to the topic's current version."""
    return _serializer(PURPOSE_REPLY).dumps(
        {
            "purpose": PURPOSE_REPLY,
            "actor_public_id": actor_public_id,
            "group_public_id": group_public_id,
            "topic_public_id": topic_public_id,
            "topic_version": topic_version,
            "nonce": nonce or new_nonce(),
        }
    )


def make_moderation_token(actor_public_id, group_public_id, topic_public_id, topic_version,
                          action):
    """A lock or reopen token bound to the topic's current version."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown discussion moderation action: {action!r}")
    return _serializer(PURPOSE_MODERATE).dumps(
        {
            "purpose": PURPOSE_MODERATE,
            "actor_public_id": actor_public_id,
            "group_public_id": group_public_id,
            "topic_public_id": topic_public_id,
            "topic_version": topic_version,
            "action": action,
        }
    )


def _load(purpose, token):
    """The payload of a genuine, unexpired, exact-shape `purpose` token,
    else ``None``. Bindings are checked by the public loaders."""
    if not token or not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    try:
        payload = _serializer(purpose).loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except BadData:
        return None
    fields = _FIELDS[purpose]
    if not isinstance(payload, dict) or set(payload) != fields:
        return None
    if any(not isinstance(payload[field], str) for field in fields - {"topic_version"}):
        return None
    if payload["purpose"] != purpose:
        return None
    if "topic_version" in fields and not _valid_version(payload["topic_version"]):
        return None
    if "nonce" in fields and not valid_discussion_nonce(payload["nonce"]):
        return None
    if "action" in fields and payload["action"] not in ACTIONS:
        return None
    return payload


def load_create_token(token, actor_public_id, group_public_id):
    """The payload when `token` is a genuine topic-creation token for
    exactly this Teacher and Group -- else ``None``."""
    payload = _load(PURPOSE_CREATE, token)
    if payload is None:
        return None
    if payload["actor_public_id"] != actor_public_id:
        return None
    if payload["group_public_id"] != group_public_id:
        return None
    return payload


def load_reply_token(token, actor_public_id, group_public_id, topic_public_id):
    """The payload when `token` is a genuine reply token for exactly this
    user, Group and topic -- else ``None``. ``topic_version`` is returned
    for the caller to compare with current state."""
    payload = _load(PURPOSE_REPLY, token)
    if payload is None:
        return None
    if payload["actor_public_id"] != actor_public_id:
        return None
    if payload["group_public_id"] != group_public_id:
        return None
    if payload["topic_public_id"] != topic_public_id:
        return None
    return payload


def load_moderation_token(token, actor_public_id, group_public_id, topic_public_id, action):
    """The payload when `token` is a genuine moderation token for exactly
    this Teacher, Group, topic and `action` -- else ``None``."""
    payload = _load(PURPOSE_MODERATE, token)
    if payload is None:
        return None
    if payload["actor_public_id"] != actor_public_id:
        return None
    if payload["group_public_id"] != group_public_id:
        return None
    if payload["topic_public_id"] != topic_public_id:
        return None
    if payload["action"] != action:
        return None
    return payload
