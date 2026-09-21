"""The append-only research consent history (Phase 6 / M01).

One row per meaningful consent transition: a Student accepted, declined, or
withdrew. **Nothing here is ever updated or deleted.** An acceptance is not
rewritten into a withdrawal -- withdrawal adds a row, and the acceptance
stays exactly as it was recorded, because the fact that consent was once
given is itself part of the record.

**What one row preserves**, and deliberately nothing more:

- ``participant_id`` -- whose decision it was, by pseudonymous link;
- ``consent_document_id`` -- which document;
- ``consent_version`` and ``consent_digest`` -- copied at the moment of the
  decision, so the exact text that was accepted is provable even though the
  document row can never change anyway. Two independent facts are cheaper
  than one disputed one;
- ``action`` -- the closed set in
  :class:`~app.models.enums.ResearchConsentAction`;
- ``actor_id`` -- who acted. In M01 this is always the linked Student: no
  Administrator or Researcher may consent for anybody, and the transaction
  proves ``actor_id == participant.student_id`` under its locks;
- ``occurred_at`` -- a server clock reading, never a browser's.

**What is deliberately absent.** No IP address, no user-agent string, no
browser or device fingerprint, no free-form comment, no extra personal
information, and no generic research-event payload. M01 collects no
**behavioural interaction** data at all; this table records consent
decisions -- research administration, not observation -- and nothing else,
and there is no column shaped to hold anything more.

**Guards.** The mapper events below refuse any update or delete of a row.
:func:`_refuse_bulk_rewrites_of_research_history` additionally refuses
``session.execute(update(...))`` and ``delete(...)`` against the three M01
tables, which mapper events never see. Together they mean research history
cannot be rewritten through the ORM even by mistake; the database keeps no
``ON DELETE`` action, so nothing cascades into it either.

Indexes -- one per real query path:

- ``ix_research_consent_events_participant_id_id`` (``participant_id``,
  ``id``) -- one participant's history, newest last, and the "latest event"
  read; also the ``participant_id`` foreign key;
- ``ix_research_consent_events_consent_document_id`` and
  ``ix_research_consent_events_actor_id`` -- the other two foreign keys.

**No MySQL execution plan has been measured for this table.**

The row carries no ``public_id``: a consent event is never addressed by a
URL, exactly like ``payment_audit_events``.
"""

from sqlalchemy import event
from sqlalchemy.orm import Session, validates

from app.extensions import db
from app.models.enums import ResearchConsentAction, ResearchParticipantStatus
from app.models.research_consent_document import (
    CONSENT_DIGEST_LENGTH,
    CONSENT_VERSION_MAX_LENGTH,
    ResearchHistoryError,
)

_ACTION_VALUES = tuple(action.value for action in ResearchConsentAction)
_ACTION_CHECK_SQL = "action IN (" + ", ".join(f"'{v}'" for v in _ACTION_VALUES) + ")"
_DIGEST_FORMAT_SQL = f"LENGTH(consent_digest) = {CONSENT_DIGEST_LENGTH}"
_VERSION_PRESENT_SQL = "LENGTH(consent_version) > 0"

#: The participant status each action leaves behind. The stored status and
#: the history can therefore be compared -- and are, by the M01 tests --
#: without the event table duplicating a status column of its own.
STATUS_AFTER_ACTION = {
    ResearchConsentAction.ACCEPTED.value: ResearchParticipantStatus.ACTIVE.value,
    ResearchConsentAction.DECLINED.value: ResearchParticipantStatus.DECLINED.value,
    ResearchConsentAction.WITHDRAWN.value: ResearchParticipantStatus.WITHDRAWN.value,
}


class ResearchConsentEvent(db.Model):
    __tablename__ = "research_consent_events"
    __table_args__ = (
        db.CheckConstraint(_ACTION_CHECK_SQL, name="ck_research_consent_events_action_valid"),
        db.CheckConstraint(
            _DIGEST_FORMAT_SQL, name="ck_research_consent_events_digest_format"
        ),
        db.CheckConstraint(
            _VERSION_PRESENT_SQL, name="ck_research_consent_events_version_present"
        ),
        db.Index(
            "ix_research_consent_events_participant_id_id", "participant_id", "id"
        ),
        db.Index(
            "ix_research_consent_events_consent_document_id", "consent_document_id"
        ),
        db.Index("ix_research_consent_events_actor_id", "actor_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    participant_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("research_participants.id"),
        nullable=False,
    )
    consent_document_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("research_consent_documents.id"),
        nullable=False,
    )
    action = db.Column(db.String(32), nullable=False)
    #: Always the linked Student in M01.
    actor_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    #: Copied from the document at the moment of the decision.
    consent_version = db.Column(db.String(CONSENT_VERSION_MAX_LENGTH), nullable=False)
    consent_digest = db.Column(db.String(CONSENT_DIGEST_LENGTH), nullable=False)
    #: A server clock reading, whole-second UTC, supplied by the transaction.
    occurred_at = db.Column(db.DateTime, nullable=False)

    @validates("action")
    def validate_action(self, _key, value):
        if value not in _ACTION_VALUES:
            raise ValueError(f"Invalid research consent action: {value}")
        return value

    @validates("consent_digest")
    def validate_consent_digest(self, _key, value):
        if (
            not isinstance(value, str)
            or len(value) != CONSENT_DIGEST_LENGTH
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise ValueError("A consent event digest is 64 lowercase hex characters")
        return value


@event.listens_for(ResearchConsentEvent, "before_update")
def _refuse_editing_a_consent_event(_mapper, _connection, _target):
    raise ResearchHistoryError("A research consent event is append-only")


@event.listens_for(ResearchConsentEvent, "before_delete")
def _refuse_deleting_a_consent_event(_mapper, _connection, _target):
    raise ResearchHistoryError("A research consent event is never deleted")


#: No research row is ever hard-deleted, in bulk or otherwise.
_NO_BULK_DELETE = frozenset(
    {"research_consent_events", "research_participants", "research_consent_documents"}
)
#: Consent history is never rewritten at all; participants and documents
#: change only through the guarded ORM path, which re-proves every rule.
_NO_BULK_UPDATE = frozenset(
    {"research_consent_events", "research_participants", "research_consent_documents"}
)


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_rewrites_of_research_history(orm_execute_state):
    """Mapper events do not see ``session.execute(update(...))`` or
    ``delete(...)``; this refuses those statements for every M01 table, so a
    bulk statement can never quietly erase or rewrite consent history."""
    if orm_execute_state.is_delete:
        protected = _NO_BULK_DELETE
    elif orm_execute_state.is_update:
        protected = _NO_BULK_UPDATE
    else:
        return
    table = getattr(orm_execute_state.statement, "table", None)
    if getattr(table, "name", None) in protected:
        raise ResearchHistoryError(f"{table.name} rows cannot be rewritten in bulk")
