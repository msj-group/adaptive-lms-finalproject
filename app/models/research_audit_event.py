"""The append-only audit of research administration (Phase 6 replacement).

One row per configuration change, collection pause or resume, subject
inclusion or exclusion, export creation or download, and retention purge.

**No Student content.** A row names the action, the channel it came from
(``workspace``, ``operator`` or ``migration``), the acting Researcher when
there is one, and the configuration, export or subject it concerns -- by
foreign key, never by name or email. ``detail_code`` is a short closed
vocabulary (for example a status basis); there is no free text.

Rows are never edited or deleted.
"""
from app.models.code_types import CODE_COLLATION

from sqlalchemy import event

from app.extensions import db
from app.models.enums import ResearchAuditAction, ResearchAuditChannel
from app.models.research_common import ID_TYPE, ResearchDataError, in_list_sql
from app.models.submission_feedback import whole_second_utc

AUDIT_ACTIONS = tuple(a.value for a in ResearchAuditAction)
AUDIT_CHANNELS = tuple(c.value for c in ResearchAuditChannel)
AUDIT_DETAIL_MAX_LENGTH = 40


class ResearchAuditEvent(db.Model):
    __tablename__ = "research_audit_events"
    __table_args__ = (
        db.CheckConstraint(
            in_list_sql("action", AUDIT_ACTIONS), name="ck_research_audit_events_action_valid"
        ),
        db.CheckConstraint(
            in_list_sql("channel", AUDIT_CHANNELS), name="ck_research_audit_events_channel_valid"
        ),
        db.CheckConstraint(
            "channel <> 'workspace' OR actor_id IS NOT NULL",
            name="ck_research_audit_events_workspace_actor",
        ),
        db.CheckConstraint("channel <> 'operator' OR actor_id IS NOT NULL OR (service_principal IS NOT NULL AND service_principal='legacy_unattributed')", name="ck_research_audit_events_operator_actor"),
        db.CheckConstraint("channel <> 'service' OR (actor_id IS NULL AND service_principal IS NOT NULL AND service_principal='daily_retention' AND action='retention_purged')", name="ck_research_audit_events_service_actor"),
        db.Index("ix_research_audit_events_occurred", "occurred_at", "id"),
        db.Index("ix_research_audit_events_actor_id", "actor_id"),
        db.Index("ix_research_audit_events_configuration_id", "configuration_id"),
        db.Index("ix_research_audit_events_export_id", "export_id"),
        db.Index("ix_research_audit_events_subject_id", "subject_id"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    action = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    channel = db.Column(db.String(16, collation=CODE_COLLATION), nullable=False)
    actor_id = db.Column(ID_TYPE, db.ForeignKey("users.id"), nullable=True)
    service_principal = db.Column(db.String(32, collation=CODE_COLLATION), nullable=True)
    configuration_id = db.Column(
        ID_TYPE, db.ForeignKey("research_configurations.id"), nullable=True
    )
    export_id = db.Column(ID_TYPE, db.ForeignKey("research_exports.id"), nullable=True)
    subject_id = db.Column(ID_TYPE, db.ForeignKey("research_subjects.id"), nullable=True)
    detail_code = db.Column(db.String(AUDIT_DETAIL_MAX_LENGTH), nullable=True)
    count_value = db.Column(db.Integer, nullable=True)
    occurred_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(ResearchAuditEvent, "before_insert")
def _validate_actual_operator(_mapper, connection, target):
    from sqlalchemy import text
    if target.service_principal == "legacy_unattributed":
        raise ResearchDataError("Only historical migration rows may be unattributed")
    if target.channel in {"workspace", "operator"}:
        if target.service_principal is not None or target.actor_id is None or not connection.execute(
                text("SELECT id FROM users WHERE id=:id AND role='researcher' AND status='active'"), {"id":target.actor_id}).first():
            raise ResearchDataError("Research actions require their actual active Researcher")
    if target.channel == "service" and (target.actor_id is not None or target.service_principal != "daily_retention" or target.action != "retention_purged"):
        raise ResearchDataError("Invalid research service attribution")


@event.listens_for(ResearchAuditEvent, "before_update")
def _audit_is_never_edited(_mapper, _connection, _target):
    raise ResearchDataError("A research audit event is never edited")


@event.listens_for(ResearchAuditEvent, "before_delete")
def _audit_is_never_deleted(_mapper, _connection, _target):
    raise ResearchDataError("A research audit event is never deleted")
