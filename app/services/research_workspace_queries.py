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
from app.services.research_event_validation import is_uuid
from app.services.research_scope import EXPORT_SCOPE_DETAIL_CODES, export_session_provenances
from app.services.schedule_occurrences import to_app_local

PAGE_SIZE = 25
TIMELINE_CAP = 500
_MAX_PAGE = 100_000

PROVENANCE_LABELS = {
    "study": "Study data",
    "development": "Operational review data — excluded from study exports",
    "demo": "Historical data — excluded from study exports",
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


def _session_scope(provenance):
    """Server-selected study scope, or retained non-study review history."""
    return ResearchSession.provenance.in_(export_session_provenances(provenance))


def ms_to_local(tz_name, moment_ms):
    if moment_ms is None:
        return None
    return to_app_local(tz_name, datetime.fromtimestamp(moment_ms / 1000, tz=timezone.utc))


def derived_prompt_state(status, offered_at_ms, displayed_at_ms, ttl_seconds, window_seconds,
                         as_of_ms, responded_at_ms=None):
    """The analysis state of one prompt at `as_of_ms`. Pure.

    Evaluated from the stored moments: a display or a response stored with a
    moment later than `as_of_ms` had not happened yet at `as_of_ms`, so it
    does not count. (Exports never reach that case: they refuse any stored
    moment later than their cutoff, see ``research_exports``.)
    """
    responded = responded_at_ms is not None and responded_at_ms <= as_of_ms
    if status == ResearchPromptStatus.ANSWERED.value and responded:
        return "answered"
    if status == ResearchPromptStatus.DISMISSED.value and responded:
        return "dismissed"
    if displayed_at_ms is not None and displayed_at_ms <= as_of_ms:
        return "no_response" if as_of_ms - displayed_at_ms > window_seconds * 1000 \
            else "awaiting_response"
    return "offer_expired" if as_of_ms - offered_at_ms > ttl_seconds * 1000 \
        else "awaiting_display"


# ---------------------------------------------------------------------------
# Configurations
# ---------------------------------------------------------------------------


def active_configuration():
    return ResearchConfiguration.query.filter(ResearchConfiguration.current_marker == 1).first()


def configurations_page(page):
    total = int(db.session.query(func.count(ResearchConfiguration.id)).scalar() or 0)
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
        .order_by(ResearchConfiguration.version_number.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page


def configuration_by_public_id(public_id):
    if not is_uuid(public_id):
        return None
    return ResearchConfiguration.query.filter_by(public_id=public_id).first()


def configuration_version_number(configuration_id):
    if configuration_id is None:
        return None
    return db.session.query(ResearchConfiguration.version_number).filter(
        ResearchConfiguration.id == configuration_id).scalar()


def configuration_choices():
    return (
        db.session.query(ResearchConfiguration.public_id, ResearchConfiguration.version_number,
                         ResearchConfiguration.label)
        .filter(ResearchConfiguration.activated_at.isnot(None))
        .order_by(ResearchConfiguration.version_number.desc())
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


def session_totals(provenance):
    row = (
        db.session.query(
            func.count(ResearchSession.id),
            func.count(ResearchSession.ended_at_ms),
            func.count(func.distinct(ResearchSession.subject_id)),
            *(func.coalesce(func.sum(getattr(ResearchSession, c)), 0) for c in _COUNTERS),
        )
        .filter(_session_scope(provenance))
        .one()
    )
    totals = {"sessions": int(row[0]), "ended": int(row[1]), "subjects": int(row[2])}
    totals.update({c: int(v) for c, v in zip(_COUNTERS, row[3:])})
    return totals


def prompt_summary(provenance, as_of_ms):
    """Delivery/response counts; grouped in SQL, not an unbounded row load."""
    from app.services.research_analytics import prompt_state_expression
    states, reasons, deferrals = Counter(), Counter(), Counter()
    p = ResearchFeedbackPrompt
    state = prompt_state_expression(as_of_ms)
    rows = (db.session.query(state, p.sampling_reason, p.last_deferral_reason,
                             func.count(p.id), func.sum(p.deferral_count), func.sum(p.late_response))
            .join(ResearchSession, ResearchSession.id == p.session_id)
            .join(ResearchConfiguration, ResearchConfiguration.id == p.configuration_id)
            .filter(_session_scope(provenance))
            .group_by(state, p.sampling_reason, p.last_deferral_reason).all())
    late = 0
    for state_name, reason, deferral_reason, count, deferred, late_count in rows:
        states[state_name] += int(count)
        reasons[reason] += int(count)
        if deferred:
            deferrals[deferral_reason] += int(deferred)
        late += int(late_count or 0)
    displayed = sum(states[s] for s in ("awaiting_response", "no_response", "answered",
                                        "dismissed"))
    return {
        "states": {state: states[state] for state in PROMPT_STATES},
        "offered": sum(states.values()),
        "displayed": displayed,
        "answered": states["answered"],
        "reasons": dict(reasons),
        "deferrals": dict(deferrals),
        "late_responses": late,
        "label_coverage": (states["answered"] / displayed) if displayed else None,
    }


def event_type_counts(provenance):
    rows = (
        db.session.query(ResearchEvent.source, ResearchEvent.event_type,
                         func.count(ResearchEvent.id))
        .join(ResearchSession, ResearchSession.id == ResearchEvent.session_id)
        .filter(_session_scope(provenance))
        .group_by(ResearchEvent.source, ResearchEvent.event_type)
        .all()
    )
    return sorted(((r[0], r[1], int(r[2])) for r in rows), key=lambda r: (r[0], -r[2], r[1]))


def device_coverage(provenance):
    rows = (
        db.session.query(ResearchEvent.detail_code, func.count(ResearchEvent.id))
        .join(ResearchSession, ResearchSession.id == ResearchEvent.session_id)
        .filter(_session_scope(provenance),
                ResearchEvent.event_type == "page_view")
        .group_by(ResearchEvent.detail_code)
        .all()
    )
    counted = {r[0]: int(r[1]) for r in rows}
    return {size: counted.get(size, 0) for size in ("narrow", "medium", "wide")}


def area_coverage(provenance):
    rows = (
        db.session.query(ResearchEvent.page_id, func.count(ResearchEvent.id))
        .join(ResearchSession, ResearchSession.id == ResearchEvent.session_id)
        .filter(_session_scope(provenance),
                ResearchEvent.event_type == "page_view")
        .group_by(ResearchEvent.page_id)
        .all()
    )
    areas = Counter()
    for page_id, count in rows:
        areas[PAGE_AREAS.get(page_id, "unknown")] += int(count)
    return {area: areas.get(area, 0) for area in ("core",) + REPORTING_AREAS}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def sessions_page(provenance, page, filters=None):
    filters = filters or {}
    conditions = [_session_scope(provenance)]
    if filters.get("subject"):
        conditions.append(ResearchSubject.subject_code == filters["subject"])
    if filters.get("version"):
        conditions.append(ResearchConfiguration.version_number == filters["version"])
    if filters.get("state") == "open":
        conditions.append(ResearchSession.ended_at_ms.is_(None))
    elif filters.get("state") == "ended":
        conditions.append(ResearchSession.ended_at_ms.is_not(None))
    if filters.get("lower") is not None:
        conditions.append(ResearchSession.started_at_ms >= filters["lower"])
    if filters.get("upper") is not None:
        conditions.append(ResearchSession.started_at_ms < filters["upper"])
    base = db.session.query(func.count(ResearchSession.id)).join(
        ResearchSubject, ResearchSubject.id == ResearchSession.subject_id).join(
        ResearchConfiguration, ResearchConfiguration.id == ResearchSession.configuration_id).filter(*conditions)
    total = int(base.scalar() or 0)
    if page > 1 and (page - 1) * PAGE_SIZE >= total:
        page = 1
    prompt_counts = (
        db.session.query(ResearchFeedbackPrompt.session_id,
                         func.count(ResearchFeedbackPrompt.id).label("prompts"))
        .group_by(ResearchFeedbackPrompt.session_id)
        .subquery()
    )
    rows = (
        db.session.query(
            ResearchSession.public_id,
            ResearchSubject.subject_code,
            ResearchConfiguration.version_number,
            ResearchSession.started_at_ms,
            ResearchSession.last_seen_at_ms,
            ResearchSession.ended_at_ms,
            ResearchSession.end_reason,
            ResearchSession.events_accepted,
            ResearchSession.events_invalid + ResearchSession.events_duplicate
            + ResearchSession.events_late,
            func.coalesce(prompt_counts.c.prompts, 0),
        )
        .join(ResearchSubject, ResearchSubject.id == ResearchSession.subject_id)
        .join(ResearchConfiguration, ResearchConfiguration.id == ResearchSession.configuration_id)
        .outerjoin(prompt_counts, prompt_counts.c.session_id == ResearchSession.id)
        .filter(*conditions)
        .order_by(ResearchSession.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page


def session_detail(public_id):
    if not is_uuid(public_id):
        return None
    return (
        db.session.query(
            ResearchSession.id,
            ResearchSession.public_id,
            ResearchSession.provenance,
            ResearchSubject.subject_code,
            ResearchConfiguration.version_number,
            ResearchConfiguration.session_inactivity_minutes,
            ResearchSession.started_at_ms,
            ResearchSession.last_seen_at_ms,
            ResearchSession.ended_at_ms,
            ResearchSession.end_reason,
            *(getattr(ResearchSession, c) for c in _COUNTERS),
        )
        .join(ResearchSubject, ResearchSubject.id == ResearchSession.subject_id)
        .join(ResearchConfiguration, ResearchConfiguration.id == ResearchSession.configuration_id)
        .filter(ResearchSession.public_id == public_id)
        .first()
    )


def session_timeline(session_id):
    return (
        db.session.query(
            ResearchEvent.source,
            ResearchEvent.event_type,
            ResearchEvent.page_id,
            ResearchEvent.element_id,
            ResearchEvent.detail_code,
            ResearchEvent.count_value,
            ResearchEvent.duration_ms,
            ResearchEvent.position_value,
            ResearchEvent.occurred_at_ms,
            ResearchEvent.received_at_ms,
        )
        .filter(ResearchEvent.session_id == session_id)
        .order_by(ResearchEvent.occurred_at_ms.asc(), ResearchEvent.id.asc())
        .limit(TIMELINE_CAP)
        .all()
    )


def session_prompts(session_id):
    return (
        db.session.query(
            ResearchFeedbackPrompt.status,
            ResearchFeedbackPrompt.sampling_reason,
            ResearchFeedbackPrompt.offered_at_ms,
            ResearchFeedbackPrompt.deferral_count,
            ResearchFeedbackPrompt.last_deferral_reason,
            ResearchFeedbackPrompt.displayed_at_ms,
            ResearchFeedbackPrompt.window_start_ms,
            ResearchFeedbackPrompt.observed_ms,
            ResearchFeedbackPrompt.responded_at_ms,
            ResearchFeedbackPrompt.rating,
            ResearchFeedbackPrompt.late_response,
            ResearchConfiguration.offer_ttl_seconds,
            ResearchConfiguration.response_window_seconds,
        )
        .join(ResearchConfiguration,
              ResearchConfiguration.id == ResearchFeedbackPrompt.configuration_id)
        .filter(ResearchFeedbackPrompt.session_id == session_id)
        .order_by(ResearchFeedbackPrompt.id.asc())
        .limit(50)
        .all()
    )


# ---------------------------------------------------------------------------
# Exports and audit
# ---------------------------------------------------------------------------


def export_scope_filter(provenance):
    """Persistent scope from creation audit; older exports were study-only.

    Never infer a historical archive's source from today's deployment setting
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
    if provenance == "study":
        return or_(detail.is_(None), detail == code)
    return detail == code


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
