"""Separate bounded v2 archive. Final-test/unassigned participants are quarantined.

Reads only the caller's consistent snapshot. Features use exact grant membership;
raw answers and predictor columns are separate files. No identity links are read.
"""
import hashlib
import io
import json
import zipfile

from flask import current_app
from app.extensions import db
from app.models import ResearchConfiguration, ResearchSession, ResearchSubject
from app.models.research_v2 import (
    ResearchV2Event as Event, ResearchV2Prompt as Prompt, ResearchV2SamplingState as State,
    ResearchV2SamplingOpportunity as Opportunity, ResearchV2FeatureMembership as Membership,
    ResearchV2EvaluationPartition as Partition, ResearchV2Protocol as Protocol,
    ResearchV2DeliveryReceipt as Receipt,
)
from app.services.research_v2_dictionary import EVENT_SCHEMA_VERSION, EXPORT_FORMAT, PROTOCOL_VERSION, FEATURE_VERSION
from app.services.research_v2_features import build_features, RENDER_TIMING_SEMANTICS


def build_export(configuration_id, period_from, period_to, cutoff_ms, tz_name):
    from app.services.research_exports import _bounds, _csv, ExportTooLarge, ClockAhead
    from app.services.research_scope import export_session_provenances
    provenance = current_app.config["RESEARCH_DATA_PROVENANCE"]
    configuration = db.session.get(ResearchConfiguration, configuration_id)
    protocol = db.session.get(Protocol, configuration_id)
    if configuration is None or protocol is None:
        raise ValueError("Select a prepared v2 configuration.")
    lower, upper = _bounds(period_from, period_to, tz_name)
    query = db.session.query(ResearchSession, ResearchSubject.subject_code, State.release_ref,
                             Partition.partition_code).join(ResearchSubject, ResearchSubject.id == ResearchSession.subject_id).join(
        State, State.session_id == ResearchSession.id).join(Partition, Partition.subject_id == ResearchSession.subject_id).filter(
        ResearchSession.configuration_id == configuration_id,
        ResearchSession.provenance.in_(export_session_provenances(provenance)),
        Partition.partition_code.in_(("development", "prospective_monitor")))
    if provenance == "study":
        query = query.filter(ResearchSubject.provenance == "study")
    if lower is not None:
        query = query.filter(ResearchSession.started_at_ms >= lower)
    if upper is not None:
        query = query.filter(ResearchSession.started_at_ms < upper)
    sessions = query.order_by(ResearchSession.id).limit(501).all()
    if len(sessions) > 500:
        raise ExportTooLarge()
    session_map = {s.id: (s.public_id, code, release, partition) for s, code, release, partition in sessions}
    ids = list(session_map)
    events = db.session.query(Event).filter(Event.session_id.in_(ids)).order_by(Event.id).limit(100001).all() if ids else []
    opportunities = db.session.query(Opportunity).filter(Opportunity.session_id.in_(ids)).order_by(Opportunity.id).limit(100001).all() if ids else []
    opp_map = {o.id: o for o in opportunities}
    prompts = db.session.query(Prompt).filter(Prompt.opportunity_id.in_(list(opp_map))).order_by(Prompt.id).limit(5001).all() if opp_map else []
    receipts = db.session.query(Receipt).filter(Receipt.session_id.in_(ids)).order_by(Receipt.id).limit(100001).all() if ids else []
    if len(events) > 100000 or len(opportunities) > 100000 or len(prompts) > 5000 or len(receipts) > 100000:
        raise ExportTooLarge()
    server_times = [s.last_seen_at_ms for s, *_ in sessions]
    server_times += [e.received_at_ms for e in events]
    server_times += [o.decided_at_ms for o in opportunities]
    server_times += [r.received_at_ms for r in receipts]
    for p in prompts:
        server_times += [x for x in (p.granted_at_ms, p.render_received_at_ms, p.response_received_at_ms, p.terminal_at_ms) if x is not None]
    if max(server_times, default=0) > cutoff_ms:
        raise ClockAhead()
    event_map = {e.id: e for e in events}
    memberships = db.session.query(Membership).filter(Membership.prompt_id.in_([p.id for p in prompts])).limit(500001).all() if prompts else []
    if len(memberships) > 500000:
        raise ExportTooLarge()
    members = {}
    for m in memberships:
        if m.event_id not in event_map:
            raise ValueError("Incomplete decision membership.")
        members.setdefault(m.prompt_id, []).append(event_map[m.event_id])
    files, dictionary = {}, []

    def add(name, header, rows):
        files[name] = _csv(header, rows)
        dictionary.extend((name, key, "Server source metadata; never a predictor." if key in {
            "rating", "cause_mask", "help_requested", "quality_code", "answer_timely", "derived_status",
            "rendered_at_ms", "response_received_at_ms"} else "See the versioned data contract and feature missing/support metadata.") for key in header)

    add("sessions.csv", ["session_ref", "subject_code", "release_ref", "analysis_partition", "provenance", "started_at_ms", "last_seen_at_ms", "ended_at_ms"],
        [(*session_map[s.id], s.provenance, s.started_at_ms, s.last_seen_at_ms, s.ended_at_ms) for s, *_ in sessions])
    event_columns = [c.name for c in Event.__table__.columns if c.name not in {"id", "session_id"}]
    add("events.csv", ["session_ref", *event_columns],
        [(session_map[e.session_id][0], *(getattr(e, c) for c in event_columns)) for e in events])
    add("opportunities.csv", ["session_ref", "opportunity_slot", "due_visible_ms", "decided_at_ms", "decision", "reason_code", "selection_permille", "reserved_at_ms"],
        [(session_map[o.session_id][0], o.opportunity_slot, o.due_visible_ms, o.decided_at_ms, o.decision, o.reason_code, o.selection_permille, o.reserved_at_ms) for o in opportunities])
    receipt_columns = [c.name for c in Receipt.__table__.columns if c.name not in {"id", "session_id"}]
    add("delivery.csv", ["session_ref", *receipt_columns],
        [(session_map[r.session_id][0], *(getattr(r, c) for c in receipt_columns)) for r in receipts])
    labels, feature_rows, membership_rows = [], [], []
    for p in prompts:
        session_ref, code, release, partition = session_map[opp_map[p.opportunity_id].session_id]
        derived = ("expired" if p.status == "reserved" and cutoff_ms >= p.expires_at_ms else
                   "no_response" if p.status == "rendered" and cutoff_ms > p.response_deadline_ms else p.status)
        eligible = p.status == "answered" and p.answer_timely and p.quality_code == "eligible"
        labels.append((p.public_id, session_ref, code, partition, derived, p.granted_at_ms, p.rendered_at_ms,
                       p.reference_start_ms, p.reference_end_ms, p.response_received_at_ms,
                       p.rating, p.cause_mask, p.help_requested, p.answer_timely, p.quality_code, bool(eligible),
                       int(p.rating >= 3) if eligible else None, p.display_locale, p.help_question_shown,
                       p.render_clock_uncertainty_ms, p.render_received_at_ms, p.response_deadline_ms, p.expires_at_ms))
        selected = members.get(p.id, [])
        feature = build_features(selected, cutoff_ms=p.granted_at_ms,
            minimum_observed_ms=configuration.min_observed_seconds * 1000,
            maximum_clock_uncertainty_ms=protocol.maximum_clock_uncertainty_ms)
        feature_rows.append((p.public_id, code, partition, release, p.granted_at_ms,
                             json.dumps(feature, sort_keys=True, separators=(",", ":"))))
        membership_rows.extend((p.public_id, e.event_uid) for e in selected)
    add("labels.csv", ["prompt_ref", "session_ref", "subject_code", "analysis_partition", "derived_status", "granted_at_ms", "rendered_at_ms",
        "reference_start_ms", "reference_end_ms", "response_received_at_ms", "rating", "cause_mask", "help_requested", "answer_timely", "quality_code", "primary_label_eligible",
        "moderate_or_higher_label", "display_locale", "help_question_shown", "render_clock_uncertainty_ms", "render_received_at_ms", "response_deadline_ms", "expires_at_ms"], labels)
    add("features.csv", ["prompt_ref", "subject_code", "analysis_partition", "release_ref", "cutoff_ms", "feature_payload_json"], feature_rows)
    add("membership.csv", ["prompt_ref", "event_uid"], membership_rows)
    files["data_dictionary.csv"] = _csv(["file", "column", "description"], dictionary)
    expiry = min((s.last_seen_at_ms + configuration.retention_days * 86400000 for s, *_ in sessions), default=cutoff_ms + configuration.retention_days * 86400000)
    counts = {"subjects": len({code for _, code, *_ in sessions}), "sessions": len(sessions), "events": len(events),
              "prompts": len(prompts), "oldest_last_seen_ms": min((s.last_seen_at_ms for s, *_ in sessions), default=None),
              "_session_refs": [s.public_id for s, *_ in sessions], "_subject_ids": sorted({s.subject_id for s, *_ in sessions}),
              "_expiry_at_ms": expiry}
    manifest = {"format": EXPORT_FORMAT, "event_schema_version": EVENT_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION, "feature_version": FEATURE_VERSION,
        "dataset_kind": "study" if provenance == "study" else "operational_review", "cutoff_ms": cutoff_ms,
        "configuration_version": configuration.version_number, "retention_days": configuration.retention_days,
        "configuration_policy": {k: getattr(configuration, k) for k in (
            "session_inactivity_minutes", "max_prompts_per_session", "max_prompts_per_day", "lookback_seconds",
            "min_observed_seconds", "random_prompt_permille", "activity_end_prompt_permille", "offer_ttl_seconds", "response_window_seconds")},
        "protocol_policy": {k: getattr(protocol, k) for k in ("study_timezone", "first_opportunity_seconds", "opportunity_step_seconds",
            "minimum_spacing_seconds", "heartbeat_seconds", "maximum_gap_seconds", "maximum_clock_uncertainty_ms")},
        "filters": {"provenance": provenance, "timezone": tz_name, "session_start_local_from": str(period_from) if period_from else None,
                    "session_start_local_to": str(period_to) if period_to else None},
        "counts": {k: v for k, v in counts.items() if not k.startswith("_")},
        "quarantine": "Unassigned and locked-final-test participants are excluded from all normal review files, including events and ratings.",
        "time_contract": "One committed read snapshot before export cutoff. Each prompt uses only immutable event membership frozen at grant G; W=[G-120000,G). Browser render T is separate; late/uncertain/misaligned answers never primary labels.",
        "render_timing_semantics": {"version": RENDER_TIMING_SEMANTICS,
            "new_validation": "Keep E unchanged. Known 0<=u<=protocol maximum and R>=G are required; J=[E-u,E+u] intersect [G,R] must be nonempty with max(J)<=G+5000.",
            "recorded_eligibility": "Stored quality and primary eligibility are preserved, including earlier source releases. This metadata does not reclassify existing rows.",
            "answer_deadline": "Unchanged: stored rendered_at_ms plus the recorded response window."},
        "predictor_allowlist": "Only feature_payload_json.values with its missing/support/denominator metadata. Never label fields, identities, subject/session IDs, render/response timing, causes, help preference, partition, sampling decisions or quality metadata.",
        "missing_label_rule": "Only primary_label_eligible=true may supply a training target. Skip, nonresponse, unprompted, late and poor-quality windows are not negatives. Raw 1–5 answers remain unchanged.",
        "primary_label_mapping": "Predeclared pilot moderate-or-higher reported frustration: raw 3–5 -> 1; raw 1–2 -> 0, only for eligible windows. This is an operational self-report target, not a diagnosis or proof of helpful intervention.",
        "evaluation": "Split by participant before model fitting/tuning. Development and prospective-monitor groups remain separate. Final-test release requires a separate frozen-model approval.",
        "question": "In the two minutes before this question was sent, how frustrated did you feel on this platform?",
        "limitations": ["Viewport class is not physical device identity.", "Browser delivery/render reports do not prove attention.", "Manual phone/pilot validation remains required."],
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    raw_manifest = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True).encode("utf-8")
    files["manifest.json"] = raw_manifest
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue(), manifest, counts, hashlib.sha256(raw_manifest).hexdigest()
