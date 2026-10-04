"""The Researcher workspace: configurations, dashboard, sessions, exclusions,
exports and the audit (Phase 6 replacement, corrected).

Every page reads pseudonymous research rows only; every write is a POST with
a signed, version-bound token and an audit event; exports carry study data
only, with a data dictionary and a manifest, and never an identity. An export
id serves the immutable archive stored when it was created -- never a
rebuild -- until retention removes it.
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import timedelta

import pytest

import tests.research_world as rw
from tests.research_world import fresh_identity_per_request  # noqa: F401
from app.extensions import db
from app.models import (
    ResearchAuditEvent,
    ResearchConfiguration,
    ResearchExport,
    ResearchExportArchive,
    ResearchFeedbackPrompt,
    ResearchSession,
)
from app.services import research_operator as operator
from app.services import research_configuration_transactions as tx
from app.services import research_exports as exporter
from app.services import research_sampling as sampling
from app.services import research_tokens as tokens
from tests.structural_checks import redact_signed_values


@pytest.fixture(autouse=True)
def _fresh_identity(fresh_identity_per_request):
    """Every request re-reads its own client's login."""


@pytest.fixture
def researcher(app):
    user = rw.user("researcher@example.com", rw.RESEARCHER, full_name="Rhea Researcher")
    return user


def _form_values(**overrides):
    values = {
        "label": "Pilot 2", "collection_starts_at": "2026-10-01T08:00",
        "collection_ends_at": "2030-12-01T20:00",
        "session_inactivity_minutes": "30", "max_prompts_per_session": "1",
        "max_prompts_per_day": "2", "lookback_seconds": "120", "min_observed_seconds": "120",
        "random_prompt_permille": "50", "activity_end_prompt_permille": "500",
        "offer_ttl_seconds": "600", "response_window_seconds": "600",
    }
    values.update(overrides)
    return values


def _state(html):
    match = re.search(r'name="state" type="hidden" value="([^"]+)"', html) or \
        re.search(r'name="state" value="([^"]+)"', html)
    return match.group(1) if match else None


def _create(client):
    response = client.post("/research/configurations/new", data=_form_values())
    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    return ResearchConfiguration.query.order_by(ResearchConfiguration.id.desc()).first()


# ---------------------------------------------------------------------------
# Configurations
# ---------------------------------------------------------------------------


def test_a_researcher_creates_a_draft_that_collects_nothing(app, client, researcher):
    rw.login_researcher(client)
    config = _create(client)
    assert config.status == "draft" and not config.is_collecting
    assert config.version_number == 1 and config.event_schema_version == rw.SCHEMA
    assert config.created_by_id == researcher.id and config.retention_days is None
    audit = ResearchAuditEvent.query.one()
    assert (audit.action, audit.channel, audit.actor_id) == ("configuration_created",
                                                             "workspace", researcher.id)


def test_server_owned_fields_cannot_be_forged_through_the_form(app, client, researcher):
    rw.login_researcher(client)
    client.post("/research/configurations/new", data=_form_values(
        status="active", is_collecting="y", current_marker="1", retention_days="9999",
        version_number="77", event_schema_version="natural-use-events.v0"))
    config = ResearchConfiguration.query.one()
    assert (config.status, config.is_collecting, config.current_marker, config.retention_days,
            config.version_number, config.event_schema_version) == (
        "draft", False, None, None, 1, rw.SCHEMA)


@pytest.mark.parametrize("field, value", [
    ("lookback_seconds", "10"), ("max_prompts_per_day", "7"), ("random_prompt_permille", "1001"),
    ("min_observed_seconds", "300"), ("collection_ends_at", "2026-09-01T08:00"), ("label", " "),
])
def test_invalid_drafts_are_refused_with_nothing_written(app, client, researcher, field, value):
    rw.login_researcher(client)
    response = client.post("/research/configurations/new", data=_form_values(**{field: value}))
    assert response.status_code == 200
    assert ResearchConfiguration.query.count() == 0


def test_editing_a_draft_is_bound_to_its_version(app, client, researcher):
    rw.login_researcher(client)
    config = _create(client)
    url = f"/research/configurations/{config.public_id}/edit"
    stale = _state(client.get(url).get_data(as_text=True))
    fresh = _state(client.get(url).get_data(as_text=True))
    assert client.post(url, data=_form_values(label="First", state=fresh)).status_code == 302
    db.session.expire_all()
    assert ResearchConfiguration.query.one().label == "First"
    client.post(url, data=_form_values(label="Second", state=stale))
    db.session.expire_all()
    config = ResearchConfiguration.query.one()
    assert config.label == "First" and config.version == 2


def test_activation_needs_the_deployment_retention(app, client, researcher):
    rw.login_researcher(client)
    config = _create(client)
    detail = client.get(f"/research/configurations/{config.public_id}").get_data(as_text=True)
    assert "has not supplied RESEARCH_RETENTION_DAYS" in detail
    client.post(f"/research/configurations/{config.public_id}/activate",
                data={"confirm": "y", "state": _state(detail)})
    db.session.expire_all()
    assert ResearchConfiguration.query.one().status == "draft"


def test_activation_freezes_retires_and_does_not_start_collection(app, client, researcher):
    app.config["RESEARCH_RETENTION_DAYS"] = 180
    previous = rw.configuration(researcher, collecting=True)
    rw.user("s1@example.com", rw.STUDENT)
    other = app.test_client()
    rw.login(other, "s1@example.com")
    rw.post(other, rw.batch([rw.Browser().event("page_view", detail="wide")]))
    rw.login_researcher(client)
    config = _create(client)
    detail = client.get(f"/research/configurations/{config.public_id}").get_data(as_text=True)
    response = client.post(f"/research/configurations/{config.public_id}/activate",
                           data={"confirm": "y", "state": _state(detail)})
    assert response.status_code == 302
    db.session.expire_all()
    new = ResearchConfiguration.query.filter_by(public_id=config.public_id).one()
    old = db.session.get(ResearchConfiguration, previous.id)
    assert new.status == "active" and new.current_marker == 1 and not new.is_collecting
    assert new.retention_days == 180 and new.activated_by_id == researcher.id
    assert old.status == "retired" and old.current_marker is None and not old.is_collecting
    assert ResearchSession.query.one().end_reason == "configuration_changed"
    actions = [a.action for a in ResearchAuditEvent.query.order_by(ResearchAuditEvent.id)]
    assert actions[-1] == "configuration_activated"
    # Frozen: the edit page refuses and the model refuses.
    assert client.get(f"/research/configurations/{config.public_id}/edit").status_code == 302
    new.label = "Changed"
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_activation_needs_the_confirmation_box(app, client, researcher):
    app.config["RESEARCH_RETENTION_DAYS"] = 30
    rw.login_researcher(client)
    config = _create(client)
    detail = client.get(f"/research/configurations/{config.public_id}").get_data(as_text=True)
    client.post(f"/research/configurations/{config.public_id}/activate",
                data={"state": _state(detail)})
    db.session.expire_all()
    assert ResearchConfiguration.query.one().status == "draft"


def test_pause_and_resume_are_audited_and_pausing_closes_sessions(app, client, researcher):
    config = rw.configuration(researcher, collecting=True)
    rw.user("s1@example.com", rw.STUDENT)
    other = app.test_client()
    rw.login(other, "s1@example.com")
    rw.post(other, rw.batch([rw.Browser().event("page_view", detail="wide")]))
    rw.login_researcher(client)
    url = f"/research/configurations/{config.public_id}"
    state = _state(client.get(url).get_data(as_text=True))
    assert client.post(url + "/collecting", data={"state": state}).status_code == 302
    db.session.expire_all()
    assert not ResearchConfiguration.query.one().is_collecting
    assert ResearchSession.query.one().end_reason == "collection_stopped"
    assert rw.post(other, rw.batch([rw.Browser().event("page_view", detail="wide")])) \
        .get_json() == {"collecting": False}
    # A replayed pause token is stale now; a fresh one resumes.
    assert client.post(url + "/collecting", data={"state": state}).status_code == 302
    db.session.expire_all()
    assert not ResearchConfiguration.query.one().is_collecting
    state = _state(client.get(url).get_data(as_text=True))
    client.post(url + "/collecting", data={"state": state})
    db.session.expire_all()
    assert ResearchConfiguration.query.one().is_collecting
    actions = [a.action for a in ResearchAuditEvent.query.order_by(ResearchAuditEvent.id)]
    assert actions[-2:] == ["collection_paused", "collection_resumed"]


def test_tokens_are_bound_to_actor_configuration_version_and_purpose(app, researcher):
    with app.test_request_context():
        token = tokens.make_token(tokens.PURPOSE_EDIT, "actor", "config", 3)
        assert tokens.load_token(tokens.PURPOSE_EDIT, token, "actor", "config")["version"] == 3
        assert tokens.load_token(tokens.PURPOSE_ACTIVATE, token, "actor", "config") is None
        assert tokens.load_token(tokens.PURPOSE_EDIT, token, "other", "config") is None
        assert tokens.load_token(tokens.PURPOSE_EDIT, token, "actor", "other") is None
        assert tokens.load_token(tokens.PURPOSE_EDIT, token + "x", "actor", "config") is None


def test_the_transactions_re_prove_an_active_researcher(app, researcher):
    student = rw.user("student@example.com", rw.STUDENT)
    values = {c: getattr(rw.configuration(researcher, status="draft"), c)
              for c in tx.EDITABLE_COLUMNS}
    assert tx.create_draft(student.id, values) == (tx.UNAUTHORIZED, None)
    researcher.status = "suspended"
    db.session.commit()
    assert tx.create_draft(researcher.id, values) == (tx.UNAUTHORIZED, None)


def test_configuration_pages_state_what_activation_is_not(app, client, researcher):
    rw.login_researcher(client)
    config = _create(client)
    body = " ".join(client.get(f"/research/configurations/{config.public_id}")
                    .get_data(as_text=True).split())
    assert "not a consent document, not an ethics approval" in body
    assert "does not start collection by itself" in body


# ---------------------------------------------------------------------------
# Dashboard and sessions: real counts, pseudonymous rows
# ---------------------------------------------------------------------------


def _collected_world(app, monkeypatch):
    people = rw.world(app, provenance_study=True)
    monkeypatch.setattr(sampling, "_draw", lambda: 0)
    browser_client = app.test_client()
    rw.login(browser_client, "s1@example.com")
    browser = rw.Browser()
    body = rw.observe(browser_client, browser, 150)[-1].get_json()
    prompt_id = body["prompt"]["id"]
    rw.post(browser_client, {}, url=rw.prompt_url(prompt_id, "display"))
    rw.post(browser_client, {"rating": 3, "causes": ["content"]},
            url=rw.prompt_url(prompt_id, "respond"))
    rw.get(browser_client, "/student/search?q=%3DHYPERLINK(%22x%22)")
    return people


def test_the_dashboard_shows_real_counts_and_empty_states(app, client, researcher):
    rw.login_researcher(client)
    body = " ".join(client.get("/research/dashboard").get_data(as_text=True).split())
    assert "No active configuration" in body and "No sessions" in body
    assert "No prompts" in body
    assert "Researcher multi-factor authentication" not in body
    assert "Not implemented" not in body
    assert "Not configured: no configuration can be activated." in body


def test_the_dashboard_counts_collected_study_data(app, client, monkeypatch):
    _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    html = client.get("/research/dashboard").get_data(as_text=True)
    assert re.search(r'data-sessions="sessions"><div class="stat-card__value">1<', html)
    # s1 was collected automatically; s2 never opened a page; one exclusion.
    assert re.search(r'data-subjects="included"><div class="stat-card__value">1<', html)
    assert re.search(r'data-subjects="excluded"><div class="stat-card__value">1<', html)
    assert re.search(r'data-subjects="reinstated"><div class="stat-card__value">0<', html)
    assert 'data-label-coverage>100.0%' in html
    assert re.search(r'data-prompt-state="answered"><td>Answered</td><td>1<', html)
    assert not re.search(r"\bpredictions?\b", html.lower())
    assert "accuracy" not in html.lower()


def test_development_data_is_shown_only_when_selected_and_labelled(app, client, monkeypatch):
    people = rw.world(app)
    monkeypatch.setattr(sampling, "_draw", lambda: 999)
    student_client = app.test_client()
    rw.login(student_client, "s1@example.com")
    rw.post(student_client, rw.batch([rw.Browser().event("page_view", detail="wide")]))
    rw.login_researcher(client)
    study = client.get("/research/sessions").get_data(as_text=True)
    assert "No sessions" in study
    development = client.get("/research/sessions?provenance=development").get_data(as_text=True)
    assert "Development data — not research data" in development
    assert rw.subject_of(people["s1"]).subject_code in development


def test_research_pages_never_render_an_identity(app, client, monkeypatch):
    people = _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    session = ResearchSession.query.first()
    pages = ["/research/dashboard", "/research/sessions",
             f"/research/sessions/{session.public_id}", "/research/activity-log",
             "/research/configurations", f"/research/configurations/{people['config'].public_id}"]
    for path in pages:
        html = redact_signed_values(client.get(path).get_data(as_text=True))
        for secret in ("Sam Student", "s1@example.com", "Sara Student", "s2@example.com",
                       people["s1"].public_id, "Rhea Researcher", "researcher@example.com"):
            assert secret not in html, (path, secret)
        for internal in (people["s1"].id, people["s2"].id):
            assert f"/{internal}\"" not in html
    detail = client.get(f"/research/sessions/{session.public_id}").get_data(as_text=True)
    assert rw.subject_of(people["s1"]).subject_code in detail
    assert 'data-event="page_view"' in detail


def test_session_detail_is_a_plain_404_for_unknown_or_malformed_ids(app, client, researcher):
    rw.login_researcher(client)
    for bad in ("nope", "00000000-0000-4000-8000-000000000000"):
        assert client.get(f"/research/sessions/{bad}").status_code == 404


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def _export(client, **data):
    response = client.post("/research/exports", data=data)
    assert response.status_code == 302, response.get_data(as_text=True)[:400]
    return ResearchExport.query.order_by(ResearchExport.id.desc()).first()


def _open(data):
    archive = zipfile.ZipFile(io.BytesIO(data))
    files = {name: archive.read(name) for name in archive.namelist()}
    return files, {name: list(csv.reader(io.StringIO(files[name].decode())))
                   for name in files if name.endswith(".csv")}


def test_an_export_is_a_study_only_archive_stored_once(app, client, monkeypatch):
    people = _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    export = _export(client)
    assert (export.sessions_count, export.prompts_count, export.subjects_count) == (1, 1, 1)
    assert export.timezone == app.config["APP_TIMEZONE"]
    archive = ResearchExportArchive.query.filter_by(export_id=export.id).one()
    first = client.get(f"/research/exports/{export.public_id}/download")
    assert first.status_code == 200 and "X-Export-Matches-Snapshot" not in first.headers
    assert first.headers["Cache-Control"] == "private, no-store"
    assert first.data == bytes(archive.content)
    assert hashlib.sha256(first.data).hexdigest() == archive.archive_sha256
    assert len(first.data) == archive.byte_size
    second = client.get(f"/research/exports/{export.public_id}/download")
    assert first.data == second.data
    files, tables = _open(first.data)
    assert set(files) == {"manifest.json", "data_dictionary.csv", "sessions.csv",
                          "events.csv", "prompts.csv"}
    manifest = json.loads(files["manifest.json"])
    assert hashlib.sha256(files["manifest.json"]).hexdigest() == export.manifest_digest
    for name, digest in manifest["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest
    assert manifest["label_transformation"] == "none; raw 1-5 ratings preserved"
    assert manifest["filters"]["provenance"] == "study"
    assert manifest["event_schema_version"] == rw.SCHEMA
    assert manifest["configuration_versions"] == [people["config"].version_number]
    prompts = tables["prompts.csv"]
    header = prompts[0]
    row = dict(zip(header, prompts[1]))
    assert row["rating"] == "3" and row["cause_content"] == "1" and row["cause_interface"] == "0"
    assert row["derived_state"] == "answered"
    assert int(row["window_end_ms"]) - int(row["window_start_ms"]) == 120_000
    actions = [a.action for a in ResearchAuditEvent.query.order_by(ResearchAuditEvent.id)]
    assert actions[-3:] == ["export_created", "export_downloaded", "export_downloaded"]


def test_an_export_id_serves_identical_bytes_after_the_data_changes(app, client, monkeypatch):
    """Create an export, then a rating is submitted, an event arrives and the
    session ends: the original id still serves exactly the original bytes."""
    rw.world(app, provenance_study=True)
    monkeypatch.setattr(sampling, "_draw", lambda: 0)
    student = app.test_client()
    rw.login(student, "s1@example.com")
    browser = rw.Browser()
    prompt_id = rw.observe(student, browser, 150)[-1].get_json()["prompt"]["id"]
    assert rw.post(student, {}, url=rw.prompt_url(prompt_id, "display")).status_code == 200
    rw.login_researcher(client)
    export = _export(client)
    original = client.get(f"/research/exports/{export.public_id}/download").data
    row = dict(zip(*_open(original)[1]["prompts.csv"][:2]))
    assert row["rating"] == "" and row["derived_state"] == "awaiting_response"

    answered = rw.post(student, {"rating": 4, "causes": ["technical"]},
                       url=rw.prompt_url(prompt_id, "respond"))
    assert answered.get_json()["recorded"] is True
    assert rw.post(student, rw.batch([browser.event("control_click", element="nav_quizzes")])) \
        .get_json()["accepted"] == 1
    rw.fresh()
    student.post("/auth/logout")
    db.session.expire_all()
    assert ResearchSession.query.one().end_reason == "logout"
    assert ResearchFeedbackPrompt.query.one().rating == 4

    again = client.get(f"/research/exports/{export.public_id}/download")
    assert again.status_code == 200 and again.data == original
    # The change is real: a new export of the same data differs.
    newer = _export(client)
    renewed = client.get(f"/research/exports/{newer.public_id}/download").data
    assert renewed != original
    assert dict(zip(*_open(renewed)[1]["prompts.csv"][:2]))["rating"] == "4"


def test_a_timezone_change_never_changes_an_export(app, client, monkeypatch):
    _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    today = rw.utcnow().date()
    export = _export(client, period_from=(today - timedelta(days=1)).isoformat(),
                     period_to=(today + timedelta(days=1)).isoformat())
    original = client.get(f"/research/exports/{export.public_id}/download").data
    created_in = app.config["APP_TIMEZONE"]
    assert created_in != "Pacific/Kiritimati"
    app.config["APP_TIMEZONE"] = "Pacific/Kiritimati"   # UTC+14
    try:
        again = client.get(f"/research/exports/{export.public_id}/download")
        assert again.status_code == 200 and again.data == original
        detail = client.get(f"/research/exports/{export.public_id}").get_data(as_text=True)
        assert f"Session start period ({created_in})" in detail
        assert "Kiritimati" not in detail
        manifest = json.loads(_open(original)[0]["manifest.json"])
        assert manifest["filters"]["timezone"] == created_in
    finally:
        app.config["APP_TIMEZONE"] = created_in
    db.session.expire_all()
    stored = ResearchExport.query.filter_by(public_id=export.public_id).one()
    assert stored.timezone == created_in


def test_a_tampered_archive_is_refused_not_served(app, client, monkeypatch):
    _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    export = _export(client)
    from sqlalchemy import text

    db.session.execute(text("UPDATE research_export_archives SET content = :c"),
                       {"c": b"PK-not-the-archive"})
    db.session.commit()
    downloads = ResearchAuditEvent.query.filter_by(action="export_downloaded").count()
    response = client.get(f"/research/exports/{export.public_id}/download")
    assert response.status_code == 409
    body = response.get_data(as_text=True)
    assert 'data-archive-state="corrupt"' in body and "PK-not" not in body
    assert ResearchAuditEvent.query.filter_by(action="export_downloaded").count() == downloads


def test_an_export_too_large_to_store_is_refused_with_nothing_written(app, client,
                                                                      monkeypatch):
    _collected_world(app, monkeypatch)
    monkeypatch.setattr(exporter, "MAX_EXPORT_ARCHIVE_BYTES", 10)
    rw.login_researcher(client)
    response = client.post("/research/exports", data={})
    assert response.status_code in (200, 302)
    assert ResearchExport.query.count() == ResearchExportArchive.query.count() == 0


def test_exports_carry_no_identity_or_content(app, client, monkeypatch):
    people = _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    export = _export(client)
    data = client.get(f"/research/exports/{export.public_id}/download").data
    files, tables = _open(data)
    blob = b"".join(files.values()).decode()
    for secret in ("Sam Student", "s1@example.com", people["s1"].public_id, "HYPERLINK",
                   "researcher@example.com", "password", "user_id", "student_id"):
        assert secret not in blob, secret
    for name in ("sessions.csv", "events.csv", "prompts.csv"):
        assert "subject_code" in tables[name][0]
        for column in tables[name][0]:
            assert column not in ("user_id", "email", "name", "full_name"), (name, column)


def test_unlabeled_prompts_are_never_given_a_label(app, client, monkeypatch):
    people = rw.world(app, provenance_study=True)
    monkeypatch.setattr(sampling, "_draw", lambda: 0)
    student_client = app.test_client()
    rw.login(student_client, "s1@example.com")
    body = rw.observe(student_client, rw.Browser(), 150)[-1].get_json()
    prompt_id = body["prompt"]["id"]
    rw.post(student_client, {}, url=rw.prompt_url(prompt_id, "display"))
    rw.post(student_client, {"dismissed": True}, url=rw.prompt_url(prompt_id, "respond"))
    rw.login_researcher(client)
    export = _export(client)
    _files, tables = _open(client.get(f"/research/exports/{export.public_id}/download").data)
    row = dict(zip(tables["prompts.csv"][0], tables["prompts.csv"][1]))
    assert row["derived_state"] == "dismissed"
    assert row["rating"] == "" and row["cause_interface"] == "" and row["cause_unsure"] == ""
    assert people["config"].version_number == int(row["configuration_version"])


def test_development_and_demo_data_are_never_exported(app, client, monkeypatch):
    rw.world(app)  # development provenance
    monkeypatch.setattr(sampling, "_draw", lambda: 999)
    student_client = app.test_client()
    rw.login(student_client, "s1@example.com")
    rw.post(student_client, rw.batch([rw.Browser().event("page_view", detail="wide")]))
    rw.login_researcher(client)
    export = _export(client)
    assert (export.sessions_count, export.events_count) == (0, 0)


def test_csv_text_is_neutralised_against_formula_injection():
    assert exporter._safe("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    for dangerous in ("+1", "-1", "@SUM(A1)", "\tx", "\rx"):
        assert exporter._safe(dangerous).startswith("'")
    assert exporter._safe(-1250) == -1250 and exporter._safe("page_view") == "page_view"
    csv_bytes = exporter._csv(["a"], [["=1+1"], [-5]])
    assert csv_bytes.decode() == "a\n'=1+1\n-5\n"


def test_an_archive_removed_by_retention_is_gone_not_rebuilt(app, client, monkeypatch):
    _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    export = _export(client)
    # Forty days pass for the data and for the copy of it in the archive.
    forty_days = 40 * 24 * 60 * 60 * 1000
    rw.age_session(ResearchSession.query.first().id, forty_days)
    from sqlalchemy import text

    db.session.execute(text("UPDATE research_exports SET oldest_last_seen_ms ="
                            " oldest_last_seen_ms - :d"), {"d": forty_days})
    db.session.commit()
    report = operator.retention_report(30, execute=True)
    assert report.executed and report.sessions == 1 and report.archives == 1
    response = client.get(f"/research/exports/{export.public_id}/download")
    assert response.status_code == 410
    assert 'data-archive-state="expired"' in response.get_data(as_text=True)
    detail = client.get(f"/research/exports/{export.public_id}").get_data(as_text=True)
    assert "Download ZIP" not in detail and 'data-archive-state="expired"' in detail
    assert ResearchExport.query.count() == 1 and ResearchExportArchive.query.count() == 0
    assert ResearchAuditEvent.query.filter_by(action="export_downloaded").count() == 0


def test_export_filters_by_configuration_and_period(app, client, monkeypatch):
    people = _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    today = rw.utcnow().date()
    future = _export(client, period_from=(today + timedelta(days=5)).isoformat())
    assert future.sessions_count == 0
    scoped = _export(client, configuration=people["config"].public_id,
                     period_from=(today - timedelta(days=1)).isoformat(),
                     period_to=(today + timedelta(days=1)).isoformat())
    assert scoped.sessions_count == 1
    response = client.post("/research/exports", data={
        "period_from": today.isoformat(), "period_to": (today - timedelta(days=3)).isoformat()})
    assert response.status_code == 200
    assert "end date is before the start date" in response.get_data(as_text=True)


def test_the_audit_log_names_no_researcher(app, client, monkeypatch):
    _collected_world(app, monkeypatch)
    rw.login_researcher(client)
    _export(client)
    html = client.get("/research/activity-log").get_data(as_text=True)
    assert "export created" in html and "You" in html
    assert "Rhea Researcher" not in html and "Operator tool" in html


# ---------------------------------------------------------------------------
# Exclusions and the population rule in the workspace
# ---------------------------------------------------------------------------


def test_the_configuration_form_has_no_scope_switch(app, client, researcher):
    rw.login_researcher(client)
    html = client.get("/research/configurations/new").get_data(as_text=True)
    assert "scope_" not in html and "Observed workflow areas" not in html
    assert "every eligible Student account" in " ".join(html.split())
    config = _create(client)
    detail = " ".join(client.get(f"/research/configurations/{config.public_id}")
                      .get_data(as_text=True).split())
    assert "Observe:" not in detail and "operator included" not in detail
    assert "Every Student page and outcome in the event dictionary" in detail


def test_excluded_students_are_listed_by_code_only(app, client):
    people = rw.world(app)
    student = rw.user("s3@example.com", rw.STUDENT, full_name="Sol Student")
    operator.exclude("s3@example.com")
    from sqlalchemy import text

    db.session.execute(text("UPDATE research_subjects SET status_basis ="
                            " 'legacy_collection_exclusion' WHERE id = :id"),
                       {"id": rw.subject_of(student).id})
    db.session.commit()
    rw.login_researcher(client)
    html = client.get("/research/exclusions").get_data(as_text=True)
    for account in (people["excluded"], student):
        assert f'data-excluded-subject="{rw.subject_of(account).subject_code}"' in html
    assert 'data-basis="legacy_collection_exclusion"' in html
    assert "Carried over from the retired consent records" in html
    for secret in ("excluded@example.com", "Ezra Excluded", "s3@example.com", "Sol Student",
                   people["excluded"].public_id, student.public_id):
        assert secret not in html
    assert 'href="/research/exclusions"' in html   # in the workspace navigation


def test_a_reinstated_student_leaves_the_exclusion_list(app, client):
    people = rw.world(app)
    code = rw.subject_of(people["excluded"]).subject_code
    rw.login_researcher(client)
    assert code in client.get("/research/exclusions").get_data(as_text=True)
    operator.reinstate("excluded@example.com")
    html = client.get("/research/exclusions").get_data(as_text=True)
    assert code not in html and "No exclusions" in html
    dashboard = client.get("/research/dashboard").get_data(as_text=True)
    assert re.search(r'data-subjects="reinstated"><div class="stat-card__value">1<', dashboard)


def test_the_exclusion_list_is_for_researchers_only(app, client):
    rw.world(app)
    for email in ("s1@example.com", "teacher@example.com", "admin@example.com"):
        rw.login(client, email)
        assert client.get("/research/exclusions").status_code in (302, 403)
