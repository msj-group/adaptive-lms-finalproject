"""The superseded Phase 6 design is removed, not hidden (Phase 6 replacement,
corrected).

Proves that the in-app consent workflow, the prescribed-task protocol
catalogue and the Student "usage research" information page with its
portal indicator are absent from the running application: no route rule, no
endpoint, no importable runtime module, no model or table in the metadata,
no template, no navigation entry, and no link on any portal page. Direct URLs
are tried as well as navigation, because a hidden button is not a removal.
"""

import importlib
import json
import pathlib
import subprocess
import sys

import pytest

import tests.research_world as rw
from tests.research_world import fresh_identity_per_request  # noqa: F401
from app import create_app
from app.extensions import db


@pytest.fixture(autouse=True)
def _fresh_identity(fresh_identity_per_request):
    """Every request re-reads its own client's login."""


ROOT = pathlib.Path(__file__).resolve().parents[1]

OBSOLETE_PATHS = (
    "/admin/research",
    "/admin/research/consent-documents/new",
    "/admin/research/participants",
    "/admin/research/participants/new",
    "/student/research-consent",
    "/student/research-consent/accept",
    "/student/research-consent/decline",
    "/student/research-consent/withdraw",
    "/research/participants",
    "/research/protocols",
    "/research/protocols/new",
    "/student/usage-research",
)

OBSOLETE_MODULES = (
    "app.blueprints.admin.research",
    "app.blueprints.admin.research_forms",
    "app.blueprints.student.research",
    "app.blueprints.research.protocols",
    "app.blueprints.research.protocol_forms",
    "app.blueprints.research.routes",
    "app.services.research_queries",
    "app.services.research_transactions",
    "app.services.research_text",
    "app.services.experiment_protocol_queries",
    "app.services.experiment_protocol_review",
    "app.services.experiment_protocol_text",
    "app.services.experiment_protocol_tokens",
    "app.services.experiment_protocol_transactions",
    "app.models.research_consent_document",
    "app.models.research_consent_event",
    "app.models.research_participant",
    "app.models.experiment_definition",
    "app.models.experiment_task",
    "app.models.experiment_task_set",
    "app.blueprints.student.usage_research",
)

OBSOLETE_TABLES = (
    "research_consent_documents", "research_participants", "research_consent_events",
    "experiment_definitions", "experiment_task_sets", "experiment_tasks",
)

OBSOLETE_NAMES = (
    "ResearchConsentDocument", "ResearchConsentEvent", "ResearchParticipant",
    "ResearchConsentDocumentStatus", "ResearchParticipantStatus", "ResearchConsentAction",
    "ExperimentDefinition", "ExperimentTaskSet", "ExperimentTask",
    "ExperimentDefinitionStatus", "ExperimentStudyStage", "ExperimentTaskType",
    "ExperimentTaskDifficulty", "ExperimentCompletionCriterion",
)


def test_no_obsolete_rule_or_endpoint_is_registered():
    app = create_app("testing")
    rules = [(rule.rule, rule.endpoint) for rule in app.url_map.iter_rules()]
    for rule, endpoint in rules:
        assert "consent" not in rule and "protocol" not in rule, rule
        assert not rule.startswith("/admin/research"), rule
        assert "research_consent" not in endpoint and "protocol" not in endpoint, endpoint
        assert not endpoint.startswith("admin.research"), endpoint
        assert "usage" not in rule and "usage_research" not in endpoint, endpoint
    assert not any(rule.startswith("/research/participants") for rule, _e in rules)


@pytest.mark.parametrize("path", OBSOLETE_PATHS)
def test_every_obsolete_url_is_a_plain_404_for_every_role(app, client, path):
    rw.user("admin@example.com", rw.ADMIN)
    rw.user("student@example.com", rw.STUDENT)
    rw.user("researcher@example.com", rw.RESEARCHER)
    for email in ("admin@example.com", "student@example.com", "researcher@example.com"):
        client.post("/auth/logout")
        client.post("/research/logout")
        rw.login(client, email)
        rw.login_researcher(client, email)
        assert client.get(path).status_code == 404, (email, path)
        assert client.post(path).status_code in (404, 405), (email, path)


def test_the_running_application_imports_no_obsolete_module():
    """Checked in a fresh interpreter, so this process's module cache is left
    alone: build the application, then list what it imported."""
    script = (
        "import sys, json\n"
        "from app import create_app\n"
        "create_app('testing')\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('app.'))))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True,
                            text=True, check=True)
    loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert "app.blueprints.research.access" in loaded
    for module in OBSOLETE_MODULES:
        assert module not in loaded, module
        assert not (ROOT / (module.replace(".", "/") + ".py")).exists(), module
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)


def test_the_data_model_has_no_obsolete_table_or_name(app):
    for table in OBSOLETE_TABLES:
        assert table not in db.metadata.tables, table
    import app.models as models

    for name in OBSOLETE_NAMES:
        assert not hasattr(models, name), name
        assert name not in models.__all__, name


def test_no_obsolete_template_remains():
    templates = ROOT / "app" / "templates"
    assert not (templates / "admin" / "research").exists()
    assert not (templates / "student" / "research").exists()
    assert not (templates / "research" / "protocols").exists()
    for old in ("participants.html", "participant.html", "_portal_nav.html"):
        assert not (templates / "research" / old).exists(), old
    assert not (templates / "student" / "usage_research.html").exists()


def test_no_application_source_mentions_the_removed_workflows():
    for path in (ROOT / "app").rglob("*"):
        if path.suffix not in (".py", ".html", ".js", ".css"):
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ("research_consent", "consent_document", "ResearchParticipant",
                       "experiment_protocol", "ExperimentTask", "research.protocols",
                       "admin.research_", "portal_consent_status", "usage_research",
                       "usage-research", "Usage research on", "student_usage_status"):
            assert marker not in text, (path.relative_to(ROOT), marker)


def test_normal_portals_carry_no_research_management_link(app, client):
    people = rw.world(app)
    pages = {
        "admin@example.com": ("/admin/dashboard", "/admin/students", "/admin/teachers"),
        "teacher@example.com": ("/teacher/dashboard",),
        "excluded@example.com": ("/student/dashboard",),
        "s1@example.com": ("/student/dashboard",),
    }
    for email, paths in pages.items():
        client.post("/auth/logout")
        rw.login(client, email)
        for path in paths:
            html = client.get(path).get_data(as_text=True)
            for marker in ('href="/research', "Research workspace", "Configurations",
                           "Participants", "Protocols", "research consent", "Exports",
                           people["researcher"].full_name, "researcher@example.com"):
                assert marker not in html, (email, path, marker)
