import itertools
from datetime import date

from app.extensions import db
from app.models import AcademicTerm, Course, Enrollment, Group, Level, User, UserRole, UserStatus
from app.security.passwords import hash_password
from tests.conftest import login, make_user

# Distinguishes default names across multiple _make_term()/_make_group()
# calls within one test that don't explicitly share a term/group, so
# calling _make_enrollment() repeatedly without arguments doesn't collide
# on AcademicTerm.name/Group.name uniqueness.
_unique_counter = itertools.count(1)


def _make_term(name=None, start=date(2026, 9, 1), end=date(2026, 12, 31)):
    name = name or f"Term {next(_unique_counter)}"
    term = AcademicTerm(name=name, start_date=start, end_date=end)
    db.session.add(term)
    db.session.commit()
    return term


def _make_group(term=None, course=None, name=None):
    name = name or f"Group {next(_unique_counter)}"
    term = term or _make_term()
    if course is None:
        level = Level(name=f"Level for {name}", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title=f"Course for {name}", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, capacity=20)
    db.session.add(group)
    db.session.commit()
    return group


def _make_student(email="student@example.com", full_name="Student One"):
    student = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name=full_name,
        role=UserRole.STUDENT.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(student)
    db.session.commit()
    return student


def _make_enrollment(student=None, group=None, status="active", full_name="Student One", email="student@example.com"):
    student = student or _make_student(email=email, full_name=full_name)
    group = group or _make_group()
    enrollment = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(enrollment)
    db.session.commit()
    return enrollment


# ======================================================================
# AUTHORIZATION
# ======================================================================


def test_administrator_can_access_enrollments_list(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/enrollments")
    assert resp.status_code == 200


def test_anonymous_user_is_redirected_to_login(client):
    resp = client.get("/admin/enrollments")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_student_cannot_access(app, client):
    with app.app_context():
        _make_student("astudent@example.com")
    login(client, "astudent@example.com")
    assert client.get("/admin/enrollments").status_code == 403


def test_teacher_cannot_access(app, client):
    with app.app_context():
        make_user("ateacher@example.com", UserRole.TEACHER.value)
    login(client, "ateacher@example.com")
    assert client.get("/admin/enrollments").status_code == 403


def test_researcher_cannot_access(app, client):
    with app.app_context():
        make_user("aresearcher@example.com", UserRole.RESEARCHER.value)
    login(client, "aresearcher@example.com")
    assert client.get("/admin/enrollments").status_code == 403


# ======================================================================
# LISTING CONTENT / ROLE ISOLATION
# ======================================================================


def test_only_enrollment_records_are_shown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="Enrolled Student", email="enrolled@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Enrolled Student" in html


def test_student_name_and_email_shown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="Jane Doe", email="jane.doe@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Jane Doe" in html
    assert "jane.doe@example.com" in html


def test_group_name_and_code_shown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="Level Z", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Course Z", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group = Group(academic_term_id=term.id, course_id=course.id, name="Group Z", code="GRZ", capacity=20)
        db.session.add(group)
        db.session.commit()
        _make_enrollment(group=group)
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Group Z" in html
    assert "GRZ" in html


def test_course_and_level_shown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="Level Q", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="Course Q", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group = _make_group(term=term, course=course)
        _make_enrollment(group=group)
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Course Q" in html
    assert "Level Q" in html


def test_academic_term_shown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Summer 2027", start=date(2027, 6, 1), end=date(2027, 8, 31))
        group = _make_group(term=term)
        _make_enrollment(group=group)
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Summer 2027" in html


def test_status_shown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(status="withdrawn", email="withdrawn@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Withdrawn" in html


def test_created_date_shown(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment()
        created_date = enrollment.created_at.strftime("%Y-%m-%d")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert created_date in html


def test_deterministic_ordering_newest_first(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="First Student", email="first@example.com")
        _make_enrollment(full_name="Second Student", email="second@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert html.index("Second Student") < html.index("First Student")


# ======================================================================
# SEARCH
# ======================================================================


def test_search_by_student_full_name_prefix(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="abdulalgadeer Joha", email="aj@example.com")
        _make_enrollment(full_name="Someone Else", email="other@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?q=abd").get_data(as_text=True)
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" not in html


def test_search_by_student_email_prefix(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="Email Match", email="unique.email@example.com")
        _make_enrollment(full_name="Email NoMatch", email="different@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?q=unique.email").get_data(as_text=True)
    assert "Email Match" in html
    assert "Email NoMatch" not in html


def test_search_mid_word_substring_does_not_match(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="abdulalgadeer Joha", email="aj2@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?q=ade").get_data(as_text=True)
    assert "abdulalgadeer Joha" not in html
    assert "No enrollments match your filters" in html


def test_search_percent_sign_treated_literally(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="50% Off Student", email="promo@example.com")
        _make_enrollment(full_name="Regular Student", email="regular@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?q=50%25").get_data(as_text=True)
    assert "50% Off Student" in html
    assert "Regular Student" not in html


def test_search_underscore_treated_literally(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="Off_Deal Student", email="underscore@example.com")
        _make_enrollment(full_name="OffXDeal Student", email="other2@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?q=Off_").get_data(as_text=True)
    assert "Off_Deal Student" in html
    assert "OffXDeal Student" not in html


def test_empty_query_returns_all_enrollments(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="Student A", email="a3@example.com")
        _make_enrollment(full_name="Student B", email="b3@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?q=").get_data(as_text=True)
    assert "Student A" in html
    assert "Student B" in html


# ======================================================================
# FILTERS
# ======================================================================


def test_status_filter_active(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(status="active", full_name="Active Student", email="active1@example.com")
        _make_enrollment(status="withdrawn", full_name="Withdrawn Student", email="withdrawn1@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?status=active").get_data(as_text=True)
    assert "Active Student" in html
    assert "Withdrawn Student" not in html


def test_status_filter_withdrawn(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(status="active", full_name="Active Student2", email="active2@example.com")
        _make_enrollment(status="withdrawn", full_name="Withdrawn Student2", email="withdrawn2@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?status=withdrawn").get_data(as_text=True)
    assert "Withdrawn Student2" in html
    assert "Active Student2" not in html


def test_academic_term_filter(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term Alpha", start=date(2026, 1, 1), end=date(2026, 5, 1))
        term_b = _make_term(name="Term Beta", start=date(2026, 6, 1), end=date(2026, 9, 1))
        group_a = _make_group(term=term_a, name="Group Alpha")
        group_b = _make_group(term=term_b, name="Group Beta")
        _make_enrollment(group=group_a, full_name="Alpha Student", email="alpha@example.com")
        _make_enrollment(group=group_b, full_name="Beta Student", email="beta@example.com")
        term_a_id = term_a.id
    login(client, "admin@example.com")

    html = client.get(f"/admin/enrollments?term_id={term_a_id}").get_data(as_text=True)
    assert "Alpha Student" in html
    assert "Beta Student" not in html


def test_search_and_status_combined(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(status="active", full_name="Karim Active", email="karim.active@example.com")
        _make_enrollment(status="withdrawn", full_name="Karim Withdrawn", email="karim.withdrawn@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?q=Karim&status=active").get_data(as_text=True)
    assert "Karim Active" in html
    assert "Karim Withdrawn" not in html


def test_search_and_term_combined(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _make_term(name="Term Gamma", start=date(2026, 1, 1), end=date(2026, 5, 1))
        term_b = _make_term(name="Term Delta", start=date(2026, 6, 1), end=date(2026, 9, 1))
        group_a = _make_group(term=term_a, name="Group Gamma")
        group_b = _make_group(term=term_b, name="Group Delta")
        _make_enrollment(group=group_a, full_name="Karim Gamma", email="karim.gamma@example.com")
        _make_enrollment(group=group_b, full_name="Karim Delta", email="karim.delta@example.com")
        term_a_id = term_a.id
    login(client, "admin@example.com")

    html = client.get(f"/admin/enrollments?q=Karim&term_id={term_a_id}").get_data(as_text=True)
    assert "Karim Gamma" in html
    assert "Karim Delta" not in html


def test_status_and_term_combined(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Term Epsilon")
        group = _make_group(term=term, name="Group Epsilon")
        other_term = _make_term(name="Term Zeta", start=date(2027, 1, 1), end=date(2027, 5, 1))
        other_group = _make_group(term=other_term, name="Group Zeta")
        _make_enrollment(group=group, status="active", full_name="In Term Active", email="inta@example.com")
        _make_enrollment(group=group, status="withdrawn", full_name="In Term Withdrawn", email="intw@example.com")
        _make_enrollment(group=other_group, status="active", full_name="Other Term Active", email="otta@example.com")
        term_id = term.id
    login(client, "admin@example.com")

    html = client.get(f"/admin/enrollments?status=active&term_id={term_id}").get_data(as_text=True)
    assert "In Term Active" in html
    assert "In Term Withdrawn" not in html
    assert "Other Term Active" not in html


def test_search_status_and_term_combined(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term(name="Term Eta")
        group = _make_group(term=term, name="Group Eta")
        _make_enrollment(group=group, status="active", full_name="Match Everything", email="everything@example.com")
        _make_enrollment(group=group, status="withdrawn", full_name="Match Wrong Status", email="wrongstatus@example.com")
        term_id = term.id
    login(client, "admin@example.com")

    html = client.get(
        f"/admin/enrollments?q=Match&status=active&term_id={term_id}"
    ).get_data(as_text=True)
    assert "Match Everything" in html
    assert "Match Wrong Status" not in html


def test_invalid_status_handled_safely(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="Safe Student", email="safe@example.com")
    login(client, "admin@example.com")

    for bad_status in ["deleted", "'; DROP TABLE users; --", "<script>alert(1)</script>"]:
        resp = client.get(f"/admin/enrollments?status={bad_status}")
        assert resp.status_code == 200
        assert "Safe Student" in resp.get_data(as_text=True)


def test_invalid_term_handled_safely(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="Safe Student2", email="safe2@example.com")
    login(client, "admin@example.com")

    for bad_term in ["not-a-number", "-1", "0", "99999999999999999999"]:
        resp = client.get(f"/admin/enrollments?term_id={bad_term}")
        assert resp.status_code == 200
        assert "Safe Student2" in resp.get_data(as_text=True)


# ======================================================================
# EMPTY STATES
# ======================================================================


def test_normal_empty_state(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "No enrollments yet" in html


def test_filtered_empty_state(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="Real Student", email="real@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments?q=NoSuchPerson").get_data(as_text=True)
    assert "No enrollments match your filters" in html
    assert "No enrollments yet" not in html
    assert 'href="/admin/enrollments"' in html  # clear-all-filters link


# ======================================================================
# SECURITY: XSS / SENSITIVE FIELDS / NO LIFECYCLE ACTIONS
# ======================================================================


def test_xss_in_student_name_is_escaped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(full_name="<script>alert('xss')</script>", email="xss1@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "<script>alert('xss')</script>" not in html
    assert "&lt;script&gt;" in html


def test_xss_in_group_name_is_escaped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = _make_term()
        level = Level(name="XSS Level", display_order=0)
        db.session.add(level)
        db.session.commit()
        course = Course(title="XSS Course", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        group = Group(
            academic_term_id=term.id, course_id=course.id,
            name="<img src=x onerror=alert(1)>", capacity=20,
        )
        db.session.add(group)
        db.session.commit()
        _make_enrollment(group=group, email="xssgroup@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img" in html


def test_password_hash_not_rendered(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="secret@example.com")
        password_hash = student.password_hash
        _make_enrollment(student=student)
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert password_hash not in html
    assert "$argon2" not in html


def test_internal_ids_not_rendered(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        enrollment = _make_enrollment(email="internalid@example.com")
        enrollment_id, student_id, group_id = enrollment.id, enrollment.student_id, enrollment.group_id
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert f">{enrollment_id}<" not in html
    assert f">{student_id}<" not in html
    assert f">{group_id}<" not in html


def test_auth_version_not_rendered(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(email="authversion@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "auth_version" not in html.lower()


def test_no_lifecycle_action_controls_present(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_enrollment(email="nolifecycle@example.com")
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "New Enrollment" not in html
    assert ">Edit<" not in html
    assert ">Withdraw<" not in html
    assert ">Reactivate<" not in html
    assert ">Transfer<" not in html
    assert ">Delete<" not in html
    assert ">Actions<" not in html


def test_role_leakage_student_referenced_user_is_displayed(app, client):
    """A baseline: an Enrollment referencing an actual Student is a valid
    Student enrollment and must appear in the listing.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = _make_student(email="realstudent@example.com", full_name="Real Student")
        group = _make_group()
        enrollment = Enrollment(student_id=student.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Real Student" in html


def test_role_leakage_teacher_referenced_user_is_excluded(app, client):
    """A manually-seeded Enrollment row referencing a Teacher (the FK
    alone cannot prevent this -- see test_enrollments.py) must NOT be
    treated as valid Student enrollment data: it must not appear in the
    Student Enrollment listing at all.
    """
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = make_user("nonstudent@example.com", UserRole.TEACHER.value, full_name="Not A Student")
        group = _make_group()
        enrollment = Enrollment(student_id=teacher.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()
    login(client, "admin@example.com")

    resp = client.get("/admin/enrollments")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Not A Student" not in html
    assert "No enrollments yet" in html


def test_role_leakage_administrator_referenced_user_is_excluded(app, client):
    """Same guarantee as the Teacher case, for an Administrator-backed row."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        rogue_admin = make_user(
            "nonstudent2@example.com", UserRole.ADMINISTRATOR.value, full_name="Not A Student Either"
        )
        group = _make_group()
        enrollment = Enrollment(student_id=rogue_admin.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Not A Student Either" not in html


def test_role_leakage_researcher_referenced_user_is_excluded(app, client):
    """Same guarantee as the Teacher case, for a Researcher-backed row."""
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        researcher = make_user(
            "nonstudent3@example.com", UserRole.RESEARCHER.value, full_name="Not A Student At All"
        )
        group = _make_group()
        enrollment = Enrollment(student_id=researcher.id, group_id=group.id)
        db.session.add(enrollment)
        db.session.commit()
    login(client, "admin@example.com")

    html = client.get("/admin/enrollments").get_data(as_text=True)
    assert "Not A Student At All" not in html


# ======================================================================
# NAVIGATION
# ======================================================================


def test_enrollments_navigation_link_present_and_working(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert 'href="/admin/enrollments"' in html
    assert client.get("/admin/enrollments").status_code == 200
