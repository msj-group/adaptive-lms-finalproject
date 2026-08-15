import re
from contextlib import contextmanager

from app.extensions import db
from app.models import User, UserRole, UserStatus
from app.security.passwords import verify_password
from tests.conftest import login, make_user

STRONG_PASSWORD = "MyValidPassphrase123"


def _make_student(email="student@example.com", full_name="Student One", status=UserStatus.ACTIVE.value):
    return make_user(email, UserRole.STUDENT.value, status=status, full_name=full_name)


@contextmanager
def _isolated_app():
    """A standalone app/DB where no app context is left pushed while test
    clients make requests.

    The shared `app`/`client` fixtures in conftest.py keep a single app
    context alive for the whole test (the `yield` sits inside their own
    `with app.app_context():` block). That is fine for tests that drive
    one client at a time, but Flask-Login caches the resolved
    `current_user` on `flask.g`, which is scoped to the *app* context --
    so two different test clients issuing requests while that same app
    context stays pushed would incorrectly share one cached identity.
    Tests that need two genuinely independent logged-in sessions at once
    (e.g. proving one user's session survives while another's is
    invalidated) must use their own app context per request instead,
    exactly like a real WSGI server would.
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
# PUBLIC IDENTIFIER
# ======================================================================


def test_every_new_user_receives_a_public_id(app):
    with app.app_context():
        user = make_user("a@example.com", UserRole.STUDENT.value)
        assert user.public_id is not None
        assert len(user.public_id) == 36


def test_public_id_values_are_unique(app):
    with app.app_context():
        u1 = make_user("u1@example.com", UserRole.STUDENT.value)
        u2 = make_user("u2@example.com", UserRole.STUDENT.value)
        assert u1.public_id != u2.public_id


def test_get_id_embeds_auth_version(app):
    with app.app_context():
        user = make_user("u1@example.com", UserRole.STUDENT.value)
        assert user.get_id() == f"{user.id}.1"
        user.bump_auth_version()
        assert user.get_id() == f"{user.id}.2"


def test_internal_numeric_id_not_used_in_student_urls(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    html = resp.get_data(as_text=True)
    with app.app_context():
        student = User.query.filter_by(email="student@example.com").first()
        assert f"/admin/students/{student.public_id}" in html
        # Use a word-boundary check rather than plain substring containment:
        # public_id is a random UUID and could coincidentally start with the
        # same digit(s) as the numeric id (e.g. id=2, public_id="21479aca-..."),
        # which would make a naive substring check for "/admin/students/2"
        # falsely match inside "/admin/students/21479aca-...".
        assert re.search(rf"/admin/students/{student.id}(?!\w)", html) is None


# ======================================================================
# CREATION
# ======================================================================


def test_administrator_can_access_create_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")
    assert client.get("/admin/students/new").status_code == 200


def test_anonymous_denied_create_page(client):
    resp = client.get("/admin/students/new")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_teacher_student_researcher_denied_create_page(app):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        make_user("student2@example.com", UserRole.STUDENT.value)
        make_user("researcher@example.com", UserRole.RESEARCHER.value)

    for email in ["teacher@example.com", "student2@example.com", "researcher@example.com"]:
        role_client = app.test_client()
        login(role_client, email)
        assert role_client.get("/admin/students/new").status_code == 403


def test_valid_student_creation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/students/new",
        data={
            "full_name": "New Student",
            "email": "new.student@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        student = User.query.filter_by(email="new.student@example.com").first()
        assert student is not None
        assert student.role == UserRole.STUDENT.value
        assert student.status == UserStatus.ACTIVE.value


def test_email_is_normalized_to_lowercase(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    client.post(
        "/admin/students/new",
        data={
            "full_name": "Case Student",
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
        "/admin/students/new",
        data={
            "full_name": "Hash Student",
            "email": "hash.student@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
        },
        follow_redirects=True,
    )

    with app.app_context():
        student = User.query.filter_by(email="hash.student@example.com").first()
        assert student.password_hash != STRONG_PASSWORD
        assert student.password_hash.startswith("$argon2")
        assert verify_password(student.password_hash, STRONG_PASSWORD)


def test_password_confirmation_mismatch_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/students/new",
        data={
            "full_name": "Mismatch Student",
            "email": "mismatch@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": "SomethingDifferent123",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"do not match" in resp.data.lower()
    with app.app_context():
        assert User.query.filter_by(email="mismatch@example.com").first() is None


def test_password_below_minimum_length_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/students/new",
        data={
            "full_name": "Short Pw",
            "email": "shortpw@example.com",
            "password": "short1",
            "confirm_password": "short1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="shortpw@example.com").first() is None


def test_password_above_maximum_length_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    too_long = "a" * 200
    resp = client.post(
        "/admin/students/new",
        data={
            "full_name": "Long Pw",
            "email": "longpw@example.com",
            "password": too_long,
            "confirm_password": too_long,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="longpw@example.com").first() is None


def test_password_exactly_14_characters_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw = "a" * 14
    resp = client.post(
        "/admin/students/new",
        data={"full_name": "Boundary14", "email": "boundary14@example.com", "password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="boundary14@example.com").first() is None


def test_password_exactly_15_characters_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw = "a" * 15
    resp = client.post(
        "/admin/students/new",
        data={"full_name": "Boundary15", "email": "boundary15@example.com", "password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(email="boundary15@example.com").first()
        assert student is not None
        assert verify_password(student.password_hash, pw)


def test_password_exactly_128_characters_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw = "a" * 128
    resp = client.post(
        "/admin/students/new",
        data={"full_name": "Boundary128", "email": "boundary128@example.com", "password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(email="boundary128@example.com").first()
        assert student is not None
        assert verify_password(student.password_hash, pw)


def test_password_exactly_129_characters_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw = "a" * 129
    resp = client.post(
        "/admin/students/new",
        data={"full_name": "Boundary129", "email": "boundary129@example.com", "password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="boundary129@example.com").first() is None


def test_password_with_leading_trailing_spaces_not_stripped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    pw_with_spaces = "  Spaced Passphrase 123  "  # spaces are significant, not decorative
    resp = client.post(
        "/admin/students/new",
        data={
            "full_name": "Spaced Pw",
            "email": "spacedpw@example.com",
            "password": pw_with_spaces,
            "confirm_password": pw_with_spaces,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(email="spacedpw@example.com").first()
        assert student is not None
        assert verify_password(student.password_hash, pw_with_spaces)
        assert not verify_password(student.password_hash, pw_with_spaces.strip())


def test_duplicate_email_rejected_against_every_role(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("existing.teacher@example.com", UserRole.TEACHER.value)
        make_user("existing.researcher@example.com", UserRole.RESEARCHER.value)
        _make_student(email="existing.student@example.com")
    login(client, "admin@example.com")

    for existing_email in [
        "admin@example.com",
        "existing.teacher@example.com",
        "existing.researcher@example.com",
        "existing.student@example.com",
    ]:
        resp = client.post(
            "/admin/students/new",
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
        "/admin/students/new",
        data={
            "full_name": "Tamper Student",
            "email": "tamper@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
            "role": "administrator",
            "status": "suspended",
            "id": "999999",
            "public_id": "11111111-1111-1111-1111-111111111111",
            "auth_version": "999",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        student = User.query.filter_by(email="tamper@example.com").first()
        assert student is not None
        assert student.role == UserRole.STUDENT.value
        assert student.status == UserStatus.ACTIVE.value
        assert student.id != 999999
        assert student.public_id != "11111111-1111-1111-1111-111111111111"
        assert student.auth_version == 1


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
                "/admin/students/new",
                data={
                    "full_name": "No CSRF",
                    "email": "nocsrf@example.com",
                    "password": STRONG_PASSWORD,
                    "confirm_password": STRONG_PASSWORD,
                },
            )
            assert resp.status_code == 400
            assert User.query.filter_by(email="nocsrf@example.com").first() is None
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_duplicate_email_race_condition_handled_without_500(app, client, monkeypatch):
    from app.blueprints.admin.forms import StudentCreateForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student(email="race@example.com")
    login(client, "admin@example.com")

    # Neutralize the form-level pre-check so the request reaches the
    # database's own unique constraint, exercising the IntegrityError path.
    monkeypatch.setattr(StudentCreateForm, "validate_email", lambda self, field: None)

    resp = client.post(
        "/admin/students/new",
        data={
            "full_name": "Race Student",
            "email": "race@example.com",
            "password": STRONG_PASSWORD,
            "confirm_password": STRONG_PASSWORD,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert resp.status_code != 500
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert User.query.filter_by(email="race@example.com").count() == 1


# ======================================================================
# DETAIL
# ======================================================================


def test_detail_shows_correct_student(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="detail@example.com", full_name="Detail Student")
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/students/{public_id}")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Detail Student" in html
    assert "detail@example.com" in html


def test_detail_does_not_expose_sensitive_or_internal_fields(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="secure@example.com")
        public_id, student_id, password_hash = student.public_id, student.id, student.password_hash
    login(client, "admin@example.com")

    html = client.get(f"/admin/students/{public_id}").get_data(as_text=True)
    assert password_hash not in html
    assert "$argon2" not in html
    assert f">{student_id}<" not in html
    assert "auth_version" not in html.lower()


def test_detail_nonexistent_public_id_returns_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")
    resp = client.get("/admin/students/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_detail_non_student_public_id_returns_404(app, client):
    with app.app_context():
        admin = make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = make_user("teacher@example.com", UserRole.TEACHER.value)
        admin_public_id, teacher_public_id = admin.public_id, teacher.public_id
    login(client, "admin@example.com")

    assert client.get(f"/admin/students/{admin_public_id}").status_code == 404
    assert client.get(f"/admin/students/{teacher_public_id}").status_code == 404


def test_detail_anonymous_and_non_admin_denied(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        student = _make_student()
        public_id = student.public_id

    resp = client.get(f"/admin/students/{public_id}")
    assert resp.status_code == 302

    login(client, "teacher@example.com")
    assert client.get(f"/admin/students/{public_id}").status_code == 403


def test_detail_xss_in_name_is_escaped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="xss@example.com", full_name="<script>alert(1)</script>")
        public_id = student.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/students/{public_id}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ======================================================================
# EDIT
# ======================================================================


def test_edit_updates_name_and_email(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="old@example.com", full_name="Old Name")
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/edit",
        data={"full_name": "New Name", "email": "new@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.full_name == "New Name"
        assert student.email == "new@example.com"


def test_edit_with_unchanged_email_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="same@example.com", full_name="Name A")
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/edit",
        data={"full_name": "Name B", "email": "same@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" not in resp.data.lower()
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.full_name == "Name B"
        assert student.auth_version == 1  # unchanged email -> no session bump


def test_edit_rejects_duplicate_email_globally(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("teacher.taken@example.com", UserRole.TEACHER.value)
        student = _make_student(email="student.own@example.com")
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/edit",
        data={"full_name": "Student", "email": "teacher.taken@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.email == "student.own@example.com"


def test_edit_cannot_tamper_role_status_password_or_public_id(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="tamper2@example.com")
        public_id = student.public_id
        original_password_hash = student.password_hash
    login(client, "admin@example.com")

    client.post(
        f"/admin/students/{public_id}/edit",
        data={
            "full_name": "Tampered",
            "email": "tamper2@example.com",
            "role": "administrator",
            "status": "suspended",
            "password_hash": "hacked",
            "public_id": "22222222-2222-2222-2222-222222222222",
        },
        follow_redirects=True,
    )

    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student is not None
        assert student.role == UserRole.STUDENT.value
        assert student.status == UserStatus.ACTIVE.value
        assert student.password_hash == original_password_hash


def test_edit_preserves_password_hash_when_only_name_changes(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="preserve@example.com", full_name="Before")
        public_id, original_hash = student.public_id, student.password_hash
    login(client, "admin@example.com")

    client.post(
        f"/admin/students/{public_id}/edit",
        data={"full_name": "After", "email": "preserve@example.com"},
        follow_redirects=True,
    )

    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.password_hash == original_hash


def test_edit_email_change_invalidates_existing_session():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            student = _make_student(email="willchange@example.com")
            public_id = student.public_id

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        student_client = app.test_client()
        login(student_client, "willchange@example.com")
        # Session alive but wrong role -> 403, proving the session currently resolves.
        assert student_client.get("/admin/students").status_code == 403

        admin_client.post(
            f"/admin/students/{public_id}/edit",
            data={"full_name": "Student One", "email": "changed@example.com"},
            follow_redirects=True,
        )

        # Old session cookie no longer resolves to any user -> redirected to login.
        resp = student_client.get("/admin/students")
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
            student = _make_student(email="csrfedit@example.com")
            public_id = student.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                f"/admin/students/{public_id}/edit",
                data={"full_name": "No CSRF Edit", "email": "csrfedit@example.com"},
            )
            assert resp.status_code == 400
            student = User.query.filter_by(public_id=public_id).first()
            assert student.full_name != "No CSRF Edit"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_edit_integrity_error_rolls_back_safely(app, client, monkeypatch):
    from app.blueprints.admin.forms import StudentEditForm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("blocked@example.com", UserRole.TEACHER.value)
        student = _make_student(email="editrace@example.com", full_name="Original")
        public_id = student.public_id
    login(client, "admin@example.com")

    monkeypatch.setattr(StudentEditForm, "validate_email", lambda self, field: None)

    resp = client.post(
        f"/admin/students/{public_id}/edit",
        data={"full_name": "Should Not Save", "email": "blocked@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert resp.status_code != 500
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.full_name == "Original"
        assert student.email == "editrace@example.com"


# ======================================================================
# STATUS MANAGEMENT
# ======================================================================


def test_active_student_can_be_suspended(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.status == UserStatus.SUSPENDED.value


def test_suspended_student_can_be_reactivated(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(status=UserStatus.SUSPENDED.value)
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.status == UserStatus.ACTIVE.value


# ======================================================================
# NAVIGATION AFTER SUSPEND/REACTIVATE (source-aware redirect)
# ======================================================================


def test_suspend_from_unfiltered_list_redirects_to_students_list(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "list", "q": "", "status": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/students"


def test_suspend_from_filtered_list_preserves_q_and_status(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="filtered@example.com", full_name="Filtered Target")
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "list", "q": "Filtered", "status": "active"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("/admin/students?")
    assert "q=Filtered" in location
    assert "status=active" in location


def test_reactivate_from_filtered_list_preserves_q_and_status(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="reactivate.filtered@example.com", full_name="Reactivate Target",
                                 status=UserStatus.SUSPENDED.value)
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "list", "q": "Reactivate", "status": "suspended"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("/admin/students?")
    assert "q=Reactivate" in location
    assert "status=suspended" in location


def test_invalid_status_not_carried_into_list_redirect(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "list", "q": "", "status": "not-a-real-status"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/students"
    assert "not-a-real-status" not in resp.headers["Location"]


def test_special_characters_in_q_are_safely_encoded_in_redirect(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "list", "q": "<script>&danger", "status": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    # The raw special characters must never appear unescaped in the
    # Location header (that would risk header/response splitting or a
    # malformed redirect); url_for() must have percent-encoded them.
    assert "<script>" not in location
    assert "&danger" not in location or "%26danger" in location or "&amp;danger" in location

    follow_resp = client.get(location)
    assert follow_resp.status_code == 200


def test_suspend_from_detail_page_still_redirects_to_detail(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    # No "source" field at all -- exactly what detail.html's form sends.
    resp = client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/admin/students/{public_id}"


def test_reactivate_from_detail_page_still_redirects_to_detail(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(status=UserStatus.SUSPENDED.value)
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/admin/students/{public_id}"


def test_unrecognized_source_value_falls_back_to_detail_redirect(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "somewhere-else", "q": "ignored", "status": "active"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/admin/students/{public_id}"


def test_flash_message_visible_after_following_list_redirect(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(full_name="Flash Target")
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "list", "q": "", "status": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Flash Target" in resp.data
    assert b"is now suspended" in resp.data


def test_toggle_status_route_is_post_only(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    assert client.get(f"/admin/students/{public_id}/toggle-status").status_code == 405


def test_toggle_status_csrf_enforced():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            student = _make_student()
            public_id = student.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(f"/admin/students/{public_id}/toggle-status")
            assert resp.status_code == 400
            student = User.query.filter_by(public_id=public_id).first()
            assert student.status == UserStatus.ACTIVE.value
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_toggle_status_anonymous_and_non_admin_denied(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        student = _make_student()
        public_id = student.public_id

    assert client.post(f"/admin/students/{public_id}/toggle-status").status_code == 302

    login(client, "teacher@example.com")
    assert client.post(f"/admin/students/{public_id}/toggle-status").status_code == 403


def test_toggle_status_csrf_still_enforced_with_source_list_fields():
    """The new source/q/status hidden fields must not create a way to skip
    CSRF -- a request carrying them but no valid csrf_token must still be
    rejected exactly like before.
    """
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            student = _make_student()
            public_id = student.public_id

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                f"/admin/students/{public_id}/toggle-status",
                data={"source": "list", "q": "abd", "status": "active"},
            )
            assert resp.status_code == 400
            student = User.query.filter_by(public_id=public_id).first()
            assert student.status == UserStatus.ACTIVE.value
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_toggle_status_authorization_unaffected_by_source_list_fields(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        student = _make_student()
        public_id = student.public_id

    anon_resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "list", "q": "abd", "status": "active"},
    )
    assert anon_resp.status_code == 302

    login(client, "teacher@example.com")
    teacher_resp = client.post(
        f"/admin/students/{public_id}/toggle-status",
        data={"source": "list", "q": "abd", "status": "active"},
    )
    assert teacher_resp.status_code == 403


def test_toggle_status_still_invalidates_sessions_via_list_redirect_path():
    """Session invalidation (auth_version bump) must keep working
    regardless of whether the action was triggered from the list (new
    redirect path) or the detail page (existing path).
    """
    with _isolated_app() as isolated_app:
        with isolated_app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            student = _make_student(email="listpath.session@example.com")
            public_id = student.public_id

        admin_client = isolated_app.test_client()
        login(admin_client, "admin@example.com")

        student_client = isolated_app.test_client()
        login(student_client, "listpath.session@example.com")
        assert student_client.get("/admin/students").status_code == 403  # session alive

        admin_client.post(
            f"/admin/students/{public_id}/toggle-status",
            data={"source": "list", "q": "", "status": ""},
            follow_redirects=True,
        )

        resp = student_client.get("/admin/students")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_toggle_status_non_student_public_id_returns_404(app, client):
    with app.app_context():
        admin = make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = make_user("teacher@example.com", UserRole.TEACHER.value)
        teacher_public_id = teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(f"/admin/students/{teacher_public_id}/toggle-status")
    assert resp.status_code == 404
    with app.app_context():
        teacher = User.query.filter_by(email="teacher@example.com").first()
        assert teacher.status == UserStatus.ACTIVE.value


def test_suspend_invalidates_existing_session():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            student = _make_student(email="tosuspend@example.com")
            public_id = student.public_id

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        student_client = app.test_client()
        login(student_client, "tosuspend@example.com")
        assert student_client.get("/admin/students").status_code == 403  # session alive

        admin_client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=True)

        resp = student_client.get("/admin/students")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_old_session_remains_invalid_after_reactivation():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            student = _make_student(email="reactivate@example.com")
            public_id = student.public_id

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        student_client = app.test_client()
        login(student_client, "reactivate@example.com")
        assert student_client.get("/admin/students").status_code == 403

        # suspend
        admin_client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=True)
        # reactivate
        admin_client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=True)

        with app.app_context():
            student = User.query.filter_by(public_id=public_id).first()
            assert student.status == UserStatus.ACTIVE.value

        # The browser that was logged in before the suspend/reactivate cycle
        # must NOT be silently revived -- it has to log in again.
        resp = student_client.get("/admin/students")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

        # A fresh login works fine now that the account is active again.
        fresh_client = app.test_client()
        login(fresh_client, "reactivate@example.com")
        assert fresh_client.get("/admin/students").status_code == 403


def test_suspended_student_login_rejected(app, client):
    with app.app_context():
        _make_student(email="suspendedlogin@example.com", status=UserStatus.SUSPENDED.value)

    resp = client.post(
        "/auth/login",
        data={"email": "suspendedlogin@example.com", "password": "Sup3rSecret!123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid email or password" in resp.data
    assert client.get("/admin/students").status_code == 302


def test_toggle_status_preserves_other_account_information(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="preserveme@example.com", full_name="Preserve Me")
        public_id, original_hash = student.public_id, student.password_hash
    login(client, "admin@example.com")

    client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=True)

    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.full_name == "Preserve Me"
        assert student.email == "preserveme@example.com"
        assert student.password_hash == original_hash


# ======================================================================
# PASSWORD RESET
# ======================================================================


def test_administrator_can_reset_password(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="resetme@example.com")
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": "BrandNewPassword123", "confirm_password": "BrandNewPassword123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert verify_password(student.password_hash, "BrandNewPassword123")


def test_reset_password_anonymous_and_non_admin_denied(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        student = _make_student()
        public_id = student.public_id

    assert client.get(f"/admin/students/{public_id}/reset-password").status_code == 302

    login(client, "teacher@example.com")
    assert client.get(f"/admin/students/{public_id}/reset-password").status_code == 403


def test_reset_password_csrf_enforced():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            student = _make_student()
            public_id = student.public_id
            original_hash = student.password_hash

            token = _get_csrf_token(client, "/auth/login")
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )
            resp = client.post(
                f"/admin/students/{public_id}/reset-password",
                data={"password": "BrandNewPassword123", "confirm_password": "BrandNewPassword123"},
            )
            assert resp.status_code == 400
            student = User.query.filter_by(public_id=public_id).first()
            assert student.password_hash == original_hash
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_reset_password_weak_oversized_and_mismatched_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id, original_hash = student.public_id, student.password_hash
    login(client, "admin@example.com")

    attempts = [
        {"password": "short", "confirm_password": "short"},
        {"password": "a" * 200, "confirm_password": "a" * 200},
        {"password": "GoodEnoughPassword1", "confirm_password": "DoesNotMatch1"},
    ]
    for data in attempts:
        resp = client.post(f"/admin/students/{public_id}/reset-password", data=data, follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.password_hash == original_hash


def test_reset_password_exactly_14_characters_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id, original_hash = student.public_id, student.password_hash
    login(client, "admin@example.com")

    pw = "a" * 14
    resp = client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.password_hash == original_hash


def test_reset_password_exactly_15_characters_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    pw = "a" * 15
    resp = client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert verify_password(student.password_hash, pw)


def test_reset_password_exactly_128_characters_accepted(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    pw = "a" * 128
    resp = client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert verify_password(student.password_hash, pw)


def test_reset_password_exactly_129_characters_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id, original_hash = student.public_id, student.password_hash
    login(client, "admin@example.com")

    pw = "a" * 129
    resp = client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": pw, "confirm_password": pw},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.password_hash == original_hash


def test_reset_password_with_leading_trailing_spaces_not_stripped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    pw_with_spaces = "  Another Spaced Pass 1  "
    resp = client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": pw_with_spaces, "confirm_password": pw_with_spaces},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert verify_password(student.password_hash, pw_with_spaces)
        assert not verify_password(student.password_hash, pw_with_spaces.strip())


def test_reset_password_old_password_stops_working():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            _make_student(email="oldpw@example.com")

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        with app.app_context():
            student = User.query.filter_by(email="oldpw@example.com").first()
            public_id = student.public_id

        admin_client.post(
            f"/admin/students/{public_id}/reset-password",
            data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
            follow_redirects=True,
        )

        old_login_client = app.test_client()
        resp = old_login_client.post(
            "/auth/login",
            data={"email": "oldpw@example.com", "password": "Sup3rSecret!123"},
            follow_redirects=True,
        )
        assert b"Invalid email or password" in resp.data


def test_reset_password_new_password_works_when_active(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_student(email="newpwworks@example.com")
    login(client, "admin@example.com")

    with app.app_context():
        student = User.query.filter_by(email="newpwworks@example.com").first()
        public_id = student.public_id

    client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
        follow_redirects=True,
    )

    new_login_client = app.test_client()
    resp = new_login_client.post(
        "/auth/login",
        data={"email": "newpwworks@example.com", "password": "CompletelyNewPassword1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] != "/auth/login"


def test_reset_password_invalidates_existing_sessions():
    with _isolated_app() as app:
        with app.app_context():
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            student = _make_student(email="resetsession@example.com")
            public_id = student.public_id

        admin_client = app.test_client()
        login(admin_client, "admin@example.com")

        student_client = app.test_client()
        login(student_client, "resetsession@example.com")
        assert student_client.get("/admin/students").status_code == 403

        admin_client.post(
            f"/admin/students/{public_id}/reset-password",
            data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
            follow_redirects=True,
        )

        resp = student_client.get("/admin/students")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_reset_password_does_not_reactivate_suspended_account(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(status=UserStatus.SUSPENDED.value)
        public_id = student.public_id
    login(client, "admin@example.com")

    client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
        follow_redirects=True,
    )

    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.status == UserStatus.SUSPENDED.value


def test_reset_password_never_appears_in_response_or_flash(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student()
        public_id = student.public_id
    login(client, "admin@example.com")

    secret_password = "ThisMustNeverAppear123"
    resp = client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": secret_password, "confirm_password": secret_password},
        follow_redirects=True,
    )
    assert secret_password not in resp.get_data(as_text=True)
    assert secret_password not in resp.request.path


def test_reset_password_preserves_other_student_fields(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="fieldskeep@example.com", full_name="Field Keeper")
        public_id = student.public_id
    login(client, "admin@example.com")

    client.post(
        f"/admin/students/{public_id}/reset-password",
        data={"password": "CompletelyNewPassword1", "confirm_password": "CompletelyNewPassword1"},
        follow_redirects=True,
    )

    with app.app_context():
        student = User.query.filter_by(public_id=public_id).first()
        assert student.full_name == "Field Keeper"
        assert student.email == "fieldskeep@example.com"
        assert student.role == UserRole.STUDENT.value


# ======================================================================
# LISTING REGRESSION / NEW UI ELEMENTS
# ======================================================================


def test_list_page_has_new_student_button(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/students").get_data(as_text=True)
    assert 'href="/admin/students/new"' in html


def test_list_page_has_edit_and_status_actions(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(full_name="Actionable Student")
        public_id = student.public_id
    login(client, "admin@example.com")

    html = client.get("/admin/students").get_data(as_text=True)
    assert f"/admin/students/{public_id}/edit" in html
    assert f"/admin/students/{public_id}/toggle-status" in html
    assert "Suspend" in html
