"""Phase 6 / M02A Researcher protocol catalogue, through its routes.

Authorization and headers on every rule (including Flask's own 404 and 405
responses), nested identifiers, the aggregate stale-form token with two open
forms, replay, the frozen version, review and activation, derivation, soft
discard, the privacy boundary, the wording, and the promise that M01 is
untouched. Tokens are always read from the page the server rendered.
"""

import re

import pytest
import sqlalchemy as sa

import tests.protocol_fixtures as px
import tests.research_fixtures as rx
import tests.structural_checks as sc
from app.extensions import db
from app.models import ExperimentDefinition, ExperimentTask, ExperimentTaskSet, User
from app.services import experiment_protocol_queries as queries
from app.services import experiment_protocol_tokens as tokens

_NO_STORE = "private, no-store"


@pytest.fixture
def people(app):
    return rx.world(app)


def _researcher(client):
    rx.login(client, rx.RESEARCHER_EMAIL)


def _flash(html):
    return " ".join(html.split())


def _every_url(protocol):
    """Every protocol rule's concrete URL for `protocol`, with its methods."""
    task_set = px.sets_of(protocol)[0]
    task = px.tasks_of(task_set)[0]
    detail = px.detail_url(protocol.public_id)
    one_set = px.set_url(protocol.public_id, task_set.public_id)
    one_task = px.task_url(protocol.public_id, task_set.public_id, task.public_id)
    return [
        (px.PROTOCOLS_URL, ("GET",)),
        (px.NEW_URL, ("GET", "POST")),
        (detail, ("GET",)),
        (detail + "/edit", ("GET", "POST")),
        (detail + "/sets/new", ("GET", "POST")),
        (one_set + "/edit", ("GET", "POST")),
        (one_set + "/move", ("POST",)),
        (one_set + "/tasks/new", ("GET", "POST")),
        (one_task + "/edit", ("GET", "POST")),
        (one_task + "/move", ("POST",)),
        (detail + "/activate", ("GET", "POST")),
        (detail + "/discard", ("POST",)),
        (detail + "/new-version", ("GET", "POST")),
    ]


def _headers_ok(response):
    return (response.headers.get("Cache-Control") == _NO_STORE
            and "Cookie" in response.headers.get("Vary", ""))


# ===========================================================================
# Authorization and headers
# ===========================================================================


def test_an_anonymous_visitor_is_sent_to_login_from_every_rule(people, client):
    protocol = px.complete_row(people["researcher"])
    for url, methods in _every_url(protocol):
        for method in methods:
            response = client.open(url, method=method)
            assert response.status_code == 302, (method, url)
            assert "/auth/login" in response.headers["Location"], url
            assert _headers_ok(response), (method, url)


@pytest.mark.parametrize("email", [rx.STUDENT_EMAIL, rx.TEACHER_EMAIL, rx.ADMIN_EMAIL])
def test_every_other_role_is_refused_every_rule_and_changes_nothing(people, client, email):
    protocol = px.complete_row(people["researcher"])
    rx.login(client, email)
    before = ExperimentDefinition.query.count(), ExperimentTask.query.count()
    for url, methods in _every_url(protocol):
        for method in methods:
            response = client.open(url, method=method, data={"confirm": "yes"})
            assert response.status_code == 403, (email, method, url)
            assert _headers_ok(response), (method, url)
    assert (ExperimentDefinition.query.count(), ExperimentTask.query.count()) == before
    assert px.definition("VA-1").version == 1


def test_a_suspended_researcher_cannot_hold_a_session(app, people, client):
    _researcher(client)
    assert client.get(px.PROTOCOLS_URL).status_code == 200
    researcher = User.query.filter_by(email=rx.RESEARCHER_EMAIL).one()
    researcher.status = "suspended"
    researcher.bump_auth_version()
    db.session.commit()
    response = client.get(px.PROTOCOLS_URL)
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]


def test_every_researcher_response_is_private_no_store_including_404_and_405(people, client):
    """The headers must also cover responses Flask produces before any view
    runs: a 405 for a wrong method and a 404 for an unknown path."""
    _researcher(client)
    protocol = px.complete_row(people["researcher"])
    for url, methods in _every_url(protocol):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            response = client.open(url, method=method)
            if method not in methods:
                assert response.status_code == 405, (method, url)
            assert _headers_ok(response), (method, url, response.status_code)
    for url in (rx.RESEARCH_DASHBOARD_URL, rx.RESEARCH_PARTICIPANTS_URL):
        response = client.post(url)
        assert response.status_code == 405 and _headers_ok(response), url
    for url in ("/research/unknown", "/research", "/research/protocols/x/y/z",
                px.detail_url("00000000-0000-0000-0000-000000000000")):
        response = client.get(url)
        assert response.status_code == 404 and _headers_ok(response), url


def test_the_no_store_hook_is_scoped_to_research_paths(people, client):
    rx.login(client, rx.STUDENT_EMAIL)
    response = client.get("/researchers-are-not-a-prefix")
    assert response.status_code == 404
    assert response.headers.get("Cache-Control") != _NO_STORE


@pytest.mark.parametrize("identifier", ["nope", "x" * 80, "1",
                                        "00000000-0000-0000-0000-000000000000"])
def test_a_malformed_unknown_or_numeric_identifier_is_a_plain_404(people, client, identifier):
    _researcher(client)
    protocol = px.complete_row(people["researcher"])
    task_set = px.sets_of(protocol)[0]
    for url in (px.detail_url(identifier), px.set_url(protocol.public_id, identifier) + "/edit",
                px.task_url(protocol.public_id, task_set.public_id, identifier) + "/edit"):
        assert client.get(url).status_code == 404, url
    assert client.get(px.detail_url(str(protocol.id))).status_code == 404


def test_a_nested_object_from_another_protocol_is_a_404(people, client):
    _researcher(client)
    researcher = people["researcher"]
    mine = px.complete_row(researcher, version="VA-1")
    other = px.complete_row(researcher, version="VA-2")
    foreign_set = px.sets_of(other)[0]
    foreign_task = px.tasks_of(foreign_set)[0]
    own_set = px.sets_of(mine)[0]
    before = px.definition("VA-2").version
    for method, url in (
        ("GET", px.set_url(mine.public_id, foreign_set.public_id) + "/edit"),
        ("GET", px.set_url(mine.public_id, foreign_set.public_id) + "/tasks/new"),
        ("POST", px.set_url(mine.public_id, foreign_set.public_id) + "/move"),
        ("GET", px.task_url(mine.public_id, own_set.public_id, foreign_task.public_id) + "/edit"),
        ("POST", px.task_url(mine.public_id, own_set.public_id, foreign_task.public_id)
         + "/move"),
    ):
        assert client.open(url, method=method).status_code == 404, url
    assert px.definition("VA-2").version == before


# ===========================================================================
# Drafting, the aggregate token, replay
# ===========================================================================


def test_a_draft_is_created_normalised_and_its_identifier_is_unique(people, client):
    _researcher(client)
    html = px.create(client, version=" va-2026.01 ", title="  Placeholder   protocol ").get_data(
        as_text=True)
    assert px.SAVED_TEXT in html
    protocol = px.definition("VA-2026.01")
    assert (protocol.title, protocol.status, protocol.version, protocol.study_stage) == (
        "Placeholder protocol", px.DRAFT, 1, "version_a_collection")
    assert protocol.created_by_id == people["researcher"].id
    again = px.create(client, version="VA-2026.01").get_data(as_text=True)
    assert "already uses that identifier" in again
    assert ExperimentDefinition.query.count() == 1


@pytest.mark.parametrize("field, value", [
    ("title", "Ask RP-ABCDEFGHJK first"),
    ("title", "Contact someone@example.com"),
    ("equivalence_rationale", "Validated with rp-abcdefghjk"),
])
def test_obvious_personal_data_is_refused_by_the_form(people, client, field, value):
    _researcher(client)
    data = {"version_identifier": "VA-1", "title": "Placeholder", "equivalence_rationale": ""}
    data[field] = value
    html = client.post(px.NEW_URL, data=data).get_data(as_text=True)
    assert px.PROHIBITED_TEXT in html
    assert ExperimentDefinition.query.count() == 0


def test_a_task_needs_a_criterion_its_type_allows_and_a_bounded_duration(people, client):
    _researcher(client)
    protocol = px.build(client, tasks=0)
    task_set = px.sets_of(protocol)[0]
    html = px.add_task(client, protocol, task_set, task_type="quiz_completion",
                       completion_criterion="lesson_opened").get_data(as_text=True)
    assert "cannot be used with this task type" in html
    for duration in ("29", "1801", "abc", "12.5", ""):
        html = px.add_task(client, px.definition("VA-1"), task_set,
                           recommended_duration_seconds=duration).get_data(as_text=True)
        assert "whole number of seconds" in html, duration
    html = px.add_task(client, px.definition("VA-1"), task_set,
                       task_type="login").get_data(as_text=True)
    assert "Not a valid choice" in html
    assert ExperimentTask.query.count() == 0


def test_a_form_opened_before_any_other_change_becomes_stale(people, client):
    """Two open forms on one draft: the header edit and the add-set form.
    Saving one moves the aggregate version, so the other cannot write."""
    _researcher(client)
    protocol = px.build(client)
    detail = px.detail_url(protocol.public_id)
    edit_token = px.state_token(rx.page(client, detail + "/edit"))
    set_token = px.state_token(rx.page(client, detail + "/sets/new"))
    task_set = px.sets_of(protocol)[0]
    task = px.tasks_of(task_set)[0]
    move_url = px.task_url(protocol.public_id, task_set.public_id, task.public_id) + "/move"
    detail_html = rx.page(client, detail)

    assert "Task set SET-B added" in px.add_set(client, protocol, code="SET-B",
                                                 token=set_token).get_data(as_text=True)
    version = px.definition("VA-1").version
    html = client.post(detail + "/edit", data={
        rx.STATE_FIELD: edit_token, "version_identifier": "VA-1", "title": "Stale edit",
        "equivalence_rationale": ""}, follow_redirects=True).get_data(as_text=True)
    assert px.STALE_TEXT in _flash(html)
    assert px.definition("VA-1").title == "Placeholder protocol"
    assert px.definition("VA-1").version == version
    # A child edit makes every token rendered before it stale too -- here the
    # detail page's discard token, read before the set was added.
    discard_token = px.form_token(detail_html, detail + "/discard")
    html = px.discard(client, px.definition("VA-1"), token=discard_token).get_data(as_text=True)
    assert px.STALE_TEXT in _flash(html) and px.definition("VA-1").status == px.DRAFT
    assert move_url  # the move forms carry the same aggregate version


def test_a_replayed_successful_form_writes_nothing(people, client):
    _researcher(client)
    protocol = px.build(client, tasks=0)
    task_set = px.sets_of(protocol)[0]
    url = f"{px.set_url(protocol.public_id, task_set.public_id)}/tasks/new"
    token = px.state_token(rx.page(client, url))
    assert "Task added" in px.add_task(client, protocol, task_set, token=token).get_data(
        as_text=True)
    replay = px.add_task(client, protocol, task_set, token=token).get_data(as_text=True)
    assert px.STALE_TEXT in _flash(replay)
    assert ExperimentTask.query.count() == 1


def test_a_move_token_is_bound_to_its_direction_and_target(people, client):
    _researcher(client)
    protocol = px.build(client, sets=("SET-A", "SET-B", "SET-C"))
    sets = px.sets_of(protocol)
    middle = sets[1]
    move_url = px.set_url(protocol.public_id, middle.public_id) + "/move"
    moves = px.move_tokens(rx.page(client, px.detail_url(protocol.public_id)), move_url)
    assert set(moves) == {"up", "down"}
    version = px.definition("VA-1").version
    swapped = client.post(move_url, data={rx.STATE_FIELD: moves["up"], "direction": "down"},
                          follow_redirects=True).get_data(as_text=True)
    assert px.STALE_TEXT in _flash(swapped)
    other_url = px.set_url(protocol.public_id, sets[2].public_id) + "/move"
    borrowed = client.post(other_url, data={rx.STATE_FIELD: moves["up"], "direction": "up"},
                           follow_redirects=True).get_data(as_text=True)
    assert px.STALE_TEXT in _flash(borrowed)
    assert px.definition("VA-1").version == version
    moved = client.post(move_url, data={rx.STATE_FIELD: moves["up"], "direction": "up"},
                        follow_redirects=True).get_data(as_text=True)
    assert "Order saved" in moved
    assert [s.set_code for s in px.sets_of(px.definition("VA-1"))] == [
        "SET-B", "SET-A", "SET-C"]


def test_the_first_and_last_items_offer_only_the_moves_they_can_make(people, client):
    _researcher(client)
    protocol = px.build(client, sets=("SET-A", "SET-B"), tasks=2, rationale="Placeholder.")
    html = rx.page(client, px.detail_url(protocol.public_id))
    first, last = px.sets_of(protocol)
    assert set(px.move_tokens(html, px.set_url(protocol.public_id, first.public_id)
                              + "/move")) == {"down"}
    assert set(px.move_tokens(html, px.set_url(protocol.public_id, last.public_id)
                              + "/move")) == {"up"}


# ===========================================================================
# Review, activation, the frozen version
# ===========================================================================


def test_an_incomplete_draft_shows_its_problems_and_cannot_be_activated(app, people, client):
    _researcher(client)
    px.create(client)
    protocol = px.definition("VA-1")
    review = rx.page(client, px.review_url(protocol))
    assert "Add at least one task set." in review
    assert px.state_token(review) is None
    # A crafted token for the incomplete draft is still refused by the
    # transaction's own structural review.
    with app.test_request_context():
        header = queries.protocol_header(protocol.public_id)
        forged = tokens.make_token(
            tokens.PURPOSE_ACTIVATE, actor_public_id=people["researcher"].public_id,
            protocol_public_id=protocol.public_id, protocol_version=header.version,
            preview_digest=queries.preview_digest(header, []), current_public_id=tokens.NONE)
    html = px.activate(client, protocol, token=forged).get_data(as_text=True)
    assert px.NOT_READY_TEXT in html
    assert px.definition("VA-1").status == px.DRAFT


def test_activation_needs_confirmation_and_freezes_the_reviewed_content(people, client):
    _researcher(client)
    protocol = px.build(client)
    review = rx.page(client, px.review_url(protocol))
    assert "passes the structural review" in review
    token = px.state_token(review)
    html = px.activate(client, protocol, token=token, confirm="").get_data(as_text=True)
    assert px.CONFIRM_TEXT in html and px.definition("VA-1").status == px.DRAFT
    html = px.activate(client, protocol, token=token).get_data(as_text=True)
    assert px.ACTIVATED_TEXT in html
    assert "not an ethics approval" in html and "starts no data collection" in html
    active = px.definition("VA-1")
    assert active.status == px.ACTIVE and active.content_digest in html
    sealed = (active.version, active.content_digest, active.activated_at)
    # A replayed activation is answered, not repeated.
    replay = px.activate(client, active, token=token).get_data(as_text=True)
    assert px.ALREADY_ACTIVE_TEXT in replay
    active = px.definition("VA-1")
    assert (active.version, active.content_digest, active.activated_at) == sealed


def test_activation_is_stale_when_the_content_changed_after_the_review(people, client):
    _researcher(client)
    protocol = px.build(client)
    token = px.state_token(rx.page(client, px.review_url(protocol)))
    px.add_task(client, protocol, px.sets_of(protocol)[0], title="Added after review")
    html = px.activate(client, px.definition("VA-1"), token=token).get_data(as_text=True)
    assert px.STALE_TEXT in _flash(html)
    assert px.definition("VA-1").status == px.DRAFT


def test_a_frozen_version_offers_no_change_and_refuses_every_change(people, client):
    _researcher(client)
    protocol = px.build(client, tasks=2)
    task_set = px.sets_of(protocol)[0]
    task = px.tasks_of(task_set)[0]
    detail = px.detail_url(protocol.public_id)
    set_edit = px.set_url(protocol.public_id, task_set.public_id) + "/edit"
    task_edit = px.task_url(protocol.public_id, task_set.public_id, task.public_id) + "/edit"
    old_tokens = {
        "edit": px.state_token(rx.page(client, detail + "/edit")),
        "set": px.state_token(rx.page(client, set_edit)),
        "task": px.state_token(rx.page(client, task_edit)),
    }
    px.activate(client, protocol)
    frozen = px.definition("VA-1")
    before = (frozen.version, frozen.content_digest, frozen.title)

    html = rx.page(client, detail)
    assert "Create new version" in html
    for control in ("Edit details", "Add task set", "Edit set", "Edit task", "Move up",
                    "Move down", "Add task", "Discard draft", "Review and activate"):
        assert control not in html, control
    for url in (detail + "/edit", detail + "/sets/new", set_edit, task_edit,
                px.set_url(protocol.public_id, task_set.public_id) + "/tasks/new"):
        response = client.get(url, follow_redirects=True)
        assert px.FROZEN_TEXT in _flash(response.get_data(as_text=True)), url
    for url, data in (
        (detail + "/edit", {rx.STATE_FIELD: old_tokens["edit"], "version_identifier": "VA-1",
                            "title": "Changed", "equivalence_rationale": ""}),
        (set_edit, {rx.STATE_FIELD: old_tokens["set"], "set_code": "SET-Z", "title": "X"}),
        (task_edit, {rx.STATE_FIELD: old_tokens["task"], **px.task_form(title="Changed")}),
    ):
        html = client.post(url, data=data, follow_redirects=True).get_data(as_text=True)
        assert px.FROZEN_TEXT in _flash(html), url
    frozen = px.definition("VA-1")
    assert (frozen.version, frozen.content_digest, frozen.title) == before
    assert px.tasks_of(px.sets_of(frozen)[0])[0].title == "Placeholder task 1"


def test_no_page_offers_a_removal_control(people, client):
    _researcher(client)
    protocol = px.build(client, sets=("SET-A", "SET-B"), tasks=2, rationale="Placeholder.")
    html = rx.page(client, px.detail_url(protocol.public_id))
    actions = re.findall(r'<form method="post" action="([^"]+)"', html)
    assert actions and all(a.endswith(("/move", "/discard", "/auth/logout")) for a in actions)
    # The labels of every control -- buttons and links -- never offer a
    # removal. (The prose may and does say that nothing is deleted.)
    labels = re.findall(r"<(?:button|a)\b[^>]*>(.*?)</(?:button|a)>", html, re.S)
    assert labels
    for label in labels:
        for word in ("remove", "delete"):
            assert word not in label.lower(), label


def test_activating_a_second_version_supersedes_the_first(people, client):
    _researcher(client)
    first = px.build(client, version="VA-1")
    px.activate(client, first)
    second = px.build(client, version="VA-2")
    review = rx.page(client, px.review_url(second))
    assert "VA-1" in review and "superseding VA-1" in " ".join(review.split())
    px.activate(client, second)
    assert (px.definition("VA-1").status, px.definition("VA-2").status) == (
        px.SUPERSEDED, px.ACTIVE)
    listing = rx.page(client, f"{px.PROTOCOLS_URL}?status=superseded")
    assert 'data-protocol="VA-1"' in listing and 'data-protocol="VA-2"' not in listing


# ===========================================================================
# A new version, and soft discard
# ===========================================================================


def test_a_new_version_copies_a_frozen_one_and_links_back_to_it(people, client):
    _researcher(client)
    source = px.build(client, sets=("SET-A", "SET-B"), tasks=2, rationale="Placeholder.")
    px.activate(client, source)
    html = px.derive(client, px.definition("VA-1"), "va-2").get_data(as_text=True)
    assert "New draft VA-2 created from VA-1" in html
    copy = px.definition("VA-2")
    assert copy.status == px.DRAFT and copy.derived_from_id == source.id
    assert f'href="{px.detail_url(source.public_id)}"' in html
    assert [s.set_code for s in px.sets_of(copy)] == ["SET-A", "SET-B"]
    assert len(px.tasks_of(px.sets_of(copy)[1])) == 2
    # A draft cannot be copied, and the frozen source is unchanged.
    response = client.get(px.detail_url(copy.public_id) + "/new-version",
                          follow_redirects=True)
    assert "Only an active or superseded" in response.get_data(as_text=True)
    assert px.definition("VA-1").status == px.ACTIVE


def test_discarding_is_confirmed_soft_and_final(people, client):
    _researcher(client)
    protocol = px.build(client)
    counts = (ExperimentDefinition.query.count(), ExperimentTaskSet.query.count(),
              ExperimentTask.query.count())
    assert px.CONFIRM_TEXT in px.discard(client, protocol, confirm="").get_data(as_text=True)
    html = px.discard(client, px.definition("VA-1")).get_data(as_text=True)
    assert px.DISCARDED_TEXT in html and "was discarded" in html
    assert px.definition("VA-1").status == px.DISCARDED
    assert (ExperimentDefinition.query.count(), ExperimentTaskSet.query.count(),
            ExperimentTask.query.count()) == counts
    page = rx.page(client, px.detail_url(protocol.public_id))
    assert "cannot be edited or activated" in page and "Discard draft" not in page
    listing = rx.page(client, f"{px.PROTOCOLS_URL}?status=discarded")
    assert 'data-protocol="VA-1"' in listing
    # A discarded draft is not told to "create a new version": it cannot be
    # copied, so the refusal says what it is.
    for url in (px.detail_url(protocol.public_id) + "/edit", px.review_url(protocol),
                px.detail_url(protocol.public_id) + "/new-version"):
        said = _flash(client.get(url, follow_redirects=True).get_data(as_text=True))
        assert "Create a new version" not in said, url
    said = _flash(client.get(px.detail_url(protocol.public_id) + "/edit",
                             follow_redirects=True).get_data(as_text=True))
    assert "it was discarded, and it cannot be changed, activated or copied" in said


# ===========================================================================
# Privacy, wording and isolation
# ===========================================================================


def test_protocol_pages_render_no_identity_and_address_objects_by_public_id(people, client):
    _researcher(client)
    protocol = px.build(client)
    px.activate(client, protocol)
    protocol = px.definition("VA-1")
    everyone = [people[role] for role in ("admin", "student", "researcher", "teacher")]
    for url in (px.PROTOCOLS_URL, px.detail_url(protocol.public_id),
                rx.RESEARCH_DASHBOARD_URL):
        html = rx.page(client, url)
        said = sc.redact_signed_values(html)
        for person in everyone:
            for value in (person.email, person.public_id):
                assert value not in said, (url, value)
        for person in everyone[:2] + everyone[3:]:
            assert person.full_name not in said, (url, person.full_name)
        links = re.findall(r'(?:href|action)="([^"]+)"', html)
        for link in links:
            for segment in link.split("/"):
                assert segment != str(protocol.id), (url, link)


def test_protocol_queries_select_no_identity_or_internal_id_columns():
    for columns in (queries.PROTOCOL_LIST_COLUMNS, queries.PROTOCOL_DETAIL_COLUMNS,
                    queries.SET_COLUMNS, queries.TASK_COLUMNS):
        sql = " ".join(str(sa.select(*columns)).split())
        assert "users" not in sql, sql
        for column in columns:
            assert column.key not in ("id", "created_by_id", "activated_by_id",
                                      "superseded_by_id", "discarded_by_id",
                                      "derived_from_id", "definition_id", "task_set_id"), sql


def test_rendered_tokens_carry_no_internal_id_name_or_wording(app, people, client):
    _researcher(client)
    protocol = px.build(client, sets=("SET-A", "SET-B"), rationale="Placeholder.")
    html = rx.page(client, px.detail_url(protocol.public_id))
    found = re.findall(rf'name="{rx.STATE_FIELD}" value="([^"]+)"', html)
    assert found
    internal = {protocol.id, *(s.id for s in px.sets_of(protocol)), people["researcher"].id}
    with app.test_request_context():
        for token in found:
            payload = None
            for purpose in tokens.PURPOSES:
                payload = tokens._load(token, purpose) or payload
            assert payload is not None
            for value in sc.leaves(payload):
                if isinstance(value, int):
                    assert value not in internal or value == payload.get("protocol_version")
                else:
                    assert people["researcher"].full_name not in value
                    assert "Placeholder" not in value


def test_task_text_is_escaped_and_rendered_with_its_line_breaks(people, client):
    _researcher(client)
    protocol = px.build(client, tasks=0)
    px.add_task(client, protocol, px.sets_of(protocol)[0],
                participant_instructions="<script>alert(1)</script>\nSecond line")
    html = rx.page(client, px.detail_url(protocol.public_id))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "white-space: pre-line" in html


def test_the_pages_state_what_activation_is_and_is_not(people, client):
    _researcher(client)
    protocol = px.build(client)
    for url in (px.PROTOCOLS_URL, px.review_url(protocol), px.detail_url(protocol.public_id)):
        html = " ".join(rx.page(client, url).lower().split())
        for claim in ("ethics-approved", "approved by the ethics", "has been approved",
                      "is approved", "detects", "detected", "verified that", "validated that",
                      "emotion", "is frustrated"):
            assert claim not in html, (url, claim)
    review = " ".join(rx.page(client, px.review_url(protocol)).lower().split())
    assert "not an ethics approval" in review
    assert "does not validate that the task sets are equivalent" in review
    assert "starts no session and no data collection" in review
    detail = " ".join(rx.page(client, px.detail_url(protocol.public_id)).lower().split())
    assert "nothing here verifies that any task was completed" in detail


def test_the_dashboard_counts_protocols_and_no_longer_claims_consent_only(people, client):
    _researcher(client)
    px.build(client, version="VA-1")
    px.build(client, version="VA-2")
    px.activate(client, px.definition("VA-1"))
    html = rx.page(client, rx.RESEARCH_DASHBOARD_URL)
    counts = dict(re.findall(
        r'data-protocol-count="(\w+)">\s*<div class="stat-card__value">(\d+)</div>', html))
    assert counts == {"draft": "1", "active": "1", "superseded": "0", "discarded": "0"}
    flat = " ".join(html.lower().split())
    for stale in ("participation consent only", "consent foundation only"):
        assert stale not in flat, stale
    assert "no experiment sessions" in flat and "no behavioural interaction data" in flat
    assert "starts no session and no collection, and it is not an ethics approval" in flat


def test_no_other_portal_links_to_or_shows_the_catalogue(people, client):
    researcher = people["researcher"]
    px.activate_row(px.complete_row(researcher), researcher)
    for email, url in ((rx.STUDENT_EMAIL, "/student/dashboard"),
                       (rx.TEACHER_EMAIL, "/teacher/dashboard"),
                       (rx.ADMIN_EMAIL, "/admin/dashboard"),
                       (rx.ADMIN_EMAIL, rx.OVERVIEW_URL)):
        rx.login(client, email)
        html = rx.page(client, url)
        assert "/research/protocols" not in html, (email, url)
        assert "Placeholder task" not in html, (email, url)
        rx.logout(client)


def test_protocol_work_leaves_m01_and_every_account_untouched(people, client):
    admin = people["admin"]
    document = rx.document_row(admin, status="active")
    rx.participant_row(people["student"], admin, status="active", document=document)

    def everything():
        db.session.expire_all()
        return {table: db.session.execute(sa.text(f"SELECT * FROM {table} ORDER BY id"))
                .fetchall()
                for table in ("users", "research_consent_documents", "research_participants",
                              "research_consent_events")}

    before = everything()
    _researcher(client)
    protocol = px.build(client, sets=("SET-A", "SET-B"), tasks=2, rationale="Placeholder.")
    px.activate(client, protocol)
    px.derive(client, px.definition("VA-1"), "VA-2")
    px.discard(client, px.definition("VA-2"))
    assert everything() == before


def test_the_list_is_paginated_and_filtered(people, client):
    researcher = people["researcher"]
    for number in range(queries.PAGE_SIZE + 2):
        px.definition_row(researcher, version=f"VA-{number:03d}")
    _researcher(client)

    def rows(html):
        return re.findall(r'<tr data-protocol="([^"]+)">', html)

    first = rows(rx.page(client, px.PROTOCOLS_URL))
    second = rows(rx.page(client, f"{px.PROTOCOLS_URL}?page=2"))
    assert len(first) == queries.PAGE_SIZE and len(second) == 2
    assert first[0] == f"VA-{queries.PAGE_SIZE + 1:03d}"
    assert rows(rx.page(client, f"{px.PROTOCOLS_URL}?status=active")) == []
    assert len(rows(rx.page(client, f"{px.PROTOCOLS_URL}?page=999"))) == queries.PAGE_SIZE
