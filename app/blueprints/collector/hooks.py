"""Request-level glue between the LMS and the research collector
(Phase 6 replacement).

- :func:`collector_view` -- what the shared portal layout needs to run the
  collector in the background, and to hold the optional feedback question,
  on every Student page of an eligible Student, or ``None``. The eligibility
  preview is memoised per request, and it **fails closed**: any error means
  "render nothing", never a broken page. It renders no visible research
  notice or indicator of any kind; v2 additionally prepares server presence
  in an isolated transaction before signing its scope. The only Student-facing
  research element is the feedback question itself, shown only when sampled.
- :func:`note_outcome` -- a Student route states a server-confirmed outcome.
  It only appends to a per-request list: no query, no write, nothing that can
  fail an academic operation.
- :func:`flush_outcomes` -- an ``after_request`` hook that hands the noted
  outcomes to ``research_collection.record_outcomes`` after the route has
  already committed or rolled back. It never raises and never changes the
  response.

The authenticated account id is read from Flask-Login's session value, never
from a possibly expired ``current_user``, so none of this reloads a user row
or opens a read ahead of a route's locks.
"""

from flask import current_app, g, request, session, url_for
from flask_wtf.csrf import generate_csrf

from app.models import CAUSE_COLUMNS, RATING_ANCHORS, User, UserRole
from app.services import research_collection as collection
from app.services.research_event_dictionary import (
    EVENT_SCHEMA_VERSION,
    EVENT_SPECS,
    MAX_BATCH_EVENTS,
    PROGRESS_PAGE_IDS,
    SOURCE_SERVER,
    page_id_for_endpoint,
)
from app.services.research_delivery_scope import issue_delivery_scope
from app.services.research_event_validation import is_uuid
from app.services.research_scope import collection_context

#: The signed-cookie key naming the browser's research session.
SESSION_REF_KEY = "research_session_ref"

HEARTBEAT_MS = 30_000
FLUSH_MS = 15_000
MAX_BUFFER = 200
MAX_NOTED_OUTCOMES = 5

_STUDENT = UserRole.STUDENT.value


def authenticated_user_id():
    """The logged-in account id for this request, or ``None``.

    Only an account Flask-Login actually loaded and accepted this request
    counts (``g._login_user`` is a :class:`User`); the id itself comes from
    the session value, so no ORM attribute is touched.
    """
    if not isinstance(g.get("_login_user"), User):
        return None
    raw = session.get("_user_id")
    try:
        return int(str(raw).split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def cached_context(user_id):
    """The collection context, computed at most once per **request** --
    memoised on the request object, never on ``g``, whose lifetime can span
    several requests when an application context is reused."""
    cache = request.environ.setdefault("research.context", {})
    if user_id not in cache:
        cache[user_id] = collection_context(user_id)
    return cache[user_id]


def deployment_provenance():
    return current_app.config.get("RESEARCH_DATA_PROVENANCE", "development")


def tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _prepare_v2_presence(context):
    """Isolate the presence write from the route's SQLAlchemy transaction."""
    if "research.v2.prepared_ref" in request.environ:
        return request.environ["research.v2.prepared_ref"]
    from app.extensions import db
    from app.services.research_v2_collection import prepare_presence
    from app.blueprints.collector.routes_v2 import source_release

    # Capture primitives while the parent context is active. In particular,
    # use the login's accepted auth version, not a later unlocked DB value.
    login_version = int(str(session.get("_user_id", "")).split(".", 1)[1])
    if login_version != context.auth_version:
        return None
    app = current_app._get_current_object()
    values = dict(user_id=context.user_id, user_public_id=context.user_public_id,
                  auth_version=login_version, configuration_id=context.configuration_id,
                  configuration_public_id=context.configuration_public_id,
                  configuration_version=context.configuration_version,
                  session_ref=session.get(SESSION_REF_KEY),
                  provenance=deployment_provenance(), release_ref=source_release())
    ref = None
    # Flask-SQLAlchemy scopes db.session to the app-context identity. The
    # nested context has its own session, including on rollback/teardown.
    with app.app_context():
        try:
            ref = prepare_presence(**values)
        except Exception as exc:
            db.session.rollback()
            app.logger.warning("V2 presence preparation unavailable (%s)", type(exc).__name__)
    request.environ["research.v2.prepared_ref"] = ref
    return ref


def collector_view(progress_position=None, progress_total=None):
    """Template values for the collector on this page, or ``None``."""
    try:
        user = g.get("_login_user")
        if not isinstance(user, User) or user.role != _STUDENT:
            return None
        user_id = authenticated_user_id()
        if user_id is None:
            return None
        context = cached_context(user_id)
        if context is None:
            return None
        if context.event_schema_version == "natural-use-events.v2":
            if not current_app.config.get("RESEARCH_V2_ENABLED", False):
                return None
            from app.services.research_v2_pages import page_code
            from app.services.research_delivery_scope import issue_v2_scope
            from app.i18n import ui_language
            code = page_code(request.endpoint)
            if code is None:
                return None
            ref = _prepare_v2_presence(context)
            if ref is None:
                return None
            # Resolve inactivity/configuration successors before HMAC signing;
            # otherwise the first client batch would carry a stale reference.
            session[SESSION_REF_KEY] = ref
            return {"v2": True, "anchors": RATING_ANCHORS, "causes": CAUSE_COLUMNS,
                    "config": {"schema_version": context.event_schema_version,
                        "scope_token": issue_v2_scope(current_app.config["SECRET_KEY"],
                            context.user_public_id, context.auth_version, context.configuration_public_id,
                            context.configuration_version, session[SESSION_REF_KEY]),
                        "page_code": code, "context_code": code, "activity_code": code,
                        "locale": ui_language(), "csrf": generate_csrf(),
                        "batch_url": url_for("collector.events_v2"), "sample_url": url_for("collector.sample_v2"),
                        "clock_url": url_for("collector.clock_v2"),
                        "prompt_url": url_for("collector.prompt_action_v2", prompt_ref="PROMPT", action="ACTION"),
                        "policy": {"batch_size": 30, "max_buffer": 200, "flush_ms": FLUSH_MS}}}
        if context.event_schema_version != EVENT_SCHEMA_VERSION:
            return None
        page_id = page_id_for_endpoint(request.endpoint)
        if page_id is None:
            return None
        if not is_uuid(session.get(SESSION_REF_KEY)):
            session[SESSION_REF_KEY] = collection.new_session_ref()
        progress = None
        if page_id in PROGRESS_PAGE_IDS and _positive(progress_position) \
                and _positive(progress_total) and progress_position <= progress_total:
            progress = {"position": progress_position, "count": progress_total}
        view = {
            "lookback_phrase": lookback_phrase(context.policy["lookback_seconds"]),
            "anchors": RATING_ANCHORS,
            "causes": CAUSE_COLUMNS,
        }
        view["config"] = {
            "schema": EVENT_SCHEMA_VERSION,
            "deliveryScope": issue_delivery_scope(
                current_app.config["SECRET_KEY"], context.user_public_id,
                context.auth_version, context.configuration_public_id,
                context.configuration_version,
            ),
            "page": page_id,
            "progress": progress,
            "csrf": generate_csrf(),
            "eventsUrl": url_for("collector.events"),
            "promptUrl": url_for("collector.prompt_action", prompt_public_id="PROMPT",
                                 action="ACTION"),
            "heartbeatMs": HEARTBEAT_MS,
            "flushMs": FLUSH_MS,
            "batchSize": MAX_BATCH_EVENTS,
            "maxBuffer": MAX_BUFFER,
        }
        return view
    except Exception:
        current_app.logger.exception("The research collector could not be prepared")
        return None


def lookback_phrase(seconds):
    """How the question names its window, from the versioned configuration
    value -- so the wording always matches the window actually stored."""
    if seconds % 60 == 0:
        minutes = seconds // 60
        return "minute" if minutes == 1 else f"{_WORDS.get(minutes, minutes)} minutes"
    return f"{seconds} seconds"


_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
          9: "nine", 10: "ten"}


def _positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def note_outcome(event_type, detail, count=None, position=None):
    """Remember one server-confirmed outcome for :func:`flush_outcomes`.

    An undeclared type or detail is dropped (and logged), never stored.
    """
    spec = EVENT_SPECS.get(event_type)
    if spec is None or spec.source != SOURCE_SERVER or detail not in spec.details:
        current_app.logger.warning("Undeclared research outcome %r/%r ignored", event_type, detail)
        return
    outcomes = request.environ.setdefault("research.outcomes", [])
    if len(outcomes) < MAX_NOTED_OUTCOMES:
        outcomes.append(
            collection.Outcome(
                event_type, detail, count, position, page_id_for_endpoint(request.endpoint)
            )
        )


def flush_outcomes(response):
    """``after_request``: record noted outcomes. Never raises."""
    outcomes = request.environ.pop("research.outcomes", None)
    if not outcomes:
        return response
    try:
        user_id = authenticated_user_id()
        if user_id is None or cached_context(user_id) is None:
            return response
        context = cached_context(user_id)
        if context.event_schema_version == "natural-use-events.v2":
            if not current_app.config.get("RESEARCH_V2_ENABLED", False):
                return response
            from app.services import research_v2_collection
            from app.services.research_v2_dictionary import is_opaque_ref
            from app.services.research_v2_dictionary import CONTEXT_CODES
            from app.blueprints.collector.routes_v2 import source_release
            hints = {}
            for key in ("action_ref", "attempt_ref", "context_ref"):
                value = request.form.get("research_v2_" + key)
                if is_opaque_ref(value):
                    hints[key] = value
            code = request.form.get("research_v2_context_code")
            if code in CONTEXT_CODES:
                hints["context_code"] = code
            from app.services.research_delivery_scope import V2DeliveryProof
            proof = V2DeliveryProof(request.form.get("research_v2_scope_token"), current_app.config["SECRET_KEY"], session.get(SESSION_REF_KEY))
            ref = research_v2_collection.record_outcomes(user_id, session.get(SESSION_REF_KEY),
                outcomes, deployment_provenance(), source_release(), hints, proof=proof)
        else:
            ref = collection.record_outcomes(
                user_id, session.get(SESSION_REF_KEY), outcomes, deployment_provenance())
        if ref and ref != session.get(SESSION_REF_KEY):
            session[SESSION_REF_KEY] = ref
    except Exception as exc:
        from app.extensions import db
        db.session.rollback()
        current_app.logger.warning("Research outcome flush unavailable (%s)", type(exc).__name__)
    return response


def end_session_on_logout(user_id):
    """Close this browser's research session and forget its reference."""
    ref = session.pop(SESSION_REF_KEY, None)
    if user_id is not None and ref is not None:
        collection.end_session_on_logout(user_id, ref)
