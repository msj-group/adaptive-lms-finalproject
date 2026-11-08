"""Current technical yield for one collection configuration; no rating/content SELECTs."""

from app.services.research_storage_contract import COLLECTION_SCHEMA
from sqlalchemy import func

from app.extensions import db
from app.models import ResearchSession
from app.models.research_observations import (
    ResearchObservation as Event, ResearchQuestion as Prompt,
    ResearchSamplingOpportunity as Opportunity,
    ResearchEvaluationGroup as Partition,
)
from app.services.research_scope import export_session_provenances


def collection_readiness(configuration, provenance, cutoff_ms):
    """Seven-day session-start cohort, with lifetime counters through this read.

    Technical counts include all evaluation groups without exposing their labels.
    They establish collection yield, not statistical power or model readiness.
    No scheduler, new quality report or research write is triggered.
    """
    if configuration is None or configuration.event_schema_version != COLLECTION_SCHEMA:
        return None
    since = max(0, cutoff_ms - 7 * 86_400_000)
    sessions = db.session.query(ResearchSession.id).filter(
        ResearchSession.configuration_id == configuration.id,
        ResearchSession.provenance.in_(export_session_provenances(provenance)),
        ResearchSession.started_at_ms >= since,
        ResearchSession.started_at_ms <= cutoff_ms,
    )
    ids = sessions.subquery()
    selected = db.select(ids.c.id)
    eligible = ((Prompt.status == "answered") & (Prompt.answer_timely.is_(True))
                & (Prompt.quality_code == "eligible"))
    cohort = db.session.query(Prompt.id).join(Opportunity, Opportunity.id == Prompt.opportunity_id).filter(
        Opportunity.session_id.in_(selected), Prompt.granted_at_ms <= cutoff_ms)
    result = {
        "version": configuration.version_number, "since_ms": since, "cutoff_ms": cutoff_ms,
        "sessions": sessions.count(),
        "client_sessions": db.session.query(func.count(func.distinct(Event.session_id))).filter(
            Event.session_id.in_(selected), Event.source == "client", Event.received_at_ms <= cutoff_ms).scalar(),
        "reserved": cohort.count(),
        "rendered": cohort.filter(Prompt.render_received_at_ms <= cutoff_ms).count(),
        "answered": cohort.filter(Prompt.status == "answered", Prompt.response_received_at_ms <= cutoff_ms).count(),
        "eligible": cohort.filter(eligible, Prompt.response_received_at_ms <= cutoff_ms).count(),
        "late": cohort.filter(Prompt.status == "answered", Prompt.answer_timely.is_(False),
                              Prompt.response_received_at_ms <= cutoff_ms).count(),
        "skipped": cohort.filter(Prompt.status == "skipped", Prompt.response_received_at_ms <= cutoff_ms).count(),
    }
    result["eligible_subjects"] = cohort.join(ResearchSession, ResearchSession.id == Opportunity.session_id).filter(
        eligible, Prompt.response_received_at_ms <= cutoff_ms).with_entities(
            func.count(func.distinct(ResearchSession.subject_id))).scalar()
    result["unassigned_subjects"] = db.session.query(func.count(func.distinct(ResearchSession.subject_id))).outerjoin(
        Partition, Partition.subject_id == ResearchSession.subject_id).filter(
            ResearchSession.id.in_(selected),
            func.coalesce(Partition.partition_code, "unassigned") == "unassigned").scalar()
    result["sampling_reasons"] = db.session.query(Opportunity.decision, Opportunity.reason_code, func.count()).filter(
        Opportunity.session_id.in_(selected), Opportunity.decided_at_ms <= cutoff_ms).group_by(
            Opportunity.decision, Opportunity.reason_code).all()
    return result
