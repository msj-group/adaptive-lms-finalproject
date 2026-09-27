"""Phase 6 / M01 Researcher portal, and the privacy boundary it rests on.

The route and method inventory, the role gate, the headers, and -- the point
of the whole module -- that no identifying value is *loaded*, not merely not
printed. The projections are asserted directly against the SQL the queries
emit, because a template test would still pass if the query had fetched a
name and simply not rendered it.
"""

import re

import pytest

import tests.research_fixtures as rx
import tests.structural_checks as sc
from app.extensions import db
from app.models import (
    PARTICIPANT_CODE_ALPHABET,
    ResearchParticipant,
    ResearchParticipantStatus,
    User,
    UserRole,
)
from app.services import research_queries as queries

_ROUTES = {
    "research.dashboard": "/research/dashboard",
    "research.participants": "/research/participants",
    "research.participant_detail": "/research/participants/<participant_public_id>",
}

_GET = frozenset({"GET"})
_POST = frozenset({"POST"})
_GET_POST = frozenset({"GET", "POST"})
_PROTOCOL = "/research/protocols/<protocol_public_id>"
_SET = _PROTOCOL + "/sets/<set_public_id>"
_TASK = _SET + "/tasks/<task_public_id>"

#: Phase 6 / M02A: the experiment protocol catalogue, the Researcher's only
#: writes. Declared here so the portal's complete inventory stays in one
#: place; tests/test_researcher_protocols.py exercises each rule.
_PROTOCOL_ROUTES = {
    "research.protocols": ("/research/protocols", _GET),
    "research.protocol_new": ("/research/protocols/new", _GET_POST),
    "research.protocol_detail": (_PROTOCOL, _GET),
    "research.protocol_edit": (_PROTOCOL + "/edit", _GET_POST),
    "research.protocol_set_new": (_PROTOCOL + "/sets/new", _GET_POST),
    "research.protocol_set_edit": (_SET + "/edit", _GET_POST),
    "research.protocol_set_move": (_SET + "/move", _POST),
    "research.protocol_task_new": (_SET + "/tasks/new", _GET_POST),
    "research.protocol_task_edit": (_TASK + "/edit", _GET_POST),
    "research.protocol_task_move": (_TASK + "/move", _POST),
    "research.protocol_activate": (_PROTOCOL + "/activate", _GET_POST),
    "research.protocol_discard": (_PROTOCOL + "/discard", _POST),
    "research.protocol_new_version": (_PROTOCOL + "/new-version", _GET_POST),
}

_INVITED = ResearchParticipantStatus.INVITED.value
_ACTIVE = ResearchParticipantStatus.ACTIVE.value
_DECLINED = ResearchParticipantStatus.DECLINED.value
_WITHDRAWN = ResearchParticipantStatus.WITHDRAWN.value


@pytest.fixture
def people(app):
    return rx.world(app)


def _login_researcher(client):
    rx.login(client, rx.RESEARCHER_EMAIL)


def _seed(people, status=_ACTIVE):
    """One document and one participant of the wanted status, written
    directly -- this module tests reading, not the write path."""
    document = rx.document_row(people["admin"], status="active")
    participant = rx.participant_row(
        people["student"], people["admin"], status=status,
        document=None if status == _INVITED else document,
    )
    return document, participant


# ===========================================================================
# Inventory, authorization and headers
# ===========================================================================


def test_the_route_and_method_inventory_is_exact(app):
    """M01's three participation rules stay GET-only; M02A adds exactly the
    protocol catalogue rules and nothing else -- in particular no removal
    rule for a task set or a task, and no participant-facing rule."""
    rules = {
        rule.endpoint: (str(rule), frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("research.")
    }
    expected = {e: (u, _GET) for e, u in _ROUTES.items()}
    expected.update(_PROTOCOL_ROUTES)
    assert rules == expected
    for endpoint, (url, _methods) in rules.items():
        for word in ("remove", "delete", "participant_session", "assign", "export"):
            assert word not in url and word not in endpoint, (endpoint, word)


def test_an_anonymous_visitor_is_redirected_to_login(people, client):
    for url in (rx.RESEARCH_DASHBOARD_URL, rx.RESEARCH_PARTICIPANTS_URL):
        response = client.get(url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"], url


@pytest.mark.parametrize("email", [rx.STUDENT_EMAIL, rx.TEACHER_EMAIL, rx.ADMIN_EMAIL])
def test_every_other_role_is_refused(people, client, email):
    rx.login(client, email)
    for url in (rx.RESEARCH_DASHBOARD_URL, rx.RESEARCH_PARTICIPANTS_URL):
        assert client.get(url).status_code == 403, (email, url)


def test_a_suspended_researcher_cannot_hold_a_session(app, people, client):
    _login_researcher(client)
    assert client.get(rx.RESEARCH_DASHBOARD_URL).status_code == 200
    researcher = User.query.filter_by(email=rx.RESEARCHER_EMAIL).one()
    researcher.status = "suspended"
    researcher.bump_auth_version()
    db.session.commit()
    response = client.get(rx.RESEARCH_DASHBOARD_URL)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_every_response_is_private_no_store_and_varies_on_cookie(people, client):
    _login_researcher(client)
    _, participant = _seed(people)
    for url in (rx.RESEARCH_DASHBOARD_URL, rx.RESEARCH_PARTICIPANTS_URL,
                f"{rx.RESEARCH_PARTICIPANTS_URL}/{participant.public_id}"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in response.headers["Vary"], url


def test_the_login_redirect_and_the_403_also_carry_the_headers(people, client):
    anonymous = client.get(rx.RESEARCH_DASHBOARD_URL)
    assert anonymous.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in anonymous.headers["Vary"]
    rx.login(client, rx.STUDENT_EMAIL)
    refused = client.get(rx.RESEARCH_DASHBOARD_URL)
    assert refused.status_code == 403
    assert refused.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in refused.headers["Vary"]


def test_the_portal_is_read_only(people, client):
    """The M01 participation pages stay read-only. M02A's protocol catalogue
    is the Researcher's only write surface and holds no participant data;
    it adds no form to these pages -- the only one is still the logout."""
    _login_researcher(client)
    _, participant = _seed(people)
    for url in (rx.RESEARCH_DASHBOARD_URL, rx.RESEARCH_PARTICIPANTS_URL,
                f"{rx.RESEARCH_PARTICIPANTS_URL}/{participant.public_id}"):
        for method in (client.post, client.put, client.patch, client.delete):
            assert method(url).status_code == 405, url
    # The only form on any Researcher page is the shared header's logout.
    for url in (rx.RESEARCH_DASHBOARD_URL, rx.RESEARCH_PARTICIPANTS_URL):
        actions = re.findall(r'<form method="post" action="([^"]+)"',
                             rx.page(client, url))
        assert actions == ["/auth/logout"], url


@pytest.mark.parametrize("identifier", ["nope", "x" * 80, "1", "00000000-0000-0000-0000-000000000000"])
def test_a_malformed_unknown_or_mismatched_identifier_is_a_plain_404(
    people, client, identifier
):
    _login_researcher(client)
    _seed(people)
    response = client.get(f"{rx.RESEARCH_PARTICIPANTS_URL}/{identifier}")
    assert response.status_code == 404
    # The 404 body discloses nothing about whether the id could exist.
    assert rx.STUDENT_EMAIL not in response.get_data(as_text=True)


def test_a_researcher_cannot_reach_the_administrator_research_area(people, client):
    _login_researcher(client)
    for url in (rx.OVERVIEW_URL, rx.ADMIN_PARTICIPANTS_URL, rx.INVITE_URL,
                rx.DOCUMENT_NEW_URL, "/admin/dashboard", "/admin/students"):
        assert client.get(url).status_code == 403, url


def test_a_researcher_cannot_reach_a_students_consent_page(people, client):
    _login_researcher(client)
    _seed(people, status=_INVITED)
    assert client.get(rx.CONSENT_URL).status_code == 403
    assert client.post(rx.ACCEPT_URL, data={}).status_code == 403
    assert client.post(rx.DECLINE_URL, data={}).status_code == 403
    assert client.post(rx.WITHDRAW_URL, data={}).status_code == 403


# ===========================================================================
# The privacy boundary, proved in SQL rather than in a template
# ===========================================================================

#: Every ``users`` column that could identify a person, plus the participant
#: columns that point at one.
_IDENTIFYING = (
    "users.full_name", "users.email", "users.public_id", "users.id",
    "users.password_hash", "users.role", "users.status",
    "research_participants.student_id", "research_participants.invited_by_id",
    "research_participants.id",
)


def _sql(statement):
    return str(statement).replace("\n", " ")


def test_the_researcher_projection_selects_no_identity_column(app):
    """Asserted against the compiled statements, so this fails if a future
    change adds a join to ``users`` even when no template prints it."""
    for statement in (
        queries._researcher_query(*queries.RESEARCHER_LIST_COLUMNS),
        queries._researcher_query(*queries.RESEARCHER_DETAIL_COLUMNS),
    ):
        sql = _sql(statement)
        assert "users" not in sql, sql
        for column in _IDENTIFYING:
            assert column not in sql, column
        # And it never selects the consent wording itself.
        assert "research_consent_documents.body" not in sql


def test_the_researcher_projection_selects_exactly_the_declared_columns():
    assert [c.key for c in queries.RESEARCHER_LIST_COLUMNS] == [
        "public_id", "participant_code", "status", "created_at", "decided_at",
        "withdrawn_at", "version_identifier",
    ]
    assert [c.key for c in queries.RESEARCHER_DETAIL_COLUMNS] == [
        "public_id", "participant_code", "status", "created_at", "decided_at",
        "withdrawn_at", "version_identifier", "title",
    ]


def test_a_researcher_row_carries_no_identifying_attribute(app, people):
    _seed(people)
    rows, total, page = queries.researcher_participants_page("all", "", 1)
    assert (total, page) == (1, 1)
    row = rows[0]
    assert set(row._mapping) == {
        "public_id", "participant_code", "status", "created_at", "decided_at",
        "withdrawn_at", "version_identifier",
    }
    for attribute in ("full_name", "email", "student_id", "id", "invited_by_id", "body"):
        assert not hasattr(row, attribute), attribute


def test_the_researcher_pages_never_render_a_name_email_or_identifier(people, client):
    _login_researcher(client)
    document, participant = _seed(people)
    student = people["student"]
    for url in (rx.RESEARCH_PARTICIPANTS_URL,
                f"{rx.RESEARCH_PARTICIPANTS_URL}/{participant.public_id}"):
        html = rx.page(client, url)
        assert participant.participant_code in html
        # Names, addresses and account identifiers, none of which the query
        # behind this page even selected. Bare integers are not checked as
        # substrings -- "2" occurs in every date -- because
        # ``test_the_researcher_urls_use_public_ids_and_never_numeric_ids``
        # proves the internal ids are not addresses, which is the real rule.
        for secret in (student.full_name, student.email, student.public_id,
                       document.body, people["admin"].full_name,
                       people["admin"].email, people["admin"].public_id):
            assert secret not in html, (url, secret)
        # No financial, messaging or academic content either -- searched with
        # the signed CSRF value blanked, as its random base64 can spell "LYD".
        said = sc.redact_signed_values(html)
        for forbidden in ("LYD", "Invoice", "Payment", "Balance", "Enrollment",
                          "Group", "Message", "password"):
            assert forbidden not in said, (url, forbidden)


def test_the_researcher_urls_use_public_ids_and_never_numeric_ids(people, client):
    _login_researcher(client)
    _, participant = _seed(people)
    html = rx.page(client, rx.RESEARCH_PARTICIPANTS_URL)
    links = re.findall(r'href="(/research/participants/[^"]+)"', html)
    assert links == [f"/research/participants/{participant.public_id}"]
    # Compared against the extracted links, never as a raw-HTML substring: a
    # v4 public id starts with "1" about 6% of the time, and the
    # participant's own href would then *contain* "/research/participants/1"
    # and fail a substring check for reasons that have nothing to do with
    # numeric ids being exposed.
    assert f"/research/participants/{participant.id}" not in links
    # The numeric id is not a usable address either.
    assert client.get(f"/research/participants/{participant.id}").status_code == 404


def test_the_search_matches_codes_and_never_a_name_or_email(people, client):
    _login_researcher(client)
    _, participant = _seed(people)
    student = people["student"]
    found = rx.page(client, f"{rx.RESEARCH_PARTICIPANTS_URL}?q={participant.participant_code}")
    assert participant.participant_code in found
    for term in (student.full_name, student.email, student.full_name.split()[0]):
        html = rx.page(client, f"{rx.RESEARCH_PARTICIPANTS_URL}?q={term}")
        assert participant.participant_code not in html, term
        assert "No participants" in html, term


def test_the_search_is_bounded_and_escapes_like_wildcards(people, client):
    _login_researcher(client)
    _, participant = _seed(people)
    # A wildcard is matched literally, so it cannot enumerate the table.
    assert participant.participant_code not in rx.page(
        client, f"{rx.RESEARCH_PARTICIPANTS_URL}?q=%")
    assert participant.participant_code not in rx.page(
        client, f"{rx.RESEARCH_PARTICIPANTS_URL}?q=_")
    assert queries.normalize_code_search("x" * 500) == "X" * queries.MAX_SEARCH_LENGTH


# ===========================================================================
# What the pages actually say
# ===========================================================================


def test_the_dashboard_shows_only_real_counts(people, client):
    _login_researcher(client)
    document = rx.document_row(people["admin"], status="active")
    rx.participant_row(people["student"], people["admin"], code="RP-AAAAAAAAAA",
                       status=_ACTIVE, document=document)
    second = rx.make_student("second@example.com", name="Second Student")
    rx.participant_row(second, people["admin"], code="RP-BBBBBBBBBB", status=_INVITED)

    html = rx.page(client, rx.RESEARCH_DASHBOARD_URL)
    counts = dict(re.findall(
        r'data-count="(\w+)">\s*<div class="stat-card__value">(\d+)</div>', html))
    assert counts == {"invited": "1", "active": "1", "declined": "0", "withdrawn": "0"}
    assert queries.participant_counts() == {
        "invited": 1, "active": 1, "declined": 0, "withdrawn": 0
    }


def test_the_dashboard_claims_no_experiment_result_or_emotion_detection(people, client):
    """The page may *deny* collecting things -- and does -- but must never
    claim to detect, measure or predict anything about anybody."""
    _login_researcher(client)
    html = rx.page(client, rx.RESEARCH_DASHBOARD_URL).lower()
    for claim in ("is frustrated", "are frustrated", "frustration level",
                  "detects", "detected", "emotion", "sentiment", "predicts",
                  "prediction", "accuracy", "confidence", "engagement score",
                  "proves that"):
        assert claim not in html, claim
    assert "collected, measured or inferred" in html


def test_the_dashboard_describes_what_m01_records_and_what_it_does_not(people, client):
    """M01R. The old wording claimed the platform collected no research data
    at all, which was untrue: it records research administration. The page
    must now draw the real distinction."""
    _login_researcher(client)
    html = " ".join(rx.page(client, rx.RESEARCH_DASHBOARD_URL).lower().split())
    # What it does record.
    assert "what is recorded" in html
    assert "consent status" in html
    assert "append-only history" in html
    # What it does not collect, named precisely.
    assert "no behavioural interaction data" in html
    for absent in ("no interaction events", "no experiment sessions",
                   "no surveys", "no frustration ratings", "no exports",
                   "no model training", "no inference"):
        assert absent in html, absent
    # And never the broad, false claim.
    for false_claim in ("no research data", "collects no data",
                        "nothing is recorded"):
        assert false_claim not in html, false_claim


def test_the_dashboard_does_not_present_activation_as_an_ethics_approval(people, client):
    """M01R. Activating a document is an administrative action. The page may
    say approvals are obtained outside the platform; it must not imply the
    application state records or constitutes one."""
    _login_researcher(client)
    html = " ".join(rx.page(client, rx.RESEARCH_DASHBOARD_URL).lower().split())
    assert "an administrative action, not an ethics approval" in html
    for implication in ("ethics-approved", "approved by the ethics",
                        "has been approved", "ethics approval has"):
        assert implication not in html, implication


def test_the_detail_page_shows_the_state_the_version_and_the_timestamps(people, client):
    _login_researcher(client)
    document, participant = _seed(people, status=_WITHDRAWN)
    html = rx.page(client, f"{rx.RESEARCH_PARTICIPANTS_URL}/{participant.public_id}")
    assert participant.participant_code in html
    assert "Withdrawn" in html
    assert document.version_identifier in html
    assert "not eligible for any future research session" in html


def test_an_invited_participant_shows_no_consent_version(people, client):
    _login_researcher(client)
    _, participant = _seed(people, status=_INVITED)
    html = rx.page(client, f"{rx.RESEARCH_PARTICIPANTS_URL}/{participant.public_id}")
    assert "None accepted" in html
    assert "has not decided yet" in html


#: Participant rights and retention terms belong to the accepted consent
#: document, so no page may assert one -- the Researcher detail page included.
_UNVERSIONED_RIGHTS = (
    "withdraw at any time",
    "without giving a reason",
    "permanent record",
    "never edited or deleted",
)


@pytest.mark.parametrize("status", [_INVITED, _ACTIVE, _DECLINED, _WITHDRAWN])
def test_the_detail_page_asserts_no_participant_right(people, client, status):
    """M01R2. The detail page used to tell a Researcher that an active
    participant "may withdraw at any time". Whether, when and on what terms
    they may is a term of the consent version they accepted, not something
    unversioned page text should assert -- here no less than on the Student
    page. Every status branch is checked, since each renders its own line."""
    _login_researcher(client)
    _, participant = _seed(people, status=status)
    html = " ".join(
        rx.page(client, f"{rx.RESEARCH_PARTICIPANTS_URL}/{participant.public_id}")
        .lower().split()
    )
    for phrase in _UNVERSIONED_RIGHTS:
        assert phrase not in html, (status, phrase)
    # The active branch still says what it is for: which version was accepted.
    if status == _ACTIVE:
        assert "accepted the consent version shown above" in html


def test_the_list_is_paginated_and_bounded(people, client):
    _login_researcher(client)
    document = rx.document_row(people["admin"], status="active")
    codes = []
    for index in range(queries.PAGE_SIZE + 3):
        student = rx.make_student(f"bulk{index}@example.com", name=f"Bulk {index}")
        # One alphabet character per participant over a fixed filler, so
        # every code is distinct and valid without any arithmetic.
        code = f"RP-VVVVVVVVV{PARTICIPANT_CODE_ALPHABET[index]}"
        codes.append(code)
        rx.participant_row(student, people["admin"], code=code, status=_ACTIVE,
                           document=document)

    first = rx.page(client, rx.RESEARCH_PARTICIPANTS_URL)
    assert first.count('data-participant="') == queries.PAGE_SIZE
    assert f"of {len(codes)}" in first
    second = rx.page(client, f"{rx.RESEARCH_PARTICIPANTS_URL}?page=2")
    assert second.count('data-participant="') == 3


def test_the_role_home_sends_a_researcher_to_the_researcher_dashboard(people, client):
    response = client.post(
        "/auth/login",
        data={"email": rx.RESEARCHER_EMAIL, "password": rx.PW},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"] == rx.RESEARCH_DASHBOARD_URL


def test_the_researcher_portal_shows_no_student_or_teacher_navigation(people, client):
    _login_researcher(client)
    html = rx.page(client, rx.RESEARCH_DASHBOARD_URL)
    for forbidden in ("Messages", "Discussions", "Notifications", "Assignments",
                      "Quizzes", "Grades", "Attendance", "Research consent"):
        assert f">{forbidden}" not in html, forbidden
    assert UserRole.RESEARCHER.value.capitalize() in html


def test_a_researcher_never_triggers_the_student_consent_link_query(people, client):
    """The portal consent helper is role-gated, so a Researcher page costs no
    participant query at all."""
    researcher = User.query.filter_by(email=rx.RESEARCHER_EMAIL).one()
    assert queries.portal_consent_status(researcher) is None
    assert queries.portal_consent_status(people["admin"]) is None
    assert queries.portal_consent_status(people["teacher"]) is None
    assert ResearchParticipant.query.count() == 0
