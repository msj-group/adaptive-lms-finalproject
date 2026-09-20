"""Phase 6 / M01 Administrator research area.

The route and method inventory, the role gate, the headers, consent-document
authoring and activation, participant invitations, and the Phase 5
navigation that must not have moved.
"""

import re

import pytest

import tests.research_fixtures as rx
from app.extensions import db
from app.models import (
    Enrollment,
    Invoice,
    PaymentTransaction,
    ResearchConsentDocument,
    ResearchConsentDocumentStatus,
    ResearchConsentEvent,
    ResearchParticipant,
    ResearchParticipantStatus,
    User,
    UserStatus,
)
from app.services import research_tokens as tokens

_GET_ONLY = {
    "admin.research_overview": "/admin/research",
    "admin.research_consent_document": "/admin/research/consent-documents/<document_public_id>",
    "admin.research_participants": "/admin/research/participants",
    "admin.research_participant": "/admin/research/participants/<participant_public_id>",
}
_GET_POST = {
    "admin.research_consent_document_new": "/admin/research/consent-documents/new",
    "admin.research_participant_new": "/admin/research/participants/new",
}
_POST_ONLY = {
    "admin.research_consent_document_activate":
        "/admin/research/consent-documents/<document_public_id>/activate",
}

_DRAFT = ResearchConsentDocumentStatus.DRAFT.value
_DOC_ACTIVE = ResearchConsentDocumentStatus.ACTIVE.value
_SUPERSEDED = ResearchConsentDocumentStatus.SUPERSEDED.value
_INVITED = ResearchParticipantStatus.INVITED.value


@pytest.fixture
def people(app):
    return rx.world(app)


def _login_admin(client):
    rx.login(client, rx.ADMIN_EMAIL)


# ===========================================================================
# Inventory, authorization and headers
# ===========================================================================


def test_the_route_and_method_inventory_is_exact(app):
    wanted = {**_GET_ONLY, **_GET_POST, **_POST_ONLY}
    rules = {
        rule.endpoint: (str(rule), frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("admin.research")
    }
    assert rules == {
        **{e: (u, frozenset({"GET"})) for e, u in _GET_ONLY.items()},
        **{e: (u, frozenset({"GET", "POST"})) for e, u in _GET_POST.items()},
        **{e: (u, frozenset({"POST"})) for e, u in _POST_ONLY.items()},
    }


def test_an_anonymous_visitor_is_redirected_to_login(people, client):
    for url in (rx.OVERVIEW_URL, rx.DOCUMENT_NEW_URL, rx.ADMIN_PARTICIPANTS_URL, rx.INVITE_URL):
        response = client.get(url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"], url


@pytest.mark.parametrize("email", [rx.STUDENT_EMAIL, rx.TEACHER_EMAIL, rx.RESEARCHER_EMAIL])
def test_every_other_role_is_refused(people, client, email):
    rx.login(client, email)
    for url in (rx.OVERVIEW_URL, rx.DOCUMENT_NEW_URL, rx.ADMIN_PARTICIPANTS_URL, rx.INVITE_URL):
        assert client.get(url).status_code == 403, (email, url)
    assert client.post(rx.DOCUMENT_NEW_URL, data={}).status_code == 403
    assert client.post(rx.INVITE_URL, data={}).status_code == 403


def test_a_researcher_cannot_author_or_activate_consent_wording(people, client):
    """Ethics text is not a Researcher's field to edit -- the routes are
    absent for them, not merely hidden."""
    _login_admin(client)
    rx.create_document(client)
    document = ResearchConsentDocument.query.one()
    rx.logout(client)

    rx.login(client, rx.RESEARCHER_EMAIL)
    assert client.get(rx.document_url(document)).status_code == 403
    assert client.post(f"{rx.document_url(document)}/activate",
                       data={"confirm": "yes"}).status_code == 403
    assert ResearchConsentDocument.query.one().status == _DRAFT


def test_every_response_is_private_no_store_and_varies_on_cookie(people, client):
    _login_admin(client)
    for url in (rx.OVERVIEW_URL, rx.DOCUMENT_NEW_URL, rx.ADMIN_PARTICIPANTS_URL, rx.INVITE_URL):
        response = client.get(url)
        assert response.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in response.headers["Vary"], url
    # ... including the 404 an unknown identifier produces.
    missing = client.get("/admin/research/participants/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in missing.headers["Vary"]


def test_unsupported_methods_are_refused(people, client):
    _login_admin(client)
    assert client.post(rx.OVERVIEW_URL, data={}).status_code == 405
    assert client.post(rx.ADMIN_PARTICIPANTS_URL, data={}).status_code == 405
    assert client.get("/admin/research/consent-documents/x/activate").status_code == 405


@pytest.mark.parametrize("identifier", ["nope", "x" * 80, "../../etc", "1"])
def test_a_malformed_or_unknown_identifier_is_a_plain_404(people, client, identifier):
    _login_admin(client)
    assert client.get(f"/admin/research/consent-documents/{identifier}").status_code == 404
    assert client.get(f"/admin/research/participants/{identifier}").status_code == 404


def test_every_mutation_requires_a_csrf_token(app, people):
    """The suite disables CSRF globally, so this one test turns it back on
    and proves each POST is protected."""
    from app import create_app
    from app.config import config_by_name

    original = config_by_name["testing"].WTF_CSRF_ENABLED
    config_by_name["testing"].WTF_CSRF_ENABLED = True
    try:
        csrf_app = create_app("testing")
    finally:
        config_by_name["testing"].WTF_CSRF_ENABLED = original

    with csrf_app.app_context():
        db.create_all()
        try:
            rx.make_admin()
            student = rx.make_student()
            client = csrf_app.test_client()
            rx.login(client, rx.ADMIN_EMAIL)
            for url, data in (
                (rx.DOCUMENT_NEW_URL,
                 {"version_identifier": "v1.0", "title": "T", "body": "B"}),
                (rx.INVITE_URL,
                 {"student_public_id": student.public_id, "state_token": "x"}),
                ("/admin/research/consent-documents/any/activate", {"confirm": "yes"}),
            ):
                assert client.post(url, data=data).status_code == 400, url
            assert ResearchConsentDocument.query.count() == 0
            assert ResearchParticipant.query.count() == 0
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


# ===========================================================================
# Consent documents
# ===========================================================================


def test_the_overview_starts_empty_and_says_an_administrator_must_act(people, client):
    _login_admin(client)
    html = rx.page(client, rx.OVERVIEW_URL)
    assert "No consent document is active" in html
    assert "No consent documents" in html
    assert "ethics approval" in html
    # Nothing that claims the platform is ready to recruit.
    assert "ready for" not in html.lower()


def test_a_new_document_is_a_draft_with_a_server_computed_digest(people, client):
    _login_admin(client)
    response = rx.create_document(client)
    assert rx.DRAFT_SAVED_TEXT in response.get_data(as_text=True)
    document = ResearchConsentDocument.query.one()
    assert document.status == _DRAFT
    assert document.current_marker is None
    assert document.activated_at is None
    assert document.digest_matches()
    assert document.body == rx.BODY_V1


@pytest.mark.parametrize("field,value,message", [
    ("version_identifier", "", "version identifier"),
    ("title", "", "a title"),
    ("body", "   ", "consent wording"),
    ("version_identifier", "v\x00 1", "ordinary text"),
    ("body", "text‮reversed", "ordinary text"),
])
def test_invalid_consent_text_is_refused_and_nothing_is_written(
    people, client, field, value, message
):
    _login_admin(client)
    data = {"version_identifier": "v1.0", "title": rx.TITLE, "body": rx.BODY_V1}
    data[field] = value
    response = client.post(rx.DOCUMENT_NEW_URL, data=data, follow_redirects=True)
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    assert ResearchConsentDocument.query.count() == 0


def test_a_duplicate_version_identifier_is_refused(people, client):
    _login_admin(client)
    rx.create_document(client, version="v1.0")
    response = rx.create_document(client, version="v1.0", body="Other wording.")
    assert "already uses that version identifier" in response.get_data(as_text=True)
    assert ResearchConsentDocument.query.count() == 1


def test_activation_needs_the_confirmation_box(people, client):
    _login_admin(client)
    rx.create_document(client)
    document = ResearchConsentDocument.query.one()
    response = rx.activate_document(client, document, confirm="no")
    assert "Tick the confirmation box" in response.get_data(as_text=True)
    db.session.expire_all()
    assert ResearchConsentDocument.query.one().status == _DRAFT


def test_activation_freezes_the_document_and_makes_it_current(people, client):
    _login_admin(client)
    document = rx.publish_document(client)
    assert document.status == _DOC_ACTIVE
    assert document.current_marker == 1
    assert document.activated_at is not None
    assert document.activated_by_id == User.query.filter_by(email=rx.ADMIN_EMAIL).one().id
    html = rx.page(client, rx.document_url(document))
    assert "wording is frozen" in html
    assert "state_token" not in html  # no activation control on an active document


def test_a_stale_activation_token_is_refused_without_writing(people, client):
    _login_admin(client)
    rx.create_document(client, version="v1.0")
    first = ResearchConsentDocument.query.filter_by(version_identifier="v1.0").one()
    stale = rx._token(rx.page(client, rx.document_url(first)))
    # Something else becomes current between rendering and submitting.
    rx.publish_document(client, version="v2.0", body=rx.BODY_V2)
    response = rx.activate_document(client, first, token=stale)
    assert rx.STALE_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert ResearchConsentDocument.query.filter_by(version_identifier="v1.0").one().status == _DRAFT
    assert ResearchConsentDocument.query.filter_by(status=_DOC_ACTIVE).count() == 1


def test_a_missing_or_forged_activation_token_is_refused(people, client):
    _login_admin(client)
    rx.create_document(client)
    document = ResearchConsentDocument.query.one()
    for token in ("", "not-a-token", "x" * 5000):
        response = rx.activate_document(client, document, token=token)
        assert rx.STALE_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert ResearchConsentDocument.query.one().status == _DRAFT


def test_activating_a_second_version_supersedes_the_first_and_never_rewrites_it(people, client):
    _login_admin(client)
    first = rx.publish_document(client, version="v1.0", body=rx.BODY_V1)
    first_digest, first_body = first.body_digest, first.body
    second = rx.publish_document(client, version="v2.0", body=rx.BODY_V2)

    db.session.expire_all()
    first = ResearchConsentDocument.query.filter_by(version_identifier="v1.0").one()
    second = ResearchConsentDocument.query.filter_by(version_identifier="v2.0").one()
    assert first.status == _SUPERSEDED and first.current_marker is None
    assert first.superseded_at is not None and first.superseded_by_id is not None
    assert second.status == _DOC_ACTIVE and second.current_marker == 1
    # The earlier version still says exactly what it said.
    assert (first.body, first.body_digest) == (first_body, first_digest)
    assert ResearchConsentDocument.query.filter_by(status=_DOC_ACTIVE).count() == 1


def test_an_already_active_document_cannot_be_activated_again(people, client):
    _login_admin(client)
    document = rx.publish_document(client)
    response = client.post(
        f"{rx.document_url(document)}/activate",
        data={"state_token": "anything", "confirm": "yes"},
        follow_redirects=True,
    )
    # The token is checked first and is not a current one, so this is stale
    # rather than "already" -- either way nothing is written.
    assert rx.STALE_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert ResearchConsentDocument.query.one().status == _DOC_ACTIVE


def test_a_superseded_document_offers_no_activation_control(people, client):
    _login_admin(client)
    first = rx.publish_document(client, version="v1.0")
    rx.publish_document(client, version="v2.0", body=rx.BODY_V2)
    html = rx.page(client, rx.document_url(first))
    assert "superseded" in html.lower()
    assert "state_token" not in html


# ===========================================================================
# Participant invitations
# ===========================================================================


def test_only_active_students_without_a_participant_are_offered(people, client):
    _login_admin(client)
    suspended = rx.make_student("suspended@example.com", name="Suspended Student",
                                status=UserStatus.SUSPENDED.value)
    html = rx.page(client, rx.INVITE_URL)
    assert people["student"].public_id in html
    for other in (people["teacher"], people["researcher"], people["admin"], suspended):
        assert other.public_id not in html, other.email


def test_inviting_an_active_student_creates_one_invited_participant(people, client):
    _login_admin(client)
    response = rx.invite(client, people["student"])
    assert response.status_code == 200
    participant = ResearchParticipant.query.one()
    assert participant.status == _INVITED
    assert participant.student_id == people["student"].id
    assert participant.consent_document_id is None
    assert participant.decided_at is None
    assert participant.version == 1
    # An invitation is not consent: no history row exists.
    assert ResearchConsentEvent.query.count() == 0
    assert participant.participant_code in response.get_data(as_text=True)
    assert "has not consented yet" in response.get_data(as_text=True)


def test_the_participant_code_is_generated_on_the_server(people, client):
    """A code submitted by the browser is ignored: the form has no field for
    one, and a planted value never reaches the row."""
    _login_admin(client)
    token = rx.invitation_token(client, people["student"])
    client.post(
        rx.INVITE_URL,
        data={
            "state_token": token,
            "student_public_id": people["student"].public_id,
            "participant_code": "RP-HACKEDCODE",
            "status": "active",
            "version": "99",
        },
        follow_redirects=True,
    )
    participant = ResearchParticipant.query.one()
    assert participant.participant_code != "RP-HACKEDCODE"
    assert participant.status == _INVITED
    assert participant.version == 1


@pytest.mark.parametrize("who,message", [
    ("teacher", rx.NOT_STUDENT_TEXT),
    ("researcher", rx.NOT_STUDENT_TEXT),
    ("admin", rx.NOT_STUDENT_TEXT),
])
def test_a_non_student_account_is_refused_even_with_a_valid_looking_post(
    people, client, who, message
):
    """The form never offers these accounts; this posts one anyway, which is
    what an attacker would do, and the locked row refuses it."""
    _login_admin(client)
    target = people[who]
    token = tokens_for(client, target)
    response = client.post(
        rx.INVITE_URL,
        data={"state_token": token, "student_public_id": target.public_id},
        follow_redirects=True,
    )
    assert message in response.get_data(as_text=True)
    assert ResearchParticipant.query.count() == 0


def tokens_for(client, user):
    """A token that *looks* right for `user`, minted the way the form would
    if it had offered them. The transaction must refuse them anyway."""
    with client.application.test_request_context():
        from flask_login import current_user  # noqa: F401

        admin = User.query.filter_by(email=rx.ADMIN_EMAIL).one()
        return tokens.make_token(
            tokens.PURPOSE_PARTICIPANT_CREATE,
            actor_public_id=admin.public_id,
            student_public_id=user.public_id,
            student_state=tokens.student_state("student", "active", False),
        )


def test_a_suspended_student_is_refused(people, client):
    _login_admin(client)
    suspended = rx.make_student("suspended2@example.com", name="Suspended Two",
                                status=UserStatus.SUSPENDED.value)
    response = client.post(
        rx.INVITE_URL,
        data={"state_token": tokens_for(client, suspended),
              "student_public_id": suspended.public_id},
        follow_redirects=True,
    )
    assert rx.SUSPENDED_TEXT in response.get_data(as_text=True)
    assert ResearchParticipant.query.count() == 0


def test_a_student_cannot_be_invited_twice(people, client):
    _login_admin(client)
    token = rx.invitation_token(client, people["student"])
    rx.invite(client, people["student"], token=token)
    # The same rendered form, submitted again.
    response = rx.invite(client, people["student"], token=token)
    assert rx.ALREADY_LINKED_TEXT in response.get_data(
        as_text=True
    ) or rx.STALE_TEXT in response.get_data(as_text=True)
    assert ResearchParticipant.query.count() == 1


def test_an_unknown_student_identifier_writes_nothing(people, client):
    _login_admin(client)
    response = client.post(
        rx.INVITE_URL,
        data={"state_token": "x", "student_public_id": "00000000-0000-0000-0000-000000000000"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert ResearchParticipant.query.count() == 0


def test_inviting_changes_no_account_enrollment_or_financial_row(people, client):
    _login_admin(client)
    before = {
        "users": [(u.id, u.role, u.status, u.email, u.full_name, u.auth_version)
                  for u in User.query.order_by(User.id)],
        "enrollments": Enrollment.query.count(),
        "invoices": Invoice.query.count(),
        "payments": PaymentTransaction.query.count(),
    }
    rx.invite(client, people["student"])
    db.session.expire_all()
    after = {
        "users": [(u.id, u.role, u.status, u.email, u.full_name, u.auth_version)
                  for u in User.query.order_by(User.id)],
        "enrollments": Enrollment.query.count(),
        "invoices": Invoice.query.count(),
        "payments": PaymentTransaction.query.count(),
    }
    assert after == before
    assert ResearchParticipant.query.count() == 1


def test_the_administrator_sees_the_protected_mapping(people, client):
    _login_admin(client)
    rx.invite(client, people["student"])
    participant = ResearchParticipant.query.one()
    html = rx.page(client, f"{rx.ADMIN_PARTICIPANTS_URL}/{participant.public_id}")
    assert participant.participant_code in html
    assert people["student"].full_name in html
    assert people["student"].email in html
    assert "No decision yet" in html
    # No internal id, and no financial figure.
    assert f">{participant.id}<" not in html
    assert "LYD" not in html


def test_the_participant_list_searches_codes_and_not_names(people, client):
    _login_admin(client)
    rx.invite(client, people["student"])
    participant = ResearchParticipant.query.one()
    found = rx.page(client, f"{rx.ADMIN_PARTICIPANTS_URL}?q={participant.participant_code}")
    assert participant.participant_code in found
    by_name = rx.page(client, f"{rx.ADMIN_PARTICIPANTS_URL}?q={people['student'].full_name}")
    assert participant.participant_code not in by_name
    assert "No participants" in by_name


def test_the_status_filter_accepts_only_known_values(people, client):
    _login_admin(client)
    rx.invite(client, people["student"])
    participant = ResearchParticipant.query.one()
    assert participant.participant_code in rx.page(
        client, f"{rx.ADMIN_PARTICIPANTS_URL}?status=invited")
    assert participant.participant_code not in rx.page(
        client, f"{rx.ADMIN_PARTICIPANTS_URL}?status=active")
    # An unknown filter falls back to "all" rather than erroring or guessing.
    assert participant.participant_code in rx.page(
        client, f"{rx.ADMIN_PARTICIPANTS_URL}?status=banana")
    assert participant.participant_code in rx.page(
        client, f"{rx.ADMIN_PARTICIPANTS_URL}?page=-4")


# ===========================================================================
# Navigation: the Phase 5 order is untouched, Research is its own entry
# ===========================================================================


def test_the_sidebar_keeps_the_phase_5_order_and_adds_research(people, client):
    _login_admin(client)
    dashboard = rx.page(client, "/admin/dashboard")
    finance = dashboard[dashboard.index("Finance &amp; Research"):dashboard.index("</nav>")]
    labels = re.findall(r'<a class="admin-nav__link[^"]*" href="[^"]+">([^<]+)</a>', finance)
    assert labels == ["Student Accounts", "Invoices", "Payments", "Fee Plans",
                      "Financial reports", "Deleted Records", "Research"]
    assert 'href="/admin/research"' in finance
    # The disabled "Soon" placeholder is gone for Research specifically.
    assert not re.search(r'Research <span class="badge badge--neutral">Soon</span>', finance)


def test_the_research_area_shows_no_financial_figure(people, client):
    _login_admin(client)
    rx.invite(client, people["student"])
    for url in (rx.OVERVIEW_URL, rx.ADMIN_PARTICIPANTS_URL, rx.INVITE_URL):
        html = rx.page(client, url)
        # The sidebar names the financial workspaces on every Administrator
        # page by design; what must carry no financial content is the page.
        body = html[html.index('<div class="admin-content">'):]
        for forbidden in ("LYD", "Invoice", "Payment", "Balance", "Fee plan"):
            assert forbidden not in body, (url, forbidden)


def test_no_group_page_gained_a_research_control(people, client):
    """Research lives in its own area; it is not bolted onto Groups, and
    Phase 5's removal of Finance from Group pages still holds.

    Scoped to the page body: the Administrator sidebar names Research (and
    the Phase 5 workspaces) on every Administrator page by design, and that
    is the sidebar's job, not the Group page's.
    """
    _login_admin(client)
    html = rx.page(client, "/admin/groups")
    body = html[html.index('<div class="admin-content">'):]
    for forbidden in ("Research", "research-consent", "/admin/research",
                      "Fee assignments", "Invoices", "Payments", "LYD"):
        assert forbidden not in body, forbidden


def test_activating_an_older_draft_over_a_newer_current_document(people, client):
    """The case the activation's explicit flush exists for.

    The incoming draft has a **lower** internal id than the document it
    supersedes. SQLAlchemy orders the UPDATEs in one flush by primary key,
    not by assignment, so without clearing the outgoing marker in its own
    flush first, both rows would momentarily carry ``current_marker = 1``
    and break ``uq_research_consent_documents_current`` -- which neither
    MySQL nor SQLite defers to commit.
    """
    _login_admin(client)
    rx.create_document(client, version="v1.0", body=rx.BODY_V1)
    older = ResearchConsentDocument.query.filter_by(version_identifier="v1.0").one()
    newer = rx.publish_document(client, version="v2.0", body=rx.BODY_V2)
    assert older.id < newer.id

    response = rx.activate_document(client, older)
    assert rx.ACTIVATED_TEXT in response.get_data(as_text=True)

    db.session.expire_all()
    older = ResearchConsentDocument.query.filter_by(version_identifier="v1.0").one()
    newer = ResearchConsentDocument.query.filter_by(version_identifier="v2.0").one()
    assert (older.status, older.current_marker) == (_DOC_ACTIVE, 1)
    assert (newer.status, newer.current_marker) == (_SUPERSEDED, None)
    assert ResearchConsentDocument.query.filter_by(status=_DOC_ACTIVE).count() == 1
