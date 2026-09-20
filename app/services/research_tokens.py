"""Signed, expiring, exact-shape stale-state tokens for the Phase 6 / M01
research workspace.

Three purposes, each minted under its **own** salt carrying the
``phase6-m01`` milestone marker:

- ``research-participant-create`` -- the acting Administrator and the exact
  Student the invitation form was rendered for, together with that Student's
  live state: role, account status, and whether a participant already exists
  for them. A Student who was promoted, suspended or already invited between
  the page load and the submit makes the token stale, so the form cannot act
  on a screen that has stopped being true.
- ``research-consent-activate`` -- the acting Administrator, the draft's
  public id and digest, and the currently current document's public id and
  digest (or ``none``). Activating supersedes whatever is current, so the
  form is bound to *which* document that was.
- ``research-consent-decision`` -- the acting **Student**, their
  participant's public id, status and version, the consent document's public
  id, version identifier and digest, and the intended action. This is the
  token that makes hidden or implicit consent impossible: nothing about the
  decision comes from the browser except a signature over state the server
  itself rendered, and every one of those facts is re-proved against locked
  rows before anything is written.

Every failure -- another salt, another purpose, a wrong shape, an
out-of-range version, an expired token -- is the same "stale" answer, so a
probe learns nothing from which way it failed.

**A token is authenticated, not encrypted.** Its payload carries public
identifiers, statuses, a version counter, a consent version label, a digest
and a purpose. It never carries a name, an email address, a numeric database
id or any consent wording.
"""

from datetime import timezone

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.models import (
    CONSENT_DIGEST_LENGTH,
    CONSENT_VERSION_MAX_LENGTH,
    ResearchConsentDocumentStatus,
    ResearchParticipantStatus,
)

PURPOSE_PARTICIPANT_CREATE = "research-participant-create"
PURPOSE_CONSENT_ACTIVATE = "research-consent-activate"
PURPOSE_CONSENT_DECISION = "research-consent-decision"

PURPOSES = (
    PURPOSE_PARTICIPANT_CREATE,
    PURPOSE_CONSENT_ACTIVATE,
    PURPOSE_CONSENT_DECISION,
)

_SALTS = {purpose: f"research.{purpose}.phase6-m01.v1" for purpose in PURPOSES}

#: The intended consent action a decision token authorises. Distinct from
#: :class:`~app.models.enums.ResearchConsentAction`, whose members name what
#: was *recorded*: a token names what a form was rendered to do.
ACTION_ACCEPT = "accept"
ACTION_DECLINE = "decline"
ACTION_WITHDRAW = "withdraw"
DECISION_ACTIONS = (ACTION_ACCEPT, ACTION_DECLINE, ACTION_WITHDRAW)

#: What "no document is current" looks like inside an activation token --
#: a literal, so an absent value and a missing key are never confused.
NO_CURRENT_DOCUMENT = "none"

#: The exact key set each payload must carry -- no more, no fewer.
_FIELDS = {
    PURPOSE_PARTICIPANT_CREATE: (
        "purpose",
        "actor_public_id",
        "student_public_id",
        "student_state",
    ),
    PURPOSE_CONSENT_ACTIVATE: (
        "purpose",
        "actor_public_id",
        "document_public_id",
        "document_digest",
        "current_public_id",
        "current_digest",
    ),
    PURPOSE_CONSENT_DECISION: (
        "purpose",
        "actor_public_id",
        "participant_public_id",
        "participant_status",
        "participant_version",
        "document_public_id",
        "consent_version",
        "consent_digest",
        "action",
    ),
}

_DIGEST_FIELDS = frozenset({"document_digest", "current_digest", "consent_digest"})
_VERSION_FIELDS = frozenset({"participant_version"})
_STATUS_FIELDS = frozenset({"participant_status"})
_ACTION_FIELDS = frozenset({"action"})

_PARTICIPANT_STATUSES = frozenset(s.value for s in ResearchParticipantStatus)
_DOCUMENT_STATUSES = frozenset(s.value for s in ResearchConsentDocumentStatus)

#: How long a rendered form stays usable. A consent page an hour old is
#: still a page somebody read; a day-old one is refused and re-read.
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60

_MAX_TOKEN_LENGTH = 4096
_MAX_TEXT_FIELD_LENGTH = max(64, CONSENT_VERSION_MAX_LENGTH)
_MAX_VERSION = 2**31 - 1
_HEX = frozenset("0123456789abcdef")

__all__ = [
    "ACTION_ACCEPT",
    "ACTION_DECLINE",
    "ACTION_WITHDRAW",
    "DECISION_ACTIONS",
    "NO_CURRENT_DOCUMENT",
    "PURPOSES",
    "PURPOSE_CONSENT_ACTIVATE",
    "PURPOSE_CONSENT_DECISION",
    "PURPOSE_PARTICIPANT_CREATE",
    "TOKEN_MAX_AGE_SECONDS",
    "make_token",
    "student_state",
    "token_is_stale",
]


def student_state(role, status, has_participant):
    """The exact Student state a participant-create form was rendered for.

    One opaque string rather than three fields, so the token's shape cannot
    be partially compared: a caller either reproduces all three facts from
    freshly locked rows or does not match.
    """
    return f"{role}.{status}.{'linked' if has_participant else 'unlinked'}"


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def _valid_version(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_VERSION


def _valid_text(value):
    return isinstance(value, str) and bool(value) and len(value) <= _MAX_TEXT_FIELD_LENGTH


def _valid_digest(value):
    if value == NO_CURRENT_DOCUMENT:
        return True
    return (
        isinstance(value, str)
        and len(value) == CONSENT_DIGEST_LENGTH
        and all(ch in _HEX for ch in value)
    )


def make_token(purpose, **payload):
    """Sign one exact-shape M01 token from freshly read persisted state."""
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
        elif field in _DIGEST_FIELDS:
            valid = _valid_digest(value)
        elif field in _STATUS_FIELDS:
            valid = value in _PARTICIPANT_STATUSES
        elif field in _ACTION_FIELDS:
            valid = value in DECISION_ACTIONS
        else:
            valid = _valid_text(value)
        if not valid:
            return None
    return payload, issued_at.astimezone(timezone.utc).replace(tzinfo=None)


def token_is_stale(token, purpose, changed_at=None, **expected):
    """``True`` when `token` does not exactly describe `expected`, or was
    issued at a whole second earlier than `changed_at` (a naive-UTC moment
    or ``None``).

    `expected` must name **every** bound field, read from rows the request
    has already locked -- a caller that forgets one raises here rather than
    silently comparing less than the token promises.
    """
    if set(expected) != set(_FIELDS[purpose]) - {"purpose"}:  # pragma: no cover
        raise ValueError(f"every {purpose} field must be compared")
    loaded = _load(token, purpose)
    if loaded is None:
        return True
    payload, issued_at = loaded
    if any(payload[field] != value for field, value in expected.items()):
        return True
    return changed_at is not None and changed_at > issued_at


def decision_action_for(status):
    """The action a participant in `status` may be offered, or ``None``.

    The one place the "what can this person do now" rule lives, so the page,
    the token and the transaction cannot disagree: an ``invited``
    participant may accept or decline, an ``active`` one may withdraw, and a
    ``declined`` or ``withdrawn`` one may do nothing at all in M01.
    """
    if status == ResearchParticipantStatus.INVITED.value:
        return (ACTION_ACCEPT, ACTION_DECLINE)
    if status == ResearchParticipantStatus.ACTIVE.value:
        return (ACTION_WITHDRAW,)
    return ()


def document_status_is_presentable(status):
    """Whether a document in `status` may be shown to a Student as consent
    text. Only the active one may: a draft is unfinished and a superseded
    one has been replaced."""
    return status == ResearchConsentDocumentStatus.ACTIVE.value
