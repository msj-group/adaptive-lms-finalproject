from app.extensions import db
from app.models import Level, UserRole
from tests.conftest import login, make_user


def _create_level(name, code=None, display_order=None):
    if display_order is None:
        max_order = db.session.query(db.func.max(Level.display_order)).scalar()
        display_order = (max_order + 1) if max_order is not None else 0
    level = Level(name=name, code=code, display_order=display_order)
    db.session.add(level)
    db.session.commit()
    return level


def test_administrator_can_list_levels(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _create_level("Level 1")
    login(client, "admin@example.com")

    resp = client.get("/admin/levels")
    assert resp.status_code == 200
    assert b"Level 1" in resp.data


def test_administrator_can_create_a_level(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/levels/new", data={"name": "Beginner", "code": "B1"}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        level = Level.query.filter_by(name="Beginner").first()
        assert level is not None
        assert level.code == "B1"
        assert level.status == "active"
        assert level.display_order == 0


def test_create_ignores_client_supplied_display_order(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _create_level("Existing Level")
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/levels/new",
        data={"name": "Tampered Order", "code": "", "display_order": "999"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        level = Level.query.filter_by(name="Tampered Order").first()
        assert level.display_order == 1


def test_administrator_can_edit_a_level(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Old Name", code="OLD")
        public_id = level.public_id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/levels/{public_id}/edit",
        data={"name": "New Name", "code": "NEW"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        level = Level.query.filter_by(public_id=public_id).first()
        assert level.name == "New Name"
        assert level.code == "NEW"


def test_administrator_can_deactivate_a_level(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level A")
        public_id = level.public_id

    login(client, "admin@example.com")
    resp = client.post(f"/admin/levels/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        level = Level.query.filter_by(public_id=public_id).first()
        assert level.status == "archived"


def test_display_ordering_move_up_and_down(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        first = _create_level("First")
        second = _create_level("Second")
        third = _create_level("Third")
        second_id = second.public_id

    login(client, "admin@example.com")

    resp = client.post(f"/admin/levels/{second_id}/move-up", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        ordered = Level.query.order_by(Level.display_order, Level.id).all()
        assert [lvl.name for lvl in ordered] == ["Second", "First", "Third"]

    resp = client.post(f"/admin/levels/{second_id}/move-down", follow_redirects=True)
    with app.app_context():
        ordered = Level.query.order_by(Level.display_order, Level.id).all()
        assert [lvl.name for lvl in ordered] == ["First", "Second", "Third"]


def test_invalid_order_move_is_safely_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        first = _create_level("Only First")
        last = _create_level("Only Last")
        first_id = first.public_id
        last_id = last.public_id

    login(client, "admin@example.com")

    resp = client.post(f"/admin/levels/{first_id}/move-up", follow_redirects=True)
    assert resp.status_code == 200
    assert b"already first" in resp.data.lower()

    resp = client.post(f"/admin/levels/{last_id}/move-down", follow_redirects=True)
    assert resp.status_code == 200
    assert b"already last" in resp.data.lower()

    with app.app_context():
        ordered = Level.query.order_by(Level.display_order, Level.id).all()
        assert [lvl.name for lvl in ordered] == ["Only First", "Only Last"]


def test_duplicate_name_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _create_level("Duplicate Name")
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/levels/new", data={"name": "Duplicate Name", "code": ""}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert Level.query.filter_by(name="Duplicate Name").count() == 1


def test_duplicate_code_is_rejected_but_multiple_blank_codes_allowed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _create_level("With Code", code="DUP")
        _create_level("No Code One", code=None)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/levels/new", data={"name": "Another With Code", "code": "DUP"}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    resp = client.post(
        "/admin/levels/new", data={"name": "No Code Two", "code": ""}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        assert Level.query.filter(Level.code.is_(None)).count() == 2


def test_anonymous_access_denied(client):
    resp = client.get("/admin/levels")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_student_denied(app, client):
    with app.app_context():
        make_user("student@example.com", UserRole.STUDENT.value)
    login(client, "student@example.com")
    assert client.get("/admin/levels").status_code == 403


def test_teacher_denied(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    assert client.get("/admin/levels").status_code == 403


def test_researcher_denied(app, client):
    with app.app_context():
        make_user("researcher@example.com", UserRole.RESEARCHER.value)
    login(client, "researcher@example.com")
    assert client.get("/admin/levels").status_code == 403


def test_csrf_enforced_on_create():
    from app import create_app
    import re

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            resp = client.post("/admin/levels/new", data={"name": "No CSRF Level", "code": ""})
            assert resp.status_code == 400
            assert Level.query.filter_by(name="No CSRF Level").first() is None
        finally:
            db.session.remove()
            db.drop_all()
