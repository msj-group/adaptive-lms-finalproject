"""Signed, expiring completion-form tokens for Student lesson progress
(Phase 4 / M13).

One purpose, ``lesson-progress``, under its own salt. A token binds the
**acting Student**, the **Group**, the **Unit**, the **Lesson**, the
intended **action** (``complete`` or ``undo``) and the progress **version**
the page was rendered against.

A token minted under any other salt in this project fails signature
verification here even though every token is signed with the same
``SECRET_KEY``; a token for another Student, Group, Unit, Lesson or action
fails the binding check; a payload that is not exactly this shape is
refused; and a token older than :data:`TOKEN_MAX_AGE_SECONDS` fails the
timestamp check. Every failure is the same ``None``: rejected, never
trusted, never repaired.

**The version is returned, not compared.** A token for an older version is
still a genuine token; whether completion has changed since is a question
about current state, which the transaction decides under the progress-row
lock (``app/services/lesson_progress_transactions.py``).

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, a purpose, an action and a version counter only -- no title,
no name and no internal database id.
"""

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

PURPOSE = "lesson-progress"

ACTION_COMPLETE = "complete"
ACTION_UNDO = "undo"
ACTIONS = (ACTION_COMPLETE, ACTION_UNDO)

#: The version suffix is part of the salt so a future payload change
#: invalidates every token in flight instead of reinterpreting one.
_SALT = "lesson-progress.completion.phase4-m13.v1"

#: The exact key set of a payload.
_FIELDS = frozenset(
    {"purpose", "actor_public_id", "group_public_id", "unit_public_id", "lesson_public_id",
     "action", "progress_version"}
)

#: How long a rendered Lesson page's completion form stays usable. An
#: expired form is refused with a request to reload; nothing is written.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

#: Anything longer is not a token this module minted.
_MAX_TOKEN_LENGTH = 1024

#: The largest version a token may carry -- the ``INTEGER`` column bound.
_MAX_VERSION = 2**31 - 1


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALT)


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def make_progress_token(actor_public_id, group_public_id, unit_public_id, lesson_public_id,
                        action, progress_version):
    """A completion-form token for `action` bound to `progress_version`."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown lesson progress action: {action!r}")
    return _serializer().dumps(
        {
            "purpose": PURPOSE,
            "actor_public_id": actor_public_id,
            "group_public_id": group_public_id,
            "unit_public_id": unit_public_id,
            "lesson_public_id": lesson_public_id,
            "action": action,
            "progress_version": progress_version,
        }
    )


def load_progress_token(token, actor_public_id, group_public_id, unit_public_id,
                        lesson_public_id, action):
    """The payload when `token` is a genuine, unexpired, exact-shape
    completion token for exactly this Student, Group, Unit, Lesson and
    `action` -- else ``None``. ``progress_version`` is returned for the
    caller to compare with current state."""
    if not token or not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    try:
        payload = _serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except BadData:
        return None
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        return None
    if any(not isinstance(payload[field], str) for field in _FIELDS - {"progress_version"}):
        return None
    if payload["purpose"] != PURPOSE:
        return None
    if not _valid_version(payload["progress_version"]):
        return None
    if payload["action"] not in ACTIONS or payload["action"] != action:
        return None
    if payload["actor_public_id"] != actor_public_id:
        return None
    if payload["group_public_id"] != group_public_id:
        return None
    if payload["unit_public_id"] != unit_public_id:
        return None
    if payload["lesson_public_id"] != lesson_public_id:
        return None
    return payload
