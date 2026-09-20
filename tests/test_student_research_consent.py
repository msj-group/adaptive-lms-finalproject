"""Phase 6 / M01 Student research consent.

The whole point of this module is what must be **impossible**: hidden
consent, default consent, consent by another person, consent to a draft or a
superseded document, consent to wording that changed after the page was
read, a replayed POST that invents history, a withdrawal without an
acceptance, and any reactivation after a withdrawal.
"""

import re

import pytest

import tests.research_fixtures as rx
from app.extensions import db
from app.models import (
    ResearchConsentAction,
    ResearchConsentDocument,
    ResearchConsentDocumentStatus,
    ResearchConsentEvent,
    ResearchParticipant,
    ResearchParticipantStatus,
    STATUS_AFTER_ACTION,
    User,
)
from app.services import research_queries as queries

_ROUTES = {
    "student.research_consent": ("/student/research-consent", {"GET"}),
    "student.research_consent_accept": ("/student/research-consent/accept", {"POST"}),
    "student.research_consent_decline": ("/student/research-consent/decline", {"POST"}),
    "student.research_consent_withdraw": ("/student/research-consent/withdraw", {"GET"}),
    "student.research_consent_withdraw_confirm":
        ("/student/research-consent/withdraw", {"POST"}),
}

_DRAFT = ResearchConsentDocumentStatus.DRAFT.value
_DOC_ACTIVE = ResearchConsentDocumentStatus.ACTIVE.value
_SUPERSEDED = ResearchConsentDocumentStatus.SUPERSEDED.value

_INVITED = ResearchParticipantStatus.INVITED.value
_ACTIVE = ResearchParticipantStatus.ACTIVE.value
_DECLINED = ResearchParticipantStatus.DECLINED.value
_WITHDRAWN = ResearchParticipantStatus.WITHDRAWN.value


@pytest.fixture
def people(app):
    return rx.world(app)


def _participant():
    return ResearchParticipant.query.one()


def _events():
    return [
        (e.action, e.consent_version, e.actor_id)
        for e in ResearchConsentEvent.query.order_by(ResearchConsentEvent.id).all()
    ]


def _invited(people, document_status=_DOC_ACTIVE, version="v1.0", body=rx.BODY_V1):
    """An active consent document and an invited participant for the
    Student, written directly: this module tests the *decision*."""
    document = rx.document_row(people["admin"], version=version, body=body,
                               status=document_status)
    participant = rx.participant_row(people["student"], people["admin"])
    return document, participant


def _accepted(people, client):
    """An invited participant who has accepted through the real route."""
    document, participant = _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    rx.decide(client, "accept")
    db.session.expire_all()
    return document, _participant()


# ===========================================================================
# Inventory, authorization and headers
# ===========================================================================


def test_the_route_and_method_inventory_is_exact(app):
    rules = {}
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("student.research_consent"):
            rules[rule.endpoint] = (str(rule), set(rule.methods - {"HEAD", "OPTIONS"}))
    assert rules == _ROUTES


def test_no_route_carries_an_object_identifier(app):
    """The participant is found from the session, so there is nothing in any
    URL for anybody to change."""
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("student.research_consent"):
            assert "<" not in str(rule), str(rule)


def test_an_anonymous_visitor_is_redirected_to_login(people, client):
    _invited(people)
    for url in (rx.CONSENT_URL, rx.WITHDRAW_URL):
        response = client.get(url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"], url
    for url in (rx.ACCEPT_URL, rx.DECLINE_URL, rx.WITHDRAW_URL):
        response = client.post(url, data={})
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"], url
    assert ResearchConsentEvent.query.count() == 0


@pytest.mark.parametrize("email", [rx.ADMIN_EMAIL, rx.TEACHER_EMAIL, rx.RESEARCHER_EMAIL])
def test_no_other_role_can_consent_for_anybody(people, client, email):
    """An Administrator or a Researcher has no route to a Student's decision
    at all -- it is 403, not a hidden control."""
    _invited(people)
    rx.login(client, email)
    assert client.get(rx.CONSENT_URL).status_code == 403
    assert client.get(rx.WITHDRAW_URL).status_code == 403
    for url in (rx.ACCEPT_URL, rx.DECLINE_URL, rx.WITHDRAW_URL):
        assert client.post(url, data={"confirm": "yes"}).status_code == 403, url
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


def test_another_student_cannot_reach_or_move_this_participant(people, client):
    _invited(people)
    other = rx.make_student("other@example.com", name="Other Student")
    rx.login(client, other.email)
    # They have no participant of their own, so there is nothing to show.
    assert client.get(rx.CONSENT_URL).status_code == 404
    assert client.get(rx.WITHDRAW_URL).status_code == 404
    for url in (rx.ACCEPT_URL, rx.DECLINE_URL, rx.WITHDRAW_URL):
        assert client.post(url, data={"confirm": "yes"}).status_code == 404, url
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


def test_a_student_with_no_invitation_sees_no_consent_prompt(people, client):
    rx.document_row(people["admin"], status=_DOC_ACTIVE)
    rx.login(client, rx.STUDENT_EMAIL)
    assert client.get(rx.CONSENT_URL).status_code == 404
    dashboard = rx.page(client, "/student/dashboard")
    assert "Research consent" not in dashboard
    assert queries.portal_consent_status(
        User.query.filter_by(email=rx.STUDENT_EMAIL).one()) is None


def test_every_consent_response_is_private_no_store_and_varies_on_cookie(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    for url in (rx.CONSENT_URL,):
        response = client.get(url)
        assert response.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in response.headers["Vary"], url


def test_every_consent_mutation_requires_a_csrf_token(app, people):
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
            admin, student = rx.make_admin(), rx.make_student()
            rx.document_row(admin, status=_DOC_ACTIVE)
            rx.participant_row(student, admin)
            client = csrf_app.test_client()
            rx.login(client, rx.STUDENT_EMAIL)
            for url in (rx.ACCEPT_URL, rx.DECLINE_URL, rx.WITHDRAW_URL):
                assert client.post(url, data={"confirm": "yes"}).status_code == 400, url
            assert ResearchConsentEvent.query.count() == 0
            assert ResearchParticipant.query.one().status == _INVITED
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


# ===========================================================================
# Nothing is consent by implication
# ===========================================================================


def test_being_invited_is_not_consent(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    html = rx.page(client, rx.CONSENT_URL)
    assert "have not agreed to anything yet" in html
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


def test_reading_the_consent_page_writes_nothing(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    before = (_participant().status, _participant().version)
    for _ in range(3):
        rx.page(client, rx.CONSENT_URL)
    db.session.expire_all()
    assert (_participant().status, _participant().version) == before
    assert ResearchConsentEvent.query.count() == 0


def test_the_consent_page_has_no_pre_ticked_or_hidden_agreement(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    html = rx.page(client, rx.CONSENT_URL)
    assert "checked" not in html
    # The only hidden fields are the CSRF token and the signed state token.
    hidden = set(re.findall(r'<input type="hidden" name="([^"]+)"', html))
    assert hidden <= {"csrf_token", "state_token"}
    # No participant, student, document or version field for anyone to forge.
    for forbidden in ("participant_id", "participant_public_id", "student_id",
                      "consent_document_id", "document_public_id", "version",
                      "status", "action"):
        assert f'name="{forbidden}"' not in html, forbidden


def test_using_the_lms_normally_records_no_consent(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    for url in ("/student/dashboard", "/student/assignments", "/student/grades",
                "/student/announcements", "/student/calendar"):
        client.get(url)
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


# ===========================================================================
# Acceptance, refusal and withdrawal
# ===========================================================================


def test_an_explicit_acceptance_is_recorded_with_the_exact_version_read(people, client):
    document, _ = _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    response = rx.decide(client, "accept")
    assert rx.ACCEPTED_TEXT in response.get_data(as_text=True)

    db.session.expire_all()
    participant = _participant()
    student = User.query.filter_by(email=rx.STUDENT_EMAIL).one()
    assert participant.status == _ACTIVE
    assert participant.consent_document_id == document.id
    assert participant.decided_at is not None and participant.withdrawn_at is None
    assert participant.version == 2
    assert _events() == [
        (ResearchConsentAction.ACCEPTED.value, document.version_identifier, student.id)
    ]
    event = ResearchConsentEvent.query.one()
    assert event.consent_digest == document.body_digest
    assert event.actor_id == participant.student_id


def test_an_explicit_refusal_is_recorded_and_ends_the_prompt(people, client):
    document, _ = _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    response = rx.decide(client, "decline")
    assert "declined to take part" in response.get_data(as_text=True)

    db.session.expire_all()
    participant = _participant()
    assert participant.status == _DECLINED
    assert participant.consent_document_id == document.id
    assert participant.withdrawn_at is None
    assert [a for a, _v, _x in _events()] == [ResearchConsentAction.DECLINED.value]
    # The portal stops prompting, and the page states the truth.
    assert queries.portal_consent_status(
        User.query.filter_by(email=rx.STUDENT_EMAIL).one()) is None
    html = rx.page(client, rx.CONSENT_URL)
    assert "You declined to take part" in html
    assert "I have read this and I agree" not in html


def test_withdrawal_after_acceptance_adds_a_record_and_rewrites_nothing(people, client):
    document, _ = _accepted(people, client)
    accepted_event = ResearchConsentEvent.query.one()
    accepted_snapshot = (accepted_event.id, accepted_event.action,
                         accepted_event.consent_version, accepted_event.consent_digest,
                         accepted_event.occurred_at)

    response = rx.decide(client, "withdraw")
    assert rx.WITHDRAWN_TEXT in response.get_data(as_text=True)

    db.session.expire_all()
    participant = _participant()
    assert participant.status == _WITHDRAWN
    assert participant.withdrawn_at == participant.decided_at
    assert participant.consent_document_id == document.id
    assert participant.version == 3
    # The acceptance is still exactly the row it was.
    events = ResearchConsentEvent.query.order_by(ResearchConsentEvent.id).all()
    assert [e.action for e in events] == ["accepted", "withdrawn"]
    first = events[0]
    assert (first.id, first.action, first.consent_version, first.consent_digest,
            first.occurred_at) == accepted_snapshot


def test_the_withdrawal_page_needs_its_confirmation_box(people, client):
    _accepted(people, client)
    response = rx.decide(client, "withdraw", confirm="no")
    assert "Tick the confirmation box" in response.get_data(as_text=True)
    db.session.expire_all()
    assert _participant().status == _ACTIVE
    assert ResearchConsentEvent.query.count() == 1


def test_withdrawal_is_impossible_without_a_prior_acceptance(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    # The confirmation page sends an invited Student back rather than
    # offering a withdrawal that has nothing to withdraw.
    assert client.get(rx.WITHDRAW_URL).status_code == 302
    response = client.post(rx.WITHDRAW_URL, data={"state_token": "x", "confirm": "yes"},
                           follow_redirects=True)
    assert rx.NOT_ALLOWED_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


def test_a_declined_participant_cannot_withdraw_either(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    rx.decide(client, "decline")
    response = client.post(rx.WITHDRAW_URL, data={"state_token": "x", "confirm": "yes"},
                           follow_redirects=True)
    assert rx.NOT_ALLOWED_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert _participant().status == _DECLINED
    assert ResearchConsentEvent.query.count() == 1


def test_a_withdrawn_participant_is_never_reactivated(people, client):
    """Not by an old form, not by a replayed POST, not by a fresh one."""
    _accepted(people, client)
    stale_accept = rx.consent_token(client, "accept") or "x"
    rx.decide(client, "withdraw")
    db.session.expire_all()
    assert _participant().status == _WITHDRAWN

    for url, data in ((rx.ACCEPT_URL, {"state_token": stale_accept}),
                      (rx.DECLINE_URL, {"state_token": stale_accept}),
                      (rx.WITHDRAW_URL, {"state_token": stale_accept, "confirm": "yes"})):
        client.post(url, data=data, follow_redirects=True)
    db.session.expire_all()
    assert _participant().status == _WITHDRAWN
    assert [a for a, _v, _x in _events()] == ["accepted", "withdrawn"]
    # And the page offers no way back.
    html = rx.page(client, rx.CONSENT_URL)
    assert "You withdrew from the study" in html
    assert "I agree to take part" not in html
    assert queries.portal_consent_status(
        User.query.filter_by(email=rx.STUDENT_EMAIL).one()) is None


# ===========================================================================
# Documents that must never be accepted
# ===========================================================================


def test_no_consent_is_possible_without_an_active_document(people, client):
    rx.participant_row(people["student"], people["admin"])
    rx.login(client, rx.STUDENT_EMAIL)
    html = rx.page(client, rx.CONSENT_URL)
    assert "No consent document is available" in html
    assert "I agree to take part" not in html

    response = client.post(rx.ACCEPT_URL, data={"state_token": "x"}, follow_redirects=True)
    assert rx.NO_DOCUMENT_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


@pytest.mark.parametrize("status", [_DRAFT, _SUPERSEDED])
def test_a_draft_or_superseded_document_is_never_presented_or_accepted(
    people, client, status
):
    document, _ = _invited(people, document_status=status)
    rx.login(client, rx.STUDENT_EMAIL)
    html = rx.page(client, rx.CONSENT_URL)
    assert document.body not in html
    assert "No consent document is available" in html
    assert "I agree to take part" not in html

    response = client.post(rx.ACCEPT_URL, data={"state_token": "x"}, follow_redirects=True)
    assert rx.NO_DOCUMENT_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


def test_a_document_whose_digest_no_longer_matches_is_never_presented(people, client):
    import sqlalchemy as sa

    document, _ = _invited(people)
    db.session.execute(
        sa.text("UPDATE research_consent_documents SET body = :b WHERE id = :i"),
        {"b": "Silently different wording.", "i": document.id},
    )
    db.session.commit()
    rx.login(client, rx.STUDENT_EMAIL)
    html = rx.page(client, rx.CONSENT_URL)
    assert "I agree to take part" not in html
    response = client.post(rx.ACCEPT_URL, data={"state_token": "x"}, follow_redirects=True)
    assert rx.STALE_TEXT in response.get_data(
        as_text=True) or rx.DOCUMENT_CHANGED_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


def test_a_document_replaced_after_the_page_was_opened_fails_without_a_partial_write(
    people, client
):
    _invited(people, version="v1.0", body=rx.BODY_V1)
    rx.login(client, rx.STUDENT_EMAIL)
    stale_token = rx.consent_token(client, "accept")
    assert stale_token

    # An Administrator publishes new wording while the page sits open.
    rx.logout(client)
    rx.login(client, rx.ADMIN_EMAIL)
    rx.publish_document(client, version="v2.0", body=rx.BODY_V2)
    rx.logout(client)

    rx.login(client, rx.STUDENT_EMAIL)
    response = rx.decide(client, "accept", token=stale_token)
    body = response.get_data(as_text=True)
    assert rx.STALE_TEXT in body or rx.DOCUMENT_CHANGED_TEXT in body
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert _participant().version == 1
    assert ResearchConsentEvent.query.count() == 0
    # Reading the page again shows the current version, which can be accepted.
    html = rx.page(client, rx.CONSENT_URL)
    assert "v2.0" in html or rx.BODY_V2 in html
    rx.decide(client, "accept")
    db.session.expire_all()
    assert _participant().status == _ACTIVE
    assert ResearchConsentEvent.query.one().consent_version == "v2.0"


def test_an_accepted_version_keeps_showing_the_student_what_they_read(people, client):
    """After version 2 is published, a Student who accepted version 1 still
    sees version 1 on their own page -- not wording they never agreed to."""
    _accepted(people, client)
    rx.logout(client)
    rx.login(client, rx.ADMIN_EMAIL)
    rx.publish_document(client, version="v2.0", body=rx.BODY_V2)
    rx.logout(client)

    rx.login(client, rx.STUDENT_EMAIL)
    html = rx.page(client, rx.CONSENT_URL)
    assert rx.BODY_V1 in html
    assert rx.BODY_V2 not in html
    assert "the exact version you decided on" in html


# ===========================================================================
# Replays, stale forms and atomicity
# ===========================================================================


def test_a_repeated_acceptance_is_a_safe_no_op_and_creates_no_false_history(
    people, client
):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    token = rx.consent_token(client, "accept")
    rx.decide(client, "accept", token=token)
    db.session.expire_all()
    after_first = (_participant().status, _participant().version,
                   _participant().decided_at)

    for _ in range(3):
        response = rx.decide(client, "accept", token=token)
        assert rx.ALREADY_ACCEPTED_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert (_participant().status, _participant().version,
            _participant().decided_at) == after_first
    assert ResearchConsentEvent.query.count() == 1


def test_a_repeated_withdrawal_is_a_safe_no_op(people, client):
    _accepted(people, client)
    token = rx.consent_token(client, "withdraw")
    rx.decide(client, "withdraw", token=token)
    db.session.expire_all()
    after_first = (_participant().status, _participant().version,
                   _participant().withdrawn_at)

    for _ in range(3):
        response = rx.decide(client, "withdraw", token=token)
        assert rx.ALREADY_WITHDRAWN_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert (_participant().status, _participant().version,
            _participant().withdrawn_at) == after_first
    assert ResearchConsentEvent.query.count() == 2


def test_a_declined_student_cannot_then_accept_with_the_old_form(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    accept_token = rx.consent_token(client, "accept")
    rx.decide(client, "decline")
    db.session.expire_all()
    assert _participant().status == _DECLINED

    response = rx.decide(client, "accept", token=accept_token)
    body = response.get_data(as_text=True)
    assert rx.STALE_TEXT in body or rx.NOT_ALLOWED_TEXT in body
    db.session.expire_all()
    assert _participant().status == _DECLINED
    assert ResearchConsentEvent.query.count() == 1


def test_a_missing_or_forged_token_writes_nothing(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    for token in ("", "not-a-token", "x" * 5000):
        response = rx.decide(client, "accept", token=token)
        assert rx.STALE_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


def test_an_accept_token_cannot_be_replayed_at_the_decline_route(people, client):
    """Each token binds the action it was rendered for."""
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    accept_token = rx.consent_token(client, "accept")
    response = client.post(rx.DECLINE_URL, data={"state_token": accept_token},
                           follow_redirects=True)
    assert rx.STALE_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert _participant().status == _INVITED
    assert ResearchConsentEvent.query.count() == 0


def test_one_students_token_is_useless_to_another_student(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    token = rx.consent_token(client, "accept")
    rx.logout(client)

    other = rx.make_student("other2@example.com", name="Other Two")
    rx.participant_row(other, people["admin"], code="RP-BBBBBBBBBB")
    rx.login(client, other.email)
    response = client.post(rx.ACCEPT_URL, data={"state_token": token}, follow_redirects=True)
    assert rx.STALE_TEXT in response.get_data(as_text=True)
    db.session.expire_all()
    assert {p.status for p in ResearchParticipant.query.all()} == {_INVITED}
    assert ResearchConsentEvent.query.count() == 0


def test_the_status_and_the_history_always_agree(people, client):
    """Every transition writes the participant and its event in one commit,
    so the latest action always implies the stored status."""
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    for step in ("accept", "withdraw"):
        rx.decide(client, step)
        db.session.expire_all()
        events = ResearchConsentEvent.query.order_by(ResearchConsentEvent.id).all()
        assert STATUS_AFTER_ACTION[events[-1].action] == _participant().status
        assert len(events) == _participant().version - 1


def test_a_consent_decision_changes_no_account_or_financial_row(people, client):
    from app.models import Enrollment, Invoice, PaymentTransaction

    _invited(people)
    before = {
        "users": [(u.id, u.role, u.status, u.email, u.full_name)
                  for u in User.query.order_by(User.id)],
        "enrollments": Enrollment.query.count(),
        "invoices": Invoice.query.count(),
        "payments": PaymentTransaction.query.count(),
    }
    rx.login(client, rx.STUDENT_EMAIL)
    rx.decide(client, "accept")
    rx.decide(client, "withdraw")
    db.session.expire_all()
    after = {
        "users": [(u.id, u.role, u.status, u.email, u.full_name)
                  for u in User.query.order_by(User.id)],
        "enrollments": Enrollment.query.count(),
        "invoices": Invoice.query.count(),
        "payments": PaymentTransaction.query.count(),
    }
    assert after == before


def test_consent_never_blocks_ordinary_lms_access(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    pages = ("/student/dashboard", "/student/assignments", "/student/grades",
             "/student/announcements", "/student/calendar")
    for step in (None, "accept", "withdraw"):
        if step:
            rx.decide(client, step)
        for url in pages:
            assert client.get(url).status_code == 200, (step, url)


# ===========================================================================
# The Student portal link
# ===========================================================================


def test_the_portal_link_appears_only_when_there_is_something_to_do(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    invited = rx.page(client, "/student/dashboard")
    assert "Research consent" in invited
    assert ">Decide<" in invited or "Decide</span>" in invited

    rx.decide(client, "accept")
    active = rx.page(client, "/student/dashboard")
    assert "Research consent" in active
    assert "Decide</span>" not in active

    rx.decide(client, "withdraw")
    withdrawn = rx.page(client, "/student/dashboard")
    assert "Research consent" not in withdrawn


def test_a_teacher_never_sees_a_research_consent_link(people, client):
    _invited(people)
    rx.login(client, rx.TEACHER_EMAIL)
    assert "Research consent" not in rx.page(client, "/teacher/dashboard")


def test_the_consent_page_makes_no_claim_about_detecting_feelings(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    html = rx.page(client, rx.CONSENT_URL)
    assert "Behavioral patterns associated with possible frustration" in html or \
        "behavioural patterns associated with possible frustration" in html.lower()
    for claim in ("we detect", "detects", "knows how you feel", "proves that you",
                  "measures your emotion", "you are frustrated"):
        assert claim not in html.lower(), claim
    # And it is explicit that nothing is collected yet.
    assert "no research data is being collected" in html.lower()
    for promise in ("keystroke", "password", "audio", "camera", "fingerprinting"):
        assert promise in html.lower(), promise


def test_the_consent_page_states_the_unresolved_retention_question(people, client):
    _invited(people)
    rx.login(client, rx.STUDENT_EMAIL)
    # Whitespace-collapsed: the sentence is wrapped across lines in the
    # template, and the wrapping is not what this test is about.
    html = " ".join(rx.page(client, rx.CONSENT_URL).lower().split())
    assert "how long any future research data would be kept" in html
    assert "what would happen to data collected before a withdrawal" in html
    assert "ethics approval" in html
