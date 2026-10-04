"""Natural-use sessions and ingestion under the population rule (Phase 6
replacement, corrected).

Every eligible Student -- an active Student account that is not excluded --
is collected automatically while a configuration collects inside its period:
the subject is created at the first collection write, with no operator step,
including for accounts created after collection started. Other roles,
suspended accounts and excluded Students are never collected, and nothing a
Student does re-enrols an exclusion.

The acting Student is always the authenticated session; the research session
is always resolved from the signed session cookie and re-proved to belong to
that Student's subject. Every write re-checks eligibility after its locks.

SQLite honours neither ``FOR UPDATE`` nor ``FOR SHARE``: the lock-order test
asserts the *requested* order only and proves nothing about InnoDB blocking.
"""

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

import tests.research_world as rw
from tests.research_world import fresh_identity_per_request  # noqa: F401
from app.extensions import db
from app.models import (
    ResearchAuditEvent,
    ResearchConfiguration,
    ResearchEvent,
    ResearchSession,
    ResearchSubject,
    ResearchSubjectLink,
    UserStatus,
    now_ms,
)
from app.services import research_collection as collection
from app.services import research_event_validation as validation
from app.services import research_operator as operator
from app.services.research_event_dictionary import (
    CLIENT_EVENT_TYPES,
    EVENT_SPECS,
    MAX_BATCH_EVENTS,
    PAGE_IDS,
)


@pytest.fixture(autouse=True)
def _fresh_identity(fresh_identity_per_request):
    """Every request re-reads its own client's login."""


@pytest.fixture
def people(app):
    return rw.world(app)


def _student(client, email="s1@example.com"):
    rw.login(client, email)


def _dashboard_batch(browser=None, **fields):
    browser = browser or rw.Browser()
    return rw.batch([browser.event("page_view", detail="wide", **fields)])


# ---------------------------------------------------------------------------
# The valid path
# ---------------------------------------------------------------------------


def test_an_eligible_student_batch_is_stored_in_one_session(app, client, people):
    assert rw.subject_of(people["s1"]) is None  # nobody included anybody
    _student(client)
    browser = rw.Browser()
    response = rw.post(client, rw.batch([
        browser.event("page_view", detail="wide"),
        browser.event("control_click", element="nav_quizzes"),
        browser.event("heartbeat", detail="idle"),
    ]))
    assert response.status_code == 200
    body = response.get_json()
    assert body["collecting"] is True and body["accepted"] == 3
    assert body["duplicates"] == body["invalid"] == body["late"] == 0
    subject = rw.subject_of(people["s1"])
    assert (subject.collection_status, subject.status_basis, subject.provenance) == (
        "included", "population_rule", "study")
    sessions = rw.sessions_of(subject)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.configuration_id == people["config"].id
    assert session.events_accepted == 3 and session.batches_received == 1
    assert session.provenance == "development"
    events = rw.events_of(session.id)
    assert [e.event_type for e in events] == ["page_view", "control_click", "heartbeat"]
    assert all(e.source == "client" and e.received_at_ms >= e.client_ts_ms - 1000
               for e in events)
    assert response.headers["Cache-Control"] == "private, no-store"


def test_client_time_and_server_receipt_are_stored_separately(app, client, people):
    _student(client)
    browser = rw.Browser()
    skewed_sent = now_ms() - 3_600_000  # the client clock is an hour slow
    event = browser.event("page_view", t=skewed_sent - 5_000, detail="medium")
    assert rw.post(client, rw.batch([event], sent_at=skewed_sent)).status_code == 200
    stored = ResearchEvent.query.one()
    assert stored.client_ts_ms == skewed_sent - 5_000
    assert stored.clock_offset_ms == stored.received_at_ms - skewed_sent
    assert stored.occurred_at_ms == stored.client_ts_ms + stored.clock_offset_ms
    assert abs(stored.occurred_at_ms - (stored.received_at_ms - 5_000)) < 50


def test_the_session_is_named_by_the_signed_cookie_never_by_the_body(app, client, people):
    _student(client)
    first = rw.post(client, _dashboard_batch())
    assert first.status_code == 200
    with client.session_transaction() as flask_session:
        ref = flask_session["research_session_ref"]
    assert ResearchSession.query.one().public_id == ref
    # A body that tries to name a session or a Student is refused outright.
    for extra in ({"session_id": ref}, {"student_id": people["s2"].id},
                  {"participant": "RS-XXXXXXXXXX"}):
        response = rw.post(client, _dashboard_batch() | extra)
        assert response.status_code == 400, extra
    assert ResearchSession.query.count() == 1


def test_tabs_of_one_browser_share_one_session_with_distinct_tab_refs(app, client, people):
    _student(client)
    tab_a, tab_b = rw.Browser(), rw.Browser()
    rw.post(client, _dashboard_batch(tab_a))
    rw.post(client, _dashboard_batch(tab_b))
    assert ResearchSession.query.count() == 1
    assert {e.tab_ref for e in ResearchEvent.query} == {tab_a.tab, tab_b.tab}


def test_a_second_browser_is_a_second_session(app, people):
    browser_one, browser_two = app.test_client(), app.test_client()
    _student(browser_one)
    _student(browser_two)
    rw.post(browser_one, _dashboard_batch())
    rw.post(browser_two, _dashboard_batch())
    assert ResearchSession.query.count() == 2
    assert {s.subject_id for s in ResearchSession.query} == {rw.subject_of(people["s1"]).id}


def test_inactivity_ends_the_session_at_its_last_activity_and_rotates(app, client, people):
    _student(client)
    rw.post(client, _dashboard_batch())
    session = ResearchSession.query.one()
    rw.age_session(session.id, 31 * 60 * 1000)
    last_seen = ResearchSession.query.one().last_seen_at_ms
    rw.post(client, _dashboard_batch())
    first, second = ResearchSession.query.order_by(ResearchSession.id).all()
    assert first.ended_at_ms == last_seen and first.end_reason == "inactivity"
    assert second.is_open and second.public_id == collection.successor_ref(first.public_id)
    with client.session_transaction() as flask_session:
        assert flask_session["research_session_ref"] == second.public_id


def test_two_tabs_rotating_at_once_converge_on_one_successor(app, client, people):
    _student(client)
    rw.post(client, _dashboard_batch())
    stale = ResearchSession.query.one()
    rw.age_session(stale.id, 31 * 60 * 1000)
    ref = stale.public_id
    user_id = people["s1"].id
    batch = validation.parse_batch(json.dumps(_dashboard_batch()).encode())[0]
    first = collection.ingest_batch(user_id, ref, batch, "development", "UTC")
    batch = validation.parse_batch(json.dumps(_dashboard_batch()).encode())[0]
    second = collection.ingest_batch(user_id, ref, batch, "development", "UTC")
    assert first.session_ref == second.session_ref == collection.successor_ref(ref)
    assert ResearchSession.query.count() == 2


def test_logout_ends_the_session_and_forgets_its_reference(app, client, people):
    _student(client)
    rw.post(client, _dashboard_batch())
    client.post("/auth/logout")
    session = ResearchSession.query.one()
    assert session.end_reason == "logout" and session.ended_at_ms is not None
    with client.session_transaction() as flask_session:
        assert "research_session_ref" not in flask_session


def test_a_changed_configuration_closes_the_old_session(app, client, people):
    _student(client)
    rw.post(client, _dashboard_batch())
    rw.retire(people["config"], people["researcher"])
    new = rw.configuration(people["researcher"])
    rw.post(client, _dashboard_batch())
    first, second = ResearchSession.query.order_by(ResearchSession.id).all()
    assert first.end_reason == "configuration_changed"
    assert second.configuration_id == new.id


# ---------------------------------------------------------------------------
# Who may write, re-checked on every write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("email", ["teacher@example.com", "admin@example.com"])
def test_other_lms_roles_are_forbidden(app, client, people, email):
    rw.login(client, email)
    response = rw.post(client, _dashboard_batch())
    assert response.status_code == 403 and response.get_json() == {"error": "forbidden"}
    assert ResearchEvent.query.count() == 0


def test_a_researcher_is_forbidden(app, client, people):
    rw.login_researcher(client)
    assert rw.post(client, _dashboard_batch()).status_code == 403


def test_an_anonymous_visitor_gets_a_json_401_not_a_redirect(app, client, people):
    response = rw.post(client, _dashboard_batch())
    assert response.status_code == 401
    assert response.get_json() == {"error": "authentication_required"}


# ---------------------------------------------------------------------------
# The population rule: automatic, idempotent, and never past an exclusion
# ---------------------------------------------------------------------------


def test_an_existing_student_is_provisioned_once_and_reused(app, people):
    first, second = app.test_client(), app.test_client()
    subject = rw.provision(first)
    _student(second)
    assert rw.post(second, _dashboard_batch()).get_json()["collecting"] is True
    rw.post(first, _dashboard_batch())
    assert ResearchSubjectLink.query.filter_by(user_id=people["s1"].id).count() == 1
    assert rw.subject_of(people["s1"]).subject_code == subject.subject_code
    assert subject.subject_code.startswith("RS-") and str(uuid.UUID(subject.public_id))
    # No operator audit row: the population rule is not an operator decision.
    assert ResearchAuditEvent.query.filter_by(subject_id=subject.id).count() == 0


def test_a_student_created_after_collection_started_is_collected_automatically(
        app, client, people):
    assert people["config"].is_collecting
    newcomer = rw.user("newcomer@example.com", rw.STUDENT, full_name="Nia Newcomer")
    assert rw.subject_of(newcomer) is None
    subject = rw.provision(client, "newcomer@example.com")
    assert (subject.collection_status, subject.status_basis) == ("included", "population_rule")
    assert rw.sessions_of(subject)[0].events_accepted == 1


def test_a_student_activated_later_is_collected_from_the_activation(app, client, people):
    later = rw.user("later@example.com", rw.STUDENT, status=UserStatus.SUSPENDED.value)
    batch = validation.parse_batch(json.dumps(_dashboard_batch()).encode())[0]
    refused = collection.ingest_batch(later.id, None, batch, "development", "UTC")
    assert refused.status == collection.NOT_COLLECTING and rw.subject_of(later) is None
    later.status = UserStatus.ACTIVE.value
    db.session.commit()
    subject = rw.provision(client, "later@example.com")
    assert subject.status_basis == "population_rule"


@pytest.mark.parametrize("role", [rw.TEACHER, rw.ADMIN, rw.RESEARCHER])
def test_no_other_role_is_ever_provisioned(app, people, role):
    account = rw.user("other@example.com", role)
    batch = validation.parse_batch(json.dumps(_dashboard_batch()).encode())[0]
    result = collection.ingest_batch(account.id, None, batch, "development", "UTC")
    assert result.status == collection.NOT_COLLECTING
    assert rw.subject_of(account) is None and ResearchSession.query.count() == 0


@pytest.mark.parametrize("state", ["paused", "draft_only", "not_started", "ended"])
def test_nobody_is_provisioned_while_nothing_collects(app, client, people, state):
    config = people["config"]
    if state == "paused":
        config.is_collecting = False
        db.session.commit()
    elif state == "draft_only":
        rw.retire(config, people["researcher"])
        rw.configuration(people["researcher"], status="draft")
    elif state == "not_started":
        rw.shift_period(config.id, starts_at=rw.utcnow() + timedelta(hours=1),
                        ends_at=rw.utcnow() + timedelta(days=2))
    else:
        rw.shift_period(config.id, ends_at=rw.utcnow() - timedelta(seconds=1))
    _student(client)
    assert rw.post(client, _dashboard_batch()).get_json() == {"collecting": False}
    assert rw.get(client, "/student/dashboard").status_code == 200
    assert rw.subject_of(people["s1"]) is None


def test_an_excluded_student_is_never_collected_or_re_enrolled(app, client, people):
    excluded = rw.subject_of(people["excluded"])
    assert (excluded.collection_status, excluded.status_basis) == (
        "excluded", "external_exclusion")
    before = (excluded.status_changed_at, excluded.updated_at)
    _student(client, "excluded@example.com")
    for path in ("/student/dashboard", "/student/quizzes", "/notifications", "/messages"):
        assert rw.get(client, path).status_code == 200
    response = rw.post(client, _dashboard_batch())
    assert response.status_code == 200 and response.get_json() == {"collecting": False}
    batch = validation.parse_batch(json.dumps(_dashboard_batch()).encode())[0]
    delayed = collection.ingest_batch(people["excluded"].id, None, batch, "development", "UTC")
    assert delayed.status == collection.NOT_COLLECTING
    db.session.expire_all()
    still = rw.subject_of(people["excluded"])
    assert still.id == excluded.id and still.collection_status == "excluded"
    assert (still.status_changed_at, still.updated_at) == before
    assert ResearchSubjectLink.query.filter_by(user_id=people["excluded"].id).count() == 1
    assert ResearchEvent.query.count() == ResearchSession.query.count() == 0


def test_a_legacy_exclusion_is_never_re_enrolled_either(app, client, people):
    from sqlalchemy import text

    db.session.execute(text(
        "UPDATE research_subjects SET status_basis = 'legacy_collection_exclusion'"
        " WHERE id = :id"), {"id": rw.subject_of(people["excluded"]).id})
    db.session.commit()
    _student(client, "excluded@example.com")
    assert rw.post(client, _dashboard_batch()).get_json() == {"collecting": False}
    assert rw.subject_of(people["excluded"]).status_basis == "legacy_collection_exclusion"


def test_a_concurrent_first_write_reuses_the_winners_subject(app, client, people, monkeypatch):
    """The unique account link is the final defense: a request that loses the
    provisioning race rolls back, retries, and reuses the winner's subject."""
    winner = {}
    original = collection._provision_subject

    def racing(user_id):
        if not winner:
            # Another request commits the subject between our lock and insert.
            winner["subject"] = original(user_id)
            db.session.commit()
            winner["code"] = winner["subject"].subject_code
            raise IntegrityError("INSERT", {}, Exception("uq_research_subject_links_user_id"))
        return original(user_id)

    monkeypatch.setattr(collection, "_provision_subject", racing)
    _student(client)
    body = rw.post(client, _dashboard_batch()).get_json()
    assert body["collecting"] is True and body["accepted"] == 1
    assert ResearchSubjectLink.query.filter_by(user_id=people["s1"].id).count() == 1
    assert rw.subject_of(people["s1"]).subject_code == winner["code"]
    assert ResearchSubject.query.filter_by(status_basis="population_rule").count() == 1


def test_provisioning_that_keeps_failing_writes_nothing(app, client, people, monkeypatch):
    def always(_user_id):
        raise IntegrityError("INSERT", {}, Exception("conflict"))

    monkeypatch.setattr(collection, "_provision_subject", always)
    _student(client)
    assert rw.post(client, _dashboard_batch()).get_json() == {"collecting": False}
    assert rw.subject_of(people["s1"]) is None
    assert ResearchEvent.query.count() == ResearchSession.query.count() == 0


def test_the_client_can_never_name_the_subject_or_account(app, client, people):
    _student(client)
    for extra in ({"subject": "RS-2345678923"}, {"user_id": people["s2"].id},
                  {"email": "s2@example.com"}):
        assert rw.post(client, _dashboard_batch() | extra).status_code == 400
    assert rw.subject_of(people["s2"]) is None


@pytest.mark.parametrize("change", ["excluded", "paused", "ended", "not_started", "none",
                                    "suspended"])
def test_delayed_batches_are_rechecked_at_write_time(app, client, people, change):
    _student(client)
    assert rw.post(client, _dashboard_batch()).get_json()["collecting"] is True
    config = people["config"]
    if change == "excluded":
        assert operator.exclude("s1@example.com")[0] == operator.EXCLUDED
    elif change == "paused":
        config.is_collecting = False
        db.session.commit()
    elif change == "ended":
        rw.shift_period(config.id, ends_at=rw.utcnow() - timedelta(seconds=1))
    elif change == "not_started":
        rw.shift_period(config.id, starts_at=rw.utcnow() + timedelta(hours=1),
                        ends_at=rw.utcnow() + timedelta(days=2))
    elif change == "none":
        rw.retire(config, people["researcher"])
    before = ResearchEvent.query.count()
    user_id = people["s1"].id
    ref = ResearchSession.query.first().public_id
    if change == "suspended":
        people["s1"].status = UserStatus.SUSPENDED.value
        db.session.commit()
    # The service itself, as a batch delayed past the change would reach it.
    batch = validation.parse_batch(json.dumps(_dashboard_batch()).encode())[0]
    result = collection.ingest_batch(user_id, ref, batch, "development", "UTC")
    assert result.status == collection.NOT_COLLECTING
    assert ResearchEvent.query.count() == before
    if change != "suspended":
        assert rw.post(client, _dashboard_batch()).get_json() == {"collecting": False}


def test_an_exclusion_closes_open_sessions_immediately(app, client, people):
    _student(client)
    rw.post(client, _dashboard_batch())
    operator.exclude("s1@example.com")
    session = ResearchSession.query.one()
    assert session.end_reason == "subject_ineligible" and session.ended_at_ms is not None


def test_a_copied_cookie_never_adopts_another_students_session(app, people):
    victim, attacker = app.test_client(), app.test_client()
    _student(victim, "s1@example.com")
    rw.post(victim, _dashboard_batch())
    with victim.session_transaction() as flask_session:
        victims_ref = flask_session["research_session_ref"]
    _student(attacker, "s2@example.com")
    with attacker.session_transaction() as flask_session:
        flask_session["research_session_ref"] = victims_ref
    rw.post(attacker, _dashboard_batch())
    victims_session = ResearchSession.query.filter_by(public_id=victims_ref).one()
    assert victims_session.subject_id == rw.subject_of(people["s1"]).id
    assert victims_session.events_accepted == 1
    theirs = ResearchSession.query.filter(ResearchSession.public_id != victims_ref).one()
    assert theirs.subject_id == rw.subject_of(people["s2"]).id
    with attacker.session_transaction() as flask_session:
        assert flask_session["research_session_ref"] == theirs.public_id


# ---------------------------------------------------------------------------
# Refused batches and events
# ---------------------------------------------------------------------------


def test_replayed_and_in_batch_duplicate_events_are_counted_not_stored(app, client, people):
    _student(client)
    browser = rw.Browser()
    event = browser.event("page_view", detail="wide")
    assert rw.post(client, rw.batch([event, dict(event)])).get_json()["duplicates"] == 1
    replay = rw.post(client, rw.batch([event])).get_json()
    assert replay["accepted"] == 0 and replay["duplicates"] == 1
    assert ResearchEvent.query.count() == 1
    assert ResearchSession.query.one().events_duplicate == 2


def test_a_marked_replay_of_a_delivered_batch_changes_no_counter(app, client, people):
    """The browser re-sends a batch whose answer it never saw (for example a
    form submission navigated away first) with the same event ids. The
    server stores nothing twice and counts the batch once."""
    _student(client)
    browser = rw.Browser()
    events = [browser.event("control_click", element="nav_quizzes"),
              browser.event("form_submit", element="quiz_save")]
    sent = now_ms()
    first = rw.post(client, rw.batch(events, sent_at=sent, dropped=2)).get_json()
    assert first["accepted"] == 2
    session = ResearchSession.query.one()
    counters = (session.batches_received, session.events_accepted, session.events_duplicate,
                session.events_dropped_client)
    for _attempt in range(2):
        again = rw.post(client, rw.batch(events, sent_at=now_ms(), dropped=2,
                                         replay=True)).get_json()
        assert again["accepted"] == 0 and again["duplicates"] == 2
    db.session.expire_all()
    session = ResearchSession.query.one()
    assert (session.batches_received, session.events_accepted, session.events_duplicate,
            session.events_dropped_client) == counters == (1, 2, 0, 2)
    assert [e.event_type for e in ResearchEvent.query.order_by(ResearchEvent.id)] == [
        "control_click", "form_submit"]


def test_a_replay_of_a_batch_that_never_arrived_is_stored_once(app, client, people):
    _student(client)
    rw.post(client, _dashboard_batch())
    browser = rw.Browser()
    events = [browser.event("form_submit", element="quiz_save")]
    body = rw.post(client, rw.batch(events, replay=True)).get_json()
    assert body["accepted"] == 1 and body["duplicates"] == 0
    assert ResearchSession.query.one().batches_received == 2


def test_the_replay_flag_must_be_a_boolean(app, client, people):
    _student(client)
    assert rw.post(client, _dashboard_batch() | {"replay": "yes"}).status_code == 400
    assert rw.post(client, _dashboard_batch() | {"replay": 1}).status_code == 400


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(type="keystroke"),
    lambda e: e.update(type="assignment_submission"),  # a server type from a client
    lambda e: e.update(text="hello"),
    lambda e: e.update(value="option-3"),
    lambda e: e.update(url="/student/grades?x=1"),
    lambda e: e.update(page="https://evil.example/"),
    lambda e: e.update(page="student.material_download"),
    lambda e: e.update(detail="huge"),
    lambda e: e.update(id="not-a-uuid"),
    lambda e: e.update(seq=0),
    lambda e: e.update(t="yesterday"),
    lambda e: e.update(count=True),
    lambda e: e.pop("view"),
])
def test_an_undeclared_event_is_refused_and_counted_invalid(app, client, people, mutate):
    _student(client)
    browser = rw.Browser()
    bad = browser.event("page_view", detail="wide")
    mutate(bad)
    good = browser.event("heartbeat", detail="idle")
    body = rw.post(client, rw.batch([bad, good])).get_json()
    assert body["accepted"] == 1 and body["invalid"] == 1
    assert [e.event_type for e in ResearchEvent.query] == ["heartbeat"]


@pytest.mark.parametrize("fields", [
    {"type": "control_click"},                                  # element missing
    {"type": "control_click", "element": "made_up_button"},
    {"type": "control_click", "element": "nav_quizzes", "position": 3},
    {"type": "repeated_click", "element": "nav_quizzes", "count": 51},
    {"type": "input_change", "element": "nav_quizzes"},
    {"type": "media_event", "element": "lesson_audio", "detail": "download"},
    {"type": "page_leave", "duration_ms": -1},
    {"type": "page_view", "detail": "wide", "position": 2, "count": 10},  # not a quiz page
    {"type": "visibility_hidden", "detail": "wide"},
])
def test_field_rules_per_event_type(app, client, people, fields):
    _student(client)
    browser = rw.Browser()
    event = browser.event(fields.pop("type"), **fields)
    assert rw.post(client, rw.batch([event])).get_json()["invalid"] == 1


def test_progress_is_accepted_only_on_progress_pages(app, client, people):
    _student(client)
    browser = rw.Browser(page="student.quiz_question")
    body = rw.post(client, rw.batch([
        browser.event("page_view", detail="wide", position=2, count=10),
        browser.event("page_view", detail="wide", position=11, count=10),
    ])).get_json()
    assert body["accepted"] == 1 and body["invalid"] == 1


@pytest.mark.parametrize("payload, status", [
    ({"schema": "natural-use-events.v0", "sent_at": 1, "events": [{}]}, 400),
    ({"sent_at": 1, "events": [{}]}, 400),
    ({"schema": rw.SCHEMA, "sent_at": 1, "events": []}, 400),
    ({"schema": rw.SCHEMA, "sent_at": 1, "events": [{}], "session": "x"}, 400),
    ({"schema": rw.SCHEMA, "sent_at": "now", "events": [{}]}, 400),
    ({"schema": rw.SCHEMA, "sent_at": 1, "events": [{}], "dropped": -1}, 400),
    ([1, 2, 3], 400),
])
def test_malformed_batches_are_refused_whole(app, client, people, payload, status):
    _student(client)
    assert rw.post(client, payload).status_code == status
    assert ResearchEvent.query.count() == 0


def test_oversized_and_overlong_batches_are_refused(app, client, people):
    _student(client)
    browser = rw.Browser()
    too_many = rw.batch([browser.event("heartbeat", detail="idle")
                         for _ in range(MAX_BATCH_EVENTS + 1)])
    assert rw.post(client, too_many).status_code == 400
    padded = json.dumps(_dashboard_batch()) + " " * (33 * 1024)
    response = rw.post(client, None, raw=padded)
    assert response.status_code == 413
    assert ResearchEvent.query.count() == 0


def test_a_non_json_body_is_refused(app, client, people):
    _student(client)
    response = client.post("/collect/events", data="a=b",
                           content_type="application/x-www-form-urlencoded")
    assert response.status_code == 415
    assert rw.post(client, None, raw="{not json").status_code == 400


def test_late_and_future_events_are_refused(app, client, people):
    _student(client)
    browser = rw.Browser()
    now = now_ms()
    body = rw.post(client, rw.batch([
        browser.event("heartbeat", t=now - 11 * 60 * 1000, detail="idle"),   # buffered too long
        browser.event("heartbeat", t=now + 5_000, detail="idle"),            # after sent_at
        browser.event("heartbeat", t=now - 1_000, detail="idle"),
    ], sent_at=now)).get_json()
    assert body == {"collecting": True, "accepted": 1, "duplicates": 0, "invalid": 1,
                    "late": 1, "prompt": None}


def test_events_older_than_the_current_session_are_late(app, client, people):
    _student(client)
    rw.post(client, _dashboard_batch())
    session = ResearchSession.query.one()
    browser = rw.Browser()
    early = session.started_at_ms - 2 * 60 * 1000
    body = rw.post(client, rw.batch([browser.event("heartbeat", t=early, detail="idle")],
                                    sent_at=now_ms())).get_json()
    assert body["late"] == 1 and body["accepted"] == 0


def test_the_dropped_count_reported_by_the_browser_is_recorded(app, client, people):
    _student(client)
    rw.post(client, _dashboard_batch() | {"dropped": 7})
    assert ResearchSession.query.one().events_dropped_client == 7


def test_every_declared_student_page_is_collected_without_a_scope_setting(app, client,
                                                                         people):
    """There is no per-area switch: a collecting configuration covers every
    Student page in the dictionary."""
    columns = {c["name"] for c in sa_inspect(db.engine).get_columns("research_configurations")}
    assert not {c for c in columns if c.startswith("scope")}
    assert not hasattr(ResearchConfiguration, "scope_enabled")
    _student(client)
    browser = rw.Browser()
    events = [browser.event("page_view", detail="wide", page=page) for page in PAGE_IDS
              if page not in ("student.quiz_question", "student.listening_question")]
    for start in range(0, len(events), MAX_BATCH_EVENTS):
        body = rw.post(client, rw.batch(events[start:start + MAX_BATCH_EVENTS])).get_json()
        assert body["invalid"] == 0
    assert ResearchEvent.query.count() == len(events)


# ---------------------------------------------------------------------------
# CSRF, provenance, lock order, privacy
# ---------------------------------------------------------------------------


def test_the_collector_is_csrf_protected_and_accepts_the_header(app, client, people):
    _student(client)
    client.get("/student/dashboard")
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert rw.post(client, _dashboard_batch()).status_code == 400
        page = client.get("/student/dashboard").get_data(as_text=True)
        config = json.loads(page.split('id="research-collector-config">', 1)[1]
                            .split("</script>", 1)[0])
        response = rw.post(client, _dashboard_batch(), headers={"X-CSRFToken": config["csrf"]})
        assert response.status_code == 200 and response.get_json()["accepted"] == 1
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_provenance_is_decided_by_the_server(app, client, people):
    app.config["RESEARCH_DATA_PROVENANCE"] = "study"
    _student(client)
    rw.post(client, _dashboard_batch() | {})
    assert ResearchSession.query.one().provenance == "study"
    demo_user = rw.user("demo@example.com", rw.STUDENT)
    rw.mark_demo(demo_user)
    other = app.test_client()
    rw.login(other, "demo@example.com")
    rw.post(other, _dashboard_batch())
    demo_session = ResearchSession.query.filter(
        ResearchSession.subject_id == rw.subject_of(demo_user).id).one()
    assert demo_session.provenance == "demo"
    # A client value is refused, never used.
    assert rw.post(client, _dashboard_batch() | {"provenance": "study"}).status_code == 400


def test_the_ingestion_takes_the_documented_lock_order(app, client, people, monkeypatch):
    rw.provision(client)
    locked = []
    original = Query.with_for_update

    def record(self, *args, **kwargs):
        locked.append(self.column_descriptions[0]["entity"].__tablename__)
        return original(self, *args, **kwargs)

    resets = []
    monkeypatch.setattr(Query, "with_for_update", record)
    monkeypatch.setattr(collection, "lock_academic_hierarchy", lambda: resets.append(1))
    rw.post(client, _dashboard_batch())
    assert resets == [1]
    assert locked[:3] == ["users", "research_configurations", "research_subjects"]
    assert set(locked[3:]) <= {"research_sessions"}


def test_provisioning_happens_under_the_students_lock(app, client, people, monkeypatch):
    """The first write locks the Student and the configuration, finds no
    subject, and creates it while still holding the Student's row."""
    _student(client)
    steps = []
    original_lock, original_provision = Query.with_for_update, collection._provision_subject

    def record(self, *args, **kwargs):
        steps.append(self.column_descriptions[0]["entity"].__tablename__)
        return original_lock(self, *args, **kwargs)

    def provision(user_id):
        steps.append("provision")
        return original_provision(user_id)

    monkeypatch.setattr(Query, "with_for_update", record)
    monkeypatch.setattr(collection, "_provision_subject", provision)
    rw.post(client, _dashboard_batch())
    assert steps[:3] == ["users", "research_configurations", "provision"]
    assert set(steps[3:]) <= {"research_sessions"}


#: Every text-typed column the research tables have. Each is an identifier,
#: a code from a closed allowlist, a digest or the Researcher's own label --
#: there is no column able to hold Student content.
_TEXT_COLUMNS = {
    "research_subjects": {"public_id", "subject_code", "collection_status", "status_basis",
                          "provenance"},
    "research_subject_links": set(),
    "research_configurations": {"public_id", "label", "event_schema_version", "status"},
    "research_sessions": {"public_id", "provenance", "end_reason"},
    "research_events": {"event_uid", "source", "event_type", "page_id", "element_id",
                        "detail_code", "page_view_ref", "tab_ref"},
    "research_feedback_prompts": {"public_id", "status", "sampling_reason",
                                  "last_deferral_reason"},
    "research_exports": {"public_id", "export_format", "event_schema_version", "timezone",
                         "manifest_digest"},
    "research_export_archives": {"archive_sha256"},
    "research_audit_events": {"action", "channel", "detail_code"},
}

#: The one binary column: the immutable ZIP of an export, built only from the
#: research tables above.
_BINARY_COLUMNS = {("research_export_archives", "content")}


def test_no_research_table_has_a_column_able_to_hold_content(app):
    import sqlalchemy as sa

    for table, allowed in _TEXT_COLUMNS.items():
        columns = sa_inspect(db.engine).get_columns(table)
        text_columns = {c["name"] for c in columns if isinstance(c["type"], (sa.String, sa.Text))}
        assert text_columns == allowed, table
        for column in columns:
            if isinstance(column["type"], sa.String):
                assert column["type"].length is not None and column["type"].length <= 80,                     (table, column["name"])
            assert not isinstance(column["type"], sa.Text), (table, column["name"])
            if isinstance(column["type"], sa.LargeBinary):
                assert (table, column["name"]) in _BINARY_COLUMNS
    research_tables = {name for name in sa_inspect(db.engine).get_table_names()
                       if name.startswith("research_")}
    assert research_tables == set(_TEXT_COLUMNS)


def test_every_declared_client_event_is_accepted_in_its_minimal_form(app, client, people):
    _student(client)
    browser = rw.Browser(page="student.quiz_question")
    samples = {
        "page_view": {"detail": "wide"},
        "page_leave": {"duration_ms": 1000},
        "visibility_hidden": {},
        "visibility_visible": {},
        "heartbeat": {"detail": "media_playing"},
        "control_click": {"element": "quiz_next"},
        "repeated_click": {"element": "quiz_save", "count": 3},
        "non_interactive_click": {},
        "input_change": {"element": "quiz_option"},
        "form_submit": {"element": "quiz_save"},
        "form_invalid": {"element": "quiz_save", "count": 1},
        "media_event": {"element": "listening_audio", "detail": "seek", "position": 12},
        "recorder_state": {"element": "speaking_recorder", "detail": "recording"},
        "recorder_failure": {"element": "speaking_recorder", "detail": "permission_denied"},
    }
    assert set(samples) == set(CLIENT_EVENT_TYPES)
    body = rw.post(client, rw.batch([browser.event(name, **fields)
                                     for name, fields in samples.items()])).get_json()
    assert body["accepted"] == len(samples) and body["invalid"] == 0


def test_the_dictionary_is_internally_consistent():
    assert len(set(PAGE_IDS)) == len(PAGE_IDS)
    for spec in EVENT_SPECS.values():
        assert spec.reason and spec.category
        if spec.detail != "forbidden":
            assert spec.details
    assert all(spec.source == "client" for name, spec in EVENT_SPECS.items()
               if name in CLIENT_EVENT_TYPES)


def test_before_any_collection_only_the_recorded_exclusion_exists(app, client, people):
    subject = ResearchSubject.query.one()
    assert (subject.collection_status, subject.status_basis) == ("excluded",
                                                                 "external_exclusion")
    assert str(uuid.UUID(subject.public_id))


def test_the_collector_rate_limit_is_per_account_not_per_address():
    """Two Students behind one address each get their own budget. Flask-Limiter
    reads RATELIMIT_ENABLED at init_app(), so this test builds its own app with
    it on -- the pattern of test_auth_redirect's rate-limit test."""
    from app import create_app
    from app.config import config_by_name

    original = config_by_name["testing"].RATELIMIT_ENABLED
    config_by_name["testing"].RATELIMIT_ENABLED = True
    try:
        limited = create_app("testing")
    finally:
        config_by_name["testing"].RATELIMIT_ENABLED = original
    with limited.app_context():
        db.create_all()
        try:
            rw.world(limited)
            first, second = limited.test_client(), limited.test_client()
            _student(first, "s1@example.com")
            _student(second, "s2@example.com")
            statuses = [rw.post(first, _dashboard_batch()).status_code for _ in range(121)]
            assert statuses[:120] == [200] * 120 and statuses[120] == 429
            assert rw.post(second, _dashboard_batch()).status_code == 200
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
