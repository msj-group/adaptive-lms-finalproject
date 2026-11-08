"""Bounded, pseudonymous views of the single natural-use collection method."""
from collections import Counter
from sqlalchemy import func
from app.extensions import db
from app.models import ResearchConfiguration, ResearchSession, ResearchSubject
from app.models.research_observations import (
    ResearchObservation as Observation, ResearchQuestion as Question,
    ResearchSamplingOpportunity as Opportunity, ResearchEvaluationGroup as EvaluationGroup,
    ResearchFeatureMembership as Membership,
)
from app.services.research_scope import export_session_provenances
from app.services.research_storage_contract import COLLECTION_SCHEMA
from app.services.research_features import build_features

PAGE_SIZE = 25
TIMELINE_CAP = 500
REVIEW_GROUPS = ("development", "prospective_monitor")
QUESTION_STATE_LABELS = {
    "reserved": "Reserved, awaiting display", "expired": "Reserved, never shown",
    "awaiting_response": "Shown, awaiting an answer", "no_response": "Shown, not answered",
    "answered": "Answered", "skipped": "Skipped",
}


def session_conditions(provenance, filters=None, *, review=False):
    filters = filters or {}
    terms = [ResearchSession.provenance.in_(export_session_provenances(provenance)),
             ResearchSession.configuration_id.in_(db.session.query(ResearchConfiguration.id).filter(
                 ResearchConfiguration.event_schema_version == COLLECTION_SCHEMA))]
    if provenance == "study":
        terms.append(ResearchSession.subject_id.in_(db.session.query(ResearchSubject.id).filter(
            ResearchSubject.provenance == "study")))
    if review:
        terms.append(ResearchSession.subject_id.in_(db.session.query(EvaluationGroup.subject_id).filter(
            EvaluationGroup.partition_code.in_(REVIEW_GROUPS))))
    if filters.get("subject"):
        terms.append(ResearchSession.subject_id.in_(db.session.query(ResearchSubject.id).filter(
            ResearchSubject.subject_code == filters["subject"])))
    number = filters.get("version") or filters.get("version_number")
    if number:
        terms.append(ResearchSession.configuration_id.in_(db.session.query(ResearchConfiguration.id).filter(
            ResearchConfiguration.version_number == int(number))))
    if filters.get("state") == "open": terms.append(ResearchSession.ended_at_ms.is_(None))
    if filters.get("state") == "ended": terms.append(ResearchSession.ended_at_ms.is_not(None))
    if filters.get("lower") is not None: terms.append(ResearchSession.started_at_ms >= filters["lower"])
    if filters.get("upper") is not None: terms.append(ResearchSession.started_at_ms < filters["upper"])
    return terms


def dashboard_summary(provenance, filters):
    """Technical aggregates only; no rating values or reserved-test labels selected."""
    base = session_conditions(provenance, {"version_number": filters["version_number"]})
    sessions = db.session.query(ResearchSession).filter(*base,
        ResearchSession.started_at_ms >= filters["lower"], ResearchSession.started_at_ms < filters["upper"])
    count, subjects = sessions.with_entities(func.count(ResearchSession.id),
        func.count(func.distinct(ResearchSession.subject_id))).one()
    events = db.session.query(Observation).join(ResearchSession, ResearchSession.id == Observation.session_id).filter(
        *base, Observation.occurred_at_ms >= filters["lower"], Observation.occurred_at_ms < filters["upper"])
    daily = events.with_entities(func.floor(Observation.occurred_at_ms / 86_400_000), func.count()).group_by(
        func.floor(Observation.occurred_at_ms / 86_400_000)).all()
    days = {int(day): int(total) for day, total in daily}
    rows = []
    from datetime import timedelta
    day = filters["start"]
    while day <= filters["end"]:
        bucket = (day - type(day)(1970, 1, 1)).days
        rows.append({"label": day.isoformat(), "count": days.get(bucket, 0)})
        day += timedelta(days=1)
    prompts = db.session.query(Question.id).join(Opportunity, Opportunity.id == Question.opportunity_id).join(
        ResearchSession, ResearchSession.id == Opportunity.session_id).filter(
            *base, Question.granted_at_ms >= filters["lower"], Question.granted_at_ms < filters["upper"])
    return {"sessions": int(count), "subjects": int(subjects), "events": events.count(),
            "answered": prompts.filter(Question.status == "answered").count(),
            "rendered": prompts.filter(Question.rendered_at_ms.is_not(None)).count(),
            "eligible": prompts.filter(Question.status == "answered", Question.answer_timely.is_(True),
                                       Question.quality_code == "eligible").count(),
            "daily": rows, "maximum": max((r["count"] for r in rows), default=0),
            "types": events.with_entities(Observation.source, Observation.event_type, func.count()).group_by(
                Observation.source, Observation.event_type).order_by(func.count().desc()).all(),
            "contexts": events.filter(Observation.event_type == "context_enter").with_entities(
                Observation.context_code, func.count()).group_by(Observation.context_code).all()}


def sessions_page(provenance, page, filters=None):
    terms = session_conditions(provenance, filters, review=True)
    total = db.session.query(func.count(ResearchSession.id)).filter(*terms).scalar() or 0
    if page > 1 and (page - 1) * PAGE_SIZE >= total: page = 1
    counts = db.session.query(Opportunity.session_id.label("session_id"), func.count(Question.id).label("prompts")).join(
        Question, Question.opportunity_id == Opportunity.id).group_by(Opportunity.session_id).subquery()
    rows = db.session.query(ResearchSession.public_id, ResearchSubject.subject_code,
        ResearchConfiguration.version_number, ResearchSession.started_at_ms, ResearchSession.last_seen_at_ms,
        ResearchSession.ended_at_ms, ResearchSession.end_reason, ResearchSession.events_accepted,
        ResearchSession.events_invalid + ResearchSession.events_duplicate + ResearchSession.events_late,
        func.coalesce(counts.c.prompts, 0)).join(ResearchSubject, ResearchSubject.id == ResearchSession.subject_id).join(
        ResearchConfiguration, ResearchConfiguration.id == ResearchSession.configuration_id).outerjoin(
            counts, counts.c.session_id == ResearchSession.id).filter(*terms).order_by(
                ResearchSession.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return rows, int(total), page


def session_detail(public_id, provenance):
    from app.services.research_dictionary import is_opaque_ref as is_uuid
    if not is_uuid(public_id): return None
    counters = ("batches_received", "events_accepted", "events_duplicate", "events_invalid", "events_late",
                "events_dropped_client", "sampling_eligible_checks", "sampling_ineligible_checks")
    return db.session.query(ResearchSession.id, ResearchSession.public_id, ResearchSession.provenance,
        ResearchSubject.subject_code, ResearchConfiguration.version_number,
        ResearchConfiguration.session_inactivity_minutes, ResearchSession.started_at_ms,
        ResearchSession.last_seen_at_ms, ResearchSession.ended_at_ms, ResearchSession.end_reason,
        *(getattr(ResearchSession, key) for key in counters)).join(
            ResearchSubject, ResearchSubject.id == ResearchSession.subject_id).join(
            ResearchConfiguration, ResearchConfiguration.id == ResearchSession.configuration_id).filter(
                *session_conditions(provenance, review=True), ResearchSession.public_id == public_id).first()


def session_timeline(session_id):
    return db.session.query(Observation.source, Observation.event_type, Observation.page_code,
        Observation.context_code, Observation.action_code, Observation.detail_code, Observation.count,
        Observation.occurred_at_ms, Observation.interval_started_at_ms, Observation.clock_uncertainty_ms).filter(
            Observation.session_id == session_id).order_by(
                Observation.occurred_at_ms, Observation.client_sequence, Observation.id).limit(TIMELINE_CAP).all()


def session_questions(session_id, cutoff_ms):
    rows = db.session.query(Question, Opportunity.reason_code).join(Opportunity,
        Opportunity.id == Question.opportunity_id).filter(Opportunity.session_id == session_id).order_by(
            Question.id).limit(50).all()
    result = []
    for question, reason in rows:
        state = question.status
        if state == "reserved" and cutoff_ms >= question.expires_at_ms: state = "expired"
        if state == "rendered":
            state = "no_response" if cutoff_ms > question.response_deadline_ms else "awaiting_response"
        members = db.session.query(Observation).join(Membership, Membership.event_id == Observation.id).filter(
            Membership.prompt_id == question.id).limit(10001).all()
        observed_ms = None
        if len(members) <= 10000:
            observed_ms = build_features(members, cutoff_ms=question.granted_at_ms)["quality"]["observed_ms"]
        result.append({"question": question, "state": state,
            "state_label": QUESTION_STATE_LABELS.get(state, state), "reason": reason,
            "observed_ms": observed_ms, "eligible": question.status == "answered"
            and question.answer_timely is True and question.quality_code == "eligible"})
    return result
