import pytest

from app import create_app
from app.extensions import db
from app.models import User, UserRole, UserStatus
from app.security.passwords import hash_password


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def material_app(tmp_path):
    """Like `app`, but with MATERIAL_STORAGE_ROOT pointed at an isolated
    per-test `tmp_path` directory (M12) -- never the real development
    `storage/materials` tree. Use this (and `material_client`) for any
    test that actually uploads/stores a file."""
    app = create_app("testing", MATERIAL_STORAGE_ROOT=str(tmp_path / "materials"))
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def material_client(material_app):
    return material_app.test_client()


def make_user(email, role, status=UserStatus.ACTIVE.value, password="Sup3rSecret!123", full_name="Test User"):
    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role=role,
        status=status,
    )
    db.session.add(user)
    db.session.commit()
    return user


def login_path(email):
    """The login entry an account signs in through.

    Phase 6: Researcher accounts sign in only through the dedicated research
    login; the LMS login refuses them. Every other role uses ``/auth/login``.
    """
    from flask import has_app_context

    if not has_app_context():
        return "/auth/login"
    role = db.session.query(User.role).filter(User.email == email.strip().lower()).scalar()
    return "/research/login" if role == UserRole.RESEARCHER.value else "/auth/login"


def login(client, email, password="Sup3rSecret!123"):
    return client.post(login_path(email), data={"email": email, "password": password}, follow_redirects=True)
