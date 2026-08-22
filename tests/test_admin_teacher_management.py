import re
from contextlib import contextmanager

from app.extensions import db
from app.models import User, UserRole, UserStatus
from app.security.passwords import verify_password
from tests.conftest import login, make_user

STRONG_PASSWORD = "MyValidPassphrase123"


def _make_teacher(email="teacher@example.com", full_name="Teacher One", status=UserStatus.ACTIVE.value):
    return make_user(email, UserRole.TEACHER.value, status=status, full_name=full_name)


@contextmanager
def _isolated_app():
    """A standalone app/DB where no app context is left pushed while test
    clients make requests. See the identical helper in
    test_admin_student_management.py for the full rationale (Flask-Login
    caches the resolved current_user on flask.g, which is scoped to the
    app context, so two simultaneous test clients sharing one pushed app
    context would incorrectly share one cached identity).
    """
    from app import create_app

    flask_app = create_app("testing")
    with flask_app.app_context():
        db.create_all()
    try:
        yield flask_app
    finally:
        with flask_app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _get_csrf_token(client, path):
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    return match.group(1) if match else None


# ======================================================================
# AUTHORIZATION AND IDENTITY ISOLATION
# ======================================================================


def test_administrator_can_access_create_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")
    assert client.get("/admin/teachers/new").status_code == 200


def test_anonymous_denied_create_page(client):
    resp = client.get("/admin/teachers/new")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_teacher_student_researcher_denied_create_page(app):
    with app.app_context():
        _make_teacher("teacherx@example.com")
        make_user("studentx@example.com", UserRole.STUDENT.value)
        make_user("researcherx@example.com", UserRole.RESEARCHER.value)

    for email in ["teacherx@example.com", "studentx@example.com", "researcherx@example.com"]:
        role_client = app.test_client()
        login(role_client, email)
        assert role_client.get("/admin/teachers/new").status_code == 403


def test_administrator_can_access_every_teacher_route(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    assert client.get(f"/admin/teachers/{public_id}").status_code == 200
    assert client.get(f"/admin/teachers/{public_id}/edit").status_code == 200
    assert client.get(f"/admin/teachers/{public_id}/reset-password").status_code == 200
    assert client.post(f"/admin/teachers/{public_id}/toggle-status", follow_redirects=True).status_code == 200


def test_non_teacher_public_id_returns_404_for_detail_edit_toggle_reset(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        admin_other = make_user("admin.other@example.com", UserRole.ADMINISTRATOR.value)
        student = make_user("astudent@example.com", UserRole.STUDENT.value)
        researcher = make_user("aresearcher@example.com", UserRole.RESEARCHER.value)
        non_teacher_public_ids = (admin_other.public_id, student.public_id, researcher.public_id)
    login(client, "admin@example.com")

    for public_id in non_teacher_public_ids:
        assert client.get(f"/admin/teachers/{public_id}").status_code == 404
        assert client.get(f"/admin/teachers/{public_id}/edit").status_code == 404
        assert client.post(f"/admin/teachers/{public_id}/toggle-status").status_code == 404
        assert client.get(f"/admin/teachers/{public_id}/reset-password").status_code == 404


def test_nonexistent_public_id_returns_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")
    resp = client.get("/admin/teachers/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_internal_numeric_id_not_used_in_teacher_urls(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
    login(client, "admin@example.com")

    resp = client.get("/admin/teachers")
    html = resp.get_data(as_text=True)
    with app.app_context():
        teacher = User.query.filter_by(email="teacher@example.com").first()
        assert f"/admin/teachers/{teacher.public_id}" in html
        # Word-boundary check: public_id is a random UUID and could
        # coincidentally start with the same digit(s) as the numeric id.
        assert re.search(rf"/admin/teachers/{teacher.id}(?!\w)", html) is None


# ======================================================================
# CREATION
# ======================================================================


def test_valid_teacher_creation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/teachers/new",
        data={
            "full_name": "New Teacher",
            "email": "new.teacher@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        teacher = User.query.filter_by(email="new.teacher@example.com").first()
        assert teacher is not None
        assert teacher.role == UserRole.TEACHER.value
        assert teacher.status == UserStatus.ACTIVE.value


def test_email_is_normalized_to_lowercase(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    client.post(
        "/admin/teachers/new",
        data={
            "full_name": "Case Teacher",
            "email": "Mixed.Case@Example.COM",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
        },
        follow_redirects=True,
    )

    with app.app_context():
        assert User.query.filter_by(email="mixed.case@example.com").first() is not None
        assert User.query.filter_by(email="Mixed.Case@Example.COM").first() is None


def test_password_is_hashed_never_plaintext(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    client.post(
        "/admin/teachers/new",
        data={
            "full_name": "Hash Teacher",
            "email": "hash.teacher@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
        },
        follow_redirects=True,
    )

    with app.app_context():
        teacher = User.query.filter_by(email="hash.teacher@example.com").first()
        assert teacher.password_hash != STRONG_PASSWORD
        assert teacher.password_hash.startswith("$argon2")
        assert verify_password(teacher.password_hash, STRONG_PASSWORD)


def test_password_confirmation_mismatch_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/teachers/new",
        data={
            "full_name": "Mismatch Teacher",
            "email": "mismatch.teacher@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": "SomethingDifferent123",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"do not match" in resp.data.lower()
    with app.app_context():
        assert User.query.filter_by(email="mismatch.teacher@example.com").first() is None


def test_password_exactly_14_characters_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw = "a" * 14
    resp = client.post(
        "/admin/teachers/new",
        data={"full_name": "Boundary14", "email": "tboundary14@example.com", "password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="tboundary14@example.com").first() is None


def test_password_exactly_15_characters_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw = "a" * 15
    resp = client.post(
        "/admin/teachers/new",
        data={"full_name": "Boundary15", "email": "tboundary15@example.com", "password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(email="tboundary15@example.com").first()
        assert teacher is not None
        assert verify_password(teacher.password_hash, pw)


def test_password_exactly_128_characters_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw = "a" * 128
    resp = client.post(
        "/admin/teachers/new",
        data={"full_name": "Boundary128", "email": "tboundary128@example.com", "password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(email="tboundary128@example.com").first()
        assert teacher is not None
        assert verify_password(teacher.password_hash, pw)


def test_password_exactly_129_characters_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw = "a" * 129
    resp = client.post(
        "/admin/teachers/new",
        data={"full_name": "Boundary129", "email": "tboundary129@example.com", "password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="tboundary129@example.com").first() is None


def test_password_with_leading_trailing_spaces_not_stripped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw_with_spaces = "  Spaced Passphrase 123  "
    resp = client.post(
        "/admin/teachers/new",
        data={
            "full_name": "Spaced Pw",
            "email": "tspacedpw@example.com",
            "password": pw_with_spaces,
            "confirm_password": pw_with_spaces,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(email="tspacedpw@example.com").first()
        assert teacher is not None
        assert verify_password(teacher.password_hash, pw_with_spaces)
        assert not verify_password(teacher.password_hash, pw_with_spaces.strip())


def test_duplicate_email_rejected_against_every_role(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("existing.student@example.com", UserRole.STUDENT.value)
        make_user("existing.researcher2@example.com", UserRole.RESEARCHER.value)
        _make_teacher(email="existing.teacher@example.com")
    login(client, "admin@example.com")

    for existing_email in [
        "admin@example.com",
        "existing.student@example.com",
        "existing.researcher2@example.com",
        "existing.teacher@example.com",
    ]:
        resp = client.post(
            "/admin/teachers/new",
            data={
                "full_name": "Duplicate Attempt",
                "email": existing_email,
                "password": STRONG_PASSWORD,
                "confirm_password": STRONG_PASSWORD,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert User.query.filter_by(full_name="Duplicate Attempt").count() == 0


def test_client_supplied_privileged_fields_cannot_escalate(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/teachers/new",
        data={
            "full_name": "Tamper Teacher",
            "email": "tampert@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
            "role": "administrator",
            "status": "suspended",
            "id": "999999",
            "public_id": "11111111-1111-1111-1111-111111111112",
            "auth_version": "999",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        teacher = User.query.filter_by(email="tampert@example.com").first()
        assert teacher is not None
        assert teacher.role == UserRole.TEACHER.value
        assert teacher.status == UserStatus.ACTIVE.value
        assert teacher.id != 999999
        assert teacher.public_id != "11111111-1111-1111-1111-111111111112"
        assert teacher.auth_version == 1


def test_csrf_enforced_on_create():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                "/admin/teachers/new",
                data={
                    "full_name": "No CSRF",
                    "email": "nocsrft@example.com",
                    "password": STRONG_PASSWORD,
                    "confirm_password": STRONG_PASSWORD,
                },
            )
            assert resp.status_code == 400
            assert User.query.filter_by(email="nocsrft@example.com").first() is None
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_duplicate_email_race_condition_handled_without_500(app, client, monkeypatch):
    from app.blueprints.admin.forms import TeacherCreateForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher(email="racet@example.com")
    login(client, "admin@example.com")

    monkeypatch.setattr(TeacherCreateForm, "validate_email", lambda self, field: None)

    resp = client.post(
        "/admin/teachers/new",
        data={
            "full_name": "Race Teacher",
            "email": "racet@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert resp.status_code != 500
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert User.query.filter_by(email="racet@example.com").count() == 1


# ======================================================================
# DETAILS AND EDITING
# ======================================================================


def test_detail_shows_correct_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="detailt@example.com", full_name="Detail Teacher")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/teachers/{public_id}")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Detail Teacher" in html
    assert "detailt@example.com" in html


def test_detail_does_not_expose_sensitive_or_internal_fields(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="securet@example.com")
        public_id, teacher_id, password_hash = teacher.public_id, teacher.id, teacher.password_hash
    login(client, "admin@example.com")

    html = client.get(f"/admin/teachers/{public_id}").get_data(as_text=True)
    assert password_hash not in html
    assert "$argon2" not in html
    assert f">{teacher_id}<" not in html
    assert "auth_version" not in html.lower()
    assert ">teacher<" not in html.lower()  # raw role string not rendered


def test_detail_xss_in_name_is_escaped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="xsst@example.com", full_name="<script>alert(1)</script>")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/teachers/{public_id}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_edit_updates_name_and_email(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="oldt@example.com", full_name="Old Name")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/edit",
        data={"full_name": "New Name", "email": "newt@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.full_name == "New Name"
        assert teacher.email == "newt@example.com"


def test_edit_with_unchanged_email_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="samet@example.com", full_name="Name A")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/edit",
        data={"full_name": "Name B", "email": "samet@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" not in resp.data.lower()
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.full_name == "Name B"
        assert teacher.auth_version == 1  # unchanged email -> no session bump


def test_edit_rejects_duplicate_email_globally(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("student.taken@example.com", UserRole.STUDENT.value)
        teacher = _make_teacher(email="teacher.own@example.com")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/edit",
        data={"full_name": "Teacher", "email": "student.taken@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.email == "teacher.own@example.com"


def test_edit_cannot_tamper_role_status_password_or_public_id(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="tamper2t@example.com")
        public_id = teacher.public_id
        original_password_hash = teacher.password_hash
    login(client, "admin@example.com")

    client.post(
        f"/admin/teachers/{public_id}/edit",
        data={
            "full_name": "Tampered",
            "email": "tamper2t@example.com",
            "role": "administrator",
            "status": "suspended",
            "password_hash": "hacked",
            "public_id": "22222222-2222-2222-2222-222222222223",
        },
        follow_redirects=True,
    )

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher is not None
        assert teacher.role == UserRole.TEACHER.value
        assert teacher.status == UserStatus.ACTIVE.value
        assert teacher.password_hash == original_password_hash


def test_edit_preserves_password_hash_when_only_name_changes(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="preservet@example.com", full_name="Before")
        public_id, original_hash = teacher.public_id, teacher.password_hash
    login(client, "admin@example.com")

    client.post(
        f"/admin/teachers/{public_id}/edit",
        data={"full_name": "After", "email": "preservet@example.com"},
        follow_redirects=True,
    )

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.password_hash == original_hash


def test_edit_name_only_does_not_bump_auth_version(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="nameonly@example.com", full_name="Name Only Before")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    client.post(
        f"/admin/teachers/{public_id}/edit",
        data={"full_name": "Name Only After", "email": "nameonly@example.com"},
        follow_redirects=True,
    )

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.auth_version == 1


def test_edit_email_change_invalidates_existing_session():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher(email="willchanget@example.com")
            public_id = teacher.public_id

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        teacher_client = app.test_client()
        login(teacher_client, "willchanget@example.com")
        # Session alive but wrong role -> 403, proving the session currently resolves.
        assert teacher_client.get("/admin/teachers").status_code == 403

        admin_client.post(
            f"/admin/teachers/{public_id}/edit",
            data={"full_name": "Teacher One", "email": "changedt@example.com"},
            follow_redirects=True,
        )

        # Old session cookie no longer resolves to any user -> redirected to login.
        resp = teacher_client.get("/admin/teachers")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_csrf_enforced_on_edit():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher(email="csrfeditt@example.com")
            public_id = teacher.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                f"/admin/teachers/{public_id}/edit",
                data={"full_name": "No CSRF Edit", "email": "csrfeditt@example.com"},
            )
            assert resp.status_code == 400
            teacher = User.query.filter_by(public_id=public_id).first()
            assert teacher.full_name != "No CSRF Edit"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_edit_integrity_error_rolls_back_safely(app, client, monkeypatch):
    from app.blueprints.admin.forms import TeacherEditForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("blockedt@example.com", UserRole.STUDENT.value)
        teacher = _make_teacher(email="editracet@example.com", full_name="Original")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    monkeypatch.setattr(TeacherEditForm, "validate_email", lambda self, field: None)

    resp = client.post(
        f"/admin/teachers/{public_id}/edit",
        data={"full_name": "Should Not Save", "email": "blockedt@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert resp.status_code != 500
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.full_name == "Original"
        assert teacher.email == "editracet@example.com"


# ======================================================================
# STATUS MANAGEMENT
# ======================================================================


def test_active_teacher_can_be_suspended(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/teachers/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.status == UserStatus.SUSPENDED.value


def test_suspended_teacher_can_be_reactivated(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(status=UserStatus.SUSPENDED.value)
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/teachers/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.status == UserStatus.ACTIVE.value


def test_toggle_status_route_is_post_only(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    assert client.get(f"/admin/teachers/{public_id}/toggle-status").status_code == 405


def test_toggle_status_csrf_enforced():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher()
            public_id = teacher.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(f"/admin/teachers/{public_id}/toggle-status")
            assert resp.status_code == 400
            teacher = User.query.filter_by(public_id=public_id).first()
            assert teacher.status == UserStatus.ACTIVE.value
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_toggle_status_anonymous_and_non_admin_denied(app, client):
    with app.app_context():
        make_user("studenty@example.com", UserRole.STUDENT.value)
        teacher = _make_teacher()
        public_id = teacher.public_id

    assert client.post(f"/admin/teachers/{public_id}/toggle-status").status_code == 302

    login(client, "studenty@example.com")
    assert client.post(f"/admin/teachers/{public_id}/toggle-status").status_code == 403


def test_toggle_status_non_teacher_public_id_returns_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = make_user("studentz@example.com", UserRole.STUDENT.value)
        student_public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/teachers/{student_public_id}/toggle-status")
    assert resp.status_code == 404
    with app.app_context():
        student = User.query.filter_by(email="studentz@example.com").first()
        assert student.status == UserStatus.ACTIVE.value


def test_suspend_invalidates_existing_session():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher(email="tosuspendt@example.com")
            public_id = teacher.public_id

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        teacher_client = app.test_client()
        login(teacher_client, "tosuspendt@example.com")
        assert teacher_client.get("/admin/teachers").status_code == 403  # session alive

        admin_client.post(f"/admin/teachers/{public_id}/toggle-status", follow_redirects=True)

        resp = teacher_client.get("/admin/teachers")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_old_session_remains_invalid_after_reactivation():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher(email="reactivatet@example.com")
            public_id = teacher.public_id

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        teacher_client = app.test_client()
        login(teacher_client, "reactivatet@example.com")
        assert teacher_client.get("/admin/teachers").status_code == 403

        # suspend
        admin_client.post(f"/admin/teachers/{public_id}/toggle-status", follow_redirects=True)
        # reactivate
        admin_client.post(f"/admin/teachers/{public_id}/toggle-status", follow_redirects=True)

        with app.app_context():
            teacher = User.query.filter_by(public_id=public_id).first()
            assert teacher.status == UserStatus.ACTIVE.value

        # The browser that was logged in before the suspend/reactivate cycle
        # must NOT be silently revived -- it has to log in again.
        resp = teacher_client.get("/admin/teachers")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

        # A fresh login works fine now that the account is active again.
        fresh_client = app.test_client()
        login(fresh_client, "reactivatet@example.com")
        assert fresh_client.get("/admin/teachers").status_code == 403


def test_suspended_teacher_login_rejected(app, client):
    with app.app_context():
        _make_teacher(email="suspendedlogint@example.com", status=UserStatus.SUSPENDED.value)

    resp = client.post(
        "/auth/login",
        data={"email": "suspendedlogint@example.com", "password": "Sup3rSecret!123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid email or password" in resp.data
    assert client.get("/admin/teachers").status_code == 302


def test_toggle_status_preserves_other_account_information(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="preservemet@example.com", full_name="Preserve Me")
        public_id, original_hash = teacher.public_id, teacher.password_hash
    login(client, "admin@example.com")

    client.post(f"/admin/teachers/{public_id}/toggle-status", follow_redirects=True)

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.full_name == "Preserve Me"
        assert teacher.email == "preservemet@example.com"
        assert teacher.password_hash == original_hash


def test_no_delete_route_or_control(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    assert client.delete(f"/admin/teachers/{public_id}").status_code in (404, 405)
    assert client.post(f"/admin/teachers/{public_id}/delete").status_code == 404

    html = client.get(f"/admin/teachers/{public_id}").get_data(as_text=True)
    assert ">Delete<" not in html


# ======================================================================
# LIST CONTEXT AND LIVE RESULTS (source-aware redirect)
# ======================================================================


def test_suspend_from_unfiltered_list_redirects_to_teachers_list(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "list", "q": "", "status": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/teachers"


def test_suspend_from_filtered_list_preserves_q_and_status(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="filteredt@example.com", full_name="Filtered Target")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "list", "q": "Filtered", "status": "active"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("/admin/teachers?")
    assert "q=Filtered" in location
    assert "status=active" in location


def test_reactivate_from_filtered_list_preserves_q_and_status(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="reactivate.filteredt@example.com", full_name="Reactivate Target",
                                 status=UserStatus.SUSPENDED.value)
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "list", "q": "Reactivate", "status": "suspended"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("/admin/teachers?")
    assert "q=Reactivate" in location
    assert "status=suspended" in location


def test_invalid_status_not_carried_into_list_redirect(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "list", "q": "", "status": "not-a-real-status"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/teachers"
    assert "not-a-real-status" not in resp.headers["Location"]


def test_special_characters_in_q_are_safely_encoded_in_redirect(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "list", "q": "<script>&danger", "status": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "<script>" not in location
    assert "&danger" not in location or "%26danger" in location or "&amp;danger" in location

    follow_resp = client.get(location)
    assert follow_resp.status_code == 200


def test_suspend_from_detail_page_still_redirects_to_detail(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    # No "source" field at all -- exactly what detail.html's form sends.
    resp = client.post(f"/admin/teachers/{public_id}/toggle-status", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/admin/teachers/{public_id}"


def test_unrecognized_source_value_falls_back_to_detail_redirect(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "somewhere-else", "q": "ignored", "status": "active"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/admin/teachers/{public_id}"


def test_no_open_redirect_via_arbitrary_source_value(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "https://evil.example/", "q": "", "status": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location == f"/admin/teachers/{public_id}"
    assert "evil.example" not in location


def test_flash_message_visible_after_following_list_redirect(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(full_name="Flash Target")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "list", "q": "", "status": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Flash Target" in resp.data
    assert b"is now suspended" in resp.data


def test_toggle_status_csrf_still_enforced_with_source_list_fields():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher()
            public_id = teacher.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                f"/admin/teachers/{public_id}/toggle-status",
                data={"source": "list", "q": "abd", "status": "active"},
            )
            assert resp.status_code == 400
            teacher = User.query.filter_by(public_id=public_id).first()
            assert teacher.status == UserStatus.ACTIVE.value
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_toggle_status_authorization_unaffected_by_source_list_fields(app, client):
    with app.app_context():
        make_user("studentw@example.com", UserRole.STUDENT.value)
        teacher = _make_teacher()
        public_id = teacher.public_id

    anon_resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "list", "q": "abd", "status": "active"},
    )
    assert anon_resp.status_code == 302

    login(client, "studentw@example.com")
    student_resp = client.post(
        f"/admin/teachers/{public_id}/toggle-status",
        data={"source": "list", "q": "abd", "status": "active"},
    )
    assert student_resp.status_code == 403


def test_toggle_status_still_invalidates_sessions_via_list_redirect_path():
    with _isolated_app() as isolated_app:
        with isolated_app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher(email="listpath.sessiont@example.com")
            public_id = teacher.public_id

        admin_client = isolated_app.test_client()
        login(admin_client, "admin@example.com")

        teacher_client = isolated_app.test_client()
        login(teacher_client, "listpath.sessiont@example.com")
        assert teacher_client.get("/admin/teachers").status_code == 403  # session alive

        admin_client.post(
            f"/admin/teachers/{public_id}/toggle-status",
            data={"source": "list", "q": "", "status": ""},
            follow_redirects=True,
        )

        resp = teacher_client.get("/admin/teachers")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_detail_edit_status_actions_and_csrf_appear_in_ajax_fragment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(full_name="Fragment Actions Teacher")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    html = client.get("/admin/teachers", headers={"X-Requested-With": "XMLHttpRequest"}).get_data(as_text=True)
    assert f"/admin/teachers/{public_id}" in html  # detail link
    assert f"/admin/teachers/{public_id}/edit" in html  # edit link
    assert f"/admin/teachers/{public_id}/toggle-status" in html  # status-toggle form
    assert "csrf_token" in html
    assert "Suspend" in html


def test_confirmation_attribute_safely_escapes_quoted_names(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher(email="quotet@example.com", full_name='Weird "Quoted" Teacher')
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert "data-confirm=" in html
    assert '&#34;Quoted&#34;' in html or "&#34;" in html


# ======================================================================
# PASSWORD RESET
# ======================================================================


def test_administrator_can_reset_password(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="resemet@example.com")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": "BrandNewPassword123", "confirm_password": "BrandNewPassword123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert verify_password(teacher.password_hash, "BrandNewPassword123")


def test_reset_password_anonymous_and_non_admin_denied(app, client):
    with app.app_context():
        make_user("studentv@example.com", UserRole.STUDENT.value)
        teacher = _make_teacher()
        public_id = teacher.public_id

    assert client.get(f"/admin/teachers/{public_id}/reset-password").status_code == 302

    login(client, "studentv@example.com")
    assert client.get(f"/admin/teachers/{public_id}/reset-password").status_code == 403


def test_reset_password_csrf_enforced():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher()
            public_id = teacher.public_id
            original_hash = teacher.password_hash

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                f"/admin/teachers/{public_id}/reset-password",
                data={"password": "BrandNewPassword123", "confirm_password": "BrandNewPassword123"},
            )
            assert resp.status_code == 400
            teacher = User.query.filter_by(public_id=public_id).first()
            assert teacher.password_hash == original_hash
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_reset_password_weak_oversized_and_mismatched_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id, original_hash = teacher.public_id, teacher.password_hash
    login(client, "admin@example.com")

    attempts = [
        {"password": "short", "confirm_password": "short"},
        {"password": "a" * 200, "confirm_password": "a" * 200},
        {"password": "GoodEnoughPassword1", "confirm_password": "DoesNotMatch1"},
    ]
    for data in attempts:
        resp = client.post(f"/admin/teachers/{public_id}/reset-password", data=data, follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.password_hash == original_hash


def test_reset_password_exactly_14_characters_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id, original_hash = teacher.public_id, teacher.password_hash
    login(client, "admin@example.com")

    pw = "a" * 14
    resp = client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.password_hash == original_hash


def test_reset_password_exactly_15_characters_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    pw = "a" * 15
    resp = client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert verify_password(teacher.password_hash, pw)


def test_reset_password_exactly_128_characters_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    pw = "a" * 128
    resp = client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert verify_password(teacher.password_hash, pw)


def test_reset_password_exactly_129_characters_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id, original_hash = teacher.public_id, teacher.password_hash
    login(client, "admin@example.com")

    pw = "a" * 129
    resp = client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.password_hash == original_hash


def test_reset_password_with_leading_trailing_spaces_not_stripped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    pw_with_spaces = "  Another Spaced Pass 1  "
    resp = client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": pw_with_spaces, "confirm_password": pw_with_spaces},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert verify_password(teacher.password_hash, pw_with_spaces)
        assert not verify_password(teacher.password_hash, pw_with_spaces.strip())


def test_reset_password_old_password_stops_working():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            _make_teacher(email="oldpwt@example.com")

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        with app.app_context():
            teacher = User.query.filter_by(email="oldpwt@example.com").first()
            public_id = teacher.public_id

        admin_client.post(
            f"/admin/teachers/{public_id}/reset-password",
            data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
            follow_redirects=True,
        )

        old_login_client = app.test_client()
        resp = old_login_client.post(
            "/auth/login",
            data={"email": "oldpwt@example.com", "password": "Sup3rSecret!123"},
            follow_redirects=True,
        )
        assert b"Invalid email or password" in resp.data


def test_reset_password_new_password_works_when_active(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher(email="newpwworkst@example.com")
    login(client, "admin@example.com")

    with app.app_context():
        teacher = User.query.filter_by(email="newpwworkst@example.com").first()
        public_id = teacher.public_id

    client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
        follow_redirects=True,
    )

    new_login_client = app.test_client()
    resp = new_login_client.post(
        "/auth/login",
        data={"email": "newpwworkst@example.com", "password": "CompletelyNewPassword1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] != "/auth/login"


def test_reset_password_invalidates_existing_sessions():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            teacher = _make_teacher(email="resetsessiont@example.com")
            public_id = teacher.public_id

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        teacher_client = app.test_client()
        login(teacher_client, "resetsessiont@example.com")
        assert teacher_client.get("/admin/teachers").status_code == 403

        admin_client.post(
            f"/admin/teachers/{public_id}/reset-password",
            data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
            follow_redirects=True,
        )

        resp = teacher_client.get("/admin/teachers")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_reset_password_does_not_reactivate_suspended_account(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(status=UserStatus.SUSPENDED.value)
        public_id = teacher.public_id
    login(client, "admin@example.com")

    client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
        follow_redirects=True,
    )

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.status == UserStatus.SUSPENDED.value


def test_reset_password_never_appears_in_response_or_flash(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher()
        public_id = teacher.public_id
    login(client, "admin@example.com")

    secret_password = "ThisMustNeverAppear123"
    resp = client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": secret_password, "confirm_password": secret_password},
        follow_redirects=True,
    )
    assert secret_password not in resp.get_data(as_text=True)
    assert secret_password not in resp.request.path


def test_reset_password_preserves_other_teacher_fields(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(email="fieldskeept@example.com", full_name="Field Keeper")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    client.post(
        f"/admin/teachers/{public_id}/reset-password",
        data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
        follow_redirects=True,
    )

    with app.app_context():
        teacher = User.query.filter_by(public_id=public_id).first()
        assert teacher.full_name == "Field Keeper"
        assert teacher.email == "fieldskeept@example.com"
        assert teacher.role == UserRole.TEACHER.value


# ======================================================================
# STUDENT REGRESSION (form reuse must not change Student behavior)
# ======================================================================


def test_student_create_form_still_has_create_student_label(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/students/new").get_data(as_text=True)
    assert 'value="Create Student"' in html


def test_teacher_create_form_has_create_teacher_label(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers/new").get_data(as_text=True)
    assert 'value="Create Teacher"' in html


def test_student_and_teacher_email_uniqueness_is_shared(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher(email="shared@example.com")
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/students/new",
        data={
            "full_name": "Should Fail",
            "email": "shared@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
        },
        follow_redirects=True,
    )
    assert b"already exists" in resp.data.lower()


# ======================================================================
# LISTING REGRESSION / NEW UI ELEMENTS
# ======================================================================


def test_list_page_has_new_teacher_button(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert 'href="/admin/teachers/new"' in html


def test_list_page_has_edit_and_status_actions(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(full_name="Actionable Teacher")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert f"/admin/teachers/{public_id}/edit" in html
    assert f"/admin/teachers/{public_id}/toggle-status" in html
    assert "Suspend" in html


def test_teacher_name_in_list_links_to_detail(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(full_name="Linked Teacher")
        public_id = teacher.public_id
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert f'href="/admin/teachers/{public_id}">Linked Teacher</a>' in html
