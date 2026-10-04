"""Shared fixtures for the natural-use research suites (Phase 6 replacement).

Setup helpers write rows directly (or through the operator service) so each
test states exactly the state it needs; the behaviour under test always goes
through the real routes and services.

Under the population rule no Student needs a setup step to be collected: a
subject is created automatically at the first collection write. The shared
world therefore creates no subject for its eligible Students; it only records
the exclusion of one Student, as the operator would.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import (
    ResearchConfiguration,
    ResearchEvent,
    ResearchFeedbackPrompt,
    ResearchSession,
    ResearchSubject,
    ResearchSubjectLink,
    User,
    UserRole,
    UserStatus,
    now_ms,
)
from app.services import research_operator as operator
from tests.conftest import make_user

PW = "Sup3rSecret!123"
STUDENT = UserRole.STUDENT.value
TEACHER = UserRole.TEACHER.value
ADMIN = UserRole.ADMINISTRATOR.value
RESEARCHER = UserRole.RESEARCHER.value
SCHEMA = "natural-use-events.v1"


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def user(email, role, status=UserStatus.ACTIVE.value, full_name="Test User"):
    return make_user(email, role, status=status, password=PW, full_name=full_name)


def configuration(creator, *, status="active", collecting=True, starts=None, ends=None,
                  retention=30, version_number=None, **overrides):
    """A configuration row in the requested lifecycle state."""
    now = utcnow()
    values = dict(
        label="Pilot",
        event_schema_version=SCHEMA,
        collection_starts_at=starts or now - timedelta(days=1),
        collection_ends_at=ends or now + timedelta(days=30),
        session_inactivity_minutes=30, max_prompts_per_session=1, max_prompts_per_day=2,
        lookback_seconds=120, min_observed_seconds=120, random_prompt_permille=50,
        activity_end_prompt_permille=500, offer_ttl_seconds=600, response_window_seconds=600,
        created_by_id=creator.id, created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2), version=1,
    )
    values.update(overrides)
    if version_number is None:
        version_number = (db.session.query(db.func.max(ResearchConfiguration.version_number))
                          .scalar() or 0) + 1
    row = ResearchConfiguration(version_number=version_number, status="draft",
                                is_collecting=False, **values)
    if status in ("active", "retired"):
        row.status = status
        row.activated_at = now - timedelta(days=2)
        row.activated_by_id = creator.id
        row.retention_days = retention
        if status == "active":
            row.current_marker = 1
            row.is_collecting = collecting
        else:
            row.retired_at = now - timedelta(days=1)
            row.retired_by_id = creator.id
    db.session.add(row)
    db.session.commit()
    return row


def exclude(student_user):
    status, _code = operator.exclude(student_user.email)
    assert status == operator.EXCLUDED, status
    return subject_of(student_user)


def mark_demo(student_user):
    status, _code = operator.mark_demo(student_user.email)
    assert status == operator.MARKED_DEMO, status
    return subject_of(student_user)


def subject_of(student_user):
    return (
        ResearchSubject.query.join(
            ResearchSubjectLink, ResearchSubjectLink.subject_id == ResearchSubject.id)
        .filter(ResearchSubjectLink.user_id == student_user.id).first()
    )


def world(app, *, collecting=True, provenance_study=False):
    """A Researcher, an active collecting configuration, two eligible
    Students with no subject yet (``s1``, ``s2``: collected automatically),
    a Student the operator excluded before any collection (``excluded``), a
    Teacher and an Administrator."""
    researcher = user("researcher@example.com", RESEARCHER, full_name="Rhea Researcher")
    config = configuration(researcher, collecting=collecting)
    s1 = user("s1@example.com", STUDENT, full_name="Sam Student")
    s2 = user("s2@example.com", STUDENT, full_name="Sara Student")
    excluded = user("excluded@example.com", STUDENT, full_name="Ezra Excluded")
    teacher = user("teacher@example.com", TEACHER, full_name="Tara Teacher")
    admin = user("admin@example.com", ADMIN, full_name="Ada Admin")
    exclude(excluded)
    if provenance_study:
        app.config["RESEARCH_DATA_PROVENANCE"] = "study"
    return {"researcher": researcher, "config": config, "s1": s1, "s2": s2,
            "excluded": excluded, "teacher": teacher, "admin": admin}


def provision(client, email="s1@example.com"):
    """Let collection reach a Student the way it does in use: sign in and
    send one page view. Returns that Student's (automatic) subject."""
    login(client, email)
    response = post(client, batch([Browser().event("page_view", detail="wide")]))
    assert response.status_code == 200 and response.get_json()["collecting"] is True
    return subject_of(user_by_email(email))


def fresh():
    """Drop Flask-Login's per-request user cache. The test app context is
    reused across requests (and across test clients), so without this a
    second client would silently act as the first client's account."""
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def logout(client):
    """Sign out of whichever entry this client is signed in through."""
    fresh()
    client.post("/auth/logout")
    fresh()
    client.post("/research/logout")
    fresh()


def login(client, email):
    logout(client)
    response = client.post("/auth/login", data={"email": email, "password": PW},
                           follow_redirects=False)
    fresh()
    return response


def login_researcher(client, email="researcher@example.com"):
    logout(client)
    response = client.post("/research/login", data={"email": email, "password": PW},
                           follow_redirects=False)
    fresh()
    return response


def get(client, path, **kwargs):
    fresh()
    return client.get(path, **kwargs)


class Browser:
    """One browser tab's collector identity: a tab reference, a page view and
    a sequence counter. Separate instances model separate tabs."""

    def __init__(self, page="student.dashboard"):
        self.tab = str(uuid.uuid4())
        self.view = str(uuid.uuid4())
        self.page = page
        self.seq = 0

    def event(self, event_type, t=None, page=None, **fields):
        self.seq += 1
        body = {"id": str(uuid.uuid4()), "type": event_type,
                "t": now_ms() if t is None else t, "page": page or self.page,
                "view": self.view, "tab": self.tab, "seq": self.seq}
        body.update(fields)
        return body


def batch(events, sent_at=None, **extra):
    payload = {"schema": SCHEMA, "sent_at": now_ms() if sent_at is None else sent_at,
               "events": events}
    payload.update(extra)
    return payload


def post(client, payload, url="/collect/events", raw=None, headers=None):
    fresh()
    data = raw if raw is not None else json.dumps(payload)
    return client.post(url, data=data, content_type="application/json", headers=headers or {})


def events_of(session_id=None):
    query = ResearchEvent.query
    if session_id is not None:
        query = query.filter_by(session_id=session_id)
    return query.order_by(ResearchEvent.id).all()


def sessions_of(subject):
    return ResearchSession.query.filter_by(subject_id=subject.id).order_by(ResearchSession.id).all()


def prompts():
    return ResearchFeedbackPrompt.query.order_by(ResearchFeedbackPrompt.id).all()


def observe(client, browser, seconds, start_ms=None, step=30):
    """Send heartbeats covering `seconds` of continuous visible observation,
    ending now (client clock == server clock in tests)."""
    end = now_ms() if start_ms is None else start_ms + seconds * 1000
    begin = end - seconds * 1000
    moments = list(range(begin, end + 1, step * 1000))
    events = [browser.event("page_view", t=moments[0], detail="wide")]
    events += [browser.event("heartbeat", t=m, detail="active") for m in moments[1:]]
    responses = []
    for chunk in range(0, len(events), 50):
        responses.append(post(client, batch(events[chunk:chunk + 50])))
    return responses


def user_by_email(email):
    return User.query.filter_by(email=email).one()


def age_session(session_id, milliseconds):
    """Move one session's moments into the past by `milliseconds`, as if that
    much time had passed. Raw SQL on purpose: the ORM guard refuses to
    rewrite a session start, which is exactly what it should do in the
    application."""
    from sqlalchemy import text

    db.session.execute(text(
        "UPDATE research_sessions SET started_at_ms = started_at_ms - :d,"
        " last_seen_at_ms = last_seen_at_ms - :d,"
        " observed_since_ms = observed_since_ms - :d,"
        " last_observed_at_ms = last_observed_at_ms - :d,"
        " activity_end_pending_at_ms = activity_end_pending_at_ms - :d"
        " WHERE id = :id"), {"d": milliseconds, "id": session_id})
    db.session.execute(text(
        "UPDATE research_events SET occurred_at_ms = occurred_at_ms - :d,"
        " received_at_ms = received_at_ms - :d,"
        " client_ts_ms = client_ts_ms - :d WHERE session_id = :id"),
        {"d": milliseconds, "id": session_id})
    db.session.commit()
    db.session.expire_all()


def retire(config, researcher):
    """Retire an active configuration the way activation of a successor would."""
    researcher_id = researcher.id
    with db.session.no_autoflush:
        config.status = "retired"
        config.current_marker = None
        config.is_collecting = False
        config.retired_at = utcnow()
        config.retired_by_id = researcher_id
        config.version += 1
    db.session.commit()


def shift_period(config_id, starts_at=None, ends_at=None, **policy):
    """Time travel for a frozen configuration: as if the clock moved relative
    to its period (or, for a policy value, as if it had been created that
    way). Raw SQL because the model correctly refuses to change an active
    version."""
    from sqlalchemy import text

    values = {}
    # Formatted the way SQLAlchemy stores a DateTime on SQLite, so no
    # deprecated sqlite3 default adapter is involved.
    if starts_at is not None:
        values["collection_starts_at"] = starts_at.strftime("%Y-%m-%d %H:%M:%S.%f")
    if ends_at is not None:
        values["collection_ends_at"] = ends_at.strftime("%Y-%m-%d %H:%M:%S.%f")
    values.update(policy)
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    db.session.execute(text(f"UPDATE research_configurations SET {assignments} WHERE id = :id"),
                       dict(values, id=config_id))
    db.session.commit()
    db.session.expire_all()


def age_prompt(prompt_id, milliseconds):
    """Move one prompt's moments into the past (raw SQL, see age_session)."""
    from sqlalchemy import text

    db.session.execute(text(
        "UPDATE research_feedback_prompts SET offered_at_ms = offered_at_ms - :d,"
        " displayed_at_ms = displayed_at_ms - :d, window_start_ms = window_start_ms - :d,"
        " window_end_ms = window_end_ms - :d WHERE id = :id"),
        {"d": milliseconds, "id": prompt_id})
    db.session.commit()
    db.session.expire_all()


def prompt_url(prompt_public_id, action):
    return f"/collect/prompts/{prompt_public_id}/{action}"


@pytest.fixture
def fresh_identity_per_request(monkeypatch):
    """Clear Flask-Login's cached user before every test-client request, so a
    test that drives several clients never acts as the wrong account (the
    test application context, and so ``g``, is shared between requests)."""
    from flask.testing import FlaskClient

    original = FlaskClient.open

    def open_(self, *args, **kwargs):
        fresh()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FlaskClient, "open", open_)
