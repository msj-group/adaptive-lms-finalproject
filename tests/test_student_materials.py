"""Student Material visibility + authorized file serving (M12).

Extends the M11 Student Lesson-detail SQL-scoped authorization to
Materials: active Material + published Lesson + active hierarchy + own
active Enrollment. Draft lessons, archived materials, inactive ancestry,
withdrawn enrollment, cross-group and mismatched-nested-id access all
return non-disclosing 404s. Serving persists the access log and fails
closed.
"""

from datetime import date, datetime, timezone

import pytest

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    FileAccessLog,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
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
from tests import file_fixtures as ff
from tests.conftest import login, make_user

PW = "Sup3rSecret!123"


def _user(email, role, status=UserStatus.ACTIVE.value):
    u = User(email=email, password_hash=hash_password(PW), full_name=email.split("@")[0],
             role=role, status=status)
    db.session.add(u)
    db.session.commit()
    return u


def _hierarchy(term_status=AcademicStatus.ACTIVE.value, level_status=AcademicStatus.ACTIVE.value,
               course_status=AcademicStatus.ACTIVE.value, group_status=AcademicStatus.ACTIVE.value,
               group_name="G"):
    term = AcademicTerm(name=f"T{group_name}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                        status=term_status)
    db.session.add(term)
    level = Level(name=f"L{group_name}", display_order=0, status=level_status)
    db.session.add(level)
    db.session.commit()
    course = Course(title=f"C{group_name}", level_id=level.id, display_order=0, status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=group_name, capacity=20,
                  status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _unit(group, status=AcademicStatus.ACTIVE.value):
    u = Unit(group_id=group.id, title=f"U{group.name}", display_order=0, status=status)
    db.session.add(u)
    db.session.commit()
    return u


_LESSON_SEQ = [0]


def _lesson(unit, status=LessonStatus.PUBLISHED.value, title=None):
    _LESSON_SEQ[0] += 1
    published_at = datetime.now(timezone.utc) if status == LessonStatus.PUBLISHED.value else None
    lsn = Lesson(unit_id=unit.id, title=title or f"Lsn{_LESSON_SEQ[0]}", display_order=0,
                 status=status, published_at=published_at)
    db.session.add(lsn)
    db.session.commit()
    return lsn


def _enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=status))
    db.session.commit()


def _rich(lesson, title="Notes", html="<p>Hello <strong>world</strong></p>", order=1,
          status=AcademicStatus.ACTIVE.value):
    m = Material(lesson_id=lesson.id, title=title, kind=MaterialKind.RICH_TEXT.value,
                 content_html=html, display_order=order, status=status,
                 creation_nonce=f"rt-{title}-{order}")
    db.session.add(m)
    db.session.commit()
    return m


def _link(lesson, title="Link", url="https://example.com/x", order=1,
          status=AcademicStatus.ACTIVE.value):
    m = Material(lesson_id=lesson.id, title=title, kind=MaterialKind.EXTERNAL_LINK.value,
                 external_url=url, display_order=order, status=status,
                 creation_nonce=f"ln-{title}-{order}")
    db.session.add(m)
    db.session.commit()
    return m


def _file_material(lesson, uploader, category="document", ext="pdf", order=1,
                   status=AcademicStatus.ACTIVE.value, title="Handout", storage_key=None):
    key = storage_key or f"key-{title}-{order}"
    uf = UploadedFile(storage_key=key, original_filename=f"{title}.{ext}", extension=ext,
                      category=category, content_type="application/pdf", byte_size=10,
                      sha256="0" * 64, uploaded_by_id=uploader.id)
    db.session.add(uf)
    db.session.commit()
    m = Material(lesson_id=lesson.id, title=title, kind=MaterialKind.FILE.value,
                 uploaded_file_id=uf.id, display_order=order, status=status,
                 creation_nonce=f"fl-{title}-{order}")
    db.session.add(m)
    db.session.commit()
    return m, uf


def _lesson_url(g, u, l):
    return f"/student/groups/{g}/units/{u}/lessons/{l}"


def _mat_url(g, u, l, m, action):
    return f"/student/groups/{g}/units/{u}/lessons/{l}/materials/{m}/{action}"


def _serve_get(client, url, **kw):
    resp = client.get(url, buffered=True, **kw)
    resp.get_data()
    resp.close()
    return resp


def _scene(app, add_materials=None, email="s@example.com"):
    """All setup in one app context; returns (gp, up, lp) strings."""
    with app.app_context():
        student = _user(email, UserRole.STUDENT.value)
        group = _hierarchy()
        unit = _unit(group)
        lesson = _lesson(unit)
        _enroll(group, student)
        if add_materials is not None:
            add_materials(lesson)
        return group.public_id, unit.public_id, lesson.public_id


# ===========================================================================
# Rendering on the Lesson detail page
# ===========================================================================


def test_active_materials_rendered_in_order(app, client):
    def add(l):
        _rich(l, title="Second", order=2, html="<p>two</p>")
        _rich(l, title="First", order=1, html="<p>one</p>")
    gp, up, lp = _scene(app, add)
    login(client, "s@example.com")
    html = client.get(_lesson_url(gp, up, lp)).get_data(as_text=True)
    assert html.index("one") < html.index("two")


def test_rich_text_rendered_sanitized(app, client):
    gp, up, lp = _scene(app, lambda l: _rich(l, html="<p>ok <strong>b</strong></p><script>x()</script>"))
    login(client, "s@example.com")
    html = client.get(_lesson_url(gp, up, lp)).get_data(as_text=True)
    assert "<strong>b</strong>" in html
    assert "<script>x()" not in html


def test_archived_material_not_rendered(app, client):
    def add(l):
        _rich(l, title="Live", order=1, html="<p>live</p>")
        _rich(l, title="Gone", order=2, html="<p>archived text</p>", status=AcademicStatus.ARCHIVED.value)
    gp, up, lp = _scene(app, add)
    login(client, "s@example.com")
    html = client.get(_lesson_url(gp, up, lp)).get_data(as_text=True)
    assert "live" in html and "archived text" not in html


def test_external_link_rendered_with_safe_rel(app, client):
    gp, up, lp = _scene(app, lambda l: _link(l, url="https://example.com/resource"))
    login(client, "s@example.com")
    html = client.get(_lesson_url(gp, up, lp)).get_data(as_text=True)
    assert 'href="https://example.com/resource"' in html
    assert 'rel="noopener noreferrer nofollow"' in html


def test_no_materials_empty_state(app, client):
    gp, up, lp = _scene(app)
    login(client, "s@example.com")
    html = client.get(_lesson_url(gp, up, lp)).get_data(as_text=True)
    assert "No materials yet" in html


def test_no_internal_ids_or_teacher_identity(app, client):
    with app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("secretteacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        unit = _unit(group)
        lesson = _lesson(unit)
        _enroll(group, student)
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id,
                                              status=GroupTeacherAssignmentStatus.ACTIVE.value))
        db.session.commit()
        m, uf = _file_material(lesson, teacher)
        gp, up, lp = group.public_id, unit.public_id, lesson.public_id
        gid, uid, lid, mid, ufid = group.id, unit.id, lesson.id, m.id, uf.id
        skey = uf.storage_key
    login(client, "s@example.com")
    html = client.get(_lesson_url(gp, up, lp)).get_data(as_text=True)
    assert "secretteacher" not in html
    assert skey not in html
    for leaked in (f"/lessons/{lid}/", f'value="{mid}"', f'value="{ufid}"', f"/groups/{gid}/"):
        assert leaked not in html


# ===========================================================================
# Authorized file serving
# ===========================================================================


def test_student_download_pdf_attachment_and_audit(material_app, material_client):
    with material_app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        unit = _unit(group)
        lesson = _lesson(unit)
        _enroll(group, student)
        mc = material_app.extensions["material_config"]
        mc.storage_root.mkdir(parents=True, exist_ok=True)
        key = "abc123.pdf"
        (mc.storage_root / key).write_bytes(ff.minimal_pdf())
        m, uf = _file_material(lesson, teacher, storage_key=key)
        gp, up, lp, mp, ufid = group.public_id, unit.public_id, lesson.public_id, m.public_id, uf.id
    login(material_client, "s@example.com")
    r = _serve_get(material_client, _mat_url(gp, up, lp, mp, "download"))
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in r.headers["Cache-Control"]
    with material_app.app_context():
        actions = [x.action for x in FileAccessLog.query.filter_by(uploaded_file_id=ufid).all()]
        assert actions == ["download"]


def test_student_open_image_inline_and_audit(material_app, material_client):
    with material_app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        unit = _unit(group)
        lesson = _lesson(unit)
        _enroll(group, student)
        mc = material_app.extensions["material_config"]
        mc.storage_root.mkdir(parents=True, exist_ok=True)
        key = "img.png"
        (mc.storage_root / key).write_bytes(ff.minimal_png())
        m, uf = _file_material(lesson, teacher, category="image", ext="png", storage_key=key)
        gp, up, lp, mp, ufid = group.public_id, unit.public_id, lesson.public_id, m.public_id, uf.id
    login(material_client, "s@example.com")
    r = _serve_get(material_client, _mat_url(gp, up, lp, mp, "open"))
    assert r.status_code == 200
    assert "attachment" not in r.headers.get("Content-Disposition", "")
    with material_app.app_context():
        actions = [x.action for x in FileAccessLog.query.filter_by(uploaded_file_id=ufid).all()]
        assert actions == ["inline"]


# ===========================================================================
# Authorization / IDOR -- non-disclosing 404s
# ===========================================================================


def _authz_setup(app, **overrides):
    with app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        group = _hierarchy(**{k: v for k, v in overrides.items() if k.endswith("_status") and k.startswith(("term", "level", "course", "group"))})
        unit = _unit(group, status=overrides.get("unit_status", AcademicStatus.ACTIVE.value))
        lesson = _lesson(unit, status=overrides.get("lesson_status", LessonStatus.PUBLISHED.value))
        enroll_status = overrides.get("enroll_status", EnrollmentStatus.ACTIVE.value)
        if overrides.get("enrolled", True):
            _enroll(group, student, status=enroll_status)
        m, uf = _file_material(lesson, teacher, status=overrides.get("material_status", AcademicStatus.ACTIVE.value))
        return group.public_id, unit.public_id, lesson.public_id, m.public_id


@pytest.mark.parametrize("action", ["open", "download"])
def test_draft_lesson_material_404(app, client, action):
    gp, up, lp, mp = _authz_setup(app, lesson_status=LessonStatus.DRAFT.value)
    login(client, "s@example.com")
    assert client.get(_mat_url(gp, up, lp, mp, action)).status_code == 404


@pytest.mark.parametrize("action", ["open", "download"])
def test_archived_material_404(app, client, action):
    gp, up, lp, mp = _authz_setup(app, material_status=AcademicStatus.ARCHIVED.value)
    login(client, "s@example.com")
    assert client.get(_mat_url(gp, up, lp, mp, action)).status_code == 404


@pytest.mark.parametrize("archived", ["term_status", "level_status", "course_status", "group_status", "unit_status"])
def test_inactive_ancestry_404(app, client, archived):
    gp, up, lp, mp = _authz_setup(app, **{archived: AcademicStatus.ARCHIVED.value})
    login(client, "s@example.com")
    assert client.get(_mat_url(gp, up, lp, mp, "open")).status_code == 404


def test_withdrawn_enrollment_404(app, client):
    gp, up, lp, mp = _authz_setup(app, enroll_status=EnrollmentStatus.WITHDRAWN.value)
    login(client, "s@example.com")
    assert client.get(_mat_url(gp, up, lp, mp, "download")).status_code == 404


def test_non_enrolled_student_404(app, client):
    gp, up, lp, mp = _authz_setup(app, enrolled=False)
    with app.app_context():
        _user("outsider@example.com", UserRole.STUDENT.value)
    login(client, "outsider@example.com")
    assert client.get(_mat_url(gp, up, lp, mp, "open")).status_code == 404


def test_cross_group_material_404(app, client):
    with app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        mine = _hierarchy(group_name="Mine")
        theirs = _hierarchy(group_name="Theirs")
        _enroll(mine, student)
        u_theirs = _unit(theirs)
        l_theirs = _lesson(u_theirs)
        m, _ = _file_material(l_theirs, teacher)
        my_gp = mine.public_id
        their_up, their_lp, their_mp = u_theirs.public_id, l_theirs.public_id, m.public_id
    login(client, "s@example.com")
    assert client.get(_mat_url(my_gp, their_up, their_lp, their_mp, "open")).status_code == 404


def test_mismatched_nested_ids_404(app, client):
    with app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        unit = _unit(group)
        l1 = _lesson(unit)
        l2 = _lesson(unit)
        _enroll(group, student)
        m, _ = _file_material(l1, teacher)  # belongs to l1
        gp, up, l2p, mp = group.public_id, unit.public_id, l2.public_id, m.public_id
    login(client, "s@example.com")
    assert client.get(_mat_url(gp, up, l2p, mp, "open")).status_code == 404


def test_rich_text_material_public_id_on_file_route_404(app, client):
    with app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        group = _hierarchy()
        unit = _unit(group)
        lesson = _lesson(unit)
        _enroll(group, student)
        m = _rich(lesson)
        gp, up, lp, mp = group.public_id, unit.public_id, lesson.public_id, m.public_id
    login(client, "s@example.com")
    assert client.get(_mat_url(gp, up, lp, mp, "open")).status_code == 404
    assert client.get(_mat_url(gp, up, lp, mp, "download")).status_code == 404


def test_non_student_roles_forbidden(app, client):
    gp, up, lp, mp = _authz_setup(app)
    with app.app_context():
        make_user("adm@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "adm@example.com")
    assert client.get(_mat_url(gp, up, lp, mp, "open")).status_code == 403


def test_anonymous_redirected(app, client):
    gp, up, lp, mp = _authz_setup(app)
    r = client.get(_mat_url(gp, up, lp, mp, "download"))
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]


def test_serving_routes_are_get_only(app, client):
    gp, up, lp, mp = _authz_setup(app)
    login(client, "s@example.com")
    assert client.post(_mat_url(gp, up, lp, mp, "open")).status_code == 405
    assert client.post(_mat_url(gp, up, lp, mp, "download")).status_code == 405


def test_student_access_independent_of_teacher_assignment_and_schedule(material_app, material_client):
    with material_app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        unit = _unit(group)
        lesson = _lesson(unit)
        _enroll(group, student)
        db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id,
                                              status=GroupTeacherAssignmentStatus.REMOVED.value))
        db.session.commit()
        mc = material_app.extensions["material_config"]
        mc.storage_root.mkdir(parents=True, exist_ok=True)
        (mc.storage_root / "k.pdf").write_bytes(ff.minimal_pdf())
        m, _uf = _file_material(lesson, teacher, storage_key="k.pdf")
        gp, up, lp, mp = group.public_id, unit.public_id, lesson.public_id, m.public_id
    login(material_client, "s@example.com")
    assert material_client.get(_lesson_url(gp, up, lp)).status_code == 200
    r = _serve_get(material_client, _mat_url(gp, up, lp, mp, "download"))
    assert r.status_code == 200


def test_missing_physical_file_student_fails_safely(material_app, material_client):
    with material_app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        unit = _unit(group)
        lesson = _lesson(unit)
        _enroll(group, student)
        m, uf = _file_material(lesson, teacher, storage_key="never-written.pdf")
        gp, up, lp, mp, ufid = group.public_id, unit.public_id, lesson.public_id, m.public_id, uf.id
    login(material_client, "s@example.com")
    r = _serve_get(material_client, _mat_url(gp, up, lp, mp, "download"))
    assert r.status_code == 404
    with material_app.app_context():
        assert FileAccessLog.query.filter_by(uploaded_file_id=ufid).count() == 0


def test_student_outline_and_lesson_query_count_bounded(app, client):
    with app.app_context():
        student = _user("s@example.com", UserRole.STUDENT.value)
        teacher = _user("t@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        unit = _unit(group)
        lesson = _lesson(unit)
        _enroll(group, student)
        for i in range(8):
            _rich(lesson, title=f"M{i}", order=i + 1, html=f"<p>body {i}</p>")
        gp, up, lp = group.public_id, unit.public_id, lesson.public_id
    login(client, "s@example.com")

    from sqlalchemy import event

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        resp = client.get(_lesson_url(gp, up, lp))
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    assert resp.status_code == 200
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(selects) <= 6, (len(selects), selects)
