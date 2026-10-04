"""The natural-use research data model (Phase 6 replacement): database
constraints as the final defense, ORM guards, and the documentation contract.
"""

import pathlib
import re
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

import tests.research_world as rw
from app.extensions import db
from app.models import (
    ResearchAuditEvent,
    ResearchConfiguration,
    ResearchDataError,
    ResearchEvent,
    ResearchExport,
    ResearchExportArchive,
    ResearchFeedbackPrompt,
    ResearchSession,
    ResearchSubject,
    ResearchSubjectLink,
    generate_subject_code,
    now_ms,
)
from app.services.research_event_dictionary import (
    CLIENT_OBSERVED_POSTS,
    DETAIL_CODE_MAX_LENGTH,
    ELEMENT_ID_MAX_LENGTH,
    ELEMENT_IDS,
    EVENT_SPECS,
    EVENT_TYPE_MAX_LENGTH,
    EVENT_TYPES,
    PAGE_AREAS,
    PAGE_ID_ALIASES,
    PAGE_ID_MAX_LENGTH,
    PAGE_IDS,
    TECHNICAL_EXCLUSIONS,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESEARCH_TABLES = (
    "research_subjects", "research_subject_links", "research_configurations",
    "research_sessions", "research_events", "research_feedback_prompts", "research_exports",
    "research_export_archives", "research_audit_events",
)


def _refused(statement, params=None):
    with pytest.raises(IntegrityError):
        db.session.execute(sa.text(statement), params or {})
        db.session.commit()
    db.session.rollback()


@pytest.fixture
def base(app):
    """The shared world after s1 was collected once: an excluded subject
    (recorded by the operator) and s1's automatic subject; s2 has none."""
    people = rw.world(app)
    rw.provision(app.test_client())
    return people


# ---------------------------------------------------------------------------
# The contract in documentation and code agree
# ---------------------------------------------------------------------------


def test_the_contract_document_names_every_event_type_and_page():
    text = (ROOT / "docs" / "PHASE6_NATURAL_USE_RESEARCH.md").read_text(encoding="utf-8")
    for event_type in EVENT_TYPES:
        assert f"`{event_type}`" in text, event_type
    for page in PAGE_IDS:
        assert f"`{page}`" in text, page
    for endpoint in list(TECHNICAL_EXCLUSIONS) + list(CLIENT_OBSERVED_POSTS):
        assert f"`{endpoint}`" in text, endpoint
    assert "student.usage_research" not in text
    flat = " ".join(text.split())
    assert "Thinking about the last two minutes on this platform, how frustrated did you" \
        " feel?" in flat
    assert "Answering is optional. Skipping does not affect your grades or your access." in flat


def test_the_runbook_has_no_inclusion_step_and_marks_demo_accounts_first():
    text = " ".join((ROOT / "docs" / "RESEARCH_DEPLOYMENT_RUNBOOK.md")
                    .read_text(encoding="utf-8").split())
    assert "research_operator.py include" not in text
    assert "--confirm-external-arrangement" not in text
    assert "mark-demo EMAIL" in text and "list-excluded" in text
    assert text.index("mark-demo EMAIL") < text.index("Start collecting**. From then on")
    assert "nine `research_*` tables" in text and "--sql" in text


def test_the_dictionary_widths_fit_their_columns():
    assert all(len(name) <= EVENT_TYPE_MAX_LENGTH for name in EVENT_TYPES)
    assert all(len(page) <= PAGE_ID_MAX_LENGTH for page in PAGE_IDS)
    assert all(len(element) <= ELEMENT_ID_MAX_LENGTH for element in ELEMENT_IDS)
    assert all(len(code) <= DETAIL_CODE_MAX_LENGTH
               for spec in EVENT_SPECS.values() for code in spec.details)
    assert set(PAGE_AREAS) == set(PAGE_IDS)
    assert set(PAGE_ID_ALIASES.values()) <= set(PAGE_IDS)


def test_every_page_id_is_a_real_student_page_endpoint(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert set(PAGE_IDS) <= endpoints
    assert set(PAGE_ID_ALIASES) <= endpoints


def test_every_declared_research_id_in_the_templates_is_in_the_dictionary():
    declared = set()
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        declared |= set(re.findall(r'data-research-id="([a-z_]+)"', text))
        declared |= set(re.findall(r'"data-research-id": "([a-z_]+)"', text))
        declared |= set(re.findall(r"data-research-form=\"([a-z_]+)\"", text))
        declared |= set(re.findall(r"'(lesson_(?:undo_)?complete)'", text))
        declared |= set(re.findall(r'data-research-media="([a-z_]+)"', text))
    assert declared, "no instrumented control found"
    assert declared <= set(ELEMENT_IDS), declared - set(ELEMENT_IDS)


def test_every_identifier_fits_mysql(app):
    for table_name in RESEARCH_TABLES:
        table = db.metadata.tables[table_name]
        names = [table_name] + [c.name for c in table.constraints if c.name] + \
            [i.name for i in table.indexes] + [c.name for c in table.columns]
        for name in names:
            assert len(name) <= 64, name


def test_the_subject_code_is_random_and_never_derived():
    codes = {generate_subject_code() for _ in range(200)}
    assert len(codes) == 200
    assert all(re.fullmatch(r"RS-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{10}", c) for c in codes)


# ---------------------------------------------------------------------------
# Constraints: the database is the final defense
# ---------------------------------------------------------------------------


def test_subject_constraints(app, base):
    subject = rw.subject_of(base["s1"])
    excluded = rw.subject_of(base["excluded"])
    assert (subject.collection_status, excluded.collection_status) == ("included", "excluded")
    # Each status only with a basis that truthfully explains it.
    for basis in ("external_exclusion", "legacy_collection_exclusion", "external_arrangement",
                  "consent", "invited"):
        _refused("UPDATE research_subjects SET status_basis = :b WHERE id = :id",
                 {"b": basis, "id": subject.id})
    for basis in ("population_rule", "operator_reinstatement", "external_arrangement"):
        _refused("UPDATE research_subjects SET status_basis = :b WHERE id = :id",
                 {"b": basis, "id": excluded.id})
    for basis in ("population_rule", "operator_reinstatement"):
        db.session.execute(sa.text("UPDATE research_subjects SET status_basis = :b"
                                   " WHERE id = :id"), {"b": basis, "id": subject.id})
        db.session.commit()
    _refused("UPDATE research_subjects SET collection_status = 'maybe' WHERE id = :id",
             {"id": subject.id})
    _refused("UPDATE research_subjects SET subject_code = 'RS-SHORT' WHERE id = :id",
             {"id": subject.id})
    _refused("UPDATE research_subjects SET provenance = 'development' WHERE id = :id",
             {"id": subject.id})
    other = ResearchSubject.query.filter(ResearchSubject.id != subject.id).first()
    _refused("UPDATE research_subjects SET subject_code = :c WHERE id = :id",
             {"c": subject.subject_code, "id": other.id})


def test_one_subject_per_account_and_one_account_per_subject(app, base):
    link = ResearchSubjectLink.query.first()
    other = ResearchSubjectLink.query.filter(ResearchSubjectLink.id != link.id).first()
    _refused("INSERT INTO research_subject_links (subject_id, user_id, created_at)"
             " VALUES (:s, :u, '2026-01-01 00:00:00')", {"s": link.subject_id, "u": base["s2"].id})
    _refused("INSERT INTO research_subject_links (subject_id, user_id, created_at)"
             " VALUES (:s, :u, '2026-01-01 00:00:00')", {"s": other.subject_id, "u": link.user_id})
    _refused("DELETE FROM users WHERE id = :u", {"u": link.user_id})


def test_configuration_constraints(app, base):
    config = base["config"]
    _refused("UPDATE research_configurations SET is_collecting = 1, status = 'draft',"
             " current_marker = NULL, activated_at = NULL, activated_by_id = NULL,"
             " retention_days = NULL WHERE id = :id", {"id": config.id})
    _refused("UPDATE research_configurations SET current_marker = NULL WHERE id = :id",
             {"id": config.id})
    _refused("UPDATE research_configurations SET retention_days = NULL WHERE id = :id",
             {"id": config.id})
    _refused("UPDATE research_configurations SET lookback_seconds = 29 WHERE id = :id",
             {"id": config.id})
    _refused("UPDATE research_configurations SET min_observed_seconds = 121 WHERE id = :id",
             {"id": config.id})
    _refused("UPDATE research_configurations SET collection_ends_at = collection_starts_at"
             " WHERE id = :id", {"id": config.id})
    _refused("UPDATE research_configurations SET event_schema_version = 'v0' WHERE id = :id",
             {"id": config.id})


def test_at_most_one_configuration_is_active(app, base):
    with pytest.raises(IntegrityError):
        rw.configuration(base["researcher"], collecting=False)
    db.session.rollback()


def test_session_and_event_constraints(app, base):
    session = ResearchSession.query.one()
    _refused("UPDATE research_sessions SET ended_at_ms = started_at_ms WHERE id = :id",
             {"id": session.id})  # an end without a reason
    _refused("UPDATE research_sessions SET provenance = 'real' WHERE id = :id", {"id": session.id})
    _refused("UPDATE research_sessions SET events_invalid = -1 WHERE id = :id", {"id": session.id})
    _refused("UPDATE research_sessions SET observed_since_ms = 5, last_observed_at_ms = NULL"
             " WHERE id = :id", {"id": session.id})
    event = ResearchEvent.query.one()
    _refused("UPDATE research_events SET event_type = 'search' WHERE id = :id", {"id": event.id})
    _refused("UPDATE research_events SET tab_ref = NULL WHERE id = :id", {"id": event.id})
    _refused("UPDATE research_events SET event_type = 'keystroke' WHERE id = :id",
             {"id": event.id})
    _refused("INSERT INTO research_events (event_uid, session_id, source, event_type,"
             " occurred_at_ms, received_at_ms) VALUES (:u, :s, 'server', 'search', 1, 1)"
             "", {"u": event.event_uid, "s": session.id})
    _refused("INSERT INTO research_events (event_uid, session_id, source, event_type, tab_ref,"
             " occurred_at_ms, received_at_ms) VALUES (:u, :s, 'server', 'search', 'x', 1, 1)",
             {"u": str(uuid.uuid4()), "s": session.id})


def _prompt(session, **values):
    prompt = ResearchFeedbackPrompt(session_id=session.id,
                                    configuration_id=session.configuration_id,
                                    sampling_reason="random", offered_at_ms=now_ms(), **values)
    db.session.add(prompt)
    db.session.commit()
    return prompt


def test_prompt_lifecycle_constraints(app, base):
    session = ResearchSession.query.one()
    prompt = _prompt(session)
    _refused("UPDATE research_feedback_prompts SET status = 'answered', rating = 3"
             " WHERE id = :id", {"id": prompt.id})  # answered without a display
    _refused("UPDATE research_feedback_prompts SET cause_other = 1 WHERE id = :id",
             {"id": prompt.id})  # a cause without an answer
    _refused("UPDATE research_feedback_prompts SET deferral_count = 1 WHERE id = :id",
             {"id": prompt.id})  # a deferral without its reason
    now = now_ms()
    db.session.execute(sa.text(
        "UPDATE research_feedback_prompts SET status = 'displayed', displayed_at_ms = :d,"
        " display_slot = 1, prompt_day = '2026-09-29', window_start_ms = :s,"
        " window_end_ms = :d, observed_ms = 120000 WHERE id = :id"),
        {"d": now, "s": now - 120_000, "id": prompt.id})
    db.session.commit()
    _refused("UPDATE research_feedback_prompts SET observed_ms = 120001 WHERE id = :id",
             {"id": prompt.id})
    _refused("UPDATE research_feedback_prompts SET window_end_ms = window_end_ms + 1"
             " WHERE id = :id", {"id": prompt.id})
    _refused("UPDATE research_feedback_prompts SET status = 'answered', rating = 6,"
             " responded_at_ms = :d WHERE id = :id", {"d": now, "id": prompt.id})
    second = _prompt(session)
    _refused("UPDATE research_feedback_prompts SET status = 'displayed', displayed_at_ms = :d,"
             " display_slot = 1, prompt_day = '2026-09-29', window_start_ms = :s,"
             " window_end_ms = :d, observed_ms = 0 WHERE id = :id",
             {"d": now, "s": now - 120_000, "id": second.id})  # the same display slot


def test_audit_constraints(app, base):
    _refused("INSERT INTO research_audit_events (action, channel, occurred_at) VALUES"
             " ('export_created', 'workspace', '2026-01-01 00:00:00')")  # no actor
    _refused("INSERT INTO research_audit_events (action, channel, occurred_at) VALUES"
             " ('consent_given', 'operator', '2026-01-01 00:00:00')")


# ---------------------------------------------------------------------------
# ORM guards
# ---------------------------------------------------------------------------


def test_identity_and_history_guards(app, base):
    subject = ResearchSubject.query.first()
    subject.subject_code = generate_subject_code()
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    db.session.delete(ResearchSubject.query.first())
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    link = ResearchSubjectLink.query.first()
    link.user_id = base["s2"].id
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    event = ResearchEvent.query.one()
    event.detail_code = "narrow"
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    session = ResearchSession.query.one()
    session.subject_id = ResearchSubject.query.filter(
        ResearchSubject.id != session.subject_id).first().id
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    audit = ResearchAuditEvent.query.first()
    audit.detail_code = "rewritten"
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()


def test_an_active_configuration_may_change_only_its_operational_state(app, base):
    config = db.session.get(ResearchConfiguration, base["config"].id)
    config.is_collecting = False
    config.version += 1
    db.session.commit()
    config.lookback_seconds = 60
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    config.version_number = 99
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()


def test_an_answered_prompt_is_final(app, base):
    session = ResearchSession.query.one()
    prompt = _prompt(session)
    now = now_ms()
    db.session.execute(sa.text(
        "UPDATE research_feedback_prompts SET status = 'answered', displayed_at_ms = :d,"
        " display_slot = 1, prompt_day = '2026-09-29', window_start_ms = :s,"
        " window_end_ms = :d, observed_ms = 1, responded_at_ms = :d, rating = 2"
        " WHERE id = :id"), {"d": now, "s": now - 120_000, "id": prompt.id})
    db.session.commit()
    db.session.expire_all()
    prompt = db.session.get(ResearchFeedbackPrompt, prompt.id)
    prompt.late_response = True
    db.session.commit()
    prompt.rating = 5
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()


def test_export_descriptions_are_fixed(app, base):
    export = ResearchExport(event_schema_version=rw.SCHEMA, cutoff_ms=1, subjects_count=0,
                            sessions_count=0, events_count=0, prompts_count=0,
                            timezone="UTC", manifest_digest="a" * 64,
                            created_by_id=base["researcher"].id)
    db.session.add(export)
    db.session.commit()
    export.timezone = "Asia/Riyadh"
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    export.events_count = 5
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    db.session.delete(db.session.get(ResearchExport, export.id))
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()


def _export_row(base, **values):
    row = dict(event_schema_version=rw.SCHEMA, cutoff_ms=1, subjects_count=0, sessions_count=0,
               events_count=0, prompts_count=0, timezone="UTC", manifest_digest="a" * 64,
               created_by_id=base["researcher"].id)
    row.update(values)
    export = ResearchExport(**row)
    db.session.add(export)
    db.session.commit()
    return export


def test_export_and_archive_constraints(app, base):
    export = _export_row(base)
    _refused("UPDATE research_exports SET timezone = '' WHERE id = :id", {"id": export.id})
    _refused("UPDATE research_exports SET sessions_count = 1 WHERE id = :id",
             {"id": export.id})  # sessions without their oldest moment
    _refused("UPDATE research_exports SET oldest_last_seen_ms = 5 WHERE id = :id",
             {"id": export.id})  # an oldest moment without sessions
    params = {"e": export.id, "c": b"PK", "now": "2026-01-01 00:00:00"}
    _refused("INSERT INTO research_export_archives (export_id, archive_sha256, byte_size,"
             " content, created_at) VALUES (:e, 'short', 2, :c, :now)", params)
    _refused("INSERT INTO research_export_archives (export_id, archive_sha256, byte_size,"
             " content, created_at) VALUES (:e, :d, 0, :c, :now)", dict(params, d="b" * 64))
    _refused("INSERT INTO research_export_archives (export_id, archive_sha256, byte_size,"
             " content, created_at) VALUES (:e, :d, 16777217, :c, :now)",
             dict(params, d="b" * 64))
    db.session.execute(sa.text(
        "INSERT INTO research_export_archives (export_id, archive_sha256, byte_size, content,"
        " created_at) VALUES (:e, :d, 2, :c, :now)"), dict(params, d="b" * 64))
    db.session.commit()
    _refused("INSERT INTO research_export_archives (export_id, archive_sha256, byte_size,"
             " content, created_at) VALUES (:e, :d, 2, :c, :now)", dict(params, d="c" * 64))
    archive = ResearchExportArchive.query.one()
    archive.content = b"PK-rewritten"
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    db.session.delete(ResearchExportArchive.query.one())
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
    assert ResearchExportArchive.query.one().content == b"PK"


def test_a_subjects_provenance_may_change_but_not_its_identity(app, base):
    subject = rw.subject_of(base["s1"])
    subject.provenance = "demo"
    db.session.commit()
    subject.public_id = str(uuid.uuid4())
    with pytest.raises(ResearchDataError):
        db.session.commit()
    db.session.rollback()
