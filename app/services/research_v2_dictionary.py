"""Closed, versioned research-v2 vocabulary and pure event-shape validation.

No Flask, ORM, file/network access or arbitrary payload fields. These versioned
codes match the reviewed v2 schema. Adding a signal requires a new documented
version and reviewed schema/collector integration; never expand an allowlist
from browser-provided data. A valid client report is not a confirmed outcome.
"""

import uuid
from types import MappingProxyType

EVENT_SCHEMA_VERSION = "natural-use-events.v2"
PROTOCOL_VERSION = "natural-use-protocol.v2"
FEATURE_VERSION = "recent-frustration-features.v1"
EXPORT_FORMAT = "natural-use-v2-export.v1"
MONITOR_VERSION = "research-quality.v1"
SOURCE_CLIENT = "client"
SOURCE_SERVER = "server"
MAX_EPOCH_MS = 2**53 - 1
MAX_BATCH_EVENTS = 100
MAX_BATCH_BYTES = 131072
MAX_DROPPED_REPORT = 100000
MAX_INTERVAL_MS = 120000


EVENT_TYPES = (
    "context_enter", "context_leave", "visible_interval", "action_intent",
    "request_start", "request_finish", "workflow_outcome", "revision_count",
    "ordinary_help", "media_interval", "media_action", "observation_gap",
    "measurement_support",
)

CONTEXT_CODES = (
    "dashboard", "course_list", "course", "settings", "activity_list",
    "records", "lesson", "reader", "search", "assignment", "quiz",
    "listening", "speaking", "attendance", "grades", "announcement",
    "calendar", "discussion", "messaging", "notifications", "help",
    "unknown",
)

ACTION_CODES = (
    "navigate", "open", "close", "search", "save", "submit", "answer",
    "complete", "undo_complete", "start", "next", "previous", "retry",
    "upload", "download", "play", "pause", "replay", "seek", "record",
    "send", "help", "revise", "unknown",
)

DETAIL_CODES = (
    "visible", "hidden", "busy", "idle", "playing", "buffering", "paused",
    "ended", "started", "finished", "timeout", "network_unknown",
    "committed", "rejected", "failed", "already_completed", "unknown",
    "changed", "opened", "replay", "playback_error", "background",
    "delivery_gap", "clock_unknown", "phone", "tablet", "desktop",
    "device_unknown", "pointer_supported", "pointer_unsupported",
    "media_supported", "media_unsupported", "visible_supported",
    "visible_unsupported",
)

QUALITY_SCOPES = ("technical", "development", "prospective_monitor")

CAUSE_BITS = MappingProxyType({"interface": 1, "technical": 2, "content": 4, "other": 8, "unsure": 16})

QUALITY_GROUP_FIELDS = (
    "configuration_id", "provenance", "protocol_version", "feature_version",
    "release_ref", "analysis_partition", "scope_code", "schema_version",
    "monitor_version", "aggregate_scope_key",
)

QUALITY_PERIOD_FIELDS = ("period_start_ms", "period_end_ms", "cutoff_ms")

INCIDENT_GROUP_FIELDS = ("scope_code", "schema_version", "monitor_version", "aggregate_scope_key")

SERVER_EVENT_TYPES = ("workflow_outcome",)
CLIENT_EVENT_TYPES = tuple(code for code in EVENT_TYPES if code not in SERVER_EVENT_TYPES)
PARTITION_CODES = ("unassigned", "development", "locked_final_test", "prospective_monitor")
PROMPT_STATUSES = ("reserved", "rendered", "answered", "skipped", "expired", "no_response")
PROMPT_QUALITY_CODES = (
    "not_assessed", "eligible", "coverage_low", "clock_unknown", "clock_uncertain",
    "alignment_invalid", "late_answer", "unsupported",
)
OPPORTUNITY_DECISIONS = ("selected", "not_selected", "ineligible", "deferred")
OPPORTUNITY_REASONS = (
    "random_draw", "coverage_low", "clock_unknown", "clock_uncertain", "cooldown",
    "session_budget", "day_budget", "skip", "ineligible_actor", "collection_stopped",
    "outside_period", "hidden_tab", "timed_activity", "recording", "uploading",
    "blocking_dialog", "unsupported",
)
DELIVERY_RESULTS = (
    "accepted", "duplicate", "partially_accepted", "invalid", "ineligible",
    "version_mismatch", "outside_period", "collection_stopped", "oversized",
)
WORKFLOW_OUTCOME_DETAILS = ("committed", "rejected", "failed", "already_completed", "unknown")

# Optional detail values are still family-specific. These are coarse states,
# never an answer, a route, DOM text, object identifier or uploaded filename.
EVENT_DETAIL_CODES = MappingProxyType({
    "context_enter": ("opened", "started", "visible"),
    "context_leave": ("finished", "ended", "hidden"),
    "visible_interval": ("visible", "busy", "idle", "hidden"),
    "action_intent": ("started",),
    "request_start": ("started",),
    "request_finish": ("finished", "timeout", "network_unknown", "unknown"),
    "workflow_outcome": WORKFLOW_OUTCOME_DETAILS,
    "revision_count": ("changed",),
    "ordinary_help": ("opened",),
    "media_interval": ("playing", "buffering", "paused"),
    "media_action": ("replay", "playback_error", "started", "paused", "ended"),
    "observation_gap": ("background", "delivery_gap", "clock_unknown", "unknown"),
    "measurement_support": (
        "phone", "tablet", "desktop", "device_unknown", "pointer_supported",
        "pointer_unsupported", "media_supported", "media_unsupported",
        "visible_supported", "visible_unsupported",
    ),
})

CLIENT_REQUIRED_KEYS = frozenset({
    "event_uid", "event_type", "page_code", "context_code", "activity_code",
    "page_ref", "tab_ref", "client_sequence", "client_at_ms",
})
CLIENT_EVENT_KEYS = CLIENT_REQUIRED_KEYS | frozenset({
    "action_code", "detail_code", "context_ref", "action_ref", "attempt_ref",
    "count", "interval_started_at_ms",
})


def is_integer(value, minimum=0, maximum=MAX_EPOCH_MS):
    """Boolean JSON values are not numeric measurements."""
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def is_opaque_ref(value):
    """Canonical random UUID form, never accepted as an authorization claim."""
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def validate_client_event(raw):
    """Return a closed-key copy of one valid raw client event, else ``None``.

    Times here remain on the client clock. The ingestion owner validates and
    records the batch clock estimate, computes corrected occurrence/interval
    times and sets server receipt/source/session fields. This helper does not
    invent a correction, treat receipt as occurrence or prove coverage.
    """
    if not isinstance(raw, dict) or set(raw) - CLIENT_EVENT_KEYS or not CLIENT_REQUIRED_KEYS <= set(raw):
        return None
    kind = raw.get("event_type")
    if kind not in CLIENT_EVENT_TYPES:
        return None
    if any(raw.get(key) not in CONTEXT_CODES for key in ("page_code", "context_code", "activity_code")):
        return None
    if any(not is_opaque_ref(raw.get(key)) for key in ("event_uid", "page_ref", "tab_ref")):
        return None
    if not is_integer(raw["client_sequence"], 1) or not is_integer(raw["client_at_ms"]):
        return None
    for key in ("context_ref", "action_ref", "attempt_ref"):
        if raw.get(key) is not None and not is_opaque_ref(raw[key]):
            return None
    action = raw.get("action_code")
    detail = raw.get("detail_code")
    if action is not None and action not in ACTION_CODES:
        return None
    if detail is not None and detail not in EVENT_DETAIL_CODES[kind]:
        return None
    if kind in ("visible_interval", "media_interval", "observation_gap", "measurement_support") and detail is None:
        return None
    if kind in ("action_intent", "request_start", "request_finish"):
        if raw.get("action_ref") is None or action is None:
            return None
    if kind in ("request_start", "request_finish"):
        if raw.get("attempt_ref") is None:
            return None
    elif raw.get("attempt_ref") is not None:
        return None
    count = raw.get("count")
    if kind == "revision_count":
        if not is_integer(count, 1, 1000):
            return None
    elif count is not None:
        return None
    start = raw.get("interval_started_at_ms")
    if kind in ("visible_interval", "media_interval") and start is None:
        return None
    if start is not None:
        if kind not in ("visible_interval", "media_interval", "request_finish"):
            return None
        if not is_integer(start) or not 0 <= raw["client_at_ms"] - start <= MAX_INTERVAL_MS:
            return None
    return dict(raw)

QUALITY_METRICS = (
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
RATING_METRICS = tuple(name for name in QUALITY_METRICS if name.startswith("rating"))
