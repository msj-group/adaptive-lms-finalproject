"""Read queries for the Researcher workspace (Phase 6 replacement).

Read-only and Flask-independent.

**The identity boundary is in SQL.** Every function here selects column
tuples from research tables only. None of them joins ``users`` or
``research_subject_links``, so no Student name, email address, account id
or subject-to-account mapping is ever loaded -- not even a Researcher's name
(the audit log says "you" or "another researcher"). A subject appears only as
its pseudonymous ``subject_code``.

**Real counts, explicit empty states.** Every number is a count or a sum of
stored rows; nothing is estimated, predicted or charted as measured emotion.
Derived prompt states (an offer never shown, a display never answered) are
computed from the stored moments and the configuration's lifetimes at the
moment of the query.

**Bounded.** Lists are paginated (:data:`PAGE_SIZE`); a session timeline is
capped at :data:`TIMELINE_CAP` events; aggregate queries are grouped counts.
"""

from app.services.research_storage_contract import COLLECTION_SCHEMA

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import exists, func, or_, select

from app.extensions import db
from app.models import (
    ResearchAuditEvent,
    ResearchAuditAction,
    ResearchCollectionStatus,
    ResearchConfiguration,
    ResearchEvent,
    ResearchExport,
    ResearchExportArchive,
    ResearchFeedbackPrompt,
    ResearchPromptStatus,
    ResearchProvenance,
    ResearchSession,
    ResearchStatusBasis,
    ResearchSubject,
)
from app.services.research_event_dictionary import PAGE_AREAS, REPORTING_AREAS
from app.services.research_dictionary import is_opaque_ref as is_uuid
from app.services.research_scope import EXPORT_SCOPE_DETAIL_CODES, export_session_provenances
from app.services.schedule_occurrences import to_app_local

PAGE_SIZE = 25
TIMELINE_CAP = 500
_MAX_PAGE = 100_000

PROVENANCE_LABELS = {
    "study": "Study data",
}

PROMPT_STATES = ("awaiting_display", "offer_expired", "awaiting_response", "no_response",
                 "answered", "dismissed")
PROMPT_STATE_LABELS = {
    "awaiting_display": "Offered, not yet shown",
    "offer_expired": "Offered, never shown",
    "awaiting_response": "Shown, awaiting an answer",
    "no_response": "Shown, not answered",
    "answered": "Answered",
    "dismissed": "Skipped",
}

END_REASON_LABELS = {
    "logout": "Logged out",
    "inactivity": "Inactivity",
    "configuration_changed": "Configuration changed",
    "collection_stopped": "Collection paused or stopped",
    "subject_ineligible": "Student excluded or no longer eligible",
}


def normalize_page(value):
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if 1 <= page <= _MAX_PAGE else 1




def ms_to_local(tz_name, moment_ms):
    if moment_ms is None:
        return None
    return to_app_local(tz_name, datetime.fromtimestamp(moment_ms / 1000, tz=timezone.utc))




# ---------------------------------------------------------------------------
# Configurations
# ---------------------------------------------------------------------------


def active_configuration():
    return ResearchConfiguration.query.filter(ResearchConfiguration.current_marker == 1, ResearchConfiguration.event_schema_version == COLLECTION_SCHEMA).first()


def configurations_page(page):
    total = int(db.session.query(func.count(ResearchConfiguration.id)).filter(ResearchConfiguration.event_schema_version == COLLECTION_SCHEMA).scalar() or 0)
    if page > 1 and (page - 1) * PAGE_SIZE >= total:
        page = 1
    rows = (
        db.session.query(
            ResearchConfiguration.public_id,
            ResearchConfiguration.version_number,
            ResearchConfiguration.label,
            ResearchConfiguration.status,
            ResearchConfiguration.is_collecting,
            ResearchConfiguration.collection_starts_at,
            ResearchConfiguration.collection_ends_at,
            ResearchConfiguration.activated_at,
            ResearchConfiguration.retired_at,
        )
        .filter(ResearchConfiguration.event_schema_version == COLLECTION_SCHEMA).order_by(ResearchConfiguration.version_number.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page


def configuration_by_public_id(public_id):
    if not is_uuid(public_id):
        return None
    return ResearchConfiguration.query.filter_by(public_id=public_id, event_schema_version=COLLECTION_SCHEMA).first()


def configuration_version_number(configuration_id):
    if configuration_id is None:
        return None
    return db.session.query(ResearchConfiguration.version_number).filter(
        ResearchConfiguration.id == configuration_id).scalar()


def configuration_choices():
    return (
        db.session.query(ResearchConfiguration.public_id, ResearchConfiguration.version_number,
                         ResearchConfiguration.label)
        .filter(ResearchConfiguration.activated_at.isnot(None), ResearchConfiguration.event_schema_version == COLLECTION_SCHEMA)
        .filter(ResearchConfiguration.event_schema_version == COLLECTION_SCHEMA).order_by(ResearchConfiguration.version_number.desc())
        .limit(200)
        .all()
    )


# ---------------------------------------------------------------------------
# Dashboard aggregates
# ---------------------------------------------------------------------------


def subject_counts():
    """``{(status, basis, provenance): count}`` -- numbers only."""
    rows = (
        db.session.query(ResearchSubject.collection_status, ResearchSubject.status_basis,
                         ResearchSubject.provenance, func.count(ResearchSubject.id))
        .group_by(ResearchSubject.collection_status, ResearchSubject.status_basis,
                  ResearchSubject.provenance)
        .all()
    )
    return {(r[0], r[1], r[2]): int(r[3]) for r in rows}


def subject_totals():
    """Pseudonymous subject counts by how each status was reached.

    A subject row exists only once collection reached the account (automatic,
    under the population rule) or once an operator recorded a decision about
    it, so "included" counts provisioned subjects, not the whole population.
    """
    counts = subject_counts()
    included = sum(v for (s, _b, _p), v in counts.items()
                   if s == ResearchCollectionStatus.INCLUDED.value)
    reinstated = sum(v for (s, b, _p), v in counts.items()
                     if s == ResearchCollectionStatus.INCLUDED.value
                     and b == ResearchStatusBasis.OPERATOR_REINSTATEMENT.value)
    excluded = sum(v for (s, _b, _p), v in counts.items()
                   if s == ResearchCollectionStatus.EXCLUDED.value)
    legacy = sum(v for (s, b, _p), v in counts.items()
                 if s == ResearchCollectionStatus.EXCLUDED.value
                 and b == ResearchStatusBasis.LEGACY_COLLECTION_EXCLUSION.value)
    return {"included": included, "reinstated": reinstated, "excluded": excluded,
            "legacy_excluded": legacy}


EXCLUSION_BASIS_LABELS = {
    "external_exclusion": "Recorded by an operator",
    "legacy_collection_exclusion": "Carried over from the retired consent records",
}


def excluded_subjects_page(page):
    """Excluded subjects, newest decision first, as pseudonymous codes only."""
    query = db.session.query(ResearchSubject).filter(
        ResearchSubject.collection_status == ResearchCollectionStatus.EXCLUDED.value)
    total = int(query.with_entities(func.count(ResearchSubject.id)).scalar() or 0)
    if page > 1 and (page - 1) * PAGE_SIZE >= total:
        page = 1
    rows = (
        db.session.query(
            ResearchSubject.subject_code,
            ResearchSubject.status_basis,
            ResearchSubject.status_changed_at,
            ResearchSubject.provenance,
        )
        .filter(ResearchSubject.collection_status == ResearchCollectionStatus.EXCLUDED.value)
        .order_by(ResearchSubject.status_changed_at.desc(), ResearchSubject.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page


_COUNTERS = ("batches_received", "events_accepted", "events_duplicate", "events_invalid",
             "events_late", "events_dropped_client", "sampling_eligible_checks",
             "sampling_ineligible_checks")












# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# Exports and audit
# ---------------------------------------------------------------------------


def export_scope_filter(provenance):
    """Persistent source scope from the export creation audit.

    Never infer an archive's source from today's deployment setting
    or from source sessions that retention may already have removed.
    """
    export_session_provenances(provenance)
    detail = (
        select(ResearchAuditEvent.detail_code)
        .where(ResearchAuditEvent.export_id == ResearchExport.id,
               ResearchAuditEvent.action == ResearchAuditAction.EXPORT_CREATED.value)
        .order_by(ResearchAuditEvent.id.asc())
        .limit(1)
        .correlate(ResearchExport)
        .scalar_subquery()
    )
    code = EXPORT_SCOPE_DETAIL_CODES[provenance]
    return (ResearchExport.event_schema_version == COLLECTION_SCHEMA) & (detail == code)


def exports_page(page, provenance):
    scope = export_scope_filter(provenance)
    total = int(db.session.query(func.count(ResearchExport.id)).filter(scope).scalar() or 0)
    if page > 1 and (page - 1) * PAGE_SIZE >= total:
        page = 1
    rows = (
        db.session.query(
            ResearchExport.public_id,
            ResearchExport.created_at,
            ResearchExport.period_from,
            ResearchExport.period_to,
            ResearchConfiguration.version_number,
            ResearchExport.subjects_count,
            ResearchExport.sessions_count,
            ResearchExport.events_count,
            ResearchExport.prompts_count,
            ResearchExport.manifest_digest,
            exists().where(ResearchExportArchive.export_id == ResearchExport.id).label("archive_available"),
        )
        .outerjoin(ResearchConfiguration,
                   ResearchConfiguration.id == ResearchExport.configuration_id)
        .filter(scope)
        .order_by(ResearchExport.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page


def export_by_public_id(public_id, provenance):
    if not is_uuid(public_id):
        return None
    return ResearchExport.query.filter(
        ResearchExport.public_id == public_id, export_scope_filter(provenance)).first()


def audit_page(page, filters=None):
    filters = filters or {}
    conditions = []
    if filters.get("action"):
        conditions.append(ResearchAuditEvent.action == filters["action"])
    if filters.get("lower_date") is not None:
        conditions.append(ResearchAuditEvent.occurred_at >= filters["lower_date"])
    if filters.get("upper_date") is not None:
        conditions.append(ResearchAuditEvent.occurred_at < filters["upper_date"])
    total = int(db.session.query(func.count(ResearchAuditEvent.id)).filter(*conditions).scalar() or 0)
    if page > 1 and (page - 1) * PAGE_SIZE >= total:
        page = 1
    rows = (
        db.session.query(
            ResearchAuditEvent.action,
            ResearchAuditEvent.channel,
            ResearchAuditEvent.actor_id,
            ResearchAuditEvent.occurred_at,
            ResearchAuditEvent.detail_code,
            ResearchAuditEvent.count_value,
            ResearchConfiguration.version_number,
            ResearchExport.public_id,
            ResearchSubject.subject_code,
        )
        .outerjoin(ResearchConfiguration,
                   ResearchConfiguration.id == ResearchAuditEvent.configuration_id)
        .outerjoin(ResearchExport, ResearchExport.id == ResearchAuditEvent.export_id)
        .outerjoin(ResearchSubject, ResearchSubject.id == ResearchAuditEvent.subject_id)
        .filter(*conditions)
        .order_by(ResearchAuditEvent.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page
