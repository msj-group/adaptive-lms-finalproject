"""The natural-use research event dictionary (Phase 6 replacement).

This module is the **single source of truth** for what the research collector
may record. The ingestion validator (``app/services/research_ingestion.py``),
the server outcome recorder (``app/services/research_outcomes.py``), the
database CHECK on ``research_events.event_type``, the export data dictionary
and ``docs/PHASE6_NATURAL_USE_RESEARCH.md`` are all derived from, or tested
against, the declarations below. An event type, field, page identifier,
element identifier or detail code that is not declared here is rejected.

Flask-independent and side-effect free: plain data and pure functions.

**What an event may carry.** Every event is a small, typed record: an event
type, a server-validated page identifier (a Flask endpoint name from an
allowlist, never a URL), an optional allowlisted element identifier, an
optional closed-set ``detail`` code, and at most three bounded integers
(``count``, ``duration_ms``, ``position``). Client events add an idempotency
id, the random page-view and tab references the collector generates, a
per-page sequence number and the client clock reading.

**What no event can carry**, because no field exists for it: typed text,
keystrokes or key counts, passwords, answer content or option identity,
search terms, message or discussion bodies, file names or contents, audio,
grades, payment data, URLs or query strings, DOM text or HTML, screen
coordinates, user-agent strings, IP addresses or any browser fingerprint.
"""

from dataclasses import dataclass, field

#: The schema a configuration version and every client batch must name.
EVENT_SCHEMA_VERSION = "natural-use-events.v1"
SUPPORTED_EVENT_SCHEMA_VERSIONS = (EVENT_SCHEMA_VERSION,)

SOURCE_CLIENT = "client"
SOURCE_SERVER = "server"

REQUIRED = "required"
OPTIONAL = "optional"
FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class IntField:
    """One bounded integer field on an event type."""

    presence: str
    minimum: int = 0
    maximum: int = 0
    unit: str = ""


_NO_INT = IntField(FORBIDDEN)


@dataclass(frozen=True)
class EventSpec:
    name: str
    source: str
    category: str
    reason: str
    element: str = FORBIDDEN
    #: The element identifiers this type accepts, when ``element`` is not
    #: forbidden. ``None`` means "any identifier in :data:`ELEMENT_IDS`".
    elements: tuple = None
    detail: str = FORBIDDEN
    details: tuple = ()
    count: IntField = _NO_INT
    duration_ms: IntField = _NO_INT
    position: IntField = _NO_INT
    #: Server outcome details that end a natural activity. Such an outcome
    #: arms the activity-end sampling trigger (``research_sampling``).
    activity_end_details: frozenset = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Pages: Flask endpoint names of Student HTML pages. Never a URL.
# ---------------------------------------------------------------------------

#: Every Student-facing HTML page the collector may run on. File-serving
#: endpoints (material bytes, listening and speaking audio) are deliberately
#: absent: they are not pages, and their requests are never observed.
PAGE_IDS = (
    "student.dashboard",
    "student.activities",
    "student.records",
    "student.episode_records",
    "student.group_units",
    "student.lesson_detail",
    "student.search",
    "student.assignments_list",
    "student.assignment_detail",
    "student.quiz_list",
    "student.quiz_detail",
    "student.quiz_question",
    "student.quiz_result",
    "student.listening_list",
    "student.listening_detail",
    "student.listening_question",
    "student.listening_result",
    "student.speaking_list",
    "student.speaking_detail",
    "student.speaking_record",
    "student.speaking_receipt",
    "student.attendance_list",
    "student.grades_list",
    "student.group_grades",
    "student.announcements_list",
    "student.announcement_detail",
    "student.calendar",
    "student.discussions_overview",
    "student.discussions_group",
    "student.discussions_topic",
    "messages.inbox",
    "messages.new_thread",
    "messages.thread",
    "notifications.inbox",
)

#: POST endpoints that re-render a page on a validation failure, mapped to
#: the page they show. The collector names the page, not the action.
PAGE_ID_ALIASES = {
    "student.assignment_submit": "student.assignment_detail",
    "student.speaking_submit": "student.speaking_record",
    "student.discussions_reply": "student.discussions_topic",
    "messages.create_thread": "messages.new_thread",
    "messages.reply": "messages.thread",
}

#: Pages whose progress context (question position and question count) is
#: server-rendered into the page and may be reported on ``page_view``.
PROGRESS_PAGE_IDS = frozenset({"student.quiz_question", "student.listening_question"})


def page_id_for_endpoint(endpoint):
    """The allowlisted page identifier for a Flask endpoint, or ``None``."""
    if endpoint in PAGE_IDS:
        return endpoint
    return PAGE_ID_ALIASES.get(endpoint)


# ---------------------------------------------------------------------------
# Route classification: every Student-facing route is deliberately classified.
# ---------------------------------------------------------------------------

#: The blueprints that make up the authenticated Student platform. Messages
#: and notifications are shared with Teachers, but their pages are
#: Student-facing too; the collector runs on them only for a Student.
STUDENT_PLATFORM_BLUEPRINTS = ("student", "messages", "notifications")

#: Student routes that are **not pages** and are never observed: they serve
#: file or audio bytes, including every Range request a player makes.
#: Observing them would add nothing but the download itself; the click that
#: opened them is already a ``control_click``.
TECHNICAL_EXCLUSIONS = {
    "student.material_open": "Serves lesson material bytes, not a page.",
    "student.material_download": "Serves lesson material bytes, not a page.",
    "student.listening_audio": "Serves listening audio bytes, including Range requests.",
    "student.speaking_audio": "Serves the Student's own recording, including Range requests.",
    "student.speaking_audio_download": "Serves the Student's own recording as a download.",
}

#: Student routes whose result the server confirms as an outcome event.
OUTCOME_ENDPOINTS = {
    "student.lesson_mark_complete": "lesson_completion",
    "student.lesson_undo_complete": "lesson_completion",
    "student.search": "search",
    "student.assignment_submit": "assignment_submission",
    "student.quiz_start": "quiz_start",
    "student.quiz_answer": "quiz_answer",
    "student.quiz_submit": "quiz_submission",
    "student.listening_start": "listening_start",
    "student.listening_answer": "listening_answer",
    "student.listening_submit": "listening_submission",
    "student.speaking_submit": "speaking_submission",
    "student.discussions_reply": "discussion_reply",
    "messages.create_thread": "message_send",
    "messages.reply": "message_send",
}

#: Student POST routes observed only by the browser (``form_submit`` and the
#: next ``page_view``), with no server outcome declared for them.
CLIENT_OBSERVED_POSTS = {
    "notifications.open_notification": "Opening a notification is a navigation; "
                                       "the next page view records it.",
    "notifications.read_notification": "Marking one notification read changes no "
                                       "learning state.",
    "notifications.read_all": "Marking all notifications read changes no learning state.",
}

#: A coarse classification of pages, used **only** to report coverage in the
#: Researcher workspace. It never narrows collection.
CORE_AREA = "core"
REPORTING_AREAS = (
    "learning", "search", "assignments", "quizzes", "listening", "speaking",
    "communication", "records",
)

PAGE_AREAS = {
    "student.dashboard": CORE_AREA,
    "student.activities": CORE_AREA,
    "student.records": "records",
    "student.episode_records": "records",
    "student.group_units": "learning",
    "student.lesson_detail": "learning",
    "student.search": "search",
    "student.assignments_list": "assignments",
    "student.assignment_detail": "assignments",
    "student.quiz_list": "quizzes",
    "student.quiz_detail": "quizzes",
    "student.quiz_question": "quizzes",
    "student.quiz_result": "quizzes",
    "student.listening_list": "listening",
    "student.listening_detail": "listening",
    "student.listening_question": "listening",
    "student.listening_result": "listening",
    "student.speaking_list": "speaking",
    "student.speaking_detail": "speaking",
    "student.speaking_record": "speaking",
    "student.speaking_receipt": "speaking",
    "student.attendance_list": "records",
    "student.grades_list": "records",
    "student.group_grades": "records",
    "student.announcements_list": "records",
    "student.announcement_detail": "records",
    "student.calendar": "records",
    "student.discussions_overview": "communication",
    "student.discussions_group": "communication",
    "student.discussions_topic": "communication",
    "messages.inbox": "communication",
    "messages.new_thread": "communication",
    "messages.thread": "communication",
    "notifications.inbox": "communication",
}


# ---------------------------------------------------------------------------
# Elements: identifiers declared on controls with ``data-research-id``.
# ---------------------------------------------------------------------------

NAVIGATION_ELEMENTS = (
    "nav_dashboard", "nav_assignments", "nav_quizzes", "nav_listening", "nav_speaking",
    "nav_activities",
    "nav_records",
    "nav_attendance", "nav_grades", "nav_announcements", "nav_calendar", "nav_search",
    "nav_messages", "nav_discussions", "nav_notifications", "nav_logout",
)

ACTION_ELEMENTS = (
    # Learning
    "lesson_open", "lesson_back", "lesson_complete", "lesson_undo_complete",
    "material_open", "material_download", "material_external_link",
    # Search
    "search_submit", "search_result", "search_filter",
    # Assignments
    "activity_open", "activity_filter",
    "assignment_open", "assignment_submit",
    # Quizzes
    "quiz_open", "quiz_start", "quiz_option", "quiz_save", "quiz_save_next",
    "quiz_previous", "quiz_next", "quiz_submit", "quiz_submit_confirm",
    # Listening
    "listening_open", "listening_start", "listening_option", "listening_save",
    "listening_save_next", "listening_previous", "listening_next", "listening_submit",
    "listening_submit_confirm",
    # Speaking
    "speaking_open", "speaking_record_start", "speaking_record_stop",
    "speaking_record_again", "speaking_file_select", "speaking_submit",
    # Audio player
    "audio_toggle", "audio_replay", "audio_speed",
    # Communication
    "discussion_open", "discussion_reply", "message_open", "message_new", "message_send",
    "notification_open", "notification_read_all",
    # Structural fallbacks: a control with no declared identifier. They say
    # what *kind* of control was used and nothing else -- no text, no URL.
    "other_link", "other_button", "other_control",
)

#: Inputs whose *change* is observable. Only the fact of a change is sent,
#: never the value, the option chosen or the file selected.
INPUT_ELEMENTS = (
    "quiz_option", "listening_option", "quiz_submit_confirm", "listening_submit_confirm",
    "speaking_file_select", "search_filter", "activity_filter",
)

#: Forms whose submission attempts and client-side validation failures are
#: counted. Never a field value.
FORM_ELEMENTS = (
    "lesson_complete", "lesson_undo_complete", "search_submit", "assignment_submit",
    "quiz_start", "quiz_save", "quiz_submit", "listening_start", "listening_save",
    "listening_submit", "speaking_submit", "discussion_reply", "message_send",
    "notification_open", "notification_read_all", "other_form",
)

#: Elements for which ``position`` (a 1-based rank) is meaningful.
POSITIONAL_ELEMENTS = frozenset({"search_result"})

#: What kind of media an ``media_event`` observed -- never the media itself.
MEDIA_KINDS = ("listening_audio", "lesson_audio", "lesson_video", "speaking_playback")

ELEMENT_IDS = tuple(
    dict.fromkeys(
        NAVIGATION_ELEMENTS + ACTION_ELEMENTS + INPUT_ELEMENTS + FORM_ELEMENTS
        + MEDIA_KINDS + ("speaking_recorder",)
    )
)

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

_DAY_MS = 24 * 60 * 60 * 1000

CLIENT_EVENTS = (
    EventSpec(
        "page_view", SOURCE_CLIENT, "navigation",
        "Page and activity transitions; the device-size class gives activity and "
        "device coverage without a fingerprint.",
        detail=REQUIRED, details=("narrow", "medium", "wide"),
        count=IntField(OPTIONAL, 1, 500, "questions in the activity"),
        position=IntField(OPTIONAL, 1, 500, "1-based question position"),
    ),
    EventSpec(
        "page_leave", SOURCE_CLIENT, "navigation",
        "Dwell time on a page, counting only the time the page was visible.",
        duration_ms=IntField(REQUIRED, 0, _DAY_MS, "milliseconds visible"),
    ),
    EventSpec(
        "visibility_hidden", SOURCE_CLIENT, "attention",
        "The tab stopped being visible. Ends the continuous observation run, so "
        "hidden time is never counted as observed.",
    ),
    EventSpec(
        "visibility_visible", SOURCE_CLIENT, "attention",
        "The tab became visible again.",
    ),
    EventSpec(
        "heartbeat", SOURCE_CLIENT, "attention",
        "Proves continuous observation while the page is visible, and says whether "
        "any input, media playback or neither happened in the interval. Contextual "
        "only: inactivity is never a frustration label.",
        detail=REQUIRED, details=("active", "idle", "media_playing"),
    ),
    EventSpec(
        "control_click", SOURCE_CLIENT, "interaction",
        "A meaningful control was used, identified by its declared identifier or by "
        "its structural kind.",
        element=REQUIRED, elements=NAVIGATION_ELEMENTS + ACTION_ELEMENTS,
        position=IntField(OPTIONAL, 1, 500, "1-based rank, search results only"),
    ),
    EventSpec(
        "repeated_click", SOURCE_CLIENT, "interaction",
        "A bounded aggregate of rapid repeated clicks on one control.",
        element=REQUIRED, elements=NAVIGATION_ELEMENTS + ACTION_ELEMENTS,
        count=IntField(REQUIRED, 2, 50, "clicks in the burst"),
    ),
    EventSpec(
        "non_interactive_click", SOURCE_CLIENT, "interaction",
        "A click on something that is not a control. No position, target or text.",
    ),
    EventSpec(
        "input_change", SOURCE_CLIENT, "interaction",
        "An answer option, confirmation box, filter or file field changed. The value "
        "is never sent.",
        element=REQUIRED, elements=INPUT_ELEMENTS,
    ),
    EventSpec(
        "form_submit", SOURCE_CLIENT, "forms",
        "A form submission attempt. Whether it succeeded is a separate server "
        "outcome.",
        element=REQUIRED, elements=FORM_ELEMENTS,
    ),
    EventSpec(
        "form_invalid", SOURCE_CLIENT, "forms",
        "The browser refused a submission because fields were invalid.",
        element=REQUIRED, elements=FORM_ELEMENTS,
        count=IntField(REQUIRED, 1, 50, "invalid fields"),
    ),
    EventSpec(
        "media_event", SOURCE_CLIENT, "media",
        "Playback of existing educational media. The media itself is never copied.",
        element=REQUIRED, elements=MEDIA_KINDS,
        detail=REQUIRED, details=("play", "pause", "ended", "seek", "rate_change"),
        position=IntField(OPTIONAL, 0, 86_400, "whole seconds into the media"),
    ),
    EventSpec(
        "recorder_state", SOURCE_CLIENT, "recording",
        "The speaking recorder changed state. No audio is sent.",
        element=REQUIRED, elements=("speaking_recorder",),
        detail=REQUIRED, details=("requesting", "recording", "preview", "uploading", "error"),
    ),
    EventSpec(
        "recorder_failure", SOURCE_CLIENT, "recording",
        "Why a recording could not start or continue.",
        element=REQUIRED, elements=("speaking_recorder",),
        detail=REQUIRED, details=("permission_denied", "unsupported", "recorder_error"),
    ),
)

SERVER_EVENTS = (
    EventSpec(
        "lesson_completion", SOURCE_SERVER, "learning",
        "Server-confirmed result of marking a lesson complete or undoing it.",
        detail=REQUIRED, details=("completed", "undone", "unchanged", "rejected"),
        activity_end_details=frozenset({"completed"}),
    ),
    EventSpec(
        "search", SOURCE_SERVER, "search",
        "A submitted search and how many results it found. Never the search terms.",
        detail=REQUIRED, details=("results", "no_results"),
        count=IntField(REQUIRED, 0, 1000, "results shown"),
    ),
    EventSpec(
        "assignment_submission", SOURCE_SERVER, "assignments",
        "Server-confirmed outcome of an assignment submission. Never the answer.",
        detail=REQUIRED,
        details=("submitted", "duplicate", "rejected_invalid", "rejected_closed",
                 "rejected_stale", "failed"),
        activity_end_details=frozenset({"submitted"}),
    ),
    EventSpec(
        "quiz_start", SOURCE_SERVER, "quizzes",
        "A quiz attempt was started, resumed or refused.",
        detail=REQUIRED, details=("started", "resumed", "refused"),
    ),
    EventSpec(
        "quiz_answer", SOURCE_SERVER, "quizzes",
        "An answer save was accepted or refused. Never the selection or its "
        "correctness.",
        detail=REQUIRED, details=("saved", "rejected", "expired"),
    ),
    EventSpec(
        "quiz_submission", SOURCE_SERVER, "quizzes",
        "Server-confirmed end of a quiz attempt. Never the score.",
        detail=REQUIRED, details=("submitted", "expired", "rejected"),
        activity_end_details=frozenset({"submitted", "expired"}),
    ),
    EventSpec(
        "listening_start", SOURCE_SERVER, "listening",
        "A listening attempt was started, resumed or refused.",
        detail=REQUIRED, details=("started", "resumed", "refused"),
    ),
    EventSpec(
        "listening_answer", SOURCE_SERVER, "listening",
        "A listening answer save was accepted or refused. Never the selection.",
        detail=REQUIRED, details=("saved", "rejected", "expired"),
    ),
    EventSpec(
        "listening_submission", SOURCE_SERVER, "listening",
        "Server-confirmed end of a listening attempt. Never the score.",
        detail=REQUIRED, details=("submitted", "expired", "rejected"),
        activity_end_details=frozenset({"submitted", "expired"}),
    ),
    EventSpec(
        "speaking_submission", SOURCE_SERVER, "speaking",
        "Server-confirmed outcome of a recording upload. Never the audio.",
        detail=REQUIRED,
        details=("submitted", "duplicate", "rejected_invalid", "rejected_closed",
                 "rejected_stale", "failed"),
        activity_end_details=frozenset({"submitted"}),
    ),
    EventSpec(
        "discussion_reply", SOURCE_SERVER, "communication",
        "A discussion reply was posted or refused. Never the text.",
        detail=REQUIRED, details=("posted", "rejected"),
    ),
    EventSpec(
        "message_send", SOURCE_SERVER, "communication",
        "A private message was sent or refused. Never the body, subject or "
        "recipient.",
        detail=REQUIRED, details=("sent", "rejected"),
    ),
)

EVENT_SPECS = {spec.name: spec for spec in CLIENT_EVENTS + SERVER_EVENTS}
CLIENT_EVENT_TYPES = tuple(spec.name for spec in CLIENT_EVENTS)
SERVER_EVENT_TYPES = tuple(spec.name for spec in SERVER_EVENTS)
EVENT_TYPES = CLIENT_EVENT_TYPES + SERVER_EVENT_TYPES

#: The exact keys a client event object may carry. Anything else rejects the
#: event: an unknown field is never stored, ignored or guessed at.
CLIENT_EVENT_KEYS = frozenset(
    {"id", "type", "t", "page", "view", "tab", "seq", "element", "detail", "count",
     "duration_ms", "position"}
)
CLIENT_REQUIRED_KEYS = frozenset({"id", "type", "t", "page", "view", "tab", "seq"})

#: The exact keys a batch may carry. ``replay`` marks a batch the browser is
#: re-sending from its outbox because no answer reached it (for example, the
#: page navigated away first); its already stored events are expected
#: re-deliveries, not duplicates.
BATCH_KEYS = frozenset({"schema", "sent_at", "events", "dropped", "replay", "delivery_scope"})

#: Bounds on one batch and on the per-page sequence counter.
MAX_BATCH_EVENTS = 50
MAX_BATCH_BYTES = 32 * 1024
MAX_SEQUENCE = 100_000
MAX_DROPPED_REPORT = 10_000

#: The detail codes of every type, flattened -- the width of the column.
DETAIL_CODE_MAX_LENGTH = 24
PAGE_ID_MAX_LENGTH = 40
ELEMENT_ID_MAX_LENGTH = 32
EVENT_TYPE_MAX_LENGTH = 24


def activity_end(event_type, detail):
    """Whether a server outcome ends a natural activity (a sampling trigger)."""
    spec = EVENT_SPECS.get(event_type)
    return spec is not None and spec.source == SOURCE_SERVER and detail in spec.activity_end_details


def dictionary_rows():
    """One plain row per event type, for the export data dictionary and the
    documentation test. Pure data: no wording is invented elsewhere."""
    rows = []
    for spec in CLIENT_EVENTS + SERVER_EVENTS:
        rows.append(
            {
                "event_type": spec.name,
                "source": spec.source,
                "category": spec.category,
                "element": spec.element,
                "details": "|".join(spec.details),
                "count": _describe(spec.count),
                "duration_ms": _describe(spec.duration_ms),
                "position": _describe(spec.position),
                "reason": spec.reason,
            }
        )
    return rows


def _describe(int_field):
    if int_field.presence == FORBIDDEN:
        return ""
    return f"{int_field.presence} {int_field.minimum}-{int_field.maximum} {int_field.unit}".strip()
