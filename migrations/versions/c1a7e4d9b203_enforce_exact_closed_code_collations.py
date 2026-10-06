"""Enforce exact closed-code spellings under MySQL's default CI collation.

Revision ID: c1a7e4d9b203
Revises: d574ab56594f

No value is rewritten. All row/type checks precede DDL. MySQL DDL commits
per table, so an eventual live run requires quiescent application writers.
The grouped ALTER retains all existing CHECK names/expressions, FKs, indexes,
widths and nullability. Name/title/email collations are not changed.
"""

from collections import defaultdict
import re

from alembic import op
import sqlalchemy as sa

revision = 'c1a7e4d9b203'
down_revision = 'd574ab56594f'
branch_labels = None
depends_on = None

_CODE_COLLATION = "utf8mb4_0900_bin"
# Frozen parent schema and legal values; never import evolving application models.
_COLUMNS = (
    ("research_subjects", "collection_status", 16, False, ("excluded", "included")),
    ("research_subjects", "status_basis", 32, False, ("external_exclusion", "legacy_collection_exclusion", "operator_reinstatement", "population_rule")),
    ("research_subjects", "provenance", 16, False, ("demo", "study")),
    ("calendar_events", "status", 32, False, ("cancelled", "scheduled")),
    ("fee_plans", "currency_code", 3, False, ("LYD",)),
    ("fee_plans", "status", 32, False, ("active", "archived", "draft")),
    ("notifications", "kind", 48, False, ("announcement_published", "discussion_topic_created", "enrollment_activated", "enrollment_withdrawn", "lesson_published", "material_available", "message_received", "schedule_changed", "teacher_assignment_activated", "teacher_assignment_removed")),
    ("research_configurations", "event_schema_version", 40, False, ("natural-use-events.v1",)),
    ("research_configurations", "status", 16, False, ("active", "draft", "retired")),
    ("uploaded_files", "category", 32, False, ("audio", "document", "image", "video")),
    ("fee_plan_items", "kind", 32, False, ("course", "registration")),
    ("fee_plan_items", "status", 32, False, ("active", "removed")),
    ("file_access_logs", "action", 16, False, ("download", "inline", "upload")),
    ("research_exports", "export_format", 32, False, ("natural-use-export.v1",)),
    ("research_sessions", "provenance", 16, False, ("demo", "development", "study")),
    ("research_sessions", "end_reason", 32, True, ("collection_stopped", "configuration_changed", "inactivity", "logout", "subject_ineligible")),
    ("announcements", "scope", 32, False, ("center", "course", "group")),
    ("announcements", "status", 32, False, ("draft", "published", "withdrawn")),
    ("assignments", "status", 32, False, ("draft", "published")),
    ("discussion_topics", "status", 16, False, ("locked", "open")),
    ("quizzes", "status", 32, False, ("draft", "published")),
    ("research_audit_events", "action", 32, False, ("collection_paused", "collection_resumed", "configuration_activated", "configuration_created", "configuration_updated", "export_created", "export_downloaded", "retention_purged", "subject_excluded", "subject_marked_demo", "subject_reinstated")),
    ("research_audit_events", "channel", 16, False, ("migration", "operator", "workspace")),
    ("research_events", "source", 8, False, ("client", "server")),
    ("research_events", "event_type", 24, False, ("assignment_submission", "control_click", "discussion_reply", "form_invalid", "form_submit", "heartbeat", "input_change", "lesson_completion", "listening_answer", "listening_start", "listening_submission", "media_event", "message_send", "non_interactive_click", "page_leave", "page_view", "quiz_answer", "quiz_start", "quiz_submission", "recorder_failure", "recorder_state", "repeated_click", "search", "speaking_submission", "visibility_hidden", "visibility_visible")),
    ("research_feedback_prompts", "status", 16, False, ("answered", "dismissed", "displayed", "offered")),
    ("research_feedback_prompts", "sampling_reason", 16, False, ("activity_end", "random")),
    ("research_feedback_prompts", "last_deferral_reason", 24, True, ("hidden_tab", "recording", "timed_activity", "uploading")),
    ("lessons", "status", 32, False, ("draft", "published")),
    ("listening_activities", "transcript_visibility", 32, False, ("after_submission", "always", "hidden")),
    ("quiz_attempts", "status", 32, False, ("expired", "in_progress", "submitted")),
    ("quiz_questions", "answer_mode", 32, False, ("multiple", "single")),
    ("student_fee_assignments", "status", 32, False, ("assigned", "cancelled")),
    ("attendance_records", "status", 32, False, ("absent", "excused", "late", "present")),
    ("grade_items", "source_kind", 32, False, ("activity", "assignment", "manual", "quiz", "speaking")),
    ("invoices", "currency_code", 3, False, ("LYD",)),
    ("invoices", "status", 32, False, ("cancelled", "draft", "issued")),
    ("materials", "kind", 32, False, ("external_link", "file", "rich_text")),
    ("materials", "status", 32, False, ("active", "archived")),
    ("invoice_items", "kind", 32, False, ("course", "registration")),
    ("invoice_items", "status", 32, False, ("active", "removed")),
    ("payment_intents", "provider", 16, False, ("mock",)),
    ("payment_intents", "status", 32, False, ("cancelled", "confirmed", "pending", "provider_failed", "provider_succeeded")),
    ("payment_intents", "currency_code", 3, False, ("LYD",)),
    ("payment_transactions", "kind", 32, False, ("collection", "reversal")),
    ("payment_transactions", "method", 32, False, ("bank_transfer", "cash", "online")),
    ("payment_transactions", "status", 32, False, ("confirmed", "pending", "rejected")),
    ("payment_transactions", "currency_code", 3, False, ("LYD",)),
    ("payment_provider_events", "provider", 16, False, ("mock",)),
    ("payment_provider_events", "event_type", 32, False, ("payment.failed", "payment.succeeded")),
    ("payment_provider_events", "currency_code", 3, False, ("LYD",)),
    ("payment_provider_events", "outcome", 32, False, ("confirmed", "duplicate", "failed", "ignored_terminal", "reconciliation_required")),
    ("receipts", "status", 32, False, ("issued", "voided")),
    ("payment_audit_events", "kind", 40, False, ("invoice_cancelled", "invoice_deleted", "invoice_draft_created", "invoice_draft_edited", "invoice_issued", "invoice_issued_edited", "payment_bank_transfer_confirmed", "payment_bank_transfer_recorded", "payment_bank_transfer_rejected", "payment_cash_recorded", "payment_deleted", "payment_online_confirmed", "payment_replaced", "payment_reversed", "receipt_deleted", "receipt_issued", "receipt_online_issued", "receipt_voided")),
)


def _plans(upgrading):
    if op.get_context().as_sql:
        raise RuntimeError("Exact-code collation migration requires live schema and row checks.")
    connection = op.get_bind()
    if connection.dialect.name != "mysql":
        raise RuntimeError("Exact-code collation migration requires MySQL.")
    pad_attribute = connection.execute(sa.text(
        "SELECT PAD_ATTRIBUTE FROM information_schema.COLLATIONS WHERE COLLATION_NAME = :collation"
    ), {"collation": _CODE_COLLATION}).scalar_one_or_none()
    if pad_attribute != "NO PAD":
        raise RuntimeError("Exact-code migration requires MySQL with utf8mb4_0900_bin NO PAD support.")
    schema = sa.inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    grouped = defaultdict(list)
    for table, column, width, nullable, values in _COLUMNS:
        grouped[table].append((column, width, nullable, values))
    plans = []
    for table, targets in sorted(grouped.items()):
        default_collation = schema.get_table_options(table)["mysql_collate"]
        if not re.fullmatch(r"utf8mb4_[a-z0-9_]+", default_collation):
            raise RuntimeError("Exact-code migration requires the reviewed utf8mb4 table schema.")
        columns = {column["name"]: column for column in schema.get_columns(table)}
        for column, width, nullable, values in targets:
            observed = columns.get(column)
            if (observed is None or not isinstance(observed["type"], sa.String)
                    or observed["type"].length != width or observed["nullable"] != nullable):
                raise RuntimeError(f"Unexpected parent column shape: {table}.{column}.")
            if upgrading:
                invalid = connection.execute(sa.text(
                    f"SELECT COUNT(*) FROM {quote(table)} WHERE {quote(column)} IS NOT NULL"
                    f" AND CAST({quote(column)} AS BINARY) NOT IN :values"
                ).bindparams(sa.bindparam("values", expanding=True)), {"values": values}).scalar_one()
                if invalid:
                    raise RuntimeError(
                        f"Refusing exact-code migration: {invalid} noncanonical row(s) in {table}.{column}."
                    )
        checks = schema.get_check_constraints(table)
        if any(not check["name"] or not check["sqltext"] for check in checks):
            raise RuntimeError("Existing named CHECK constraints must be preserved.")
        target_collation = _CODE_COLLATION if upgrading else default_collation
        changes = [f"DROP CHECK {quote(check['name'])}" for check in checks]
        changes.extend(
            f"MODIFY COLUMN {quote(column)} VARCHAR({width}) CHARACTER SET utf8mb4"
            f" COLLATE {target_collation} {'NULL' if nullable else 'NOT NULL'}"
            for column, width, nullable, _values in targets
        )
        changes.extend(
            f"ADD CONSTRAINT {quote(check['name'])} CHECK ({check['sqltext']})"
            for check in checks
        )
        plans.append(f"ALTER TABLE {quote(table)} " + ", ".join(changes))
    return connection, plans


def upgrade():
    connection, plans = _plans(True)
    for statement in plans:
        connection.exec_driver_sql(statement)


def downgrade():
    connection, plans = _plans(False)
    for statement in plans:
        connection.exec_driver_sql(statement)
