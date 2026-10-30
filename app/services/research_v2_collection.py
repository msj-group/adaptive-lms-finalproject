"""V2 collection writes. Ordinary account/configuration/subject locks are reused.

No emotion is inferred here. Browser observations, committed workflow outcomes,
sampling decisions and optional answers retain distinct provenance. All writes
are version gated by the HTTP integration and rechecked under database locks.
"""
from datetime import datetime, time, timezone
from pathlib import Path
import hashlib
import re
import secrets
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.extensions import db
from app.models import ResearchSession, now_ms
from app.models.research_v2 import (
    ResearchV2Protocol as Protocol, ResearchV2SamplingState as State,
    ResearchV2SamplingOpportunity as Opportunity, ResearchV2Event as Event,
    ResearchV2Prompt as Prompt, ResearchV2FeatureMembership as Membership,
    ResearchV2DeliveryReceipt as Receipt, ResearchV2EvaluationPartition as Partition,
)
from app.services import research_collection as legacy
from app.services.research_v2_dictionary import (
    EVENT_SCHEMA_VERSION, CAUSE_BITS, is_integer, is_opaque_ref, validate_client_event,
)
from app.services.research_v2_features import build_features, interval_length, render_alignment_code

MAX_SNAPSHOT = 10000
_OFFER_NAMESPACE = uuid.UUID("9bd4c5e2-40cb-4332-a6cb-af13031e677a")
_DEFERRALS = {"hidden_tab", "timed_activity", "recording", "uploading", "blocking_dialog"}


def release_digest(root):
    """Content digest, including uncommitted source; never read environment secrets."""
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()
                       and p.suffix in {".py", ".js", ".html", ".css", ".json"}
                       and "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _off(scope=True):
    return {"collecting": False, "scope_valid": scope}


def prepare_presence(*, user_id, user_public_id, auth_version, configuration_id,
                     configuration_public_id, configuration_version, session_ref,
                     provenance, release_ref):
    """Prepare server presence before signing the browser's delivery scope.

    The caller MUST own an isolated Flask application-context session, never
    the academic route's session: the shared lock helper deliberately resets
    its transaction. Only primitive request-context values cross that boundary.
    Presence is neither a browser observation nor visible time. It creates no
    event, opportunity, display, answer or observation-run coverage.
    """
    if not isinstance(release_ref, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", release_ref):
        return None
    moment = now_ms()
    chain, refused = legacy.lock_collection_chain(
        user_id, moment, required_schema=EVENT_SCHEMA_VERSION)
    if refused:
        return None
    # An unlocked preview cannot authorize a changed login/configuration.
    if (chain.user.public_id != user_public_id or chain.user.auth_version != auth_version
            or chain.configuration.id != configuration_id
            or chain.configuration.public_id != configuration_public_id
            or chain.configuration.version_number != configuration_version):
        db.session.rollback()
        return None
    protocol = db.session.get(Protocol, chain.configuration.id)
    if protocol is None:
        db.session.rollback()
        return None
    session, ref = legacy.resolve_session(chain, session_ref, moment, provenance)
    state = db.session.get(State, session.id)
    if state is not None and state.release_ref != release_ref:
        db.session.rollback()
        return None
    if state is None:
        db.session.add(State(session_id=session.id, release_ref=release_ref,
                             observed_visible_ms=0, next_opportunity_slot=1,
                             next_due_visible_ms=protocol.first_opportunity_seconds * 1000,
                             version=1))
    if db.session.get(Partition, chain.subject.id) is None:
        db.session.add(Partition(subject_id=chain.subject.id, partition_code="unassigned",
                                 assignment_reason="pending", version=1))
    session.last_seen_at_ms = max(session.last_seen_at_ms, moment)
    db.session.commit()
    return ref


def _chain(user_id, session_ref, body, proof, provenance, release_ref, *, provision=True):
    moment = now_ms()
    chain, refused = legacy.lock_collection_chain(
        user_id, moment, provision=provision, delivery_proof=proof,
        delivery_scope=body["scope_token"], required_schema=EVENT_SCHEMA_VERSION,
    )
    if refused:
        return None, None, None, _off(refused != legacy.STALE_DELIVERY_SCOPE), session_ref
    protocol = db.session.get(Protocol, chain.configuration.id)
    if protocol is None:
        db.session.rollback()
        return None, None, None, _off(), session_ref
    old = legacy._lock_session(session_ref) if is_opaque_ref(session_ref) else None
    if old is not None and (old.subject_id != chain.subject.id
                            or not legacy._usable(old, chain.configuration, moment)):
        if old.subject_id == chain.subject.id:
            legacy.close_session(old, chain.configuration, moment)
        db.session.commit()
        return None, None, None, _off(False), legacy.successor_ref(session_ref)
    if not provision and old is None:
        db.session.rollback()
        return None, None, None, _off(False), session_ref
    session, ref = legacy.resolve_session(chain, session_ref, moment, provenance)
    state = db.session.get(State, session.id)
    if state is not None and state.release_ref != release_ref:
        # Deployments must pause/close collection before changing source. Never
        # attribute new observations to an old session release after an unsafe deploy.
        db.session.rollback()
        return None, None, None, _off(), ref
    if state is None:
        state = State(session_id=session.id, release_ref=release_ref,
                      observed_visible_ms=0, next_opportunity_slot=1,
                      next_due_visible_ms=protocol.first_opportunity_seconds * 1000,
                      version=1)
        db.session.add(state)
    if db.session.get(Partition, chain.subject.id) is None:
        db.session.add(Partition(subject_id=chain.subject.id, partition_code="unassigned",
                                 assignment_reason="pending", version=1))
    return chain, session, (state, protocol), None, ref


def _clock(body):
    offset, uncertainty = body.get("clock_offset_ms"), body.get("clock_uncertainty_ms")
    if offset is None and uncertainty is None:
        return None, None
    if (type(offset) is not int or abs(offset) > 86400000
            or not is_integer(uncertainty, 0, 86400000)):
        raise ValueError("invalid_clock")
    return offset, uncertainty


def ingest(user_id, session_ref, body, proof, provenance, release_ref):
    allowed = {"schema_version", "scope_token", "batch_ref", "attempt_ref", "client_now_ms",
               "clock_offset_ms", "clock_uncertainty_ms", "events", "dropped_client"}
    if (set(body) - allowed or not is_opaque_ref(body.get("batch_ref"))
            or not is_opaque_ref(body.get("attempt_ref"))
            or not is_integer(body.get("client_now_ms"))
            or not isinstance(body.get("events"), list) or not 1 <= len(body["events"]) <= 100
            or not is_integer(body.get("dropped_client", 0), 0, 100000)):
        raise ValueError("invalid_batch")
    offset, uncertainty = _clock(body)
    chain, session, state_protocol, refusal, ref = _chain(
        user_id, session_ref, body, proof, provenance, release_ref)
    if refusal:
        return refusal, ref
    moment = now_ms()
    prior_receipt = db.session.query(Receipt).filter_by(
        session_id=session.id, batch_ref=body["batch_ref"], attempt_ref=body["attempt_ref"]).first()
    # Every HTTP retry has a fresh attempt UUID. Exact delivery-attempt replays
    # do not increment counters or extend a session.
    accepted, rejected = [], []
    counts = dict(events_accepted=0, events_duplicate=0, events_invalid=0, events_late=0)
    uids = [r.get("event_uid") for r in body["events"]
            if isinstance(r, dict) and is_opaque_ref(r.get("event_uid"))]
    existing = {r.event_uid: r.session_id for r in db.session.query(Event)
                .filter(Event.event_uid.in_(uids)).all()} if uids else {}
    if prior_receipt is not None and any(uid not in existing for uid in uids):
        raise ValueError("delivery_attempt_reused")
    for raw in body["events"]:
        uid = raw.get("event_uid") if isinstance(raw, dict) else None
        if uid in existing:
            if existing[uid] == session.id:
                counts["events_duplicate"] += 1
                accepted.append(uid)
            else:
                counts["events_invalid"] += 1
                rejected.append(uid)
            continue
        clean = validate_client_event(raw)
        if clean is None:
            counts["events_invalid"] += 1
            if is_opaque_ref(uid):
                rejected.append(uid)
            continue
        corrected = clean["client_at_ms"] + (offset or 0)
        interval = clean.get("interval_started_at_ms")
        interval = interval + (offset or 0) if interval is not None else None
        # Unknown clocks are preserved as unknown raw measurements, not converted
        # into false server-clock coverage. Bound delivery age when alignment exists.
        if (corrected < 0 or (interval is not None and interval < 0)
                or (offset is not None and (corrected > moment + uncertainty + 1000
                    or corrected < session.started_at_ms - uncertainty - 45000
                    or moment - corrected > 1800000))):
            counts["events_late"] += 1
            rejected.append(uid)
            continue
        clean.pop("interval_started_at_ms", None)
        db.session.add(Event(**clean, session_id=session.id, source="client",
                             confirmed_progress=False, occurred_at_ms=corrected,
                             interval_started_at_ms=interval, received_at_ms=moment,
                             clock_offset_ms=offset, clock_uncertainty_ms=uncertainty))
        existing[uid] = session.id
        accepted.append(uid)
        counts["events_accepted"] += 1
    if prior_receipt is not None:
        db.session.rollback()
        return {"collecting": True, "scope_valid": True,
                "accepted_event_uids": accepted, "rejected_event_uids": rejected}, ref
    result = ("accepted" if counts["events_accepted"] == len(body["events"]) else
              "duplicate" if counts["events_duplicate"] == len(body["events"]) else
              "partially_accepted" if counts["events_accepted"] else "invalid")
    prior_batch = db.session.query(Receipt.id).filter_by(session_id=session.id, batch_ref=body["batch_ref"]).first()
    dropped = 0 if prior_batch else body.get("dropped_client", 0)
    db.session.add(Receipt(receipt_uid=str(uuid.uuid4()), session_id=session.id,
                           batch_ref=body["batch_ref"], attempt_ref=body["attempt_ref"],
                           scope_code="session", result_code=result, received_at_ms=moment,
                           events_received=len(body["events"]), events_dropped_client=dropped, **counts))
    session.batches_received += 1
    for key, count in counts.items():
        setattr(session, key, getattr(session, key) + count)
    session.events_dropped_client += dropped
    if counts["events_accepted"]:
        session.last_seen_at_ms = max(session.last_seen_at_ms, moment)
    db.session.commit()
    return {"collecting": True, "scope_valid": True,
            "accepted_event_uids": accepted, "rejected_event_uids": rejected}, ref


def _events(session_id):
    # Include context/support/action starts from before the feature window;
    # overflow is an explicit exclusion, never a truncated valid example.
    rows = db.session.query(Event).filter_by(session_id=session_id).order_by(Event.id).limit(MAX_SNAPSHOT + 1).all()
    return rows, len(rows) > MAX_SNAPSHOT


def _feature_quality(rows, cutoff, configuration, protocol, overflow=False):
    feature = build_features(rows, cutoff_ms=cutoff,
                             minimum_observed_ms=configuration.min_observed_seconds * 1000,
                             maximum_clock_uncertainty_ms=protocol.maximum_clock_uncertainty_ms)
    q = feature["quality"]
    reason = ("unsupported" if overflow else "clock_unknown" if q["clock_unknown_events"]
              else "clock_uncertain" if q["clock_uncertain_events"] else
              "coverage_low" if not q["coverage_sufficient"] else None)
    return feature, reason


def _budget_reason(chain, session, state, protocol, moment, tz_name):
    if state.skipped_at_ms is not None:
        return "skip"
    query = db.session.query(Opportunity).join(ResearchSession, Opportunity.session_id == ResearchSession.id).filter(
        ResearchSession.subject_id == chain.subject.id, Opportunity.reserved_at_ms.is_not(None))
    last = query.with_entities(func.max(Opportunity.reserved_at_ms)).scalar()
    if last is not None and moment - last < protocol.minimum_spacing_seconds * 1000:
        return "cooldown"
    if query.filter(ResearchSession.id == session.id).count() >= chain.configuration.max_prompts_per_session:
        return "session_budget"
    local = datetime.fromtimestamp(moment / 1000, timezone.utc).astimezone(ZoneInfo(protocol.study_timezone))
    midnight = int(datetime.combine(local.date(), time.min, ZoneInfo(protocol.study_timezone)).timestamp() * 1000)
    if query.filter(Opportunity.reserved_at_ms >= midnight).count() >= chain.configuration.max_prompts_per_day:
        return "day_budget"
    return None


def _offer_ref(session, opportunity):
    return str(uuid.uuid5(_OFFER_NAMESPACE, session.public_id + "/" + str(opportunity.opportunity_slot)))


def sample(user_id, session_ref, body, proof, provenance, release_ref, tz_name):
    if set(body) - {"schema_version", "scope_token", "client_at_ms", "critical_deferral"}:
        raise ValueError("invalid_sample")
    defer = body.get("critical_deferral")
    if defer is not None and defer not in _DEFERRALS:
        raise ValueError("invalid_deferral")
    chain, session, pair, refusal, ref = _chain(
        user_id, session_ref, body, proof, provenance, release_ref, provision=False)
    if refusal:
        return refusal, ref
    state, protocol = pair
    rows, overflow = _events(session.id)
    moment = now_ms()
    feature, reason = _feature_quality(rows, moment, chain.configuration, protocol, overflow)
    intervals = [(r.interval_started_at_ms, r.occurred_at_ms) for r in rows
                 if r.event_type == "visible_interval" and r.detail_code == "visible"
                 and r.clock_uncertainty_ms is not None
                 and r.clock_uncertainty_ms <= protocol.maximum_clock_uncertainty_ms
                 and r.interval_started_at_ms is not None
                 and 0 < r.occurred_at_ms - r.interval_started_at_ms <= protocol.maximum_gap_seconds * 1000
                 and r.occurred_at_ms < moment]
    observed = interval_length(intervals)
    state.observed_visible_ms = max(state.observed_visible_ms, observed)
    pending = db.session.query(Opportunity).filter_by(session_id=session.id, decision="selected").order_by(Opportunity.id.desc()).limit(8).all()
    for offered in pending:
        if offered.reserved_at_ms is None and moment < offered.decided_at_ms + chain.configuration.offer_ttl_seconds * 1000:
            db.session.commit()
            return {"collecting": True, "scope_valid": True,
                    "prompt": {"id": _offer_ref(session, offered)}}, ref
    prompt = None
    if observed >= state.next_due_visible_ms:
        reason = defer or reason or _budget_reason(chain, session, state, protocol, moment, tz_name)
        selected = reason is None and secrets.randbelow(1000) < chain.configuration.random_prompt_permille
        opportunity = Opportunity(session_id=session.id, opportunity_slot=state.next_opportunity_slot,
            due_visible_ms=state.next_due_visible_ms, decided_at_ms=moment,
            decision="deferred" if defer else "ineligible" if reason else "selected" if selected else "not_selected",
            reason_code=reason or "random_draw",
            selection_permille=chain.configuration.random_prompt_permille if reason is None else None)
        db.session.add(opportunity)
        state.next_opportunity_slot += 1
        # Delayed polls never run multiple retrospective lottery draws.
        state.next_due_visible_ms = observed + protocol.opportunity_step_seconds * 1000
        state.last_accounted_at_ms = moment
        state.version += 1
        if reason:
            session.sampling_ineligible_checks += 1
        else:
            session.sampling_eligible_checks += 1
        if selected:
            prompt = {"id": _offer_ref(session, opportunity)}
    db.session.commit()
    return {"collecting": True, "scope_valid": True, "prompt": prompt}, ref


def prompt_action(user_id, session_ref, prompt_ref, action, body, proof, provenance, release_ref, tz_name):
    common = {"schema_version", "scope_token", "client_at_ms"}
    allowed = {"grant": common | {"critical_deferral"},
               "render": common | {"clock_offset_ms", "clock_uncertainty_ms", "locale", "help_question_shown"},
               "respond": common | {"response_ref", "rating", "causes", "help_requested", "skipped"}}
    if action not in allowed or set(body) - allowed[action] or not is_opaque_ref(prompt_ref):
        raise ValueError("invalid_prompt")
    chain, session, pair, refusal, ref = _chain(
        user_id, session_ref, body, proof, provenance, release_ref, provision=False)
    if refusal:
        return refusal, ref
    state, protocol = pair
    moment = now_ms()
    prompt = db.session.query(Prompt).join(Opportunity, Prompt.opportunity_id == Opportunity.id).filter(
        Opportunity.session_id == session.id, Prompt.public_id == prompt_ref).first()
    if action == "grant":
        if prompt is not None:
            db.session.rollback()
            return {"collecting": True, "status": "granted" if prompt.status == "reserved" and moment < prompt.expires_at_ms else "unavailable",
                    "prompt": {"id": prompt.public_id, "help_question_shown": True}}, ref
        opportunities = db.session.query(Opportunity).filter_by(session_id=session.id, decision="selected").order_by(Opportunity.id.desc()).limit(8).all()
        opportunity = next((o for o in opportunities if _offer_ref(session, o) == prompt_ref), None)
        if (opportunity is None or opportunity.reserved_at_ms is not None or
                moment >= opportunity.decided_at_ms + chain.configuration.offer_ttl_seconds * 1000):
            db.session.rollback()
            return {"collecting": True, "status": "unavailable"}, ref
        defer = body.get("critical_deferral")
        if defer is not None and defer not in _DEFERRALS:
            raise ValueError("invalid_deferral")
        reason = defer or _budget_reason(chain, session, state, protocol, moment, tz_name)
        rows, overflow = _events(session.id)
        # G is after the committed SELECT snapshot, never inferred from receipts.
        grant = now_ms()
        _feature, quality = _feature_quality(rows, grant, chain.configuration, protocol, overflow)
        if reason or quality:
            db.session.rollback()
            return {"collecting": True, "status": "deferred"}, ref
        count = db.session.query(Opportunity).filter(Opportunity.session_id == session.id, Opportunity.reserved_at_ms.is_not(None)).count()
        opportunity.reservation_slot = count + 1
        opportunity.reserved_at_ms = grant
        db.session.flush()
        prompt = Prompt(public_id=prompt_ref, opportunity_id=opportunity.id, status="reserved",
                        granted_at_ms=grant, expires_at_ms=grant + chain.configuration.offer_ttl_seconds * 1000,
                        quality_code="not_assessed")
        db.session.add(prompt)
        db.session.flush()
        db.session.add_all(Membership(prompt_id=prompt.id, event_id=r.id) for r in rows
                           if r.occurred_at_ms < grant and r.received_at_ms < grant)
        db.session.commit()
        return {"collecting": True, "status": "granted", "server_now_ms": grant,
                "prompt": {"id": prompt_ref, "expires_at_ms": prompt.expires_at_ms,
                           "help_question_shown": True}}, ref
    if prompt is None:
        db.session.rollback()
        return {"collecting": True, "status": "unavailable"}, ref
    if action == "render":
        if prompt.rendered_at_ms is not None:
            db.session.rollback()
            return {"collecting": True, "rendered": True}, ref
        if prompt.status != "reserved" or moment >= prompt.expires_at_ms:
            if prompt.status == "reserved":
                prompt.status, prompt.terminal_at_ms = "expired", max(moment, prompt.expires_at_ms)
                db.session.commit()
            else:
                db.session.rollback()
            return {"collecting": True, "status": "unavailable"}, ref
        offset, uncertainty = _clock(body)
        if not is_integer(body.get("client_at_ms")) or body.get("locale") not in {"ar", "en"} or body.get("help_question_shown") is not True:
            raise ValueError("invalid_render")
        rendered = body["client_at_ms"] + offset if offset is not None else moment
        # The server receipt is a bound, never a fabricated precise render time.
        if rendered < 0:
            raise ValueError("invalid_render")
        prompt.rendered_at_ms, prompt.render_received_at_ms = rendered, moment
        prompt.reference_end_ms = prompt.granted_at_ms
        prompt.reference_start_ms = prompt.granted_at_ms - chain.configuration.lookback_seconds * 1000
        prompt.render_clock_uncertainty_ms = uncertainty
        prompt.display_locale = body["locale"]
        prompt.help_question_shown = True
        prompt.response_deadline_ms = rendered + chain.configuration.response_window_seconds * 1000
        prompt.status = "rendered"
        prompt.quality_code = render_alignment_code(
            granted_at_ms=prompt.granted_at_ms, rendered_at_ms=rendered,
            render_received_at_ms=moment, clock_uncertainty_ms=uncertainty,
            maximum_clock_uncertainty_ms=protocol.maximum_clock_uncertainty_ms)
        db.session.commit()
        return {"collecting": True, "rendered": True}, ref
    if not is_opaque_ref(body.get("response_ref")) or type(body.get("skipped")) is not bool:
        raise ValueError("invalid_answer")
    skip = body["skipped"]
    rating, mask, help_requested = None, None, None
    if skip:
        if set(body) - (common | {"response_ref", "skipped"}):
            raise ValueError("invalid_answer")
    else:
        causes = body.get("causes", [])
        if (not is_integer(body.get("rating"), 1, 5) or not isinstance(causes, list)
                or len(causes) > 5 or any(type(c) is not str or c not in CAUSE_BITS for c in causes)
                or len(set(causes)) != len(causes)
                or (body.get("help_requested") is not None and type(body["help_requested"]) is not bool)):
            raise ValueError("invalid_answer")
        rating, mask, help_requested = body["rating"], sum(CAUSE_BITS[c] for c in causes), body.get("help_requested")
    if prompt.status in {"answered", "skipped"}:
        same = (prompt.status == ("skipped" if skip else "answered") and
                (prompt.rating, prompt.cause_mask, prompt.help_requested) == (rating, mask, help_requested))
        db.session.rollback()
        return {"collecting": True, "status": "already" if same else "different"}, ref
    if prompt.rendered_at_ms is None or prompt.status not in {"rendered", "no_response"} or (skip and prompt.status == "no_response"):
        db.session.rollback()
        return {"collecting": True, "status": "unavailable"}, ref
    # Finish ORM reads before changing terminal fields: query autoflush must
    # never persist an answered prompt before its final quality is assigned.
    answer_timely, quality_code = None, prompt.quality_code
    if not skip:
        answer_timely = moment <= prompt.response_deadline_ms
        if not answer_timely:
            quality_code = "late_answer"
        elif quality_code == "not_assessed":
            rows = db.session.query(Event).join(Membership, Membership.event_id == Event.id).filter(Membership.prompt_id == prompt.id).all()
            _feature, reason = _feature_quality(rows, prompt.granted_at_ms, chain.configuration, protocol)
            quality_code = reason or "eligible"
    prompt.status = "skipped" if skip else "answered"
    prompt.response_received_at_ms = prompt.terminal_at_ms = max(moment, prompt.render_received_at_ms)
    if skip:
        state.skipped_at_ms = moment
        state.version += 1
    else:
        prompt.rating, prompt.cause_mask, prompt.help_requested = rating, mask, help_requested
        prompt.answer_timely, prompt.quality_code = answer_timely, quality_code
    db.session.commit()
    return {"collecting": True, "recorded": True}, ref


# Existing workflow routes declare these only AFTER their business transaction.
_OUTCOMES = {
    "lesson_completion": ("lesson", "complete"), "assignment_submission": ("assignment", "submit"),
    "quiz_start": ("quiz", "start"), "quiz_answer": ("quiz", "save"), "quiz_submission": ("quiz", "submit"),
    "listening_start": ("listening", "start"), "listening_answer": ("listening", "save"),
    "listening_submission": ("listening", "complete"), "speaking_submission": ("speaking", "submit"),
    "search": ("search", "search"), "message_send": ("messaging", "send"), "discussion_reply": ("discussion", "send"),
}


def record_outcomes(user_id, session_ref, outcomes, provenance, release_ref, hints, *, proof=None):
    moment = now_ms()
    chain, refusal = legacy.lock_collection_chain(user_id, moment, required_schema=EVENT_SCHEMA_VERSION)
    if refusal:
        return session_ref
    if proof is None or not proof.matches(proof.token, chain.user.public_id, chain.user.auth_version,
                                          chain.configuration.public_id, chain.configuration.version_number):
        hints = {}  # Preserve authoritative outcome; correlation is unavailable.
    session, ref = legacy.resolve_session(chain, session_ref, moment, provenance)
    protocol = db.session.get(Protocol, chain.configuration.id)
    if protocol is None:
        db.session.rollback()
        return session_ref
    recorded_state = db.session.get(State, session.id)
    if recorded_state is not None and recorded_state.release_ref != release_ref:
        db.session.rollback()
        return session_ref
    if recorded_state is None:
        db.session.add(State(session_id=session.id, release_ref=release_ref, observed_visible_ms=0,
                             next_opportunity_slot=1, next_due_visible_ms=protocol.first_opportunity_seconds * 1000, version=1))
    if db.session.get(Partition, chain.subject.id) is None:
        db.session.add(Partition(subject_id=chain.subject.id, partition_code="unassigned", assignment_reason="pending", version=1))
    count = 0
    for outcome in outcomes:
        if outcome.event_type not in _OUTCOMES:
            continue
        activity, action = _OUTCOMES[outcome.event_type]
        detail = ("committed" if outcome.detail in {"submitted", "completed", "saved", "started", "resumed", "sent", "posted", "results", "no_results"}
                  else "already_completed" if outcome.detail in {"already_completed", "already_submitted", "duplicate", "unchanged"}
                  else "rejected" if outcome.detail in {"rejected", "invalid", "unavailable", "closed", "refused", "expired"} or outcome.detail.startswith("rejected_")
                  else "failed" if outcome.detail in {"failed", "error"} else "unknown")
        if outcome.event_type == "lesson_completion" and outcome.detail in {"undone", "unmarked"}:
            action, detail = "undo_complete", "committed"
        db.session.add(Event(event_uid=str(uuid.uuid4()), outcome_ref=str(uuid.uuid4()),
            session_id=session.id, source="server", event_type="workflow_outcome",
            page_code=activity, context_code=hints.get("context_code", activity), activity_code=activity,
            action_code=action, detail_code=detail, occurred_at_ms=moment, received_at_ms=moment,
            action_ref=hints.get("action_ref"), attempt_ref=hints.get("attempt_ref"),
            context_ref=hints.get("context_ref"), confirmed_progress=detail == "committed"
            and (activity, action) in {("lesson", "complete"), ("assignment", "submit"), ("quiz", "submit"), ("listening", "complete"), ("speaking", "submit")}))
        count += 1
    if count:
        session.events_accepted += count
        session.last_seen_at_ms = max(session.last_seen_at_ms, moment)
    db.session.commit()
    return ref
