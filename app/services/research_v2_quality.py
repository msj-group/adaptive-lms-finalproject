"""Bounded research-quality SELECTs; this module never writes or locks data.

collect_quality_snapshot accepts a SQLAlchemy Connection already inside a
REPEATABLE READ, READ ONLY consistent snapshot. The dedicated runner owns that
transaction. A web reader can instead own its short snapshot explicitly.
No LMS account link, users table, survey free text or v1 counters are queried.
"""
from collections import defaultdict
from hashlib import sha256
import re

from sqlalchemy import text

if __package__ == "app.services":
    from app.services.research_v2_features import (
        build_features, linear_quantile, render_alignment_code, RENDER_TIMING_SEMANTICS,
    )
else:
    # Dedicated monitor loads these two source modules directly. Importing the
    # Flask package would load deployment dotenv/LMS writer configuration.
    import importlib.util
    from pathlib import Path
    _spec = importlib.util.spec_from_file_location(
        "research_quality_features", Path(__file__).with_name("research_v2_features.py"))
    _features = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_features)
    build_features, linear_quantile = _features.build_features, _features.linear_quantile
    render_alignment_code = _features.render_alignment_code
    RENDER_TIMING_SEMANTICS = _features.RENDER_TIMING_SEMANTICS


MONITOR_VERSION = "research-quality.v1"
# Source semantics are explicit without changing the installed CHECK identity.
DENOMINATOR_SEMANTICS = "server-presence-client-delivery.v1"
LABEL_YIELD_SEMANTICS = "stored-primary-label-yield.v1"
# Provisional investigation minimum, never a model-readiness acceptance rule.
LABEL_YIELD_MINIMUM_MATURED = 5
_LABEL_EXCLUSION_REASONS = (
    "not_assessed", "coverage_low", "clock_unknown", "clock_uncertain",
    "alignment_invalid", "late_answer", "unsupported",
)
SCHEMA_VERSION = "natural-use-events.v2"
PROTOCOL_VERSION = "natural-use-protocol.v2"
FEATURE_VERSION = "recent-frustration-features.v1"
MAX_PERIOD_MS = 86_400_000
# Engineering investigation margins, not research observation or label rules.
CLIENT_START_GRACE_MS = 180_000
# A daily job can leave almost 24 h of normal backlog; add 2 h scheduling margin.
RETENTION_JOB_GRACE_MS = 26 * 3_600_000
SCOPES = frozenset({"technical", "development", "prospective_monitor"})
METRIC_NAMES = (
    "eligible_sessions", "observed_sessions", "distinct_subjects", "events_received",
    "events_accepted", "events_duplicate", "events_invalid", "events_late",
    "events_dropped_client", "prompts_reserved", "prompts_rendered",
    "prompts_matured", "prompts_answered_matured", "prompts_unanswered_matured",
    "prompts_pending", "prompts_skipped", "prompts_expired", "prompts_delivery_uncertain",
    "clock_unknown_windows", "clock_uncertain_windows", "low_coverage_windows",
    "phone_sessions", "tablet_sessions", "desktop_sessions", "unknown_device_sessions",
    "retention_overdue_sessions", "ratings_total", "rating_participants",
    *["rating_" + str(i) + suffix for i in range(1, 6) for suffix in ("_count", "_participants")],
    "logical_actions", "known_terminal_actions", "unknown_actions", "confirmed_actions",
    "pending_actions", "replay_only_receipts", "unsupported_sessions",
    "ingest_delay_count", "ingest_delay_p50_ms", "ingest_delay_p90_ms", "ingest_delay_p99_ms",
    "outcome_delay_count", "outcome_delay_p50_ms", "outcome_delay_p90_ms",
    "pointer_supported_sessions", "pointer_unsupported_sessions", "media_supported_sessions",
    "media_unsupported_sessions", "visibility_supported_sessions", "visibility_unsupported_sessions",
)
_RATING_NAMES = frozenset(n for n in METRIC_NAMES if n.startswith("rating"))


class QualitySnapshotError(ValueError):
    """Closed safe code only; never a database driver message."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _rows(connection, sql, params, limit):
    # All SQL templates are local constants. User choices are bound parameters.
    # MAX_EXECUTION_TIME is SELECT-only MySQL 8; no session setting is mutated.
    result = connection.execute(text(sql + " LIMIT :row_limit"),
                                dict(params, row_limit=limit + 1)).mappings().all()
    if len(result) > limit:
        raise QualitySnapshotError("query_limit_exceeded")
    return result


def aggregate_scope_key(group):
    names = ("configuration_id", "provenance", "protocol_version", "feature_version",
             "release_ref", "analysis_partition", "scope_code", "schema_version", "monitor_version")
    return sha256("|".join(str(group[n]) for n in names).encode("utf-8")).hexdigest()


def _label_yield_evidence():
    # Separate JSON evidence: fixed SQL report columns and identities stay put.
    return {
        "semantics": LABEL_YIELD_SEMANTICS,
        "cohort": "rendered_in_period_and_response_deadline_matured_at_cutoff",
        "minimum_matured": LABEL_YIELD_MINIMUM_MATURED,
        "matured_rendered": 0, "pending_rendered": 0, "raw_answers": 0,
        "timely_answers": 0, "late_answers": 0, "primary_eligible_answers": 0,
        "excluded_answers": 0, "skipped": 0, "unanswered": 0,
        "exclusion_reasons": dict.fromkeys(_LABEL_EXCLUSION_REASONS, 0),
        "primary_eligible_fraction": None, "assessment": "insufficient_evidence",
    }


def collect_quality_snapshot(connection, *, period_start_ms, period_end_ms, cutoff_ms,
                             scope_code="technical", configuration_id=None,
                             max_groups=64, max_rows=50_000):
    """Return homogeneous aggregates, without exposing per-participant rows.

    Technical scope never SELECTs the rating column. Scientific scopes select
    only their explicitly assigned development/prospective participants; final
    test and unassigned labels never enter the query. Immutable release metadata
    is read from the session's v2 sampling-state row, never the current server.

    Source-row and group limits fail the entire snapshot, never return a
    misleading partial denominator. Large studies must use shorter periods or
    a separately reviewed aggregate query implementation before raising limits.
    """
    if (scope_code not in SCOPES or type(period_start_ms) is not int
            or type(period_end_ms) is not int or type(cutoff_ms) is not int
            or not 0 <= period_start_ms < period_end_ms <= cutoff_ms
            or period_end_ms - period_start_ms > MAX_PERIOD_MS
            or not 1 <= max_groups <= 128 or not 1 <= max_rows <= 100_000
            or (configuration_id is not None
                and (type(configuration_id) is not int or configuration_id < 1))):
        raise QualitySnapshotError("invalid_scope")
    if connection.dialect.name != "mysql":
        raise QualitySnapshotError("mysql_required")
    if not connection.in_transaction():
        raise QualitySnapshotError("snapshot_required")
    params = {"start": period_start_ms, "end": period_end_ms, "cutoff": cutoff_ms,
              "event_start": max(0, period_start_ms - 120_000), "schema": SCHEMA_VERSION,
              "presence_start": max(0, period_start_ms - CLIENT_START_GRACE_MS),
              "presence_end": max(0, period_end_ms - CLIENT_START_GRACE_MS),
              "retention_overdue_cutoff": cutoff_ms - RETENTION_JOB_GRACE_MS}
    filters = "c.event_schema_version=:schema"
    if configuration_id is not None:
        filters += " AND c.id=:configuration_id"
        params["configuration_id"] = configuration_id
    if scope_code != "technical":
        filters += " AND ep.partition_code=:partition"
        params["partition"] = scope_code
    global_delivery = None
    if scope_code == "technical" and configuration_id is None:
        global_delivery = {k: int(v) for k, v in _rows(connection,
            "SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) AS attempts,"
            "COALESCE(SUM(scope_code='session' AND events_accepted>0),0) AS accepted_client_attempts,"
            "COALESCE(SUM(scope_code='unresolved' AND result_code IN ('invalid','oversized','version_mismatch')),0) AS rejected_attempts "
            "FROM research_v2_delivery_receipts WHERE received_at_ms>=:start AND received_at_ms<:end",
            params, 1)[0].items()}
    joins = (" FROM research_sessions s JOIN research_configurations c ON c.id=s.configuration_id "
             "JOIN research_v2_protocols p ON p.configuration_id=c.id "
             "JOIN research_v2_sampling_state s2 ON s2.session_id=s.id "
             "LEFT JOIN research_v2_evaluation_partitions ep ON ep.subject_id=s.subject_id ")
    if global_delivery is not None:
        # This independent server-known cohort includes a session even if the
        # collector never sends a batch. Assign it by start + grace, so a page
        # prepared near an hour boundary is assessed in the following hour.
        # Accepted server workflow outcomes cannot prove client delivery.
        presence = _rows(connection, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ s.id,"
            "EXISTS(SELECT 1 FROM research_v2_events ce WHERE ce.session_id=s.id"
            " AND ce.source='client' AND ce.received_at_ms<:cutoff) AS client_observed"
            + joins + "WHERE " + filters
            + " AND s.started_at_ms>=:presence_start AND s.started_at_ms<:presence_end"
            " ORDER BY s.id", params, max_rows)
        observed = sum(int(row["client_observed"]) for row in presence)
        global_delivery.update(server_sessions_matured=len(presence),
            client_observed_matured_sessions=observed,
            missing_client_sessions=len(presence) - observed,
            client_start_grace_ms=CLIENT_START_GRACE_MS,
            presence_cohort="server_known_session_start_plus_grace",
            recovery_evidence="clean_matured_cohort" if presence and observed == len(presence)
                              else "missing_client_observations" if presence else "unavailable")
    configs = _rows(connection, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
                   "c.id AS configuration_id,c.status,c.is_collecting,c.retention_days,"
                   "c.collection_starts_at,c.collection_ends_at,p.protocol_version,p.feature_version "
                   "FROM research_configurations c JOIN research_v2_protocols p "
                   "ON p.configuration_id=c.id WHERE c.event_schema_version=:schema"
                   + (" AND c.id=:configuration_id" if configuration_id is not None else "")
                   + " ORDER BY c.id", params, max_groups)
    sessions = _rows(connection, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
                     "s.id,s.subject_id,s.configuration_id,s.provenance,s.started_at_ms,s.last_seen_at_ms,"
                     "COALESCE(ep.partition_code,'unassigned') AS analysis_partition,"
                     "p.protocol_version,p.feature_version,c.retention_days,s2.release_ref"
                     + joins + "WHERE " + filters
                     + " AND s.started_at_ms<:end AND s.last_seen_at_ms>=:start ORDER BY s.id",
                     params, max_rows)
    # Deliberately independent of the hourly last_seen filter: old expired
    # groups must remain visible even when there are no recent sessions.
    expired = _rows(connection, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
        "s.id,s.subject_id,s.configuration_id,s.provenance,s.last_seen_at_ms,"
        "COALESCE(ep.partition_code,'unassigned') AS analysis_partition,"
        "p.protocol_version,p.feature_version,c.retention_days,s2.release_ref,"
        "(s.last_seen_at_ms+c.retention_days*86400000<=:retention_overdue_cutoff) AS prolonged_overdue"
        + joins + "WHERE " + filters
        + " AND s.last_seen_at_ms+c.retention_days*86400000<=:cutoff ORDER BY s.id",
        params, max_rows)
    global_retention = None
    if scope_code == "technical" and configuration_id is None:
        global_retention = {"due_sessions": len(expired),
                            "prolonged_overdue_sessions": sum(int(r["prolonged_overdue"]) for r in expired),
                            "daily_job_grace_ms": RETENTION_JOB_GRACE_MS,
                            "evidence": "recorded_configuration_retention_all_v2_sessions",
                            "expiry_job_success": "unavailable_requires_independent_heartbeat"}
    session_map, grouped = {}, {}
    by_session = {}

    def register_group(row):
        if row["protocol_version"] != PROTOCOL_VERSION or row["feature_version"] != FEATURE_VERSION:
            raise QualitySnapshotError("version_mismatch")
        release = row["release_ref"]
        if release != "unknown" and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", release):
            raise QualitySnapshotError("release_unknown")
        if scope_code != "technical" and release == "unknown":
            raise QualitySnapshotError("release_unknown")
        key = (row["configuration_id"], row["provenance"], row["protocol_version"],
               row["feature_version"], release, row["analysis_partition"])
        if key not in grouped:
            if len(grouped) >= max_groups:
                raise QualitySnapshotError("query_limit_exceeded")
            group = dict(zip(("configuration_id", "provenance", "protocol_version", "feature_version",
                              "release_ref", "analysis_partition"), key))
            group.update(scope_code=scope_code, schema_version=SCHEMA_VERSION, monitor_version=MONITOR_VERSION)
            group["aggregate_scope_key"] = aggregate_scope_key(group)
            metrics = dict.fromkeys(METRIC_NAMES, 0)
            for name in ("ingest_delay_p50_ms", "ingest_delay_p90_ms", "ingest_delay_p99_ms",
                         "outcome_delay_p50_ms", "outcome_delay_p90_ms"):
                metrics[name] = None
            if scope_code == "technical":
                for name in _RATING_NAMES:
                    metrics[name] = None
            grouped[key] = {"scope": group, "metrics": metrics,
                            "evidence": {"eligible_usage": "server_known_session_activity_cohort",
                                         "client_observation": "accepted_client_events_only",
                                         "label_yield": _label_yield_evidence(),
                                         "retention_due_sessions": 0,
                                         "retention_grace_ms": RETENTION_JOB_GRACE_MS,
                                         "release": "unrecorded" if release == "unknown" else "session_metadata"}}
            by_session[key] = {"observed": set(), "subjects": set(), "delays": [], "outcome_delays": [],
                               "ratings": defaultdict(set), "rated_subjects": set()}
        return key

    for row in sessions:
        key = register_group(row)
        grouped[key]["metrics"]["eligible_sessions"] += 1
        session_map[row["id"]] = (row, key)
    for row in expired:
        key = register_group(row)
        grouped[key]["evidence"]["retention_due_sessions"] += 1
        grouped[key]["metrics"]["retention_overdue_sessions"] += int(row["prolonged_overdue"])
    if not sessions:
        return _report(period_start_ms, period_end_ms, cutoff_ms, scope_code,
                       list(grouped.values()), configs, global_delivery, global_retention)
    # Repeat the same server-scoped join/filter in each query rather than a
    # user-supplied IN string. Every row is also checked against session_map.
    event_rows = _rows(connection, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
                      "e.session_id,e.event_uid,e.source,e.event_type,e.page_code,e.context_code,"
                      "e.activity_code,e.action_code,e.detail_code,e.context_ref,e.action_ref,"
                      "e.attempt_ref,e.outcome_ref,e.count,e.confirmed_progress,e.interval_started_at_ms,"
                      "e.occurred_at_ms,e.received_at_ms,e.clock_uncertainty_ms"
                      + joins + "JOIN research_v2_events e ON e.session_id=s.id WHERE " + filters
                      + " AND s.started_at_ms<:end AND s.last_seen_at_ms>=:start"
                      " AND e.received_at_ms<:cutoff AND (e.occurred_at_ms>=:event_start"
                      " OR e.received_at_ms>=:start) ORDER BY e.session_id,e.occurred_at_ms,e.id",
                      params, max_rows)
    receipt_rows = _rows(connection, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
                        "r.session_id,r.events_received,r.events_accepted,r.events_duplicate,"
                        "r.events_invalid,r.events_late,r.events_dropped_client,r.result_code"
                        + joins + "JOIN research_v2_delivery_receipts r ON r.session_id=s.id WHERE " + filters
                        + " AND s.started_at_ms<:end AND s.last_seen_at_ms>=:start"
                        " AND r.received_at_ms>=:start AND r.received_at_ms<:end"
                        " ORDER BY r.id", params, max_rows)
    prompt_columns = ("pr.id AS prompt_id,q.session_id,pr.status,pr.granted_at_ms,pr.rendered_at_ms,pr.render_received_at_ms,"
                      "pr.expires_at_ms,pr.response_deadline_ms,pr.response_received_at_ms,"
                      "pr.render_clock_uncertainty_ms,pr.answer_timely,pr.quality_code")
    if scope_code != "technical":
        prompt_columns += ",pr.rating"
    prompt_rows = _rows(connection, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ " + prompt_columns + joins
                       + "JOIN research_v2_sampling_opportunities q ON q.session_id=s.id "
                       "JOIN research_v2_prompts pr ON pr.opportunity_id=q.id WHERE " + filters
                       + " AND s.started_at_ms<:end AND s.last_seen_at_ms>=:start"
                       " AND pr.granted_at_ms<:cutoff AND ((pr.granted_at_ms>=:start"
                       " AND pr.granted_at_ms<:end) OR (pr.rendered_at_ms>=:start"
                       " AND pr.rendered_at_ms<:end AND pr.render_received_at_ms<:cutoff))"
                       " ORDER BY pr.id", params, min(max_rows, 2_000))
    membership_rows = _rows(connection, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ fm.prompt_id,e.*" + joins
        + "JOIN research_v2_sampling_opportunities q ON q.session_id=s.id "
        "JOIN research_v2_prompts pr ON pr.opportunity_id=q.id "
        "JOIN research_v2_feature_memberships fm ON fm.prompt_id=pr.id "
        "JOIN research_v2_events e ON e.id=fm.event_id WHERE " + filters
        + " AND s.started_at_ms<:end AND s.last_seen_at_ms>=:start AND pr.granted_at_ms<:cutoff"
        " AND ((pr.granted_at_ms>=:start AND pr.granted_at_ms<:end) OR "
        "(pr.rendered_at_ms>=:start AND pr.rendered_at_ms<:end AND pr.render_received_at_ms<:cutoff))"
        " ORDER BY fm.prompt_id,e.id", params, max_rows)
    members_by_prompt = defaultdict(list)
    for member in membership_rows:
        members_by_prompt[member["prompt_id"]].append(member)
    events_by_session = defaultdict(list)
    for event in event_rows:
        if event["session_id"] not in session_map:
            raise QualitySnapshotError("invalid_aggregate")
        events_by_session[event["session_id"]].append(event)
    for receipt in receipt_rows:
        session, key = session_map[receipt["session_id"]]
        metrics, state = grouped[key]["metrics"], by_session[key]
        for name in ("events_received", "events_accepted", "events_duplicate", "events_invalid",
                     "events_late", "events_dropped_client"):
            metrics[name] += receipt[name]
        if receipt["events_received"] != sum(receipt[n] for n in
                                             ("events_accepted", "events_duplicate", "events_invalid", "events_late")):
            raise QualitySnapshotError("invalid_aggregate")
        metrics["replay_only_receipts"] += int(receipt["events_received"] > 0
                                               and receipt["events_duplicate"] == receipt["events_received"])
        if receipt["events_accepted"] > 0:
            state["observed"].add(session["id"])
            state["subjects"].add(session["subject_id"])
    for session_id, events in events_by_session.items():
        session, key = session_map[session_id]
        metrics, state = grouped[key]["metrics"], by_session[key]
        period_events = [r for r in events if period_start_ms <= r["received_at_ms"] < period_end_ms]
        if any(r["source"] == "client" for r in period_events):
            state["observed"].add(session_id)
            state["subjects"].add(session["subject_id"])
            declarations = {}
            for event in sorted(events, key=lambda r: (r["received_at_ms"], r["occurred_at_ms"])):
                if event["event_type"] == "measurement_support":
                    declarations[event["detail_code"]] = event["received_at_ms"]
            devices = {"phone": "phone_sessions", "tablet": "tablet_sessions", "desktop": "desktop_sessions"}
            device = max((d for d in devices if d in declarations), key=lambda d: declarations[d], default=None)
            metrics[devices.get(device, "unknown_device_sessions")] += 1
            for prefix, output in (("pointer", "pointer"), ("media", "media"), ("visible", "visibility")):
                codes = [c for c in (prefix + "_supported", prefix + "_unsupported") if c in declarations]
                if codes:
                    latest = max(codes, key=lambda c: declarations[c])
                    metrics[output + ("_supported_sessions" if latest.endswith("_supported")
                                      else "_unsupported_sessions")] += 1
            metrics["unsupported_sessions"] += int("visible_unsupported" in declarations
                                                    and declarations.get("visible_unsupported", -1)
                                                    >= declarations.get("visible_supported", -1))
        for event in period_events:
            uncertainty = event["clock_uncertainty_ms"]
            if event["source"] == "client" and uncertainty is not None and uncertainty <= 5_000:
                delay = event["received_at_ms"] - event["occurred_at_ms"]
                if delay >= 0:
                    state["delays"].append(delay)
        intents = {r["action_ref"]: r for r in events if r["event_type"] == "action_intent"
                   and period_start_ms <= r["occurred_at_ms"] < period_end_ms
                   and r["occurred_at_ms"] < cutoff_ms and r["action_ref"]}
        terminal = {}
        for event in events:
            if (event["source"] == "server" and event["event_type"] == "workflow_outcome"
                    and event["detail_code"] in ("committed", "rejected", "failed", "already_completed")
                    and event["occurred_at_ms"] < cutoff_ms and event["action_ref"] in intents):
                terminal.setdefault(event["action_ref"], event)
        metrics["logical_actions"] += len(intents)
        metrics["known_terminal_actions"] += len(terminal)
        metrics["confirmed_actions"] += sum(r["detail_code"] in ("committed", "already_completed") for r in terminal.values())
        metrics["unknown_actions"] += len(intents) - len(terminal)
        metrics["pending_actions"] += len(intents) - len(terminal)
        for ref, result in terminal.items():
            intent = intents[ref]
            if intent["clock_uncertainty_ms"] is not None and intent["clock_uncertainty_ms"] <= 5_000:
                delay = result["occurred_at_ms"] - intent["occurred_at_ms"]
                if delay >= 0:
                    state["outcome_delays"].append(delay)
    for prompt in prompt_rows:
        session, key = session_map[prompt["session_id"]]
        metrics, state = grouped[key]["metrics"], by_session[key]
        grant_in_period = period_start_ms <= prompt["granted_at_ms"] < period_end_ms
        rendered = (prompt["rendered_at_ms"] is not None and prompt["render_received_at_ms"] is not None
                    and prompt["render_received_at_ms"] < cutoff_ms)
        if grant_in_period:
            metrics["prompts_reserved"] += 1
            metrics["prompts_expired"] += int(not rendered and prompt["expires_at_ms"] <= cutoff_ms)
            metrics["prompts_delivery_uncertain"] += int(not rendered)
        if not rendered or not period_start_ms <= prompt["rendered_at_ms"] < period_end_ms:
            continue
        metrics["prompts_rendered"] += 1
        matured = prompt["response_deadline_ms"] <= cutoff_ms
        received_answer = (prompt["response_received_at_ms"] is not None
                           and prompt["response_received_at_ms"] < cutoff_ms)
        answered = received_answer and prompt["status"] == "answered"
        metrics["prompts_pending"] += int(not matured)
        metrics["prompts_matured"] += int(matured)
        metrics["prompts_answered_matured"] += int(matured and answered)
        metrics["prompts_unanswered_matured"] += int(matured and not answered)
        metrics["prompts_skipped"] += int(received_answer and prompt["status"] == "skipped")
        label_yield = grouped[key]["evidence"]["label_yield"]
        label_yield["pending_rendered"] += int(not matured)
        if matured:
            label_yield["matured_rendered"] += 1
            if answered:
                timely, quality_code = prompt["answer_timely"], prompt["quality_code"]
                if (timely not in (0, 1) or quality_code not in
                        ("eligible", *_LABEL_EXCLUSION_REASONS)):
                    raise QualitySnapshotError("invalid_aggregate")
                label_yield["raw_answers"] += 1
                label_yield["timely_answers"] += int(bool(timely))
                label_yield["late_answers"] += int(not timely)
                # Match the export's stored predicate; never reclassify old rows
                # from reconstructed timing or inspect an emotional rating.
                eligible = bool(timely) and quality_code == "eligible"
                label_yield["primary_eligible_answers"] += int(eligible)
                if not eligible:
                    if quality_code not in label_yield["exclusion_reasons"]:
                        raise QualitySnapshotError("invalid_aggregate")
                    label_yield["excluded_answers"] += 1
                    label_yield["exclusion_reasons"][quality_code] += 1
            elif received_answer and prompt["status"] == "skipped":
                label_yield["skipped"] += 1
            else:
                label_yield["unanswered"] += 1
        # Late answers can overwrite the stored quality code. Reconstruct clock
        # evidence from the raw timing fields independently; never relabel rows.
        alignment = render_alignment_code(
            granted_at_ms=prompt["granted_at_ms"], rendered_at_ms=prompt["rendered_at_ms"],
            render_received_at_ms=prompt["render_received_at_ms"],
            clock_uncertainty_ms=prompt["render_clock_uncertainty_ms"])
        metrics["clock_unknown_windows"] += int(alignment == "clock_unknown")
        metrics["clock_uncertain_windows"] += int(alignment in {"clock_uncertain", "alignment_invalid"})
        features = build_features(members_by_prompt[prompt["prompt_id"]], cutoff_ms=prompt["granted_at_ms"])
        metrics["low_coverage_windows"] += int(not features["quality"]["coverage_sufficient"])
        if scope_code != "technical" and answered:
            rating = prompt["rating"]
            if rating not in (1, 2, 3, 4, 5):
                raise QualitySnapshotError("invalid_aggregate")
            metrics["ratings_total"] += 1
            metrics["rating_" + str(rating) + "_count"] += 1
            state["ratings"][rating].add(session["subject_id"])
            state["rated_subjects"].add(session["subject_id"])
    for key, group in grouped.items():
        state, metrics = by_session[key], group["metrics"]
        label_yield = group["evidence"]["label_yield"]
        if label_yield["matured_rendered"] >= LABEL_YIELD_MINIMUM_MATURED:
            label_yield["assessment"] = "observed"
            label_yield["primary_eligible_fraction"] = (
                label_yield["primary_eligible_answers"] / label_yield["matured_rendered"])
        metrics["observed_sessions"] = len(state["observed"])
        metrics["distinct_subjects"] = len(state["subjects"])
        if scope_code != "technical":
            metrics["rating_participants"] = len(state["rated_subjects"])
            for rating in range(1, 6):
                metrics["rating_" + str(rating) + "_participants"] = len(state["ratings"][rating])
        for prefix, data, qs in (("ingest_delay", state["delays"], (("p50", .5), ("p90", .9), ("p99", .99))),
                                 ("outcome_delay", state["outcome_delays"], (("p50", .5), ("p90", .9)))):
            metrics[prefix + "_count"] = len(data)
            for name, q in qs:
                value = linear_quantile(data, q)
                metrics[prefix + "_" + name + "_ms"] = round(value) if value is not None else None
    return _report(period_start_ms, period_end_ms, cutoff_ms, scope_code, list(grouped.values()),
                   configs, global_delivery, global_retention)


def _report(start, end, cutoff, scope, groups, configs, global_delivery=None, global_retention=None):
    return {"monitor_version": MONITOR_VERSION, "schema_version": SCHEMA_VERSION,
            "period_start_ms": start, "period_end_ms": end, "cutoff_ms": cutoff,
            "scope_code": scope, "groups": groups,
            "evidence": {"denominator_semantics": DENOMINATOR_SEMANTICS,
                         "render_timing_semantics": RENDER_TIMING_SEMANTICS,
                         "label_yield_semantics": LABEL_YIELD_SEMANTICS,
                         "client_start_grace_ms": CLIENT_START_GRACE_MS,
                         "retention_job_grace_ms": RETENTION_JOB_GRACE_MS},
            "global_delivery": global_delivery,
            "global_retention": global_retention,
            "collection": [{"configuration_id": c["configuration_id"], "status": c["status"],
                            "collecting": bool(c["is_collecting"]), "retention_days": c["retention_days"],
                            "period_start": c["collection_starts_at"].isoformat(),
                            "period_end": c["collection_ends_at"].isoformat()} for c in configs],
            "assessment": "observed" if any(g["metrics"]["observed_sessions"] for g in groups)
                          else "insufficient_evidence",
            "limitations": ["Server-known sessions are not exact hourly page views, confirmed browser display or visible coverage.",
                            "Missing client observations after the startup grace trigger technical investigation, not a claim of attention.",
                            "The startup cohort cannot detect later total client silence after an earlier accepted observation in that session.",
                            "Release is the immutable collector source digest, not a historical deployment guess.",
                            "Device categories are client-reported viewport classes, not physical hardware identification.",
                            "No export, backup, purge or notification-delivery acceptance is inferred.",
                            "Quality aggregates do not establish model accuracy or benefit of assistance.",
                            "Label-yield evidence uses stored eligibility, not a new assessment of old answers or emotional truth.",
                            "Additional label-yield evidence is private JSON; fixed SQL and Researcher report counts remain raw.",
                            "Unresolved ingestion receipts cannot be attributed to a study group.",
                            "Retention due counts use each recorded configuration policy; prolonged overdue adds a 26-hour daily-job grace.",
                            "No expired rows is not proof the daily purge ran; its success requires an independent heartbeat."]}


def critical_issues(snapshot):
    """Return deterministic invariant issues only; no uncalibrated rate alarms.

    Runtime query/schema failures are handled separately by the runner. Rate,
    coverage and nonresponse counts remain observations until the pilot defines
    minimum sample sizes and repeat-evidence thresholds. No traffic supplies
    no recovery evidence for an existing client-delivery incident.
    """
    issues = []
    delivery = snapshot.get("global_delivery")
    if (delivery and delivery["attempts"] >= 20
            and delivery["rejected_attempts"] / delivery["attempts"] >= .95
            and any(c["collecting"] for c in snapshot["collection"])):
        issues.append({"rule_code": "invalid_rate", "severity": "critical",
                       "aggregate_scope_key": sha256(b"v2-global-technical-delivery").hexdigest()})
    if delivery and delivery.get("missing_client_sessions", 0):
        issues.append({"rule_code": "delivery_stopped", "severity": "critical",
                       "aggregate_scope_key": sha256(b"v2-global-server-presence").hexdigest()})
    retention = snapshot.get("global_retention")
    if retention and retention["prolonged_overdue_sessions"]:
        issues.append({"rule_code": "expiry_backlog", "severity": "critical",
                       "aggregate_scope_key": sha256(b"v2-global-retention").hexdigest()})
    for group in snapshot["groups"]:
        m = group["metrics"]
        if (m["prompts_matured"] != m["prompts_answered_matured"] + m["prompts_unanswered_matured"]
                or m["known_terminal_actions"] > m["logical_actions"]):
            issues.append({"rule_code": "invalid_aggregate", "severity": "critical",
                           "aggregate_scope_key": group["scope"]["aggregate_scope_key"]})
    return issues


def read_latest_reports(connection, *, now_ms, limit=10, configuration_id=None,
                        scope_code="technical"):
    """Read bounded unexpired reports for an already authorized Researcher.

    Authorization belongs to the role-gated route, not this storage helper.
    This function does not create reports or silently run the checker with the
    LMS write principal. Technical reads omit rating columns altogether.
    """
    if (scope_code not in SCOPES or type(now_ms) is not int or now_ms < 0
            or type(limit) is not int or not 1 <= limit <= 30
            or (configuration_id is not None
                and (type(configuration_id) is not int or configuration_id < 1))):
        raise QualitySnapshotError("invalid_scope")
    if connection.dialect.name != "mysql":
        raise QualitySnapshotError("mysql_required")
    columns = ("configuration_id", "provenance", "protocol_version", "feature_version", "release_ref",
               "analysis_partition", "scope_code", "schema_version", "monitor_version", "aggregate_scope_key",
               "period_start_ms", "period_end_ms", "cutoff_ms", "created_at_ms", "expiry_at_ms")
    metrics = tuple(n for n in METRIC_NAMES if scope_code != "technical" or n not in _RATING_NAMES)
    sql = ("SELECT /*+ MAX_EXECUTION_TIME(5000) */ " + ",".join(columns + metrics)
           + " FROM research_quality_reports WHERE expiry_at_ms>:now AND scope_code=:scope"
           " AND schema_version=:schema AND monitor_version=:monitor")
    params = {"now": now_ms, "scope": scope_code, "schema": SCHEMA_VERSION, "monitor": MONITOR_VERSION,
              "row_limit": limit}
    if configuration_id is not None:
        sql += " AND configuration_id=:configuration_id"
        params["configuration_id"] = configuration_id
    sql += " ORDER BY period_end_ms DESC,created_at_ms DESC,id DESC LIMIT :row_limit"
    return [dict(row) for row in connection.execute(text(sql), params).mappings().all()]
