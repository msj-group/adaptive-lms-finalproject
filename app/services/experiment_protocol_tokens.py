"""Signed, expiring, exact-shape stale-state tokens for the Phase 6 / M02A
experiment protocol catalogue.

Four purposes, each minted under its **own** salt carrying the
``phase6-m02a`` milestone marker:

- ``experiment-draft-change`` -- the acting Researcher, the protocol's
  public id, the protocol's aggregate ``version``, the target set or task
  (or ``none``) and the intended action. Every header, set and task change
  moves that one version, so **any** successful change anywhere in the draft
  makes every other open draft form stale -- two forms opened on the same
  draft can never both write. The action is bound too, so a Move Up token
  cannot be replayed as Move Down or as an edit.
- ``experiment-protocol-activate`` -- the Researcher, the draft's public id
  and version, the digest of the exact content the review page showed, and
  the public id of the version that was current (or ``none``). Activation
  seals exactly what was reviewed and supersedes exactly the version that
  was shown as current.
- ``experiment-protocol-discard`` -- the Researcher, the draft's public id
  and version.
- ``experiment-protocol-derive`` -- the Researcher, the frozen source's
  public id and its content digest.

Every failure -- another salt, another purpose, a wrong shape, an
out-of-range version, an expired token -- is the same "stale" answer, so a
probe learns nothing from which way it failed. Creating a new draft needs no
token: a repeated submission is refused by the unique version identifier.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, a version counter, an action name and a digest. It never
carries a name, an email address, a numeric database id or protocol wording.
"""

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.models import PROTOCOL_DIGEST_LENGTH
from app.services.research_tokens import TOKEN_MAX_AGE_SECONDS

PURPOSE_DRAFT_CHANGE = "experiment-draft-change"
PURPOSE_ACTIVATE = "experiment-protocol-activate"
PURPOSE_DISCARD = "experiment-protocol-discard"
PURPOSE_DERIVE = "experiment-protocol-derive"

PURPOSES = (PURPOSE_DRAFT_CHANGE, PURPOSE_ACTIVATE, PURPOSE_DISCARD, PURPOSE_DERIVE)

_SALTS = {purpose: f"research.{purpose}.phase6-m02a.v1" for purpose in PURPOSES}

#: What "no target" and "no current version" look like inside a payload --
#: a literal, so an absent value and a missing key are never confused.
NONE = "none"

#: The draft changes a draft-change token may authorise.
ACTION_EDIT_PROTOCOL = "edit_protocol"
ACTION_ADD_SET = "add_set"
ACTION_EDIT_SET = "edit_set"
ACTION_MOVE_SET_UP = "move_set_up"
ACTION_MOVE_SET_DOWN = "move_set_down"
ACTION_ADD_TASK = "add_task"
ACTION_EDIT_TASK = "edit_task"
ACTION_MOVE_TASK_UP = "move_task_up"
ACTION_MOVE_TASK_DOWN = "move_task_down"
DRAFT_ACTIONS = (
    ACTION_EDIT_PROTOCOL, ACTION_ADD_SET, ACTION_EDIT_SET, ACTION_MOVE_SET_UP,
    ACTION_MOVE_SET_DOWN, ACTION_ADD_TASK, ACTION_EDIT_TASK, ACTION_MOVE_TASK_UP,
    ACTION_MOVE_TASK_DOWN,
)

#: The exact key set each payload must carry -- no more, no fewer.
_FIELDS = {
    PURPOSE_DRAFT_CHANGE: ("purpose", "actor_public_id", "protocol_public_id",
                           "protocol_version", "target_public_id", "action"),
    PURPOSE_ACTIVATE: ("purpose", "actor_public_id", "protocol_public_id",
                       "protocol_version", "preview_digest", "current_public_id"),
    PURPOSE_DISCARD: ("purpose", "actor_public_id", "protocol_public_id",
                      "protocol_version"),
    PURPOSE_DERIVE: ("purpose", "actor_public_id", "source_public_id", "source_digest"),
}

_VERSION_FIELDS = frozenset({"protocol_version"})
_DIGEST_FIELDS = frozenset({"preview_digest", "source_digest"})
_ACTION_FIELDS = frozenset({"action"})

_MAX_TOKEN_LENGTH = 4096
_MAX_TEXT_FIELD_LENGTH = 64
_MAX_VERSION = 2**31 - 1
_HEX = frozenset("0123456789abcdef")


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _valid(field, value):
    if field in _VERSION_FIELDS:
        return (isinstance(value, int) and not isinstance(value, bool)
                and 1 <= value <= _MAX_VERSION)
    if field in _DIGEST_FIELDS:
        return (isinstance(value, str) and len(value) == PROTOCOL_DIGEST_LENGTH
                and all(ch in _HEX for ch in value))
    if field in _ACTION_FIELDS:
        return value in DRAFT_ACTIONS
    return isinstance(value, str) and bool(value) and len(value) <= _MAX_TEXT_FIELD_LENGTH


def make_token(purpose, **payload):
    """Sign one exact-shape M02A token from freshly read persisted state."""
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(purpose).dumps(body)


def _load(token, purpose):
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
    if payload["purpose"] != purpose:
        return None
    if not all(_valid(field, payload[field]) for field in fields if field != "purpose"):
        return None
    return payload


def token_is_stale(token, purpose, **expected):
    """``True`` when `token` does not exactly describe `expected`.

    `expected` must name **every** bound field, read from rows the request
    has already read or locked -- a caller that forgets one raises here
    rather than silently comparing less than the token promises.
    """
    if set(expected) != set(_FIELDS[purpose]) - {"purpose"}:  # pragma: no cover
        raise ValueError(f"every {purpose} field must be compared")
    payload = _load(token, purpose)
    if payload is None:
        return True
    return any(payload[field] != value for field, value in expected.items())
