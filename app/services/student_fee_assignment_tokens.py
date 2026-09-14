"""Signed, expiring, exact-shape stale-state tokens for Administrator fee
assignments by Enrollment (Phase 5 / M03).

Two purposes, each minted under its **own** salt carrying the ``phase5-m03``
milestone marker:

- ``fee-assignment-create`` -- the acting Administrator, the Enrollment's
  public id, the Fee Plan's public id and the Fee Plan's ``version``. Every
  plan lifecycle transition moves that version, so a token minted while a
  plan was active is stale once the plan has been archived -- even if it is
  active again.
- ``fee-assignment-cancel`` -- the acting Administrator, the Enrollment's
  public id, the assignment's public id and the assignment's ``version``.
  Cancellation moves that version.

**A create token may not predate the Enrollment's fee history.** Assigning a
plan changes no version the create token binds, so on its own a replayed
confirmation form could re-create an assignment somebody had just cancelled.
The caller therefore passes ``changed_at`` -- the latest ``updated_at`` among
the Enrollment's locked assignment rows -- and a token issued at an earlier
whole second is stale. The comparison uses the issue time inside the signed
token itself, so the payload shape stays exactly as above. Both moments are
whole seconds, and a change in the very second the page was rendered is not
detected: the locks and the one-assigned-plan rule still hold.

A token minted under any other salt in this project fails signature
verification here even though every token is signed with the same
``SECRET_KEY``; a token for one purpose fails on the other; a payload that is
not exactly the purpose's shape, or carries a non-``int`` / out-of-range
version (``bool`` excluded explicitly), is refused; and a token older than
:data:`TOKEN_MAX_AGE_SECONDS` is expired. Every failure is the same ``None``
and is treated exactly like an outdated token.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, versions and a purpose only -- no internal id, Student name,
plan name, item label, amount, total or authorization fact.
"""

from datetime import timezone

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

PURPOSE_ASSIGN = "fee-assignment-create"
PURPOSE_CANCEL = "fee-assignment-cancel"

PURPOSES = (PURPOSE_ASSIGN, PURPOSE_CANCEL)

_SALTS = {purpose: f"admin.{purpose}.phase5-m03.v1" for purpose in PURPOSES}

#: The exact key set each payload must carry -- no more, no fewer.
_FIELDS = {
    PURPOSE_ASSIGN: (
        "purpose",
        "actor_public_id",
        "enrollment_public_id",
        "plan_public_id",
        "plan_version",
    ),
    PURPOSE_CANCEL: (
        "purpose",
        "actor_public_id",
        "enrollment_public_id",
        "assignment_public_id",
        "assignment_version",
    ),
}

_VERSION_FIELDS = frozenset({"plan_version", "assignment_version"})

#: How long a rendered form stays usable.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

_MAX_TOKEN_LENGTH = 2048
_MAX_TEXT_FIELD_LENGTH = 64
_MAX_VERSION = 2**31 - 1


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def make_token(purpose, **payload):
    """Sign one exact-shape M03 token from freshly read persisted state."""
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(purpose).dumps(body)


def load_token_with_issue_time(token, purpose):
    """``(payload, issued_at)`` -- ``issued_at`` a naive-UTC whole-second
    ``datetime`` -- or ``None`` for a missing, oversized, malformed, invalidly
    signed, expired, wrong-salt, wrong-purpose or wrong-shaped token."""
    if purpose not in _SALTS:
        return None
    if not token or not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    try:
        payload, issued_at = _serializer(purpose).loads(
            token, max_age=TOKEN_MAX_AGE_SECONDS, return_timestamp=True
        )
    except BadData:
        return None
    fields = _FIELDS[purpose]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return None
    if payload["purpose"] != purpose:
        return None
    for field in fields:
        value = payload[field]
        if field in _VERSION_FIELDS:
            if not _valid_version(value):
                return None
        elif not isinstance(value, str) or not value or len(value) > _MAX_TEXT_FIELD_LENGTH:
            return None
    return payload, issued_at.astimezone(timezone.utc).replace(tzinfo=None)


def load_token(token, purpose):
    """The payload, or ``None`` -- see :func:`load_token_with_issue_time`."""
    loaded = load_token_with_issue_time(token, purpose)
    return None if loaded is None else loaded[0]


def token_is_stale(token, purpose, changed_at=None, **expected):
    """``True`` when `token` does not exactly describe `expected`, or was
    issued at a whole second earlier than `changed_at`.

    `expected` must name **every** bound field, and its values must come from
    the rows this request locked. `changed_at` is a naive-UTC moment or
    ``None``.
    """
    if set(expected) != set(_FIELDS[purpose]) - {"purpose"}:  # pragma: no cover
        raise ValueError(f"every {purpose} field must be compared")
    loaded = load_token_with_issue_time(token, purpose)
    if loaded is None:
        return True
    payload, issued_at = loaded
    if any(payload[field] != value for field, value in expected.items()):
        return True
    return changed_at is not None and changed_at > issued_at
