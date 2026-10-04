"""Pure validation of collector batches against the event dictionary
(Phase 6 replacement).

No Flask, no ORM, no I/O. :func:`parse_batch` decides whether a request body
is a well-formed batch at all; :func:`validate_event` decides, per event,
whether it matches exactly one declared event type. Anything not declared in
``research_event_dictionary`` -- an unknown key, type, page, element or
detail code, a value of the wrong type or outside its range -- is refused,
never coerced, truncated or stored partially.

A batch-level failure rejects the whole batch; an event-level failure rejects
that event only (it is counted as invalid, never stored).
"""

import json
import uuid
from collections import namedtuple

from app.services.research_event_dictionary import (
    BATCH_KEYS,
    CLIENT_EVENT_KEYS,
    CLIENT_REQUIRED_KEYS,
    ELEMENT_IDS,
    EVENT_SPECS,
    FORBIDDEN,
    MAX_BATCH_BYTES,
    MAX_BATCH_EVENTS,
    MAX_DROPPED_REPORT,
    MAX_SEQUENCE,
    PAGE_IDS,
    POSITIONAL_ELEMENTS,
    PROGRESS_PAGE_IDS,
    REQUIRED,
    SOURCE_CLIENT,
    SUPPORTED_EVENT_SCHEMA_VERSIONS,
)

# Batch-level error codes.
TOO_LARGE = "too_large"
MALFORMED = "malformed"
UNKNOWN_FIELD = "unknown_field"
UNSUPPORTED_SCHEMA = "unsupported_schema"
BAD_EVENT_COUNT = "bad_event_count"

#: The largest epoch-millisecond value accepted (JavaScript's safe integer).
_MAX_EPOCH_MS = 2**53 - 1

Batch = namedtuple("Batch", "schema sent_at events dropped replay")
ParsedEvent = namedtuple(
    "ParsedEvent",
    "event_uid event_type page_id page_view_ref tab_ref sequence_number client_ts_ms "
    "element_id detail_code count_value duration_ms position_value",
)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_uuid(value):
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def parse_batch(raw):
    """``(Batch, None)`` or ``(None, error_code)`` for one request body."""
    if raw is None or len(raw) > MAX_BATCH_BYTES:
        return None, TOO_LARGE
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, MALFORMED
    if not isinstance(body, dict):
        return None, MALFORMED
    if set(body) - BATCH_KEYS:
        return None, UNKNOWN_FIELD
    if not {"schema", "sent_at", "events"} <= set(body):
        return None, MALFORMED
    if body["schema"] not in SUPPORTED_EVENT_SCHEMA_VERSIONS:
        return None, UNSUPPORTED_SCHEMA
    sent_at = body["sent_at"]
    if not _is_int(sent_at) or not 0 < sent_at <= _MAX_EPOCH_MS:
        return None, MALFORMED
    events = body["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_BATCH_EVENTS:
        return None, BAD_EVENT_COUNT
    dropped = body.get("dropped", 0)
    if not _is_int(dropped) or not 0 <= dropped <= MAX_DROPPED_REPORT:
        return None, MALFORMED
    replay = body.get("replay", False)
    if not isinstance(replay, bool):
        return None, MALFORMED
    return Batch(body["schema"], sent_at, events, dropped, replay), None


def _int_field(spec_field, raw, key):
    """``(ok, value)`` for one bounded integer field."""
    present = key in raw
    if spec_field.presence == FORBIDDEN:
        return (not present), None
    if not present:
        return spec_field.presence != REQUIRED, None
    value = raw[key]
    if not _is_int(value) or not spec_field.minimum <= value <= spec_field.maximum:
        return False, None
    return True, value


def validate_event(raw):
    """A :class:`ParsedEvent` for one declared client event, else ``None``."""
    if not isinstance(raw, dict):
        return None
    keys = set(raw)
    if keys - CLIENT_EVENT_KEYS or not CLIENT_REQUIRED_KEYS <= keys:
        return None
    spec = EVENT_SPECS.get(raw["type"]) if isinstance(raw["type"], str) else None
    if spec is None or spec.source != SOURCE_CLIENT:
        return None
    if not (_is_uuid(raw["id"]) and _is_uuid(raw["view"]) and _is_uuid(raw["tab"])):
        return None
    if raw["page"] not in PAGE_IDS:
        return None
    if not _is_int(raw["t"]) or not 0 < raw["t"] <= _MAX_EPOCH_MS:
        return None
    if not _is_int(raw["seq"]) or not 1 <= raw["seq"] <= MAX_SEQUENCE:
        return None

    element = raw.get("element")
    if spec.element == FORBIDDEN:
        if "element" in raw:
            return None
    elif element is None:
        if spec.element == REQUIRED:
            return None
    else:
        allowed = spec.elements if spec.elements is not None else ELEMENT_IDS
        if not isinstance(element, str) or element not in allowed:
            return None

    detail = raw.get("detail")
    if spec.detail == FORBIDDEN:
        if "detail" in raw:
            return None
    elif detail is None:
        if spec.detail == REQUIRED:
            return None
    elif not isinstance(detail, str) or detail not in spec.details:
        return None

    values = {}
    for key in ("count", "duration_ms", "position"):
        ok, value = _int_field(getattr(spec, key), raw, key)
        if not ok:
            return None
        values[key] = value

    # Context rules the per-field declarations cannot express alone.
    if spec.name == "control_click" and values["position"] is not None \
            and element not in POSITIONAL_ELEMENTS:
        return None
    if spec.name == "page_view":
        progress = (values["position"], values["count"])
        if any(v is not None for v in progress):
            if raw["page"] not in PROGRESS_PAGE_IDS or None in progress:
                return None
            if values["position"] > values["count"]:
                return None

    return ParsedEvent(
        event_uid=raw["id"],
        event_type=spec.name,
        page_id=raw["page"],
        page_view_ref=raw["view"],
        tab_ref=raw["tab"],
        sequence_number=raw["seq"],
        client_ts_ms=raw["t"],
        element_id=element,
        detail_code=detail,
        count_value=values["count"],
        duration_ms=values["duration_ms"],
        position_value=values["position"],
    )


def is_uuid(value):
    return _is_uuid(value)
