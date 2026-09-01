from urllib.parse import quote

from app.extensions import db
from app.models import UserRole, UserStatus
from app.security.redirects import get_safe_redirect_target
from tests.conftest import make_user

PASSWORD = "MyValidPassphrase123"


def _make_admin(email="admin@example.com"):
    return make_user(email, UserRole.ADMINISTRATOR.value, password=PASSWORD)


def _make_student(email="student@example.com"):
    return make_user(email, UserRole.STUDENT.value, password=PASSWORD)


# ======================================================================
# UNIT TESTS FOR THE HELPER ITSELF
# ======================================================================


def test_safe_local_path_is_allowed():
    assert get_safe_redirect_target("/admin/students") == "/admin/students"
    assert (
        get_safe_redirect_target("/admin/students/21479aca-ce4e-40b8-9e20-b7156cca035c")
        == "/admin/students/21479aca-ce4e-40b8-9e20-b7156cca035c"
    )


def test_external_https_url_is_rejected():
    assert get_safe_redirect_target("https://evil.example/phish") is None


def test_external_http_url_is_rejected():
    assert get_safe_redirect_target("http://evil.example/phish") is None


def test_scheme_relative_url_is_rejected():
    assert get_safe_redirect_target("//evil.example/") is None
    assert get_safe_redirect_target("///evil.example/") is None


def test_javascript_scheme_is_rejected():
    assert get_safe_redirect_target("javascript:alert(1)") is None


def test_backslash_authority_confusion_is_rejected():
    assert get_safe_redirect_target("/\\evil.example") is None
    assert get_safe_redirect_target("\\/evil.example") is None
    assert get_safe_redirect_target("\\\\evil.example") is None


def test_malformed_or_ambiguous_targets_are_rejected():
    assert get_safe_redirect_target("") is None
    assert get_safe_redirect_target(None) is None
    assert get_safe_redirect_target("evil.example") is None  # no leading slash
    assert get_safe_redirect_target("admin/students") is None  # relative, no leading slash
    assert get_safe_redirect_target("/\t/evil.example") is None  # control char
    assert get_safe_redirect_target("/\n/evil.example") is None  # control char


# ======================================================================
# INTEGRATION TESTS AGAINST THE REAL /auth/login VIEW
# ======================================================================


def test_login_with_safe_next_redirects_there(app, client):
    with app.app_context():
        _make_admin()

    resp = client.post(
        "/auth/login?next=/admin/students",
        data={"email": "admin@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/students"


def test_login_with_external_https_next_falls_back_to_role_home(app, client):
    with app.app_context():
        _make_admin()

    target = quote("https://evil.example/phish", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "admin@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/dashboard"
    assert "evil.example" not in resp.headers["Location"]


def test_login_with_external_http_next_falls_back_to_role_home(app, client):
    with app.app_context():
        _make_admin()

    target = quote("http://evil.example/phish", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "admin@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/dashboard"


def test_login_with_scheme_relative_next_falls_back_to_role_home(app, client):
    with app.app_context():
        _make_admin()

    target = quote("//evil.example/", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "admin@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/dashboard"
    assert "evil.example" not in resp.headers["Location"]


def test_login_with_javascript_next_falls_back_to_role_home(app, client):
    with app.app_context():
        _make_admin()

    target = quote("javascript:alert(1)", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "admin@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/dashboard"


def test_login_with_backslash_next_falls_back_to_role_home(app, client):
    with app.app_context():
        _make_admin()

    target = quote("/\\evil.example", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "admin@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/dashboard"
    assert "evil.example" not in resp.headers["Location"]


def test_login_unsafe_next_falls_back_to_correct_role_home_for_non_admin(app, client):
    with app.app_context():
        _make_student()

    target = quote("https://evil.example/", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "student@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # M09: Students now have a dedicated dashboard.
    assert resp.headers["Location"] == "/student/dashboard"


def test_login_unsafe_next_falls_back_to_role_home_for_teacher(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value, password=PASSWORD)

    target = quote("//evil.example/", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "teacher@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/teacher/dashboard"
    assert "evil.example" not in resp.headers["Location"]


def test_researcher_still_falls_back_to_default_home(app, client):
    """The Researcher dashboard is deferred to Phase 6, so Researcher
    keeps the DEFAULT_HOME_ENDPOINT fallback."""
    with app.app_context():
        make_user("researcher@example.com", UserRole.RESEARCHER.value, password=PASSWORD)

    resp = client.post(
        "/auth/login",
        data={"email": "researcher@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/design-system/"


def test_invalid_credentials_never_redirect_via_next(app, client):
    with app.app_context():
        _make_admin()

    target = quote("https://evil.example/phish", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "admin@example.com", "password": "wrong-password"},
        follow_redirects=False,
    )
    # Invalid credentials re-render the login form (200), never a redirect.
    assert resp.status_code == 200
    assert b"Invalid email or password" in resp.data
    assert b"evil.example" not in resp.data


def test_rejected_next_value_not_exposed_in_flash_message(app, client):
    with app.app_context():
        _make_admin()

    target = quote("https://evil.example/phish", safe="")
    resp = client.post(
        f"/auth/login?next={target}",
        data={"email": "admin@example.com", "password": PASSWORD},
        follow_redirects=True,
    )
    assert b"evil.example" not in resp.data


# ======================================================================
# REGRESSION: existing login/logout/suspended/rate-limit behaviour intact
# ======================================================================


def test_existing_login_success_without_next_still_works(app, client):
    with app.app_context():
        _make_admin()

    resp = client.post(
        "/auth/login",
        data={"email": "admin@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/dashboard"


def test_authenticated_user_visiting_login_is_sent_to_role_home(app, client):
    """An already-authenticated user who loads /auth/login is redirected
    to their own role home -- for every role, not just Administrator."""
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value, password=PASSWORD)
    client.post("/auth/login", data={"email": "teacher@example.com", "password": PASSWORD})

    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/teacher/dashboard"


def test_existing_logout_still_works(app, client):
    with app.app_context():
        _make_admin()
    client.post("/auth/login", data={"email": "admin@example.com", "password": PASSWORD})

    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/auth/login"


def test_existing_suspended_account_still_rejected(app, client):
    with app.app_context():
        make_user(
            "suspended@example.com",
            UserRole.STUDENT.value,
            status=UserStatus.SUSPENDED.value,
            password=PASSWORD,
        )

    resp = client.post(
        "/auth/login",
        data={"email": "suspended@example.com", "password": PASSWORD},
        follow_redirects=True,
    )
    assert b"Invalid email or password" in resp.data


def test_existing_rate_limit_still_enforced():
    # TestingConfig disables rate limiting for the rest of the suite, so
    # this one test spins up its own app with it forced back on -- same
    # pattern as test_foundation.py's CSRF-forced test -- to prove the
    # @limiter.limit("10 per minute") decorator on /auth/login is untouched.
    # flask-limiter reads RATELIMIT_ENABLED at init_app() time, so the
    # config class must be patched *before* create_app() runs.
    from app import create_app
    from app.config import config_by_name

    original = config_by_name["testing"].RATELIMIT_ENABLED
    config_by_name["testing"].RATELIMIT_ENABLED = True
    try:
        app = create_app("testing")
    finally:
        config_by_name["testing"].RATELIMIT_ENABLED = original

    with app.app_context():
        db.create_all()
        try:
            client = app.test_client()
            statuses = [
                client.post(
                    "/auth/login",
                    data={"email": "nobody@example.com", "password": "wrong"},
                ).status_code
                for _ in range(11)
            ]
            assert statuses[:10].count(429) == 0
            assert statuses[10] == 429
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
