"""Teacher Material management (M12): nested authorization, the three
kinds, lifecycle (archive/reactivate), ordering (active-only, skip
archived), the signed stale-edit snapshot, the signed create token +
creation-nonce replay protection, the canonical lock order, post-lock
rechecks, file upload + audit, authorized serving headers/disposition,
and non-disclosure 404s.

Rich-text materials cover the shared authz/lifecycle/ordering/stale-form
paths (no filesystem needed); a dedicated block covers file upload,
storage cleanup, serving, and the access log.
"""

import io
import re
from datetime import date, datetime, timezone

import pytest

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
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
               group_name="Group A"):
    term = AcademicTerm(name=f"Term {group_name}", start_date=date(2026, 1, 1),
                        end_date=date(2026, 12, 31), status=term_status)
    db.session.add(term)
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add(level)
    db.session.commit()
    course = Course(title=f"English {group_name}", level_id=level.id, display_order=0, status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=group_name, capacity=20,
                  status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _unit(group, title="Unit 1", status=AcademicStatus.ACTIVE.value):
    u = Unit(group_id=group.id, title=title, display_order=0, status=status)
    db.session.add(u)
    db.session.commit()
    return u


def _lesson(unit, title="Lesson 1", status=LessonStatus.DRAFT.value):
    published_at = datetime.now(timezone.utc) if status == LessonStatus.PUBLISHED.value else None
    lsn = Lesson(unit_id=unit.id, title=title, display_order=0, status=status, published_at=published_at)
    db.session.add(lsn)
    db.session.commit()
    return lsn


def _material(lesson, title="M", kind=MaterialKind.RICH_TEXT.value, display_order=1,
              status=AcademicStatus.ACTIVE.value, nonce=None, uploaded_file_id=None):
    nonce = nonce or f"seed-{title}-{display_order}"
    kwargs = dict(lesson_id=lesson.id, title=title, kind=kind, display_order=display_order,
                  status=status, creation_nonce=nonce)
    if kind == MaterialKind.RICH_TEXT.value:
        kwargs["content_html"] = "<p>seed</p>"
    elif kind == MaterialKind.EXTERNAL_LINK.value:
        kwargs["external_url"] = "https://example.com/seed"
    elif kind == MaterialKind.FILE.value:
        kwargs["uploaded_file_id"] = uploaded_file_id
    m = Material(**kwargs)
    db.session.add(m)
    db.session.commit()
    return m


def _setup(email="teacher@example.com", lesson_status=LessonStatus.DRAFT.value, **hk):
    teacher = _user(email, UserRole.TEACHER.value)
    group = _hierarchy(**hk)
    _assign(group, teacher)
    unit = _unit(group)
    lesson = _lesson(unit, status=lesson_status)
    return teacher, group, unit, lesson


def _serve_get(client, url, **kw):
    resp = client.get(url, buffered=True, **kw)
    resp.get_data()
    resp.close()
    return resp


def _base_url(g, u, l):
    return f"/teacher/groups/{g}/units/{u}/lessons/{l}/materials"


def _create_token(client, g, u, l, kind_slug):
    html = client.get(f"{_base_url(g, u, l)}/new/{kind_slug}").get_data(as_text=True)
    m = re.search(r'name="create_token" value="([^"]*)"', html)
    return m.group(1) if m else ""


def _create_rich_text(client, g, u, l, title="Notes", content="<p>Hello</p>", token=None, follow=True):
    token = token if token is not None else _create_token(client, g, u, l, "rich-text")
    return client.post(
        f"{_base_url(g, u, l)}/new/rich-text",
        data={"title": title, "content_html": content, "create_token": token},
        follow_redirects=follow,
    )


def _edit_snapshot(client, g, u, l, mp):
    html = client.get(f"{_base_url(g, u, l)}/{mp}/edit").get_data(as_text=True)
    m = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return m.group(1) if m else ""


# ===========================================================================
# Authorization / routing
# ===========================================================================


def test_anonymous_redirected_to_login(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    resp = client.get(_base_url(gp, up, lp))
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_non_teacher_roles_get_403(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
        make_user("s@example.com", UserRole.STUDENT.value)
        make_user("a@example.com", UserRole.ADMINISTRATOR.value)
        make_user("r@example.com", UserRole.RESEARCHER.value)
    for email in ("s@example.com", "a@example.com", "r@example.com"):
        login(client, email)
        assert client.get(_base_url(gp, up, lp)).status_code == 403


def test_active_assigned_teacher_can_view(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    assert client.get(_base_url(gp, up, lp)).status_code == 200


def test_unassigned_teacher_404_no_disclosure(app, client):
    with app.app_context():
        _, g, u, l = _setup("assigned@example.com")
        _user("outsider@example.com", UserRole.TEACHER.value)
        m = _material(l, title="Secret Material")
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "outsider@example.com")
    assert client.get(_base_url(gp, up, lp)).status_code == 404
    r = client.get(f"{_base_url(gp, up, lp)}/{mp}/edit")
    assert r.status_code == 404 and b"Secret Material" not in r.data


def test_removed_assignment_404(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
        unit = _unit(group)
        lesson = _lesson(unit)
        gp, up, lp = group.public_id, unit.public_id, lesson.public_id
    login(client, "teacher@example.com")
    assert client.get(_base_url(gp, up, lp)).status_code == 404


def test_co_teacher_is_equal(app, client):
    with app.app_context():
        _, g, u, l = _setup("t1@example.com")
        t2 = _user("t2@example.com", UserRole.TEACHER.value)
        _assign(g, t2)
        m = _material(l, title="By t1")
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "t2@example.com")
    snap = _edit_snapshot(client, gp, up, lp, mp)
    r = client.post(f"{_base_url(gp, up, lp)}/{mp}/edit",
                    data={"title": "By t2", "content_html": "<p>seed</p>", "edit_snapshot": snap},
                    follow_redirects=True)
    assert b"updated" in r.data.lower()
    with app.app_context():
        assert Material.query.first().title == "By t2"


def test_cross_lesson_material_public_id_404(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group)
        l1 = _lesson(unit, title="L1")
        l2 = _lesson(unit, title="L2")
        m = _material(l1, title="M1")
        gp, up, l2p, mp = group.public_id, unit.public_id, l2.public_id, m.public_id
    login(client, "teacher@example.com")
    assert client.get(f"{_base_url(gp, up, l2p)}/{mp}/edit").status_code == 404
    assert client.post(f"{_base_url(gp, up, l2p)}/{mp}/toggle-status").status_code == 404


def test_get_available_when_group_archived(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(group_status=AcademicStatus.ARCHIVED.value)
        _assign(group, teacher)
        unit = _unit(group)
        lesson = _lesson(unit)
        _material(lesson, title="Historical Material")
        gp, up, lp = group.public_id, unit.public_id, lesson.public_id
    login(client, "teacher@example.com")
    r = client.get(_base_url(gp, up, lp))
    assert r.status_code == 200 and b"Historical Material" in r.data
    assert b"not operational" in r.data.lower()


# ===========================================================================
# Create -- rich text / external link, defaults, blocking
# ===========================================================================


def test_create_rich_text_defaults_active_order_1(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    r = _create_rich_text(client, gp, up, lp, title="Intro")
    assert b"created" in r.data.lower()
    with app.app_context():
        m = Material.query.filter_by(title="Intro").first()
        assert m.status == "active" and m.display_order == 1 and m.kind == "rich_text"


def test_create_external_link_validates_url(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    token = _create_token(client, gp, up, lp, "external-link")
    r = client.post(f"{_base_url(gp, up, lp)}/new/external-link",
                    data={"title": "Bad", "external_url": "http://insecure.example", "create_token": token},
                    follow_redirects=True)
    assert b"https" in r.data.lower()
    with app.app_context():
        assert Material.query.count() == 0


def test_create_rich_text_rejects_script_only_content(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    r = _create_rich_text(client, gp, up, lp, title="X", content="<script>bad()</script>")
    assert b"empty" in r.data.lower()
    with app.app_context():
        assert Material.query.count() == 0


def test_create_appends_after_highest_order(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        _material(l, title="A", display_order=1)
        _material(l, title="B", display_order=7)
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    _create_rich_text(client, gp, up, lp, title="C")
    with app.app_context():
        assert Material.query.filter_by(title="C").first().display_order == 8


def test_create_duplicate_title_rejected(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        _material(l, title="Dup", status=AcademicStatus.ARCHIVED.value)
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    r = _create_rich_text(client, gp, up, lp, title="Dup")
    assert b"already exists" in r.data.lower()
    with app.app_context():
        assert Material.query.filter_by(title="Dup").count() == 1


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_create_blocked_when_hierarchy_not_operational(app, client, archived):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        _assign(group, teacher)
        unit = _unit(group)
        lesson = _lesson(unit)
        gp, up, lp = group.public_id, unit.public_id, lesson.public_id
    login(client, "teacher@example.com")
    r = _create_rich_text(client, gp, up, lp, title="Nope")
    assert r.status_code == 200
    with app.app_context():
        assert Material.query.count() == 0


def test_create_blocked_when_unit_archived(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group, status=AcademicStatus.ARCHIVED.value)
        lesson = _lesson(unit)
        gp, up, lp = group.public_id, unit.public_id, lesson.public_id
    login(client, "teacher@example.com")
    r = _create_rich_text(client, gp, up, lp, title="Nope")
    assert r.status_code == 200
    with app.app_context():
        assert Material.query.count() == 0


def test_create_on_published_lesson_warns(app, client):
    with app.app_context():
        _, g, u, l = _setup(lesson_status=LessonStatus.PUBLISHED.value)
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    r = _create_rich_text(client, gp, up, lp, title="Live")
    assert b"immediately visible" in r.data.lower()


# ===========================================================================
# Creation nonce / signed token replay protection
# ===========================================================================


def test_missing_create_token_rejected(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    r = client.post(f"{_base_url(gp, up, lp)}/new/rich-text",
                    data={"title": "X", "content_html": "<p>x</p>"}, follow_redirects=True)
    assert b"could not be verified" in r.data.lower()
    with app.app_context():
        assert Material.query.count() == 0


def test_tampered_create_token_rejected(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    token = _create_token(client, gp, up, lp, "rich-text")
    r = _create_rich_text(client, gp, up, lp, title="X", token=token + "x")
    assert b"could not be verified" in r.data.lower()
    with app.app_context():
        assert Material.query.count() == 0


def test_ordinary_replay_creates_one_material(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    token = _create_token(client, gp, up, lp, "rich-text")
    first = _create_rich_text(client, gp, up, lp, title="Once", token=token)
    assert b"created" in first.data.lower()
    second = _create_rich_text(client, gp, up, lp, title="Once", token=token)
    assert b"already created" in second.data.lower()
    with app.app_context():
        assert Material.query.filter_by(title="Once").count() == 1


def test_create_token_bound_to_lesson(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group)
        l1 = _lesson(unit, title="L1")
        l2 = _lesson(unit, title="L2")
        gp, up, l1p, l2p = group.public_id, unit.public_id, l1.public_id, l2.public_id
    login(client, "teacher@example.com")
    token_for_l1 = _create_token(client, gp, up, l1p, "rich-text")
    r = _create_rich_text(client, gp, up, l2p, title="X", token=token_for_l1)
    assert b"could not be verified" in r.data.lower()


# ===========================================================================
# Edit + stale-form snapshot
# ===========================================================================


def test_edit_rich_text_updates_title_and_content(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        m = _material(l, title="Old")
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "teacher@example.com")
    snap = _edit_snapshot(client, gp, up, lp, mp)
    r = client.post(f"{_base_url(gp, up, lp)}/{mp}/edit",
                    data={"title": "New", "content_html": "<p>updated</p>", "edit_snapshot": snap},
                    follow_redirects=True)
    assert b"updated" in r.data.lower()
    with app.app_context():
        row = Material.query.first()
        assert row.title == "New" and row.content_html == "<p>updated</p>"
        assert row.display_order == 1 and row.status == "active"


def test_edit_missing_snapshot_rejected(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        m = _material(l, title="Keep")
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "teacher@example.com")
    r = client.post(f"{_base_url(gp, up, lp)}/{mp}/edit",
                    data={"title": "Hacked", "content_html": "<p>x</p>"}, follow_redirects=True)
    assert b"changed since this form was opened" in r.data.lower()
    with app.app_context():
        assert Material.query.first().title == "Keep"


def test_edit_stale_after_concurrent_change_rejected(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        m = _material(l, title="Original")
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "teacher@example.com")
    snap = _edit_snapshot(client, gp, up, lp, mp)
    with app.app_context():
        row = Material.query.first()
        row.title = "Changed by co-teacher"
        db.session.commit()
    r = client.post(f"{_base_url(gp, up, lp)}/{mp}/edit",
                    data={"title": "Mine", "content_html": "<p>x</p>", "edit_snapshot": snap},
                    follow_redirects=True)
    assert b"changed since this form was opened" in r.data.lower()
    with app.app_context():
        assert Material.query.first().title == "Changed by co-teacher"


def test_edit_snapshot_excludes_status_and_order(app, client):
    """Archiving (status) or moving (order) a material must NOT stale an
    open rich-text edit form -- the snapshot only covers title+content."""
    with app.app_context():
        _, g, u, l = _setup()
        a = _material(l, title="A", display_order=1)
        b = _material(l, title="B", display_order=2)
        gp, up, lp, ap = g.public_id, u.public_id, l.public_id, a.public_id
    login(client, "teacher@example.com")
    snap = _edit_snapshot(client, gp, up, lp, ap)
    client.post(f"{_base_url(gp, up, lp)}/{ap}/move-down", follow_redirects=True)  # order changed
    r = client.post(f"{_base_url(gp, up, lp)}/{ap}/edit",
                    data={"title": "A", "content_html": "<p>edited</p>", "edit_snapshot": snap},
                    follow_redirects=True)
    assert b"updated" in r.data.lower()
    with app.app_context():
        assert Material.query.filter_by(title="A").first().content_html == "<p>edited</p>"


# ===========================================================================
# Lifecycle: archive / reactivate (file row preserved)
# ===========================================================================


def test_archive_then_reactivate_appends_to_end(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        a = _material(l, title="A", display_order=1)
        _material(l, title="B", display_order=2)
        gp, up, lp, ap = g.public_id, u.public_id, l.public_id, a.public_id
    login(client, "teacher@example.com")
    client.post(f"{_base_url(gp, up, lp)}/{ap}/toggle-status", follow_redirects=True)
    with app.app_context():
        assert Material.query.filter_by(title="A").first().status == "archived"
    client.post(f"{_base_url(gp, up, lp)}/{ap}/toggle-status", follow_redirects=True)
    with app.app_context():
        row = Material.query.filter_by(title="A").first()
        assert row.status == "active" and row.display_order == 3


def test_archive_always_allowed_under_archived_hierarchy(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(course_status=AcademicStatus.ARCHIVED.value)
        _assign(group, teacher)
        unit = _unit(group)
        lesson = _lesson(unit)
        m = _material(lesson, title="Cleanup")
        gp, up, lp, mp = group.public_id, unit.public_id, lesson.public_id, m.public_id
    login(client, "teacher@example.com")
    r = client.post(f"{_base_url(gp, up, lp)}/{mp}/toggle-status", follow_redirects=True)
    assert b"archived" in r.data.lower()
    with app.app_context():
        assert Material.query.first().status == "archived"


def test_reactivate_blocked_under_archived_hierarchy(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(term_status=AcademicStatus.ARCHIVED.value)
        _assign(group, teacher)
        unit = _unit(group)
        lesson = _lesson(unit)
        m = _material(lesson, title="M", status=AcademicStatus.ARCHIVED.value)
        gp, up, lp, mp = group.public_id, unit.public_id, lesson.public_id, m.public_id
    login(client, "teacher@example.com")
    r = client.post(f"{_base_url(gp, up, lp)}/{mp}/toggle-status", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert Material.query.first().status == "archived"


# ===========================================================================
# Ordering -- active only, skip archived, boundaries
# ===========================================================================


def _three(app):
    with app.app_context():
        _, g, u, l = _setup()
        a = _material(l, title="A", display_order=1)
        b = _material(l, title="B", display_order=2)
        c = _material(l, title="C", display_order=3)
        return g.public_id, u.public_id, l.public_id, a.public_id, b.public_id, c.public_id


def _order(app):
    with app.app_context():
        return [m.title for m in Material.query.order_by(Material.display_order, Material.id).all()]


def test_move_down_swaps(app, client):
    gp, up, lp, ap, bp, cp = _three(app)
    login(client, "teacher@example.com")
    client.post(f"{_base_url(gp, up, lp)}/{ap}/move-down", follow_redirects=True)
    assert _order(app) == ["B", "A", "C"]


def test_move_up_at_boundary_no_op(app, client):
    gp, up, lp, ap, bp, cp = _three(app)
    login(client, "teacher@example.com")
    r = client.post(f"{_base_url(gp, up, lp)}/{ap}/move-up", follow_redirects=True)
    assert b"already first" in r.data.lower()
    assert _order(app) == ["A", "B", "C"]


def test_move_skips_archived(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        a = _material(l, title="A", display_order=1)
        _material(l, title="ARCH", display_order=2, status=AcademicStatus.ARCHIVED.value)
        c = _material(l, title="C", display_order=3)
        gp, up, lp, ap = g.public_id, u.public_id, l.public_id, a.public_id
    login(client, "teacher@example.com")
    client.post(f"{_base_url(gp, up, lp)}/{ap}/move-down", follow_redirects=True)
    with app.app_context():
        active = [m.title for m in Material.query.filter_by(status="active").order_by(Material.display_order, Material.id).all()]
        assert active == ["C", "A"]
        assert Material.query.filter_by(title="ARCH").first().display_order == 2


def test_reorder_archived_material_rejected(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        _material(l, title="A", display_order=1)
        arch = _material(l, title="ARCH", display_order=2, status=AcademicStatus.ARCHIVED.value)
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, arch.public_id
    login(client, "teacher@example.com")
    r = client.post(f"{_base_url(gp, up, lp)}/{mp}/move-up", follow_redirects=True)
    assert b"only active materials can be reordered" in r.data.lower()


# ===========================================================================
# CSRF / POST-only / no leaked ids
# ===========================================================================


def test_mutations_are_post_only(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        m = _material(l, title="M")
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "teacher@example.com")
    assert client.get(f"{_base_url(gp, up, lp)}/{mp}/toggle-status").status_code == 405
    assert client.get(f"{_base_url(gp, up, lp)}/{mp}/move-up").status_code == 405


def test_csrf_enforced_on_material_mutations(tmp_path):
    from app import create_app

    app = create_app("testing", MATERIAL_STORAGE_ROOT=str(tmp_path / "m"))
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        db.create_all()
        try:
            _, g, u, l = _setup()
            m = _material(l, title="M")
            gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
            page = client.get("/auth/login")
            token = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', page.get_data(as_text=True)).group(1)
            client.post("/auth/login", data={"email": "teacher@example.com", "password": PW, "csrf_token": token})
            assert client.post(f"{_base_url(gp, up, lp)}/{mp}/toggle-status").status_code == 400
            assert client.post(f"{_base_url(gp, up, lp)}/new/rich-text", data={"title": "X"}).status_code == 400
            assert Material.query.count() == 1
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_no_internal_ids_in_materials_html(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        m = _material(l, title="M")
        gp, gid, up, uid, lp, lid, mid = (
            g.public_id, g.id, u.public_id, u.id, l.public_id, l.id, m.id
        )
        mp = m.public_id
    login(client, "teacher@example.com")
    for path in (_base_url(gp, up, lp), f"{_base_url(gp, up, lp)}/new/rich-text",
                 f"{_base_url(gp, up, lp)}/{mp}/edit"):
        html = client.get(path).get_data(as_text=True)
        assert f'value="{mid}"' not in html
        assert f'value="{lid}"' not in html
        assert f"/lessons/{lid}/" not in html


# ===========================================================================
# Structural: canonical lock order + single reset
# ===========================================================================


def _capture_locks(fn):
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    events = []
    original_rollback = db.session.rollback
    original_wfu = Query.with_for_update

    def rollback_spy(*a, **k):
        events.append("reset")
        return original_rollback(*a, **k)

    def wfu_spy(self, *a, **k):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_wfu(self, *a, **k)

    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", wfu_spy
    ):
        fn()
    return events


def test_create_locks_canonical_order(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    events = _capture_locks(lambda: _create_rich_text(client, gp, up, lp, title="Locked"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit", "lock:Lesson",
    ]


def test_edit_locks_canonical_order_including_material(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        m = _material(l, title="M")
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "teacher@example.com")
    snap = _edit_snapshot(client, gp, up, lp, mp)
    events = _capture_locks(lambda: client.post(
        f"{_base_url(gp, up, lp)}/{mp}/edit",
        data={"title": "M2", "content_html": "<p>y</p>", "edit_snapshot": snap},
    ))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit", "lock:Lesson", "lock:Material",
    ]


def test_reorder_locks_materials_ascending_id(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        a = _material(l, title="A", display_order=1)
        b = _material(l, title="B", display_order=2)
        gp, up, lp, ap = g.public_id, u.public_id, l.public_id, a.public_id
        a_id, b_id = a.id, b.id
    login(client, "teacher@example.com")
    events = _capture_locks(lambda: client.post(f"{_base_url(gp, up, lp)}/{ap}/move-down"))
    locks = [e for e in events if e.startswith("lock:")]
    assert locks[:8] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit", "lock:Lesson",
    ]
    assert locks[8:] == ["lock:Material", "lock:Material"]
    assert a_id < b_id


def test_m11_publish_unpublish_still_works_alongside_materials(app, client):
    with app.app_context():
        _, g, u, l = _setup(lesson_status=LessonStatus.DRAFT.value)
        _material(l, title="M")
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")
    r = client.post(
        f"/teacher/groups/{gp}/units/{up}/lessons/{lp}/toggle-publication", follow_redirects=True
    )
    assert b"published" in r.data.lower()
    with app.app_context():
        assert Lesson.query.first().status == "published"


# ===========================================================================
# File materials -- upload, storage, serving, audit
# ===========================================================================


def _upload_file(client, g, u, l, name="doc.pdf", data=None, title="A File", token=None, follow=True):
    data = data if data is not None else ff.minimal_pdf()
    token = token if token is not None else _create_token(client, g, u, l, "file")
    return client.post(
        f"{_base_url(g, u, l)}/new/file",
        data={
            "title": title,
            "create_token": token,
            "file": (io.BytesIO(data), name),
        },
        content_type="multipart/form-data",
        follow_redirects=follow,
    )


def test_upload_creates_material_file_and_upload_log(material_app, material_client):
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    r = _upload_file(material_client, gp, up, lp, name="lecture.pdf", data=ff.minimal_pdf())
    assert b"created" in r.data.lower()
    with material_app.app_context():
        m = Material.query.first()
        assert m.kind == "file"
        uf = m.uploaded_file
        assert uf.extension == "pdf" and uf.category == "document"
        assert uf.original_filename == "lecture.pdf"
        assert "lecture" not in uf.storage_key
        logs = FileAccessLog.query.filter_by(uploaded_file_id=uf.id).all()
        assert [x.action for x in logs] == ["upload"]
        mc = material_app.extensions["material_config"]
        assert (mc.storage_root / uf.storage_key).is_file()


def test_upload_rejected_bad_signature_leaves_nothing(material_app, material_client):
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
        mc = material_app.extensions["material_config"]
        root = mc.storage_root
    login(material_client, "teacher@example.com")
    r = _upload_file(material_client, gp, up, lp, name="x.pdf", data=ff.executable_bytes())
    assert r.status_code == 200
    with material_app.app_context():
        assert Material.query.count() == 0 and UploadedFile.query.count() == 0
    assert not root.exists() or list(root.iterdir()) == []


def test_upload_replay_creates_one_record_and_one_file(material_app, material_client):
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    token = _create_token(material_client, gp, up, lp, "file")
    first = _upload_file(material_client, gp, up, lp, title="Once", token=token)
    assert b"created" in first.data.lower()
    second = _upload_file(material_client, gp, up, lp, title="Once", token=token)
    assert b"already created" in second.data.lower()
    with material_app.app_context():
        assert Material.query.filter_by(title="Once").count() == 1
        assert UploadedFile.query.count() == 1
        mc = material_app.extensions["material_config"]
        assert len([p for p in mc.storage_root.iterdir() if p.suffix != ".part"]) == 1


def test_teacher_download_is_attachment_with_nosniff_and_audit(material_app, material_client):
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    _upload_file(material_client, gp, up, lp, name="notes.pdf")
    with material_app.app_context():
        mp = Material.query.first().public_id
        uf_id = Material.query.first().uploaded_file_id
    r = _serve_get(material_client, f"{_base_url(gp, up, lp)}/{mp}/download")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in r.headers["Cache-Control"]
    assert r.headers["Content-Type"].startswith("application/pdf")
    with material_app.app_context():
        actions = [x.action for x in FileAccessLog.query.filter_by(uploaded_file_id=uf_id).all()]
        assert actions == ["upload", "download"]


def test_image_open_is_inline_and_logs_inline(material_app, material_client):
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    _upload_file(material_client, gp, up, lp, name="pic.png", data=ff.minimal_png())
    with material_app.app_context():
        mp = Material.query.first().public_id
        uf_id = Material.query.first().uploaded_file_id
    r = _serve_get(material_client, f"{_base_url(gp, up, lp)}/{mp}/open")
    assert r.status_code == 200
    assert "attachment" not in r.headers.get("Content-Disposition", "")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    with material_app.app_context():
        actions = [x.action for x in FileAccessLog.query.filter_by(uploaded_file_id=uf_id).all()]
        assert actions == ["upload", "inline"]


def test_pdf_open_still_downloads_as_attachment(material_app, material_client):
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    _upload_file(material_client, gp, up, lp, name="d.pdf")
    with material_app.app_context():
        mp = Material.query.first().public_id
    r = _serve_get(material_client, f"{_base_url(gp, up, lp)}/{mp}/open")
    assert "attachment" in r.headers["Content-Disposition"]


def test_range_request_supported_and_logged(material_app, material_client):
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    _upload_file(material_client, gp, up, lp, name="a.png", data=ff.minimal_png())
    with material_app.app_context():
        mp = Material.query.first().public_id
        uf_id = Material.query.first().uploaded_file_id
    r = _serve_get(material_client, f"{_base_url(gp, up, lp)}/{mp}/open", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.headers["Accept-Ranges"] == "bytes"
    with material_app.app_context():
        actions = [x.action for x in FileAccessLog.query.filter_by(uploaded_file_id=uf_id).all()]
        assert actions == ["upload", "inline"]  # one row for the Range request


def test_missing_physical_file_fails_safely(material_app, material_client):
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    _upload_file(material_client, gp, up, lp, name="gone.pdf")
    with material_app.app_context():
        m = Material.query.first()
        mp = m.public_id
        mc = material_app.extensions["material_config"]
        (mc.storage_root / m.uploaded_file.storage_key).unlink()
        uf_id = m.uploaded_file_id
    r = _serve_get(material_client, f"{_base_url(gp, up, lp)}/{mp}/download")
    assert r.status_code == 404
    assert b"/storage/" not in r.data and b"materials" not in r.data.lower()
    with material_app.app_context():
        actions = [x.action for x in FileAccessLog.query.filter_by(uploaded_file_id=uf_id).all()]
        assert actions == ["upload"]  # no success log for the failed serve


def test_open_download_on_rich_text_material_404(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        m = _material(l, title="RT", kind=MaterialKind.RICH_TEXT.value)
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "teacher@example.com")
    assert client.get(f"{_base_url(gp, up, lp)}/{mp}/open").status_code == 404
    assert client.get(f"{_base_url(gp, up, lp)}/{mp}/download").status_code == 404


def test_413_on_oversize_upload(tmp_path):
    from app import create_app

    app = create_app(
        "testing",
        MATERIAL_STORAGE_ROOT=str(tmp_path / "m"),
        MATERIAL_ALLOWED_EXTENSIONS="png",
        MATERIAL_MAX_IMAGE_BYTES="256",
        MATERIAL_MAX_DOCUMENT_BYTES="256",
        MATERIAL_MAX_AUDIO_BYTES="256",
        MATERIAL_MAX_VIDEO_BYTES="256",
    )
    client = app.test_client()
    with app.app_context():
        db.create_all()
        try:
            _, g, u, l = _setup()
            gp, up, lp = g.public_id, u.public_id, l.public_id
            login(client, "teacher@example.com")
            token = _create_token(client, gp, up, lp, "file")
            # Over MAX_CONTENT_LENGTH (256 + 64 KiB) but small enough that the
            # test client does not spool the request body to a temp file.
            big = ff.minimal_png() + b"\x00" * (200 * 1024)
            r = client.post(
                f"{_base_url(gp, up, lp)}/new/file",
                data={"title": "Big", "create_token": token, "file": (io.BytesIO(big), "b.png")},
                content_type="multipart/form-data",
            )
            assert r.status_code == 413
            assert b"File Too Large" in r.data
            assert Material.query.count() == 0
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


# ===========================================================================
# Review corrections (M12 focused review-correction pass)
# ===========================================================================


SECRET_PATH = "C:/Users/abdul/secret-vault/xyzzy-4242.bin"


def test_unexpected_upload_error_does_not_disclose_details(material_app, material_client, monkeypatch):
    """A non-FileValidationError from store_validated_upload (containing a
    fake secret path) must never reach the response body, a flash, or the
    rendered HTML -- it surfaces as a generic 500."""
    import app.blueprints.teacher.materials as materials_mod

    # As in production (DEBUG=False): an unhandled exception becomes a
    # generic 500 rather than propagating to the caller.
    monkeypatch.setitem(material_app.config, "PROPAGATE_EXCEPTIONS", False)
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")

    def boom(*a, **k):
        raise RuntimeError(f"disk exploded at {SECRET_PATH} while writing")

    monkeypatch.setattr(materials_mod, "store_validated_upload", boom)
    r = _upload_file(material_client, gp, up, lp, follow=False)
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert SECRET_PATH not in body
    assert "xyzzy-4242" not in body
    assert "disk exploded" not in body
    follow = material_client.get(_base_url(gp, up, lp))
    follow_body = follow.get_data(as_text=True)
    assert "xyzzy-4242" not in follow_body and "disk exploded" not in follow_body


def test_validation_error_message_is_shown(material_app, material_client):
    """A user-caused FileValidationError keeps its (safe) message."""
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    r = _upload_file(material_client, gp, up, lp, name="bad.pdf", data=ff.executable_bytes())
    assert r.status_code == 200
    assert b"does not look like a valid PDF" in r.data


def test_lock_failure_after_storage_cleans_file_and_leaves_no_rows(material_app, material_client, monkeypatch):
    """A non-IntegrityError failure after the file is on disk: the new
    file is removed, no losing Material/UploadedFile/upload log is
    committed, and no .part remains -- while an earlier successful upload
    is untouched."""
    import app.blueprints.teacher.materials as materials_mod

    monkeypatch.setitem(material_app.config, "PROPAGATE_EXCEPTIONS", False)
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    _upload_file(material_client, gp, up, lp, title="Good One", name="good.pdf")
    with material_app.app_context():
        mc = material_app.extensions["material_config"]
        good_key = UploadedFile.query.first().storage_key
        before = {p.name for p in mc.storage_root.iterdir()}

    def boom(*a, **k):
        raise RuntimeError("simulated lock/db failure")

    monkeypatch.setattr(materials_mod, "_lock_and_authorize_for_create", boom)
    r = _upload_file(material_client, gp, up, lp, title="Loser", name="loser.pdf", follow=False)
    assert r.status_code == 500

    with material_app.app_context():
        mc = material_app.extensions["material_config"]
        assert Material.query.filter_by(title="Loser").count() == 0
        assert UploadedFile.query.count() == 1
        assert Material.query.count() == 1
        assert FileAccessLog.query.count() == 1
        after = {p.name for p in mc.storage_root.iterdir()}
        assert after == before
        assert not any(n.endswith(".part") for n in after)
        assert (mc.storage_root / good_key).is_file()


def test_orphan_cleanup_failure_is_logged_and_does_not_leak(material_app, material_client, monkeypatch, caplog):
    """When the just-stored file cannot be deleted after a handled
    failure: the response stays a generic 500 (no path / OSError text),
    the DB has no losing rows, and BOTH the file_storage module logger
    and the request-context app logger record the undeletable key."""
    import logging
    from pathlib import Path

    import app.blueprints.teacher.materials as materials_mod

    monkeypatch.setitem(material_app.config, "PROPAGATE_EXCEPTIONS", False)
    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")

    def boom_lock(*a, **k):
        raise RuntimeError("simulated lock/db failure")

    def boom_unlink(self, *a, **k):
        raise OSError(13, "Permission denied (simulated)")

    monkeypatch.setattr(materials_mod, "_lock_and_authorize_for_create", boom_lock)
    monkeypatch.setattr(Path, "unlink", boom_unlink)

    with caplog.at_level(logging.ERROR):
        r = _upload_file(material_client, gp, up, lp, title="Orphan", name="o.pdf", follow=False)
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert "Permission denied" not in body and "simulated" not in body
    assert "/storage/" not in body and "materials" not in body.lower()

    assert "Could not delete stored file" in caplog.text  # file_storage module logger
    assert "could not be deleted" in caplog.text  # _cleanup_orphan_upload request-context line

    with material_app.app_context():
        assert Material.query.filter_by(title="Orphan").count() == 0
        assert UploadedFile.query.count() == 0
        assert FileAccessLog.query.count() == 0


def test_serving_fails_closed_when_audit_log_cannot_persist(material_app, material_client):
    """If the access-log commit fails, no file bytes are served."""
    from unittest.mock import patch

    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(material_client, "teacher@example.com")
    _upload_file(material_client, gp, up, lp, name="n.pdf")
    with material_app.app_context():
        mp = Material.query.first().public_id
        uf_id = Material.query.first().uploaded_file_id
    pdf_bytes = ff.minimal_pdf()

    with patch.object(db.session, "commit", side_effect=RuntimeError("audit db down")):
        r = _serve_get(material_client, f"{_base_url(gp, up, lp)}/{mp}/download")
    assert r.status_code == 500
    assert pdf_bytes not in r.data
    assert not r.headers.get("Content-Type", "").startswith("application/pdf")
    with material_app.app_context():
        actions = [x.action for x in FileAccessLog.query.filter_by(uploaded_file_id=uf_id).all()]
        assert actions == ["upload"]


def test_denied_teacher_file_request_writes_no_success_log(material_app, material_client):
    # One authenticated identity for the whole test (an *unassigned*
    # teacher): a switch mid-test is unreliable with the open-app-context
    # fixture -- see test_teacher_dashboard.py's note. The `file` Material
    # and its physical file are seeded directly.
    with material_app.app_context():
        _, g, u, l = _setup("assigned@example.com")
        outsider = _user("outsider@example.com", UserRole.TEACHER.value)
        mc = material_app.extensions["material_config"]
        mc.storage_root.mkdir(parents=True, exist_ok=True)
        (mc.storage_root / "seeded-denied.pdf").write_bytes(ff.minimal_pdf())
        uf = UploadedFile(
            storage_key="seeded-denied.pdf", original_filename="n.pdf", extension="pdf",
            category="document", content_type="application/pdf", byte_size=10, sha256="0" * 64,
            uploaded_by_id=outsider.id,
        )
        db.session.add(uf)
        db.session.commit()
        m = Material(
            lesson_id=l.id, title="Seeded", kind="file", uploaded_file_id=uf.id,
            status="active", display_order=1, creation_nonce="seed-denied",
        )
        db.session.add(m)
        db.session.commit()
        gp, up, lp, mp, uf_id = g.public_id, u.public_id, l.public_id, m.public_id, uf.id

    login(material_client, "outsider@example.com")  # unassigned to this group
    r = material_client.get(f"{_base_url(gp, up, lp)}/{mp}/download")
    assert r.status_code == 404
    with material_app.app_context():
        assert FileAccessLog.query.filter_by(uploaded_file_id=uf_id).count() == 0


def test_concurrent_replay_loser_resolves_to_winner_and_cleans_own_file(material_app, material_client, monkeypatch):
    """Between store_validated_upload and the lock, a competitor commits
    the same nonce (with its own file). The loser must: redirect to the
    winner, delete its own physical file, and leave exactly one Material,
    UploadedFile, upload log, and final file."""
    import app.blueprints.teacher.materials as materials_mod
    from werkzeug.datastructures import FileStorage

    with material_app.app_context():
        _, g, u, l = _setup()
        gp, up, lp, lid = g.public_id, u.public_id, l.public_id, l.id
        teacher_id = User.query.filter_by(email="teacher@example.com").first().id
    login(material_client, "teacher@example.com")
    token = _create_token(material_client, gp, up, lp, "file")

    original = materials_mod._lock_and_authorize_for_create
    state = {"done": False}

    def competitor_then_original(gpid, upid, lpid, grp, unt, lsn):
        if not state["done"]:
            state["done"] = True
            mc = materials_mod.current_material_config()
            stored = materials_mod.store_validated_upload(
                mc, FileStorage(stream=io.BytesIO(ff.minimal_png()), filename="w.png"), "w.png"
            )
            nonce = materials_mod._load_create_nonce(token, teacher_id, lid, "file")
            uf = UploadedFile(
                storage_key=stored.storage_key, original_filename=stored.original_filename,
                extension=stored.extension, category=stored.category,
                content_type=stored.content_type, byte_size=stored.byte_size,
                sha256=stored.sha256, uploaded_by_id=teacher_id,
            )
            mat = Material(
                lesson_id=lid, title="Winner", kind="file", uploaded_file=uf,
                status="active", display_order=1, creation_nonce=nonce,
            )
            log = FileAccessLog(uploaded_file=uf, actor_id=teacher_id, action="upload")
            db.session.add_all([uf, mat, log])
            db.session.commit()
        return original(gpid, upid, lpid, grp, unt, lsn)

    monkeypatch.setattr(materials_mod, "_lock_and_authorize_for_create", competitor_then_original)
    r = _upload_file(material_client, gp, up, lp, title="Loser", token=token)
    assert b"already created" in r.data.lower()

    with material_app.app_context():
        mc = material_app.extensions["material_config"]
        assert Material.query.count() == 1
        assert Material.query.first().title == "Winner"
        assert UploadedFile.query.count() == 1
        assert FileAccessLog.query.count() == 1
        finals = [p for p in mc.storage_root.iterdir() if not p.name.endswith(".part")]
        assert len(finals) == 1
        assert finals[0].name == UploadedFile.query.first().storage_key
        assert not any(p.name.endswith(".part") for p in mc.storage_root.iterdir())


def test_reactivation_acquires_lesson_and_material_locks(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        m = _material(l, title="M", status=AcademicStatus.ARCHIVED.value)
        gp, up, lp, mp = g.public_id, u.public_id, l.public_id, m.public_id
    login(client, "teacher@example.com")
    events = _capture_locks(
        lambda: client.post(f"{_base_url(gp, up, lp)}/{mp}/toggle-status")
    )
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit", "lock:Lesson", "lock:Material",
    ]


def test_teacher_materials_list_query_count_bounded(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        up_users = _user("uploader@example.com", UserRole.TEACHER.value)
        for i in range(10):
            uf = UploadedFile(
                storage_key=f"k{i}.pdf", original_filename=f"f{i}.pdf", extension="pdf",
                category="document", content_type="application/pdf", byte_size=10,
                sha256="0" * 64, uploaded_by_id=up_users.id,
            )
            db.session.add(uf)
            db.session.commit()
            status = AcademicStatus.ARCHIVED.value if i % 2 else AcademicStatus.ACTIVE.value
            db.session.add(Material(
                lesson_id=l.id, title=f"File {i}", kind="file", uploaded_file_id=uf.id,
                status=status, display_order=i + 1, creation_nonce=f"q-{i}",
            ))
            db.session.commit()
        gp, up, lp = g.public_id, u.public_id, l.public_id
    login(client, "teacher@example.com")

    from sqlalchemy import event

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        resp = client.get(_base_url(gp, up, lp))
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    assert resp.status_code == 200
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(selects) <= 8, (len(selects), selects)


def test_teacher_list_exposes_no_internal_ids_or_paths(app, client):
    with app.app_context():
        _, g, u, l = _setup()
        up_user = _user("up2@example.com", UserRole.TEACHER.value)
        uf = UploadedFile(
            storage_key="deadbeef-secret-key.pdf", original_filename="h.pdf", extension="pdf",
            category="document", content_type="application/pdf", byte_size=10, sha256="0" * 64,
            uploaded_by_id=up_user.id,
        )
        db.session.add(uf)
        db.session.commit()
        m = Material(
            lesson_id=l.id, title="Handout", kind="file", uploaded_file_id=uf.id,
            status="active", display_order=1, creation_nonce="np-1",
        )
        db.session.add(m)
        db.session.commit()
        gp, up, lp = g.public_id, u.public_id, l.public_id
        lid, mid, ufid = l.id, m.id, uf.id
    login(client, "teacher@example.com")
    html = client.get(_base_url(gp, up, lp)).get_data(as_text=True)
    assert "deadbeef-secret-key" not in html
    assert f"/lessons/{lid}/" not in html
    assert f'value="{mid}"' not in html and f'value="{ufid}"' not in html
