"""The time contract of research exports (Phase 6 final data-integrity
correction).

``research_exports`` reads every file inside one read transaction whose
snapshot is established by its first read, and reads ``cutoff_ms`` only
after that. So every row and value in an archive -- mutable ones included --
is the committed state of one snapshot, everything in it was saved before the
cutoff, and nothing in it is dated after the cutoff. A stored server moment
later than the cutoff (only possible with a server clock running ahead)
refuses the export instead of being clamped or filtered.

1. **Concurrency.** A file SQLite database in WAL mode, so a second
   connection can commit while the export's snapshot is open, as on InnoDB.
   Between the cutoff and the end of the archive, another thread answers the
   displayed prompt, stores a new event and ends the session. None of it
   enters the archive or its counts, the manifest states the contract, and
   the next export contains all of it.
2. **Clock.** A session, event or prompt moment stored later than the
   cutoff refuses the export with nothing stored; once the cutoff has
   passed it, the export includes it unchanged.
3. **Derived states** count a display or a response only from its own
   moment.

SQLite in WAL mode stands in for InnoDB's REPEATABLE READ read view here;
neither MySQL's isolation nor its execution plans are tested.
"""

import csv
import io
import json
import threading
import time
import zipfile

import pytest
from sqlalchemy import text

import tests.research_world as rw
from tests.research_world import fresh_identity_per_request  # noqa: F401
from app import create_app
from app.extensions import db
from app.models import (
    ResearchAuditEvent,
    ResearchEvent,
    ResearchExport,
    ResearchExportArchive,
    ResearchFeedbackPrompt,
    ResearchSession,
    now_ms,
)
from app.services import research_collection as collection
from app.services import research_event_validation as validation
from app.services import research_exports as exporter
from app.services import research_sampling as sampling
from app.services.research_workspace_queries import derived_prompt_state


@pytest.fixture(autouse=True)
def _fresh_identity(fresh_identity_per_request):
    """Every request re-reads its own client's login."""


def _open(data):
    archive = zipfile.ZipFile(io.BytesIO(data))
    files = {name: archive.read(name) for name in archive.namelist()}
    tables = {name: [dict(zip(rows[0], row)) for row in rows[1:]]
              for name, rows in ((name, list(csv.reader(io.StringIO(files[name].decode()))))
                                 for name in files if name.endswith(".csv"))}
    return json.loads(files["manifest.json"]), tables


def _archive(export_public_id):
    export = ResearchExport.query.filter_by(public_id=export_public_id).one()
    archive = ResearchExportArchive.query.filter_by(export_id=export.id).one()
    return export, bytes(archive.content)


def _displayed_prompt(app, monkeypatch):
    """s1 observed long enough, offered and shown one prompt (not answered).
    Returns the people, the student's client, browser, prompt id and the
    browser's session reference."""
    people = rw.world(app, provenance_study=True)
    monkeypatch.setattr(sampling, "_draw", lambda: 0)
    student = app.test_client()
    rw.login(student, "s1@example.com")
    browser = rw.Browser()
    prompt_id = rw.observe(student, browser, 150)[-1].get_json()["prompt"]["id"]
    assert rw.post(student, {}, url=rw.prompt_url(prompt_id, "display")).get_json()["granted"]
    with student.session_transaction() as flask_session:
        ref = flask_session["research_session_ref"]
    return people, student, browser, prompt_id, ref


# ---------------------------------------------------------------------------
# 1. Concurrency: writes after the cutoff never enter the archive
# ---------------------------------------------------------------------------


@pytest.fixture
def wal_app(tmp_path):
    database = tmp_path / "snapshot.db"
    flask_app = create_app("testing", SQLALCHEMY_DATABASE_URI=f"sqlite:///{database.as_posix()}")
    with flask_app.app_context():
        with db.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar() == "wal"
        db.create_all()
        yield flask_app
        db.session.remove()
        db.engine.dispose()


def test_writes_after_the_cutoff_never_enter_the_archive(wal_app, monkeypatch):
    app = wal_app
    people, _student, browser, prompt_id, ref = _displayed_prompt(app, monkeypatch)
    user_id = people["s1"].id
    session = ResearchSession.query.one()
    before = (session.last_seen_at_ms, session.batches_received, session.events_accepted)
    events_before = ResearchEvent.query.count()
    writes = {}

    def concurrent_writer():
        """Another request, on its own connection, after the cutoff."""
        with app.app_context():
            time.sleep(0.02)          # strictly after the cutoff, on the real clock
            moment = now_ms()
            writes["moment"] = moment
            writes["respond"] = sampling.respond(
                user_id, ref, prompt_id, sampling.Answer(False, 5, ("content",)), moment=moment)
            event = browser.event("control_click", t=moment, element="nav_quizzes")
            writes["event_uid"] = event["id"]
            batch = validation.parse_batch(
                json.dumps(rw.batch([event], sent_at=moment)).encode())[0]
            writes["ingest"] = collection.ingest_batch(
                user_id, ref, batch, "study", "UTC").status
            collection.end_session_on_logout(user_id, ref)

    original_sessions = exporter._sessions

    def sessions_then_concurrent_writes(*args, **kwargs):
        rows = original_sessions(*args, **kwargs)
        thread = threading.Thread(target=concurrent_writer)
        thread.start()
        thread.join(timeout=60)
        assert not thread.is_alive()
        return rows

    monkeypatch.setattr(exporter, "_sessions", sessions_then_concurrent_writes)
    status, public_id = exporter.create_export(people["researcher"].id, None, None, None, "UTC")
    monkeypatch.setattr(exporter, "_sessions", original_sessions)

    # The concurrent writes really happened, during the export.
    assert status == exporter.CREATED
    assert writes["respond"] == sampling.ANSWERED and writes["ingest"] == collection.RECORDED
    db.session.expire_all()
    export, data = _archive(public_id)
    assert export.cutoff_ms < writes["moment"]

    manifest, tables = _open(data)
    assert manifest["cutoff_ms"] == export.cutoff_ms and "as_of_ms" not in manifest
    assert manifest["time_contract"] == exporter.TIME_CONTRACT
    # The answer committed after the cutoff is absent: the prompt is what it
    # was in the snapshot, evaluated at the cutoff.
    (prompt,) = tables["prompts.csv"]
    assert (prompt["stored_status"], prompt["derived_state"], prompt["rating"],
            prompt["responded_at_ms"], prompt["cause_content"]) == (
        "displayed", "awaiting_response", "", "", "")
    # The session update committed after the cutoff is absent too: no end,
    # and last activity and every counter as they were.
    (row,) = tables["sessions.csv"]
    assert (row["ended_at_ms"], row["end_reason"]) == ("", "")
    assert (int(row["last_seen_at_ms"]), int(row["batches_received"]),
            int(row["events_accepted"])) == before
    assert writes["event_uid"] not in {e["event_uid"] for e in tables["events.csv"]}
    assert len(tables["events.csv"]) == events_before == manifest["counts"]["events"]
    # Nothing in the archive is dated after its cutoff.
    moments = [int(row[c]) for row in tables["sessions.csv"]
               for c in ("started_at_ms", "last_seen_at_ms") if row[c]]
    moments += [int(e["received_at_ms"]) for e in tables["events.csv"]]
    moments += [int(p[c]) for p in tables["prompts.csv"]
                for c in ("offered_at_ms", "displayed_at_ms", "responded_at_ms") if p[c]]
    assert moments and max(moments) <= export.cutoff_ms

    # The database moved on, and the next export -- a new id -- shows it.
    stored = ResearchFeedbackPrompt.query.one()
    assert (stored.status, stored.rating) == ("answered", 5)
    assert ResearchSession.query.one().end_reason == "logout"
    status, newer_id = exporter.create_export(people["researcher"].id, None, None, None, "UTC")
    assert status == exporter.CREATED and newer_id != public_id
    _newer, newer_data = _archive(newer_id)
    _manifest, newer_tables = _open(newer_data)
    (prompt,) = newer_tables["prompts.csv"]
    assert (prompt["derived_state"], prompt["rating"], prompt["cause_content"]) == (
        "answered", "5", "1")
    assert newer_tables["sessions.csv"][0]["end_reason"] == "logout"
    assert writes["event_uid"] in {e["event_uid"] for e in newer_tables["events.csv"]}
    # The first id still serves its own bytes.
    assert _archive(public_id)[1] == data


# ---------------------------------------------------------------------------
# 2. Clock: a stored moment after the cutoff refuses the export
# ---------------------------------------------------------------------------


_AHEAD_MS = 60_000


@pytest.mark.parametrize("table, column", [
    ("research_sessions", "last_seen_at_ms"),
    ("research_events", "received_at_ms"),
    ("research_feedback_prompts", "responded_at_ms"),
])
def test_a_moment_later_than_the_cutoff_refuses_the_export(app, monkeypatch, table, column):
    people, student, _browser, prompt_id, _ref = _displayed_prompt(app, monkeypatch)
    assert rw.post(student, {"rating": 2}, url=rw.prompt_url(prompt_id, "respond")) \
        .get_json()["recorded"] is True
    # A server whose clock runs a minute ahead stored this moment.
    ahead = now_ms() + _AHEAD_MS
    db.session.execute(text(
        f"UPDATE {table} SET {column} = :m WHERE id = (SELECT MAX(id) FROM {table})"),
        {"m": ahead})
    db.session.commit()
    audits = ResearchAuditEvent.query.count()

    status, public_id = exporter.create_export(people["researcher"].id, None, None, None, "UTC")
    assert (status, public_id) == (exporter.CLOCK_AHEAD, None)
    assert ResearchExport.query.count() == ResearchExportArchive.query.count() == 0
    assert ResearchAuditEvent.query.count() == audits

    # Once this server's clock has passed that moment, it is exported as stored.
    monkeypatch.setattr(exporter, "now_ms", lambda: ahead + 1000)
    status, public_id = exporter.create_export(people["researcher"].id, None, None, None, "UTC")
    assert status == exporter.CREATED
    export, data = _archive(public_id)
    manifest, tables = _open(data)
    assert export.cutoff_ms == manifest["cutoff_ms"] == ahead + 1000
    exported = {
        "research_sessions": [int(r["last_seen_at_ms"]) for r in tables["sessions.csv"]],
        "research_events": [int(r["received_at_ms"]) for r in tables["events.csv"]],
        "research_feedback_prompts": [int(r["responded_at_ms"]) for r in tables["prompts.csv"]],
    }[table]
    assert ahead in exported


def test_the_workspace_says_why_an_export_was_refused(app, client, monkeypatch):
    rw.world(app, provenance_study=True)
    rw.provision(app.test_client())
    db.session.execute(text("UPDATE research_sessions SET last_seen_at_ms = :m"),
                       {"m": now_ms() + _AHEAD_MS})
    db.session.commit()
    rw.login_researcher(client)
    response = client.post("/research/exports", data={}, follow_redirects=True)
    body = " ".join(response.get_data(as_text=True).split())
    assert "carries a time later than this server&#39;s clock" in body
    assert "nothing was exported" in body
    assert ResearchExport.query.count() == 0


# ---------------------------------------------------------------------------
# 3. Derived states use each moment, never only the stored status
# ---------------------------------------------------------------------------


def test_a_derived_state_counts_a_display_or_response_only_from_its_moment():
    offered, displayed, responded = 1_000, 3_000, 5_000
    window = ttl = 600

    def state(status, at, responded_at=responded):
        return derived_prompt_state(status, offered, displayed, ttl, window, at, responded_at)

    assert state("answered", 6_000) == "answered"
    assert state("dismissed", 6_000) == "dismissed"
    assert state("answered", 5_000) == "answered"
    assert state("answered", 4_999) == "awaiting_response"
    assert state("dismissed", 2_000) == "awaiting_display"
    assert state("answered", 3_000 + 600_001, responded_at=3_000 + 700_000) == "no_response"
    assert state("answered", 6_000, responded_at=None) == "awaiting_response"
    assert state("displayed", 3_000 + 600_001, responded_at=None) == "no_response"
    assert state("displayed", 3_000 + 600_000, responded_at=None) == "awaiting_response"
    assert derived_prompt_state("offered", offered, None, ttl, window, 1_000 + 600_001) == \
        "offer_expired"
