"""Research control numbering, actual operators, export membership and gaps.

Revision ID: d68e4a1b720f
Revises: b52f9d1a0e83
Source only; no live database or scheduler changes.
"""
import csv
import hashlib
import io
import uuid
import zipfile
from alembic import op
import sqlalchemy as sa

revision = "d68e4a1b720f"
down_revision = "b52f9d1a0e83"
branch_labels = None
depends_on = None
OLD_ACTIONS = ("configuration_created", "configuration_updated", "configuration_activated", "collection_paused", "collection_resumed",
    "subject_excluded", "subject_reinstated", "subject_marked_demo", "export_created", "export_downloaded", "retention_purged")
NEW_ACTIONS = OLD_ACTIONS + ("data_cleanup_previewed", "data_cleaned", "archive_removed", "operator_read")


def _members(content, digest, count):
    if hashlib.sha256(content).hexdigest() != digest:
        raise RuntimeError("Research archive membership preflight refused invalid bytes.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo("sessions.csv")
            if info.file_size > 64 * 1024 * 1024:
                raise ValueError
            reader = csv.DictReader(io.StringIO(archive.read(info).decode("utf-8-sig")))
            values = [row["session_id"] for row in reader]
            if len(values) != count or len(set(values)) != count or any(str(uuid.UUID(value)) != value for value in values):
                raise ValueError
            return values
    except (KeyError, ValueError, UnicodeError, zipfile.BadZipFile) as exc:
        raise RuntimeError("Research archive membership preflight refused inconsistent metadata.") from exc


def _archives(connection):
    last_id = 0
    while True:
        ids = connection.execute(sa.text("SELECT id FROM research_export_archives WHERE id>:last ORDER BY id LIMIT 100"), {"last": last_id}).scalars().all()
        if not ids:
            return
        for archive_id in ids:
            row = connection.execute(sa.text("SELECT a.export_id,a.content,a.archive_sha256,e.sessions_count FROM research_export_archives a JOIN research_exports e ON e.id=a.export_id WHERE a.id=:id"), {"id": archive_id}).one()
            yield row[0], _members(row[1], row[2], row[3])
        last_id = ids[-1]


def upgrade():
    connection = op.get_bind()
    # Validate all existing bytes before DDL. No archive or description is changed.
    for _export_id, _refs in _archives(connection):
        pass
    number = int(connection.execute(sa.text("SELECT COALESCE(MAX(version_number),0) FROM research_configurations")).scalar())
    op.create_table("research_configuration_sequence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False), sa.Column("last_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("id=1 AND last_number>=0", name="ck_research_configuration_sequence_singleton"))
    connection.execute(sa.text("INSERT INTO research_configuration_sequence (id,last_number) VALUES (1,:number)"), {"number": number})
    op.add_column("research_audit_events", sa.Column("service_principal", sa.String(32, collation="utf8mb4_0900_bin"), nullable=True))
    # Old anonymous operator rows remain explicitly unattributed. Never invent
    # an actor for them. All new operator writes require an actual Researcher.
    connection.execute(sa.text("UPDATE research_audit_events SET service_principal='legacy_unattributed' WHERE channel='operator' AND actor_id IS NULL"))
    op.drop_constraint("ck_research_audit_events_action_valid", "research_audit_events", type_="check")
    op.drop_constraint("ck_research_audit_events_channel_valid", "research_audit_events", type_="check")
    op.create_check_constraint("ck_research_audit_events_action_valid", "research_audit_events", "action IN (" + ",".join("'" + value + "'" for value in NEW_ACTIONS) + ")")
    op.create_check_constraint("ck_research_audit_events_channel_valid", "research_audit_events", "channel IN ('workspace','operator','migration','service')")
    op.create_check_constraint("ck_research_audit_events_operator_actor", "research_audit_events", "channel<>'operator' OR actor_id IS NOT NULL OR (service_principal IS NOT NULL AND service_principal='legacy_unattributed')")
    op.create_check_constraint("ck_research_audit_events_service_actor", "research_audit_events", "channel<>'service' OR (actor_id IS NULL AND service_principal IS NOT NULL AND service_principal='daily_retention' AND action='retention_purged')")
    op.create_table("research_export_sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("export_id", sa.BigInteger(), sa.ForeignKey("research_exports.id"), nullable=False),
        sa.Column("session_public_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("export_id", "session_public_id", name="uq_research_export_sessions_member"))
    op.create_index("ix_research_export_sessions_session", "research_export_sessions", ["session_public_id", "export_id"])
    refs_table = sa.table("research_export_sessions", sa.column("export_id"), sa.column("session_public_id"))
    for export_id, refs in _archives(connection):
        for offset in range(0, len(refs), 500):
            connection.execute(refs_table.insert(), [{"export_id": export_id, "session_public_id": public_id} for public_id in refs[offset:offset+500]])
    op.create_table("research_data_gaps",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("service_principal", sa.String(32, collation="utf8mb4_0900_bin"), nullable=True), sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=True), sa.Column("period_to", sa.Date(), nullable=True), sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("cutoff_ms", sa.BigInteger(), nullable=True), sa.Column("selection_digest", sa.String(64), nullable=False),
        *[sa.Column(kind + "_count", sa.Integer(), nullable=False) for kind in ("sessions", "events", "prompts", "archives")],
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("sessions_count>=0 AND events_count>=0 AND prompts_count>=0 AND archives_count>=0", name="ck_research_data_gaps_counts"),
        sa.CheckConstraint("(actor_id IS NOT NULL AND service_principal IS NULL) OR (actor_id IS NULL AND service_principal IS NOT NULL AND service_principal='daily_retention')", name="ck_research_data_gaps_actor"),
        sa.CheckConstraint("LENGTH(selection_digest)=64", name="ck_research_data_gaps_digest"))
    op.create_index("ix_research_data_gaps_created", "research_data_gaps", ["created_at", "id"])


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT id FROM research_data_gaps LIMIT 1")).first() or connection.execute(sa.text("SELECT id FROM research_audit_events WHERE channel='service' OR action IN ('data_cleanup_previewed','data_cleaned','archive_removed','operator_read') LIMIT 1")).first():
        raise RuntimeError("Downgrade refused: preserve research accountability and cleanup history.")
    if connection.execute(sa.text("SELECT id FROM research_export_sessions LIMIT 1")).first():
        raise RuntimeError("Downgrade refused: preserve immutable export membership.")
    op.drop_table("research_data_gaps")
    op.drop_table("research_export_sessions")
    op.drop_table("research_configuration_sequence")
    for name in ("ck_research_audit_events_operator_actor", "ck_research_audit_events_service_actor", "ck_research_audit_events_action_valid", "ck_research_audit_events_channel_valid"):
        op.drop_constraint(name, "research_audit_events", type_="check")
    op.create_check_constraint("ck_research_audit_events_action_valid", "research_audit_events", "action IN (" + ",".join("'" + value + "'" for value in OLD_ACTIONS) + ")")
    op.create_check_constraint("ck_research_audit_events_channel_valid", "research_audit_events", "channel IN ('workspace','operator','migration')")
    op.drop_column("research_audit_events", "service_principal")
