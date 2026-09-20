"""Shared fixtures for the Phase 6 / M01 research test modules.

Kept in one module so the model, route, privacy and migration suites build
the same accounts, consent documents and participants.

Two kinds of helper, deliberately separated:

- **Row helpers** (:func:`document_row`, :func:`participant_row`) write
  straight into the tables, so a test about (say) a superseded document
  being unacceptable is not also a test of the activation route.
- **Route helpers** (:func:`create_document`, :func:`activate_document`,
  :func:`invite`, :func:`decide`) drive the application's own write path and
  read every signed token out of the page the server rendered -- never out
  of a service call -- which is the only way a test proves the *page* and
  the *route* agree.

**No test here ever creates a real Researcher account for use outside the
suite**, and no consent wording in this module is, or claims to be, ethics
approved: every string is obvious development placeholder text.
"""

import re

from app.extensions import db
from app.models import (
    ResearchConsentDocument,
    ResearchConsentDocumentStatus,
    ResearchParticipant,
    ResearchParticipantStatus,
    UserRole,
    UserStatus,
    consent_digest,
)
from app.models.submission_feedback import whole_second_utc
from tests.conftest import make_user

PW = "MyValidPassphrase123"

#: Obvious development placeholder wording. Not ethics approved, not a real
#: participant information sheet, and never presented as one.
BODY_V1 = (
    "Development placeholder consent wording, version 1.\n"
    "This text has not been reviewed or approved by any ethics committee."
)
BODY_V2 = (
    "Development placeholder consent wording, version 2.\n"
    "This text has not been reviewed or approved by any ethics committee."
)

TITLE = "Participant Information And Consent"

STATE_FIELD = "state_token"

# Wording the routes flash, asserted by name rather than by retyping it.
STALE_TEXT = "no longer describes the current"
ACTIVATED_TEXT = "now the current one"
DRAFT_SAVED_TEXT = "draft saved"
ACCEPTED_TEXT = "participation has been recorded"
DECLINED_TEXT = "you have declined to take part"
WITHDRAWN_TEXT = "withdrawn from the study"
ALREADY_ACCEPTED_TEXT = "already accepted"
ALREADY_WITHDRAWN_TEXT = "already withdrawn"
NOT_ALLOWED_TEXT = "not a decision you can take"
DOCUMENT_CHANGED_TEXT = "changed after this page was opened"
NO_DOCUMENT_TEXT = "no active consent document"
ALREADY_LINKED_TEXT = "already a research participant"
NOT_STUDENT_TEXT = "Only a Student account"
SUSPENDED_TEXT = "is suspended, so it cannot be invited"

ADMIN_EMAIL = "research-admin@example.com"
STUDENT_EMAIL = "research-student@example.com"
RESEARCHER_EMAIL = "research-researcher@example.com"
TEACHER_EMAIL = "research-teacher@example.com"

OVERVIEW_URL = "/admin/research"
DOCUMENT_NEW_URL = "/admin/research/consent-documents/new"
ADMIN_PARTICIPANTS_URL = "/admin/research/participants"
INVITE_URL = "/admin/research/participants/new"
RESEARCH_DASHBOARD_URL = "/research/dashboard"
RESEARCH_PARTICIPANTS_URL = "/research/participants"
CONSENT_URL = "/student/research-consent"
ACCEPT_URL = "/student/research-consent/accept"
DECLINE_URL = "/student/research-consent/decline"
WITHDRAW_URL = "/student/research-consent/withdraw"


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def make_admin(email=ADMIN_EMAIL, name="Research Admin"):
    return make_user(email, UserRole.ADMINISTRATOR.value, password=PW, full_name=name)


def make_student(email=STUDENT_EMAIL, name="Amina Student", status=UserStatus.ACTIVE.value):
    return make_user(email, UserRole.STUDENT.value, password=PW, full_name=name, status=status)


def make_researcher(email=RESEARCHER_EMAIL, name="Rashid Researcher"):
    return make_user(email, UserRole.RESEARCHER.value, password=PW, full_name=name)


def make_teacher(email=TEACHER_EMAIL, name="Tariq Teacher"):
    return make_user(email, UserRole.TEACHER.value, password=PW, full_name=name)


def login(client, email):
    return client.post(
        "/auth/login", data={"email": email, "password": PW}, follow_redirects=True
    )


def logout(client):
    return client.post("/auth/logout")


def world(_app):
    """One Administrator, one Student, one Researcher and one Teacher.

    Deliberately **not** inside a nested ``app.app_context()``: the ``app``
    fixture already pushes one for the whole test, and pushing a second
    would pop the Flask-SQLAlchemy session with it and detach every account
    the test still holds.
    """
    return {
        "admin": make_admin(),
        "student": make_student(),
        "researcher": make_researcher(),
        "teacher": make_teacher(),
    }


# ---------------------------------------------------------------------------
# Row helpers -- straight into the tables
# ---------------------------------------------------------------------------


def document_row(creator, version="v1.0", title=TITLE, body=BODY_V1, status=None,
                 activator=None, superseder=None, moment=None):
    """One consent document written directly, in any lifecycle state."""
    status = status or ResearchConsentDocumentStatus.DRAFT.value
    moment = moment or whole_second_utc()
    activated = status in (
        ResearchConsentDocumentStatus.ACTIVE.value,
        ResearchConsentDocumentStatus.SUPERSEDED.value,
    )
    superseded = status == ResearchConsentDocumentStatus.SUPERSEDED.value
    document = ResearchConsentDocument(
        version_identifier=version,
        title=title,
        body=body,
        body_digest=consent_digest(version, title, body),
        status=status,
        current_marker=1 if status == ResearchConsentDocumentStatus.ACTIVE.value else None,
        created_by_id=creator.id,
        activated_at=moment if activated else None,
        activated_by_id=(activator or creator).id if activated else None,
        superseded_at=moment if superseded else None,
        superseded_by_id=(superseder or creator).id if superseded else None,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(document)
    db.session.commit()
    return document


def participant_row(student, inviter, code="RP-AAAAAAAAAA", status=None, document=None,
                    moment=None):
    """One participant written directly, in any lifecycle state."""
    status = status or ResearchParticipantStatus.INVITED.value
    moment = moment or whole_second_utc()
    decided = status != ResearchParticipantStatus.INVITED.value
    withdrawn = status == ResearchParticipantStatus.WITHDRAWN.value
    participant = ResearchParticipant(
        student_id=student.id,
        participant_code=code,
        status=status,
        consent_document_id=document.id if decided else None,
        decided_at=moment if decided else None,
        withdrawn_at=moment if withdrawn else None,
        invited_by_id=inviter.id,
        version=2 if decided else 1,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(participant)
    db.session.commit()
    return participant


# ---------------------------------------------------------------------------
# Route helpers -- the application's own write path
# ---------------------------------------------------------------------------


def _token(html, action_url=None):
    """The signed token from the rendered page. With `action_url`, the one
    inside that exact form -- so a test cannot accidentally read the accept
    token and post it to the decline route."""
    if action_url:
        form = re.search(
            rf'action="{re.escape(action_url)}"(.*?)</form>', html, re.S
        )
        if form is None:
            return None
        html = form.group(1)
    found = re.search(rf'name="{STATE_FIELD}" value="([^"]+)"', html)
    return None if found is None else found.group(1)


def page(client, url, status=200):
    response = client.get(url)
    assert response.status_code == status, (url, response.status_code)
    return response.get_data(as_text=True)


def create_document(client, version="v1.0", title=TITLE, body=BODY_V1):
    return client.post(
        DOCUMENT_NEW_URL,
        data={"version_identifier": version, "title": title, "body": body},
        follow_redirects=True,
    )


def document_url(document):
    return f"/admin/research/consent-documents/{document.public_id}"


def activate_document(client, document, token=None, confirm="yes"):
    if token is None:
        token = _token(page(client, document_url(document)))
    return client.post(
        f"{document_url(document)}/activate",
        data={STATE_FIELD: token, "confirm": confirm},
        follow_redirects=True,
    )


def publish_document(client, version="v1.0", title=TITLE, body=BODY_V1):
    """Create a draft through the form and activate it. Returns the row."""
    create_document(client, version=version, title=title, body=body)
    document = ResearchConsentDocument.query.filter_by(version_identifier=version).one()
    activate_document(client, document)
    db.session.expire_all()
    return ResearchConsentDocument.query.filter_by(version_identifier=version).one()


def invitation_token(client, student, search=""):
    """The candidate token the invitation form rendered for `student`."""
    html = page(client, f"{INVITE_URL}?q={search}")
    row = re.search(
        rf'data-candidate="{re.escape(student.public_id)}"(.*?)</tr>', html, re.S
    )
    if row is None:
        return None
    return _token(row.group(1))


def invite(client, student, token=None, student_public_id=None):
    if token is None:
        token = invitation_token(client, student)
    return client.post(
        INVITE_URL,
        data={
            STATE_FIELD: token or "",
            "student_public_id": student_public_id or student.public_id,
        },
        follow_redirects=True,
    )


def consent_token(client, action):
    """The token for `action` ('accept', 'decline' or 'withdraw') out of the
    Student's own rendered consent or withdrawal page."""
    if action == "withdraw":
        return _token(page(client, WITHDRAW_URL))
    url = ACCEPT_URL if action == "accept" else DECLINE_URL
    return _token(page(client, CONSENT_URL), url)


def decide(client, action, token=None, confirm="yes"):
    if token is None:
        token = consent_token(client, action)
    url = {"accept": ACCEPT_URL, "decline": DECLINE_URL, "withdraw": WITHDRAW_URL}[action]
    data = {STATE_FIELD: token or ""}
    if action == "withdraw":
        data["confirm"] = confirm
    return client.post(url, data=data, follow_redirects=True)
