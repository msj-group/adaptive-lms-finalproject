"""15-day expiry with an authenticated operator or dedicated service principal."""
from flask import current_app
from app.extensions import db
from app.models import ResearchAuditEvent, ResearchConfiguration, ResearchEvent, ResearchExport, ResearchExportArchive, ResearchFeedbackPrompt, ResearchSession, User, now_ms
from app.models.research_storage import ResearchDataGap
from app.services.research_control_gate import lock_research_control
from app.services.research_storage import _cohort_digest


def purge_expired(retention_days, moment_ms=None, execute=False, *, actor_id=None, service_principal=None):
    from app.services.research_operator import RetentionReport
    from app.services.research_settings import parse_retention_days
    retention_days = parse_retention_days(retention_days)
    if retention_days is None or retention_days != current_app.config.get("RESEARCH_RETENTION_DAYS"):
        raise ValueError("Use the configured deployment retention period (approved deployment: 15 days).")
    db.session.rollback()
    if service_principal is not None:
        if service_principal != "daily_retention" or actor_id is not None:
            raise ValueError("Invalid retention service principal.")
        channel = "service"
    else:
        actor = User.query.filter_by(id=actor_id, role="researcher", status="active").populate_existing().with_for_update().first()
        if actor is None:
            raise ValueError("An authenticated active Researcher is required.")
        from app.services.actor_authorization import require_operator_authentication
        require_operator_authentication(actor)
        channel = "operator"
    lock_research_control()
    from app.services.research_control_gate import lock_all_configurations
    lock_all_configurations()
    moment_ms = now_ms() if moment_ms is None else moment_ms
    cutoff = moment_ms - retention_days * 24 * 60 * 60 * 1000
    expired = ResearchSession.query.filter(ResearchSession.last_seen_at_ms < cutoff)
    selected = expired.with_entities(ResearchSession.id).statement
    sessions = int(expired.count())
    events = int(ResearchEvent.query.filter(ResearchEvent.session_id.in_(selected)).count())
    prompts = int(ResearchFeedbackPrompt.query.filter(ResearchFeedbackPrompt.session_id.in_(selected)).count())
    expired_archives = ResearchExportArchive.query.join(ResearchExport, ResearchExport.id == ResearchExportArchive.export_id).filter(ResearchExport.oldest_last_seen_ms < cutoff)
    archives = int(expired_archives.count())
    if not execute or (not sessions and not archives):
        db.session.rollback()
        return RetentionReport(cutoff, sessions, events, prompts, archives, False)
    digest = _cohort_digest(expired)
    while True:
        ids = [row[0] for row in expired_archives.with_entities(ResearchExportArchive.id).order_by(ResearchExportArchive.id).limit(500).with_for_update().all()]
        if not ids:
            break
        ResearchExportArchive.query.filter(ResearchExportArchive.id.in_(ids)).delete(synchronize_session=False)
    while True:
        ids = [row[0] for row in expired.with_entities(ResearchSession.id).order_by(ResearchSession.id).limit(500).with_for_update().all()]
        if not ids:
            break
        ResearchEvent.query.filter(ResearchEvent.session_id.in_(ids)).delete(synchronize_session=False)
        ResearchFeedbackPrompt.query.filter(ResearchFeedbackPrompt.session_id.in_(ids)).delete(synchronize_session=False)
        ResearchSession.query.filter(ResearchSession.id.in_(ids)).delete(synchronize_session=False)
    db.session.add(ResearchAuditEvent(action="retention_purged", channel=channel, actor_id=actor_id,
        service_principal=service_principal, detail_code=f"daily;days={retention_days}", count_value=sessions))
    db.session.add(ResearchDataGap(actor_id=actor_id, service_principal=service_principal, reason_code="retention_last_activity",
        timezone=current_app.config["APP_TIMEZONE"], cutoff_ms=cutoff, selection_digest=digest,
        sessions_count=sessions, events_count=events, prompts_count=prompts, archives_count=archives))
    db.session.commit()
    return RetentionReport(cutoff, sessions, events, prompts, archives, True)
