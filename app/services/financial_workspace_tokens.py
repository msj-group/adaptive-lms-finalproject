"""Signed, expiring, exact-shape stale-state tokens for the Phase 5 / M10
financial workspaces.

Four purposes, each minted under its **own** salt carrying the ``phase5-m10``
milestone marker:

- ``workspace-invoice-create`` -- the acting Administrator and the exact
  Student, Enrollment and Fee Plan public ids, with the plan's ``version``.
  Creating or cancelling a fee assignment or an invoice moves no version this
  token binds, so the caller also passes ``changed_at`` -- the latest
  ``updated_at`` among the Enrollment's locked assignments and the chosen
  assignment's locked invoices -- and a token issued at an earlier whole
  second is stale.
- ``workspace-invoice-delete`` -- the actor, the invoice's public id and
  ``version``, and the invoice's complete live payment state.
- ``workspace-payment-edit`` / ``workspace-payment-delete`` -- the actor, the
  invoice's public id and ``version``, the payment's public id and
  ``version``, and the invoice's live payment state.

**A payment state** is M05's: ``[public_id, status, version]`` for every live
transaction of the invoice, in ascending internal id order. Every recording,
decision, reversal, edit and deletion adds, moves or removes a row, so a token
goes stale as soon as anything in the invoice's payments changed after its
page was rendered, and a replayed token is stale because its own write changed
the state.

Every failure -- another salt, another purpose, a wrong shape, an
out-of-range version, an expired token -- is the same "stale" answer. **A
token is authenticated, not encrypted**: public identifiers, statuses,
versions and a purpose only.
"""

from datetime import timezone

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.models import MAX_INVOICE_PAYMENT_ROWS, PaymentTransactionStatus
from app.services.payment_tokens import payment_state

PURPOSE_INVOICE_CREATE = "workspace-invoice-create"
PURPOSE_INVOICE_DELETE = "workspace-invoice-delete"
PURPOSE_PAYMENT_EDIT = "workspace-payment-edit"
PURPOSE_PAYMENT_DELETE = "workspace-payment-delete"

PURPOSES = (
    PURPOSE_INVOICE_CREATE,
    PURPOSE_INVOICE_DELETE,
    PURPOSE_PAYMENT_EDIT,
    PURPOSE_PAYMENT_DELETE,
)

_SALTS = {purpose: f"admin.{purpose}.phase5-m10.v1" for purpose in PURPOSES}

_PAYMENT_FIELDS = (
    "purpose",
    "actor_public_id",
    "invoice_public_id",
    "invoice_version",
    "payment_public_id",
    "payment_version",
    "payment_state",
)

#: The exact key set each payload must carry -- no more, no fewer.
_FIELDS = {
    PURPOSE_INVOICE_CREATE: (
        "purpose",
        "actor_public_id",
        "student_public_id",
        "enrollment_public_id",
        "plan_public_id",
        "plan_version",
    ),
    PURPOSE_INVOICE_DELETE: (
        "purpose",
        "actor_public_id",
        "invoice_public_id",
        "invoice_version",
        "payment_state",
    ),
    PURPOSE_PAYMENT_EDIT: _PAYMENT_FIELDS,
    PURPOSE_PAYMENT_DELETE: _PAYMENT_FIELDS,
}

_VERSION_FIELDS = frozenset({"plan_version", "invoice_version", "payment_version"})
_STATE_FIELD = "payment_state"
_STATUSES = frozenset(status.value for status in PaymentTransactionStatus)

#: How long a rendered form stays usable.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

_MAX_TOKEN_LENGTH = 8192
_MAX_TEXT_FIELD_LENGTH = 64
_MAX_VERSION = 2**31 - 1

__all__ = [
    "PURPOSES",
    "PURPOSE_INVOICE_CREATE",
    "PURPOSE_INVOICE_DELETE",
    "PURPOSE_PAYMENT_DELETE",
    "PURPOSE_PAYMENT_EDIT",
    "make_token",
    "payment_state",
    "token_is_stale",
]


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def _valid_text(value):
    return isinstance(value, str) and bool(value) and len(value) <= _MAX_TEXT_FIELD_LENGTH


def _valid_state(value):
    if not isinstance(value, list) or len(value) > MAX_INVOICE_PAYMENT_ROWS:
        return False
    for entry in value:
        if (
            not isinstance(entry, list)
            or len(entry) != 3
            or not _valid_text(entry[0])
            or entry[1] not in _STATUSES
            or not _valid_version(entry[2])
        ):
            return False
    return len({entry[0] for entry in value}) == len(value)


def make_token(purpose, **payload):
    """Sign one exact-shape M10 token from freshly read persisted state."""
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
        elif field == _STATE_FIELD:
            valid = _valid_state(value)
        else:
            valid = _valid_text(value)
        if not valid:
            return None
    return payload, issued_at.astimezone(timezone.utc).replace(tzinfo=None)


def token_is_stale(token, purpose, changed_at=None, **expected):
    """``True`` when `token` does not exactly describe `expected`, or was
    issued at a whole second earlier than `changed_at` (a naive-UTC moment or
    ``None``). `expected` must name every bound field, from rows the request
    locked or read after its locks."""
    if set(expected) != set(_FIELDS[purpose]) - {"purpose"}:  # pragma: no cover
        raise ValueError(f"every {purpose} field must be compared")
    loaded = _load(token, purpose)
    if loaded is None:
        return True
    payload, issued_at = loaded
    if any(payload[field] != value for field, value in expected.items()):
        return True
    return changed_at is not None and changed_at > issued_at
