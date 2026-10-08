"""One research export: its snapshot description (Phase 6 replacement,
corrected).

An export row records **what** a Researcher exported: the filters, the
timezone the period bounds used, the ``cutoff_ms`` of its read snapshot (see
``research_exports`` for the exact time contract), the event schema version,
the row counts, the SHA-256 of the manifest, and the last-activity moment of
the oldest session it contains. The exported bytes themselves are
stored once, at creation, in
:class:`~app.models.research_export_archive.ResearchExportArchive`, and every
download serves exactly those bytes: an export id can never serve different
content. Nothing is regenerated from rows that may have changed since.

``oldest_last_seen_ms`` is what makes the archive obey the retention rule:
the archive is removed as soon as the oldest data it holds expires, so a
copy never outlives its source. The description row is kept.

The row never stores Student content, identity or any row values. Every
creation and every download is also an audit event.
"""
from app.models.code_types import CODE_COLLATION

import uuid

from sqlalchemy import event, inspect

from app.extensions import db
from app.models.research_common import ID_TYPE, ResearchDataError, changed_columns
from app.models.submission_feedback import whole_second_utc

EXPORT_FORMAT = "natural-use-export.v1"
EXPORT_DIGEST_LENGTH = 64
EXPORT_TIMEZONE_MAX_LENGTH = 64

_FIXED = frozenset(
    {"public_id", "export_format", "event_schema_version", "configuration_id", "period_from",
     "period_to", "timezone", "cutoff_ms", "oldest_last_seen_ms", "subjects_count",
     "sessions_count", "events_count", "prompts_count", "manifest_digest", "created_by_id",
     "created_at"}
)


class ResearchExport(db.Model):
    __tablename__ = "research_exports"
    __table_args__ = (
        db.CheckConstraint(
            f"export_format = '{EXPORT_FORMAT}'", name="ck_research_exports_format_valid"
        ),
        db.CheckConstraint(
            f"LENGTH(manifest_digest) = {EXPORT_DIGEST_LENGTH}",
            name="ck_research_exports_digest_format",
        ),
        db.CheckConstraint(
            "subjects_count >= 0 AND sessions_count >= 0 AND events_count >= 0"
            " AND prompts_count >= 0",
            name="ck_research_exports_counts_non_negative",
        ),
        db.CheckConstraint(
            "period_from IS NULL OR period_to IS NULL OR period_to >= period_from",
            name="ck_research_exports_period_ordered",
        ),
        db.CheckConstraint("LENGTH(timezone) > 0", name="ck_research_exports_timezone_present"),
        db.CheckConstraint(
            "(sessions_count = 0 AND oldest_last_seen_ms IS NULL)"
            " OR (sessions_count > 0 AND oldest_last_seen_ms IS NOT NULL)",
            name="ck_research_exports_oldest_present",
        ),
        db.Index("ix_research_exports_created_by_id", "created_by_id"),
        db.Index("ix_research_exports_configuration_id", "configuration_id"),
        db.Index("ix_research_exports_oldest", "oldest_last_seen_ms"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    export_format = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False, default=EXPORT_FORMAT)
    event_schema_version = db.Column(db.String(40), nullable=False)
    #: NULL means every configuration version.
    configuration_id = db.Column(
        ID_TYPE, db.ForeignKey("research_configurations.id"), nullable=True
    )
    #: Inclusive local-calendar session-start bounds; NULL means unbounded.
    period_from = db.Column(db.Date, nullable=True)
    period_to = db.Column(db.Date, nullable=True)
    #: The ``APP_TIMEZONE`` the period bounds were computed in.
    timezone = db.Column(db.String(EXPORT_TIMEZONE_MAX_LENGTH), nullable=False)
    cutoff_ms = db.Column(db.BigInteger, nullable=False)
    #: The last activity of the oldest exported session; NULL for an empty
    #: export. The archive expires with it.
    oldest_last_seen_ms = db.Column(db.BigInteger, nullable=True)
    subjects_count = db.Column(db.Integer, nullable=False)
    sessions_count = db.Column(db.Integer, nullable=False)
    events_count = db.Column(db.Integer, nullable=False)
    prompts_count = db.Column(db.Integer, nullable=False)
    manifest_digest = db.Column(db.String(EXPORT_DIGEST_LENGTH), nullable=False)
    created_by_id = db.Column(ID_TYPE, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(ResearchExport, "before_update")
def _export_snapshot_is_fixed(_mapper, _connection, target):
    if changed_columns(inspect(target), _FIXED):
        raise ResearchDataError("An export snapshot description never changes")


@event.listens_for(ResearchExport, "before_delete")
def _refuse_deleting_an_export(_mapper, _connection, _target):
    raise ResearchDataError("An export snapshot description is never deleted")
