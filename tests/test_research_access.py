"""The dedicated Researcher login and the workspace boundary (Phase 6
replacement).

Covers: only active Researcher accounts authenticate at ``/research/login``;
the LMS login refuses them; a signed-in account of another role is sent back
to its own portal without being signed out; safe workspace-only redirects;
generic errors; logout; the active-Researcher requirement on every workspace
rule; the anonymous redirect to the research login; and ``private, no-store``
on every response under ``/research``, including Flask's own 404 and 405.
"""

import pytest

import tests.research_world as rw
from tests.research_world import fresh_identity_per_request  # noqa: F401
from app import create_app
from app.models import UserStatus


@pytest.fixture(autouse=True)
def _fresh_identity(fresh_identity_per_request):
    """Every request re-reads its own client's login."""


GENERIC = b"Invalid email or password."


def _workspace_rules():
    app = create_app("testing")
    rules = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("research.") and rule.endpoint not in (
                "research.login", "research.logout"):
            rules.append(rule)
    return rules


def _concrete(rule):
    path = rule.rule
    for argument in rule.arguments:
        path = path.replace(f"<{argument}>", "00000000-0000-4000-8000-000000000000")
    return path


WORKSPACE_RULES = [(r.rule, _concrete(r), sorted(r.methods - {"HEAD", "OPTIONS"}))
                   for r in _workspace_rules()]


def test_the_workspace_route_inventory_is_exactly_the_implemented_workflow():
    assert sorted(rule for rule, _path, _methods in WORKSPACE_RULES) == sorted([
        "/research/", "/research/dashboard", "/research/activity-log",
        "/research/configurations", "/research/configurations/new",
        "/research/configurations/<configuration_public_id>",
        "/research/configurations/<configuration_public_id>/edit",
        "/research/configurations/<configuration_public_id>/activate",
        "/research/configurations/<configuration_public_id>/collecting",
        "/research/sessions", "/research/sessions/<session_public_id>",
        "/research/exports", "/research/exports/<export_public_id>",
        "/research/exports/<export_public_id>/download",
        "/research/exclusions",
    ])


def test_an_active_researcher_signs_in_and_out_at_the_dedicated_entry(app, client):
    rw.user("researcher@example.com", rw.RESEARCHER)
    response = rw.login_researcher(client)
    assert response.status_code == 302
    assert response.headers["Location"] == "/research/dashboard"
    assert client.get("/research/dashboard").status_code == 200
    out = client.post("/research/logout")
    assert out.status_code == 302 and out.headers["Location"] == "/research/login"
    assert client.get("/research/dashboard").status_code == 302


@pytest.mark.parametrize("role", [rw.STUDENT, rw.TEACHER, rw.ADMIN])
def test_other_roles_cannot_authenticate_at_the_research_login(app, client, role):
    rw.user("someone@example.com", role)
    response = rw.login_researcher(client, "someone@example.com")
    assert response.status_code == 200 and GENERIC in response.data
    assert client.get("/research/dashboard").status_code == 302


def test_failures_are_generic_and_disclose_nothing(app, client):
    rw.user("researcher@example.com", rw.RESEARCHER)
    rw.user("gone@example.com", rw.RESEARCHER, status=UserStatus.SUSPENDED.value)
    for email, password in (("researcher@example.com", "wrong-password"),
                            ("nobody@example.com", rw.PW),
                            ("gone@example.com", rw.PW)):
        response = client.post("/research/login", data={"email": email, "password": password})
        assert response.status_code == 200 and GENERIC in response.data
        body = response.get_data(as_text=True)
        assert "suspended" not in body.lower() and "not found" not in body.lower()


def test_the_lms_login_refuses_researcher_accounts_with_the_same_message(app, client):
    rw.user("researcher@example.com", rw.RESEARCHER)
    response = rw.login(client, "researcher@example.com")
    assert response.status_code == 200 and GENERIC in response.data


def test_an_email_domain_or_an_allowlist_never_grants_the_role(app, client):
    app.config["RESEARCHER_EMAIL_ALLOWLIST"] = frozenset({"student@research.example"})
    rw.user("student@research.example", rw.STUDENT)
    response = rw.login_researcher(client, "student@research.example")
    assert GENERIC in response.data
    assert client.get("/research/dashboard").status_code == 302


@pytest.mark.parametrize("role, home", [(rw.STUDENT, "/student/dashboard"),
                                        (rw.TEACHER, "/teacher/dashboard"),
                                        (rw.ADMIN, "/admin/dashboard")])
def test_another_signed_in_role_is_sent_home_without_being_signed_out(app, client, role, home):
    rw.user("someone@example.com", role)
    rw.user("researcher@example.com", rw.RESEARCHER)
    rw.login(client, "someone@example.com")
    credentials = {"email": "researcher@example.com", "password": rw.PW}
    for response in (client.get("/research/login"),
                     client.post("/research/login", data=credentials)):
        assert response.status_code == 302 and response.headers["Location"] == home
    # Still the same account: the credentials above were never checked.
    assert client.get(home).status_code == 200
    assert client.get("/research/dashboard").status_code == 403


def test_a_signed_in_researcher_opening_the_login_goes_to_the_dashboard(app, client):
    rw.user("researcher@example.com", rw.RESEARCHER)
    rw.login_researcher(client)
    response = client.get("/research/login")
    assert response.status_code == 302 and response.headers["Location"] == "/research/dashboard"


@pytest.mark.parametrize("target, expected", [
    ("/research/sessions", "/research/sessions"),
    ("/admin/dashboard", "/research/dashboard"),
    ("//evil.example/research", "/research/dashboard"),
    ("https://evil.example/research/", "/research/dashboard"),
    ("/research/login", "/research/dashboard"),
    ("/researchers-elsewhere", "/research/dashboard"),
])
def test_next_is_honoured_only_inside_the_workspace(app, client, target, expected):
    rw.user("researcher@example.com", rw.RESEARCHER)
    response = client.post("/research/login", query_string={"next": target},
                           data={"email": "researcher@example.com", "password": rw.PW})
    assert response.headers["Location"] == expected


def test_an_anonymous_visitor_is_sent_to_the_research_login(app, client):
    response = client.get("/research/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/research/login")
    assert "/auth/login" not in response.headers["Location"]


@pytest.mark.parametrize("rule, path, methods", WORKSPACE_RULES)
@pytest.mark.parametrize("role", [rw.STUDENT, rw.TEACHER, rw.ADMIN])
def test_every_workspace_rule_refuses_every_other_role(app, client, rule, path, methods, role):
    rw.user("someone@example.com", role)
    rw.login(client, "someone@example.com")
    for method in methods:
        response = client.open(path, method=method)
        assert response.status_code == 403, (rule, method)
        assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("rule, path, methods", WORKSPACE_RULES)
def test_every_workspace_rule_needs_an_authenticated_researcher(app, client, rule, path, methods):
    for method in methods:
        response = client.open(path, method=method)
        assert response.status_code == 302, (rule, method)
        assert response.headers["Location"].startswith("/research/login")
        assert response.headers["Cache-Control"] == "private, no-store"


def test_a_suspended_researcher_loses_the_session(app, client):
    researcher = rw.user("researcher@example.com", rw.RESEARCHER)
    rw.login_researcher(client)
    assert client.get("/research/dashboard").status_code == 200
    researcher.status = UserStatus.SUSPENDED.value
    researcher.bump_auth_version()
    from app.extensions import db

    db.session.commit()
    from flask import g

    g.pop("_login_user", None)
    assert client.get("/research/dashboard").status_code == 302


def test_every_response_under_research_is_private_and_not_stored(app, client):
    rw.user("researcher@example.com", rw.RESEARCHER)
    for response in (client.get("/research/login"), client.get("/research/nowhere"),
                     client.put("/research/dashboard"), client.get("/research/dashboard")):
        assert response.headers["Cache-Control"] == "private, no-store"
        assert "Cookie" in response.headers.get("Vary", "")
    rw.login_researcher(client)
    for response in (client.get("/research/dashboard"),
                     client.get("/research/sessions/not-a-uuid"),
                     client.delete("/research/exports")):
        assert response.headers["Cache-Control"] == "private, no-store"


def test_logout_from_the_workspace_refuses_another_role(app, client):
    rw.user("student@example.com", rw.STUDENT)
    rw.login(client, "student@example.com")
    assert client.post("/research/logout").status_code == 403
    assert client.get("/student/dashboard").status_code == 200


def test_the_login_and_logout_posts_need_csrf_when_it_is_enabled(app, client):
    rw.user("researcher@example.com", rw.RESEARCHER)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = rw.login_researcher(client)
        # CSRFProtect refuses a token-less POST before the view runs.
        assert response.status_code == 400
        assert client.get("/research/dashboard").status_code == 302
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
