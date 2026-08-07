import pytest

from app import create_app
from app.extensions import db
from app.models.user import User, UserRole, UserStatus
from app.security.passwords import hash_password, verify_password


def test_app_factory_creates_app():
    app = create_app("testing")
    assert app is not None
    assert app.config["TESTING"] is True


def test_health_route(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_design_system_page_loads(client):
    resp = client.get("/design-system/")
    assert resp.status_code == 200


def test_login_page_loads(client):
    resp = client.get("/auth/login")
    assert resp.status_code == 200
    assert b"Login" in resp.data


def test_404_page(client):
    resp = client.get("/this-page-does-not-exist")
    assert resp.status_code == 404


def test_password_hashing_roundtrip():
    hashed = hash_password("Sup3rSecret!123")
    assert hashed != "Sup3rSecret!123"
    assert verify_password(hashed, "Sup3rSecret!123") is True
    assert verify_password(hashed, "wrong-password") is False


def test_user_model_rejects_invalid_role(app):
    with app.app_context():
        with pytest.raises(ValueError):
            User(email="x@example.com", password_hash="h", full_name="X", role="not-a-role")


def test_login_flow_success_and_redirect(app, client):
    with app.app_context():
        user = User(
            email="admin@example.com",
            password_hash=hash_password("Sup3rSecret!123"),
            full_name="Admin",
            role=UserRole.ADMINISTRATOR.value,
            status=UserStatus.ACTIVE.value,
        )
        db.session.add(user)
        db.session.commit()

    resp = client.post(
        "/auth/login",
        data={"email": "admin@example.com", "password": "Sup3rSecret!123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_login_flow_generic_invalid_message(client):
    resp = client.post(
        "/auth/login",
        data={"email": "nobody@example.com", "password": "wrong"},
        follow_redirects=True,
    )
    assert b"Invalid email or password" in resp.data


def test_suspended_user_cannot_login(app, client):
    with app.app_context():
        user = User(
            email="suspended@example.com",
            password_hash=hash_password("Sup3rSecret!123"),
            full_name="Suspended",
            role=UserRole.STUDENT.value,
            status=UserStatus.SUSPENDED.value,
        )
        db.session.add(user)
        db.session.commit()

    resp = client.post(
        "/auth/login",
        data={"email": "suspended@example.com", "password": "Sup3rSecret!123"},
        follow_redirects=True,
    )
    assert b"Invalid email or password" in resp.data


def test_csrf_protection_blocks_missing_token():
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        db.create_all()
        try:
            resp = client.post(
                "/auth/login", data={"email": "a@example.com", "password": "whatever"}
            )
            assert resp.status_code == 400
        finally:
            db.session.remove()
            db.drop_all()
