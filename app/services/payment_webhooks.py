"""Verified provider entry point and result types for general student accounts."""
import time
from collections import namedtuple
from app.services.schedule_occurrences import utc_reference_now

class WebhookRejected(Exception):
    """The delivery is not authentic, fresh, well-formed, known, or it reuses
    a stored event id with a different body. Nothing was stored. The message
    is internal; the endpoint answers generically."""


class WebhookRetry(Exception):
    """A transient failure -- a lost race, a lock timeout, a year that turned
    while waiting. Everything was rolled back and nothing was committed; the
    provider may deliver the same event again."""


WebhookResult = namedtuple('WebhookResult', 'outcome event_public_id receipt_number redelivered')


def _trusted_now():
    """The LMS's own naive-UTC whole-second moment. Never the provider's."""
    return utc_reference_now().replace(microsecond=0)


def process_provider_webhook(provider, raw_body, headers, *, tz_name, clock=_trusted_now, epoch=time.time):
    from app.services.general_payment_webhooks import process_general_webhook
    return process_general_webhook(provider, raw_body, headers, tz_name=tz_name, clock=clock, epoch=epoch)
