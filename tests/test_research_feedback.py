"""Hybrid feedback sampling and the optional frustration question
(Phase 6 replacement).

Sampling is made deterministic by replacing ``research_sampling._draw``; the
rest runs through the real routes. Time is moved with raw-SQL helpers that
shift stored moments into the past, never by editing guarded columns through
the ORM.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Query

import tests.research_world as rw
from tests.research_world import fresh_identity_per_request  # noqa: F401
from app.extensions import db
from app.models import ResearchFeedbackPrompt, ResearchSession, now_ms
from app.services import research_collection as collection
from app.services import research_sampling as sampling
from app.services.research_workspace_queries import derived_prompt_state


@pytest.fixture(autouse=True)
def _fresh_identity(fresh_identity_per_request):
    """Every request re-reads its own client's login."""


@pytest.fixture
def people(app):
    return rw.world(app)


@pytest.fixture
def always(monkeypatch):
    monkeypatch.setattr(sampling, "_draw", lambda: 0)


@pytest.fixture
def never(monkeypatch):
    monkeypatch.setattr(sampling, "_draw", lambda: 999)


def _observed(client, email="s1@example.com", seconds=150):
    rw.login(client, email)
    browser = rw.Browser()
    responses = rw.observe(client, browser, seconds)
    return browser, responses[-1].get_json()


def _offer(client, email="s1@example.com"):
    browser, body = _observed(client, email)
    assert body["prompt"] is not None, body
    return browser, body["prompt"]["id"]


def _display(client, prompt_id):
    return rw.post(client, {}, url=rw.prompt_url(prompt_id, "display"))


def _respond(client, prompt_id, body):
    return rw.post(client, body, url=rw.prompt_url(prompt_id, "respond"))


# ---------------------------------------------------------------------------
# When a prompt is offered
# ---------------------------------------------------------------------------


def test_no_prompt_without_enough_continuous_observation(app, client, people, always):
    rw.login(client, "s1@example.com")
    body = rw.post(client, rw.batch([rw.Browser().event("page_view", detail="wide")])).get_json()
    assert body["prompt"] is None
    session = ResearchSession.query.one()
    assert session.sampling_ineligible_checks == 1 and session.sampling_eligible_checks == 0
    assert ResearchFeedbackPrompt.query.count() == 0


def test_a_random_eligible_moment_offers_one_prompt(app, client, people, always):
    _browser, prompt_id = _offer(client)
    prompt = ResearchFeedbackPrompt.query.one()
    assert prompt.public_id == prompt_id and prompt.status == "offered"
    assert prompt.sampling_reason == "random"
    assert prompt.configuration_id == people["config"].id
    assert ResearchSession.query.one().sampling_eligible_checks >= 1


def test_an_unlucky_draw_offers_nothing_but_is_counted_eligible(app, client, people, never):
    _browser, body = _observed(client)
    assert body["prompt"] is None
    session = ResearchSession.query.one()
    assert session.sampling_eligible_checks >= 1
    assert ResearchFeedbackPrompt.query.count() == 0


def test_a_natural_activity_ending_is_a_sampling_trigger(app, client, people, monkeypatch):
    draws = iter([0])
    monkeypatch.setattr(sampling, "_draw", lambda: next(draws, 999))
    rw.login(client, "s1@example.com")
    browser = rw.Browser()
    monkeypatch.setattr(sampling, "_draw", lambda: 999)
    rw.observe(client, browser, 150)
    with client.session_transaction() as flask_session:
        ref = flask_session["research_session_ref"]
    collection.record_outcomes(people["s1"].id, ref, [collection.Outcome(
        "lesson_completion", "completed", None, None, "student.lesson_detail")], "development")
    assert ResearchSession.query.one().activity_end_pending_at_ms is not None
    monkeypatch.setattr(sampling, "_draw", lambda: next(draws, 999))
    body = rw.post(client, rw.batch([browser.event("heartbeat", detail="active")])).get_json()
    assert body["prompt"] is not None
    assert ResearchFeedbackPrompt.query.one().sampling_reason == "activity_end"
    # The trigger is consumed by that check whatever it decided.
    assert ResearchSession.query.one().activity_end_pending_at_ms is None


def test_errors_play_no_part_in_the_sampling_decision(app, client, people, monkeypatch):
    """The decision reads only budgets, observation and the draw: a batch full
    of refusals and repeated clicks samples exactly like a smooth one."""
    calls = []
    monkeypatch.setattr(sampling, "_draw", lambda: calls.append(1) or 999)
    browser, _ = _observed(client)
    before = len(calls)
    rw.post(client, rw.batch([browser.event("repeated_click", element="quiz_save", count=9),
                              browser.event("form_invalid", element="quiz_save", count=3)]))
    rw.post(client, rw.batch([browser.event("heartbeat", detail="active")]))
    assert len(calls) - before == 2


def test_a_hidden_tab_ends_the_observation_run(app, client, people, always):
    browser, _ = _observed(client, seconds=60)
    ResearchFeedbackPrompt.query.delete()
    db.session.commit()
    rw.post(client, rw.batch([browser.event("visibility_hidden")]))
    session = ResearchSession.query.one()
    assert session.observed_since_ms is None and session.last_observed_at_ms is None


def test_a_zero_budget_configuration_never_prompts(app, client, people, always):
    rw.shift_period(people["config"].id, max_prompts_per_session=0)
    _browser, body = _observed(client)
    assert body["prompt"] is None and ResearchFeedbackPrompt.query.count() == 0


# ---------------------------------------------------------------------------
# Showing it: the display grant and the window
# ---------------------------------------------------------------------------


def test_the_display_grant_fixes_the_exact_pre_prompt_window(app, client, people, always):
    _browser, prompt_id = _offer(client)
    before = now_ms()
    response = _display(client, prompt_id)
    assert response.status_code == 200 and response.get_json()["granted"] is True
    prompt = ResearchFeedbackPrompt.query.one()
    assert prompt.status == "displayed" and prompt.display_slot == 1
    assert before <= prompt.displayed_at_ms <= now_ms()
    assert prompt.window_end_ms == prompt.displayed_at_ms
    assert prompt.window_start_ms == prompt.displayed_at_ms - 120_000
    assert prompt.observed_ms == 120_000
    assert prompt.prompt_day == sampling.local_day(prompt.displayed_at_ms,
                                                   app.config["APP_TIMEZONE"])


def test_events_after_the_display_are_outside_the_window(app, client, people, always):
    browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    rw.post(client, rw.batch([browser.event("heartbeat", detail="active")]))
    prompt = ResearchFeedbackPrompt.query.one()
    last = collection.ResearchEvent.query.order_by(collection.ResearchEvent.id.desc()).first()
    assert last.occurred_at_ms >= prompt.window_end_ms


def test_one_prompt_per_session_even_across_tabs(app, client, people, always):
    browser, prompt_id = _offer(client)
    assert _display(client, prompt_id).get_json()["granted"] is True
    # A second tab tries to show the same prompt: refused, no second slot.
    second = _display(client, prompt_id)
    assert second.status_code == 409 and second.get_json()["status"] == "unavailable"
    # And no second prompt is ever offered in this session.
    body = rw.post(client, rw.batch([browser.event("heartbeat", detail="active")])).get_json()
    assert body["prompt"] is None
    assert ResearchFeedbackPrompt.query.count() == 1


def test_two_prompts_per_day_across_sessions_then_the_next_local_day(app, people, always):
    clients = [app.test_client() for _ in range(3)]
    for index in range(2):
        _browser, prompt_id = _offer(clients[index])
        assert _display(clients[index], prompt_id).get_json()["granted"] is True
    # The third browser session of the same day is ineligible.
    _browser, body = _observed(clients[2])
    assert body["prompt"] is None
    third_session = ResearchSession.query.order_by(ResearchSession.id.desc()).first()
    assert third_session.sampling_ineligible_checks >= 1
    # Move the earlier displays to yesterday: the budget resets.
    from sqlalchemy import text

    db.session.execute(text("UPDATE research_feedback_prompts SET prompt_day = :d"),
                       {"d": "2000-01-01"})
    db.session.commit()
    body = rw.post(clients[2], rw.batch([rw.Browser().event("heartbeat", detail="active")]))
    assert body.get_json()["prompt"] is not None


def test_the_daily_budget_is_re_proved_at_display_time(app, people, always):
    first, second, third = app.test_client(), app.test_client(), app.test_client()
    _b1, offer_one = _offer(first)
    _b2, offer_two = _offer(second)
    _b3, offer_three = _offer(third)   # offered while the day was still open
    assert _display(first, offer_one).get_json()["granted"] is True
    assert _display(second, offer_two).get_json()["granted"] is True
    refused = _display(third, offer_three)
    assert refused.status_code == 409
    assert refused.get_json()["reason"] == "daily_budget"


def test_the_local_day_follows_the_application_timezone():
    moment = int(datetime(2026, 9, 29, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert sampling.local_day(moment, "UTC").isoformat() == "2026-09-29"
    assert sampling.local_day(moment, "UTC+2").isoformat() == "2026-09-30"


def test_an_expired_offer_cannot_be_shown(app, client, people, always):
    _browser, prompt_id = _offer(client)
    rw.age_prompt(ResearchFeedbackPrompt.query.one().id, 601_000)
    response = _display(client, prompt_id)
    assert response.status_code == 409 and response.get_json()["reason"] == "expired"
    prompt = ResearchFeedbackPrompt.query.one()
    assert prompt.status == "offered"
    assert derived_prompt_state(prompt.status, prompt.offered_at_ms, None, 600, 600,
                                now_ms()) == "offer_expired"


def test_deferral_is_recorded_with_its_reason(app, client, people, always):
    _browser, prompt_id = _offer(client)
    url = rw.prompt_url(prompt_id, "defer")
    assert rw.post(client, {"reason": "timed_activity"}, url=url).get_json()["deferred"] is True
    assert rw.post(client, {"reason": "hidden_tab"}, url=url).status_code == 200
    prompt = ResearchFeedbackPrompt.query.one()
    assert prompt.deferral_count == 2 and prompt.last_deferral_reason == "hidden_tab"
    assert rw.post(client, {"reason": "bored"}, url=url).status_code == 400
    assert rw.post(client, {"reason": "recording", "x": 1}, url=url).status_code == 400
    _display(client, prompt_id)
    assert rw.post(client, {"reason": "recording"}, url=url).status_code == 409


# ---------------------------------------------------------------------------
# Answering, skipping, not answering
# ---------------------------------------------------------------------------


def test_an_answer_keeps_the_raw_rating_and_the_optional_causes(app, client, people, always):
    _browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    response = _respond(client, prompt_id, {"rating": 4, "causes": ["interface", "technical"]})
    assert response.status_code == 200 and response.get_json()["recorded"] is True
    prompt = ResearchFeedbackPrompt.query.one()
    assert prompt.status == "answered" and prompt.rating == 4
    assert (prompt.cause_interface, prompt.cause_technical, prompt.cause_content,
            prompt.cause_other, prompt.cause_unsure) == (True, True, False, False, False)
    assert prompt.responded_at_ms >= prompt.displayed_at_ms


def test_a_rating_without_causes_is_complete(app, client, people, always):
    _browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    assert _respond(client, prompt_id, {"rating": 1}).status_code == 200
    assert ResearchFeedbackPrompt.query.one().rating == 1


def _stored(prompt_public_id):
    db.session.expire_all()
    prompt = ResearchFeedbackPrompt.query.filter_by(public_id=prompt_public_id).one()
    return (prompt.status, prompt.rating, prompt.cause_interface, prompt.cause_technical,
            prompt.cause_content, prompt.cause_other, prompt.cause_unsure, prompt.responded_at_ms)


def test_a_repeated_answer_is_refused_and_the_first_kept(app, client, people, always):
    """A stored response is final. Repeating exactly the same choice is
    "already" (a retry whose answer was lost); any other choice is
    "different" and changes nothing -- never "already", which the browser
    would acknowledge as if the new choice had been recorded."""
    _browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    first = _respond(client, prompt_id, {"rating": 2, "causes": ["technical", "content"]})
    assert first.status_code == 200 and first.get_json()["recorded"] is True
    kept = _stored(prompt_id)
    same = _respond(client, prompt_id, {"rating": 2, "causes": ["content", "technical"]})
    assert same.status_code == 409
    assert same.get_json() == {"collecting": True, "recorded": False, "status": "already"}
    for other in ({"rating": 5}, {"rating": 2}, {"rating": 2, "causes": ["technical"]},
                  {"rating": 2, "causes": ["technical", "content", "other"]},
                  {"dismissed": True}):
        again = _respond(client, prompt_id, other)
        assert again.status_code == 409, other
        assert again.get_json() == {"collecting": True, "recorded": False,
                                    "status": "different"}, other
    assert _stored(prompt_id) == kept
    assert kept[:7] == ("answered", 2, False, True, True, False, False)


def test_a_rating_after_a_stored_skip_never_replaces_it(app, client, people, always):
    """The reverse case: Skip was stored (its answer lost), then a rating is
    sent. The dismissal stays; the rating is refused as different."""
    _browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    assert _respond(client, prompt_id, {"dismissed": True}).get_json()["recorded"] is True
    kept = _stored(prompt_id)
    rating = _respond(client, prompt_id, {"rating": 4, "causes": ["interface"]})
    assert rating.status_code == 409 and rating.get_json()["status"] == "different"
    again = _respond(client, prompt_id, {"dismissed": True})
    assert again.status_code == 409 and again.get_json()["status"] == "already"
    assert _stored(prompt_id) == kept and kept[:2] == ("dismissed", None)


def test_another_students_retry_learns_nothing_about_a_stored_response(app, client, people,
                                                                      always):
    """Comparison happens only after ownership is proved: another Student
    gets the same 404 whatever is stored or sent."""
    _browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    _respond(client, prompt_id, {"rating": 3})
    other = app.test_client()
    rw.login(other, "s2@example.com")
    rw.post(other, rw.batch([rw.Browser().event("page_view", detail="wide")]))
    for body in ({"rating": 3}, {"rating": 1}, {"dismissed": True}):
        response = rw.post(other, body, url=rw.prompt_url(prompt_id, "respond"))
        assert response.status_code == 404 and response.get_json() == {"error": "not_found"}
    assert _stored(prompt_id)[:2] == ("answered", 3)


def test_skipping_stores_no_label_and_stops_prompts_in_that_session(app, client, people,
                                                                   always):
    browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    assert _respond(client, prompt_id, {"dismissed": True}).get_json()["recorded"] is True
    prompt = ResearchFeedbackPrompt.query.one()
    assert prompt.status == "dismissed" and prompt.rating is None
    assert not any((prompt.cause_interface, prompt.cause_technical, prompt.cause_content,
                    prompt.cause_other, prompt.cause_unsure))
    session = ResearchSession.query.one()
    assert session.prompt_dismissed_at_ms is not None
    rw.shift_period(people["config"].id, max_prompts_per_session=3)
    body = rw.post(client, rw.batch([browser.event("heartbeat", detail="active")])).get_json()
    assert body["prompt"] is None


def test_a_late_answer_is_flagged_and_not_stored(app, client, people, always):
    _browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    rw.age_prompt(ResearchFeedbackPrompt.query.one().id, 601_000)
    late = _respond(client, prompt_id, {"rating": 3})
    assert late.status_code == 409 and late.get_json()["status"] == "late"
    prompt = ResearchFeedbackPrompt.query.one()
    assert prompt.status == "displayed" and prompt.rating is None and prompt.late_response
    assert derived_prompt_state(prompt.status, prompt.offered_at_ms, prompt.displayed_at_ms,
                                600, 600, now_ms()) == "no_response"


def test_an_unshown_prompt_cannot_be_answered(app, client, people, always):
    _browser, prompt_id = _offer(client)
    response = _respond(client, prompt_id, {"rating": 3})
    assert response.status_code == 409 and response.get_json()["status"] == "unavailable"


@pytest.mark.parametrize("body", [
    {"rating": 0}, {"rating": 6}, {"rating": "4"}, {"rating": True}, {"rating": 3.5},
    {"rating": 3, "causes": ["boredom"]}, {"rating": 3, "causes": ["other", "other"]},
    {"rating": 3, "comment": "free text"}, {"dismissed": False}, {"dismissed": "yes"},
    {"rating": 3, "dismissed": True}, {}, [],
])
def test_only_the_declared_answer_shapes_are_accepted(app, client, people, always, body):
    _browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    assert _respond(client, prompt_id, body).status_code == 400
    assert ResearchFeedbackPrompt.query.one().status == "displayed"


def test_another_student_cannot_touch_the_prompt(app, people, always):
    owner, other = app.test_client(), app.test_client()
    _browser, prompt_id = _offer(owner)
    rw.login(other, "s2@example.com")
    rw.observe(other, rw.Browser(), 10)
    for action, body in (("display", {}), ("defer", {"reason": "hidden_tab"}),
                         ("respond", {"rating": 5})):
        assert rw.post(other, body, url=rw.prompt_url(prompt_id, action)).status_code == 404
    assert ResearchFeedbackPrompt.query.filter_by(public_id=prompt_id).one().status == "offered"


def test_a_paused_collection_stores_no_answer(app, client, people, always):
    _browser, prompt_id = _offer(client)
    _display(client, prompt_id)
    people["config"].is_collecting = False
    db.session.commit()
    response = _respond(client, prompt_id, {"rating": 5})
    assert response.get_json() == {"collecting": False}
    assert ResearchFeedbackPrompt.query.one().rating is None


def test_unknown_prompt_actions_and_ids_are_404(app, client, people, always):
    _browser, prompt_id = _offer(client)
    assert rw.post(client, {}, url=rw.prompt_url(prompt_id, "label")).status_code == 404
    assert rw.post(client, {}, url=rw.prompt_url("not-a-uuid", "display")).status_code == 404
    assert _display(client, "00000000-0000-4000-8000-000000000000").status_code == 404


def test_the_display_grant_takes_the_documented_lock_order(app, client, people, always,
                                                          monkeypatch):
    _browser, prompt_id = _offer(client)
    locked = []
    original = Query.with_for_update

    def record(self, *args, **kwargs):
        locked.append(self.column_descriptions[0]["entity"].__tablename__)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", record)
    _display(client, prompt_id)
    assert locked == ["users", "research_configurations", "research_subjects",
                      "research_sessions", "research_feedback_prompts"]


def test_the_student_facing_question_is_honest_and_complete(app, client, people):
    rw.login(client, "s1@example.com")
    html = rw.get(client, "/student/dashboard").get_data(as_text=True)
    assert "Thinking about the last two minutes on this platform, how frustrated did you feel?" \
        in html
    for value, label in ((1, "Not frustrated at all"), (2, "Slightly frustrated"),
                         (3, "Moderately frustrated"), (4, "Very frustrated"),
                         (5, "Extremely frustrated")):
        assert f"<strong>{value}</strong> &mdash; {label}" in html
    for label in ("The interface was hard to use", "A technical delay or problem",
                  "The learning content was difficult", "Something else", "I&#39;m not sure"):
        assert label in html
    assert "data-feedback-skip" in html and ">Skip<" in html
    assert "does\n      not affect your grades or your access" in html or \
        "not affect your grades or your access" in " ".join(html.split())
    assert "<textarea" not in html.split('id="research-feedback"', 1)[1].split("</section>")[0]
    assert "satisf" not in html.lower() and "difficulty of the task" not in html.lower()
    # What the Student can read: no research, study or usage wording, no claim
    # of a purpose (grades, performance, support), and no researcher identity.
    import re
    section = html.split('id="research-feedback"', 1)[1].split("</section>", 1)[0]
    visible = " ".join(re.sub(r"<[^>]+>", " ", section.split(">", 1)[1]).split()).lower()
    for word in ("research", "study", "usage", "experiment", "researcher", "support",
                 "performance", "consent"):
        assert word not in visible, word
    assert "help us" not in visible and "will be used" not in visible
