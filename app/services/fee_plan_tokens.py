"""Signed, expiring, exact-shape stale-state tokens for the Administrator
fee plan catalogue (Phase 5 / M02).

Eight purposes, one per mutating form, each minted under its **own** salt
carrying the ``phase5-m02`` milestone marker:

- ``fee-plan-create`` -- binds the acting Administrator only. There is no
  row yet, so there is no version or lifecycle to bind; a replayed create
  form is stopped by ``uq_fee_plans_name`` rather than by the token.
- ``fee-plan-edit``, ``fee-plan-item-create``, ``fee-plan-activate``,
  ``fee-plan-archive``, ``fee-plan-reactivate`` -- the actor, the plan's
  public id, its ``version`` and its ``status``.
- ``fee-plan-item-edit``, ``fee-plan-item-remove`` -- all of that, plus the
  item's public id, ``version`` and ``status``.

Because every item change also increments the plan's version, an
activation token is stale the moment anything in the aggregate changed
after the page was rendered: an Administrator can only ever activate the
exact definition they were shown. Every replayed token is stale for the
same reason -- the write it authorized moved the version.

A token minted under any other salt in this project fails signature
verification here even though every token is signed with the same
``SECRET_KEY``; a token for one of these purposes fails on the other seven;
a payload that is not exactly the purpose's shape, or carries a
non-``int`` / out-of-range version (``bool`` excluded explicitly), is
refused; and a token older than :data:`TOKEN_MAX_AGE_SECONDS` is expired.
Every failure is the same ``None`` and is treated exactly like an outdated
token: rejected, never trusted and never repaired.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, versions, statuses and a purpose only -- no plan name, no
description, no label, no amount and no internal database id.

**Locks and tokens solve different problems, and M02 keeps both.** The lock
chain (``app/services/fee_plan_transactions.py``) decides against the rows
as they are *now*; a token decides against the state the form was
*opened* on. Every expected value is compared against the **locked** rows,
never against a pre-lock preview.
"""

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

PURPOSE_CREATE = "fee-plan-create"
PURPOSE_EDIT = "fee-plan-edit"
PURPOSE_ITEM_CREATE = "fee-plan-item-create"
PURPOSE_ITEM_EDIT = "fee-plan-item-edit"
PURPOSE_ITEM_REMOVE = "fee-plan-item-remove"
PURPOSE_ACTIVATE = "fee-plan-activate"
PURPOSE_ARCHIVE = "fee-plan-archive"
PURPOSE_REACTIVATE = "fee-plan-reactivate"

PURPOSES = (
    PURPOSE_CREATE,
    PURPOSE_EDIT,
    PURPOSE_ITEM_CREATE,
    PURPOSE_ITEM_EDIT,
    PURPOSE_ITEM_REMOVE,
    PURPOSE_ACTIVATE,
    PURPOSE_ARCHIVE,
    PURPOSE_REACTIVATE,
)

#: Only the Administrator surface mints one. The surface and the milestone
#: are part of every salt, so a future second surface or a later payload
#: change invalidates every token in flight instead of reinterpreting one.
_SALTS = {purpose: f"admin.{purpose}.phase5-m02.v1" for purpose in PURPOSES}

_PLAN_FIELDS = ("purpose", "actor_public_id", "plan_public_id", "plan_version", "plan_status")
_ITEM_FIELDS = _PLAN_FIELDS + ("item_public_id", "item_version", "item_status")

#: The exact key set each payload must carry -- no more, no fewer.
_FIELDS = {
    PURPOSE_CREATE: ("purpose", "actor_public_id"),
    PURPOSE_EDIT: _PLAN_FIELDS,
    PURPOSE_ITEM_CREATE: _PLAN_FIELDS,
    PURPOSE_ITEM_EDIT: _ITEM_FIELDS,
    PURPOSE_ITEM_REMOVE: _ITEM_FIELDS,
    PURPOSE_ACTIVATE: _PLAN_FIELDS,
    PURPOSE_ARCHIVE: _PLAN_FIELDS,
    PURPOSE_REACTIVATE: _PLAN_FIELDS,
}

_VERSION_FIELDS = frozenset({"plan_version", "item_version"})

#: How long a rendered form stays usable. An expired form is refused with a
#: request to reload; nothing is written.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

#: Anything longer is not a token this module minted.
_MAX_TOKEN_LENGTH = 2048

#: A public id is 36 characters and a status or purpose far shorter.
_MAX_TEXT_FIELD_LENGTH = 64

#: The ``INTEGER`` column bound.
_MAX_VERSION = 2**31 - 1


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def make_token(purpose, **payload):
    """Sign one exact-shape M02 token.

    Only ever called with freshly read persisted state, never with attempted
    form values: a fresh token may only pair with fresh state.
    """
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(purpose).dumps(body)


def load_token(token, purpose):
    """The payload, or ``None`` for a missing, oversized, malformed,
    invalidly signed, expired, wrong-salt, wrong-purpose or wrong-shaped
    token."""
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
    for field in fields:
        value = payload[field]
        if field in _VERSION_FIELDS:
            if not _valid_version(value):
                return None
        elif not isinstance(value, str) or not value or len(value) > _MAX_TEXT_FIELD_LENGTH:
            return None
    return payload


def token_is_stale(token, purpose, **expected):
    """``True`` when `token` does not exactly describe `expected`.

    `expected` must name **every** bound field -- a caller cannot forget to
    compare one -- and its values must come from the rows this request
    locked.
    """
    if set(expected) != set(_FIELDS[purpose]) - {"purpose"}:  # pragma: no cover
        raise ValueError(f"every {purpose} field must be compared")
    payload = load_token(token, purpose)
    if payload is None:
        return True
    return any(payload[field] != value for field, value in expected.items())
