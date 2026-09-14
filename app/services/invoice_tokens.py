"""Signed, expiring, exact-shape stale-state tokens for Administrator
invoices (Phase 5 / M04).

Six purposes, each minted under its **own** salt carrying the ``phase5-m04``
milestone marker:

- ``invoice-create`` -- the acting Administrator and the exact Group,
  Enrollment, Student Fee Assignment and Fee Plan public ids, with the
  assignment's ``version``. Creating or cancelling an invoice moves no version
  that token binds, so the caller also passes ``changed_at`` -- the latest
  ``updated_at`` among the assignment's locked invoices -- and a token issued
  at an earlier whole second is stale. A replayed confirmation form therefore
  cannot open a new draft after somebody cancelled the one it was rendered
  beside. A change within the same second is not detected; the locks and the
  one-open-invoice rule still hold then.
- ``invoice-item-create`` -- the actor, the invoice's public id and its
  ``version``.
- ``invoice-item-edit`` / ``invoice-item-remove`` -- all of that plus the
  affected line's public id.
- ``invoice-issue`` -- the actor, the invoice's public id and ``version``,
  and the complete current active-line state: each active line's public id
  and ``version``, in ascending internal id order.
- ``invoice-cancel`` -- the actor, the invoice's public id and ``version``.

Every line change, the issue and the cancellation move the invoice version,
so a token for any purpose goes stale as soon as anything in the invoice
changed after its page was rendered, and a replayed token is stale because
its own write moved the version.

A token minted under any other salt in this project fails signature
verification here; a token for one purpose fails on the other five; a payload
that is not exactly the purpose's shape, carries a non-``int`` or
out-of-range version (``bool`` excluded), or an ill-formed line state is
refused; a token older than :data:`TOKEN_MAX_AGE_SECONDS` is expired. Every
failure is the same ``None`` and is treated exactly like an outdated token.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, versions and a purpose only -- no internal id, Student name,
label, amount, total, snapshot, reason or authorization fact.
"""

from datetime import timezone

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.models import MAX_ACTIVE_INVOICE_ITEMS

PURPOSE_CREATE = "invoice-create"
PURPOSE_ITEM_CREATE = "invoice-item-create"
PURPOSE_ITEM_EDIT = "invoice-item-edit"
PURPOSE_ITEM_REMOVE = "invoice-item-remove"
PURPOSE_ISSUE = "invoice-issue"
PURPOSE_CANCEL = "invoice-cancel"

PURPOSES = (
    PURPOSE_CREATE,
    PURPOSE_ITEM_CREATE,
    PURPOSE_ITEM_EDIT,
    PURPOSE_ITEM_REMOVE,
    PURPOSE_ISSUE,
    PURPOSE_CANCEL,
)

_SALTS = {purpose: f"admin.{purpose}.phase5-m04.v1" for purpose in PURPOSES}

_INVOICE_FIELDS = ("purpose", "actor_public_id", "invoice_public_id", "invoice_version")

#: The exact key set each payload must carry -- no more, no fewer.
_FIELDS = {
    PURPOSE_CREATE: (
        "purpose",
        "actor_public_id",
        "group_public_id",
        "enrollment_public_id",
        "assignment_public_id",
        "plan_public_id",
        "assignment_version",
    ),
    PURPOSE_ITEM_CREATE: _INVOICE_FIELDS,
    PURPOSE_ITEM_EDIT: _INVOICE_FIELDS + ("item_public_id",),
    PURPOSE_ITEM_REMOVE: _INVOICE_FIELDS + ("item_public_id",),
    PURPOSE_ISSUE: _INVOICE_FIELDS + ("active_items",),
    PURPOSE_CANCEL: _INVOICE_FIELDS,
}

_VERSION_FIELDS = frozenset({"assignment_version", "invoice_version"})
_LINE_STATE_FIELD = "active_items"

#: How long a rendered form stays usable.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

#: An issue token carries up to twenty line states, so the bound is wider
#: than one public id needs.
_MAX_TOKEN_LENGTH = 4096
_MAX_TEXT_FIELD_LENGTH = 64
_MAX_VERSION = 2**31 - 1


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def _valid_text(value):
    return isinstance(value, str) and bool(value) and len(value) <= _MAX_TEXT_FIELD_LENGTH


def _valid_line_state(value):
    """A list of at most :data:`MAX_ACTIVE_INVOICE_ITEMS` distinct
    ``[public_id, version]`` pairs."""
    if not isinstance(value, list) or len(value) > MAX_ACTIVE_INVOICE_ITEMS:
        return False
    for pair in value:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not _valid_text(pair[0])
            or not _valid_version(pair[1])
        ):
            return False
    return len({pair[0] for pair in value}) == len(value)


def line_state(items):
    """The ``active_items`` value for `items` -- active lines in ascending
    internal id order -- exactly as a decoded token carries it."""
    return [[item.public_id, item.version] for item in items]


def make_token(purpose, **payload):
    """Sign one exact-shape M04 token from freshly read persisted state."""
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
            valid = _valid_version(value)
        elif field == _LINE_STATE_FIELD:
            valid = _valid_line_state(value)
        else:
            valid = _valid_text(value)
        if not valid:
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
