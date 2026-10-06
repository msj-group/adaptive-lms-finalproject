"""Research control mutex, immutable export membership and truthful data gaps."""
from sqlalchemy import event
from app.models.code_types import CODE_COLLATION
from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class ResearchConfigurationSequence(db.Model):
    __tablename__ = "research_configuration_sequence"
    __table_args__ = (db.CheckConstraint("id=1 AND last_number>=0", name="ck_research_configuration_sequence_singleton"),)
    id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    last_number = db.Column(db.Integer, nullable=False)


class ResearchExportSession(db.Model):
    __tablename__ = "research_export_sessions"
    __table_args__ = (db.UniqueConstraint("export_id", "session_public_id", name="uq_research_export_sessions_member"),
        db.Index("ix_research_export_sessions_session", "session_public_id", "export_id"))
    id = db.Column(db.BigInteger, primary_key=True)
    export_id = db.Column(db.BigInteger, db.ForeignKey("research_exports.id"), nullable=False)
    # Deliberately no FK to raw sessions: metadata survives raw cleanup.
    session_public_id = db.Column(db.String(36), nullable=False)


class ResearchDataGap(db.Model):
    __tablename__ = "research_data_gaps"
    __table_args__ = (db.Index("ix_research_data_gaps_created", "created_at", "id"),
        db.CheckConstraint("(actor_id IS NOT NULL AND service_principal IS NULL) OR (actor_id IS NULL AND service_principal IS NOT NULL AND service_principal='daily_retention')", name="ck_research_data_gaps_actor"),
        db.CheckConstraint("LENGTH(selection_digest)=64", name="ck_research_data_gaps_digest"),
        db.CheckConstraint("sessions_count>=0 AND events_count>=0 AND prompts_count>=0 AND archives_count>=0", name="ck_research_data_gaps_counts"))
    id = db.Column(db.BigInteger, primary_key=True)
    actor_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=True)
    service_principal = db.Column(db.String(32, collation=CODE_COLLATION), nullable=True)
    reason_code = db.Column(db.String(32), nullable=False)
    period_from = db.Column(db.Date, nullable=True)
    period_to = db.Column(db.Date, nullable=True)
    timezone = db.Column(db.String(64), nullable=False)
    cutoff_ms = db.Column(db.BigInteger, nullable=True)
    selection_digest = db.Column(db.String(64), nullable=False)
    sessions_count = db.Column(db.Integer, nullable=False)
    events_count = db.Column(db.Integer, nullable=False)
    prompts_count = db.Column(db.Integer, nullable=False)
    archives_count = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(ResearchExportSession, "before_update")
@event.listens_for(ResearchExportSession, "before_delete")
@event.listens_for(ResearchDataGap, "before_update")
@event.listens_for(ResearchDataGap, "before_delete")
def _preserve_research_metadata(_mapper, _connection, _target):
    raise ValueError("Research membership and gap metadata are append-only")
