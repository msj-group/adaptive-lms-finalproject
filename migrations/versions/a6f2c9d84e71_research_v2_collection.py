"""PROPOSAL ONLY: separate research-v2 measurements and aggregate monitoring.

Revision ID: a6f2c9d84e71
Revises: 8e5f3b0d7124
Prepared: 2026-10-28

This owner-authorized local review file deliberately lives OUTSIDE versions/.
It is not discoverable by the application's ordinary Alembic upgrade path.
Creating it is not authority to move, apply, deploy or activate it. Application
models, version dispatch, eligibility, export, retention and monitor services
must be implemented and reviewed before any eventual release. No configuration,
45-day policy, scheduler, notification, paid service or study is activated here.

The upgrade creates empty v2/quality tables, adds a nullable archive expiry and
extends exactly two existing CHECKs. It never copies, updates, deletes, seeds or
reclassifies existing rows, nor changes an existing archive's bytes/description.
Codes below are a frozen proposed vocabulary, not proof of source integration.
Foreign keys have no cascades. Existing configuration policy remains the source
of truth for lookback, minimum coverage, offer/answer deadlines, budgets and
retention; protocols store the additional v2 rules only.

Online execution preflights MySQL, the exact revision, parent shapes and code
constraints before its first DDL. MySQL DDL is not collectively transactional;
any future execution needs quiescent writers and an approved recovery plan.
Offline upgrade SQL is a DDL REVIEW ONLY: it cannot establish those preconditions.
Downgrade is online-only and refuses before DDL if it would discard any new data.
"""

import re

from alembic import op
import sqlalchemy as sa


revision = "a6f2c9d84e71"
down_revision = "8e5f3b0d7124"
branch_labels = None
depends_on = None

_COLLATION = "utf8mb4_0900_bin"
_TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4",
                  "mysql_collate": _COLLATION, "mysql_row_format": "DYNAMIC"}
_SCHEMA_V1 = "natural-use-events.v1"
_SCHEMA_V2 = "natural-use-events.v2"
_FORMAT_V1 = "natural-use-export.v1"
_FORMAT_V2 = "natural-use-v2-export.v1"
_PROTOCOL = "natural-use-protocol.v2"
_FEATURES = "recent-frustration-features.v1"
_MONITOR = "research-quality.v1"
_CONFIG_CHECK = "ck_research_configurations_schema_valid"
_EXPORT_CHECK = "ck_research_exports_format_valid"
_ARCHIVE_EXPIRY_CHECK = "ck_research_export_archives_expiry_ms"
_ARCHIVE_EXPIRY_INDEX = "ix_research_export_archives_expiry_ms"

# Parent first; reverse order is suitable for an empty-only downgrade.
_NEW_TABLES = (
    "research_v2_protocols", "research_v2_sampling_state",
    "research_v2_sampling_opportunities", "research_v2_events",
    "research_v2_prompts", "research_v2_feature_memberships", "research_v2_delivery_receipts",
    "research_v2_evaluation_partitions", "research_quality_runs",
    "research_quality_reports", "research_quality_incidents",
)
_PARENTS = ("users", "research_subjects", "research_configurations",
            "research_sessions", "research_exports", "research_export_archives")
_EVENT_TYPES = (
    "context_enter", "context_leave", "visible_interval", "action_intent",
    "request_start", "request_finish", "workflow_outcome", "revision_count",
    "ordinary_help", "media_interval", "media_action", "observation_gap",
    "measurement_support",
)
_CONTEXTS = (
    "dashboard", "course_list", "course", "settings", "activity_list",
    "records", "lesson", "reader", "search", "assignment", "quiz",
    "listening", "speaking", "attendance", "grades", "announcement",
    "calendar", "discussion", "messaging", "notifications", "help",
    "unknown",
)
_ACTIONS = (
    "navigate", "open", "close", "search", "save", "submit", "answer",
    "complete", "undo_complete", "start", "next", "previous", "retry",
    "upload", "download", "play", "pause", "replay", "seek", "record",
    "send", "help", "revise", "unknown",
)
_DETAILS = (
    "visible", "hidden", "busy", "idle", "playing", "buffering", "paused",
    "ended", "started", "finished", "timeout", "network_unknown",
    "committed", "rejected", "failed", "already_completed", "unknown",
    "changed", "opened", "replay", "playback_error", "background",
    "delivery_gap", "clock_unknown", "phone", "tablet", "desktop",
    "device_unknown", "pointer_supported", "pointer_unsupported",
    "media_supported", "media_unsupported", "visible_supported",
    "visible_unsupported",
)
_SCOPES = ("technical", "development", "prospective_monitor")
# Frozen bit meanings; "other" is a flag only, never a free-text response.
_CAUSE_BITS = {"interface": 1, "technical": 2, "content": 4, "other": 8, "unsure": 16}
_QUALITY_GROUP = (
    "configuration_id", "provenance", "protocol_version", "feature_version",
    "release_ref", "analysis_partition", "scope_code", "schema_version",
    "monitor_version", "aggregate_scope_key",
)
_QUALITY_PERIOD = ("period_start_ms", "period_end_ms", "cutoff_ms")
_INCIDENT_GROUP = ("scope_code", "schema_version", "monitor_version", "aggregate_scope_key")
_METRICS = (
    "eligible_sessions", "observed_sessions", "distinct_subjects", "events_received",
    "events_accepted", "events_duplicate", "events_invalid", "events_late",
    "events_dropped_client", "prompts_reserved", "prompts_rendered",
    "prompts_matured", "prompts_answered_matured", "prompts_unanswered_matured",
    "prompts_pending", "prompts_skipped", "prompts_expired", "prompts_delivery_uncertain",
    "clock_unknown_windows", "clock_uncertain_windows",
    "low_coverage_windows", "phone_sessions", "tablet_sessions", "desktop_sessions",
    "unknown_device_sessions", "retention_overdue_sessions",
    "ratings_total", "rating_participants", "rating_1_count", "rating_2_count",
    "rating_3_count", "rating_4_count", "rating_5_count",
    "rating_1_participants", "rating_2_participants", "rating_3_participants",
    "rating_4_participants", "rating_5_participants",
    "logical_actions", "known_terminal_actions", "unknown_actions",
    "confirmed_actions", "pending_actions", "replay_only_receipts", "unsupported_sessions",
    "ingest_delay_count", "ingest_delay_p50_ms", "ingest_delay_p90_ms", "ingest_delay_p99_ms",
    "outcome_delay_count", "outcome_delay_p50_ms", "outcome_delay_p90_ms",
    "pointer_supported_sessions", "pointer_unsupported_sessions",
    "media_supported_sessions", "media_unsupported_sessions",
    "visibility_supported_sessions", "visibility_unsupported_sessions",
)
_RATING_METRICS = tuple(name for name in _METRICS if name.startswith("rating"))


def _in(column, values):
    return column + " IN (" + ",".join("'" + value + "'" for value in values) + ")"


def _code(name, size, *, nullable=False):
    return sa.Column(name, sa.String(size, collation=_COLLATION), nullable=nullable)


def _ref(name, *, nullable=False):
    # Opaque correlation UUIDs, never content IDs, account IDs or URL fragments.
    return _code(name, 36, nullable=nullable)


def _ms(name, *, nullable=False):
    return sa.Column(name, sa.BigInteger(), nullable=nullable)


def _fk(name, target, *, primary_key=False, nullable=False):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey(target),
                     primary_key=primary_key, nullable=nullable,
                     autoincrement=False if primary_key else "auto")


def _id():
    return sa.Column("id", sa.BigInteger(), primary_key=True)


def _ck(name, expression):
    return sa.CheckConstraint(expression, name=name)


def _quality_group_columns():
    return (
        _fk("configuration_id", "research_configurations.id"),
        _code("provenance", 16), _code("protocol_version", 40),
        _code("feature_version", 48), _code("release_ref", 64),
        _code("analysis_partition", 24), _code("scope_code", 24),
        _code("schema_version", 40), _code("monitor_version", 40),
        _code("aggregate_scope_key", 64),
    )


def _quality_group_checks(prefix):
    digest = "SHA2(CONCAT_WS('|'," + ",".join(_QUALITY_GROUP[:-1]) + "),256)"
    return (
        _ck(prefix + "_versions", "schema_version='" + _SCHEMA_V2
            + "' AND monitor_version='" + _MONITOR + "' AND protocol_version='" + _PROTOCOL
            + "' AND feature_version='" + _FEATURES + "'"),
        _ck(prefix + "_provenance", _in("provenance", ("demo", "development", "study"))),
        _ck(prefix + "_release", "(release_ref='unknown' AND scope_code='technical') OR "
            "(CHAR_LENGTH(release_ref) IN (40,64) AND UNHEX(release_ref) IS NOT NULL "
            "AND release_ref=LOWER(HEX(UNHEX(release_ref))))"),
        _ck(prefix + "_scope", _in("scope_code", _SCOPES) + " AND "
            + _in("analysis_partition", (
                "all", "unassigned", "development", "locked_final_test", "prospective_monitor"))
            + " AND (scope_code='technical' OR "
            "(scope_code='development' AND analysis_partition='development') OR "
            "(scope_code='prospective_monitor' AND analysis_partition='prospective_monitor'))"),
        _ck(prefix + "_scope_key", "CHAR_LENGTH(aggregate_scope_key)=64 AND "
            + digest + " IS NOT NULL AND aggregate_scope_key=" + digest),
    )


def _normalized_check(expression):
    # MySQL reflection can add parentheses, backticks and charset introducers.
    expression = re.sub(r"_[A-Za-z0-9]+(?=')", "", expression)
    # Normalize SQL spelling without lowering literal machine-code values.
    expression = re.sub(r"\bIN\b", "in", expression, flags=re.IGNORECASE)
    return re.sub(r"[\s`()]+", "", expression)


def _assert_check(inspector, table, name, expression):
    checks = {item["name"]: item["sqltext"]
              for item in inspector.get_check_constraints(table)}
    if name not in checks or _normalized_check(checks[name]) != _normalized_check(expression):
        raise RuntimeError("Unexpected parent research CHECK: " + table + "." + name)


def _assert_bigint(columns, table, names):
    for name in names:
        found = columns.get(name)
        if (found is None or not isinstance(found["type"], sa.BigInteger)
                or getattr(found["type"], "unsigned", False) or found["nullable"]):
            raise RuntimeError("Expected non-null signed BIGINT: " + table + "." + name)


def _online_environment(expected_revision):
    if op.get_context().as_sql:
        raise RuntimeError("This research preflight requires an online MySQL connection.")
    connection = op.get_bind()
    if connection.dialect.name != "mysql":
        raise RuntimeError("Research v2 requires MySQL, never a substitute database.")
    version_text = connection.execute(sa.text("SELECT VERSION()")).scalar_one()
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(version_text))
    if (not match or "mariadb" in str(version_text).lower()
            or tuple(map(int, match.groups())) < (8, 0, 17)):
        raise RuntimeError("Research v2 requires MySQL 8.0.17 or newer.")
    pad = connection.execute(sa.text(
        "SELECT PAD_ATTRIBUTE FROM information_schema.COLLATIONS "
        "WHERE COLLATION_NAME=:collation"), {"collation": _COLLATION}).scalar_one_or_none()
    if pad != "NO PAD":
        raise RuntimeError("Research v2 requires utf8mb4_0900_bin NO PAD support.")
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "alembic_version" not in tables or any(table not in tables for table in _PARENTS):
        raise RuntimeError("Research v2 parent tables or revision metadata are missing.")
    revisions = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all()
    if revisions != [expected_revision]:
        raise RuntimeError("Research v2 refuses an unexpected database revision.")
    for table in _PARENTS:
        if inspector.get_table_options(table).get("mysql_engine", "").lower() != "innodb":
            raise RuntimeError("Research v2 requires the reviewed InnoDB parent tables.")
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        _assert_bigint(columns, table, ("id",))
        if inspector.get_pk_constraint(table).get("constrained_columns") != ["id"]:
            raise RuntimeError("Research v2 found an unexpected parent primary key.")
    return connection, inspector, tables


def _upgrade_preflight():
    connection, inspector, tables = _online_environment(down_revision)
    if any(table in tables for table in _NEW_TABLES):
        raise RuntimeError("Research v2 tables already exist; do not repair or overwrite them.")
    archive_columns = {item["name"]: item
                       for item in inspector.get_columns("research_export_archives")}
    if "expiry_at_ms" in archive_columns:
        raise RuntimeError("Research archive expiry already exists; manual review is required.")
    archive_check_names = {item["name"] for item in
                           inspector.get_check_constraints("research_export_archives")}
    archive_index_names = {item["name"] for item in
                           inspector.get_indexes("research_export_archives")}
    if _ARCHIVE_EXPIRY_CHECK in archive_check_names or _ARCHIVE_EXPIRY_INDEX in archive_index_names:
        raise RuntimeError("Research archive expiry constraint or index already exists.")
    for table, names in (
        ("research_sessions", ("subject_id", "configuration_id")),
        ("research_export_archives", ("export_id",)),
    ):
        _assert_bigint({item["name"]: item for item in inspector.get_columns(table)}, table, names)
    for table, column, parent in (
        ("research_sessions", "subject_id", "research_subjects"),
        ("research_sessions", "configuration_id", "research_configurations"),
        ("research_export_archives", "export_id", "research_exports"),
    ):
        matches = [item for item in inspector.get_foreign_keys(table)
                   if item.get("constrained_columns") == [column]
                   and item.get("referred_table") == parent
                   and item.get("referred_columns") == ["id"]]
        if len(matches) != 1 or any(value not in (None, "RESTRICT", "NO ACTION")
                                    for value in matches[0].get("options", {}).values()):
            raise RuntimeError("Unexpected parent research foreign key: " + table + "." + column)
    for table, column, width in (
        ("research_configurations", "event_schema_version", 40),
        ("research_exports", "export_format", 32),
    ):
        row = connection.execute(sa.text(
            "SELECT DATA_TYPE,CHARACTER_MAXIMUM_LENGTH,IS_NULLABLE,COLLATION_NAME "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME=:table AND COLUMN_NAME=:column"),
            {"table": table, "column": column}).one_or_none()
        if row is None or tuple(row) != ("varchar", width, "NO", _COLLATION):
            raise RuntimeError("Unexpected exact-code parent column: " + table + "." + column)
    _assert_check(inspector, "research_configurations", _CONFIG_CHECK,
                  # MySQL stores a singleton IN CHECK as scalar equality.
                  "event_schema_version = '" + _SCHEMA_V1 + "'")
    _assert_check(inspector, "research_exports", _EXPORT_CHECK,
                  "export_format = '" + _FORMAT_V1 + "'")


def _replace_check(table, name, expression):
    # A single ALTER avoids a successful standalone DROP leaving no defense.
    op.execute(sa.text("ALTER TABLE `" + table + "` DROP CHECK `" + name
                       + "`, ADD CONSTRAINT `" + name + "` CHECK (" + expression + ")"))


def upgrade():
    if op.get_context().as_sql:
        op.get_context().impl.static_output(
            "-- REVIEW ONLY: offline SQL skips revision, engine, row and collation preflight."
        )
        op.get_context().impl.static_output(
            "-- Do not execute this output; an approved online migration must run its preflight."
        )
    else:
        _upgrade_preflight()

    op.create_table(
        "research_v2_protocols",
        _fk("configuration_id", "research_configurations.id", primary_key=True),
        _code("protocol_version", 40), _code("feature_version", 48),
        _code("study_timezone", 64),
        sa.Column("first_opportunity_seconds", sa.Integer(), nullable=False),
        sa.Column("opportunity_step_seconds", sa.Integer(), nullable=False),
        sa.Column("minimum_spacing_seconds", sa.Integer(), nullable=False),
        sa.Column("heartbeat_seconds", sa.Integer(), nullable=False),
        sa.Column("maximum_gap_seconds", sa.Integer(), nullable=False),
        sa.Column("maximum_clock_uncertainty_ms", sa.Integer(), nullable=False),
        _ms("created_at_ms"),
        _ck("ck_rv2_protocol_versions", "protocol_version='" + _PROTOCOL
            + "' AND feature_version='" + _FEATURES + "'"),
        _ck("ck_rv2_protocol_timing", "first_opportunity_seconds BETWEEN 30 AND 600 "
            "AND opportunity_step_seconds BETWEEN 15 AND 600 "
            "AND minimum_spacing_seconds BETWEEN 60 AND 86400 "
            "AND heartbeat_seconds BETWEEN 5 AND 60 "
            "AND maximum_gap_seconds BETWEEN 5 AND 120 "
            "AND maximum_gap_seconds>=heartbeat_seconds "
            "AND maximum_clock_uncertainty_ms BETWEEN 0 AND 60000"),
        _ck("ck_rv2_protocol_created", "created_at_ms>=0"),
        _ck("ck_rv2_protocol_timezone", "study_timezone='Africa/Tripoli'"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_v2_sampling_state",
        _fk("session_id", "research_sessions.id", primary_key=True),
        _code("release_ref", 64),
        sa.Column("observed_visible_ms", sa.BigInteger(), nullable=False),
        sa.Column("next_opportunity_slot", sa.Integer(), nullable=False),
        sa.Column("next_due_visible_ms", sa.BigInteger(), nullable=False),
        _ms("last_accounted_at_ms", nullable=True), _ms("skipped_at_ms", nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        _ck("ck_rv2_sampling_counters", "observed_visible_ms>=0 "
            "AND next_opportunity_slot>=1 AND next_due_visible_ms>=0 AND version>=1"),
        _ck("ck_rv2_sampling_times", "(last_accounted_at_ms IS NULL OR last_accounted_at_ms>=0) "
            "AND (skipped_at_ms IS NULL OR skipped_at_ms>=0)"),
        _ck("ck_rv2_sampling_release", "CHAR_LENGTH(release_ref) IN (40,64) "
            "AND UNHEX(release_ref) IS NOT NULL "
            "AND release_ref=LOWER(HEX(UNHEX(release_ref)))"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_v2_sampling_opportunities", _id(),
        _fk("session_id", "research_sessions.id"),
        sa.Column("opportunity_slot", sa.Integer(), nullable=False),
        sa.Column("due_visible_ms", sa.BigInteger(), nullable=False),
        _ms("decided_at_ms"), _code("decision", 24), _code("reason_code", 32),
        sa.Column("selection_permille", sa.Integer(), nullable=True),
        sa.Column("reservation_slot", sa.SmallInteger(), nullable=True),
        _ms("reserved_at_ms", nullable=True),
        sa.UniqueConstraint("session_id", "opportunity_slot", name="uq_rv2_opp_session_slot"),
        sa.UniqueConstraint("session_id", "reservation_slot", name="uq_rv2_opp_reservation_slot"),
        sa.UniqueConstraint("id", "reserved_at_ms", name="uq_rv2_opp_grant"),
        _ck("ck_rv2_opp_counters", "opportunity_slot>=1 AND due_visible_ms>=0 AND decided_at_ms>=0"),
        _ck("ck_rv2_opp_decision", _in("decision", (
            "selected", "not_selected", "ineligible", "deferred"))),
        _ck("ck_rv2_opp_reason", _in("reason_code", (
            "random_draw", "coverage_low", "clock_unknown", "clock_uncertain",
            "cooldown", "session_budget", "day_budget", "skip", "ineligible_actor",
            "collection_stopped", "outside_period", "hidden_tab", "timed_activity",
            "recording", "uploading", "blocking_dialog", "unsupported"))),
        _ck("ck_rv2_opp_probability", "(decision IN ('selected','not_selected') "
            "AND reason_code='random_draw' AND selection_permille IS NOT NULL "
            "AND selection_permille BETWEEN 0 AND 1000) OR "
            "(decision IN ('ineligible','deferred') AND reason_code<>'random_draw' "
            "AND selection_permille IS NULL)"),
        _ck("ck_rv2_opp_draw_possible", "(decision<>'selected' OR selection_permille>0) "
            "AND (decision<>'not_selected' OR selection_permille<1000)"),
        _ck("ck_rv2_opp_reservation", "(reservation_slot IS NULL AND reserved_at_ms IS NULL) OR "
            "(decision='selected' AND reservation_slot IS NOT NULL "
            "AND reservation_slot BETWEEN 1 AND 3 AND reserved_at_ms IS NOT NULL "
            "AND reserved_at_ms>=decided_at_ms)"),
        sa.Index("ix_rv2_opp_decided", "decided_at_ms", "session_id"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_v2_events", _id(), _ref("event_uid"),
        _fk("session_id", "research_sessions.id"),
        _code("source", 8), _code("event_type", 24),
        _code("page_code", 24), _code("context_code", 24),
        _code("activity_code", 24), _code("action_code", 24, nullable=True),
        _code("detail_code", 32, nullable=True),
        _ref("context_ref", nullable=True), _ref("action_ref", nullable=True),
        _ref("attempt_ref", nullable=True), _ref("outcome_ref", nullable=True),
        sa.Column("count", sa.Integer(), nullable=True),
        sa.Column("confirmed_progress", sa.Boolean(), nullable=False),
        _ms("interval_started_at_ms", nullable=True),
        _ms("occurred_at_ms"), _ms("received_at_ms"),
        _ref("page_ref", nullable=True), _ref("tab_ref", nullable=True),
        sa.Column("client_sequence", sa.BigInteger(), nullable=True),
        _ms("client_at_ms", nullable=True),
        sa.Column("clock_offset_ms", sa.BigInteger(), nullable=True),
        sa.Column("clock_uncertainty_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint("event_uid", name="uq_rv2_event_uid"),
        sa.UniqueConstraint("session_id", "outcome_ref", name="uq_rv2_event_outcome"),
        _ck("ck_rv2_event_uid_length", "CHAR_LENGTH(event_uid)=36"),
        _ck("ck_rv2_event_type", _in("event_type", _EVENT_TYPES)),
        _ck("ck_rv2_event_codes", _in("page_code", _CONTEXTS) + " AND "
            + _in("context_code", _CONTEXTS) + " AND " + _in("activity_code", _CONTEXTS)
            + " AND (action_code IS NULL OR " + _in("action_code", _ACTIONS) + ")"
            + " AND (detail_code IS NULL OR " + _in("detail_code", _DETAILS) + ")"),
        _ck("ck_rv2_event_source", "(source='server' AND event_type='workflow_outcome' "
            "AND outcome_ref IS NOT NULL AND page_ref IS NULL AND tab_ref IS NULL "
            "AND client_sequence IS NULL AND client_at_ms IS NULL "
            "AND clock_offset_ms IS NULL AND clock_uncertainty_ms IS NULL) OR "
            "(source='client' AND event_type<>'workflow_outcome' AND outcome_ref IS NULL "
            "AND page_ref IS NOT NULL AND tab_ref IS NOT NULL "
            "AND client_sequence IS NOT NULL AND client_sequence>=1 "
            "AND client_at_ms IS NOT NULL AND client_at_ms>=0)"),
        _ck("ck_rv2_event_clock", "(clock_offset_ms IS NULL AND clock_uncertainty_ms IS NULL) "
            "OR (clock_offset_ms IS NOT NULL AND clock_uncertainty_ms IS NOT NULL "
            "AND clock_uncertainty_ms BETWEEN 0 AND 86400000 "
            "AND clock_offset_ms BETWEEN -86400000 AND 86400000)"),
        _ck("ck_rv2_event_interval", "occurred_at_ms>=0 AND received_at_ms>=0 "
            "AND ((interval_started_at_ms IS NULL "
            "AND event_type NOT IN ('visible_interval','media_interval')) OR "
            "(interval_started_at_ms IS NOT NULL AND interval_started_at_ms>=0 "
            "AND event_type IN ('visible_interval','media_interval','request_finish') "
            "AND occurred_at_ms>=interval_started_at_ms "
            "AND occurred_at_ms-interval_started_at_ms<=120000))"),
        _ck("ck_rv2_event_count", "(count IS NULL AND event_type<>'revision_count') "
            "OR (count IS NOT NULL AND count BETWEEN 1 AND 1000)"),
        _ck("ck_rv2_event_action", "(event_type IN ('action_intent','request_start','request_finish') "
            "AND action_ref IS NOT NULL AND action_code IS NOT NULL) OR "
            "event_type NOT IN ('action_intent','request_start','request_finish')"),
        _ck("ck_rv2_event_attempt", "(event_type IN ('request_start','request_finish') "
            "AND attempt_ref IS NOT NULL) OR event_type='workflow_outcome' OR (event_type NOT IN ('request_start','request_finish') "
            "AND attempt_ref IS NULL)"),
        _ck("ck_rv2_event_progress", "confirmed_progress IN (0,1) AND "
            "(confirmed_progress=0 OR (source='server' AND event_type='workflow_outcome' "
            "AND detail_code IS NOT NULL AND detail_code='committed'))"),
        _ck("ck_rv2_event_outcome_detail", "event_type<>'workflow_outcome' OR "
            "(detail_code IS NOT NULL AND detail_code IN "
            "('committed','rejected','failed','already_completed','unknown'))"),
        sa.Index("ix_rv2_event_session_time", "session_id", "occurred_at_ms", "id"),
        sa.Index("ix_rv2_event_received", "received_at_ms", "id"),
        sa.Index("ix_rv2_event_action", "session_id", "action_ref", "occurred_at_ms"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_v2_prompts", _id(), _ref("public_id"),
        sa.Column("opportunity_id", sa.BigInteger(), nullable=False),
        _code("status", 16), _ms("granted_at_ms"), _ms("expires_at_ms"),
        _ms("rendered_at_ms", nullable=True), _ms("render_received_at_ms", nullable=True),
        _ms("reference_start_ms", nullable=True), _ms("reference_end_ms", nullable=True),
        sa.Column("render_clock_uncertainty_ms", sa.Integer(), nullable=True),
        _code("display_locale", 8, nullable=True),
        sa.Column("help_question_shown", sa.Boolean(), nullable=True),
        _ms("response_deadline_ms", nullable=True),
        _ms("response_received_at_ms", nullable=True), _ms("terminal_at_ms", nullable=True),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("cause_mask", sa.SmallInteger(), nullable=True),
        sa.Column("help_requested", sa.Boolean(), nullable=True),
        sa.Column("answer_timely", sa.Boolean(), nullable=True),
        _code("quality_code", 24),
        sa.UniqueConstraint("public_id", name="uq_rv2_prompt_public"),
        sa.UniqueConstraint("opportunity_id", name="uq_rv2_prompt_opportunity"),
        sa.ForeignKeyConstraint(["opportunity_id", "granted_at_ms"],
                                ["research_v2_sampling_opportunities.id",
                                 "research_v2_sampling_opportunities.reserved_at_ms"],
                                name="fk_rv2_prompt_reserved_opportunity"),
        _ck("ck_rv2_prompt_public_length", "CHAR_LENGTH(public_id)=36"),
        _ck("ck_rv2_prompt_status", _in("status", (
            "reserved", "rendered", "answered", "skipped", "expired", "no_response"))),
        _ck("ck_rv2_prompt_grant", "granted_at_ms>=0 AND expires_at_ms>granted_at_ms"),
        _ck("ck_rv2_prompt_render", "(rendered_at_ms IS NULL "
            "AND render_received_at_ms IS NULL AND reference_start_ms IS NULL "
            "AND reference_end_ms IS NULL AND render_clock_uncertainty_ms IS NULL "
            "AND display_locale IS NULL AND help_question_shown IS NULL "
            "AND response_deadline_ms IS NULL) OR "
            "(rendered_at_ms IS NOT NULL AND rendered_at_ms>=0 "
            "AND render_received_at_ms IS NOT NULL AND render_received_at_ms>=granted_at_ms "
            "AND reference_start_ms IS NOT NULL AND reference_start_ms>=0 "
            "AND reference_end_ms IS NOT NULL AND reference_end_ms=granted_at_ms "
            "AND display_locale IS NOT NULL AND display_locale IN ('en','ar') "
            "AND help_question_shown IS NOT NULL AND help_question_shown IN (0,1) "
            "AND reference_end_ms-reference_start_ms BETWEEN 30000 AND 600000 "
            "AND response_deadline_ms IS NOT NULL "
            "AND response_deadline_ms-rendered_at_ms BETWEEN 60000 AND 3600000 "
            "AND (render_clock_uncertainty_ms IS NULL OR "
            "render_clock_uncertainty_ms BETWEEN 0 AND 86400000))"),
        _ck("ck_rv2_prompt_lifecycle", "(status='reserved' AND rendered_at_ms IS NULL "
            "AND terminal_at_ms IS NULL AND response_received_at_ms IS NULL) OR "
            "(status='rendered' AND rendered_at_ms IS NOT NULL AND terminal_at_ms IS NULL "
            "AND response_received_at_ms IS NULL) OR (status IN ('answered','skipped') "
            "AND rendered_at_ms IS NOT NULL AND response_received_at_ms IS NOT NULL "
            "AND response_received_at_ms>=render_received_at_ms AND terminal_at_ms IS NOT NULL "
            "AND terminal_at_ms=response_received_at_ms) OR (status='expired' "
            "AND rendered_at_ms IS NULL AND response_received_at_ms IS NULL "
            "AND terminal_at_ms IS NOT NULL AND terminal_at_ms>=expires_at_ms) OR "
            "(status='no_response' AND rendered_at_ms IS NOT NULL "
            "AND response_received_at_ms IS NULL AND terminal_at_ms IS NOT NULL "
            "AND terminal_at_ms>=render_received_at_ms AND terminal_at_ms>=response_deadline_ms)"),
        _ck("ck_rv2_prompt_answer", "(status='answered' AND rating IS NOT NULL "
            "AND rating BETWEEN 1 AND 5 AND answer_timely IS NOT NULL "
            "AND answer_timely IN (0,1)) OR (status<>'answered' AND rating IS NULL "
            "AND cause_mask IS NULL AND help_requested IS NULL AND answer_timely IS NULL)"),
        _ck("ck_rv2_prompt_causes", "(cause_mask IS NULL OR cause_mask BETWEEN 0 AND 31) "
            "AND (help_requested IS NULL OR help_requested IN (0,1))"),
        _ck("ck_rv2_prompt_help_exposure", "help_requested IS NULL OR "
            "(help_question_shown IS NOT NULL AND help_question_shown=1)"),
        _ck("ck_rv2_prompt_timely", "status<>'answered' OR "
            "(answer_timely=1 AND response_received_at_ms<=response_deadline_ms) OR "
            "(answer_timely=0 AND response_received_at_ms>response_deadline_ms "
            "AND quality_code='late_answer')"),
        _ck("ck_rv2_prompt_quality", _in("quality_code", (
            "not_assessed", "eligible", "coverage_low", "clock_unknown",
            "clock_uncertain", "alignment_invalid", "late_answer", "unsupported"))
            + " AND (quality_code<>'eligible' OR (status='answered' AND answer_timely=1 "
            "AND render_clock_uncertainty_ms IS NOT NULL))"),
        sa.Index("ix_rv2_prompt_granted", "granted_at_ms", "id"),
        sa.Index("ix_rv2_prompt_rendered", "rendered_at_ms", "id"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_v2_feature_memberships",
        _fk("prompt_id", "research_v2_prompts.id", primary_key=True),
        _fk("event_id", "research_v2_events.id", primary_key=True),
        sa.Index("ix_rv2_membership_event", "event_id"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_v2_delivery_receipts", _id(), _ref("receipt_uid"),
        _fk("session_id", "research_sessions.id", nullable=True),
        _ref("batch_ref", nullable=True), _ref("attempt_ref", nullable=True),
        _code("scope_code", 16), _code("result_code", 24), _ms("received_at_ms"),
        *[sa.Column(name, sa.Integer(), nullable=False) for name in (
            "events_received", "events_accepted", "events_duplicate", "events_invalid",
            "events_late", "events_dropped_client")],
        sa.UniqueConstraint("receipt_uid", name="uq_rv2_delivery_uid"),
        sa.UniqueConstraint("session_id", "batch_ref", "attempt_ref", name="uq_rv2_delivery_attempt"),
        _ck("ck_rv2_delivery_uid_length", "CHAR_LENGTH(receipt_uid)=36"),
        _ck("ck_rv2_delivery_scope", "(scope_code='session' AND session_id IS NOT NULL "
            "AND batch_ref IS NOT NULL AND attempt_ref IS NOT NULL) OR "
            "(scope_code='unresolved' AND session_id IS NULL AND events_accepted=0)"),
        _ck("ck_rv2_delivery_result", _in("result_code", (
            "accepted", "duplicate", "partially_accepted", "invalid", "ineligible",
            "version_mismatch", "outside_period", "collection_stopped", "oversized"))),
        _ck("ck_rv2_delivery_counts", "received_at_ms>=0 AND " + " AND ".join(
            name + " BETWEEN 0 AND 100000" for name in (
                "events_received", "events_accepted", "events_duplicate", "events_invalid",
                "events_late", "events_dropped_client"))
            + " AND events_received=events_accepted+events_duplicate+events_invalid+events_late"),
        sa.Index("ix_rv2_delivery_received", "received_at_ms", "id"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_v2_evaluation_partitions",
        _fk("subject_id", "research_subjects.id", primary_key=True),
        _code("partition_code", 24),
        _fk("assigned_by_id", "users.id", nullable=True),
        _ms("assigned_at_ms", nullable=True), _ms("locked_at_ms", nullable=True),
        _ms("development_exposed_at_ms", nullable=True),
        _code("assignment_reason", 24),
        sa.Column("version", sa.Integer(), nullable=False),
        _ck("ck_rv2_partition_code", _in("partition_code", (
            "unassigned", "development", "locked_final_test", "prospective_monitor"))),
        _ck("ck_rv2_partition_assignment", "(partition_code='unassigned' "
            "AND assigned_by_id IS NULL AND assigned_at_ms IS NULL AND locked_at_ms IS NULL) "
            "OR (partition_code<>'unassigned' AND assigned_by_id IS NOT NULL "
            "AND assigned_at_ms IS NOT NULL AND assigned_at_ms>=0 "
            "AND ((partition_code='locked_final_test' AND locked_at_ms IS NOT NULL "
            "AND locked_at_ms>=assigned_at_ms AND development_exposed_at_ms IS NULL) "
            "OR (partition_code<>'locked_final_test' AND locked_at_ms IS NULL)))"),
        _ck("ck_rv2_partition_reason", _in("assignment_reason", (
            "pending", "predeclared_split", "development_pilot", "prospective_review"))
            + " AND (partition_code<>'unassigned' OR assignment_reason='pending') "
            "AND (partition_code='unassigned' OR assignment_reason<>'pending')"),
        _ck("ck_rv2_partition_version", "version>=1 AND "
            "(development_exposed_at_ms IS NULL OR development_exposed_at_ms>=0)"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_quality_runs", _id(), _code("run_key", 64),
        *_quality_group_columns(),
        _ms("period_start_ms"), _ms("period_end_ms"), _ms("cutoff_ms"),
        _ms("started_at_ms"), _ms("finished_at_ms", nullable=True),
        _code("status", 16), _code("error_code", 32, nullable=True), _ms("expiry_at_ms"),
        sa.UniqueConstraint("run_key", name="uq_rquality_run_key"),
        sa.UniqueConstraint("id", *_QUALITY_GROUP, *_QUALITY_PERIOD, name="uq_rquality_run_group"),
        sa.UniqueConstraint("id", *_INCIDENT_GROUP, name="uq_rquality_run_scope"),
        _ck("ck_rquality_run_key_length", "CHAR_LENGTH(run_key)=64"),
        *_quality_group_checks("ck_rquality_run"),
        _ck("ck_rquality_run_times", "period_start_ms>=0 AND period_end_ms>period_start_ms "
            "AND cutoff_ms>=period_end_ms AND started_at_ms>=cutoff_ms "
            "AND expiry_at_ms>started_at_ms"),
        _ck("ck_rquality_run_lifecycle", "(status='running' AND finished_at_ms IS NULL "
            "AND error_code IS NULL) OR (status='succeeded' AND finished_at_ms IS NOT NULL "
            "AND finished_at_ms>=started_at_ms AND error_code IS NULL) OR "
            "(status='failed' AND finished_at_ms IS NOT NULL AND finished_at_ms>=started_at_ms "
            "AND error_code IS NOT NULL)"),
        _ck("ck_rquality_run_error", "error_code IS NULL OR " + _in("error_code", (
            "database_unavailable", "schema_mismatch", "query_timeout", "lease_unavailable",
            "invalid_aggregate", "report_store_failed", "internal_error"))),
        sa.Index("ix_rquality_run_started", "started_at_ms", "id"),
        sa.Index("ix_rquality_run_expiry", "expiry_at_ms"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_quality_reports", _id(),
        sa.Column("run_id", sa.BigInteger(), nullable=False), *_quality_group_columns(),
        _ms("period_start_ms"), _ms("period_end_ms"), _ms("cutoff_ms"),
        _ms("created_at_ms"), _ms("expiry_at_ms"),
        # A closed numeric body: NULL means unavailable, never silently zero.
        *[sa.Column(name, sa.BigInteger(), nullable=True) for name in _METRICS],
        sa.ForeignKeyConstraint(["run_id", *_QUALITY_GROUP, *_QUALITY_PERIOD],
                                ["research_quality_runs." + name
                                 for name in ("id", *_QUALITY_GROUP, *_QUALITY_PERIOD)],
                                name="fk_rquality_report_run_group"),
        sa.UniqueConstraint("id", *_INCIDENT_GROUP, name="uq_rquality_report_scope"),
        sa.UniqueConstraint(*_QUALITY_GROUP,
                            "period_start_ms", "period_end_ms", name="uq_rquality_report_period"),
        *_quality_group_checks("ck_rquality_report"),
        _ck("ck_rquality_report_times", "period_start_ms>=0 AND period_end_ms>period_start_ms "
            "AND cutoff_ms>=period_end_ms AND created_at_ms>=cutoff_ms AND expiry_at_ms>created_at_ms"),
        _ck("ck_rquality_report_counts", " AND ".join(
            "(" + name + " IS NULL OR " + name + ">=0)" for name in _METRICS)),
        _ck("ck_rquality_report_matured", "(prompts_matured IS NULL "
            "AND prompts_answered_matured IS NULL AND prompts_unanswered_matured IS NULL) OR "
            "(prompts_matured IS NOT NULL AND prompts_answered_matured IS NOT NULL "
            "AND prompts_unanswered_matured IS NOT NULL "
            "AND prompts_matured=prompts_answered_matured+prompts_unanswered_matured)"),
        _ck("ck_rquality_report_ratings", "(" + " AND ".join(
            name + " IS NULL" for name in _RATING_METRICS) + ") OR (ratings_total IS NOT NULL "
            "AND rating_participants IS NOT NULL AND rating_participants<=ratings_total "
            "AND rating_1_count IS NOT NULL AND rating_2_count IS NOT NULL "
            "AND rating_3_count IS NOT NULL AND rating_4_count IS NOT NULL "
            "AND rating_5_count IS NOT NULL "
            "AND ratings_total=rating_1_count+rating_2_count+rating_3_count+rating_4_count+rating_5_count "
            "AND " + " AND ".join("rating_" + str(i) + "_participants IS NOT NULL AND rating_"
                                    + str(i) + "_participants<=rating_" + str(i) + "_count"
                                    for i in range(1, 6)) + ")"),
        _ck("ck_rquality_report_label_quarantine", "scope_code<>'technical' OR ("
            + " AND ".join(name + " IS NULL" for name in _RATING_METRICS) + ")"),
        _ck("ck_rquality_report_quantiles", "(ingest_delay_p50_ms IS NULL OR ingest_delay_p90_ms IS NULL "
            "OR ingest_delay_p90_ms>=ingest_delay_p50_ms) AND (ingest_delay_p90_ms IS NULL "
            "OR ingest_delay_p99_ms IS NULL OR ingest_delay_p99_ms>=ingest_delay_p90_ms) "
            "AND (outcome_delay_p50_ms IS NULL OR outcome_delay_p90_ms IS NULL "
            "OR outcome_delay_p90_ms>=outcome_delay_p50_ms)"),
        sa.Index("ix_rquality_report_expiry", "expiry_at_ms"),
        **_TABLE_OPTIONS,
    )
    op.create_table(
        "research_quality_incidents", _id(),
        _code("rule_code", 32), _code("scope_code", 24), _code("aggregate_scope_key", 64),
        _code("schema_version", 40), _code("monitor_version", 40),
        sa.Column("first_run_id", sa.BigInteger(), nullable=False),
        sa.Column("last_run_id", sa.BigInteger(), nullable=False),
        sa.Column("latest_report_id", sa.BigInteger(), nullable=True),
        _ms("first_seen_at_ms"), _ms("last_seen_at_ms"),
        _code("status", 16), _code("severity", 16), _ms("recovered_at_ms", nullable=True),
        _code("notification_channel", 16), _code("notification_state", 16),
        sa.Column("notification_attempt_count", sa.Integer(), nullable=False),
        _ms("notification_attempted_at_ms", nullable=True),
        _ms("notification_confirmed_at_ms", nullable=True),
        _code("notification_error_code", 24, nullable=True), _ms("expiry_at_ms"),
        sa.UniqueConstraint("rule_code", "aggregate_scope_key", "scope_code", "schema_version", "monitor_version",
                            name="uq_rquality_incident_rule"),
        sa.ForeignKeyConstraint(["first_run_id", *_INCIDENT_GROUP],
                                ["research_quality_runs." + name for name in ("id", *_INCIDENT_GROUP)],
                                name="fk_rquality_incident_first_scope"),
        sa.ForeignKeyConstraint(["last_run_id", *_INCIDENT_GROUP],
                                ["research_quality_runs." + name for name in ("id", *_INCIDENT_GROUP)],
                                name="fk_rquality_incident_last_scope"),
        sa.ForeignKeyConstraint(["latest_report_id", *_INCIDENT_GROUP],
                                ["research_quality_reports." + name for name in ("id", *_INCIDENT_GROUP)],
                                name="fk_rquality_incident_report_scope"),
        _ck("ck_rquality_incident_scope_key", "CHAR_LENGTH(aggregate_scope_key)=64 "
            "AND UNHEX(aggregate_scope_key) IS NOT NULL "
            "AND aggregate_scope_key=LOWER(HEX(UNHEX(aggregate_scope_key)))"),
        _ck("ck_rquality_incident_rule", _in("rule_code", (
            "delivery_stopped", "invalid_rate", "clock_alignment", "coverage_gap",
            "prompt_delivery", "response_rate", "expiry_backlog", "checker_failure", "device_gap"))),
        _ck("ck_rquality_incident_scope", _in("scope_code", _SCOPES)),
        _ck("ck_rquality_incident_versions", "schema_version='" + _SCHEMA_V2
            + "' AND monitor_version='" + _MONITOR + "'"),
        _ck("ck_rquality_incident_severity", _in("severity", ("info", "warning", "critical"))),
        _ck("ck_rquality_incident_times", "first_seen_at_ms>=0 AND last_seen_at_ms>=first_seen_at_ms "
            "AND expiry_at_ms>last_seen_at_ms AND ((status='open' AND recovered_at_ms IS NULL) "
            "OR (status='recovered' AND recovered_at_ms IS NOT NULL "
            "AND recovered_at_ms>=first_seen_at_ms AND recovered_at_ms<=last_seen_at_ms))"),
        _ck("ck_rquality_incident_delivery", "notification_channel='telegram' "
            "AND notification_attempt_count>=0 AND ((notification_state='none' "
            "AND notification_attempt_count=0 AND notification_attempted_at_ms IS NULL "
            "AND notification_confirmed_at_ms IS NULL AND notification_error_code IS NULL) OR "
            "(notification_state='pending' AND notification_attempt_count>=1 "
            "AND notification_attempted_at_ms IS NOT NULL AND notification_attempted_at_ms>=first_seen_at_ms "
            "AND notification_confirmed_at_ms IS NULL AND notification_error_code IS NULL) OR "
            "(notification_state='sent' AND notification_attempt_count>=1 "
            "AND notification_attempted_at_ms IS NOT NULL AND notification_attempted_at_ms>=first_seen_at_ms "
            "AND notification_confirmed_at_ms IS NOT NULL "
            "AND notification_confirmed_at_ms>=notification_attempted_at_ms AND notification_error_code IS NULL) OR "
            "(notification_state='failed' AND notification_attempt_count>=1 "
            "AND notification_attempted_at_ms IS NOT NULL AND notification_attempted_at_ms>=first_seen_at_ms "
            "AND notification_confirmed_at_ms IS NULL AND notification_error_code IS NOT NULL))"),
        _ck("ck_rquality_incident_delivery_error", "notification_error_code IS NULL OR "
            + _in("notification_error_code", (
                "timeout", "rate_limited", "unauthorized", "unavailable", "invalid_destination", "unknown"))),
        sa.Index("ix_rquality_incident_expiry", "expiry_at_ms"),
        **_TABLE_OPTIONS,
    )
    # NULL has an explicit legacy meaning. Do not fabricate an expiry or alter
    # archive bytes/metadata; future retention code must support mixed policies.
    # Keep the column, its range defense and its future expiry lookup index
    # together in one MySQL ALTER; existing archives receive only NULL.
    op.execute(sa.text(
        "ALTER TABLE `research_export_archives` ADD COLUMN `expiry_at_ms` BIGINT NULL, "
        "ADD CONSTRAINT `" + _ARCHIVE_EXPIRY_CHECK + "` "
        "CHECK (expiry_at_ms IS NULL OR expiry_at_ms>0), "
        "ADD INDEX `" + _ARCHIVE_EXPIRY_INDEX + "` (`expiry_at_ms`)"
    ))
    _replace_check("research_configurations", _CONFIG_CHECK,
                   _in("event_schema_version", (_SCHEMA_V1, _SCHEMA_V2)))
    _replace_check("research_exports", _EXPORT_CHECK,
                   _in("export_format", (_FORMAT_V1, _FORMAT_V2)))


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Research v2 downgrade requires online empty-data preflight; offline is refused.")
    connection, inspector, tables = _online_environment(revision)
    if any(table not in tables for table in _NEW_TABLES):
        raise RuntimeError("Research v2 downgrade found an incomplete schema; manual recovery is required.")
    _assert_check(inspector, "research_configurations", _CONFIG_CHECK,
                  _in("event_schema_version", (_SCHEMA_V1, _SCHEMA_V2)))
    _assert_check(inspector, "research_exports", _EXPORT_CHECK,
                  _in("export_format", (_FORMAT_V1, _FORMAT_V2)))
    # Complete every preservation check before the first ALTER or DROP.
    for table in _NEW_TABLES:
        if connection.execute(sa.text("SELECT 1 FROM `" + table + "` LIMIT 1")).first():
            raise RuntimeError("Research v2 downgrade refuses to discard rows in " + table + ".")
    if connection.execute(sa.text(
            "SELECT 1 FROM research_configurations WHERE event_schema_version=:version LIMIT 1"),
            {"version": _SCHEMA_V2}).first():
        raise RuntimeError("Research v2 downgrade preserves versioned configuration history.")
    if connection.execute(sa.text(
            "SELECT 1 FROM research_exports WHERE export_format=:format "
            "OR event_schema_version=:version LIMIT 1"),
            {"format": _FORMAT_V2, "version": _SCHEMA_V2}).first():
        raise RuntimeError("Research v2 downgrade preserves immutable export history.")
    archive_columns = {item["name"]: item
                       for item in inspector.get_columns("research_export_archives")}
    if "expiry_at_ms" not in archive_columns:
        raise RuntimeError("Research v2 downgrade found an unexpected archive schema.")
    expiry_column = archive_columns["expiry_at_ms"]
    if (not isinstance(expiry_column["type"], sa.BigInteger)
            or getattr(expiry_column["type"], "unsigned", False) or not expiry_column["nullable"]):
        raise RuntimeError("Research v2 downgrade found an unexpected archive expiry type.")
    _assert_check(inspector, "research_export_archives", _ARCHIVE_EXPIRY_CHECK,
                  "expiry_at_ms IS NULL OR expiry_at_ms>0")
    archive_indexes = {item["name"]: item for item in
                       inspector.get_indexes("research_export_archives")}
    if archive_indexes.get(_ARCHIVE_EXPIRY_INDEX, {}).get("column_names") != ["expiry_at_ms"]:
        raise RuntimeError("Research v2 downgrade found an unexpected archive expiry index.")
    if connection.execute(sa.text(
            "SELECT 1 FROM research_export_archives WHERE expiry_at_ms IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Research v2 downgrade refuses to discard recorded archive expiry.")
    _replace_check("research_configurations", _CONFIG_CHECK,
                   _in("event_schema_version", (_SCHEMA_V1,)))
    _replace_check("research_exports", _EXPORT_CHECK, "export_format='" + _FORMAT_V1 + "'")
    op.execute(sa.text(
        "ALTER TABLE `research_export_archives` DROP CHECK `" + _ARCHIVE_EXPIRY_CHECK
        + "`, DROP INDEX `" + _ARCHIVE_EXPIRY_INDEX + "`, DROP COLUMN `expiry_at_ms`"
    ))
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)
