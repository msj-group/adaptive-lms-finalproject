"""Researcher-only storage visibility and complete-session manual cleanup."""
import hashlib
import json
from flask import current_app
from sqlalchemy import bindparam, func, or_, select, text
from app.extensions import db
from app.models import ResearchAuditEvent, ResearchConfiguration, ResearchEvent, ResearchExport, ResearchExportArchive, ResearchFeedbackPrompt, ResearchSession, User, now_ms
from app.models.research_storage import ResearchDataGap, ResearchExportSession
from app.services.research_control_gate import lock_research_control
from app.services.research_exports import _bounds

TABLES = ("research_subjects", "research_subject_links", "research_configurations", "research_sessions", "research_events",
    "research_feedback_prompts", "research_exports", "research_export_archives", "research_audit_events",
    "research_export_sessions", "research_data_gaps", "research_configuration_sequence")


def _researcher(actor_id):
    from app.services.actor_authorization import require_current_actor
    return require_current_actor(actor_id, "researcher")


def storage_usage():
    query = text("SELECT COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN :tables").bindparams(bindparam("tables", expanding=True))
    allocated = int(db.session.execute(query, {"tables": TABLES}).scalar() or 0)
    archives, archive_bytes = db.session.query(func.count(ResearchExportArchive.id), func.coalesce(func.sum(ResearchExportArchive.byte_size), 0)).one()
    counts = {"sessions": int(ResearchSession.query.count()), "events": int(ResearchEvent.query.count()),
              "prompts": int(ResearchFeedbackPrompt.query.count()), "archives": int(archives), "archive_bytes": int(archive_bytes),
              "estimated_allocated_bytes": allocated}
    return counts


def _sessions(start, end, all_data, provenance):
    if not all_data and (start is None or end is None):
        raise ValueError("Select both dates for a session-start range, or choose all data.")
    if start and end and end < start:
        raise ValueError("The end date must not precede the start date.")
    if provenance not in {"all", "study", "development", "demo"}:
        raise ValueError("Select a valid provenance.")
    query = ResearchSession.query
    if not all_data:
        try:
            lower, upper = _bounds(start, end, current_app.config["APP_TIMEZONE"])
        except (ValueError, OverflowError) as exc:
            raise ValueError("Select a valid date interval in the center timezone.") from exc
        query = query.filter(ResearchSession.started_at_ms >= lower, ResearchSession.started_at_ms < upper)
    if provenance != "all":
        query = query.filter(ResearchSession.provenance == provenance)
    return query


def _cohort_digest(query):
    digest = hashlib.sha256()
    last_id = 0
    while True:
        rows = query.filter(ResearchSession.id > last_id).with_entities(ResearchSession.id, ResearchSession.public_id).order_by(ResearchSession.id).limit(500).all()
        if not rows:
            return digest.hexdigest()
        for _id, public_id in rows:
            digest.update((public_id + "\n").encode("ascii"))
        last_id = rows[-1][0]


def _affected_archives(query):
    refs = query.with_entities(ResearchSession.public_id).statement
    exports = select(ResearchExportSession.export_id).where(ResearchExportSession.session_public_id.in_(refs))
    # Old exports with missing membership metadata are removed conservatively;
    # the preview includes them, while the description/digest remains intact.
    unknown = ~select(ResearchExportSession.id).where(ResearchExportSession.export_id == ResearchExport.id).exists()
    return ResearchExportArchive.query.join(ResearchExport, ResearchExport.id == ResearchExportArchive.export_id).filter(
        or_(ResearchExport.id.in_(exports), (ResearchExport.sessions_count > 0) & unknown))


def cleanup_preview(start, end, all_data=False, provenance="all"):
    query = _sessions(start, end, all_data, provenance)
    selected = query.with_entities(ResearchSession.id).statement
    count = int(query.count())
    archives = _affected_archives(query) if count else ResearchExportArchive.query.filter(False)
    return {"start": start.isoformat() if start and not all_data else None, "end": end.isoformat() if end and not all_data else None,
        "all_data": bool(all_data), "provenance": provenance, "timezone": current_app.config["APP_TIMEZONE"],
        "sessions": count, "events": int(ResearchEvent.query.filter(ResearchEvent.session_id.in_(selected)).count()),
        "prompts": int(ResearchFeedbackPrompt.query.filter(ResearchFeedbackPrompt.session_id.in_(selected)).count()),
        "archives": int(archives.count()), "open_sessions": int(query.filter(ResearchSession.ended_at_ms.is_(None)).count()),
        "selection_digest": _cohort_digest(query)}


def cleanup_data(actor_id, expected, start, end, all_data=False, provenance="all"):
    db.session.rollback()
    actor = _researcher(actor_id)
    lock_research_control()
    from app.services.research_control_gate import lock_all_configurations
    if lock_all_configurations():
        raise ValueError("Pause research collection before deleting raw data.")
    actual = cleanup_preview(start, end, all_data, provenance)
    if actual != expected:
        raise ValueError("The selection changed. Review a new cleanup preview.")
    if actual["open_sessions"]:
        raise ValueError("Close the selected sessions through the collection pause action before cleanup.")
    if not actual["sessions"]:
        return actual
    query = _sessions(start, end, all_data, provenance)
    # The control gate and all configuration locks exclude collection and
    # concurrent retention/export removal while whole sessions are removed.
    while True:
        archive_ids = [row[0] for row in _affected_archives(query).with_entities(ResearchExportArchive.id).order_by(ResearchExportArchive.id).limit(500).with_for_update().all()]
        if not archive_ids:
            break
        ResearchExportArchive.query.filter(ResearchExportArchive.id.in_(archive_ids)).delete(synchronize_session=False)
    while True:
        ids = [row[0] for row in query.with_entities(ResearchSession.id).order_by(ResearchSession.id).limit(500).with_for_update().all()]
        if not ids:
            break
        ResearchEvent.query.filter(ResearchEvent.session_id.in_(ids)).delete(synchronize_session=False)
        ResearchFeedbackPrompt.query.filter(ResearchFeedbackPrompt.session_id.in_(ids)).delete(synchronize_session=False)
        ResearchSession.query.filter(ResearchSession.id.in_(ids)).delete(synchronize_session=False)
    db.session.add(ResearchDataGap(actor_id=actor.id, reason_code="manual_all" if all_data else "manual_range",
        period_from=start if not all_data else None, period_to=end if not all_data else None,
        timezone=actual["timezone"], selection_digest=actual["selection_digest"], sessions_count=actual["sessions"],
        events_count=actual["events"], prompts_count=actual["prompts"], archives_count=actual["archives"]))
    db.session.add(ResearchAuditEvent(action="data_cleaned", channel="workspace", actor_id=actor.id,
        detail_code="whole_sessions_all" if all_data else "whole_sessions_range", count_value=actual["sessions"]))
    db.session.flush()
    return actual


def remove_archive(actor_id, public_id, expected_digest):
    db.session.rollback()
    actor = _researcher(actor_id)
    lock_research_control()
    export = ResearchExport.query.filter_by(public_id=public_id).populate_existing().with_for_update().first()
    if export is None or export.manifest_digest != expected_digest:
        raise ValueError("This export is unavailable or changed. Reload its record.")
    archive = ResearchExportArchive.query.filter_by(export_id=export.id).populate_existing().with_for_update().first()
    if archive is None:
        return
    ResearchExportArchive.query.filter_by(id=archive.id).delete(synchronize_session=False)
    db.session.add(ResearchAuditEvent(action="archive_removed", channel="workspace", actor_id=actor.id,
        export_id=export.id, count_value=1, detail_code="archive_bytes_removed"))
