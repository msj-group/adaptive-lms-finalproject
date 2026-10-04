"""The operator tool outside every portal (Phase 6 replacement, corrected):
exclusions and reinstatements, demonstration accounts, identity recovery,
retention, and deliberate Researcher provisioning.

Collection itself needs no operator step (the population rule); the tool
records only the exceptions, and none of them is consent.
"""

import io
import sys
import types

import pytest
from sqlalchemy import text

import tests.research_world as rw
from app.extensions import db
from app.models import (
    ResearchAuditEvent,
    ResearchEvent,
    ResearchExport,
    ResearchExportArchive,
    ResearchFeedbackPrompt,
    ResearchSession,
    ResearchSubject,
    ResearchSubjectLink,
    UserStatus,
)
from app.services import research_exports as exporter
from app.services import research_operator as operator
from app.services.research_settings import (
    ResearchSettingsError,
    parse_allowlist,
    parse_retention_days,
    resolve_research_settings,
)
from scripts import research_operator as script


def _run(app, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = script.main(list(argv), app=app, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _legacy(subject_id):
    db.session.execute(text("UPDATE research_subjects SET status_basis ="
                            " 'legacy_collection_exclusion' WHERE id = :id"), {"id": subject_id})
    db.session.commit()
    db.session.expire_all()


def test_there_is_no_include_operation_any_more():
    assert not hasattr(operator, "include") and not hasattr(operator, "INCLUDED")
    with pytest.raises(SystemExit):
        script._parser().parse_args(["include", "s@example.com"])


def test_an_exclusion_before_any_collection_creates_one_excluded_subject(app):
    student = rw.user("s@example.com", rw.STUDENT)
    status, code = operator.exclude("S@Example.com ")
    assert status == operator.EXCLUDED and code.startswith("RS-") and len(code) == 13
    subject = rw.subject_of(student)
    assert (subject.collection_status, subject.status_basis, subject.provenance) == (
        "excluded", "external_exclusion", "study")
    audit = ResearchAuditEvent.query.one()
    assert (audit.action, audit.channel, audit.subject_id, audit.actor_id,
            audit.detail_code, audit.count_value) == (
        "subject_excluded", "operator", subject.id, None, "external_exclusion", 0)
    assert operator.exclude("s@example.com") == (operator.UNCHANGED, code)
    assert ResearchSubject.query.count() == ResearchSubjectLink.query.count() == 1


def test_an_exclusion_after_automatic_collection_keeps_the_same_subject(app):
    rw.world(app)
    client = app.test_client()
    subject = rw.provision(client)
    assert operator.exclude("s1@example.com") == (operator.EXCLUDED, subject.subject_code)
    db.session.expire_all()
    again = rw.subject_of(rw.user_by_email("s1@example.com"))
    assert again.id == subject.id and again.collection_status == "excluded"
    assert ResearchSession.query.one().end_reason == "subject_ineligible"
    audit = ResearchAuditEvent.query.filter_by(action="subject_excluded",
                                               subject_id=subject.id).one()
    assert audit.count_value == 1  # the open session it closed


@pytest.mark.parametrize("role", [rw.TEACHER, rw.ADMIN, rw.RESEARCHER])
def test_only_student_accounts_are_handled(app, role):
    rw.user("x@example.com", role)
    for action in (operator.exclude, operator.reinstate, operator.mark_demo):
        assert action("x@example.com") == (operator.NOT_A_STUDENT, None)
    assert ResearchSubject.query.count() == 0


def test_a_suspended_student_can_be_excluded(app):
    rw.user("gone@example.com", rw.STUDENT, status=UserStatus.SUSPENDED.value)
    assert operator.exclude("gone@example.com")[0] == operator.EXCLUDED


def test_an_unknown_address_is_never_created_or_matched_by_domain(app):
    rw.user("s@example.com", rw.STUDENT)
    for action in (operator.exclude, operator.reinstate, operator.mark_demo):
        assert action("new@example.com") == (operator.NO_ACCOUNT, None)
        assert action("example.com") == (operator.NO_ACCOUNT, None)
    assert ResearchSubject.query.count() == 0


def test_reinstatement_lifts_an_exclusion_with_a_truthful_basis(app):
    student = rw.user("s@example.com", rw.STUDENT)
    assert operator.reinstate("s@example.com") == (operator.NO_SUBJECT, None)
    _status, code = operator.exclude("s@example.com")
    assert operator.reinstate("s@example.com") == (operator.REINSTATED, code)
    db.session.expire_all()
    subject = rw.subject_of(student)
    assert (subject.collection_status, subject.status_basis) == (
        "included", "operator_reinstatement")
    audit = ResearchAuditEvent.query.filter_by(action="subject_reinstated").one()
    assert (audit.channel, audit.detail_code) == ("operator", "lifted:external_exclusion")
    assert operator.reinstate("s@example.com") == (operator.NOT_EXCLUDED, code)


def test_a_reinstated_student_is_collected_again(app):
    rw.world(app)
    client = app.test_client()
    operator.reinstate("excluded@example.com")
    subject = rw.provision(client, "excluded@example.com")
    assert subject.status_basis == "operator_reinstatement"
    assert rw.sessions_of(subject)[0].events_accepted == 1


def test_a_legacy_exclusion_is_lifted_only_deliberately(app):
    student = rw.user("s@example.com", rw.STUDENT)
    operator.exclude("s@example.com")
    _legacy(rw.subject_of(student).id)
    assert operator.reinstate("s@example.com")[0] == operator.LEGACY_EXCLUSION
    db.session.expire_all()
    assert rw.subject_of(student).collection_status == "excluded"
    assert operator.reinstate("s@example.com", allow_legacy_override=True)[0] == \
        operator.REINSTATED
    audit = ResearchAuditEvent.query.filter_by(action="subject_reinstated").one()
    assert audit.detail_code == "lifted:legacy_collection_exclusion"


def test_mark_demo_before_and_after_collection(app):
    fresh = rw.user("demo@example.com", rw.STUDENT)
    status, code = operator.mark_demo("demo@example.com")
    assert status == operator.MARKED_DEMO
    subject = rw.subject_of(fresh)
    assert (subject.collection_status, subject.status_basis, subject.provenance) == (
        "included", "population_rule", "demo")
    assert operator.mark_demo("demo@example.com") == (operator.UNCHANGED, code)
    rw.world(app)
    client = app.test_client()
    collected = rw.provision(client)
    assert collected.provenance == "study"
    assert operator.mark_demo("s1@example.com") == (operator.MARKED_DEMO,
                                                    collected.subject_code)
    db.session.expire_all()
    assert rw.subject_of(rw.user_by_email("s1@example.com")).provenance == "demo"
    assert ResearchAuditEvent.query.filter_by(action="subject_marked_demo").count() == 2


def test_status_and_the_exclusion_list_are_identity_recovery_for_the_operator(app):
    rw.user("s@example.com", rw.STUDENT)
    assert operator.status("s@example.com") == (operator.NO_SUBJECT, None)
    _status, code = operator.exclude("s@example.com")
    found, state = operator.status("s@example.com")
    assert state == (code, "excluded", "external_exclusion", "study")
    listed = operator.excluded_accounts()
    assert [(a.email, a.subject_code, a.status_basis) for a in listed] == [
        ("s@example.com", code, "external_exclusion")]


def test_a_database_refusal_writes_nothing(app, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    rw.user("s@example.com", rw.STUDENT)

    def refuse():
        raise IntegrityError("INSERT", {}, Exception("uq_research_subject_links_user_id"))

    monkeypatch.setattr(db.session, "commit", refuse)
    assert operator.exclude("s@example.com") == (operator.CONFLICT, None)
    monkeypatch.undo()
    assert ResearchSubject.query.count() == ResearchAuditEvent.query.count() == 0


def test_the_script_records_exceptions_only(app):
    rw.user("s@example.com", rw.STUDENT)
    code, out, err = _run(app, "status", "s@example.com")
    assert code == 1 and "no research subject" in err
    code, out, err = _run(app, "exclude", "s@example.com")
    assert code == 0 and "Excluded: RS-" in out
    code, out, err = _run(app, "list-excluded")
    assert code == 0 and "s@example.com" in out and "1 excluded Student account(s)." in out
    code, out, err = _run(app, "reinstate", "s@example.com")
    assert code == 0 and "Reinstated: RS-" in out
    code, out, err = _run(app, "status", "s@example.com")
    assert code == 0 and "included (operator_reinstatement, study)" in out
    code, out, err = _run(app, "mark-demo", "s@example.com")
    assert code == 0 and "Marked as demonstration data" in out
    code, out, err = _run(app, "reinstate", "s@example.com")
    assert code == 0 and "not excluded" in err
    code, out, err = _run(app, "list-excluded")
    assert "0 excluded Student account(s)." in out


def test_the_script_needs_the_override_for_a_legacy_exclusion(app):
    student = rw.user("s@example.com", rw.STUDENT)
    operator.exclude("s@example.com")
    _legacy(rw.subject_of(student).id)
    code, _out, err = _run(app, "reinstate", "s@example.com")
    assert code == 1 and "--lift-legacy-exclusion" in err
    code, out, _err = _run(app, "reinstate", "s@example.com", "--lift-legacy-exclusion")
    assert code == 0 and "Reinstated" in out


def _expired_session(app):
    people = rw.world(app)
    client = app.test_client()
    rw.login(client, "s1@example.com")
    rw.post(client, rw.batch([rw.Browser().event("page_view", detail="wide")]))
    session = ResearchSession.query.one()
    rw.age_session(session.id, 40 * 24 * 60 * 60 * 1000)
    return people


def test_retention_is_a_dry_run_unless_executed(app):
    _expired_session(app)
    report = operator.retention_report(30)
    assert (report.sessions, report.events, report.archives, report.executed) == (1, 1, 0,
                                                                                  False)
    assert ResearchSession.query.count() == 1
    report = operator.retention_report(30, execute=True)
    assert report.executed and report.sessions == 1
    assert ResearchSession.query.count() == ResearchEvent.query.count() == 0
    assert ResearchFeedbackPrompt.query.count() == 0
    # Subjects and their links stay: an exclusion must stay enforceable.
    assert ResearchSubject.query.count() == 2
    audit = ResearchAuditEvent.query.order_by(ResearchAuditEvent.id.desc()).first()
    assert (audit.action, audit.count_value, audit.detail_code) == ("retention_purged", 1,
                                                                    "days=30")


def test_recent_sessions_survive_a_purge(app):
    _expired_session(app)
    assert operator.retention_report(60, execute=True).sessions == 0
    assert ResearchSession.query.count() == 1


def test_the_purge_script_needs_a_configured_retention_and_execute(app):
    _expired_session(app)
    code, _out, err = _run(app, "purge-expired")
    assert code == 1 and "RESEARCH_RETENTION_DAYS is not configured" in err
    app.config["RESEARCH_RETENTION_DAYS"] = 30
    code, out, _err = _run(app, "purge-expired")
    assert code == 0 and "Would delete 1 session(s)" in out and "Nothing was deleted" in out
    assert "0 export archive(s)" in out
    assert ResearchSession.query.count() == 1
    code, out, _err = _run(app, "purge-expired", "--execute")
    assert "Deleted 1 session(s)" in out and ResearchSession.query.count() == 0


def _export(app, people):
    app.config["RESEARCH_DATA_PROVENANCE"] = "study"
    client = app.test_client()
    rw.provision(client, "s2@example.com")
    status, public_id = exporter.create_export(people["researcher"].id, None, None, None, "UTC")
    assert status == exporter.CREATED
    return ResearchExport.query.filter_by(public_id=public_id).one()


def test_an_export_archive_never_outlives_the_oldest_data_in_it(app):
    people = _expired_session(app)    # s1's session is 40 days old (development)
    db.session.execute(text("UPDATE research_sessions SET provenance = 'study'"))
    db.session.commit()
    export = _export(app, people)     # holds s1's old session and s2's new one
    assert export.oldest_last_seen_ms == ResearchSession.query.order_by(
        ResearchSession.last_seen_at_ms).first().last_seen_at_ms
    report = operator.retention_report(30)
    assert (report.sessions, report.archives, report.executed) == (1, 1, False)
    assert ResearchExportArchive.query.count() == 1
    report = operator.retention_report(30, execute=True)
    assert report.executed and report.archives == 1
    assert ResearchExportArchive.query.count() == 0
    # The description stays: it records what existed and when.
    assert ResearchExport.query.count() == 1
    details = {a.detail_code for a in ResearchAuditEvent.query.filter_by(
        action="retention_purged")}
    assert details == {"days=30", "export_archives;days=30"}
    assert exporter.download_export(people["researcher"].id, export.public_id) == (
        exporter.EXPIRED, None)


def test_a_recent_export_archive_survives_a_purge(app):
    people = rw.world(app)
    export = _export(app, people)
    report = operator.retention_report(30, execute=True)
    assert report.archives == 0 and ResearchExportArchive.query.count() == 1
    assert exporter.archive_available(export.id)


@pytest.mark.parametrize("raw, expected", [(None, None), ("", None), ("  ", None),
                                           ("30", 30), (" 365 ", 365)])
def test_retention_parsing(raw, expected):
    assert parse_retention_days(raw) == expected


@pytest.mark.parametrize("raw", ["0", "3651", "forever", "30.5"])
def test_invalid_retention_refuses_to_start(raw):
    with pytest.raises(ResearchSettingsError):
        parse_retention_days(raw)


def test_settings_fail_closed_on_an_unknown_provenance():
    with pytest.raises(ResearchSettingsError):
        resolve_research_settings({"RESEARCH_DATA_PROVENANCE": "real"})
    config = {"RESEARCH_DATA_PROVENANCE": " Study ", "RESEARCHER_EMAIL_ALLOWLIST": "A@x.org, b@y.org"}
    resolve_research_settings(config)
    assert config["RESEARCH_DATA_PROVENANCE"] == "study"
    assert config["RESEARCHER_EMAIL_ALLOWLIST"] == frozenset({"a@x.org", "b@y.org"})
    assert parse_allowlist("") == frozenset()


def test_production_defaults_to_study_and_testing_to_development():
    from app.config import ProductionConfig, TestingConfig

    assert TestingConfig.RESEARCH_DATA_PROVENANCE == "development"
    assert TestingConfig.RESEARCH_RETENTION_DAYS is None
    assert ProductionConfig.RESEARCH_DATA_PROVENANCE in ("study", "development")


def _provision(app, monkeypatch, allowlist, email):
    import scripts.create_researcher as provisioning

    monkeypatch.setattr(provisioning, "create_app", lambda _name: app)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    answers = iter([email, "Rhea Researcher"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(provisioning.getpass, "getpass", lambda _prompt: "a-long-password-123")
    app.config["RESEARCHER_EMAIL_ALLOWLIST"] = allowlist
    return provisioning.main()


def test_provisioning_refuses_an_empty_allowlist(app, monkeypatch):
    assert _provision(app, monkeypatch, frozenset(), "rhea@example.com") == 1
    from app.models import User

    assert User.query.count() == 0


def test_provisioning_refuses_an_address_outside_the_allowlist(app, monkeypatch):
    assert _provision(app, monkeypatch, frozenset({"rhea@example.com"}),
                      "someone@example.com") == 1
    from app.models import User

    assert User.query.count() == 0


def test_provisioning_creates_a_researcher_for_an_approved_address(app, monkeypatch):
    assert _provision(app, monkeypatch, frozenset({"rhea@example.com"}), "Rhea@Example.com") == 0
    from app.models import User

    user = User.query.one()
    assert (user.email, user.role) == ("rhea@example.com", "researcher")
    assert user.password_hash.startswith("$argon2")
