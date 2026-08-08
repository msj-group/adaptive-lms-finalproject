from datetime import date

from app.extensions import db
from app.models import AcademicTerm, Course, Group, Level, UserRole
from tests.conftest import login, make_user


def _make_term(name="Fall 2026"):
    term = AcademicTerm(name=name, start_date=date(2026, 9, 1), end_date=date(2026, 12, 31))
    db.session.add(term)
    db.session.commit()
    return term


def _make_course(level_name="Level 1", title="General English"):
    level = Level(name=level_name, display_order=0)
    db.session.add(level)
    db.session.commit()
    course = Course(title=title, level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    return course


def _make_group(term=None, course=None, name="Group A", code=None, capacity=20):
    term = term or _make_term()
    course = course or _make_course()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, code=code, capacity=capacity)
    db.session.add(group)
    db.session.commit()
    return group


def test_administrator_can_access_groups_list(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert resp.status_code == 200


def test_unauthenticated_user_cannot_access(client):
    resp = client.get("/admin/groups")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_non_administrator_cannot_access(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    assert client.get("/admin/groups").status_code == 403


def test_existing_groups_are_displayed_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_group(name="Morning Batch", code="MB1", capacity=25)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    html = resp.get_data(as_text=True)
    assert "Morning Batch" in html
    assert "MB1" in html
    assert ">25<" in html


def test_course_is_displayed_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        course = _make_course(title="Business English")
        _make_group(course=course, name="Group A")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert "Business English" in resp.get_data(as_text=True)


def test_level_is_displayed_through_course_relationship(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        course = _make_course(level_name="Level 7")
        _make_group(course=course, name="Group A")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert "Level 7" in resp.get_data(as_text=True)


def test_academic_term_is_displayed_correctly(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Spring 2027")
        _make_group(term=term, name="Group A")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert "Spring 2027" in resp.get_data(as_text=True)


def test_empty_state_when_no_groups_exist(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    html = resp.get_data(as_text=True)
    assert "No groups yet" in html
    assert "disabled" in html


def test_search_filters_by_name_or_code(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        _make_group(term=term, course=course, name="Alpha Group", code="ALP")
        _make_group(term=term, course=course, name="Beta Group", code="BET")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups?q=Alpha")
    html = resp.get_data(as_text=True)
    assert "Alpha Group" in html
    assert "Beta Group" not in html

    resp = client.get("/admin/groups?q=BET")
    html = resp.get_data(as_text=True)
    assert "Beta Group" in html
    assert "Alpha Group" not in html


def test_admin_can_access_create_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_term()
        _make_course()
    login(client, "admin@example.com")

    resp = client.get("/admin/groups/new")
    assert resp.status_code == 200
    assert b"New Group" in resp.data


def test_create_page_redirects_when_no_term_or_course_exists(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups/new", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Create at least one Academic Term and one Course" in resp.data


def test_unauthenticated_user_cannot_access_create_page(client):
    resp = client.get("/admin/groups/new")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_non_admin_user_cannot_access_create_page(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")
    assert client.get("/admin/groups/new").status_code == 403


def test_successful_group_creation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "GA1",
            "capacity": "25",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        group = Group.query.filter_by(name="Group A").first()
        assert group is not None
        assert group.code == "GA1"
        assert group.capacity == 25
        assert group.status == "active"


def test_correct_database_relationships_after_creation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Winter 2027")
        course = _make_course(level_name="Level 9", title="Advanced English")
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )

    with app.app_context():
        group = Group.query.filter_by(name="Group A").first()
        assert group.academic_term_id == term_id
        assert group.course_id == course_id
        assert group.academic_term.name == "Winter 2027"
        assert group.course.title == "Advanced English"
        assert group.course.level.name == "Level 9"


def test_correct_status_handling(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Archived Group",
            "code": "",
            "capacity": "10",
            "status": "archived",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        group = Group.query.filter_by(name="Archived Group").first()
        assert group.status == "archived"


def test_required_field_validation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_term()
        _make_course()
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={"academic_term_id": "", "course_id": "", "name": "", "code": "", "capacity": "", "status": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"this field is required" in resp.data.lower()

    with app.app_context():
        assert Group.query.count() == 0


def test_invalid_capacity_zero_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "0",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Group.query.filter_by(name="Group A").first() is None


def test_invalid_capacity_negative_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "-3",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Group.query.filter_by(name="Group A").first() is None


def test_duplicate_group_name_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
        _make_group(term=term, course=course, name="Group A")
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert Group.query.filter_by(name="Group A").count() == 1


def test_duplicate_group_code_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
        _make_group(term=term, course=course, name="Existing Group", code="DUP")
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Another Group",
            "code": "DUP",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        assert Group.query.filter_by(name="Another Group").first() is None


def test_same_name_allowed_in_different_term_or_course(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
        _make_group(term=term, course=course, name="Group A")
        other_term = _make_term(name="Spring 2027")
        other_term_id = other_term.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": other_term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"created" in resp.data.lower()

    with app.app_context():
        assert Group.query.filter_by(name="Group A").count() == 2


def test_invalid_nonexistent_course_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        term_id = term.id
        _make_course()
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": "999999",
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Group.query.filter_by(name="Group A").first() is None


def test_invalid_nonexistent_academic_term_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_term()
        course = _make_course()
        course_id = course.id
    login(client, "admin@example.com")

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": "999999",
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Group.query.filter_by(name="Group A").first() is None


def test_csrf_protection_on_create():
    import re

    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            term = _make_term()
            course = _make_course()
            term_id, course_id = term.id, course.id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            resp = client.post(
                "/admin/groups/new",
                data={
                    "academic_term_id": term_id,
                    "course_id": course_id,
                    "name": "No CSRF Group",
                    "code": "",
                    "capacity": "10",
                    "status": "active",
                },
            )
            assert resp.status_code == 400
            assert Group.query.filter_by(name="No CSRF Group").first() is None
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_admin_can_access_edit_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups/{public_id}/edit")
    assert resp.status_code == 200
    assert b"Edit Group" in resp.data


def test_edit_page_returns_404_for_nonexistent_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups/00000000-0000-0000-0000-000000000000/edit")
    assert resp.status_code == 404


def test_unauthenticated_user_cannot_access_edit_page(app, client):
    with app.app_context():
        group = _make_group(name="Group A")
        public_id = group.public_id

    resp = client.get(f"/admin/groups/{public_id}/edit")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_non_admin_user_cannot_access_edit_page(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
    login(client, "teacher@example.com")

    assert client.get(f"/admin/groups/{public_id}/edit").status_code == 403


def test_successful_group_edit(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Old Name", code="OLD", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "New Name",
            "code": "NEW",
            "capacity": "35",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "New Name"
        assert group.code == "NEW"
        assert group.capacity == 35


def test_edit_preserves_id_and_public_id(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A")
        public_id, internal_id, term_id, course_id = group.public_id, group.id, term.id, course.id

    login(client, "admin@example.com")
    client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Renamed Group",
            "code": "",
            "capacity": "15",
            "status": "active",
        },
        follow_redirects=True,
    )

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.id == internal_id
        assert group.public_id == public_id


def test_edit_allows_keeping_same_name_and_code(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", code="GA1", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "GA1",
            "capacity": "20",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 20


def test_edit_rejects_conflicting_name_with_another_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        _make_group(term=term, course=course, name="Group A")
        group_b = _make_group(term=term, course=course, name="Group B")
        public_id, term_id, course_id = group_b.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        group_b = Group.query.filter_by(public_id=public_id).first()
        assert group_b.name == "Group B"


def test_edit_rejects_conflicting_code_with_another_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        _make_group(term=term, course=course, name="Group A", code="DUP")
        group_b = _make_group(term=term, course=course, name="Group B", code="OTHER")
        public_id, term_id, course_id = group_b.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group B",
            "code": "DUP",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()

    with app.app_context():
        group_b = Group.query.filter_by(public_id=public_id).first()
        assert group_b.code == "OTHER"


def test_edit_invalid_capacity_is_rejected(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "0",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 10


def test_edit_rejects_nonexistent_academic_term(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A")
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": "999999",
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_id


def test_edit_rejects_nonexistent_course(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A")
        public_id, term_id, course_id_expected = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": "999999",
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.course_id == course_id_expected


def test_edit_can_move_group_to_different_term_and_course(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Fall 2026")
        term_b = _make_term(name="Spring 2027")
        course_a = _make_course(level_name="Level 1", title="General English")
        course_b = _make_course(level_name="Level 2", title="Advanced English")
        group = _make_group(term=term_a, course=course_a, name="Group A")
        public_id, term_b_id, course_b_id = group.public_id, term_b.id, course_b.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_b_id,
            "course_id": course_b_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term.name == "Spring 2027"
        assert group.course.title == "Advanced English"
        assert group.course.level.name == "Level 2"


def test_edit_status_can_be_changed(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A")
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Group A",
            "code": "",
            "capacity": "10",
            "status": "archived",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.status == "archived"


def test_csrf_protection_on_edit():
    import re

    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            term = _make_term()
            course = _make_course()
            group = _make_group(term=term, course=course, name="Group A")
            public_id, term_id, course_id = group.public_id, term.id, course.id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            resp = client.post(
                f"/admin/groups/{public_id}/edit",
                data={
                    "academic_term_id": term_id,
                    "course_id": course_id,
                    "name": "No CSRF Edit",
                    "code": "",
                    "capacity": "10",
                    "status": "active",
                },
            )
            assert resp.status_code == 400

            group = Group.query.filter_by(public_id=public_id).first()
            assert group.name == "Group A"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_activate_a_deactivated_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        group.status = "archived"
        db.session.commit()
        public_id = group.public_id

    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200
    assert b"active" in resp.data.lower()

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.status == "active"


def test_deactivate_an_active_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
        assert group.status == "active"

    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.status == "archived"


def test_toggle_status_is_post_only(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id

    login(client, "admin@example.com")
    resp = client.get(f"/admin/groups/{public_id}/toggle-status")
    assert resp.status_code == 405

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.status == "active"


def test_toggle_status_preserves_other_group_information(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", code="GA1", capacity=33)
        public_id, term_name, course_title = group.public_id, term.name, course.title

    login(client, "admin@example.com")
    client.post(f"/admin/groups/{public_id}/toggle-status", follow_redirects=True)

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Group A"
        assert group.code == "GA1"
        assert group.capacity == 33
        assert group.academic_term.name == term_name
        assert group.course.title == course_title


def test_toggle_status_unauthenticated_denied(app, client):
    with app.app_context():
        group = _make_group(name="Group A")
        public_id = group.public_id

    resp = client.post(f"/admin/groups/{public_id}/toggle-status")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.status == "active"


def test_toggle_status_non_admin_denied(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        group = _make_group(name="Group A")
        public_id = group.public_id

    login(client, "teacher@example.com")
    resp = client.post(f"/admin/groups/{public_id}/toggle-status")
    assert resp.status_code == 403

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.status == "active"


def test_toggle_status_nonexistent_group_returns_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.post("/admin/groups/00000000-0000-0000-0000-000000000000/toggle-status")
    assert resp.status_code == 404


def test_toggle_status_ui_shows_correct_action_and_badge(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_group(name="Group A")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    html = resp.get_data(as_text=True)
    assert "Deactivate" in html
    assert "Activate" not in html

    with app.app_context():
        group = Group.query.filter_by(name="Group A").first()
        public_id = group.public_id
    client.post(f"/admin/groups/{public_id}/toggle-status", follow_redirects=True)

    resp = client.get("/admin/groups")
    html = resp.get_data(as_text=True)
    assert "Activate" in html
    assert "Deactivate" not in html


def test_toggle_status_csrf_enforced():
    import re

    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            group = _make_group(name="Group A")
            public_id = group.public_id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            resp = client.post(f"/admin/groups/{public_id}/toggle-status")
            assert resp.status_code == 400

            group = Group.query.filter_by(public_id=public_id).first()
            assert group.status == "active"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_deactivate_confirmation_attribute_present_only_for_active_groups(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        active_group = _make_group(name="Active Group")
        archived_group = _make_group(
            term=active_group.academic_term, course=active_group.course, name="Archived Group"
        )
        archived_group.status = "archived"
        db.session.commit()
    login(client, "admin@example.com")

    html = client.get("/admin/groups").get_data(as_text=True)
    assert 'data-confirm="Deactivate Active Group?' in html
    assert 'data-confirm="Deactivate Archived Group?' not in html


def test_group_name_with_quote_does_not_break_confirmation_markup(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_group(name="O'Brien's Group")
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "O&#39;Brien&#39;s Group" in html or "O'Brien's Group" in html
    assert "data-confirm=" in html


def _setup_filter_fixture():
    """Two terms x two courses (each on its own level), one group per combination."""
    term_spring = _make_term(name="Spring 2026")
    term_fall = _make_term(name="Fall 2026")
    course_beginner = _make_course(level_name="Beginner", title="English Beginner")
    course_advanced = _make_course(level_name="Advanced", title="English Advanced")

    g1 = _make_group(term=term_spring, course=course_beginner, name="Spring Beginner A", capacity=10)
    g2 = _make_group(term=term_spring, course=course_advanced, name="Spring Advanced A", capacity=10)
    g3 = _make_group(term=term_fall, course=course_beginner, name="Fall Beginner A", capacity=10)
    g4 = _make_group(term=term_fall, course=course_advanced, name="Fall Advanced A", capacity=10)
    g4.status = "archived"
    db.session.commit()

    return {
        "term_spring": term_spring,
        "term_fall": term_fall,
        "course_beginner": course_beginner,
        "course_advanced": course_advanced,
        "groups": [g1, g2, g3, g4],
    }


def test_filter_by_academic_term(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        fixture = _setup_filter_fixture()
        term_spring_id = fixture["term_spring"].id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups?term_id={term_spring_id}")
    html = resp.get_data(as_text=True)
    assert "Spring Beginner A" in html
    assert "Spring Advanced A" in html
    assert "Fall Beginner A" not in html
    assert "Fall Advanced A" not in html


def test_filter_by_course(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        fixture = _setup_filter_fixture()
        course_beginner_id = fixture["course_beginner"].id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups?course_id={course_beginner_id}")
    html = resp.get_data(as_text=True)
    assert "Spring Beginner A" in html
    assert "Fall Beginner A" in html
    assert "Spring Advanced A" not in html
    assert "Fall Advanced A" not in html


def test_filter_by_level_through_course(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        fixture = _setup_filter_fixture()
        advanced_level_id = fixture["course_advanced"].level_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups?level_id={advanced_level_id}")
    html = resp.get_data(as_text=True)
    assert "Spring Advanced A" in html
    assert "Fall Advanced A" in html
    assert "Spring Beginner A" not in html
    assert "Fall Beginner A" not in html


def test_filter_by_status(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _setup_filter_fixture()
    login(client, "admin@example.com")

    resp = client.get("/admin/groups?status=archived")
    html = resp.get_data(as_text=True)
    assert "Fall Advanced A" in html
    assert "Spring Beginner A" not in html
    assert "Spring Advanced A" not in html
    assert "Fall Beginner A" not in html


def test_combined_filters_term_course_status(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        fixture = _setup_filter_fixture()
        term_fall_id = fixture["term_fall"].id
        course_advanced_id = fixture["course_advanced"].id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups?term_id={term_fall_id}&course_id={course_advanced_id}&status=archived")
    html = resp.get_data(as_text=True)
    assert "Fall Advanced A" in html
    assert "Spring Beginner A" not in html
    assert "Spring Advanced A" not in html
    assert "Fall Beginner A" not in html

    # Same term+course but Active status should now return nothing (that group is archived)
    resp = client.get(f"/admin/groups?term_id={term_fall_id}&course_id={course_advanced_id}&status=active")
    html = resp.get_data(as_text=True)
    assert "No groups match your filters" in html


def test_combined_filters_with_search(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        fixture = _setup_filter_fixture()
        term_spring_id = fixture["term_spring"].id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups?term_id={term_spring_id}&q=Advanced")
    html = resp.get_data(as_text=True)
    assert "Spring Advanced A" in html
    assert "Spring Beginner A" not in html
    assert "Fall Advanced A" not in html


def test_clear_filters_link_present_only_when_filters_active(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _setup_filter_fixture()
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    assert ">Clear<" not in resp.get_data(as_text=True)

    resp = client.get("/admin/groups?status=active")
    assert ">Clear<" in resp.get_data(as_text=True)


def test_admin_can_access_detail_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A", code="GA1", capacity=25)
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups/{public_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Group A" in html
    assert "GA1" in html
    assert ">25<" in html


def test_detail_page_shows_term_course_and_level(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Winter 2027")
        course = _make_course(level_name="Level 9", title="Advanced English")
        group = _make_group(term=term, course=course, name="Group A")
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups/{public_id}")
    html = resp.get_data(as_text=True)
    assert "Winter 2027" in html
    assert "Advanced English" in html
    assert "Level 9" in html


def test_detail_page_shows_created_and_updated_dates(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups/{public_id}")
    html = resp.get_data(as_text=True)
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.created_at.strftime("%Y-%m-%d") in html
        assert group.updated_at.strftime("%Y-%m-%d") in html


def test_detail_page_has_edit_and_back_navigation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups/{public_id}")
    html = resp.get_data(as_text=True)
    assert f"/admin/groups/{public_id}/edit" in html
    assert 'href="/admin/groups"' in html


def test_detail_page_returns_404_for_nonexistent_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/groups/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_unauthenticated_user_cannot_access_detail_page(app, client):
    with app.app_context():
        group = _make_group(name="Group A")
        public_id = group.public_id

    resp = client.get(f"/admin/groups/{public_id}")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_non_admin_user_cannot_access_detail_page(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
    login(client, "teacher@example.com")

    assert client.get(f"/admin/groups/{public_id}").status_code == 403


def test_list_page_links_to_detail_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.get("/admin/groups")
    html = resp.get_data(as_text=True)
    assert f'href="/admin/groups/{public_id}"' in html


def test_group_new_route_not_shadowed_by_detail_route(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_term()
        _make_course()
    login(client, "admin@example.com")

    resp = client.get("/admin/groups/new")
    assert resp.status_code == 200
    assert b"New Group" in resp.data


def test_malformed_or_malicious_public_id_returns_clean_404(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    malicious_ids = [
        "abc",
        "../../etc/passwd",
        "<script>alert(1)</script>",
        "' OR '1'='1",
        "1 OR 1=1",
        "a" * 5000,
    ]
    for bad_id in malicious_ids:
        assert client.get(f"/admin/groups/{bad_id}").status_code == 404
        assert client.get(f"/admin/groups/{bad_id}/edit").status_code == 404
        assert client.post(f"/admin/groups/{bad_id}/toggle-status").status_code in (400, 404)


def test_filter_params_reject_non_integer_values_without_crashing(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_group(name="Group A")
    login(client, "admin@example.com")

    for bad_value in ["abc", "'; DROP TABLE groups; --", "-1", "999999999999999999999"]:
        resp = client.get(f"/admin/groups?term_id={bad_value}&course_id={bad_value}&level_id={bad_value}")
        assert resp.status_code == 200

    with app.app_context():
        assert Group.query.count() == 1
