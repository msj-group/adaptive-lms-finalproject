"""Immediate, audited collection control in one locked transaction."""
from datetime import datetime
from flask import current_app
from app.extensions import db
from app.models import (ResearchConfiguration, ResearchAuditAction,
    ResearchSessionEndReason, now_ms)
from app.models.research_observations import ResearchProtocol
from app.models.submission_feedback import whole_second_utc
from app.services import research_configuration_transactions as tx
from app.services.research_collection import close_open_sessions
from app.services.research_policy import POLICY, PROTOCOL
from app.services.research_storage_contract import COLLECTION_SCHEMA


def approved_end(configuration):
    """Preserve the owner's study limit; the UI never asks for a schedule."""
    raw = current_app.config.get("RESEARCH_COLLECTION_ENDS_AT")
    if raw:
        end = datetime.fromisoformat(raw)
        if end.tzinfo is not None:
            raise ValueError("Collection end must use naive UTC.")
        return end
    return configuration.collection_ends_at if configuration else None


def control(actor_id, public_id, version, collecting):
    if type(collecting) is not bool:
        return tx.CONFLICT
    actor = tx._lock_researcher(actor_id)
    if actor is None:
        return tx.UNAUTHORIZED
    active = ResearchConfiguration.query.filter_by(current_marker=1).with_for_update().first()
    state = (active.public_id, active.version) if active else (None, 0)
    if state != (public_id, version):
        db.session.rollback()
        return tx.STALE
    if not collecting:
        if active is None or not active.is_collecting:
            db.session.rollback()
            return tx.ALREADY
        closed = close_open_sessions(active, now_ms(), ResearchSessionEndReason.COLLECTION_STOPPED.value)
        active.is_collecting = False
        active.version += 1
        active.updated_at = max(whole_second_utc(), active.updated_at)
        tx._audit(ResearchAuditAction.COLLECTION_PAUSED.value, actor.id, active.id, count=closed)
        return tx._commit(tx.PAUSED)
    if (not current_app.config.get("RESEARCH_COLLECTION_ENABLED", False)
            or current_app.config.get("RESEARCH_DATA_PROVENANCE") != "study"
            or current_app.config.get("RESEARCH_RETENTION_DAYS") != 45):
        db.session.rollback()
        return tx.COLLECTION_NOT_READY
    now = whole_second_utc()
    end = approved_end(active)
    if end is None:
        db.session.rollback()
        return tx.NO_RETENTION
    if end <= now:
        db.session.rollback()
        return tx.PERIOD_ENDED
    if active and active.collection_starts_at <= now < active.collection_ends_at:
        protocol = db.session.get(ResearchProtocol, active.id)
        if (active.event_schema_version != COLLECTION_SCHEMA or active.retention_days != 45
                or protocol is None or any(getattr(active, k) != v for k, v in POLICY.items())
                or any(getattr(protocol, k) != v for k, v in PROTOCOL.items())):
            db.session.rollback()
            return tx.COLLECTION_NOT_READY
        if active.is_collecting:
            db.session.rollback()
            return tx.ALREADY
    else:
        from app.services.research_control_gate import lock_research_control
        sequence = lock_research_control()
        if sequence.last_number >= 2147483647:
            db.session.rollback()
            return tx.CONFLICT
        sequence.last_number += 1
        if active:
            close_open_sessions(active, now_ms(), ResearchSessionEndReason.CONFIGURATION_CHANGED.value)
            active.status, active.current_marker, active.is_collecting = "retired", None, False
            active.retired_at, active.retired_by_id = max(now, active.activated_at), actor.id
            active.updated_at = max(now, active.updated_at)
            active.version += 1
            db.session.flush()
        active = ResearchConfiguration(version_number=sequence.last_number,
            label="Study collection", event_schema_version=COLLECTION_SCHEMA,
            status="draft", is_collecting=False, collection_starts_at=now,
            collection_ends_at=end, created_by_id=actor.id, created_at=now, updated_at=now, **POLICY)
        db.session.add(active)
        db.session.flush()
        db.session.add(ResearchProtocol(configuration_id=active.id, created_at_ms=now_ms(), **PROTOCOL))
        db.session.flush()
        tx._audit(ResearchAuditAction.CONFIGURATION_CREATED.value, actor.id, active.id)
        active.status, active.current_marker = "active", 1
        active.activated_at, active.activated_by_id, active.retention_days = now, actor.id, 45
        active.version += 1
        tx._audit(ResearchAuditAction.CONFIGURATION_ACTIVATED.value, actor.id, active.id, count=45)
    active.is_collecting = True
    active.version += 1
    active.updated_at = max(now, active.updated_at)
    tx._audit(ResearchAuditAction.COLLECTION_RESUMED.value, actor.id, active.id)
    return tx._commit(tx.COLLECTING)
