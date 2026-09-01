"""M13 Teacher-defined ``search_keywords`` on Units / Lessons /
Materials -- normalisation on save, escaped plain-text rendering,
validation rejection, and the stale-edit snapshot now covering the
keyword field. Reuses the M10/M11/M12 authorization + lock + stale-form
machinery unchanged.
"""

import re
from datetime import date, datetime, timezone

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
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
from tests.conftest import login

PW = "Sup3rSecret!123"
ACTIVE = AcademicStatus.ACTIVE.value


def _user(email, role):
    u = User(email=email, password_hash=hash_password(PW), full_name=email.split("@")[0],
             role=role, status=UserStatus.ACTIVE.value)
    db.session.add(u)
    db.session.commit()
    return u


def _setup(email="teacher@example.com"):
    teacher = _user(email, UserRole.TEACHER.value)
    term = AcademicTerm(name="Term", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    level = Level(name="Level", display_order=0)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title="English", level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20)
    db.session.add(group)
    db.session.commit()
    db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id,
                                          status=GroupTeacherAssignmentStatus.ACTIVE.value))
    db.session.commit()
    return teacher, group


def _unit(group, title="Unit 1", keywords=None):
    u = Unit(group_id=group.id, title=title, display_order=0, status=ACTIVE, search_keywords=keywords)
    db.session.add(u)
    db.session.commit()
    return u


def _lesson(unit, title="Lesson 1", keywords=None):
    lsn = Lesson(unit_id=unit.id, title=title, display_order=0, status=LessonStatus.DRAFT.value,
                 search_keywords=keywords)
    db.session.add(lsn)
    db.session.commit()
    return lsn


def _snapshot(client, url):
    html = client.get(url).get_data(as_text=True)
    m = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return m.group(1) if m else ""


def _create_token(client, url):
    html = client.get(url).get_data(as_text=True)
    m = re.search(r'name="create_token" value="([^"]*)"', html)
    return m.group(1) if m else ""


# ===========================================================================
# Unit keywords
# ===========================================================================


def test_unit_create_stores_normalised_keywords(app, client):
    with app.app_context():
        _, g = _setup()
        gpid = g.public_id
    login(client, "teacher@example.com")
    client.post(f"/teacher/groups/{gpid}/units/new", data={
        "title": "Grammar", "description": "",
        "search_keywords": "Present Perfect, present   perfect\npast simple",
    }, follow_redirects=True)
    with app.app_context():
        u = Unit.query.filter_by(title="Grammar").first()
        assert u.search_keywords == "Present Perfect, past simple"


def test_unit_edit_changes_keywords_and_clears_them(app, client):
    with app.app_context():
        _, g = _setup()
        u = _unit(g, keywords="old, stale")
        gpid, upid = g.public_id, u.public_id
    login(client, "teacher@example.com")
    edit_url = f"/teacher/groups/{gpid}/units/{upid}/edit"
    snap = _snapshot(client, edit_url)
    client.post(edit_url, data={"title": "Unit 1", "description": "",
                                "search_keywords": "fresh, terms", "edit_snapshot": snap},
                follow_redirects=True)
    with app.app_context():
        assert Unit.query.filter_by(public_id=upid).first().search_keywords == "fresh, terms"
    snap2 = _snapshot(client, edit_url)
    client.post(edit_url, data={"title": "Unit 1", "description": "",
                                "search_keywords": "", "edit_snapshot": snap2},
                follow_redirects=True)
    with app.app_context():
        assert Unit.query.filter_by(public_id=upid).first().search_keywords is None


def test_unit_keywords_rejected_when_too_many(app, client):
    with app.app_context():
        _, g = _setup()
        gpid = g.public_id
    login(client, "teacher@example.com")
    resp = client.post(f"/teacher/groups/{gpid}/units/new", data={
        "title": "Bad", "description": "",
        "search_keywords": ", ".join(f"k{i}" for i in range(21)),
    }, follow_redirects=True)
    assert b"at most 20 keywords" in resp.data
    with app.app_context():
        assert Unit.query.filter_by(title="Bad").first() is None


def test_unit_keywords_control_char_rejected(app, client):
    with app.app_context():
        _, g = _setup()
        gpid = g.public_id
    login(client, "teacher@example.com")
    resp = client.post(f"/teacher/groups/{gpid}/units/new", data={
        "title": "Bad", "description": "", "search_keywords": "good\x07bell",
    }, follow_redirects=True)
    assert b"control characters" in resp.data
    with app.app_context():
        assert Unit.query.filter_by(title="Bad").first() is None


def test_unit_keywords_rendered_escaped(app, client):
    with app.app_context():
        _, g = _setup()
        u = _unit(g, keywords="<script>alpha</script>")
        gpid, upid = g.public_id, u.public_id
    login(client, "teacher@example.com")
    html = client.get(f"/teacher/groups/{gpid}/units/{upid}/edit").get_data(as_text=True)
    assert "<script>alpha</script>" not in html
    assert "&lt;script&gt;alpha&lt;/script&gt;" in html


# ===========================================================================
# Unit keyword stale-form protection
# ===========================================================================


def test_unit_keyword_edit_is_stale_after_concurrent_keyword_change(app, client):
    with app.app_context():
        _, g = _setup()
        u = _unit(g, keywords="original")
        gpid, upid = g.public_id, u.public_id
    login(client, "teacher@example.com")
    edit_url = f"/teacher/groups/{gpid}/units/{upid}/edit"
    snap = _snapshot(client, edit_url)
    with app.app_context():
        row = Unit.query.filter_by(public_id=upid).first()
        row.search_keywords = "changed by co-teacher"
        db.session.commit()
    resp = client.post(edit_url, data={"title": "Unit 1", "description": "",
                                       "search_keywords": "mine", "edit_snapshot": snap},
                       follow_redirects=True)
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Unit.query.filter_by(public_id=upid).first().search_keywords == "changed by co-teacher"


def test_pre_m13_shape_snapshot_token_rejected(app, client):
    """A snapshot token missing `search_keywords` (pre-M13 shape) is
    wrong-shaped and rejected without mutation."""
    from itsdangerous import URLSafeSerializer

    with app.app_context():
        _, g = _setup()
        u = _unit(g, keywords="keep")
        gpid, upid = g.public_id, u.public_id
        secret = app.config["SECRET_KEY"]
    old_token = URLSafeSerializer(secret, salt="teacher.unit-edit-snapshot.v1").dumps(
        {"public_id": upid, "title": "Unit 1", "description": None}
    )
    login(client, "teacher@example.com")
    resp = client.post(f"/teacher/groups/{gpid}/units/{upid}/edit",
                       data={"title": "Hacked", "description": "", "search_keywords": "x",
                             "edit_snapshot": old_token},
                       follow_redirects=True)
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        row = Unit.query.filter_by(public_id=upid).first()
        assert row.title == "Unit 1" and row.search_keywords == "keep"


# ===========================================================================
# Lesson keywords
# ===========================================================================


def test_lesson_create_and_edit_keywords(app, client):
    with app.app_context():
        _, g = _setup()
        u = _unit(g)
        gpid, upid = g.public_id, u.public_id
    login(client, "teacher@example.com")
    client.post(f"/teacher/groups/{gpid}/units/{upid}/lessons/new", data={
        "title": "Reading", "description": "", "search_keywords": "skimming, scanning",
    }, follow_redirects=True)
    with app.app_context():
        lsn = Lesson.query.filter_by(title="Reading").first()
        assert lsn.search_keywords == "skimming, scanning"
        lpid = lsn.public_id
    edit_url = f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/edit"
    snap = _snapshot(client, edit_url)
    client.post(edit_url, data={"title": "Reading", "description": "",
                                "search_keywords": "gist", "edit_snapshot": snap},
                follow_redirects=True)
    with app.app_context():
        assert Lesson.query.filter_by(public_id=lpid).first().search_keywords == "gist"


# ===========================================================================
# Material keywords -- rich text + file edit (title + keywords)
# ===========================================================================


def test_rich_text_material_create_with_keywords(app, client):
    with app.app_context():
        _, g = _setup()
        u = _unit(g)
        lsn = _lesson(u)
        gpid, upid, lpid = g.public_id, u.public_id, lsn.public_id
    login(client, "teacher@example.com")
    base = f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/materials"
    token = _create_token(client, f"{base}/new/rich-text")
    client.post(f"{base}/new/rich-text", data={
        "title": "Notes", "content_html": "<p>hi</p>",
        "search_keywords": "vocab, revision", "create_token": token,
    }, follow_redirects=True)
    with app.app_context():
        m = Material.query.filter_by(title="Notes").first()
        assert m.search_keywords == "vocab, revision"


def test_file_material_edit_writes_title_and_keywords_only(app, client):
    with app.app_context():
        teacher, g = _setup()
        u = _unit(g)
        lsn = _lesson(u)
        uf = UploadedFile(storage_key="k1", original_filename="notes.pdf", extension="pdf",
                          category="document", content_type="application/pdf", byte_size=10,
                          sha256="0" * 64, uploaded_by_id=teacher.id)
        db.session.add(uf)
        db.session.commit()
        m = Material(lesson_id=lsn.id, title="Old", kind=MaterialKind.FILE.value,
                     uploaded_file_id=uf.id, status=ACTIVE, display_order=1, creation_nonce="n1")
        db.session.add(m)
        db.session.commit()
        gpid, upid, lpid, mpid = g.public_id, u.public_id, lsn.public_id, m.public_id
        uf_id = uf.id
    login(client, "teacher@example.com")
    edit_url = f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/materials/{mpid}/edit"
    snap = _snapshot(client, edit_url)
    client.post(edit_url, data={"title": "New Title", "search_keywords": "handout, pdf",
                                "edit_snapshot": snap}, follow_redirects=True)
    with app.app_context():
        m = Material.query.filter_by(public_id=mpid).first()
        assert m.title == "New Title"
        assert m.search_keywords == "handout, pdf"
        assert m.uploaded_file_id == uf_id  # bytes/link unchanged
        assert m.kind == MaterialKind.FILE.value


def test_material_keyword_edit_stale_after_concurrent_change(app, client):
    with app.app_context():
        _, g = _setup()
        u = _unit(g)
        lsn = _lesson(u)
        m = Material(lesson_id=lsn.id, title="M", kind=MaterialKind.EXTERNAL_LINK.value,
                     external_url="https://example.com/x", status=ACTIVE, display_order=1,
                     creation_nonce="n1", search_keywords="orig")
        db.session.add(m)
        db.session.commit()
        gpid, upid, lpid, mpid = g.public_id, u.public_id, lsn.public_id, m.public_id
    login(client, "teacher@example.com")
    edit_url = f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/materials/{mpid}/edit"
    snap = _snapshot(client, edit_url)
    with app.app_context():
        row = Material.query.filter_by(public_id=mpid).first()
        row.search_keywords = "co-teacher change"
        db.session.commit()
    resp = client.post(edit_url, data={"title": "M", "external_url": "https://example.com/x",
                                       "search_keywords": "mine", "edit_snapshot": snap},
                       follow_redirects=True)
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Material.query.filter_by(public_id=mpid).first().search_keywords == "co-teacher change"
