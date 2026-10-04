"""Scheduled retention removes only expired research data, and fails closed
when the deployment has not configured a duration."""
import json

import tests.research_world as rw
from app.extensions import db
from app.models import ResearchEvent, ResearchExport, ResearchExportArchive, ResearchSession
from app.services import research_exports as exporter
from scripts import run_research_retention as job


def test_daily_job_expires_15_day_data_and_archives(app, tmp_path, monkeypatch, capsys):
    people = rw.world(app, provenance_study=True)
    rw.provision(app.test_client())
    session = ResearchSession.query.one()
    rw.age_session(session.id, 16 * 86_400_000)
    status, _public_id = exporter.create_export(people['researcher'].id, None, None, None, 'UTC')
    assert status == exporter.CREATED
    app.config['RESEARCH_RETENTION_DAYS'] = 15
    monkeypatch.setattr(job, 'ROOT', tmp_path)
    monkeypatch.setattr(job, 'create_app', lambda _name: app)
    assert job.main() == 0
    assert ResearchSession.query.count() == ResearchEvent.query.count() == 0
    assert ResearchExportArchive.query.count() == 0
    assert ResearchExport.query.count() == 1
    record = json.loads((tmp_path / 'instance' / 'research-retention.jsonl').read_text())
    assert record['success'] and record['retention_days'] == 15
    assert record['sessions'] == record['archives'] == 1
    assert '@example.com' not in capsys.readouterr().out


def test_daily_job_without_retention_does_not_delete(app, tmp_path, monkeypatch):
    rw.world(app)
    rw.provision(app.test_client())
    monkeypatch.setattr(job, 'ROOT', tmp_path)
    monkeypatch.setattr(job, 'create_app', lambda _name: app)
    app.config['RESEARCH_RETENTION_DAYS'] = None
    assert job.main() == 1
    assert ResearchSession.query.count() == 1 and ResearchEvent.query.count() > 0
    record = json.loads((tmp_path / 'instance' / 'research-retention.jsonl').read_text())
    assert record['success'] is False and record['error_type'] == 'ValueError'
