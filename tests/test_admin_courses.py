import re
from datetime import date
from html.parser import HTMLParser

from app.extensions import db
from app.models import (
    AcademicStatus,
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


def _get_course_edit_snapshot(client, public_id):
    """Fetch the Course edit page and extract the signed edit_snapshot
    hidden field -- every test that POSTs to `course_edit` must include a
    freshly fetched token (matching real browser behaviour: an edit page
    is always loaded via GET before it is submitted), since a POST with a
    missing/invalid/cross-Course/stale token is now correctly rejected.
    """
    html = client.get(f"/admin/courses/{public_id}/edit").get_data(as_text=True)
    match = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _course_edit_post_data(level_id, title="Course", code="", description="", edit_snapshot=""):
    return {
        "level_id": level_id,
        "title": title,
        "code": code,
        "description": description,
        "edit_snapshot": edit_snapshot,
    }


class _CourseFormParser(HTMLParser):
    """Minimal structural parser for the Course edit/create form page,
    mirroring `_GroupFormParser` in test_admin_groups.py -- used instead
    of brittle raw-string attribute-order matching. Tracks
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


def _parse_course_form(html):
    parser = _CourseFormParser()
    parser.feed(html)
    return parser


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


def _create_term(name="Fall 2026"):
    term = AcademicTerm(name=name, start_date=date(2026, 9, 1), end_date=date(2026, 12, 31))
    db.session.add(term)
    db.session.commit()
    return term


def _create_group(course, term=None, name="Group A", status=AcademicStatus.ACTIVE.value, capacity=20):
    term = term or _create_term()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, status=status, capacity=capacity)
    db.session.add(group)
    db.session.commit()
    return group


def _create_teacher(email="teacher@example.com"):
    teacher = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name="Teacher One",
        role=UserRole.TEACHER.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(teacher)
    db.session.commit()
    return teacher


def _create_student(email="student@example.com"):
    student = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name="Student One",
        role=UserRole.STUDENT.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(student)
    db.session.commit()
    return student


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
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data={
            "level_id": level_id,
            "title": "New Title",
            "code": "NEW",
            "description": "Updated",
            "edit_snapshot": snapshot,
        },
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
    """Moving a Course between Levels remains allowed as long as no Group
    currently references it -- the only scenario this test exercises; see
    tests/test_admin_courses.py's "Course-level identity (Policy A)"
    section below for the referenced-Course cases this one deliberately
    does not cover."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Movable Course")
        public_id = course.public_id
        level_b_id = level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data={
            "level_id": level_b_id,
            "title": "Movable Course",
            "code": "",
            "description": "",
            "edit_snapshot": snapshot,
        },
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


# ======================================================================
# Part 7B1 -- Course-level identity hardening
#
# Approved Policy A (see docs/DECISIONS.md, "Course-level identity
# hardening", to be added after this implementation is approved):
# Course.level_id may change while no Group currently references the
# Course. Once at least one Group does -- regardless of its own status
# (active/archived), emptiness, or Enrollment/GroupTeacherAssignment
# history -- level_id is frozen; only title/code/description remain
# editable. Submitting the Course's own current level_id back is always
# allowed regardless of any Group reference. This is a *current*
# references check, not a historical "ever referenced" one -- see
# app/services/course_integrity.py::course_has_group_reference.
#
# course_edit is also hardened the same way group_edit was in Part 7B0:
# a shared Course write-transaction lock
# (app/services/course_transactions.py) and a signed edit-snapshot
# stale-form guard covering exactly the 5 fields the route may overwrite
# (public_id, level_id, title, code, description -- explicitly excluding
# status and display_order, which this route never writes).
#
# SQLite (used by this test suite) has no SELECT ... FOR UPDATE syntax
# and no REPEATABLE READ snapshot isolation -- the structural tests below
# can only prove the code *requests* the deliberate rollback and lock, in
# the right order; they cannot prove SQLite (or MySQL) actually blocks a
# concurrent transaction on it. That guarantee is real only on
# MySQL/InnoDB, and no isolated MySQL test database exists in this
# project to verify it directly.
# ======================================================================


# ----------------------------------------------------------------------
# Course-level identity (Policy A)
# ----------------------------------------------------------------------


def test_course_has_group_reference_returns_correct_booleans(app):
    """Direct unit test of the EXISTS-query helper itself (not through a
    route): a genuine Python bool, True only when a Group currently
    references the Course and False when none does -- regardless of that
    Group's status (an archived Group still counts, per Policy A)."""
    from app.services.course_integrity import course_has_group_reference

    with app.app_context():
        level = _create_level("Level 1")
        unreferenced = _create_course(level.id, "Unreferenced Course")
        referenced = _create_course(level.id, "Referenced Course")
        _create_group(referenced, status=AcademicStatus.ARCHIVED.value)

        result_unreferenced = course_has_group_reference(unreferenced.id)
        result_referenced = course_has_group_reference(referenced.id)

    assert result_unreferenced is False
    assert result_referenced is True


def test_same_level_submission_succeeds_with_groups_referencing_course(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Stable Course")
        _create_group(course)
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="Renamed While Locked", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_id
        assert course.title == "Renamed While Locked"


def test_empty_active_group_blocks_level_move(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Referenced Course")
        _create_group(course, status=AcademicStatus.ACTIVE.value)
        public_id, level_a_id, level_b_id = course.public_id, level_a.id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Referenced Course", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_a_id


def test_empty_archived_group_blocks_level_move(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Referenced Course")
        _create_group(course, status=AcademicStatus.ARCHIVED.value)
        public_id, level_a_id, level_b_id = course.public_id, level_a.id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Referenced Course", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_a_id


def test_group_with_enrollment_history_blocks_level_move(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "History Course")
        group = _create_group(course)
        student = _create_student()
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        public_id, level_a_id, level_b_id = course.public_id, level_a.id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="History Course", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_a_id


def test_group_with_teacher_assignment_history_blocks_level_move(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Teacher History Course")
        group = _create_group(course)
        teacher = _create_teacher()
        db.session.add(
            GroupTeacherAssignment(
                group_id=group.id,
                teacher_id=teacher.id,
                status=GroupTeacherAssignmentStatus.ACTIVE.value,
            )
        )
        db.session.commit()
        public_id, level_a_id, level_b_id = course.public_id, level_a.id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Teacher History Course", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_a_id


def test_locked_ui_exposes_exactly_one_level_field_and_rejects_crafted_change(app, client):
    """Semantic-HTML contract for the locked case: no editable <select
    name="level_id">, exactly one hidden level_id carrying the current
    value, and a read-only <dt>/<dd> pair -- then proves the server
    itself, not the UI, is what rejects a crafted POST attempting a
    different level_id."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Locked Course")
        _create_group(course)
        public_id, level_a_id, level_b_id = course.public_id, level_a.id, level_b.id

    login(client, "admin@example.com")
    html = client.get(f"/admin/courses/{public_id}/edit").get_data(as_text=True)
    parsed = _parse_course_form(html)
    assert "level_id" not in parsed.select_names
    assert parsed.hidden_values("level_id") == [str(level_a_id)]
    assert parsed.dt_dd_pairs == [("Level", "Level A")]

    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Locked Course", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_a_id


def test_unlocked_ui_shows_normal_level_select_with_no_hidden_replacement(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Free Course")
        public_id = course.public_id

    login(client, "admin@example.com")
    html = client.get(f"/admin/courses/{public_id}/edit").get_data(as_text=True)
    parsed = _parse_course_form(html)
    assert "level_id" in parsed.select_names
    assert parsed.hidden_values("level_id") == []


def test_title_code_description_remain_editable_with_groups(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Old Title", code="OLD")
        _create_group(course)
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(
            level_id, title="New Title", code="NEW", description="Updated", edit_snapshot=snapshot
        ),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.title == "New Title"
        assert course.code == "NEW"
        assert course.description == "Updated"


def test_level_move_rejection_causes_no_partial_mutation(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Old Title", code="OLD")
        course.description = "Old description"
        db.session.commit()
        _create_group(course)
        public_id, level_a_id, level_b_id = course.public_id, level_a.id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(
            level_b_id,
            title="Attempted New Title",
            code="NEW",
            description="Attempted",
            edit_snapshot=snapshot,
        ),
    )
    assert resp.status_code == 200
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        # Nothing changed at all -- the whole update is rejected together,
        # not just the level_id field.
        assert course.level_id == level_a_id
        assert course.title == "Old Title"
        assert course.code == "OLD"
        assert course.description == "Old description"


def test_course_can_move_again_after_all_groups_retargeted_away(app, client):
    """Not a historical 'ever referenced' audit trail: once every Group
    that used to reference this Course has been individually retargeted
    away (each allowed only because it itself had no membership history),
    nothing derives this Course's level any more, and it may move
    again."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        other_course = _create_course(level_a.id, "Other Course")
        course = _create_course(level_a.id, "Movable Again")
        term = _create_term()
        group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20)
        db.session.add(group)
        db.session.commit()
        public_id, level_a_id, level_b_id = course.public_id, level_a.id, level_b.id
        group_public_id, other_course_id, term_id = group.public_id, other_course.id, term.id

    login(client, "admin@example.com")

    # Retarget the only referencing (empty, no-history) Group away, using
    # the Group edit route's own signed-snapshot dance.
    group_html = client.get(f"/admin/groups/{group_public_id}/edit").get_data(as_text=True)
    group_snapshot = re.search(r'name="edit_snapshot" value="([^"]*)"', group_html).group(1)
    retarget_resp = client.post(
        f"/admin/groups/{group_public_id}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": other_course_id,
            "name": "Group A",
            "code": "",
            "capacity": "20",
            "status": "active",
            "edit_snapshot": group_snapshot,
        },
        follow_redirects=True,
    )
    assert b"updated" in retarget_resp.data.lower()

    # The Course no Group references any more may move again.
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Movable Again", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_b_id


# ----------------------------------------------------------------------
# Stale-form protection (signed Course edit snapshot)
# ----------------------------------------------------------------------


def test_second_of_two_edit_forms_is_stale(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Original Title")
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    snapshot_a = _get_course_edit_snapshot(client, public_id)
    snapshot_b = _get_course_edit_snapshot(client, public_id)

    resp_a = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="First Editor", edit_snapshot=snapshot_a),
        follow_redirects=True,
    )
    assert b"updated" in resp_a.data.lower()

    resp_b = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="Second Editor", edit_snapshot=snapshot_b),
        follow_redirects=True,
    )
    assert b"changed by someone else" in resp_b.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.title == "First Editor"


def test_missing_invalid_wrong_shaped_cross_course_tokens_rejected(app, client):
    import app.blueprints.admin.courses as courses_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course_a = _create_course(level.id, "Course A")
        course_b = _create_course(level.id, "Course B")
        public_id_a, public_id_b, level_id = course_a.public_id, course_b.public_id, level.id

    login(client, "admin@example.com")

    # Missing token.
    resp = client.post(
        f"/admin/courses/{public_id_a}/edit",
        data=_course_edit_post_data(level_id, title="Should Not Apply", edit_snapshot=""),
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/courses/{public_id_a}/edit")

    # Tampered/invalid signature.
    valid = _get_course_edit_snapshot(client, public_id_a)
    resp = client.post(
        f"/admin/courses/{public_id_a}/edit",
        data=_course_edit_post_data(level_id, title="Should Not Apply", edit_snapshot=valid + "tampered"),
    )
    assert resp.status_code == 302

    # Wrong-shaped payload (valid signature, wrong fields).
    with app.app_context():
        wrong_shape_token = courses_module._course_edit_snapshot_serializer().dumps({"unexpected": "shape"})
    resp = client.post(
        f"/admin/courses/{public_id_a}/edit",
        data=_course_edit_post_data(level_id, title="Should Not Apply", edit_snapshot=wrong_shape_token),
    )
    assert resp.status_code == 302

    # Cross-Course token (valid for a different Course).
    cross_token = _get_course_edit_snapshot(client, public_id_b)
    resp = client.post(
        f"/admin/courses/{public_id_a}/edit",
        data=_course_edit_post_data(level_id, title="Should Not Apply", edit_snapshot=cross_token),
    )
    assert resp.status_code == 302

    with app.app_context():
        course = Course.query.filter_by(public_id=public_id_a).first()
        assert course.title == "Course A"


def test_stale_token_combined_with_invalid_wtforms_data_still_uses_prg(app, client):
    """A stale token is checked and rejected *before* WTForms validation
    even runs -- so an also-invalid title (too long) does not change the
    outcome: still a PRG redirect that discards every submitted value,
    never a re-rendered form with the invalid title's field error."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Original Title")
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    stale_snapshot = _get_course_edit_snapshot(client, public_id)
    with app.app_context():
        Course.query.filter_by(public_id=public_id).update({"title": "Changed Concurrently"})
        db.session.commit()

    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="x" * 200, edit_snapshot=stale_snapshot),
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/courses/{public_id}/edit")


def test_valid_ordinary_validation_failure_preserves_original_token(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Original Title")
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    token = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="x" * 200, edit_snapshot=token),
    )
    assert resp.status_code == 200
    preserved = re.search(r'name="edit_snapshot" value="([^"]*)"', resp.get_data(as_text=True)).group(1)
    assert preserved == token


def test_double_submit_token_upgrade_regression_impossible(app, client):
    """The exact bug fixed for Group edit in Part 7B0 must not exist for
    Course edit either: a rejected submission's response must never pair
    a *freshly generated* token with the *stale/attempted* values it is
    displaying -- otherwise an unmodified resubmission of that response
    would silently bypass the staleness check against whatever the
    Course has become by then."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "S0")
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    token_s0 = _get_course_edit_snapshot(client, public_id)

    # Reject via a too-long title (ordinary WTForms failure) -- response
    # preserves token_s0 and the attempted title.
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="x" * 200, edit_snapshot=token_s0),
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1) == token_s0

    # Someone else genuinely changes the Course in the meantime.
    fresh_html = client.get(f"/admin/courses/{public_id}/edit").get_data(as_text=True)
    fresh_token = re.search(r'name="edit_snapshot" value="([^"]*)"', fresh_html).group(1)
    other_resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="Renamed By Someone Else", edit_snapshot=fresh_token),
        follow_redirects=True,
    )
    assert b"updated" in other_resp.data.lower()

    # Resubmitting the earlier rejected response's exact form (still
    # carrying token_s0, still the same "x"*200 attempted title) must be
    # rejected as stale -- never silently applied.
    resp2 = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="x" * 200, edit_snapshot=token_s0),
    )
    assert resp2.status_code == 302
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.title == "Renamed By Someone Else"


def test_late_stale_check_catches_course_field_changed_after_preview(app, client, monkeypatch):
    """The token is genuinely non-stale against the unlocked preview read
    (so the early check passes), but a snapshotted field (title) changes
    -- simulating a concurrent commit -- in the window between that
    preview and the fresh Course lock. The late, post-lock staleness
    recheck must still catch it and reject via PRG, releasing the lock
    first."""
    import app.blueprints.admin.courses as courses_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Original Title")
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    token = _get_course_edit_snapshot(client, public_id)

    original_lock = courses_module.lock_course_for_write

    def rename_then_lock(pid):
        Course.query.filter_by(public_id=pid).update({"title": "Renamed Concurrently"})
        db.session.commit()
        return original_lock(pid)

    monkeypatch.setattr(courses_module, "lock_course_for_write", rename_then_lock)

    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="Original Title", edit_snapshot=token),
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/courses/{public_id}/edit")
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.title == "Renamed Concurrently"


def test_status_toggle_does_not_stale_edit_form_and_is_not_overwritten(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Course A")
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    token = _get_course_edit_snapshot(client, public_id)

    # A status toggle completes in between -- it does not touch any of
    # the 5 snapshotted fields.
    toggle_resp = client.post(f"/admin/courses/{public_id}/toggle-status", follow_redirects=True)
    assert b"archived" in toggle_resp.data.lower()

    # The still-open edit form's token remains valid (not stale) and its
    # edit succeeds without needing a fresh token.
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="Renamed After Toggle", edit_snapshot=token),
        follow_redirects=True,
    )
    assert b"updated" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        # Title updated by the edit; status remains archived -- the edit
        # never wrote (and thus never reverted) status.
        assert course.title == "Renamed After Toggle"
        assert course.status == "archived"


# ----------------------------------------------------------------------
# Uniqueness and IntegrityError handling
# ----------------------------------------------------------------------


def test_target_level_title_collision_on_move(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        _create_course(level_b.id, "Taken Title")
        course = _create_course(level_a.id, "Movable")
        public_id, level_b_id = course.public_id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Taken Title", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.title == "Movable"


def test_target_level_code_collision_on_move(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        _create_course(level_b.id, "Other Course", code="DUP")
        course = _create_course(level_a.id, "Movable", code="ORIG")
        public_id, level_b_id = course.public_id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Movable", code="DUP", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.code == "ORIG"


def test_same_title_in_another_level_allowed_when_move_permitted(app, client):
    """A title already used by a Course in a *different* Level does not
    block editing (here, without even moving Level) this Course to use
    that same title -- the uniqueness check is correctly scoped per
    Level, in the edit path exactly as it already is in the create
    path."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        _create_course(level_b.id, "Shared Title")
        course = _create_course(level_a.id, "Original Title")
        public_id, level_a_id = course.public_id, level_a.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)
    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_a_id, title="Shared Title", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"updated" in resp.data.lower()
    with app.app_context():
        assert Course.query.filter_by(title="Shared Title").count() == 2


def test_safe_integrity_error_rollback_and_generic_message(app, client, monkeypatch):
    """A race that slips past every application-level check must still be
    caught safely at commit time -- generic message, no raw SQL/driver
    text, and no partial mutation. Uses monkeypatching to simulate the
    failure, never a deliberately broken production file."""
    import app.blueprints.admin.courses as courses_module
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Course A")
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)

    def failing_commit():
        raise IntegrityError("statement", {}, Exception("duplicate key"))

    monkeypatch.setattr(courses_module.db.session, "commit", failing_commit)

    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_id, title="Attempted", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True).lower()
    assert "could not be saved" in html
    assert "sql" not in html
    assert "integrityerror" not in html
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.title == "Course A"


def test_destination_display_order_calculated_after_locking(app, client, monkeypatch):
    """_next_display_order for the destination Level must reflect data
    committed right up to the moment of the Course lock -- not a value
    computed from an earlier, pre-lock read."""
    import app.blueprints.admin.courses as courses_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        _create_course(level_b.id, "Existing In B")  # display_order 0
        course = _create_course(level_a.id, "Movable")
        public_id, level_b_id = course.public_id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)

    original_lock = courses_module.lock_course_for_write

    def add_another_course_then_lock(pid):
        # Simulates a concurrent Course created in the destination Level
        # right before this lock -- committed between the preview read
        # and the fresh lock.
        extra = Course(level_id=level_b_id, title="Concurrently Added", display_order=1)
        courses_module.db.session.add(extra)
        courses_module.db.session.commit()
        return original_lock(pid)

    monkeypatch.setattr(courses_module, "lock_course_for_write", add_another_course_then_lock)

    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Movable", edit_snapshot=snapshot),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        # Must be 2 (after "Existing In B"=0 and "Concurrently Added"=1),
        # not 1 (which a pre-lock computation would have produced and
        # collided with "Concurrently Added").
        assert course.display_order == 2


# ----------------------------------------------------------------------
# Concurrency / lock-order structure
# ----------------------------------------------------------------------


def test_course_reset_occurs_before_course_lock_as_first_query(app, client):
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Course A")
        public_id, level_id = course.public_id, level.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)

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
        resp = client.post(
            f"/admin/courses/{public_id}/edit",
            data=_course_edit_post_data(level_id, title="Renamed", edit_snapshot=snapshot),
        )

    assert resp.status_code == 302
    assert events == ["reset", "lock:Course"]


def test_course_reference_check_occurs_after_course_lock(app, client):
    from unittest.mock import patch

    import app.blueprints.admin.courses as courses_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Course A")
        public_id, level_b_id = course.public_id, level_b.id

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)

    events = []
    original_lock = courses_module.lock_course_for_write
    original_reference_check = courses_module.course_has_group_reference

    def lock_spy(pid):
        events.append("lock")
        return original_lock(pid)

    def reference_check_spy(cid):
        events.append("reference_check")
        return original_reference_check(cid)

    with patch.object(courses_module, "lock_course_for_write", side_effect=lock_spy), patch.object(
        courses_module, "course_has_group_reference", side_effect=reference_check_spy
    ):
        client.post(
            f"/admin/courses/{public_id}/edit",
            data=_course_edit_post_data(level_b_id, title="Course A", edit_snapshot=snapshot),
        )

    # Two preview-time (early, non-authoritative) checks happen first --
    # one to compute `level_locked` for template rendering, one inside
    # `_course_level_change_error`'s early call -- then the lock, then
    # the single authoritative recheck immediately after it.
    assert events == ["reference_check", "reference_check", "lock", "reference_check"]


def test_group_create_and_course_edit_share_the_course_transactions_module(app, client):
    """Structural: Group create/retarget and Course edit lock the target
    Course through primitives defined in the same
    app/services/course_transactions.py module, not separate,
    independently-drifting implementations."""
    import app.blueprints.admin.courses as courses_module
    import app.blueprints.admin.groups as groups_module
    import app.services.course_transactions as course_transactions_module

    assert courses_module.lock_course_for_write is course_transactions_module.lock_course_for_write
    assert (
        groups_module.lock_course_for_write_by_id is course_transactions_module.lock_course_for_write_by_id
    )


def test_course_level_change_catches_group_inserted_in_preview_to_lock_window(app, client, monkeypatch):
    """The Course reference check runs twice: an early, non-authoritative
    friendly check against the unlocked preview read, and again,
    authoritatively, immediately after the fresh Course lock. This test
    proves the *authoritative* post-lock recheck is what actually
    matters -- a Group inserted (simulating a concurrent group_create
    commit) in the window between the unlocked preview read and that lock
    is still correctly caught by the post-lock recheck and rejects the
    level move, even though the earlier preview check saw no Group at
    all."""
    import app.blueprints.admin.courses as courses_module

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level_a = _create_level("Level A")
        level_b = _create_level("Level B")
        course = _create_course(level_a.id, "Race Course")
        term = _create_term()
        public_id, level_a_id, level_b_id, course_id, term_id = (
            course.public_id,
            level_a.id,
            level_b.id,
            course.id,
            term.id,
        )

    login(client, "admin@example.com")
    snapshot = _get_course_edit_snapshot(client, public_id)

    original_lock = courses_module.lock_course_for_write

    def create_group_then_lock(pid):
        db.session.add(
            Group(academic_term_id=term_id, course_id=course_id, name="Race Group", capacity=10)
        )
        db.session.commit()
        return original_lock(pid)

    monkeypatch.setattr(courses_module, "lock_course_for_write", create_group_then_lock)

    resp = client.post(
        f"/admin/courses/{public_id}/edit",
        data=_course_edit_post_data(level_b_id, title="Race Course", edit_snapshot=snapshot),
    )
    assert resp.status_code == 200
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.level_id == level_a_id


# ----------------------------------------------------------------------
# Security and page behavior
# ----------------------------------------------------------------------


def test_course_edit_requires_administrator(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("teacher@example.com", UserRole.TEACHER.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Course A")
        public_id = course.public_id

    login(client, "teacher@example.com")
    assert client.get(f"/admin/courses/{public_id}/edit").status_code == 403


def test_course_edit_unauthenticated_denied(app, client):
    with app.app_context():
        level = _create_level("Level 1")
        course = _create_course(level.id, "Course A")
        public_id = course.public_id

    resp = client.get(f"/admin/courses/{public_id}/edit")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_course_edit_404_for_unknown_public_id(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")
    resp = client.get("/admin/courses/00000000-0000-0000-0000-000000000000/edit")
    assert resp.status_code == 404


def test_course_toggle_status_404_for_unknown_public_id(app, client):
    """toggle-status has no preview read before the lock (unlike
    course_edit) -- a nonexistent public_id reaches `_lock_course_or_404`
    directly, so this is the route that actually exercises its
    `abort(404)` branch."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")
    resp = client.post("/admin/courses/00000000-0000-0000-0000-000000000000/toggle-status")
    assert resp.status_code == 404


def test_get_causes_no_mutation_on_course_edit(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        level = _create_level("Level 1")
        course = _create_course(level.id, "Course A", code="C1")
        public_id = course.public_id

    login(client, "admin@example.com")
    resp = client.get(f"/admin/courses/{public_id}/edit")
    assert resp.status_code == 200
    with app.app_context():
        course = Course.query.filter_by(public_id=public_id).first()
        assert course.title == "Course A"
        assert course.code == "C1"


def test_course_edit_csrf_enforced():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            level = _create_level("Level 1")
            course = _create_course(level.id, "Course A")
            public_id, level_id = course.public_id, level.id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            edit_page = client.get(f"/admin/courses/{public_id}/edit").get_data(as_text=True)
            snapshot = re.search(r'name="edit_snapshot" value="([^"]*)"', edit_page).group(1)

            resp = client.post(
                f"/admin/courses/{public_id}/edit",
                data=_course_edit_post_data(level_id, title="No CSRF", edit_snapshot=snapshot),
            )
            assert resp.status_code == 400
            course = Course.query.filter_by(public_id=public_id).first()
            assert course.title == "Course A"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
