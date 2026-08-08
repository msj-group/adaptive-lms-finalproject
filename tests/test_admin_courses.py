from app.extensions import db
from app.models import AcademicStatus, Course, Level, UserRole
from tests.conftest import login, make_user


def _create_level(name, status=AcademicStatus.ACTIVE.value):
    max_order = db.session.query(db.func.max(Level.display_order)).scalar()
    level = Level(name=name, display_order=(max_order + 1) if max_order is not None else 0, status=status)
    db.session.add(level)
    db.session.commit()
    return level


def _create_course(level_id, title, code=None):
    max_order = (
        db.session.query(db.func.max(Course.display_order)).filter(Course.level_id == level_id).scalar()
    )
    course = Course(
        level_id=level_id, title=title, code=code, display_order=(max_order + 1) if max_order is not None else 0
    )
    db.session.add(course)
    db.session.commit()
    return course


def test_administrator_lists_courses(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        _create_course(level.id, "Grammar Basics")
    login(client, "admin@example.com")

    resp = client.get("/admin/courses")
    assert resp.status_code == 200
    assert b"Grammar Basics" in resp.data
    assert b"Level 1" in resp.data


def test_administrator_filters_courses_by_level(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        _create_course(level_a.id, "Course In A")
        _create_course(level_b.id, "Course In B")
        level_a_id = level_a.id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/courses?level_id={level_a_id}")
    assert resp.status_code == 200
    assert b"Course In A" in resp.data
    assert b"Course In B" not in resp.data


def test_administrator_creates_course(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        level_id = level.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/courses/new",
        data={"level_id": level_id, "title": "Speaking Practice", "code": "SP1", "description": "Intro speaking"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        course = Course.query.filter_by(title="Speaking Practice").first()
        assert course is not None
        assert course.level_id == level_id
        assert course.status == "active"
        assert course.display_order == 0


def test_description_over_max_length_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        level_id = level.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/courses/new",
        data={"level_id": level_id, "title": "Too Long", "code": "", "description": "x" * 5001},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Course.query.filter_by(title="Too Long").first() is None


def test_course_references_correct_level(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Target Level")
        level_id = level.id
    login(client, "admin@example.com")

    client.post(
        "/admin/courses/new",
        data={"level_id": level_id, "title": "Reading Skills", "code": "", "description": ""},
        follow_redirects=True,
    )

    with app.app_context():
        course = Course.query.filter_by(title="Reading Skills").first()
        assert course.level.id == level_id
        assert course.level.name == "Target Level"


def test_invalid_level_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/courses/new",
        data={"level_id": "999999", "title": "Orphan Course", "code": "", "description": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Course.query.filter_by(title="Orphan Course").first() is None


def test_editing_a_course_works(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Old Title", code="OLD")
        public_id = course.public_id
        level_id = level.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data={"level_id": level_id, "title": "New Title", "code": "NEW", "description": "Updated"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.title == "New Title"
        assert course.code == "NEW"
        assert course.description == "Updated"


def test_changing_level_works(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Movable Course")
        public_id = course.public_id
        level_b_id = level_b.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data={"level_id": level_b_id, "title": "Movable Course", "code": "", "description": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_b_id
        assert course.display_order == 0


def test_deactivate_works(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Course To Deactivate")
        public_id = course.public_id

    login(client, "admin@example.com")
    resp = client.post(f"/admin/courses/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.status == "archived"


def test_duplicate_title_rejected_within_same_level(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        _create_course(level.id, "Duplicate Title")
        level_id = level.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/courses/new",
        data={"level_id": level_id, "title": "Duplicate Title", "code": "", "description": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert Course.query.filter_by(title="Duplicate Title").count() == 1


def test_same_title_allowed_in_different_levels(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        _create_course(level_a.id, "Shared Title")
        level_b_id = level_b.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/courses/new",
        data={"level_id": level_b_id, "title": "Shared Title", "code": "", "description": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        assert Course.query.filter_by(title="Shared Title").count() == 2


def test_display_ordering_move_up_and_down(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        _create_course(level.id, "First")
        second = _create_course(level.id, "Second")
        _create_course(level.id, "Third")
        second_id = second.public_id
        level_id = level.id

    login(client, "admin@example.com")
    resp = client.post(f"/admin/courses/{second_id}/move-up", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        ordered = Course.query.filter_by(level_id=level_id).order_by(Course.display_order, Course.id).all()
        assert [c.title for c in ordered] == ["Second", "First", "Third"]


def test_anonymous_access_denied(client):
    resp = client.get("/admin/courses")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_student_denied(app, client):
    with app.app_context():
        make_user("student@example.com", UserRole.STUDENT.value)
    login(client, "student@example.com")
    assert client.get("/admin/courses").status_code == 403


def test_teacher_denied(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    assert client.get("/admin/courses").status_code == 403


def test_researcher_denied(app, client):
    with app.app_context():
        make_user("researcher@example.com", UserRole.RESEARCHER.value)
    login(client, "researcher@example.com")
    assert client.get("/admin/courses").status_code == 403


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
            level = _create_level("Level 1")
            level_id = level.id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            resp = client.post(
                "/admin/courses/new",
                data={"level_id": level_id, "title": "No CSRF Course", "code": "", "description": ""},
            )
            assert resp.status_code == 400
            assert Course.query.filter_by(title="No CSRF Course").first() is None
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
