"""Additive pre-decision context for frustration and assistance research.

No database, labels, prompt interactions, content, identity or future events.
Detector and context features have separate contracts and the same temporal cutoff.
"""
from app.services.research_features import (
    WINDOW_MS, _get, union_intervals, intersect_intervals, interval_length,
)

CONTEXT_FEATURE_VERSION = "recent-use-context.v1"


def build_context_features(events, *, cutoff_ms, base_features):
    """Use the SAME frozen grant membership and coverage rules as the detector.

    Values describe observations, never diagnose a cause or recommend an answer.
    The caller supplies base_features from the same events and cutoff.
    """
    if base_features.get("cutoff_ms") != cutoff_ms:
        raise ValueError("Context and detector cutoffs must match.")
    start = cutoff_ms - WINDOW_MS
    rows, seen = [], set()
    for row in events:
        occurred, received = _get(row, "occurred_at_ms"), _get(row, "received_at_ms")
        if type(occurred) is not int or type(received) is not int:
            continue
        if occurred >= cutoff_ms or received >= cutoff_ms:
            continue
        if _get(row, "source") == "client":
            uncertainty = _get(row, "clock_uncertainty_ms")
            if type(uncertainty) is not int or not 0 <= uncertainty <= 5_000:
                continue
        uid = _get(row, "event_uid")
        if uid in seen:
            continue
        seen.add(uid)
        rows.append(row)
    visible = union_intervals([
        (_get(r, "interval_started_at_ms"), _get(r, "occurred_at_ms")) for r in rows
        if _get(r, "event_type") == "visible_interval" and _get(r, "detail_code") == "visible"
        and _get(r, "interval_started_at_ms") is not None
    ], start_ms=start, end_ms=cutoff_ms)
    observed = interval_length(visible)
    recent = [r for r in rows if _get(r, "occurred_at_ms") >= start]
    support = base_features.get("support", {})
    enough = base_features.get("quality", {}).get("coverage_sufficient") is True
    values, missing, denominators = {}, {}, {}

    def put(name, value, channel, denominator=None):
        reason = ("coverage_low" if not enough else
                  "support_unknown" if support.get(channel) is None else
                  "unsupported" if support.get(channel) is not True else
                  "no_denominator" if denominator == 0 else None)
        values[name] = None if reason else value
        if reason:
            missing[name] = reason
        if denominator is not None:
            denominators[name] = denominator

    def duration(kind=None, context=None):
        spans = [(_get(r, "interval_started_at_ms"), _get(r, "occurred_at_ms")) for r in rows
                 if _get(r, "event_type") == ("media_interval" if kind else "visible_interval")
                 and _get(r, "interval_started_at_ms") is not None
                 and (kind is None or _get(r, "detail_code") == kind)
                 and (context is None or _get(r, "context_code") == context)]
        return interval_length(intersect_intervals(spans, visible))

    reader_ms = duration(context="reader")
    put("reader_visible_fraction", reader_ms / observed if observed else None, "visibility", observed)
    media_spans = [(_get(r, "interval_started_at_ms"), _get(r, "occurred_at_ms")) for r in rows
                   if _get(r, "event_type") == "media_interval" and _get(r, "interval_started_at_ms") is not None]
    media_ms = interval_length(intersect_intervals(media_spans, visible))
    put("media_visible_fraction", media_ms / observed if observed else None, "media", observed)
    put("media_playing_fraction", duration(kind="playing") / observed if observed else None, "media", observed)
    # This is visible time without observed media, never proof of reading/attention.
    put("nonmedia_visible_fraction", (observed - media_ms) / observed if observed else None, "media", observed)
    intents = {_get(r, "action_ref"): r for r in recent
               if _get(r, "event_type") == "action_intent" and _get(r, "action_ref")}
    for name, action in (("open_action_count", "open"), ("close_action_count", "close"),
                         ("navigate_action_count", "navigate"), ("retry_action_count", "retry"),
                         ("seek_action_count", "seek")):
        put(name, sum(_get(r, "action_code") == action for r in intents.values()), "actions")
    speed_changes = {_get(r, "action_ref") or _get(r, "event_uid") for r in recent
                     if _get(r, "event_type") == "media_action" and _get(r, "action_code") == "revise"}
    put("media_speed_change_count", len(speed_changes), "media")
    units = {_get(r, "outcome_ref") or _get(r, "event_uid") for r in recent
             if _get(r, "source") == "server" and _get(r, "event_type") == "workflow_outcome"
             and _get(r, "activity_code") == "reader" and _get(r, "action_code") == "complete"
             and _get(r, "detail_code") == "committed"}
    put("confirmed_unit_completion_count", len(units), "visibility")
    return {"feature_version": CONTEXT_FEATURE_VERSION, "cutoff_ms": cutoff_ms,
            "window_start_ms": start, "window_end_ms": cutoff_ms, "values": values,
            "missing": missing, "denominators": denominators,
            "limitations": ["Visible time does not establish attention or comprehension.",
                            "Behavior does not establish the cause of frustration or the benefit of help."]}
