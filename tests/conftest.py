import pytest

from app import create_app
from app.extensions import db
from app.models import User, UserStatus
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


def login(client, email, password="Sup3rSecret!123"):
    return client.post("/auth/login", data={"email": email, "password": password}, follow_redirects=True)
