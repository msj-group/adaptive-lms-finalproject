"""Constrain research data to Study without relabelling existing records.

Revision ID: e7f9012abc34
Revises: d6e8f9012abc
"""
from alembic import op
import sqlalchemy as sa

revision = "e7f9012abc34"
down_revision = "d6e8f9012abc"
branch_labels = None
depends_on = None

CONSTRAINTS = (
    ("research_subjects", "ck_research_subjects_provenance_valid"),
    ("research_sessions", "ck_research_sessions_provenance_valid"),
    ("research_quality_runs", "ck_rquality_run_provenance"),
    ("research_quality_reports", "ck_rquality_report_provenance"),
)
AUDIT_ACTIONS = (
    "configuration_created", "configuration_updated", "configuration_activated",
    "collection_paused", "collection_resumed", "subject_excluded", "subject_reinstated",
    "export_created", "export_downloaded", "retention_purged", "data_cleanup_previewed",
    "data_cleaned", "archive_removed", "operator_read",
)


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "mysql":
        raise RuntimeError("Study-only research migration requires MySQL.")
    # Validate the entire population before the first MySQL DDL implicit commit.
    # Existing non-Study evidence is never deleted or relabelled by this migration.
    for table, _constraint in CONSTRAINTS:
        present = connection.execute(sa.text(
            f"SELECT 1 FROM `{table}` WHERE provenance <> 'study' LIMIT 1"
        )).first()
        if present is not None:
            raise RuntimeError("Existing non-Study research data requires a separate preservation decision.")
    if connection.execute(sa.text(
        "SELECT 1 FROM research_audit_events WHERE action='export_created' "
        "AND detail_code='export_development' LIMIT 1"
    )).first() is not None:
        raise RuntimeError("Existing non-Study exports require a separate preservation decision.")
    if connection.execute(sa.text(
        "SELECT 1 FROM research_audit_events WHERE action='subject_marked_demo' LIMIT 1"
    )).first() is not None:
        raise RuntimeError("Existing demonstration audit history requires a separate preservation decision.")
    for table, constraint in CONSTRAINTS:
        # One atomic ALTER per table preserves its original CHECK name.
        op.execute(sa.text(
            f"ALTER TABLE `{table}` DROP CHECK `{constraint}`, "
            f"ADD CONSTRAINT `{constraint}` CHECK (provenance IN ('study'))"
        ))
    actions = ",".join("'" + action + "'" for action in AUDIT_ACTIONS)
    op.execute(sa.text(
        "ALTER TABLE research_audit_events DROP CHECK ck_research_audit_events_action_valid, "
        "ADD CONSTRAINT ck_research_audit_events_action_valid CHECK (action IN (" + actions + "))"
    ))


def downgrade():
    raise RuntimeError("Preserve Study-only provenance; use a reviewed source rollback.")
