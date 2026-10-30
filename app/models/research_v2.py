"""Versioned v2 measurements and bounded aggregate quality records.

The schema mirrors the inactive reviewed research-v2 proposal; importing these
models performs no DDL and is not authority to activate the protocol. Closed
CHECK expressions remain literal/versioned, independently of migration code.
All IDs use the shared signed BIGINT type. There are no relationship cascades.

ORM guards protect ordinary writes. Deliberate retention uses its explicitly
scoped bulk deletion in FK order, not ORM mutation of recorded history; bulk
SQL callers remain responsible for this authorization boundary.
"""

import sqlalchemy as sa
from sqlalchemy import event, inspect

from app.extensions import db
from app.models.research_common import ID_TYPE, ResearchDataError, changed_columns



_COLLATION = "utf8mb4_0900_bin"


_TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4",
                  "mysql_collate": _COLLATION, "mysql_row_format": "DYNAMIC"}


_SCHEMA_V2 = "natural-use-events.v2"


_PROTOCOL = "natural-use-protocol.v2"


_FEATURES = "recent-frustration-features.v1"


_MONITOR = "research-quality.v1"


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
    return sa.Column(name, ID_TYPE, sa.ForeignKey(target),
                     primary_key=primary_key, nullable=nullable,
                     autoincrement=False if primary_key else "auto")


def _id():
    return sa.Column("id", ID_TYPE, primary_key=True)


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


class ResearchV2Protocol(db.Model):
    __table__ = db.Table(
        "research_v2_protocols", db.metadata,
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


class ResearchV2SamplingState(db.Model):
    __table__ = db.Table(
        "research_v2_sampling_state", db.metadata,
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


class ResearchV2SamplingOpportunity(db.Model):
    __table__ = db.Table(
        "research_v2_sampling_opportunities", db.metadata, _id(),
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


class ResearchV2Event(db.Model):
    __table__ = db.Table(
        "research_v2_events", db.metadata, _id(), _ref("event_uid"),
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


class ResearchV2Prompt(db.Model):
    __table__ = db.Table(
        "research_v2_prompts", db.metadata, _id(), _ref("public_id"),
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


class ResearchV2FeatureMembership(db.Model):
    """Exact committed observations visible to a prompt's decision snapshot."""

    __table__ = db.Table(
        "research_v2_feature_memberships", db.metadata,
        _fk("prompt_id", "research_v2_prompts.id", primary_key=True),
        _fk("event_id", "research_v2_events.id", primary_key=True),
        sa.Index("ix_rv2_membership_event", "event_id"),
        **_TABLE_OPTIONS,
    )


class ResearchV2DeliveryReceipt(db.Model):
    __table__ = db.Table(
        "research_v2_delivery_receipts", db.metadata, _id(), _ref("receipt_uid"),
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


class ResearchV2EvaluationPartition(db.Model):
    __table__ = db.Table(
        "research_v2_evaluation_partitions", db.metadata,
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


class ResearchQualityRun(db.Model):
    __table__ = db.Table(
        "research_quality_runs", db.metadata, _id(), _code("run_key", 64),
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


class ResearchQualityReport(db.Model):
    __table__ = db.Table(
        "research_quality_reports", db.metadata, _id(),
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


class ResearchQualityIncident(db.Model):
    __table__ = db.Table(
        "research_quality_incidents", db.metadata, _id(),
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


def _changed(target):
    state = inspect(target)
    return set(changed_columns(state, frozenset(a.key for a in state.mapper.column_attrs)))


def _stored(connection, target):
    table = target.__table__
    predicate = sa.and_(*(column == getattr(target, column.name)
                          for column in table.primary_key.columns))
    return connection.execute(sa.select(table).where(predicate)).mappings().one()


@event.listens_for(ResearchV2Protocol, "before_insert")
def _protocol_requires_draft_configuration(_mapper, connection, target):
    configuration = connection.execute(sa.text(
        "SELECT status,event_schema_version FROM research_configurations "
        "WHERE id=:configuration_id"
    ), {"configuration_id": target.configuration_id}).mappings().one_or_none()
    if (configuration is None or configuration["status"] != "draft"
            or configuration["event_schema_version"] != _SCHEMA_V2):
        raise ResearchDataError("A v2 protocol must be attached to its matching draft configuration")


@event.listens_for(ResearchV2Protocol, "before_update")
def _activated_protocol_is_frozen(_mapper, connection, target):
    changed = _changed(target)
    if changed & {"configuration_id", "protocol_version", "feature_version", "created_at_ms"}:
        raise ResearchDataError("A protocol's identity and version never change")
    status = connection.execute(sa.text(
        "SELECT status FROM research_configurations WHERE id=:configuration_id"
    ), {"configuration_id": target.configuration_id}).scalar_one_or_none()
    if changed and status != "draft":
        raise ResearchDataError("An activated protocol is frozen")


@event.listens_for(ResearchV2SamplingOpportunity, "before_update")
def _opportunity_decision_is_final(_mapper, connection, target):
    changed = _changed(target)
    if not changed:
        return
    if changed - {"reservation_slot", "reserved_at_ms"}:
        raise ResearchDataError("A sampling opportunity's decision never changes")
    stored = _stored(connection, target)
    if (stored["reserved_at_ms"] is not None or stored["reservation_slot"] is not None
            or stored["decision"] != "selected" or target.reservation_slot is None
            or target.reserved_at_ms is None):
        raise ResearchDataError("A selected opportunity can be reserved only once")


@event.listens_for(ResearchV2SamplingState, "before_update")
def _session_release_is_immutable(_mapper, _connection, target):
    if _changed(target) & {"session_id", "release_ref"}:
        raise ResearchDataError("A sampling session's identity and recorded application release never change")


def _record_is_append_only(_mapper, _connection, target):
    if _changed(target):
        raise ResearchDataError("A recorded research observation or aggregate is never edited")


for _append_only_model in (ResearchV2Event, ResearchV2FeatureMembership, ResearchV2DeliveryReceipt, ResearchQualityReport):
    event.listen(_append_only_model, "before_update", _record_is_append_only)


_PROMPT_IDENTITY = frozenset({
    "id", "public_id", "opportunity_id", "granted_at_ms", "expires_at_ms",
})
_PROMPT_RENDER = frozenset({
    "rendered_at_ms", "render_received_at_ms", "reference_start_ms", "reference_end_ms",
    "render_clock_uncertainty_ms", "display_locale", "help_question_shown", "response_deadline_ms",
})


@event.listens_for(ResearchV2Prompt, "before_update")
def _prompt_history_is_final(_mapper, connection, target):
    changed = _changed(target)
    if not changed:
        return
    if changed & _PROMPT_IDENTITY:
        raise ResearchDataError("A feedback reservation's identity never changes")
    stored = _stored(connection, target)
    status = stored["status"]
    if status in ("answered", "skipped", "expired"):
        raise ResearchDataError("An answered, skipped or expired feedback prompt is final")
    if stored["rendered_at_ms"] is not None and changed & _PROMPT_RENDER:
        raise ResearchDataError("A confirmed feedback render and reference window never change")
    allowed = {
        "reserved": {"reserved", "rendered", "expired"},
        "rendered": {"rendered", "answered", "skipped", "no_response"},
        "no_response": {"answered"},
    }
    if target.status not in allowed.get(status, set()):
        raise ResearchDataError("Invalid feedback prompt transition")
    if status == "no_response" and target.answer_timely is not False:
        raise ResearchDataError("An answer received after no-response classification must remain late")


@event.listens_for(ResearchV2EvaluationPartition, "before_update")
def _evaluation_exposure_is_not_erased(_mapper, connection, target):
    changed = _changed(target)
    if not changed:
        return
    if "subject_id" in changed:
        raise ResearchDataError("An evaluation partition's subject never changes")
    stored = _stored(connection, target)
    if stored["partition_code"] == "locked_final_test":
        raise ResearchDataError("A reserved final-test participant is frozen")
    if (stored["development_exposed_at_ms"] is not None
            and target.development_exposed_at_ms != stored["development_exposed_at_ms"]):
        raise ResearchDataError("Prior development exposure cannot be erased or rewritten")
    if target.partition_code == "locked_final_test" and stored["development_exposed_at_ms"] is not None:
        raise ResearchDataError("A development-exposed participant cannot enter the final test")


@event.listens_for(ResearchQualityRun, "before_update")
def _quality_run_scope_and_terminal_receipt_are_final(_mapper, connection, target):
    changed = _changed(target)
    if not changed:
        return
    if changed - {"status", "finished_at_ms", "error_code"}:
        raise ResearchDataError("A quality run's scope, period and expiry never change")
    stored = _stored(connection, target)
    if stored["status"] != "running" or target.status not in ("succeeded", "failed"):
        raise ResearchDataError("A quality run can finish only once")


def _ordinary_delete_is_refused(_mapper, _connection, _target):
    raise ResearchDataError("Research records can be removed only by the authorized retention service")


for _retained_model in (
    ResearchV2Protocol, ResearchV2SamplingState, ResearchV2SamplingOpportunity,
    ResearchV2Event, ResearchV2Prompt, ResearchV2FeatureMembership, ResearchV2DeliveryReceipt,
    ResearchV2EvaluationPartition, ResearchQualityRun, ResearchQualityReport,
    ResearchQualityIncident,
):
    event.listen(_retained_model, "before_delete", _ordinary_delete_is_refused)
