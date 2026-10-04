"""Instrumentation, server-confirmed outcomes and failure isolation
(Phase 6 replacement, corrected).

The collector runs in the background on **every** page of the authenticated
Student platform for an eligible Student while a configuration collects, and
costs the portal one bounded query. Every Student route is deliberately
classified (a page, a technical exclusion, an outcome, or a client-observed
action), so a new route without a classification fails here. No ordinary
portal shows any research notice, indicator, page or link; the only
Student-facing element is the optional feedback question, rendered hidden.

Outcomes are noted by the routes in memory and recorded only after the LMS
request finished; research can never fail or roll back an LMS operation.
"""

import json
import logging
import re
from pathlib import Path

import pytest
from sqlalchemy import event as sa_event

import tests.lesson_progress_fixtures as lx
import tests.research_world as rw
from tests.research_world import fresh_identity_per_request  # noqa: F401
import tests.test_student_submissions as sx
from app import create_app
from app.blueprints.collector import hooks
from app.extensions import db
from app.models import ResearchEvent, ResearchSession, Submission
from app.services import research_collection as collection
from app.services.research_event_dictionary import (
    CLIENT_OBSERVED_POSTS,
    EVENT_SPECS,
    OUTCOME_ENDPOINTS,
    PAGE_AREAS,
    PAGE_ID_ALIASES,
    PAGE_IDS,
    REPORTING_AREAS,
    STUDENT_PLATFORM_BLUEPRINTS,
    TECHNICAL_EXCLUSIONS,
)


@pytest.fixture(autouse=True)
def _fresh_identity(fresh_identity_per_request):
    """Every request re-reads its own client's login."""


CONFIG_MARKER = 'id="research-collector-config"'
TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


@pytest.fixture
def people(app):
    return rw.world(app)


def _config(html):
    raw = html.split(CONFIG_MARKER + ">", 1)[1].split("</script>", 1)[0]
    return json.loads(raw)


def _visible_text(html):
    """What a reader of the page can see: no script, style or template
    content, no tags, no attribute values."""
    html = re.sub(r"(?is)<(script|style|template)\b.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _count_statements(client, path, needle="research_"):
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    rw.get(client, path)
    sa_event.listen(db.engine, "before_cursor_execute", record)
    try:
        response = rw.get(client, path)
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", record)
    assert response.status_code == 200
    return [s for s in statements if needle in s]


# ---------------------------------------------------------------------------
# Route inventory: every Student route is classified on purpose
# ---------------------------------------------------------------------------


def _platform_rules():
    app = create_app("testing")
    return [rule for rule in app.url_map.iter_rules()
            if rule.endpoint.split(".", 1)[0] in STUDENT_PLATFORM_BLUEPRINTS]


def test_every_student_platform_route_has_an_intentional_classification():
    """A new Student page or action that nobody classified fails here: add
    it to PAGE_IDS (observed), TECHNICAL_EXCLUSIONS (with a reason),
    OUTCOME_ENDPOINTS or CLIENT_OBSERVED_POSTS."""
    unclassified = []
    for rule in _platform_rules():
        methods = rule.methods - {"HEAD", "OPTIONS"}
        endpoint = rule.endpoint
        if "GET" in methods and endpoint not in PAGE_IDS \
                and endpoint not in TECHNICAL_EXCLUSIONS:
            unclassified.append(("GET", endpoint))
        if "POST" in methods and endpoint not in OUTCOME_ENDPOINTS \
                and endpoint not in CLIENT_OBSERVED_POSTS and endpoint not in PAGE_ID_ALIASES:
            unclassified.append(("POST", endpoint))
        assert methods <= {"GET", "POST"}, (endpoint, methods)
    assert unclassified == []


def test_the_classification_names_only_real_routes_and_never_overlaps():
    endpoints = {rule.endpoint for rule in _platform_rules()}
    for name in (set(PAGE_IDS) | set(TECHNICAL_EXCLUSIONS) | set(OUTCOME_ENDPOINTS)
                 | set(CLIENT_OBSERVED_POSTS) | set(PAGE_ID_ALIASES)):
        assert name in endpoints, name
    assert not set(PAGE_IDS) & set(TECHNICAL_EXCLUSIONS)
    assert not set(CLIENT_OBSERVED_POSTS) & set(OUTCOME_ENDPOINTS)
    assert all(reason.strip() for reason in TECHNICAL_EXCLUSIONS.values())
    assert all(reason.strip() for reason in CLIENT_OBSERVED_POSTS.values())
    for event_type in OUTCOME_ENDPOINTS.values():
        assert EVENT_SPECS[event_type].source == "server"
    assert set(PAGE_AREAS) == set(PAGE_IDS)
    assert set(PAGE_AREAS.values()) <= {"core"} | set(REPORTING_AREAS)


def test_every_student_platform_page_template_runs_the_collector_layout():
    """Every full page of the Student platform extends the portal layout,
    which is where the collector bootstrap lives. Partials start with ``_``."""
    pages = []
    for folder in STUDENT_PLATFORM_BLUEPRINTS:
        for path in sorted((TEMPLATES / folder).rglob("*.html")):
            if path.name.startswith("_"):
                continue
            pages.append(path)
            source = path.read_text(encoding="utf-8")
            assert '{% extends "layouts/portal_base.html" %}' in source, path
    assert len(pages) >= len(PAGE_IDS) - 3   # some pages share one template
    layout = (TEMPLATES / "layouts" / "portal_base.html").read_text(encoding="utf-8")
    assert "research_collector(" in layout and "usage_collector.js" in layout


# ---------------------------------------------------------------------------
# Where the collector runs
# ---------------------------------------------------------------------------


def test_an_eligible_student_page_carries_the_collector_and_nothing_identifying(app, client,
                                                                                people):
    rw.login(client, "s1@example.com")
    html = rw.get(client, "/student/dashboard").get_data(as_text=True)
    assert CONFIG_MARKER in html and "js/usage_collector.js" in html
    config = _config(html)
    assert config["page"] == "student.dashboard" and config["schema"] == rw.SCHEMA
    assert config["eventsUrl"] == "/collect/events"
    assert set(config) == {"schema", "page", "progress", "csrf", "eventsUrl", "promptUrl",
                           "heartbeatMs", "flushMs", "batchSize", "maxBuffer"}
    blob = json.dumps(config)
    for secret in ("s1@example.com", "Sam Student", people["s1"].public_id, "RS-"):
        assert secret not in blob


#: Student platform pages reachable without an object id. Pages with ids are
#: rendered with their fixtures in the outcome tests below; all of them use
#: the same layout (see the template test above).
_PLAIN_PAGES = {
    "student.dashboard": "/student/dashboard",
    "student.search": "/student/search",
    "student.assignments_list": "/student/assignments",
    "student.quiz_list": "/student/quizzes",
    "student.listening_list": "/student/listening",
    "student.speaking_list": "/student/speaking",
    "student.attendance_list": "/student/attendance",
    "student.grades_list": "/student/grades",
    "student.announcements_list": "/student/announcements",
    "student.calendar": "/student/calendar",
    "student.discussions_overview": "/student/discussions",
    "messages.inbox": "/messages",
    "messages.new_thread": "/messages/new",
    "notifications.inbox": "/notifications",
}


@pytest.mark.parametrize("page_id, path", sorted(_PLAIN_PAGES.items()))
def test_the_collector_runs_in_the_background_on_every_student_page(app, client, people,
                                                                    page_id, path):
    rw.login(client, "s1@example.com")
    response = rw.get(client, path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert CONFIG_MARKER in html and "js/usage_collector.js" in html
    assert _config(html)["page"] == page_id
    # The question is present but hidden until the server grants a display.
    assert re.search(r'<section class="research-feedback" id="research-feedback" hidden', html)


def test_the_plain_page_list_matches_the_router():
    app = create_app("testing")
    with app.test_request_context():
        from flask import url_for

        for page_id, path in _PLAIN_PAGES.items():
            assert url_for(page_id) == path


def _login_as(client, role_email):
    if role_email == "researcher@example.com":
        rw.login_researcher(client)
    else:
        rw.login(client, role_email)


@pytest.mark.parametrize("email, path", [
    ("excluded@example.com", "/student/dashboard"),
    ("excluded@example.com", "/messages"),
    ("teacher@example.com", "/teacher/dashboard"),
    ("teacher@example.com", "/messages"),
    ("teacher@example.com", "/notifications"),
    ("admin@example.com", "/admin/dashboard"),
    ("researcher@example.com", "/research/dashboard"),
])
def test_no_ineligible_account_gets_a_collector_or_a_question(app, client, people, email,
                                                             path):
    _login_as(client, email)
    html = rw.get(client, path).get_data(as_text=True)
    assert CONFIG_MARKER not in html and "usage_collector.js" not in html
    assert 'id="research-feedback"' not in html


def test_anonymous_pages_carry_no_collector(app, client, people):
    for path in ("/auth/login", "/research/login"):
        html = rw.get(client, path).get_data(as_text=True)
        assert CONFIG_MARKER not in html and "usage_collector.js" not in html


def test_a_paused_collection_renders_nothing(app, client, people):
    people["config"].is_collecting = False
    db.session.commit()
    rw.login(client, "s1@example.com")
    html = rw.get(client, "/student/dashboard").get_data(as_text=True)
    assert CONFIG_MARKER not in html and 'id="research-feedback"' not in html


def test_progress_context_is_rendered_only_for_question_pages(app, client, people):
    rw.login(client, "s1@example.com")
    config = _config(rw.get(client, "/student/dashboard").get_data(as_text=True))
    assert config["progress"] is None


def test_the_portal_pays_one_research_query_and_teachers_none(app, client, people):
    rw.login(client, "s1@example.com")
    assert len(_count_statements(client, "/student/dashboard")) == 1
    rw.login(client, "excluded@example.com")
    assert len(_count_statements(client, "/student/dashboard")) == 1
    rw.login(client, "teacher@example.com")
    assert _count_statements(client, "/teacher/dashboard") == []


def test_rendering_a_page_writes_nothing(app, client, people):
    """The bootstrap is read-only: the subject is created by the first
    collection write, never by opening a page."""
    rw.login(client, "s1@example.com")
    for path in _PLAIN_PAGES.values():
        rw.get(client, path)
    assert rw.subject_of(people["s1"]) is None
    assert ResearchSession.query.count() == ResearchEvent.query.count() == 0


def test_a_broken_context_fails_closed_and_the_page_still_renders(app, client, people,
                                                                 monkeypatch, caplog):
    def broken(_user_id):
        raise RuntimeError("research tables unavailable")

    monkeypatch.setattr(hooks, "collection_context", broken)
    rw.login(client, "s1@example.com")
    with caplog.at_level(logging.ERROR):
        response = rw.get(client, "/student/dashboard")
    assert response.status_code == 200
    assert CONFIG_MARKER not in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# No research UI in any ordinary portal
# ---------------------------------------------------------------------------


_INDICATOR_WORDS = ("research", "usage", "study", "studies", "experiment", "researcher",
                    "data collection", "being recorded", "monitor", "tracking")


@pytest.mark.parametrize("email, paths", [
    ("s1@example.com", tuple(_PLAIN_PAGES.values())),
    ("excluded@example.com", ("/student/dashboard", "/messages", "/notifications")),
    ("teacher@example.com", ("/teacher/dashboard", "/messages", "/notifications")),
    ("admin@example.com", ("/admin/dashboard",)),
])
def test_no_ordinary_portal_shows_any_research_status_indicator_or_link(app, client, people,
                                                                       email, paths):
    rw.login(client, email)
    for path in paths:
        html = rw.get(client, path).get_data(as_text=True)
        visible = _visible_text(html).lower()
        for word in _INDICATOR_WORDS:
            assert word not in visible, (path, word)
        links = re.findall(r'<a\s[^>]*href="([^"]+)"', html)
        assert links and not [href for href in links
                              if "research" in href or "usage" in href], path
        assert "data-usage-state" not in html and "research-badge" not in html


def test_the_student_information_page_is_gone(app, client, people):
    rw.login(client, "s1@example.com")
    assert rw.get(client, "/student/usage-research").status_code == 404
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert "student.usage_research" not in endpoints
    assert not (TEMPLATES / "student" / "usage_research.html").exists()


def test_the_question_is_still_offered_without_any_research_wording(app, client, people):
    rw.login(client, "s1@example.com")
    html = rw.get(client, "/student/dashboard").get_data(as_text=True)
    section = html.split('id="research-feedback"', 1)[1].split("</section>", 1)[0]
    text = _visible_text("<x" + section).lower()
    assert "how frustrated did you feel?" in text and "skip" in text
    assert "answering is optional" in text
    for word in _INDICATOR_WORDS:
        assert word not in text, word


# ---------------------------------------------------------------------------
# Server-confirmed outcomes
# ---------------------------------------------------------------------------


def _lesson(app):
    student, _teacher, group = lx.classroom(student_email="learner@example.com",
                                            teacher_email="lesson-teacher@example.com")
    unit = lx.unit(group)
    lesson = lx.lesson(unit)
    return lx.lesson_ids(student, group, unit, lesson)


def _server_events():
    return [(e.event_type, e.detail_code, e.page_id)
            for e in ResearchEvent.query.filter_by(source="server").order_by(ResearchEvent.id)]


def test_lesson_completion_outcomes_are_recorded_after_the_commit(app, client, people):
    ids = _lesson(app)
    rw.login(client, "learner@example.com")
    rw.fresh()
    assert lx.post_action(client, "complete", ids).status_code == 302
    assert lx.progress_of(ids["student"], ids["group"], ids["lesson"]).completed_at is not None
    session = ResearchSession.query.one()
    assert session.activity_end_pending_at_ms is not None   # a natural activity ending
    rw.fresh()
    lx.post_action(client, "undo", ids)
    rw.fresh()
    lx.post_action(client, "complete", ids, token="forged")
    assert _server_events() == [
        ("lesson_completion", "completed", None),
        ("lesson_completion", "undone", None),
        ("lesson_completion", "rejected", None),
    ]


def test_assignment_submission_outcomes_never_carry_the_answer(app, client, people):
    student, group = sx._setup("writer@example.com")
    assignment = sx._assignment(group)
    rw.login(client, "writer@example.com")
    rw.fresh()
    sx._submit_once(app, client, group.public_id, assignment.public_id,
                    answer="A secret essay about my family.")
    rw.fresh()
    sx._submit_once(app, client, group.public_id, assignment.public_id)
    # The POST endpoint is named by the page it belongs to, never by a URL.
    assert _server_events() == [
        ("assignment_submission", "submitted", "student.assignment_detail"),
        ("assignment_submission", "duplicate", "student.assignment_detail"),
    ]
    dumped = json.dumps([[getattr(e, c.name) for c in ResearchEvent.__table__.columns]
                         for e in ResearchEvent.query], default=str)
    assert "secret essay" not in dumped
    assert Submission.query.count() == 1


def test_an_invalid_assignment_answer_is_a_rejected_outcome(app, client, people):
    student, group = sx._setup("writer@example.com")
    assignment = sx._assignment(group)
    rw.login(client, "writer@example.com")
    rw.fresh()
    with sx._at(sx.NOW, sx.NOW):
        token = sx._context_token(client, group.public_id, assignment.public_id)
        rw.fresh()
        sx._post(client, group.public_id, assignment.public_id, answer="   ", token=token)
    assert _server_events() == [
        ("assignment_submission", "rejected_invalid", "student.assignment_detail")]


def test_search_outcomes_carry_a_count_and_never_the_terms(app, client, people):
    rw.login(client, "s1@example.com")
    assert rw.get(client, "/student/search?q=confidential+phrase").status_code == 200
    stored = ResearchEvent.query.filter_by(source="server").one()
    assert (stored.event_type, stored.detail_code, stored.count_value) == ("search",
                                                                           "no_results", 0)
    dumped = json.dumps([getattr(stored, c.name) for c in ResearchEvent.__table__.columns])
    assert "confidential" not in dumped
    rw.get(client, "/student/search")                    # no query: no outcome
    assert ResearchEvent.query.filter_by(source="server").count() == 1


def test_an_outcome_alone_provisions_an_eligible_student(app, client, people):
    """A server outcome is a collection write too: the first one creates the
    subject under the population rule, with no page batch before it."""
    rw.login(client, "s1@example.com")
    rw.get(client, "/student/search?q=anything")
    subject = rw.subject_of(people["s1"])
    assert subject is not None and subject.status_basis == "population_rule"
    assert ResearchEvent.query.filter_by(source="server").count() == 1


def test_outcomes_of_an_excluded_student_are_never_recorded(app, client, people):
    rw.login(client, "excluded@example.com")
    rw.get(client, "/student/search?q=anything")
    assert ResearchEvent.query.count() == 0 and ResearchSession.query.count() == 0
    assert rw.subject_of(people["excluded"]).collection_status == "excluded"


def test_an_undeclared_outcome_is_dropped(app, people):
    with app.test_request_context("/student/search"):
        hooks.note_outcome("search", "found_everything")
        hooks.note_outcome("lesson_completion", "completed")
        hooks.note_outcome("keystroke", "typed")
        from flask import request

        noted = request.environ.get("research.outcomes", [])
    assert [(o.event_type, o.detail) for o in noted] == [("lesson_completion", "completed")]


def test_a_teacher_message_notes_no_outcome(app, client, people):
    from tests import message_fixtures as mx

    assert hasattr(mx, "login_as")
    rw.login(client, "teacher@example.com")
    rw.get(client, "/messages")
    assert ResearchEvent.query.count() == 0


# ---------------------------------------------------------------------------
# Failure isolation: research never breaks the LMS
# ---------------------------------------------------------------------------


def test_a_failing_outcome_write_never_fails_the_lms_operation(app, client, people,
                                                              monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("research database unavailable")

    monkeypatch.setattr(collection, "_record_outcomes", broken)
    student, group = sx._setup("writer@example.com")
    assignment = sx._assignment(group)
    rw.login(client, "writer@example.com")
    rw.fresh()
    response = sx._submit_once(app, client, group.public_id, assignment.public_id)
    assert response.status_code == 200
    assert "Your answer was submitted." in response.get_data(as_text=True)
    assert Submission.query.count() == 1
    assert ResearchEvent.query.count() == 0


def test_a_failing_context_never_fails_a_submission(app, client, people, monkeypatch):
    def broken(_user_id):
        raise RuntimeError("research tables unavailable")

    monkeypatch.setattr(hooks, "collection_context", broken)
    ids = _lesson(app)
    rw.login(client, "learner@example.com")
    rw.fresh()
    response = lx.post_action(client, "complete", ids)
    assert response.status_code == 302
    assert lx.progress_of(ids["student"], ids["group"], ids["lesson"]).completed_at is not None


def test_a_failing_research_commit_is_rolled_back_and_the_lms_row_survives(app, client,
                                                                           people, monkeypatch):
    original = collection.resolve_session

    def exploding(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("disk full")

    monkeypatch.setattr(collection, "resolve_session", exploding)
    ids = _lesson(app)
    rw.login(client, "learner@example.com")
    rw.fresh()
    assert lx.post_action(client, "complete", ids).status_code == 302
    assert lx.progress_of(ids["student"], ids["group"], ids["lesson"]).completed_at is not None
    assert ResearchSession.query.count() == 0 and ResearchEvent.query.count() == 0


def test_with_no_configuration_every_workflow_behaves_as_before(app, client):
    """No research row exists at all: the LMS path is the untouched one."""
    ids = None
    student, group = sx._setup("plain@example.com")
    assignment = sx._assignment(group)
    rw.login(client, "plain@example.com")
    rw.fresh()
    response = sx._submit_once(app, client, group.public_id, assignment.public_id)
    assert "Your answer was submitted." in response.get_data(as_text=True)
    html = rw.get(client, "/student/dashboard").get_data(as_text=True)
    assert CONFIG_MARKER not in html
    assert ids is None and ResearchEvent.query.count() == 0
