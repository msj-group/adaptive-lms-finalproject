"""CSRF protected, account-scoped v2 JSON endpoints. No database activation."""
import json
import uuid
from flask import current_app, session
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from app.blueprints.collector import collector_bp
from app.blueprints.collector.routes import _account_key, _json, _read_json_body, _student_json
from app.blueprints.collector.hooks import SESSION_REF_KEY, cached_context, deployment_provenance, tz_name
from app.extensions import db, limiter
from app.services.research_delivery_scope import V2DeliveryProof
from app.services import research_v2_collection as collection
from app.services.research_v2_dictionary import EVENT_SCHEMA_VERSION, MAX_BATCH_BYTES


def _unresolved(code, body=None):
    """Bounded technical receipt only; no actor, subject, content or IP stored."""
    from app.models.research_v2 import ResearchV2DeliveryReceipt
    from app.services.research_v2_dictionary import is_opaque_ref
    body = body if isinstance(body, dict) else {}
    events = body.get("events")
    count = min(len(events), 100000) if isinstance(events, list) else 0
    try:
        db.session.rollback()
        db.session.add(ResearchV2DeliveryReceipt(receipt_uid=str(uuid.uuid4()),
            scope_code="unresolved", result_code=code, received_at_ms=collection.now_ms(),
            batch_ref=body.get("batch_ref") if is_opaque_ref(body.get("batch_ref")) else None,
            attempt_ref=body.get("attempt_ref") if is_opaque_ref(body.get("attempt_ref")) else None,
            events_received=count, events_accepted=0, events_duplicate=0, events_invalid=count,
            events_late=0, events_dropped_client=0))
        db.session.commit()
    except Exception:
        db.session.rollback()


def source_release():
    key = "research_v2_source_digest"
    if key not in current_app.extensions:
        current_app.extensions[key] = collection.release_digest(current_app.root_path)
    return current_app.extensions[key]


def _dispatch(operation, prompt_ref=None, action=None):
    if not current_app.config.get("RESEARCH_V2_ENABLED", False):
        return _json({"collecting": False})
    raw, refusal = _read_json_body(MAX_BATCH_BYTES if operation == "events" else 2048)
    if refusal is not None:
        if operation == "events":
            _unresolved("oversized" if refusal.status_code == 413 else "invalid")
        return refusal
    body = None
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise ValueError("invalid_version")
        proof = V2DeliveryProof(body.get("scope_token"), current_app.config["SECRET_KEY"], session.get(SESSION_REF_KEY))
        if operation == "clock":
            if set(body) - {"schema_version", "scope_token", "client_at_ms"}:
                raise ValueError("invalid_clock")
            context = cached_context(current_user.id)
            valid = context is not None and context.event_schema_version == EVENT_SCHEMA_VERSION and proof.matches(
                body.get("scope_token"), context.user_public_id, context.auth_version,
                context.configuration_public_id, context.configuration_version)
            return _json({"collecting": bool(valid), "scope_valid": bool(valid), "server_now_ms": collection.now_ms()})
        args = (current_user.id, session.get(SESSION_REF_KEY), body, proof, deployment_provenance(), source_release())
        if operation == "events":
            result, ref = collection.ingest(*args)
        elif operation == "sample":
            result, ref = collection.sample(*args, tz_name())
        else:
            result, ref = collection.prompt_action(args[0], args[1], prompt_ref, action, *args[2:], tz_name())
        if ref and ref != session.get(SESSION_REF_KEY):
            session[SESSION_REF_KEY] = ref
        if operation == "events" and result.get("collecting") is False:
            _unresolved("version_mismatch" if result.get("scope_valid") is False else "ineligible", body)
        return _json(result)
    except (ValueError, TypeError, UnicodeError):
        db.session.rollback()
        if operation == "events":
            _unresolved("invalid", body)
        return _json({"error": "invalid_request"}, 400)
    except IntegrityError:
        db.session.rollback()
        return _json({"collecting": True, "retry": True}, 409)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning("V2 collector unavailable (%s)", type(exc).__name__)
        return _json({"error": "temporarily_unavailable"}, 503)


@collector_bp.post("/v2/events")
@limiter.limit("120 per minute", key_func=_account_key)
@_student_json
def events_v2():
    return _dispatch("events")


@collector_bp.post("/v2/sample")
@limiter.limit("20 per minute", key_func=_account_key)
@_student_json
def sample_v2():
    return _dispatch("sample")


@collector_bp.post("/v2/clock")
@limiter.limit("20 per minute", key_func=_account_key)
@_student_json
def clock_v2():
    return _dispatch("clock")


@collector_bp.post("/v2/prompts/<prompt_ref>/<action>")
@limiter.limit("30 per minute", key_func=_account_key)
@_student_json
def prompt_action_v2(prompt_ref, action):
    return _dispatch("prompt", prompt_ref, action)
