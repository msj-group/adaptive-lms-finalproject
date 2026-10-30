"""Explicit FK-order removal shared by authorized retention/manual cleanup."""
from sqlalchemy import select, text
from app.extensions import db
from app.models.research_v2 import (
    ResearchV2Event as Event, ResearchV2Prompt as Prompt, ResearchV2SamplingOpportunity as Opportunity,
    ResearchV2SamplingState as State, ResearchV2FeatureMembership as Membership,
    ResearchV2DeliveryReceipt as Receipt, ResearchQualityIncident as Incident,
    ResearchQualityReport as Report, ResearchQualityRun as Run,
)


def schema_present():
    # Read only; works while collection is disabled, including before migration.
    return bool(db.session.execute(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='research_v2_feature_memberships'")).scalar())


def raw_counts(selected):
    opportunities = select(Opportunity.id).where(Opportunity.session_id.in_(selected))
    return (db.session.query(Event).filter(Event.session_id.in_(selected)).count(),
            db.session.query(Prompt).filter(Prompt.opportunity_id.in_(opportunities)).count())


def delete_session_children(ids):
    opportunities = select(Opportunity.id).where(Opportunity.session_id.in_(ids))
    prompts = select(Prompt.id).where(Prompt.opportunity_id.in_(opportunities))
    db.session.query(Membership).filter(Membership.prompt_id.in_(prompts)).delete(synchronize_session=False)
    db.session.query(Prompt).filter(Prompt.opportunity_id.in_(opportunities)).delete(synchronize_session=False)
    db.session.query(Opportunity).filter(Opportunity.session_id.in_(ids)).delete(synchronize_session=False)
    db.session.query(Event).filter(Event.session_id.in_(ids)).delete(synchronize_session=False)
    db.session.query(Receipt).filter(Receipt.session_id.in_(ids)).delete(synchronize_session=False)
    db.session.query(State).filter(State.session_id.in_(ids)).delete(synchronize_session=False)


def purge_quality(moment, *, execute=False):
    # A report/run can be retained by a still-live incident. Remove that incident
    # only at its own approved expiry, then remove eligible unreferenced parents.
    incidents = db.session.query(Incident).filter(Incident.expiry_at_ms <= moment)
    reports = db.session.query(Report).filter(Report.expiry_at_ms <= moment)
    runs = db.session.query(Run).filter(Run.expiry_at_ms <= moment)
    unresolved = db.session.query(Receipt).filter(Receipt.session_id.is_(None), Receipt.received_at_ms + 45 * 86400000 <= moment)
    if not execute:
        return incidents.count() + reports.count() + runs.count() + unresolved.count()
    unresolved.delete(synchronize_session=False)
    incidents.delete(synchronize_session=False)
    referenced_reports = select(Incident.latest_report_id).where(Incident.latest_report_id.is_not(None))
    reports.filter(~Report.id.in_(referenced_reports)).delete(synchronize_session=False)
    referenced_runs = select(Report.run_id)
    first_runs = select(Incident.first_run_id)
    last_runs = select(Incident.last_run_id)
    runs.filter(~Run.id.in_(referenced_runs), ~Run.id.in_(first_runs), ~Run.id.in_(last_runs)).delete(synchronize_session=False)
    return 0
