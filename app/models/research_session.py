"""One natural-use research session (Phase 6 replacement).

A session is a period of ordinary LMS use by one subject in one browser login
session, under one configuration version. It needs no experiment, task set or
task: it starts with the first accepted observation and ends on logout, after
the configuration's inactivity timeout, when the active configuration
changes, or when the subject stops being eligible. Tabs of one browser share
one session (the signed Flask session cookie names it); a second browser is a
second session.

**Clocks.** Every moment here is integer epoch milliseconds on the **server**
timescale (``research_common.now_ms``). Client event times are corrected into
that timescale before they touch the observation-run columns.

**Observation runs.** ``observed_since_ms`` is the start of the current run of
continuous, visible observation and ``last_observed_at_ms`` its latest
moment; a hidden tab or a gap longer than the heartbeat tolerance ends a run.
Feedback prompts use them so that unobserved time is never counted as
observed.

**Quality counters** record what the ingestion accepted and rejected, and the
sampling counters record how often the session was (in)eligible for a
prompt, so selection and delivery limitations are measurable rather than
invisible.

``provenance`` is set by the server when the session starts and never
changes: ``demo`` for a demonstration subject, otherwise the deployment's
``RESEARCH_DATA_PROVENANCE``.
"""
from app.models.code_types import CODE_COLLATION

import uuid

from sqlalchemy import event, inspect

from app.extensions import db
from app.models.enums import ResearchProvenance, ResearchSessionEndReason
from app.models.research_common import ID_TYPE, ResearchDataError, changed_columns, in_list_sql

SESSION_PROVENANCES = tuple(p.value for p in ResearchProvenance)
END_REASONS = tuple(r.value for r in ResearchSessionEndReason)

COUNTER_COLUMNS = (
    "batches_received",
    "events_accepted",
    "events_duplicate",
    "events_invalid",
    "events_late",
    "events_dropped_client",
    "sampling_eligible_checks",
    "sampling_ineligible_checks",
)

SESSION_IDENTITY_COLUMNS = frozenset(
    {"public_id", "subject_id", "configuration_id", "provenance", "started_at_ms"}
)


class ResearchSession(db.Model):
    __tablename__ = "research_sessions"
    __table_args__ = (
        db.CheckConstraint(
            in_list_sql("provenance", SESSION_PROVENANCES),
            name="ck_research_sessions_provenance_valid",
        ),
        db.CheckConstraint(
            "end_reason IS NULL OR " + in_list_sql("end_reason", END_REASONS),
            name="ck_research_sessions_end_reason_valid",
        ),
        db.CheckConstraint(
            "(ended_at_ms IS NULL AND end_reason IS NULL)"
            " OR (ended_at_ms IS NOT NULL AND end_reason IS NOT NULL"
            " AND ended_at_ms >= started_at_ms)",
            name="ck_research_sessions_end_pair",
        ),
        db.CheckConstraint(
            "last_seen_at_ms >= started_at_ms", name="ck_research_sessions_times_ordered"
        ),
        db.CheckConstraint(
            "(observed_since_ms IS NULL AND last_observed_at_ms IS NULL)"
            " OR (observed_since_ms IS NOT NULL AND last_observed_at_ms IS NOT NULL"
            " AND last_observed_at_ms >= observed_since_ms)",
            name="ck_research_sessions_observation_run",
        ),
        db.CheckConstraint(
            " AND ".join(f"{column} >= 0" for column in COUNTER_COLUMNS),
            name="ck_research_sessions_counters_non_negative",
        ),
        db.Index("ix_research_sessions_subject_id_id", "subject_id", "id"),
        db.Index("ix_research_sessions_configuration_id_id", "configuration_id", "id"),
        db.Index("ix_research_sessions_last_seen", "last_seen_at_ms"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    subject_id = db.Column(ID_TYPE, db.ForeignKey("research_subjects.id"), nullable=False)
    configuration_id = db.Column(
        ID_TYPE, db.ForeignKey("research_configurations.id"), nullable=False
    )
    provenance = db.Column(db.String(16, collation=CODE_COLLATION), nullable=False)
    started_at_ms = db.Column(db.BigInteger, nullable=False)
    last_seen_at_ms = db.Column(db.BigInteger, nullable=False)
    ended_at_ms = db.Column(db.BigInteger, nullable=True)
    end_reason = db.Column(db.String(32, collation=CODE_COLLATION), nullable=True)
    observed_since_ms = db.Column(db.BigInteger, nullable=True)
    last_observed_at_ms = db.Column(db.BigInteger, nullable=True)
    #: Armed by a server outcome that ends a natural activity; consumed by the
    #: next sampling check whatever it decides.
    activity_end_pending_at_ms = db.Column(db.BigInteger, nullable=True)
    #: Set when the Student chose Skip: no further prompt in this session.
    prompt_dismissed_at_ms = db.Column(db.BigInteger, nullable=True)

    batches_received = db.Column(db.Integer, nullable=False, default=0)
    events_accepted = db.Column(db.Integer, nullable=False, default=0)
    events_duplicate = db.Column(db.Integer, nullable=False, default=0)
    events_invalid = db.Column(db.Integer, nullable=False, default=0)
    events_late = db.Column(db.Integer, nullable=False, default=0)
    events_dropped_client = db.Column(db.Integer, nullable=False, default=0)
    sampling_eligible_checks = db.Column(db.Integer, nullable=False, default=0)
    sampling_ineligible_checks = db.Column(db.Integer, nullable=False, default=0)

    @property
    def is_open(self):
        return self.ended_at_ms is None


@event.listens_for(ResearchSession, "before_update")
def _session_identity_is_immutable(_mapper, _connection, target):
    changed = changed_columns(inspect(target), SESSION_IDENTITY_COLUMNS)
    if changed:
        raise ResearchDataError("A research session's " + ", ".join(changed) + " never changes.")
