"""Create and serve immutable natural-use datasets under the Researcher gate."""
import csv
import hashlib
import io
from datetime import datetime, time, timedelta, timezone
from flask import current_app
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import (MAX_EXPORT_ARCHIVE_BYTES, ResearchExportArchive, ResearchAuditAction,
    ResearchAuditChannel, ResearchAuditEvent, ResearchConfiguration, ResearchExport,
    ResearchSession, ResearchSubject, User, UserRole, UserStatus, now_ms)
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.research_workspace_queries import export_scope_filter
from app.services.research_scope import EXPORT_SCOPE_DETAIL_CODES, export_session_provenances
from app.services.schedule_occurrences import from_app_local
from app.services.research_storage_contract import COLLECTION_SCHEMA

CREATED = "created"
UNAUTHORIZED = "unauthorized"
TOO_LARGE = "too_large"
CONFLICT = "conflict"
BAD_PERIOD = "bad_period"
SERVED = "served"
NOT_FOUND = "not_found"
EXPIRED = "expired"
CORRUPT = "corrupt"
CLOCK_AHEAD = "clock_ahead"
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

def _safe(value):
    """Neutralise a text cell against spreadsheet formula injection."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _csv(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if v is None else _safe(v) for v in row])
    return buffer.getvalue().encode("utf-8")


def _bounds(period_from, period_to, tz_name):
    """UTC epoch-ms bounds of the local calendar period, or ``(None, None)``."""
    lower = upper = None
    if period_from is not None:
        lower = _local_midnight_ms(period_from, tz_name)
    if period_to is not None:
        upper = _local_midnight_ms(period_to + timedelta(days=1), tz_name)
    return lower, upper


def _local_midnight_ms(day, tz_name):
    utc = from_app_local(tz_name, datetime.combine(day, time.min))
    return int(utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


class ExportTooLarge(Exception):
    pass


class ClockAhead(Exception):
    """A stored server moment is later than the snapshot cutoff."""


def open_read_snapshot():
    """End this session's current transaction, open one read transaction,
    establish its snapshot with a first read, and only then read the cutoff
    from the server clock. Returns ``cutoff_ms``.

    MySQL/InnoDB creates a REPEATABLE READ read view at the first consistent
    (non-locking) read, and every later plain read of the transaction uses
    it; the isolation level is set explicitly for this transaction. The export reads use no
    locking read, which would bypass the snapshot.
    """
    db.session.rollback()
    connection = db.session.connection(
        execution_options={"isolation_level": "REPEATABLE READ"})
    connection.exec_driver_sql("SELECT COUNT(*) FROM research_sessions").scalar()
    return now_ms()


def _researcher_is_active(actor_id):
    row = db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    return row is not None and row[0] == UserRole.RESEARCHER.value \
        and row[1] == UserStatus.ACTIVE.value


def _lock_researcher(actor_id):
    lock_academic_hierarchy()
    actor = db.session.query(User).filter(User.id == actor_id).with_for_update().first()
    if actor is None or actor.role != UserRole.RESEARCHER.value \
            or actor.status != UserStatus.ACTIVE.value:
        db.session.rollback()
        return None
    from app.services.actor_authorization import require_current_actor
    actor = require_current_actor(actor_id, "researcher")
    return actor


def selection_summary(configuration_public_id, period_from, period_to, tz_name):
    """Count the exact supported source/partition scope; creation takes a fresh snapshot."""
    from app.models.research_observations import ResearchObservation, ResearchQuestion, ResearchSamplingOpportunity, ResearchEvaluationGroup
    if period_from and period_to and period_to < period_from:
        raise ValueError("Invalid period")
    config = db.session.query(ResearchConfiguration.id).filter(
        ResearchConfiguration.public_id == configuration_public_id,
        ResearchConfiguration.event_schema_version == COLLECTION_SCHEMA).scalar()
    if config is None:
        raise ValueError("Select collection settings.")
    lower, upper = _bounds(period_from, period_to, tz_name)
    provenance = current_app.config["RESEARCH_DATA_PROVENANCE"]
    query = db.session.query(ResearchSession.id, ResearchSession.subject_id).join(
        ResearchSubject, ResearchSubject.id == ResearchSession.subject_id).join(
        ResearchEvaluationGroup, ResearchEvaluationGroup.subject_id == ResearchSession.subject_id).filter(
            ResearchSession.configuration_id == config,
            ResearchSession.provenance.in_(export_session_provenances(provenance)),
            ResearchEvaluationGroup.partition_code.in_(("development", "prospective_monitor")))
    if provenance == "study":
        query = query.filter(ResearchSubject.provenance == "study")
    if lower is not None: query = query.filter(ResearchSession.started_at_ms >= lower)
    if upper is not None: query = query.filter(ResearchSession.started_at_ms < upper)
    scope = query.subquery()
    sessions, subjects = db.session.execute(select(func.count(), func.count(func.distinct(scope.c.subject_id))).select_from(scope)).one()
    events = db.session.execute(select(func.count(ResearchObservation.id)).join(scope, scope.c.id == ResearchObservation.session_id)).scalar()
    prompts = db.session.execute(select(func.count(ResearchQuestion.id)).join(
        ResearchSamplingOpportunity, ResearchQuestion.opportunity_id == ResearchSamplingOpportunity.id).join(
        scope, scope.c.id == ResearchSamplingOpportunity.session_id)).scalar()
    return {"sessions": int(sessions), "subjects": int(subjects), "events": int(events), "prompts": int(prompts)}

def create_export(actor_id, configuration_public_id, period_from, period_to, tz_name):
    """Build one read snapshot, then recheck authorization/retention under locks."""
    if period_from and period_to and period_to < period_from:
        return BAD_PERIOD, None
    cutoff = open_read_snapshot()
    if not _researcher_is_active(actor_id):
        db.session.rollback()
        return UNAUTHORIZED, None
    configuration_id = db.session.query(ResearchConfiguration.id).filter(
        ResearchConfiguration.public_id == configuration_public_id,
        ResearchConfiguration.event_schema_version == COLLECTION_SCHEMA).scalar()
    if configuration_id is None or not current_app.config.get("RESEARCH_COLLECTION_ENABLED", False):
        db.session.rollback()
        return CONFLICT, None
    try:
        from app.services.research_dataset_exports import build_export
        data, manifest, counts, digest = build_export(configuration_id, period_from, period_to, cutoff, tz_name)
    except ExportTooLarge:
        db.session.rollback()
        return TOO_LARGE, None
    except ClockAhead:
        db.session.rollback()
        return CLOCK_AHEAD, None
    except ValueError:
        db.session.rollback()
        return CONFLICT, None
    db.session.rollback()
    if len(data) > MAX_EXPORT_ARCHIVE_BYTES:
        return TOO_LARGE, None
    actor = _lock_researcher(actor_id)
    if actor is None: return UNAUTHORIZED, None
    from app.services.research_control_gate import lock_research_control
    from app.models.research_storage import ResearchExportSession
    from app.models.research_observations import ResearchEvaluationGroup
    lock_research_control()
    for subject_id in counts.get("_subject_ids", []):
        db.session.query(ResearchSubject).filter_by(id=subject_id).with_for_update().one()
    refs = counts["_session_refs"]
    retained = 0
    for start in range(0, len(refs), 500):
        retained += len(db.session.query(ResearchSession.id).filter(
            ResearchSession.public_id.in_(refs[start:start+500])).with_for_update().all())
    if retained != len(refs):
        db.session.rollback()
        return CONFLICT, None
    for subject_id in counts.get("_subject_ids", []):
        part = db.session.get(ResearchEvaluationGroup, subject_id)
        if part is None or part.partition_code not in {"development", "prospective_monitor"}:
            db.session.rollback()
            return CONFLICT, None
        if part.partition_code == "development" and part.development_exposed_at_ms is None:
            part.development_exposed_at_ms = now_ms()
            part.version += 1
    if counts["_expiry_at_ms"] <= now_ms():
        db.session.rollback()
        return EXPIRED, None
    export = ResearchExport(export_format=manifest["format"], event_schema_version=COLLECTION_SCHEMA,
        configuration_id=configuration_id, period_from=period_from, period_to=period_to,
        timezone=tz_name, cutoff_ms=cutoff, oldest_last_seen_ms=counts["oldest_last_seen_ms"],
        subjects_count=counts["subjects"], sessions_count=counts["sessions"], events_count=counts["events"],
        prompts_count=counts["prompts"], manifest_digest=digest, created_by_id=actor.id)
    db.session.add(export)
    db.session.flush()
    db.session.add_all(ResearchExportSession(export_id=export.id, session_public_id=ref) for ref in refs)
    db.session.execute(ResearchExportArchive.__table__.insert().values(export_id=export.id,
        archive_sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data), content=data,
        expiry_at_ms=counts["_expiry_at_ms"]))
    db.session.add(ResearchAuditEvent(action=ResearchAuditAction.EXPORT_CREATED.value,
        channel=ResearchAuditChannel.WORKSPACE.value, actor_id=actor.id, export_id=export.id,
        configuration_id=configuration_id, detail_code=EXPORT_SCOPE_DETAIL_CODES[manifest["filters"]["provenance"]],
        count_value=counts["sessions"]))
    public_id = export.public_id
    try: db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT, None
    return CREATED, public_id

def download_export(actor_id, export_public_id):
    """``(status, zip_bytes)``: the stored bytes of exactly this export.

    Never regenerated. An archive the retention rule removed is
    :data:`EXPIRED`; one whose stored digest no longer matches its bytes is
    :data:`CORRUPT` and is refused rather than served. Only a served
    download is audited as a download.
    """
    actor = _lock_researcher(actor_id)
    if actor is None:
        return UNAUTHORIZED, None
    export = db.session.query(ResearchExport).filter(
        ResearchExport.public_id == export_public_id,
        export_scope_filter(current_app.config["RESEARCH_DATA_PROVENANCE"])).first()
    if export is None:
        db.session.rollback()
        return NOT_FOUND, None
    archive = db.session.query(ResearchExportArchive).filter(
        ResearchExportArchive.export_id == export.id).first()
    if archive is None:
        db.session.rollback()
        return EXPIRED, None
    if archive_is_expired(export, archive):
        db.session.rollback()
        return EXPIRED, None
    data = bytes(archive.content)
    if hashlib.sha256(data).hexdigest() != archive.archive_sha256 \
            or len(data) != archive.byte_size:
        db.session.rollback()
        return CORRUPT, None
    db.session.add(ResearchAuditEvent(
        action=ResearchAuditAction.EXPORT_DOWNLOADED.value,
        channel=ResearchAuditChannel.WORKSPACE.value,
        actor_id=actor.id, export_id=export.id, configuration_id=export.configuration_id,
    ))
    db.session.commit()
    return SERVED, data


def archive_available(export_id):
    """Whether the stored archive of an export still exists."""
    return db.session.query(ResearchExportArchive.id).filter(
        ResearchExportArchive.export_id == export_id).first() is not None


def archive_is_expired(export, archive):
    """Refuse expired archives even if a scheduled cleanup missed its run."""
    return archive.expiry_at_ms is None or archive.expiry_at_ms <= now_ms()
