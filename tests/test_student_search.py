"""M13 Student search route -- role boundary, display states, filters,
non-disclosure, valid deep links + public-id anchors, no leaked internal
data, and private/non-cacheable headers.
"""

from datetime import date, datetime, timezone

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    Lesson,
    LessonStatus,
    Level,
    Material,
    MaterialKind,
    Unit,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login, make_user

PW = "Sup3rSecret!123"
ACTIVE = AcademicStatus.ACTIVE.value
URL = "/student/search"


def _user(email, role, status=UserStatus.ACTIVE.value):
    u = User(email=email, password_hash=hash_password(PW), full_name=email.split("@")[0],
             role=role, status=status)
    db.session.add(u)
    db.session.commit()
    return u


def _full_group(name="A", course_title="General English", code="ENG101"):
    term = AcademicTerm(name=f"Term {name}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    level = Level(name=f"Level {name}", display_order=0)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=course_title, code=code, description="Grammar practice",
                    level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=f"Group {name}", capacity=30)
    db.session.add(group)
    db.session.commit()
    return group


def _content(group, unit_kw=None):
    u = Unit(group_id=group.id, title="Present Tenses", display_order=0, search_keywords=unit_kw)
    db.session.add(u)
    db.session.commit()
    lsn = Lesson(unit_id=u.id, title="Present Perfect", display_order=0,
                 status=LessonStatus.PUBLISHED.value, published_at=datetime.now(timezone.utc))
    db.session.add(lsn)
    db.session.commit()
    m = Material(lesson_id=lsn.id, title="Present Homework", kind=MaterialKind.EXTERNAL_LINK.value,
                 external_url="https://example.com/present", status=ACTIVE, display_order=1,
                 creation_nonce="n-present")
    db.session.add(m)
    db.session.commit()
    return u, lsn, m


def _setup(email="stud@example.com"):
    s = _user(email, UserRole.STUDENT.value)
    g = _full_group()
    db.session.add(Enrollment(student_id=s.id, group_id=g.id, status=EnrollmentStatus.ACTIVE.value))
    db.session.commit()
    u, lsn, m = _content(g)
    return s, g, u, lsn, m


# ===========================================================================
# Role boundary
# ===========================================================================


def test_anonymous_redirected_to_login(app, client):
    resp = client.get(URL)
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_other_roles_forbidden(app, client):
    with app.app_context():
        make_user("t@example.com", UserRole.TEACHER.value)
        make_user("a@example.com", UserRole.ADMINISTRATOR.value)
        make_user("r@example.com", UserRole.RESEARCHER.value)
    for email in ("t@example.com", "a@example.com", "r@example.com"):
        login(client, email)
        assert client.get(URL).status_code == 403


def test_student_gets_page(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    assert client.get(URL).status_code == 200


# ===========================================================================
# Display states
# ===========================================================================


def test_initial_state_no_query(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert "Search your learning content" in html
    assert "Search tips" in html


def test_too_short_state(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "a"}).get_data(as_text=True)
    assert "at least 2 characters" in html


def test_no_results_state(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "quantum physics"}).get_data(as_text=True)
    assert "No matching content" in html


def test_results_grouped_by_type(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "present"}).get_data(as_text=True)
    assert "Present Tenses" in html and "Present Perfect" in html and "Present Homework" in html
    assert "<h2>Units</h2>" in html and "<h2>Lessons</h2>" in html


def test_capped_group_shows_refine_notice(app, client):
    with app.app_context():
        s, g, u, lsn, m = _setup()
        for i in range(25):
            db.session.add(Lesson(unit_id=u.id, title=f"Present Extra {i:02d}", display_order=i + 5,
                                  status=LessonStatus.PUBLISHED.value,
                                  published_at=datetime.now(timezone.utc)))
        db.session.commit()
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "present"}).get_data(as_text=True)
    assert "Add another word or a filter to narrow" in html


# ===========================================================================
# Filters
# ===========================================================================


def test_type_filter_limits_sections(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "present", "type": "unit"}).get_data(as_text=True)
    assert "<h2>Units</h2>" in html
    assert "<h2>Lessons</h2>" not in html


def test_invalid_type_and_kind_normalise_to_all(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    resp = client.get(URL, query_string={"q": "present", "type": "'; DROP", "kind": "xxx"})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "<h2>Units</h2>" in html and "<h2>Lessons</h2>" in html


def test_group_filter_by_public_id(app, client):
    with app.app_context():
        s = _user("stud@example.com", UserRole.STUDENT.value)
        g1 = _full_group("One", course_title="Alpha Course", code="P1")
        g2 = _full_group("Two", course_title="Beta Course", code="P2")
        for g in (g1, g2):
            db.session.add(Enrollment(student_id=s.id, group_id=g.id,
                                      status=EnrollmentStatus.ACTIVE.value))
        db.session.commit()
        g1_pid = g1.public_id
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "course", "group": g1_pid}).get_data(as_text=True)
    # both course titles appear as <option> labels; only the filtered one
    # appears as a result link (`...</a>`).
    assert "Alpha Course</a>" in html
    assert "Beta Course</a>" not in html


def test_unauthorized_group_is_non_disclosing(app, client):
    with app.app_context():
        _setup()
        other = _full_group("Secret", course_title="Present Secret", code="SEC")
        other_pid = other.public_id
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "present", "group": other_pid}).get_data(as_text=True)
    assert "No matching content" in html
    assert "Present Secret" not in html
    # a totally bogus id behaves identically
    html2 = client.get(URL, query_string={"q": "present", "group": "nope"}).get_data(as_text=True)
    assert "No matching content" in html2


def test_clear_filters_link_preserves_query(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "present", "type": "unit"}).get_data(as_text=True)
    assert "q=present" in html
    assert "Clear filters" in html


# ===========================================================================
# Deep links + anchors
# ===========================================================================


def test_result_links_are_valid_and_use_public_id_anchors(app, client):
    with app.app_context():
        s, g, u, lsn, m = _setup()
        gpid, upid, lpid, mpid = g.public_id, u.public_id, lsn.public_id, m.public_id
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "present"}).get_data(as_text=True)
    assert f"/student/groups/{gpid}/units#unit-{upid}" in html
    assert f"/student/groups/{gpid}/units/{upid}/lessons/{lpid}" in html
    assert f"#material-{mpid}" in html
    # and the links actually resolve
    assert client.get(f"/student/groups/{gpid}/units").status_code == 200
    lesson_html = client.get(
        f"/student/groups/{gpid}/units/{upid}/lessons/{lpid}"
    ).get_data(as_text=True)
    assert f'id="material-{mpid}"' in lesson_html
    outline_html = client.get(f"/student/groups/{gpid}/units").get_data(as_text=True)
    assert f'id="unit-{upid}"' in outline_html


# ===========================================================================
# No leakage
# ===========================================================================


def test_no_internal_ids_storage_keys_or_raw_html_leaked(app, client):
    with app.app_context():
        s, g, u, lsn, m = _setup()
        # add a file material with a rich-text sibling containing HTML
        uf = UploadedFile(storage_key="secretkey123", original_filename="present-notes.pdf",
                          extension="pdf", category="document", content_type="application/pdf",
                          byte_size=5, sha256="a" * 64, uploaded_by_id=s.id)
        db.session.add(uf)
        db.session.commit()
        db.session.add(Material(lesson_id=lsn.id, title="Present File", kind=MaterialKind.FILE.value,
                                uploaded_file_id=uf.id, status=ACTIVE, display_order=2,
                                creation_nonce="n-file"))
        db.session.add(Material(lesson_id=lsn.id, title="Present Rich",
                                kind=MaterialKind.RICH_TEXT.value,
                                content_html="<p>hidden <strong>markup</strong></p>",
                                status=ACTIVE, display_order=3, creation_nonce="n-rich"))
        db.session.commit()
        gid, uid, lid = g.id, u.id, lsn.id
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "present"}).get_data(as_text=True)
    assert "secretkey123" not in html
    assert "a" * 64 not in html
    assert "hidden <strong>markup" not in html
    assert f"/groups/{gid}/" not in html
    assert f"/units/{uid}/" not in html
    assert f"/lessons/{lid}/" not in html
    assert f"/lessons/{lid}#" not in html


def test_unauthorized_content_titles_never_appear(app, client):
    with app.app_context():
        _setup()
        # content in a group the student is NOT enrolled in
        other = _full_group("Other", course_title="Present Other", code="OTH")
        ou = Unit(group_id=other.id, title="Present Secret Unit", display_order=0)
        db.session.add(ou)
        db.session.commit()
        db.session.add(Lesson(unit_id=ou.id, title="Present Secret Lesson", display_order=0,
                              status=LessonStatus.PUBLISHED.value,
                              published_at=datetime.now(timezone.utc)))
        db.session.commit()
    login(client, "stud@example.com")
    html = client.get(URL, query_string={"q": "present"}).get_data(as_text=True)
    assert "Secret Unit" not in html
    assert "Secret Lesson" not in html
    assert "Present Other" not in html


# ===========================================================================
# Headers + nav
# ===========================================================================


def test_response_is_private_and_non_cacheable(app, client):
    with app.app_context():
        _setup()
    login(client, "stud@example.com")
    resp = client.get(URL, query_string={"q": "present"})
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_search_link_in_student_nav_not_teacher_nav(app, client):
    with app.app_context():
        _setup()
        make_user("teach@example.com", UserRole.TEACHER.value)
    login(client, "stud@example.com")
    dash = client.get("/student/dashboard").get_data(as_text=True)
    assert 'href="/student/search"' in dash
    resp = client.post("/auth/logout", follow_redirects=True)
    assert resp.status_code == 200
    login(client, "teach@example.com")
    tdash = client.get("/teacher/dashboard")
    assert tdash.status_code == 200
    assert "/student/search" not in tdash.get_data(as_text=True)
