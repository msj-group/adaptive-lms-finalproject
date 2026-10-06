"""The Student-side research collector endpoints (Phase 6 replacement)::

    POST  /collect/events
    POST  /collect/prompts/<prompt_public_id>/<action>    display | defer | respond

**Authentication and eligibility.** Every rule needs an authenticated, active
Student (JSON 401 / 403 otherwise -- never a login redirect a script would
follow). The acting Student comes from the session; the research session
comes from the signed session cookie; no request body can name either, and a
body that tries (an unknown field) is refused.

**CSRF.** These are ordinary CSRF-protected POSTs: the collector sends the
token in the ``X-CSRFToken`` header, including on unload through
``fetch(..., {keepalive: true})``. There is no CSRF exemption and no
``sendBeacon`` path.

**Bounds.** A body larger than the batch limit is refused before it is read;
the content type must be JSON; the endpoints are rate limited per account
(not per address: Students at the centre may share one public IP).

**Honesty of the answer.** When nothing may be collected for the Student
(excluded, no active configuration, paused, outside the period, suspended in
the meantime), the answer is ``{"collecting": false}`` with nothing stored,
and the collector stops. Responses are ``private, no-store``.
"""

import json
from functools import wraps

from flask import current_app, jsonify, request, session
from flask_limiter.util import get_remote_address
from flask_login import current_user

from app.blueprints.collector import collector_bp
from app.blueprints.collector.hooks import (
    SESSION_REF_KEY,
    deployment_provenance,
    tz_name,
)
from app.extensions import limiter
from app.models import UserRole
from app.services import research_collection as collection
from app.services import research_sampling as sampling
from app.services.research_delivery_scope import DeliveryProof
from app.services.research_event_dictionary import MAX_BATCH_BYTES
from app.services.research_event_validation import parse_batch

_STUDENT = UserRole.STUDENT.value
#: A prompt action body is tiny: a rating and at most five cause codes.
MAX_PROMPT_BODY_BYTES = 1024
PROMPT_ACTIONS = ("display", "defer", "respond")


def _account_key():
    """Rate-limit per signed-in account, not per address: Students at the
    centre may share one public IP, and a shared budget would silently drop
    everyone's data. Anonymous requests (refused anyway) fall back to the
    address."""
    raw = session.get("_user_id")
    return f"account:{str(raw).split('.', 1)[0]}" if raw else get_remote_address()


def _json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def _student_json(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return _json({"error": "authentication_required"}, 401)
        if current_user.role != _STUDENT:
            return _json({"error": "forbidden"}, 403)
        return view_func(*args, **kwargs)

    return wrapped


def _read_json_body(limit):
    """``(bytes, None)`` or ``(None, response)`` for a bounded JSON body."""
    if not request.is_json:
        return None, _json({"error": "unsupported_media_type"}, 415)
    length = request.content_length
    if length is None:
        return None, _json({"error": "length_required"}, 411)
    if length > limit:
        return None, _json({"error": "too_large"}, 413)
    request.max_content_length = limit
    return request.get_data(cache=False), None


def _user_id():
    return current_user.id


@collector_bp.post("/events")
@limiter.limit("120 per minute", key_func=_account_key)
@_student_json
def events():
    raw, refusal = _read_json_body(MAX_BATCH_BYTES)
    if refusal is not None:
        return refusal
    batch, error = parse_batch(raw)
    if error is not None:
        return _json({"error": error}, 413 if error == "too_large" else 400)
    result = collection.ingest_batch(
        _user_id(), session.get(SESSION_REF_KEY), batch, deployment_provenance(), tz_name(),
        delivery_proof=DeliveryProof(batch.delivery_scope, current_app.config["SECRET_KEY"]),
    )
    if result.status == collection.STALE_DELIVERY_SCOPE:
        return _json({"error": collection.STALE_DELIVERY_SCOPE}, 403)
    if result.status == collection.NOT_COLLECTING:
        return _json({"collecting": False})
    if result.status == collection.RETRY:
        return _json({"collecting": True, "retry": True}, 409)
    if result.session_ref != session.get(SESSION_REF_KEY):
        session[SESSION_REF_KEY] = result.session_ref
    return _json(
        {
            "collecting": True,
            "accepted": result.accepted,
            "duplicates": result.duplicates,
            "invalid": result.invalid,
            "late": result.late,
            "prompt": {"id": result.prompt_public_id} if result.prompt_public_id else None,
        }
    )


@collector_bp.post("/prompts/<prompt_public_id>/<action>")
@limiter.limit("30 per minute", key_func=_account_key)
@_student_json
def prompt_action(prompt_public_id, action):
    if action not in PROMPT_ACTIONS:
        return _json({"error": "not_found"}, 404)
    raw, refusal = _read_json_body(MAX_PROMPT_BODY_BYTES)
    if refusal is not None:
        return refusal
    try:
        body = json.loads(raw) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return _json({"error": "malformed"}, 400)
    ref = session.get(SESSION_REF_KEY)

    if action == "display":
        if body != {}:
            return _json({"error": "malformed"}, 400)
        status, reason = sampling.grant_display(_user_id(), ref, prompt_public_id, tz_name())
        return _prompt_response(status, {"granted": status == sampling.GRANTED, "reason": reason})

    if action == "defer":
        if not isinstance(body, dict) or set(body) != {"reason"}:
            return _json({"error": "malformed"}, 400)
        reason = body["reason"]
        if reason not in sampling.DEFERRAL_REASONS:
            return _json({"error": "malformed"}, 400)
        status = sampling.defer_display(_user_id(), ref, prompt_public_id, reason)
        return _prompt_response(status, {"deferred": status == sampling.DEFERRED})

    answer = sampling.parse_answer(body)
    if answer is None:
        return _json({"error": "malformed"}, 400)
    status = sampling.respond(_user_id(), ref, prompt_public_id, answer)
    return _prompt_response(status, {"recorded": status in (sampling.ANSWERED,
                                                            sampling.DISMISSED)})


def _prompt_response(status, payload):
    if status == sampling.NOT_COLLECTING:
        return _json({"collecting": False})
    if status == sampling.NOT_FOUND:
        return _json({"error": "not_found"}, 404)
    if status in (sampling.ALREADY, sampling.DIFFERENT, sampling.LATE, sampling.UNAVAILABLE):
        payload = dict(payload, status=status)
        return _json(dict(payload, collecting=True), 409)
    return _json(dict(payload, collecting=True, status=status))


@collector_bp.after_request
def _private_answers(response):
    """``private, no-store`` on every answer of this blueprint's rules."""
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response
