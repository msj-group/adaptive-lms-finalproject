import re
from datetime import date

import pytest

from app.extensions import db
from app.models import (
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login, make_user


def _make_teacher(email="teacher@example.com", full_name="Teacher One", status=UserStatus.ACTIVE.value):
    teacher = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=full_name,
        role=UserRole.TEACHER.value,
        status=status,
    )
    db.session.add(teacher)
    db.session.commit()
    return teacher


def _make_student(email="student@example.com", full_name="Student One", status=UserStatus.ACTIVE.value):
    student = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=full_name,
        role=UserRole.STUDENT.value,
        status=status,
    )
    db.session.add(student)
    db.session.commit()
    return student


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


def _get_edit_snapshot(client, public_id):
    """Fetch the Group edit page and extract the signed edit_snapshot
    hidden field -- every test that POSTs to `group_edit` must include a
    freshly fetched token (matching real browser behaviour: an edit page
    is always loaded via GET before it is submitted), since a POST with
    a missing/invalid/cross-Group/stale token is now correctly rejected.
    """
    import re

    html = client.get(f"/admin/groups/{public_id}/edit").get_data(as_text=True)
    match = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _edit_post_data(term_id, course_id, name="Group A", code="", capacity="10", edit_snapshot="", status=None):
    """Model a real Group edit submission -- which no longer carries a
    `status` field (Part M07C2). `status` is accepted only so the
    dedicated tampering tests can forge one; normal submissions must not
    pass it.
    """
    data = {
        "academic_term_id": term_id,
        "course_id": course_id,
        "name": name,
        "code": code,
        "capacity": capacity,
        "edit_snapshot": edit_snapshot,
    }
    if status is not None:
        data["status"] = status  # forged only -- the real form has no such control
    return data


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
    # Phase 6 / M01: this used to assert "disabled" in the page, which was
    # only ever satisfied by the sidebar's disabled Research "Soon"
    # placeholder. The Groups empty state never had a disabled control of its
    # own, so the assertion checks what this test is actually named for.
    assert "Groups will appear here once created" in html
    assert 'href="/admin/groups/new"' in html


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


def test_group_form_has_no_status_field(app):
    """Part M07C2: `GroupForm` must not expose a status control -- Group
    lifecycle status is owned solely by `group_toggle_status`."""
    from app.blueprints.admin.forms import GroupForm

    with app.app_context():
        _make_term()
        _make_course()
        form = GroupForm()
    assert not hasattr(form, "status")
    assert "status" not in form._fields


def test_create_and_edit_html_contain_no_status_control(app, client):
    """Neither the create nor the edit Group form renders a control
    named `status` (no select, no hidden field)."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
    login(client, "admin@example.com")

    create_html = client.get("/admin/groups/new").get_data(as_text=True)
    edit_html = client.get(f"/admin/groups/{public_id}/edit").get_data(as_text=True)
    for html in (create_html, create_html.lower(), edit_html, edit_html.lower()):
        assert 'name="status"' not in html
    assert "status" not in _parse_group_form(create_html).select_names
    assert "status" not in _parse_group_form(edit_html).select_names


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


def test_create_always_active_regardless_of_forged_status(app, client):
    """Part M07C2: every new Group is created `active`, decided
    server-side. A crafted `status` field in the POST body -- `archived`,
    `active`, or an invalid value -- never controls the stored status."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id
    login(client, "admin@example.com")

    for forged, group_name in (("archived", "Forged Archived"), ("banana", "Forged Invalid")):
        resp = client.post(
            "/admin/groups/new",
            data={
                "academic_term_id": term_id,
                "course_id": course_id,
                "name": group_name,
                "code": "",
                "capacity": "10",
                "status": forged,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"created" in resp.data.lower()
        with app.app_context():
            group = Group.query.filter_by(name=group_name).first()
            assert group is not None
            assert group.status == "active"


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
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(
            term_id, course_id, name="New Name", code="NEW", capacity="35", edit_snapshot=snapshot
        ),
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
    snapshot = _get_edit_snapshot(client, public_id)
    client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(
            term_id, course_id, name="Renamed Group", capacity="15", edit_snapshot=snapshot
        ),
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
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, code="GA1", capacity="20", edit_snapshot=snapshot),
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
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Group A", edit_snapshot=snapshot),
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
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Group B", code="DUP", edit_snapshot=snapshot),
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
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="0", edit_snapshot=snapshot),
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
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data("999999", course_id, edit_snapshot=snapshot),
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
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, "999999", edit_snapshot=snapshot),
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
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_b_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term.name == "Spring 2027"
        assert group.course.title == "Advanced English"
        assert group.course.level.name == "Level 2"


def test_edit_never_writes_status_even_with_forged_field(app, client):
    """Part M07C2: `group_edit` no longer reads or writes status. A
    crafted `status` field in an otherwise-valid edit POST must not
    change the persisted status -- in either direction -- while every
    genuine editable field still updates normally."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        active_group = _make_group(term=term, course=course, name="Active One", capacity=10)
        archived_group = _make_group(term=term, course=course, name="Archived One", capacity=10)
        archived_group.status = "archived"
        db.session.commit()
        active_pid, archived_pid = active_group.public_id, archived_group.public_id
        term_id, course_id = term.id, course.id

    login(client, "admin@example.com")

    # Active Group + forged status=archived -> stays active; name/capacity update.
    snapshot = _get_edit_snapshot(client, active_pid)
    resp = client.post(
        f"/admin/groups/{active_pid}/edit",
        data=_edit_post_data(
            term_id, course_id, name="Active Renamed", capacity="12",
            status="archived", edit_snapshot=snapshot,
        ),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        g = Group.query.filter_by(public_id=active_pid).first()
        assert g.status == "active"
        assert g.name == "Active Renamed"
        assert g.capacity == 12

    # Archived Group + forged status=active -> stays archived; code updates.
    snapshot = _get_edit_snapshot(client, archived_pid)
    resp = client.post(
        f"/admin/groups/{archived_pid}/edit",
        data=_edit_post_data(
            term_id, course_id, name="Archived One", code="ARC",
            status="active", edit_snapshot=snapshot,
        ),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        g = Group.query.filter_by(public_id=archived_pid).first()
        assert g.status == "archived"
        assert g.code == "ARC"


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
    assert "0/25" in html


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


# ======================================================================
# PART 6D -- GROUP DETAIL / MANAGE MEMBERS INTEGRATION
# ======================================================================


def test_manage_members_button_points_to_correct_group(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Linked Group")
        public_id = group.public_id
    login(client, "admin@example.com")

    resp = client.get(f"/admin/groups/{public_id}")
    html = resp.get_data(as_text=True)
    assert f'href="/admin/groups/{public_id}/members"' in html
    assert client.get(f"/admin/groups/{public_id}/members").status_code == 200


def test_detail_shows_active_eligible_teacher(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Taught Group")
        teacher = _make_teacher(full_name="Eligible Teacher")
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}").get_data(as_text=True)
    assert "Eligible Teacher" in html
    assert "No active teacher assigned" not in html


def test_detail_shows_no_active_teacher_message_when_none_eligible(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Teacherless Group")
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}").get_data(as_text=True)
    assert "No active teacher assigned" in html


def test_detail_suspended_teacher_not_shown_as_eligible(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group With Suspended Assignee")
        teacher = _make_teacher(full_name="Suspended Teacher", status=UserStatus.SUSPENDED.value)
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}").get_data(as_text=True)
    assert "Suspended Teacher" not in html
    assert "No active teacher assigned" in html


def test_detail_removed_assignment_not_shown_as_eligible(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Removed Assignment Group")
        teacher = _make_teacher(full_name="Removed Teacher")
        db.session.add(
            GroupTeacherAssignment(
                group_id=group.id, teacher_id=teacher.id, status=GroupTeacherAssignmentStatus.REMOVED.value
            )
        )
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}").get_data(as_text=True)
    assert "Removed Teacher" not in html
    assert "No active teacher assigned" in html


def test_detail_corrupted_assignment_not_shown_as_eligible(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Corrupted Assignment Group")
        corrupt_user = make_user("corruptdetail@example.com", UserRole.RESEARCHER.value, full_name="Not A Teacher")
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=corrupt_user.id))
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}").get_data(as_text=True)
    assert "Not A Teacher" not in html
    assert "No active teacher assigned" in html


def test_detail_active_student_count_correct(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Counted Group", capacity=10)
        student_a = _make_student(email="counta@example.com")
        student_b = _make_student(email="countb@example.com")
        db.session.add(Enrollment(student_id=student_a.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.add(
            Enrollment(student_id=student_b.id, group_id=group.id, status=EnrollmentStatus.WITHDRAWN.value)
        )
        db.session.commit()
        public_id = group.public_id
    login(client, "admin@example.com")

    html = client.get(f"/admin/groups/{public_id}").get_data(as_text=True)
    assert "1/10" in html



# ======================================================================
# PART 7B0 -- GROUP IDENTITY AND CAPACITY INTEGRITY HARDENING
#
# Group edit/status-toggle now lock the Group through the shared,
# Flask-independent `lock_group_for_write` primitive
# (app/services/group_transactions.py) -- the exact same one every
# Enrollment and GroupTeacherAssignment mutation route uses, via each
# module's own thin, route-local 404 wrapper (`_lock_group_or_404` here,
# `_get_group_locked_or_404` in group_members.py). This is what makes a
# concurrent Group edit, status toggle, and membership mutation on the
# same Group genuinely serialize against each other.
#
# Every `group_edit` submission also carries a signed `edit_snapshot`
# token (see the "Group-edit stale-form protection" section of
# app/blueprints/admin/groups.py) proving the form was opened against the
# Group's current persisted state -- this is what stops an edit form
# opened minutes ago, and submitted after someone else's change already
# committed, from silently overwriting that newer change. It is a
# staleness guard, not a concurrency one: `FOR UPDATE` alone cannot catch
# it, because the two requests never overlap in time.
#
# As throughout this project, SQLite has no SELECT ... FOR UPDATE syntax
# and no REPEATABLE READ snapshot isolation -- the structural tests below
# can only prove the code *requests* the deliberate rollback and the
# Group lock in the right order, not that SQLite (or MySQL) actually
# blocks a concurrent transaction on it. That guarantee is real only on
# MySQL/InnoDB, and no isolated MySQL test database exists in this
# project to verify it directly.
# ======================================================================


from html.parser import HTMLParser


class _GroupFormParser(HTMLParser):
    """Minimal structural parser for the Group edit/create form page,
    used instead of brittle raw-string attribute-order matching. Tracks
    <select name=...> field names, <input type=hidden name=... value=...>
    pairs, and <dt>/<dd> read-only label/value pairs.
    """

    def __init__(self):
        super().__init__()
        self.select_names = set()
        self.hidden_inputs = []
        self.dt_dd_pairs = []
        self._current_tag = None
        self._current_text = []
        self._pending_dt = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            name = attrs.get("name")
            if name:
                self.select_names.add(name)
        elif tag == "input" and attrs.get("type") == "hidden":
            self.hidden_inputs.append((attrs.get("name"), attrs.get("value", "")))
        if tag in ("dt", "dd"):
            self._current_tag = tag
            self._current_text = []

    def handle_endtag(self, tag):
        if tag == "dt":
            self._pending_dt = "".join(self._current_text).strip()
            self._current_tag = None
        elif tag == "dd":
            if self._pending_dt is not None:
                self.dt_dd_pairs.append((self._pending_dt, "".join(self._current_text).strip()))
                self._pending_dt = None
            self._current_tag = None

    def handle_data(self, data):
        if self._current_tag in ("dt", "dd"):
            self._current_text.append(data)

    def hidden_values(self, name):
        return [value for field_name, value in self.hidden_inputs if field_name == name]


def _parse_group_form(html):
    parser = _GroupFormParser()
    parser.feed(html)
    return parser


def _post_lock_rollback_before_display_events(client, public_id, post_data):
    """Records, in order, every `db.session.rollback()` call and every
    `_course_choices()` (a real display query) call made while handling
    one POST to `group_edit` -- used to prove a post-lock rejection
    releases the write lock before running any display query that
    renders a response, not merely at request teardown. Returns
    `(events, response)` so callers can also assert on the response
    itself (e.g. a stale rejection's PRG redirect renders no template at
    all, so it must produce no `course_choices_query` event whatsoever).
    """
    from unittest.mock import patch

    import app.blueprints.admin.groups as groups_module

    events = []
    original_rollback = db.session.rollback
    original_choices = groups_module._course_choices

    def rollback_spy(*args, **kwargs):
        events.append("rollback")
        return original_rollback(*args, **kwargs)

    def choices_spy():
        events.append("course_choices_query")
        return original_choices()

    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch(
        "app.blueprints.admin.groups._course_choices", side_effect=choices_spy
    ):
        response = client.post(f"/admin/groups/{public_id}/edit", data=post_data)
    return events, response


# ----------------------------------------------------------------------
# Identity (Academic Term / Course) history rules
# ----------------------------------------------------------------------


def test_edit_allows_term_course_change_with_no_membership_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course_a = _make_course(level_name="Level A", title="Course A")
        course_b = _make_course(level_name="Level B", title="Course B")
        group = _make_group(term=term_a, course=course_a, name="Group A")
        public_id, term_b_id, course_b_id = group.public_id, term_b.id, course_b.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_b_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_b_id
        assert group.course_id == course_b_id


def test_edit_rejects_term_course_change_with_active_enrollment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course_a = _make_course(level_name="Level A", title="Course A")
        course_b = _make_course(level_name="Level B", title="Course B")
        group = _make_group(term=term_a, course=course_a, name="Group A")
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_a_id, term_b_id, course_b_id = group.public_id, term_a.id, term_b.id, course_b.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_b_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


def test_edit_rejects_term_course_change_with_withdrawn_enrollment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course_a = _make_course(level_name="Level A", title="Course A")
        course_b = _make_course(level_name="Level B", title="Course B")
        group = _make_group(term=term_a, course=course_a, name="Group A")
        student = _make_student()
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.WITHDRAWN.value)
        )
        db.session.commit()
        public_id, term_a_id, term_b_id, course_b_id = group.public_id, term_a.id, term_b.id, course_b.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_b_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


def test_edit_rejects_term_course_change_with_active_teacher_assignment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course_a = _make_course(level_name="Level A", title="Course A")
        course_b = _make_course(level_name="Level B", title="Course B")
        group = _make_group(term=term_a, course=course_a, name="Group A")
        teacher = _make_teacher()
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
        db.session.commit()
        public_id, term_a_id, term_b_id, course_b_id = group.public_id, term_a.id, term_b.id, course_b.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_b_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


def test_edit_rejects_term_course_change_with_removed_teacher_assignment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course_a = _make_course(level_name="Level A", title="Course A")
        course_b = _make_course(level_name="Level B", title="Course B")
        group = _make_group(term=term_a, course=course_a, name="Group A")
        teacher = _make_teacher()
        db.session.add(
            GroupTeacherAssignment(
                group_id=group.id, teacher_id=teacher.id, status=GroupTeacherAssignmentStatus.REMOVED.value
            )
        )
        db.session.commit()
        public_id, term_a_id, term_b_id, course_b_id = group.public_id, term_a.id, term_b.id, course_b.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_b_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


def test_edit_rejects_changing_only_term_with_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_a_id, term_b_id, course_id = group.public_id, term_a.id, term_b.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


def test_edit_rejects_changing_only_course_with_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course_a = _make_course(level_name="Level A", title="Course A")
        course_b = _make_course(level_name="Level B", title="Course B")
        group = _make_group(term=term, course=course_a, name="Group A")
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_a_id, course_b_id = group.public_id, term.id, course_a.id, course_b.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_b_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.course_id == course_a_id


def test_edit_allows_posting_original_term_course_with_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Renamed", capacity="10", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Renamed"
        assert group.academic_term_id == term_id
        assert group.course_id == course_id


def test_edit_allows_other_fields_with_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Old Name", code="OLD", capacity=10)
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(
            term_id, course_id, name="New Name", code="NEW", capacity="15",
            edit_snapshot=snapshot,
        ),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "New Name"
        assert group.code == "NEW"
        assert group.capacity == 15
        # `group_edit` never touches status -- the Group keeps whatever it had.
        assert group.status == "active"


def test_edit_rejects_tampered_identity_post_despite_locked_ui(app, client):
    """Simulates a crafted POST that changes one of the hidden identity
    fields the locked UI would normally submit unchanged, while still
    carrying a genuinely fresh, valid edit_snapshot -- the server must
    reject this on the identity check alone, independent of staleness."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        teacher = _make_teacher()
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
        db.session.commit()
        public_id, term_a_id, term_b_id, course_id = group.public_id, term_a.id, term_b.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


def test_edit_identity_rejection_causes_no_partial_mutation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Original Name", code="ORIG", capacity=10)
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_b_id, course_id = group.public_id, term_b.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(
            term_b_id, course_id, name="Attempted New Name", code="NEWCODE", capacity="99",
            edit_snapshot=snapshot,
        ),
    )
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        # Every field must remain exactly as it was -- the rejected
        # identity change must not let name/code/capacity slip through.
        assert group.name == "Original Name"
        assert group.code == "ORIG"
        assert group.capacity == 10


def test_edit_malformed_enrollment_still_locks_identity(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        corrupt_user = make_user(
            "corruptidentity@example.com", UserRole.RESEARCHER.value, full_name="Not A Student Either"
        )
        db.session.add(
            Enrollment(student_id=corrupt_user.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        public_id, term_a_id, term_b_id, course_id = group.public_id, term_a.id, term_b.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    # Even though this Enrollment never counts toward capacity, it is
    # still real relationship history and must still freeze identity.
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


def test_edit_malformed_teacher_assignment_still_locks_identity(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        corrupt_user = make_user(
            "corruptassignment@example.com", UserRole.STUDENT.value, full_name="Not A Teacher"
        )
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=corrupt_user.id))
        db.session.commit()
        public_id, term_a_id, term_b_id, course_id = group.public_id, term_a.id, term_b.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


def test_edit_uniqueness_validation_still_intact_with_history(app, client):
    """Existing name/code uniqueness validation must remain unaffected by
    the new identity/capacity/staleness checks."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        _make_group(term=term, course=course, name="Taken Name")
        group_b = _make_group(term=term, course=course, name="Group B")
        student = _make_student()
        db.session.add(
            Enrollment(student_id=student.id, group_id=group_b.id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        public_id, term_id, course_id = group_b.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Taken Name", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"already exists" in resp.data.lower()
    with app.app_context():
        group_b = Group.query.filter_by(public_id=public_id).first()
        assert group_b.name == "Group B"


# ----------------------------------------------------------------------
# Capacity rules
# ----------------------------------------------------------------------


def test_edit_rejects_capacity_below_active_student_count(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student_a = _make_student(email="capa@example.com")
        student_b = _make_student(email="capb@example.com")
        db.session.add(Enrollment(student_id=student_a.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.add(Enrollment(student_id=student_b.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="1", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"active student count" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 10


def test_edit_allows_capacity_equal_to_active_student_count(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="1", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 1


def test_edit_allows_capacity_above_active_student_count(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="50", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 50


def test_edit_capacity_check_counts_suspended_student_with_active_enrollment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        suspended_student = _make_student(email="suspended@example.com", status=UserStatus.SUSPENDED.value)
        active_student = _make_student(email="activestudent@example.com")
        db.session.add(
            Enrollment(student_id=suspended_student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.add(
            Enrollment(student_id=active_student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    # Active count is 2 (including the suspended student's still-active
    # seat) -- capacity=1 must be rejected, proving the suspended
    # student's Enrollment is included in the count.
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="1", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"active student count" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 10


def test_edit_capacity_check_excludes_non_student_active_enrollment(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        corrupt_user = make_user(
            "corruptcapacity@example.com", UserRole.RESEARCHER.value, full_name="Not A Student"
        )
        db.session.add(
            Enrollment(student_id=corrupt_user.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="1", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    # The malformed active Enrollment does not count toward capacity, so
    # lowering to 1 must succeed even though a row referencing it exists.
    assert b"updated" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 1


def test_edit_withdrawn_enrollment_does_not_consume_capacity(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student = _make_student()
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.WITHDRAWN.value)
        )
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="0", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    # capacity=0 is invalid on its own (NumberRange(min=1)), but the
    # point here is that the withdrawn Enrollment does not force capacity
    # to stay above 1 the way an active one would -- try capacity=1
    # instead, which must succeed.
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="1", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 1


def test_edit_capacity_rejection_leaves_name_code_status_unchanged(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Original Name", code="ORIG", capacity=10)
        group.status = "active"
        student_a = _make_student(email="cra@example.com")
        student_b = _make_student(email="crb@example.com")
        db.session.add(Enrollment(student_id=student_a.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.add(Enrollment(student_id=student_b.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(
            term_id, course_id, name="Attempted Rename", code="NEWCODE", capacity="1",
            edit_snapshot=snapshot,
        ),
    )
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Original Name"
        assert group.code == "ORIG"
        assert group.capacity == 10
        assert group.status == "active"


def test_edit_capacity_check_reflects_enrollment_created_before_lock(app, client, monkeypatch):
    """There is no separate early capacity check in `group_edit` -- the
    active-student-count comparison happens exactly once, only after the
    fresh Group lock. This proves that single authoritative check
    correctly reflects an Enrollment created (simulating a concurrent
    request's commit) in the window between the ordinary preview read
    and that lock, not that an earlier check is being "caught" by a
    later one."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student_a = _make_student(email="racea@example.com")
        student_b = _make_student(email="raceb@example.com")
        db.session.add(
            Enrollment(student_id=student_a.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        public_id, group_id, student_b_id = group.public_id, group.id, student_b.id
        term_id, course_id = term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)

    original_lock_group_in_open_transaction_or_404 = groups_module._lock_group_in_open_transaction_or_404

    def create_second_enrollment_then_lock(gpid):
        # Active count is 1 when the snapshot above was fetched (capacity
        # =1 would be accepted); a second active Enrollment is created
        # here, right before the lock, making the active count 2 by the
        # time the one and only capacity check runs.
        db.session.add(
            Enrollment(student_id=student_b_id, group_id=group_id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        return original_lock_group_in_open_transaction_or_404(gpid)

    monkeypatch.setattr(groups_module, "_lock_group_in_open_transaction_or_404", create_second_enrollment_then_lock)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="1", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"active student count" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 10


def test_edit_valid_snapshot_does_not_bypass_authoritative_history_check(app, client, monkeypatch):
    """A perfectly valid, non-stale edit_snapshot token only proves the 6
    snapshotted scalar Group fields have not changed -- it says nothing
    about related-table history (Enrollment/GroupTeacherAssignment rows)
    created in between. Even when the snapshot itself passes, the
    authoritative post-lock identity/capacity checks must still run
    independently and can still reject the submission."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        student = _make_student()
        public_id, group_id, student_id = group.public_id, group.id, student.id
        term_a_id, term_b_id, course_id = term_a.id, term_b.id, course.id

    login(client, "admin@example.com")
    # Valid, matches the current (history-free) state -- none of the 6
    # snapshotted fields are about to change.
    snapshot = _get_edit_snapshot(client, public_id)

    original_lock_group_in_open_transaction_or_404 = groups_module._lock_group_in_open_transaction_or_404

    def create_history_then_lock(gpid):
        db.session.add(Enrollment(student_id=student_id, group_id=group_id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        return original_lock_group_in_open_transaction_or_404(gpid)

    monkeypatch.setattr(groups_module, "_lock_group_in_open_transaction_or_404", create_history_then_lock)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_id, edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.academic_term_id == term_a_id


# ----------------------------------------------------------------------
# IntegrityError handling
# ----------------------------------------------------------------------


def test_edit_integrity_error_handled_safely(app, client):
    """Simulates a genuine race at the database level: the pre-commit
    name/code uniqueness checks (form validators) find no conflict, but
    the unique constraint still fires at commit time. Mocking
    `db.session.commit` is the only way to exercise that except block
    without genuinely running two concurrent requests -- the same
    technique already established for the Enrollment-creation route. The
    route does not claim to know which constraint fired, so the message
    is generic rather than assuming duplicate name/code specifically."""
    from unittest.mock import patch

    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        _make_group(term=term, course=course, name="Taken Name")
        group_b = _make_group(term=term, course=course, name="Group B")
        public_id, term_id, course_id = group_b.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)

    original_commit = db.session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("update", {}, Exception("duplicate"))
        return original_commit()

    with patch.object(db.session, "commit", side_effect=flaky_commit):
        resp = client.post(
            f"/admin/groups/{public_id}/edit",
            data=_edit_post_data(term_id, course_id, name="Renamed Despite Race", edit_snapshot=snapshot),
            follow_redirects=True,
        )
    assert resp.status_code == 200
    assert resp.status_code != 500
    # Generic, safe message -- never a specific duplicate-name/code claim
    # the route cannot actually confirm, and never raw SQL/driver text.
    html = resp.data.lower()
    assert b"could not be saved" in html
    assert b"reload" in html
    assert b"integrityerror" not in html
    assert b"traceback" not in html
    with app.app_context():
        group_b = Group.query.filter_by(public_id=public_id).first()
        assert group_b.name == "Group B"


# ----------------------------------------------------------------------
# Semantic HTML contract (html.parser-based, not raw-string matching)
# ----------------------------------------------------------------------


def test_edit_page_locked_identity_semantic_contract(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Term Alpha")
        course = _make_course(level_name="Level Alpha", title="Course Alpha")
        group = _make_group(term=term, course=course, name="Group A")
        student = _make_student()
        db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    html = client.get(f"/admin/groups/{public_id}/edit").get_data(as_text=True)

    assert (
        "Academic Term and Course are locked because this group already has enrollment, "
        "teacher-assignment, schedule, unit, assignment, quiz, or gradebook history"
        in html
    )
    parser = _parse_group_form(html)
    assert "academic_term_id" not in parser.select_names
    assert "course_id" not in parser.select_names
    assert parser.hidden_values("academic_term_id") == [str(term_id)]
    assert parser.hidden_values("course_id") == [str(course_id)]
    assert ("Academic Term", "Term Alpha") in parser.dt_dd_pairs
    assert ("Course", "Course Alpha (Level Alpha)") in parser.dt_dd_pairs
    # Other editable controls remain real, non-hidden form fields.
    assert "name" in parser.select_names or 'name="name"' in html
    assert 'name="capacity"' in html
    # Part M07C2: the edit form owns no status control at all.
    assert "status" not in parser.select_names
    assert 'name="status"' not in html


def test_edit_page_unlocked_identity_semantic_contract(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id

    login(client, "admin@example.com")
    html = client.get(f"/admin/groups/{public_id}/edit").get_data(as_text=True)

    assert (
        "Academic Term and Course are locked because this group already has enrollment, "
        "teacher-assignment, schedule, unit, assignment, quiz, or gradebook history"
        not in html
    )
    parser = _parse_group_form(html)
    assert "academic_term_id" in parser.select_names
    assert "course_id" in parser.select_names
    assert parser.hidden_values("academic_term_id") == []
    assert parser.hidden_values("course_id") == []
    # Part M07C2: no status control on the unlocked edit form either.
    assert "status" not in parser.select_names
    assert 'name="status"' not in html


# ----------------------------------------------------------------------
# Stale-form (signed snapshot) protection
# ----------------------------------------------------------------------


def test_edit_stale_form_second_of_two_concurrent_submissions_rejected(app, client):
    """Two edit forms opened from the same original Group state: the
    first submission succeeds, and the second -- still carrying the
    now-outdated original snapshot -- is rejected as stale rather than
    silently overwriting what the first one just committed."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Original", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot_a = _get_edit_snapshot(client, public_id)
    snapshot_b = _get_edit_snapshot(client, public_id)
    assert snapshot_a == snapshot_b  # both opened from the identical original state

    resp1 = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="First Editor", edit_snapshot=snapshot_a),
        follow_redirects=True,
    )
    assert b"updated" in resp1.data.lower()

    resp2 = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Second Editor", edit_snapshot=snapshot_b),
        follow_redirects=True,
    )
    assert b"changed by someone else" in resp2.data.lower()

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "First Editor"


def test_status_toggle_after_form_open_does_not_stale_edit_and_is_not_overwritten(app, client):
    """Part M07C2 concurrency contract: a Group status toggle performed
    after an edit form was opened must NOT invalidate that form when none
    of the six editable fields changed. The subsequent metadata edit
    succeeds, its change is saved, and the toggled status survives
    untouched -- no stale-form message solely because status changed."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    # 1. Open the edit form and retain its valid snapshot.
    snapshot = _get_edit_snapshot(client, public_id)

    # 2. Toggle the Group status through the real POST route.
    toggle_resp = client.post(f"/admin/groups/{public_id}/toggle-status", follow_redirects=True)
    assert b"archived" in toggle_resp.data.lower()

    # 3. Submit a legitimate metadata edit with the previously opened snapshot.
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Renamed While Archived", capacity="20", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    # 4/7. The edit succeeds; no stale-form message.
    assert b"updated" in resp.data.lower()
    assert b"changed by someone else" not in resp.data.lower()

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        # 5. The metadata change is saved.
        assert group.name == "Renamed While Archived"
        assert group.capacity == 20
        # 6. The toggle's archived status is not overwritten by the edit.
        assert group.status == "archived"


def test_edit_stale_form_cannot_restore_any_old_value(app, client):
    """A stale form -- opened before someone else changed the Group --
    is rejected wholesale: none of its fields apply, not even ones the
    Administrator did not intend to change (here, capacity)."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    stale_snapshot = _get_edit_snapshot(client, public_id)

    fresh_snapshot = _get_edit_snapshot(client, public_id)
    client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="50", edit_snapshot=fresh_snapshot),
    )

    # The stale form still shows the Group's original capacity (10) and
    # submits it back unchanged -- it must not be able to "restore" that
    # old capacity over the meanwhile-committed 50.
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="10", edit_snapshot=stale_snapshot),
        follow_redirects=True,
    )
    assert b"changed by someone else" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.capacity == 50


def test_edit_fresh_reload_after_conflict_succeeds(app, client):
    """After a stale rejection (a Post/Redirect/Get to the edit GET
    route, not a rendered page), the redirected page carries a new,
    valid token that permits a genuine subsequent edit."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    stale_snapshot = _get_edit_snapshot(client, public_id)
    # Something else changes a genuine snapshotted field (name), making
    # the still-open form stale.
    with app.app_context():
        g = Group.query.filter_by(public_id=public_id).first()
        g.name = "Changed Elsewhere"
        db.session.commit()

    reject_resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Rejected", edit_snapshot=stale_snapshot),
    )
    assert reject_resp.status_code == 302
    assert reject_resp.headers["Location"].endswith(f"/admin/groups/{public_id}/edit")

    fresh_snapshot = _get_edit_snapshot(client, public_id)
    assert fresh_snapshot != stale_snapshot
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Accepted", edit_snapshot=fresh_snapshot),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Accepted"


def test_edit_missing_snapshot_rejected_without_mutation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Should Not Apply"),
        follow_redirects=True,
    )
    assert b"changed by someone else" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Group A"


def test_edit_invalid_snapshot_rejected_without_mutation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Should Not Apply", edit_snapshot=snapshot + "tampered"),
        follow_redirects=True,
    )
    assert b"changed by someone else" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Group A"


def test_edit_cross_group_snapshot_rejected_without_mutation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        other_group = _make_group(term=term, course=course, name="Other Group", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id
        other_public_id = other_group.public_id

    login(client, "admin@example.com")
    other_group_snapshot = _get_edit_snapshot(client, other_public_id)
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Should Not Apply", edit_snapshot=other_group_snapshot),
        follow_redirects=True,
    )
    assert b"changed by someone else" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Group A"


def test_edit_wrong_shape_snapshot_rejected_without_mutation(app, client):
    """A token that is genuinely signed with the correct secret/salt (so
    its signature verifies) but decodes to a payload with a different
    field set than expected -- e.g. a stale format from a hypothetical
    future change, or any other validly-signed-but-wrong-shape value --
    must still be rejected as invalid, not trusted just because its
    signature checks out."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    with app.app_context():
        wrong_shape_token = groups_module._group_edit_snapshot_serializer().dumps({"unexpected": "shape"})

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Should Not Apply", edit_snapshot=wrong_shape_token),
        follow_redirects=True,
    )
    assert b"changed by someone else" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Group A"


def test_group_edit_snapshot_payload_has_exactly_the_six_approved_fields(app, client):
    """Part M07C2: the signed Group edit snapshot carries exactly the six
    fields `group_edit` can write -- `status` is excluded because that
    route no longer touches it."""
    import app.blueprints.admin.groups as groups_module

    assert groups_module._GROUP_EDIT_SNAPSHOT_FIELDS == (
        "public_id",
        "academic_term_id",
        "course_id",
        "name",
        "code",
        "capacity",
    )

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id
    login(client, "admin@example.com")

    token = _get_edit_snapshot(client, public_id)
    with app.app_context():
        payload = groups_module._group_edit_snapshot_serializer().loads(token)
    assert set(payload) == {
        "public_id",
        "academic_term_id",
        "course_id",
        "name",
        "code",
        "capacity",
    }
    assert "status" not in payload


def test_old_seven_field_snapshot_token_rejected_safely(app, client):
    """A pre-deployment token still carrying the old seven-field shape
    (with `status`) must fail through the existing wrong-shape/stale PRG
    path and mutate nothing."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id
        with app.app_context():
            old_payload = {
                "public_id": public_id,
                "academic_term_id": term_id,
                "course_id": course_id,
                "name": "Group A",
                "code": None,
                "capacity": 10,
                "status": "active",
            }
            old_token = groups_module._group_edit_snapshot_serializer().dumps(old_payload)

    login(client, "admin@example.com")
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Should Not Apply", edit_snapshot=old_token),
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/groups/{public_id}/edit")
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Group A"


# ----------------------------------------------------------------------
# Stale-token-upgrade-after-rejection fix (final Part 7B0 correction)
#
# Root cause closed here: a stale rejection must never pair a freshly
# generated snapshot token with the Administrator's stale/attempted
# submitted values in the same response, because a second, unmodified
# resubmission of that response would then carry a token that validates
# against whatever the Group has become, silently overwriting it. The
# fix is Post/Redirect/Get: a stale/missing/invalid/cross-Group token
# discards every submitted value and redirects to a fresh GET, which is
# the only place a fresh token is ever paired with fresh values.
# ----------------------------------------------------------------------


def test_edit_double_submit_stale_bypass_is_closed(app, client):
    """The exact scenario this correction exists to prevent: Admin A
    opens the form at S0, Admin B commits a different state S1, Admin A
    submits the old S0 form (correctly rejected as stale), and the
    rejection response must show S1 -- never A's attempted S0 values --
    with a token that genuinely represents S1. Submitting that displayed
    form again must not let S0 overwrite S1."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="S0-Name", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    # Admin A opens the form at S0.
    token_s0 = _get_edit_snapshot(client, public_id)

    # Admin B commits a different state S1 directly.
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        group.name = "S1-Name-By-B"
        db.session.commit()

    # Admin A submits the stale S0 form.
    resp1 = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="S0-Attempted-Rename", edit_snapshot=token_s0),
    )
    assert resp1.status_code == 302
    assert resp1.headers["Location"].endswith(f"/admin/groups/{public_id}/edit")

    # Following the rejection shows S1, never the attempted S0 rename,
    # with a fresh token that genuinely represents S1.
    followed = client.get(resp1.headers["Location"])
    html = followed.get_data(as_text=True)
    parser = _parse_group_form(html)
    assert 'value="S1-Name-By-B"' in html
    assert "S0-Attempted-Rename" not in html
    fresh_token = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    assert fresh_token != token_s0

    # Submitting the displayed (S1) form again with the fresh token must
    # succeed and must not let the earlier S0 attempt through.
    resp2 = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="S1-Name-By-B", edit_snapshot=fresh_token),
        follow_redirects=True,
    )
    assert b"updated" in resp2.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "S1-Name-By-B"


def test_edit_stale_token_with_invalid_form_field_still_treated_as_stale(app, client):
    """A stale token combined with an ALSO-invalid field (capacity=0)
    must still be handled as stale first -- discarding the submitted
    values and redirecting to current state -- not as an ordinary
    WTForms validation error that would preserve the (stale) attempted
    values alongside a token."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Original", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    stale_token = _get_edit_snapshot(client, public_id)
    # A genuine snapshotted-field change (capacity) elsewhere makes the token stale.
    with app.app_context():
        g = Group.query.filter_by(public_id=public_id).first()
        g.capacity = 25
        db.session.commit()

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Bad Attempt", capacity="0", edit_snapshot=stale_token),
    )
    assert resp.status_code == 302

    followed = client.get(resp.headers["Location"])
    html = followed.get_data(as_text=True)
    assert "Bad Attempt" not in html
    assert 'value="Original"' in html
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Original"
        assert group.capacity == 25


@pytest.mark.parametrize(
    "token_kind",
    ["missing", "invalid_signature", "cross_group"],
)
def test_edit_bad_token_with_invalid_form_field_discards_and_reloads(app, client, token_kind):
    """Missing, invalidly signed, and cross-Group tokens must each be
    treated identically to a stale one when the form also contains an
    invalid field: discard submitted values and reload current state,
    never preserve the attempted values beside any token."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Original", capacity=10)
        other_group = _make_group(term=term, course=course, name="Other")
        public_id, term_id, course_id = group.public_id, term.id, course.id
        other_public_id = other_group.public_id

    login(client, "admin@example.com")
    if token_kind == "missing":
        token = ""
    elif token_kind == "invalid_signature":
        token = _get_edit_snapshot(client, public_id) + "tampered"
    else:
        token = _get_edit_snapshot(client, other_public_id)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Bad Attempt", capacity="0", edit_snapshot=token),
    )
    assert resp.status_code == 302

    followed = client.get(resp.headers["Location"])
    html = followed.get_data(as_text=True)
    assert "Bad Attempt" not in html
    assert 'value="Original"' in html
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Original"
        assert group.capacity == 10


def test_edit_valid_token_ordinary_validation_error_preserves_values_and_token(app, client):
    """A valid, current token combined with an invalid editable field
    (capacity format) preserves the attempted values AND the exact same
    submitted token -- never a freshly generated one. Correcting the
    field and resubmitting with that same preserved token succeeds,
    since the Group has not changed in between."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    token = _get_edit_snapshot(client, public_id)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Attempted Name", capacity="0", edit_snapshot=token),
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'value="Attempted Name"' in html
    preserved_token = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    assert preserved_token == token

    resp2 = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(
            term_id, course_id, name="Attempted Name", capacity="5", edit_snapshot=preserved_token
        ),
        follow_redirects=True,
    )
    assert b"updated" in resp2.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Attempted Name"
        assert group.capacity == 5


def test_edit_identity_rejection_preserves_original_token_not_a_fresh_one(app, client, monkeypatch):
    """Post-lock identity rejection (reached with a valid, non-stale
    token via the history-injection race) preserves the Administrator's
    attempted values AND the original submitted token -- not a freshly
    generated one bound to the just-refetched display Group."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        student = _make_student()
        public_id, group_id, student_id = group.public_id, group.id, student.id
        term_b_id, course_id = term_b.id, course.id

    login(client, "admin@example.com")
    token = _get_edit_snapshot(client, public_id)

    original_lock_group_in_open_transaction_or_404 = groups_module._lock_group_in_open_transaction_or_404

    def create_history_then_lock(gpid):
        db.session.add(Enrollment(student_id=student_id, group_id=group_id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        return original_lock_group_in_open_transaction_or_404(gpid)

    monkeypatch.setattr(groups_module, "_lock_group_in_open_transaction_or_404", create_history_then_lock)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_b_id, course_id, name="Attempted", edit_snapshot=token),
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    preserved_token = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    assert preserved_token == token


def test_edit_capacity_rejection_preserves_original_token_not_a_fresh_one(app, client):
    """Post-lock capacity rejection preserves the original submitted
    token, not a freshly generated one."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student_a = _make_student(email="preserve_a@example.com")
        student_b = _make_student(email="preserve_b@example.com")
        db.session.add(Enrollment(student_id=student_a.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.add(Enrollment(student_id=student_b.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    token = _get_edit_snapshot(client, public_id)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="1", edit_snapshot=token),
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    preserved_token = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    assert preserved_token == token


def test_edit_preserved_token_becomes_stale_if_group_changes_before_next_submission(app, client):
    """A business-rule rejection preserves the original token (proven
    above); if the Group is changed by something else before the
    Administrator's next submission, that preserved token must correctly
    become stale and must not be able to overwrite the newer state."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student_a = _make_student(email="chain_a@example.com")
        student_b = _make_student(email="chain_b@example.com")
        db.session.add(Enrollment(student_id=student_a.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.add(Enrollment(student_id=student_b.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    token = _get_edit_snapshot(client, public_id)

    # Capacity rejection: preserves `token` unchanged (proven above).
    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="1", edit_snapshot=token),
    )
    preserved_token = re.search(r'name="edit_snapshot" value="([^"]*)"', resp.get_data(as_text=True)).group(1)
    assert preserved_token == token

    # Something else changes a genuine snapshotted field before the next submission.
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        group.name = "Changed By Someone Else"
        db.session.commit()

    # Resubmitting with the preserved token must now be rejected as
    # stale, not silently applied over the newer name.
    resp2 = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, capacity="15", edit_snapshot=preserved_token),
    )
    assert resp2.status_code == 302
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Changed By Someone Else"
        assert group.capacity == 10


def test_edit_late_stale_check_catches_group_field_changed_after_preview(app, client, monkeypatch):
    """The token is genuinely non-stale against the unlocked preview read
    (so the early check passes), but one of the 6 snapshotted Group
    fields (name) changes -- simulating a concurrent commit -- in the
    window between that preview and the fresh lock. The late,
    post-lock staleness recheck must still catch it and reject via PRG,
    releasing the lock first."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Original Name", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    token = _get_edit_snapshot(client, public_id)

    original_lock_group_in_open_transaction_or_404 = groups_module._lock_group_in_open_transaction_or_404

    def rename_then_lock(gpid):
        db.session.query(Group).filter_by(public_id=gpid).update({"name": "Renamed Concurrently"})
        db.session.commit()
        return original_lock_group_in_open_transaction_or_404(gpid)

    monkeypatch.setattr(groups_module, "_lock_group_in_open_transaction_or_404", rename_then_lock)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, name="Original Name", edit_snapshot=token),
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/groups/{public_id}/edit")
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Renamed Concurrently"


def test_edit_csrf_and_snapshot_enforcement():
    """With CSRF genuinely enabled: fetch the locked edit page's CSRF and
    edit_snapshot tokens, prove a same-identity submission with both
    succeeds, a tampered Course/Term submission with a valid CSRF token
    and an otherwise-valid (fresh) snapshot is rejected on the identity
    check, a missing CSRF token is rejected, and missing/invalid/
    cross-Group snapshot tokens are all rejected without mutation.
    """
    import re

    from app import create_app

    isolated_app = create_app("testing")
    isolated_app.config["WTF_CSRF_ENABLED"] = True
    client = isolated_app.test_client()

    with isolated_app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            term_a = _make_term(name="Term A")
            term_b = _make_term(name="Term B")
            course = _make_course()
            group = _make_group(term=term_a, course=course, name="Group A", capacity=10)
            other_group = _make_group(term=term_a, course=course, name="Other Group")
            student = _make_student()
            db.session.add(
                Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value)
            )
            db.session.commit()
            public_id, other_public_id = group.public_id, other_group.public_id
            term_a_id, term_b_id, course_id = term_a.id, term_b.id, course.id

            login_page = client.get("/auth/login")
            login_token = re.search(
                r'name="csrf_token"[^>]*value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": login_token},
            )

            def fetch_tokens(gpid):
                edit_html = client.get(f"/admin/groups/{gpid}/edit").get_data(as_text=True)
                csrf_token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', edit_html).group(1)
                snapshot_token = re.search(r'name="edit_snapshot" value="([^"]*)"', edit_html).group(1)
                return csrf_token, snapshot_token

            # 1. Valid same-identity submission with genuine CSRF succeeds.
            csrf_token, snapshot = fetch_tokens(public_id)
            resp = client.post(
                f"/admin/groups/{public_id}/edit",
                data={
                    **_edit_post_data(term_a_id, course_id, name="Renamed", edit_snapshot=snapshot),
                    "csrf_token": csrf_token,
                },
                follow_redirects=True,
            )
            assert b"updated" in resp.data.lower()

            # 2. Tampered Course/Term with a valid CSRF token and a fresh
            #    (non-stale) snapshot -- rejected on the identity check.
            csrf_token, snapshot = fetch_tokens(public_id)
            resp = client.post(
                f"/admin/groups/{public_id}/edit",
                data={
                    **_edit_post_data(term_b_id, course_id, name="Renamed", edit_snapshot=snapshot),
                    "csrf_token": csrf_token,
                },
                follow_redirects=True,
            )
            assert b"cannot be changed" in resp.data.lower()
            group = Group.query.filter_by(public_id=public_id).first()
            assert group.academic_term_id == term_a_id

            # 3. Missing CSRF token is rejected.
            _, snapshot = fetch_tokens(public_id)
            resp = client.post(
                f"/admin/groups/{public_id}/edit",
                data=_edit_post_data(term_a_id, course_id, name="No CSRF", edit_snapshot=snapshot),
            )
            assert resp.status_code == 400
            group = Group.query.filter_by(public_id=public_id).first()
            assert group.name == "Renamed"

            # 4. Missing edit_snapshot, with a valid CSRF token, is rejected.
            csrf_token, _ = fetch_tokens(public_id)
            resp = client.post(
                f"/admin/groups/{public_id}/edit",
                data={
                    **_edit_post_data(term_a_id, course_id, name="No Snapshot", edit_snapshot=""),
                    "csrf_token": csrf_token,
                },
                follow_redirects=True,
            )
            assert b"changed by someone else" in resp.data.lower()
            group = Group.query.filter_by(public_id=public_id).first()
            assert group.name == "Renamed"

            # 5. Invalid (tampered) edit_snapshot is rejected.
            csrf_token, snapshot = fetch_tokens(public_id)
            resp = client.post(
                f"/admin/groups/{public_id}/edit",
                data={
                    **_edit_post_data(
                        term_a_id, course_id, name="Bad Snapshot", edit_snapshot=snapshot + "x"
                    ),
                    "csrf_token": csrf_token,
                },
                follow_redirects=True,
            )
            assert b"changed by someone else" in resp.data.lower()
            group = Group.query.filter_by(public_id=public_id).first()
            assert group.name == "Renamed"

            # 6. A snapshot signed for a *different* Group is rejected even
            #    though its own signature is genuinely valid.
            csrf_token, _ = fetch_tokens(public_id)
            _, other_snapshot = fetch_tokens(other_public_id)
            resp = client.post(
                f"/admin/groups/{public_id}/edit",
                data={
                    **_edit_post_data(
                        term_a_id, course_id, name="Cross Group", edit_snapshot=other_snapshot
                    ),
                    "csrf_token": csrf_token,
                },
                follow_redirects=True,
            )
            assert b"changed by someone else" in resp.data.lower()
            group = Group.query.filter_by(public_id=public_id).first()
            assert group.name == "Renamed"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


# ----------------------------------------------------------------------
# Locking / transaction-boundary structure
# ----------------------------------------------------------------------


def test_edit_locks_hierarchy_then_group_in_order(app, client):
    """Structural (Part M07C3 global lock order): a metadata-only edit
    (source == target term/course) locks the single AcademicTerm, then
    the single Level, then the single Course, then the Group -- in that
    fixed order and no other row."""
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A")
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)

    calls = []
    original_with_for_update = Query.with_for_update

    def lock_spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        calls.append(getattr(entity, "__name__", "?"))
        return original_with_for_update(self, *args, **kwargs)

    with patch.object(Query, "with_for_update", lock_spy):
        client.post(
            f"/admin/groups/{public_id}/edit",
            data=_edit_post_data(term_id, course_id, edit_snapshot=snapshot),
        )

    assert calls == ["AcademicTerm", "Level", "Course", "Group"]


def test_edit_resets_transaction_before_lock_and_rechecks_after(app, client):
    """Structural: an early, unlocked has_history check runs first (used
    only for friendly UX, never trusted for the decision), then exactly
    one deliberate transaction reset (owned by `lock_academic_hierarchy`),
    then the ancestor rows AcademicTerm -> Level -> Course, then the Group
    lock with no second reset in between, then the authoritative
    has_history recheck immediately after that Group lock -- in that
    exact order (Part M07C3 global lock order). SQLite proves only that
    this sequence is *requested*, not that it blocks a real concurrent
    MySQL transaction."""
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    from app.extensions import db as db_ext
    from app.services import group_memberships as gm

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A")
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)

    events = []
    original_rollback = db_ext.session.rollback

    def rollback_spy(*args, **kwargs):
        events.append("reset")
        return original_rollback(*args, **kwargs)

    original_with_for_update = Query.with_for_update

    def lock_spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_with_for_update(self, *args, **kwargs)

    original_has_history = gm.group_has_membership_history

    def has_history_spy(group_id):
        events.append("has_history_check")
        return original_has_history(group_id)

    with patch.object(db_ext.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ), patch("app.blueprints.admin.groups.group_has_membership_history", side_effect=has_history_spy):
        resp = client.post(
            f"/admin/groups/{public_id}/edit",
            data=_edit_post_data(term_id, course_id, edit_snapshot=snapshot),
        )

    assert resp.status_code == 302
    assert events == [
        "has_history_check",
        "reset",
        "lock:AcademicTerm",
        "lock:Level",
        "lock:Course",
        "lock:Group",
        "has_history_check",
    ]


def test_edit_identity_rejection_rolls_back_before_display_query(app, client, monkeypatch):
    """When no membership history exists at preview time, a term/course
    change is caught by the *early*, pre-lock check, which never locks or
    rolls back at all -- so to exercise the *post-lock* identity
    rejection specifically, history must be injected (simulating a
    concurrent commit) between the preview and the fresh lock, exactly
    like `test_edit_valid_snapshot_does_not_bypass_authoritative_history_check`."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        student = _make_student()
        public_id, group_id, student_id = group.public_id, group.id, student.id
        term_b_id, course_id = term_b.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)

    original_lock_group_in_open_transaction_or_404 = groups_module._lock_group_in_open_transaction_or_404

    def create_history_then_lock(gpid):
        db.session.add(Enrollment(student_id=student_id, group_id=group_id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        return original_lock_group_in_open_transaction_or_404(gpid)

    monkeypatch.setattr(groups_module, "_lock_group_in_open_transaction_or_404", create_history_then_lock)

    events, resp = _post_lock_rollback_before_display_events(
        client, public_id, _edit_post_data(term_b_id, course_id, edit_snapshot=snapshot)
    )
    assert resp.status_code == 200
    assert events[-2:] == ["rollback", "course_choices_query"]


def test_edit_capacity_rejection_rolls_back_before_display_query(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        student_a = _make_student(email="rda@example.com")
        student_b = _make_student(email="rdb@example.com")
        db.session.add(Enrollment(student_id=student_a.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.add(Enrollment(student_id=student_b.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    events, resp = _post_lock_rollback_before_display_events(
        client, public_id, _edit_post_data(term_id, course_id, capacity="1", edit_snapshot=snapshot)
    )
    assert resp.status_code == 200
    assert events[-2:] == ["rollback", "course_choices_query"]


def test_edit_early_stale_rejection_rolls_back_and_runs_no_display_query(app, client):
    """A token already stale against the *unlocked preview* read is
    rejected by the early check, before the Group lock is ever taken
    (proven separately by
    `test_edit_early_stale_rejection_never_reaches_the_lock`). That
    rejection is a pure Post/Redirect/Get: `_redirect_stale_group_edit`
    performs exactly one rollback and renders no template at all, so no
    display query runs -- a stronger guarantee than the identity/capacity
    business-rejection paths above, which do render a page and so do run
    exactly one display query after their own rollback."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    stale_snapshot = _get_edit_snapshot(client, public_id)
    # A genuine snapshotted-field change elsewhere makes the token stale
    # against the unlocked preview read.
    with app.app_context():
        g = Group.query.filter_by(public_id=public_id).first()
        g.name = "Renamed Elsewhere"
        db.session.commit()

    events, resp = _post_lock_rollback_before_display_events(
        client, public_id, _edit_post_data(term_id, course_id, edit_snapshot=stale_snapshot)
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/groups/{public_id}/edit")
    assert events == ["rollback"]
    assert "course_choices_query" not in events


def test_edit_early_stale_rejection_never_reaches_the_lock(app, client, monkeypatch):
    """A token already stale against the unlocked preview read is
    rejected before any write lock is ever acquired -- the ancestor
    `lock_academic_hierarchy` (which owns the one reset) is never called,
    so there is nothing for the caller to release and
    `_redirect_stale_group_edit`'s own rollback is the only one needed."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    stale_snapshot = _get_edit_snapshot(client, public_id)
    # A genuine snapshotted-field change elsewhere makes the token stale.
    with app.app_context():
        g = Group.query.filter_by(public_id=public_id).first()
        g.name = "Renamed Elsewhere"
        db.session.commit()

    lock_calls = []
    original_hierarchy = groups_module.lock_academic_hierarchy

    def hierarchy_spy(*args, **kwargs):
        lock_calls.append((args, kwargs))
        return original_hierarchy(*args, **kwargs)

    monkeypatch.setattr(groups_module, "lock_academic_hierarchy", hierarchy_spy)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, edit_snapshot=stale_snapshot),
    )
    assert resp.status_code == 302
    assert lock_calls == []


def test_edit_post_lock_stale_rejection_releases_lock_before_redirect(app, client, monkeypatch):
    """Structural proof for the *authoritative* post-lock stale branch:
    the token is valid against the unlocked preview (so the early check
    passes and the Group lock IS taken), but one of the snapshotted
    fields changes before the lock. `_redirect_stale_group_edit` is then
    the single owner of the rollback that releases that write lock, and
    it must do so before the redirect, with no display query in between
    -- the caller must not perform its own redundant rollback.

    Deliberately asserts on the ordering of the relevant events rather
    than on an exact total rollback count, since request teardown may add
    further rollbacks of its own that say nothing about this route's
    behaviour."""
    from unittest.mock import patch

    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Original Name", capacity=10)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    token = _get_edit_snapshot(client, public_id)

    events = []
    original_lock = groups_module._lock_group_in_open_transaction_or_404
    original_rollback = db.session.rollback
    original_choices = groups_module._course_choices

    def lock_then_make_stale(gpid):
        # Valid at preview time; a concurrent commit changes a
        # snapshotted field right before the lock is acquired.
        db.session.query(Group).filter_by(public_id=gpid).update({"name": "Renamed Concurrently"})
        db.session.commit()
        locked = original_lock(gpid)
        events.append("lock")
        return locked

    def rollback_spy(*args, **kwargs):
        events.append("rollback")
        return original_rollback(*args, **kwargs)

    def choices_spy():
        events.append("course_choices_query")
        return original_choices()

    monkeypatch.setattr(groups_module, "_lock_group_in_open_transaction_or_404", lock_then_make_stale)

    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch(
        "app.blueprints.admin.groups._course_choices", side_effect=choices_spy
    ):
        resp = client.post(
            f"/admin/groups/{public_id}/edit",
            data=_edit_post_data(term_id, course_id, name="Original Name", edit_snapshot=token),
        )

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/groups/{public_id}/edit")
    # The lock was genuinely taken, then released by exactly one rollback
    # (the helper's), with no display query anywhere after the lock.
    assert "lock" in events
    after_lock = events[events.index("lock") + 1 :]
    assert after_lock == ["rollback"]

    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Renamed Concurrently"


def test_toggle_status_uses_shared_group_lock_primitive(app, client):
    """Group status toggle must lock the Group through the exact same
    shared `lock_group_in_open_transaction` primitive Group edit uses
    (after the Part M07C3 ancestor locks), so a concurrent edit/membership
    change and a status toggle on the same Group serialize against each
    other."""
    from unittest.mock import patch

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        public_id = group.public_id

    login(client, "admin@example.com")

    calls = {"n": 0}
    import app.blueprints.admin.groups as groups_module

    original = groups_module.lock_group_in_open_transaction

    def spy(gpid):
        calls["n"] += 1
        return original(gpid)

    with patch("app.blueprints.admin.groups.lock_group_in_open_transaction", side_effect=spy):
        client.post(f"/admin/groups/{public_id}/toggle-status")

    assert calls["n"] == 1


# ======================================================================
# Part 7B1 -- Course-level identity hardening: closing the Course <-> Group
# race for both Group reference-creation paths.
#
# group_create and the retargeting path of group_edit now lock the
# *target* Course first (app/services/course_transactions.py, by
# internal id -- Group.course_id and GroupForm.course_id both store the
# Course's internal id, never a public_id), then -- for group_edit only
# -- the Group, in that fixed Course -> Group order and without a second
# transaction reset in between. This is what makes a Group create/
# retarget serialize against a concurrent Course-level move on the same
# Course (Policy A, see app/services/course_integrity.py and
# tests/test_admin_courses.py's "Course-level identity (Policy A)"
# section for the Course-side half of this race).
#
# SQLite cannot prove real MySQL/InnoDB blocking -- see the same caveat
# repeated throughout this file and in course_transactions.py's module
# docstring.
# ======================================================================


def test_create_locks_hierarchy_before_creating_group(app, client):
    """Structural (Part M07C3): group_create locks AcademicTerm -> Level
    -> Course, in that fixed order and no other row, before the new Group
    is inserted."""
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id

    login(client, "admin@example.com")

    calls = []
    original_with_for_update = Query.with_for_update

    def lock_spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        calls.append(getattr(entity, "__name__", "?"))
        return original_with_for_update(self, *args, **kwargs)

    with patch.object(Query, "with_for_update", lock_spy):
        resp = client.post(
            "/admin/groups/new",
            data={
                "academic_term_id": term_id,
                "course_id": course_id,
                "name": "Locked Create",
                "code": "",
                "capacity": "10",
            },
            follow_redirects=True,
        )

    assert b"created" in resp.data.lower()
    assert calls == ["AcademicTerm", "Level", "Course"]


def test_create_resets_transaction_before_hierarchy_lock(app, client):
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id

    login(client, "admin@example.com")

    events = []
    original_rollback = db.session.rollback
    original_with_for_update = Query.with_for_update

    def rollback_spy(*args, **kwargs):
        events.append("reset")
        return original_rollback(*args, **kwargs)

    def lock_spy(self, *args, **kwargs):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_with_for_update(self, *args, **kwargs)

    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ):
        client.post(
            "/admin/groups/new",
            data={
                "academic_term_id": term_id,
                "course_id": course_id,
                "name": "Reset Then Lock",
                "code": "",
                "capacity": "10",
            },
        )

    assert events == ["reset", "lock:AcademicTerm", "lock:Level", "lock:Course"]


def test_create_rejects_when_target_course_vanishes_between_validation_and_lock(app, client, monkeypatch):
    """Exceedingly unlikely in practice (Courses are only ever archived,
    never hard-deleted, anywhere in this application), but handled
    gracefully -- a friendly re-rendered form error, not a crash -- if
    the locked target Course comes back empty."""
    import app.services.academic_hierarchy_transactions as hierarchy_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        term_id, course_id = term.id, course.id

    login(client, "admin@example.com")
    monkeypatch.setattr(
        hierarchy_module, "lock_course_in_open_transaction_by_id", lambda cid: None
    )

    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Should Not Be Created",
            "code": "",
            "capacity": "10",
        },
    )
    assert resp.status_code == 200
    assert b"no longer exists" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name="Should Not Be Created").first() is None


def test_create_rejected_for_archived_course(app, client):
    """Part M07C3 -- parent-first creation: a Group may not be created
    under an archived Course. The server rejects it regardless of what
    the (filtered) form choices contain."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        course.status = "archived"
        db.session.commit()
        term_id, course_id = term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Archived Course Group",
            "code": "",
            "capacity": "10",
        },
    )
    assert resp.status_code == 200
    body = resp.data.lower()
    assert b"archived" in body and b"active" in body
    with app.app_context():
        assert Group.query.filter_by(name="Archived Course Group").first() is None


def test_create_rejected_for_course_under_archived_level(app, client):
    """The Level-active half of the parent-first create rule: even an
    active Course cannot host a new Group while its Level is archived."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        course.level.status = "archived"
        db.session.commit()
        term_id, course_id = term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Archived Level Group",
            "code": "",
            "capacity": "10",
        },
    )
    assert resp.status_code == 200
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name="Archived Level Group").first() is None


def test_create_rejected_for_archived_academic_term(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        term.status = "archived"
        course = _make_course()
        db.session.commit()
        term_id, course_id = term.id, course.id

    login(client, "admin@example.com")
    resp = client.post(
        "/admin/groups/new",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": "Archived Term Group",
            "code": "",
            "capacity": "10",
        },
    )
    assert resp.status_code == 200
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(name="Archived Term Group").first() is None


def test_group_edit_retarget_rejected_when_target_course_level_moves_in_preview_to_lock_window(
    app, client, monkeypatch
):
    """Part M07C3: a Group with no history is retargeted to a Course
    whose Level is concurrently moved (simulating a concurrent
    course_edit) in the window between the preview that discovered the
    target Level and the ancestor locks. The route must NOT continue
    while holding the wrong Level -- it detects the mismatch and rejects
    safely via PRG, leaving the Group unchanged."""
    import app.blueprints.admin.groups as groups_module
    from app.models import Level

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level_a = Level(name="Level A", display_order=0)
        level_b = Level(name="Level B", display_order=1)
        db.session.add_all([level_a, level_b])
        db.session.commit()
        target_course = Course(level_id=level_a.id, title="Target Course", display_order=0)
        db.session.add(target_course)
        db.session.commit()
        original_course = _make_course(title="Other Course")
        group = _make_group(term=term, course=original_course, name="Unused Group")
        public_id, term_id, target_course_id = group.public_id, term.id, target_course.id
        original_course_id, level_b_id = original_course.id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)

    original_hierarchy = groups_module.lock_academic_hierarchy

    def move_target_level_then_lock(*args, **kwargs):
        Course.query.filter_by(id=target_course_id).update({"level_id": level_b_id})
        db.session.commit()
        return original_hierarchy(*args, **kwargs)

    monkeypatch.setattr(groups_module, "lock_academic_hierarchy", move_target_level_then_lock)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, target_course_id, edit_snapshot=snapshot),
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/groups/{public_id}/edit")
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.course_id == original_course_id  # retarget did NOT land


def test_group_edit_rejects_when_target_course_vanishes_between_validation_and_lock(app, client, monkeypatch):
    """Exceedingly unlikely in practice (Courses are only ever archived,
    never hard-deleted), but handled gracefully -- a friendly re-rendered
    form error, not a crash -- if the locked target Course comes back
    empty."""
    import app.services.academic_hierarchy_transactions as hierarchy_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A")
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    monkeypatch.setattr(
        hierarchy_module, "lock_course_in_open_transaction_by_id", lambda cid: None
    )

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"no longer exists" in resp.data.lower()
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Group A"


def test_group_edit_404_when_group_vanishes_between_course_lock_and_group_lock(app, client, monkeypatch):
    """Exceedingly unlikely in practice (Groups are only ever archived,
    never hard-deleted), but the Group lock following the Course lock
    must still 404 correctly rather than crash if it ever comes back
    empty."""
    import app.blueprints.admin.groups as groups_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A")
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    snapshot = _get_edit_snapshot(client, public_id)
    monkeypatch.setattr(groups_module, "lock_group_in_open_transaction", lambda public_id: None)

    resp = client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(term_id, course_id, edit_snapshot=snapshot),
    )
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# Phase 4 / M01 -- Assignment history extends the Group identity freeze
# ----------------------------------------------------------------------


def _make_assignment(group, title="Task 1", status=None, published_at=None):
    """One Group-owned Assignment row. Publication status is irrelevant to
    the identity freeze -- both variants are exercised below."""
    from datetime import datetime

    from app.models import Assignment, AssignmentStatus

    status = status or AssignmentStatus.DRAFT.value
    if status == AssignmentStatus.PUBLISHED.value and published_at is None:
        published_at = datetime(2026, 4, 1, 9, 0)
    row = Assignment(
        group_id=group.id,
        title=title,
        instructions="Do the work.",
        opens_at=datetime(2026, 5, 1, 8, 0),
        due_at=datetime(2026, 5, 8, 23, 59),
        status=status,
        published_at=published_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _retarget(client, public_id, term_id, course_id, name="Group A", capacity="10"):
    snapshot = _get_edit_snapshot(client, public_id)
    return client.post(
        f"/admin/groups/{public_id}/edit",
        data=_edit_post_data(
            term_id, course_id, name=name, capacity=capacity, edit_snapshot=snapshot
        ),
        follow_redirects=True,
    )


@pytest.mark.parametrize("assignment_status", ["draft", "published"])
def test_assignment_history_freezes_group_identity(app, client, assignment_status):
    """A draft Assignment freezes identity exactly like a published one:
    both were authored against this Group's current Course and Term."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        _make_assignment(group, status=assignment_status)
        assert Enrollment.query.count() == 0
        assert GroupTeacherAssignment.query.count() == 0
        public_id, term_a_id, term_b_id, course_id = (
            group.public_id, term_a.id, term_b.id, course.id
        )

    login(client, "admin@example.com")
    resp = _retarget(client, public_id, term_b_id, course_id)
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data
    # The rejection must still name assignment history as one of the kinds
    # that froze this group. Phase 4 / M04A extended the enumeration to
    # "...unit, assignment, quiz, or gradebook history", so the message is matched
    # through that phrase rather than the older standalone wording.
    assert b"unit, assignment, quiz, or gradebook history" in resp.data
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().academic_term_id == term_a_id


def test_assignment_history_freezes_the_course_too(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course_a = _make_course(level_name="Level A", title="Course A")
        course_b = _make_course(level_name="Level B", title="Course B")
        group = _make_group(term=term, course=course_a, name="Group A")
        _make_assignment(group)
        public_id, term_id, course_a_id, course_b_id = (
            group.public_id, term.id, course_a.id, course_b.id
        )

    login(client, "admin@example.com")
    resp = _retarget(client, public_id, term_id, course_b_id)
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().course_id == course_a_id


def test_same_identity_resubmission_remains_allowed_with_assignment_history(app, client):
    """Posting the Group's own current Term and Course back is not a
    retarget, so the freeze must not block an ordinary metadata edit."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=20)
        _make_assignment(group)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = _retarget(client, public_id, term_id, course_id, name="Renamed", capacity="30")
    assert resp.status_code == 200
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Renamed" and group.capacity == 30
        assert group.academic_term_id == term_id and group.course_id == course_id


def test_group_status_toggle_remains_allowed_with_assignment_history(app, client):
    """The freeze is about identity only -- it adds no archive blocker,
    and archiving never cascades into the Assignment."""
    from app.models import Assignment, AssignmentStatus

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        _make_assignment(group, status=AssignmentStatus.PUBLISHED.value)
        public_id = group.public_id

    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().status == "archived"
        row = Assignment.query.first()
        assert row.status == AssignmentStatus.PUBLISHED.value
        assert row.published_at is not None


def test_assignment_history_adds_no_new_ancestor_archive_blocker(app, client):
    """Assignments must not change ancestor archiving at all. Two
    identical AcademicTerms -- one whose Group carries Assignments, one
    whose Group does not -- must produce the *same* outcome, whatever the
    pre-existing M07C3 rules decide it is."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        plain_term = _make_term(name="Plain Term")
        _make_group(term=plain_term, course=_make_course(level_name="L1", title="C1"),
                    name="Plain Group")
        with_term = _make_term(name="Assignment Term")
        with_group = _make_group(term=with_term, course=_make_course(level_name="L2", title="C2"),
                                 name="Assignment Group")
        _make_assignment(with_group)
        plain_pid, with_pid = plain_term.public_id, with_term.public_id

    login(client, "admin@example.com")
    plain = client.post(f"/admin/academic-terms/{plain_pid}/toggle-status", follow_redirects=True)
    loaded = client.post(f"/admin/academic-terms/{with_pid}/toggle-status", follow_redirects=True)

    assert plain.status_code == loaded.status_code
    with app.app_context():
        plain_status = AcademicTerm.query.filter_by(public_id=plain_pid).first().status
        loaded_status = AcademicTerm.query.filter_by(public_id=with_pid).first().status
    assert plain_status == loaded_status


def test_locked_notice_mentions_assignment_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        _make_assignment(group)
        public_id = group.public_id

    login(client, "admin@example.com")
    html = client.get(f"/admin/groups/{public_id}/edit").get_data(as_text=True)
    assert (
        "Academic Term and Course are locked because this group already has enrollment, "
        "teacher-assignment, schedule, unit, assignment, quiz, or gradebook history"
        in html
    )
    parser = _parse_group_form(html)
    assert "academic_term_id" not in parser.select_names
    assert "course_id" not in parser.select_names


def test_post_lock_recheck_detects_newly_created_assignment_history(app, client):
    """The pre-lock preview sees no history and the form renders
    unlocked, but an Assignment is created before the Group lock is
    taken. The authoritative post-lock recheck must catch it -- this is
    the race the shared Group lock exists to serialize."""
    import app.blueprints.admin.groups as groups_module
    from unittest.mock import patch

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        public_id, group_id, term_a_id, term_b_id, course_id = (
            group.public_id, group.id, term_a.id, term_b.id, course.id
        )

    login(client, "admin@example.com")
    # Fetched while the Group genuinely has no history at all.
    snapshot = _get_edit_snapshot(client, public_id)

    from datetime import datetime

    from app.models import Assignment

    original = groups_module.lock_group_in_open_transaction

    def create_assignment_then_lock(pid):
        """Stand in for a co-teacher committing an Assignment in the
        window between the unlocked preview read and the Group lock."""
        db.session.add(Assignment(
            group_id=group_id,
            title="Raced in",
            instructions="Do the work.",
            opens_at=datetime(2026, 5, 1, 8, 0),
            due_at=datetime(2026, 5, 8, 23, 59),
            status="draft",
        ))
        db.session.commit()
        return original(pid)

    with patch.object(
        groups_module, "lock_group_in_open_transaction", side_effect=create_assignment_then_lock
    ):
        resp = client.post(
            f"/admin/groups/{public_id}/edit",
            data=_edit_post_data(term_b_id, course_id, edit_snapshot=snapshot),
            follow_redirects=True,
        )

    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().academic_term_id == term_a_id


# ----------------------------------------------------------------------
# Phase 4 / M04A -- Quiz history extends the Group identity freeze
# ----------------------------------------------------------------------


def _make_quiz(group, title="Unit 1 check", instructions="Answer every question."):
    """One Group-owned quiz draft. Every Quiz is a draft in M04A, and an
    EMPTY one (no questions exist at all yet) still counts as history."""
    from datetime import datetime

    from app.models import Quiz

    row = Quiz(
        group_id=group.id,
        title=title,
        instructions=instructions,
        version=1,
        created_at=datetime(2026, 5, 1, 8, 0),
        updated_at=datetime(2026, 5, 1, 8, 0),
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_quiz_history_freezes_group_identity(app, client):
    """An empty quiz draft freezes identity exactly like a draft Unit or a
    draft Assignment: its title and instructions were already authored
    against this Group's current Course and Term."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        _make_quiz(group)
        # The quiz is the ONLY history row of any kind.
        assert Enrollment.query.count() == 0
        assert GroupTeacherAssignment.query.count() == 0
        public_id, term_a_id, term_b_id, course_id = (
            group.public_id, term_a.id, term_b.id, course.id
        )

    login(client, "admin@example.com")
    resp = _retarget(client, public_id, term_b_id, course_id)
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data
    assert b"quiz, or gradebook history" in resp.data
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().academic_term_id == term_a_id


def test_quiz_history_freezes_the_course_too(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course_a = _make_course(level_name="Level A", title="Course A")
        course_b = _make_course(level_name="Level B", title="Course B")
        group = _make_group(term=term, course=course_a, name="Group A")
        _make_quiz(group)
        public_id, term_id, course_a_id, course_b_id = (
            group.public_id, term.id, course_a.id, course_b.id
        )

    login(client, "admin@example.com")
    resp = _retarget(client, public_id, term_id, course_b_id)
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().course_id == course_a_id


def test_same_identity_resubmission_remains_allowed_with_quiz_history(app, client):
    """Posting the Group's own current Term and Course back is not a
    retarget, so the freeze must not block an ordinary metadata edit."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        course = _make_course()
        group = _make_group(term=term, course=course, name="Group A", capacity=20)
        _make_quiz(group)
        public_id, term_id, course_id = group.public_id, term.id, course.id

    login(client, "admin@example.com")
    resp = _retarget(client, public_id, term_id, course_id, name="Renamed", capacity="30")
    assert resp.status_code == 200
    with app.app_context():
        group = Group.query.filter_by(public_id=public_id).first()
        assert group.name == "Renamed" and group.capacity == 30
        assert group.academic_term_id == term_id and group.course_id == course_id


def test_group_status_toggle_remains_allowed_with_quiz_history(app, client):
    """The freeze is about identity only -- it adds no archive blocker,
    and archiving never cascades into the quiz draft."""
    from app.models import Quiz

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        _make_quiz(group, title="Kept", instructions="Kept body")
        public_id = group.public_id

    login(client, "admin@example.com")
    resp = client.post(f"/admin/groups/{public_id}/toggle-status", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().status == "archived"
        row = Quiz.query.one()
        assert row.title == "Kept" and row.instructions == "Kept body"
        assert row.version == 1


def test_quiz_history_adds_no_new_ancestor_archive_blocker(app, client):
    """Quizzes must not change ancestor archiving at all. Two identical
    AcademicTerms -- one whose Group carries a quiz draft, one whose Group
    does not -- must produce the *same* outcome, whatever the pre-existing
    M07C3 rules decide it is."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        plain_term = _make_term(name="Plain Term")
        _make_group(term=plain_term, course=_make_course(level_name="L1", title="C1"),
                    name="Plain Group")
        with_term = _make_term(name="Quiz Term")
        with_group = _make_group(term=with_term, course=_make_course(level_name="L2", title="C2"),
                                 name="Quiz Group")
        _make_quiz(with_group)
        plain_pid, with_pid = plain_term.public_id, with_term.public_id

    login(client, "admin@example.com")
    plain = client.post(f"/admin/academic-terms/{plain_pid}/toggle-status", follow_redirects=True)
    loaded = client.post(f"/admin/academic-terms/{with_pid}/toggle-status", follow_redirects=True)

    assert plain.status_code == loaded.status_code
    with app.app_context():
        plain_status = AcademicTerm.query.filter_by(public_id=plain_pid).first().status
        loaded_status = AcademicTerm.query.filter_by(public_id=with_pid).first().status
    assert plain_status == loaded_status


def test_locked_notice_mentions_quiz_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _make_group(name="Group A")
        _make_quiz(group)
        public_id = group.public_id

    login(client, "admin@example.com")
    html = client.get(f"/admin/groups/{public_id}/edit").get_data(as_text=True)
    assert (
        "Academic Term and Course are locked because this group already has enrollment, "
        "teacher-assignment, schedule, unit, assignment, quiz, or gradebook history"
        in html
    )
    parser = _parse_group_form(html)
    assert "academic_term_id" not in parser.select_names
    assert "course_id" not in parser.select_names


def test_post_lock_recheck_detects_newly_created_quiz_history(app, client):
    """The pre-lock preview sees no history and the form renders unlocked,
    but a quiz draft is created before the Group lock is taken. The
    authoritative post-lock recheck must catch it -- this is the race the
    shared Group lock exists to serialize."""
    import app.blueprints.admin.groups as groups_module
    from unittest.mock import patch

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term A")
        term_b = _make_term(name="Term B")
        course = _make_course()
        group = _make_group(term=term_a, course=course, name="Group A")
        public_id, group_id, term_a_id, term_b_id, course_id = (
            group.public_id, group.id, term_a.id, term_b.id, course.id
        )

    login(client, "admin@example.com")
    # Fetched while the Group genuinely has no history at all.
    snapshot = _get_edit_snapshot(client, public_id)

    from datetime import datetime

    from app.models import Quiz

    original = groups_module.lock_group_in_open_transaction

    def create_quiz_then_lock(pid):
        """Stand in for a co-teacher committing a quiz draft in the window
        between the unlocked preview read and the Group lock."""
        db.session.add(Quiz(
            group_id=group_id,
            title="Raced in",
            instructions="Answer every question.",
            version=1,
            created_at=datetime(2026, 5, 1, 8, 0),
            updated_at=datetime(2026, 5, 1, 8, 0),
        ))
        db.session.commit()
        return original(pid)

    with patch.object(
        groups_module, "lock_group_in_open_transaction", side_effect=create_quiz_then_lock
    ):
        resp = client.post(
            f"/admin/groups/{public_id}/edit",
            data=_edit_post_data(term_b_id, course_id, edit_snapshot=snapshot),
            follow_redirects=True,
        )

    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data
    with app.app_context():
        assert Group.query.filter_by(public_id=public_id).first().academic_term_id == term_a_id
