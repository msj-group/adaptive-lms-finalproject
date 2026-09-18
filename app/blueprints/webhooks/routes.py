"""The public provider webhook endpoint (Phase 5 / M07).

One URL rule::

    POST  /webhooks/payments/mock      a signed Mock/Sandbox provider event

**It stands for an external provider's server-to-server call**, so it needs no
login and is the application's one explicit CSRF exception; its own
HMAC-SHA256 signature over the exact raw body is what authenticates it
(:mod:`app.services.mock_payment_provider`). It exists only while the
Mock/Sandbox provider is enabled -- otherwise it is a plain 404 -- and it never
reads the session or the signed-in account.

**Strict and quiet.** Only ``application/json`` with a declared length of at
most :data:`~app.services.mock_payment_provider.MAX_WEBHOOK_BODY_BYTES` is
read; the raw bytes are verified before they are parsed. Every refusal --
wrong content type, oversized, unsigned, badly signed, stale, future-dated,
malformed, duplicate or unknown key, unknown reference, or an event id reused
with a different body -- gets the same generic ``400`` and stores nothing. No
response repeats the secret, the signature, the provider reference or why a
delivery was refused.

**Success only after a commit.** ``200`` is returned only once the event's
result -- a confirmation, a failure, a duplicate, an ignored or a
reconciliation-required event, or the stored result of a re-delivery -- is
committed. A transient database failure is rolled back and answered ``503``,
so the provider may deliver again. Every response carries
``Cache-Control: no-store``.
"""

from flask import abort, current_app, jsonify, request

from app.blueprints.webhooks import webhooks_bp
from app.extensions import csrf, limiter
from app.services.mock_payment_provider import MAX_WEBHOOK_BODY_BYTES, WEBHOOK_HEADERS
from app.services.payment_webhooks import (
    WebhookRejected,
    WebhookRetry,
    process_provider_webhook,
)

csrf.exempt(webhooks_bp)

_JSON = "application/json"


def _answer(status, http_status):
    response = jsonify({"status": status})
    response.status_code = http_status
    response.headers["Cache-Control"] = "no-store"
    return response


def _rejected():
    return _answer("rejected", 400)


@webhooks_bp.post("/payments/mock")
@limiter.limit("120 per minute")
def mock_payment_webhook():
    """Verify, record and apply one signed Mock/Sandbox provider event."""
    settings = current_app.extensions["payment_provider"]
    if not settings.mock_enabled:
        abort(404)
    if request.mimetype != _JSON:
        return _rejected()
    length = request.content_length
    if length is None or not 0 < length <= MAX_WEBHOOK_BODY_BYTES:
        return _rejected()
    body = request.get_data(cache=False)
    if len(body) != length:
        return _rejected()
    headers = {name: request.headers.get(name) for name in WEBHOOK_HEADERS}
    try:
        process_provider_webhook(
            settings.provider,
            body,
            headers,
            tz_name=current_app.config.get("APP_TIMEZONE", "UTC"),
        )
    except WebhookRejected:
        return _rejected()
    except WebhookRetry:
        return _answer("retry", 503)
    return _answer("ok", 200)
