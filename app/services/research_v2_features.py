"""Shared, I/O-free v2 reconstruction at a decision cutoff.

Inputs are already validated event mappings or model objects. No prompt answer,
identity, future arrival or response-time metadata is accepted as a predictor.
The result is JSON-compatible; unavailable values are None with a reason.
Quantiles use linear interpolation at (n-1)*q (Hyndman/Fan type 7).
"""
from collections.abc import Mapping
from math import floor


FEATURE_VERSION = "recent-frustration-features.v1"
RENDER_TIMING_SEMANTICS = "causal-receipt-clock-bounds.v1"
WINDOW_MS = 120_000
CONFIRMED_PROGRESS_ACTIONS = frozenset({
    ("lesson", "complete"), ("assignment", "submit"), ("quiz", "submit"),
    ("listening", "complete"), ("speaking", "submit"),
})
TERMINAL_DETAILS = frozenset({"committed", "rejected", "failed", "already_completed"})


def _get(row, name, default=None):
    return row.get(name, default) if isinstance(row, Mapping) else getattr(row, name, default)


def render_alignment_code(*, granted_at_ms, rendered_at_ms, render_received_at_ms,
                          clock_uncertainty_ms, maximum_clock_uncertainty_ms=5_000):
    """Assess the full plausible causal render interval without changing E.

    E is a calibrated estimate, u its uncertainty, G the grant and R the render
    acknowledgement receipt. Closed intervals [E-u,E+u] and [G,R] must overlap,
    and their entire intersection must fit within [G,G+5000]. An exact shared
    endpoint is nonempty; an interval straddling G+5000 remains excluded.
    This validates reported timing, never display attention or answer timeliness.
    """
    if clock_uncertainty_ms is None:
        return "clock_unknown"
    if (type(clock_uncertainty_ms) is not int or clock_uncertainty_ms < 0
            or clock_uncertainty_ms > maximum_clock_uncertainty_ms):
        return "clock_uncertain"
    if any(type(value) is not int or value < 0 for value in
           (granted_at_ms, rendered_at_ms, render_received_at_ms)):
        return "alignment_invalid"
    lower = max(rendered_at_ms - clock_uncertainty_ms, granted_at_ms)
    upper = min(rendered_at_ms + clock_uncertainty_ms, render_received_at_ms)
    if (render_received_at_ms < granted_at_ms or lower > upper
            or upper > granted_at_ms + 5_000):
        return "alignment_invalid"
    return "not_assessed"


def union_intervals(intervals, *, start_ms=None, end_ms=None):
    """Return sorted disjoint half-open intervals, clipped before union."""
    clipped = []
    for left, right in intervals:
        if start_ms is not None:
            left = max(left, start_ms)
        if end_ms is not None:
            right = min(right, end_ms)
        if right > left:
            clipped.append((left, right))
    merged = []
    for left, right in sorted(clipped):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def interval_length(intervals):
    return sum(right - left for left, right in union_intervals(intervals))


def intersect_intervals(first, second):
    result = []
    a, b = union_intervals(first), union_intervals(second)
    i = j = 0
    while i < len(a) and j < len(b):
        left, right = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if right > left:
            result.append((left, right))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return result


def linear_quantile(values, probability):
    """Type-7 quantile; an empty sample has no quantile."""
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * probability
    low = floor(index)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (index - low)


def _context(row):
    # A category alone cannot prove that two different activities are the same.
    ref = _get(row, "context_ref")
    return (_get(row, "activity_code"), _get(row, "context_code"), ref) if ref else None


def is_confirmed_progress(row):
    return (_get(row, "source") == "server"
            and _get(row, "event_type") == "workflow_outcome"
            and _get(row, "detail_code") == "committed"
            and bool(_get(row, "confirmed_progress"))
            and (_get(row, "activity_code"), _get(row, "action_code"))
            in CONFIRMED_PROGRESS_ACTIONS)


def build_features(events, *, cutoff_ms, minimum_observed_ms=90_000,
                   maximum_clock_uncertainty_ms=5_000, channel_support=None):
    """Build the exact 120-second feature window using observations available at T.

    Pass all relevant session events, including starts/context preceding W and
    support declarations. The caller must bound retrieval and preserve an
    explicit truncated-data exclusion if its retrieval limit was exceeded.
    channel_support optionally supplies trusted per-channel instrumentation
    support (actions/revisions/help/requests/media/visibility). It must describe
    collector capability, never inferred emotion. Client support declarations
    remain reported capabilities, not evidence of human attention.
    """
    if type(cutoff_ms) is not int or cutoff_ms < WINDOW_MS:
        raise ValueError("Invalid feature cutoff.")
    if not 0 < minimum_observed_ms <= WINDOW_MS:
        raise ValueError("Invalid minimum observation coverage.")
    start = cutoff_ms - WINDOW_MS
    available, aligned = [], []
    unknown_clock = uncertain_clock = future_arrivals = 0
    seen = set()
    for row in events:
        occurred, received = _get(row, "occurred_at_ms"), _get(row, "received_at_ms")
        if type(occurred) is not int or type(received) is not int:
            continue
        if occurred >= cutoff_ms or received >= cutoff_ms:
            future_arrivals += 1
            continue
        uid = _get(row, "event_uid")
        if uid and uid in seen:
            continue
        if uid:
            seen.add(uid)
        available.append(row)
        if _get(row, "source") == "client":
            uncertainty = _get(row, "clock_uncertainty_ms")
            if uncertainty is None:
                unknown_clock += int(occurred >= start)
                continue
            if uncertainty > maximum_clock_uncertainty_ms:
                uncertain_clock += int(occurred >= start)
                continue
        aligned.append(row)
    # Equal-millisecond client events retain their order within one page/tab.
    # Random event IDs must not put a context's leave after its replacement enter.
    aligned.sort(key=lambda r: (
        _get(r, "occurred_at_ms"), str(_get(r, "tab_ref") or ""),
        str(_get(r, "page_ref") or ""), _get(r, "client_sequence") or 0,
        str(_get(r, "event_uid", "")),
    ))
    window = [r for r in aligned if _get(r, "occurred_at_ms") >= start]
    visible = union_intervals([
        (_get(r, "interval_started_at_ms"), _get(r, "occurred_at_ms"))
        for r in aligned if _get(r, "event_type") == "visible_interval"
        and _get(r, "detail_code") == "visible"
        and _get(r, "interval_started_at_ms") is not None
    ], start_ms=start, end_ms=cutoff_ms)
    observed = interval_length(visible)
    enough = observed >= minimum_observed_ms
    declarations = {}
    for row in aligned:
        if _get(row, "event_type") != "measurement_support":
            continue
        code = _get(row, "detail_code", "")
        for prefix, channel in (("pointer", "actions"), ("media", "media"),
                                ("visible", "visibility")):
            if code in (prefix + "_supported", prefix + "_unsupported"):
                declarations[channel] = code.endswith("_supported")
    # Revisions/help/requests are semantic protocol channels. Explicit capability
    # overrides can mark an unsupported journey; absent visibility is unknown.
    core = declarations.get("visibility")
    support = {"visibility": core, "actions": core,
               "media": declarations.get("media"), "revisions": core,
               "help": core, "requests": core}
    if channel_support:
        for key in support:
            if key in channel_support:
                support[key] = channel_support[key] if type(channel_support[key]) is bool else None
    values, missing, denominators = {}, {}, {}

    def put(name, value, channel=None, denominator=None, reason=None):
        if denominator is not None:
            denominators[name] = denominator
        if reason is None and channel:
            if support.get(channel) is not True:
                reason = "unsupported" if support.get(channel) is False else "support_unknown"
            elif not enough:
                reason = "coverage_low"
        if reason is None and value is None:
            reason = "no_denominator" if denominator == 0 else "unavailable"
        values[name] = None if reason else value
        if reason:
            missing[name] = reason

    put("observed_fraction", observed / WINDOW_MS)
    actions = {}
    for row in window:
        ref = _get(row, "action_ref")
        if _get(row, "event_type") == "action_intent" and ref:
            actions.setdefault(ref, row)
    put("action_count", len(actions), "actions", denominator=len(actions))
    repeats, previous = 0, {}
    for row in actions.values():
        key = (_get(row, "action_code"), _context(row))
        moment = _get(row, "occurred_at_ms")
        if key[1] is not None and key in previous and moment - previous[key] <= 2_000:
            repeats += 1
        if key[1] is not None:
            previous[key] = moment
    put("rapid_repeat_fraction", repeats / len(actions) if actions else None,
        "actions", denominator=len(actions))
    terminals, all_terminals = {}, {}
    progress = []
    for row in aligned:
        if (_get(row, "source") == "server" and _get(row, "event_type") == "workflow_outcome"
                and _get(row, "detail_code") in TERMINAL_DETAILS and _get(row, "action_ref")):
            all_terminals.setdefault(_get(row, "action_ref"), row)
    for row in window:
        if is_confirmed_progress(row):
            progress.append(row)
        if (_get(row, "source") == "server" and _get(row, "event_type") == "workflow_outcome"
                and _get(row, "detail_code") in TERMINAL_DETAILS):
            ref = _get(row, "action_ref")
            if ref in actions:
                terminals.setdefault(ref, row)
    terminal_count = len(terminals)
    for name, detail in (("rejection_fraction", "rejected"),
                         ("technical_failure_fraction", "failed")):
        count = sum(_get(r, "detail_code") == detail for r in terminals.values())
        put(name, count / terminal_count if terminal_count else None,
            "actions", denominator=terminal_count)
    put("unknown_action_fraction", (len(actions) - terminal_count) / len(actions) if actions else None,
        "actions", denominator=len(actions))
    starts, finishes = {}, {}
    for row in aligned:
        attempt = _get(row, "attempt_ref")
        if not attempt:
            continue
        if _get(row, "event_type") == "request_start":
            starts.setdefault(attempt, row)
        elif _get(row, "event_type") == "request_finish":
            finishes.setdefault(attempt, row)
    # Wait measurements cover declared workflow writers, not every background
    # fetch or native navigation. Absence of an instrumented request is unknown,
    # rather than a false zero for all platform latency.
    if not starts and not finishes:
        support["requests"] = None
    waits, pending_by_action = [], {}
    for attempt, row in starts.items():
        left = _get(row, "occurred_at_ms")
        action_ref = _get(row, "action_ref")
        result = all_terminals.get(action_ref)
        right = (_get(finishes[attempt], "occurred_at_ms") if attempt in finishes else
                 _get(result, "occurred_at_ms") if result else cutoff_ms)
        if attempt not in finishes and result is None:
            # A disappeared page/request has an unknown ending. Never extend
            # its wait across a navigation or indefinitely through later work.
            boundaries = [_get(r, "occurred_at_ms") for r in aligned
                if _get(r, "occurred_at_ms") >= left and
                ((_get(r, "event_type") == "context_leave" and _get(r, "page_ref") == _get(row, "page_ref"))
                 or (_get(r, "event_type") == "context_enter" and _get(r, "tab_ref") == _get(row, "tab_ref")
                     and _get(r, "page_ref") != _get(row, "page_ref")))]
            right = min([right, left + 120000, *boundaries])
        if right >= left:
            intervals = intersect_intervals([(max(left, start), right)], visible)
            waits.extend(intervals)
            if result is None and action_ref and attempt not in finishes and right == cutoff_ms:
                pending_by_action.setdefault(action_ref, []).extend(intervals)
    for row in finishes.values():
        left = _get(row, "interval_started_at_ms")
        if left is not None:
            waits.append((left, _get(row, "occurred_at_ms")))
    wait_ms = interval_length(intersect_intervals(waits, visible))
    put("visible_wait_fraction", wait_ms / observed if observed else None,
        "requests", denominator=observed)
    pending_waits = [interval_length(intervals) / 1_000 for intervals in pending_by_action.values()]
    missing_starts = sum(attempt not in starts and _get(row, "interval_started_at_ms") is None
                         for attempt, row in finishes.items())
    put("pending_wait_max", max(pending_waits, default=0), "requests",
        denominator=len(pending_by_action), reason="request_start_unknown" if missing_starts else None)
    durations = []
    invalid_duration_count = 0
    for ref, result in terminals.items():
        duration = _get(result, "occurred_at_ms") - _get(actions[ref], "occurred_at_ms")
        if duration >= 0:
            durations.append(duration / 1_000)
        else:
            invalid_duration_count += 1
    for name, q in (("completed_latency_p50", .5), ("completed_latency_p90", .9)):
        put(name, linear_quantile(durations, q), "actions", denominator=len(durations))
    distinct_progress = {_get(r, "outcome_ref") or _get(r, "event_uid") for r in progress}
    put("confirmed_progress_count", len(distinct_progress), "visibility")
    contexts = [r for r in aligned if _get(r, "event_type") == "context_enter"]
    active_contexts = {}
    for row in aligned:
        key = (_get(row, "tab_ref"), _get(row, "page_ref"))
        if _get(row, "event_type") == "context_enter":
            # A new page in the same tab supersedes a lost unload event.
            active_contexts = {k: v for k, v in active_contexts.items() if k[0] != key[0]}
            active_contexts[key] = row
        elif _get(row, "event_type") == "context_leave":
            active_contexts.pop(key, None)
    current = next(iter(active_contexts.values())) if len(active_contexts) == 1 else None
    activity = _get(current, "activity_code", "unknown") if current else "unknown"
    put("activity_context", activity)
    current_context = _context(current) if current else None
    same_activity_progress = [r for r in progress if current_context is not None
                              and _context(r) == current_context]
    since = max((_get(r, "occurred_at_ms") for r in same_activity_progress), default=start)
    put("since_progress_visible_seconds",
        interval_length(intersect_intervals([(since, cutoff_ms)], visible)) / 1_000,
        "visibility", reason="context_unknown" if current_context is None else None)
    put("since_progress_censored", not bool(same_activity_progress))
    context_codes = []
    for row in contexts:
        if _get(row, "occurred_at_ms") < start:
            continue
        code = _get(row, "context_code")
        if not context_codes or context_codes[-1] != code:
            context_codes.append(code)
    loops = sum(a == c and a != b for a, b, c in zip(context_codes, context_codes[1:], context_codes[2:]))
    put("return_loop_count", loops, "visibility")
    put("revision_count", sum(_get(r, "count", 0) or 0 for r in window
                              if _get(r, "event_type") == "revision_count"), "revisions")
    # Recovery is a cohort of failures occurring in W, even when their action
    # began before W. It is not restricted to the action-start latency cohort.
    failure_map = {}
    for row in window:
        if (_get(row, "source") == "server" and _get(row, "event_type") == "workflow_outcome"
                and _get(row, "detail_code") in ("failed", "rejected")):
            failure_map.setdefault(_get(row, "action_ref") or _get(row, "outcome_ref"), row)
    failures = list(failure_map.values())
    recovered = eligible_followups = pending_followups = 0
    for failure in failures:
        moment = _get(failure, "occurred_at_ms")
        end = moment + 30_000
        context = _context(failure)
        if (end >= cutoff_ms or context is None
                or interval_length(intersect_intervals([(moment, end)], visible)) < 30_000):
            pending_followups += 1
            continue
        eligible_followups += 1
        recovered += any(_context(r) == context and moment < _get(r, "occurred_at_ms") <= end
                         for r in progress)
    put("recovery_fraction", recovered / eligible_followups if eligible_followups else None,
        "visibility", denominator=eligible_followups)
    help_refs = {_get(r, "action_ref") or _get(r, "event_uid") for r in window
                 if _get(r, "event_type") == "ordinary_help"}
    put("ordinary_help_count", len(help_refs), "help")
    media_intervals = [(_get(r, "interval_started_at_ms"), _get(r, "occurred_at_ms"))
                       for r in aligned if _get(r, "event_type") == "media_interval"
                       and _get(r, "interval_started_at_ms") is not None]
    media_visible = intersect_intervals(media_intervals, visible)
    buffer_intervals = [(_get(r, "interval_started_at_ms"), _get(r, "occurred_at_ms"))
                        for r in aligned if _get(r, "event_type") == "media_interval"
                        and _get(r, "detail_code") == "buffering"
                        and _get(r, "interval_started_at_ms") is not None]
    media_ms = interval_length(media_visible)
    put("media_buffer_fraction", interval_length(intersect_intervals(buffer_intervals, visible)) / media_ms
        if media_ms else None, "media", denominator=media_ms)
    for name, detail in (("media_replay_count", "replay"), ("media_error_count", "playback_error")):
        refs = {_get(r, "action_ref") or _get(r, "event_uid") for r in window
                if _get(r, "event_type") == "media_action" and _get(r, "detail_code") == detail}
        put(name, len(refs), "media")
    return {
        "feature_version": FEATURE_VERSION, "cutoff_ms": cutoff_ms,
        "window_start_ms": start, "window_end_ms": cutoff_ms,
        "quantile_convention": "linear_n_minus_one", "values": values,
        "time_unit": "seconds",
        "missing": missing, "denominators": denominators, "support": support,
        "quality": {"observed_ms": observed, "minimum_observed_ms": minimum_observed_ms,
                    "request_scope": "declared_workflow_writers_only",
                    "coverage_sufficient": enough, "clock_unknown_events": unknown_clock,
                    "clock_uncertain_events": uncertain_clock,
                    "excluded_unavailable_at_cutoff": future_arrivals,
                    "invalid_latency_count": invalid_duration_count,
                    "recovery_pending_count": pending_followups,
                    "completed_latency_count": len(durations),
                    "action_terminal_count": terminal_count,
                    "pending_action_count": len(actions) - terminal_count,
                    "pending_request_action_count": len(pending_by_action),
                    "timeout_action_count": len({_get(r, "action_ref") for r in finishes.values()
                                                 if _get(r, "detail_code") == "timeout"}),
                    "network_unknown_action_count": len({_get(r, "action_ref") for r in finishes.values()
                                                         if _get(r, "detail_code") == "network_unknown"}),
                    "reported_gap_count": sum(_get(r, "event_type") == "observation_gap" for r in window)},
    }
