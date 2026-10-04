"""The immutable bytes of one research export (Phase 6 replacement,
corrected).

The ZIP a Researcher created is stored here exactly once, with its SHA-256
and size, and every download of that export serves these bytes after
re-checking the digest. A rating, an event, a session end, a change of
``APP_TIMEZONE`` or anything else that happens after the export was created
cannot change what its id serves.

Private research data: it is never served outside the authorized,
audited Researcher download, and it obeys the deployment's retention -- the
operator purge removes an archive as soon as the oldest data in it expires
(the export description stays). The row is never edited; the purge is the
only deletion, and it is a deliberate bulk delete.
"""

from sqlalchemy import event
from sqlalchemy.dialects import mysql

from app.extensions import db
from app.models.research_common import ID_TYPE, ResearchDataError
from app.models.submission_feedback import whole_second_utc

#: The largest archive stored. A pilot of 20-50 Students is far below it;
#: reaching it refuses the export ("narrow the period") rather than writing a
#: row MySQL's packet limit or memory could not carry.
MAX_EXPORT_ARCHIVE_BYTES = 16 * 1024 * 1024

ARCHIVE_CONTENT_TYPE = db.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql")


class ResearchExportArchive(db.Model):
    __tablename__ = "research_export_archives"
    __table_args__ = (
        db.UniqueConstraint("export_id", name="uq_research_export_archives_export_id"),
        db.CheckConstraint(
            "LENGTH(archive_sha256) = 64", name="ck_research_export_archives_digest_format"
        ),
        db.CheckConstraint(
            f"byte_size > 0 AND byte_size <= {MAX_EXPORT_ARCHIVE_BYTES}",
            name="ck_research_export_archives_size_range",
        ),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    export_id = db.Column(ID_TYPE, db.ForeignKey("research_exports.id"), nullable=False)
    archive_sha256 = db.Column(db.String(64), nullable=False)
    byte_size = db.Column(db.Integer, nullable=False)
    content = db.Column(ARCHIVE_CONTENT_TYPE, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(ResearchExportArchive, "before_update")
def _archive_is_never_edited(_mapper, _connection, _target):
    raise ResearchDataError("An export archive is never edited")


@event.listens_for(ResearchExportArchive, "before_delete")
def _archive_is_removed_only_by_retention(_mapper, _connection, _target):
    raise ResearchDataError("An export archive is removed only by the retention purge")
