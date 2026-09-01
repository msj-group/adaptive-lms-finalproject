"""Teacher Lesson management (M11): nested authorization, draft/published
lifecycle, publication timestamps, ordering across mixed draft/published
Lessons, the signed stale-edit snapshot, the canonical lock order and
single reset, post-lock rechecks, compatible locking with Unit
lifecycle, CSRF / POST-only / PRG behaviour, and non-disclosure 404s.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the structural lock tests here
prove only the *requested* reset/lock order -- never that a real InnoDB
lock blocks a concurrent transaction.
"""

import re
from datetime import date

import pytest

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
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login, make_user

PW = "Sup3rSecret!123"


def _user(email, role, status=UserStatus.ACTIVE.value):
    u = User(
        email=email, password_hash=hash_password(PW),
        full_name=email.split("@")[0], role=role, status=status,
    )
    db.session.add(u)
    db.session.commit()
    return u


def _hierarchy(term_status=AcademicStatus.ACTIVE.value, level_status=AcademicStatus.ACTIVE.value,
               course_status=AcademicStatus.ACTIVE.value, group_status=AcademicStatus.ACTIVE.value,
               group_name="Group A", course_title="English"):
    term = AcademicTerm(name=f"Term {group_name}", start_date=date(2026, 1, 1),
                        end_date=date(2026, 12, 31), status=term_status)
    db.session.add(term)
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add(level)
    db.session.commit()
    course = Course(title=course_title, level_id=level.id, display_order=0, status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=group_name,
                  capacity=20, status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _unit(group, title="Unit 1", display_order=0, status=AcademicStatus.ACTIVE.value):
    u = Unit(group_id=group.id, title=title, display_order=display_order, status=status)
    db.session.add(u)
    db.session.commit()
    return u


def _lesson(unit, title="Lesson 1", display_order=0, status=LessonStatus.DRAFT.value):
    published_at = None
    if status == LessonStatus.PUBLISHED.value:
        from datetime import datetime, timezone
        published_at = datetime.now(timezone.utc)
    lsn = Lesson(unit_id=unit.id, title=title, display_order=display_order,
                 status=status, published_at=published_at)
    db.session.add(lsn)
    db.session.commit()
    return lsn


def _setup(email="teacher@example.com", **hkw):
    """(teacher, group, unit) with an active assignment and an active unit."""
    teacher = _user(email, UserRole.TEACHER.value)
    group = _hierarchy(**hkw)
    _assign(group, teacher)
    unit = _unit(group)
    return teacher, group, unit


def _lessons_url(gpid, upid):
    return f"/teacher/groups/{gpid}/units/{upid}/lessons"


def _create(client, gpid, upid, title="Lesson A", description="", follow=True):
    return client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/new",
        data={"title": title, "description": description},
        follow_redirects=follow,
    )


def _get_edit_snapshot(client, gpid, upid, lpid):
    html = client.get(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/edit"
    ).get_data(as_text=True)
    m = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return m.group(1) if m else ""


def _edit(client, gpid, upid, lpid, snapshot, title="Lesson A", description="", follow=True):
    return client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/edit",
        data={"title": title, "description": description, "edit_snapshot": snapshot},
        follow_redirects=follow,
    )


def _toggle_pub(client, gpid, upid, lpid, follow=True):
    return client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/toggle-publication",
        follow_redirects=follow,
    )


# ===========================================================================
# Authorization / routing
# ===========================================================================


def test_anonymous_redirected_to_login(app, client):
    with app.app_context():
        _, group, unit = _setup()
        gpid, upid = group.public_id, unit.public_id
    resp = client.get(_lessons_url(gpid, upid))
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_student_admin_researcher_get_403(app, client):
    with app.app_context():
        _, group, unit = _setup()
        gpid, upid = group.public_id, unit.public_id
        make_user("s@example.com", UserRole.STUDENT.value)
        make_user("a@example.com", UserRole.ADMINISTRATOR.value)
        make_user("r@example.com", UserRole.RESEARCHER.value)
    for email in ("s@example.com", "a@example.com", "r@example.com"):
        login(client, email)
        assert client.get(_lessons_url(gpid, upid)).status_code == 403


def test_active_assigned_teacher_can_view(app, client):
    with app.app_context():
        _, group, unit = _setup()
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    resp = client.get(_lessons_url(gpid, upid))
    assert resp.status_code == 200 and b"Lessons" in resp.data


def test_co_teacher_is_an_equal_collaborator(app, client):
    with app.app_context():
        _, group, unit = _setup("t1@example.com")
        t2 = _user("t2@example.com", UserRole.TEACHER.value)
        _assign(group, t2)
        lsn = _lesson(unit, title="Made by t1")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "t2@example.com")
    assert client.get(_lessons_url(gpid, upid)).status_code == 200
    snap = _get_edit_snapshot(client, gpid, upid, lpid)
    resp = _edit(client, gpid, upid, lpid, snap, title="Edited by t2")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        assert Lesson.query.first().title == "Edited by t2"


def test_unassigned_teacher_gets_404_no_disclosure(app, client):
    with app.app_context():
        _, group, unit = _setup("assigned@example.com")
        _user("outsider@example.com", UserRole.TEACHER.value)
        lsn = _lesson(unit, title="Secret Lesson")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "outsider@example.com")
    assert client.get(_lessons_url(gpid, upid)).status_code == 404
    r = client.get(f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/edit")
    assert r.status_code == 404 and b"Secret Lesson" not in r.data
    assert _create(client, gpid, upid, follow=False).status_code == 404
    assert _toggle_pub(client, gpid, upid, lpid, follow=False).status_code == 404


def test_removed_assignment_gets_404(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
        unit = _unit(group)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    assert client.get(_lessons_url(gpid, upid)).status_code == 404


def test_cross_group_unit_public_id_gets_404(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        g1 = _hierarchy(group_name="G1")
        g2 = _hierarchy(group_name="G2")
        _assign(g1, teacher)
        _assign(g2, teacher)
        u1 = _unit(g1, title="U1")
        g2pid, u1pid = g2.public_id, u1.public_id
    login(client, "teacher@example.com")
    assert client.get(_lessons_url(g2pid, u1pid)).status_code == 404


def test_cross_unit_lesson_public_id_gets_404(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        u1 = _unit(group, title="U1", display_order=0)
        u2 = _unit(group, title="U2", display_order=1)
        l1 = _lesson(u1, title="L1")
        gpid, u2pid, l1pid = group.public_id, u2.public_id, l1.public_id
    login(client, "teacher@example.com")
    assert client.get(
        f"/teacher/groups/{gpid}/units/{u2pid}/lessons/{l1pid}/edit"
    ).status_code == 404
    assert client.post(
        f"/teacher/groups/{gpid}/units/{u2pid}/lessons/{l1pid}/toggle-publication"
    ).status_code == 404


def test_invalid_uuids_return_404(app, client):
    with app.app_context():
        _, group, unit = _setup()
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    assert client.get(f"/teacher/groups/not-a-uuid/units/{upid}/lessons").status_code == 404
    assert client.get(_lessons_url(gpid, "not-a-uuid")).status_code == 404
    assert client.get(f"{_lessons_url(gpid, upid)}/not-a-uuid/edit").status_code == 404


def test_get_still_available_when_group_archived(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(group_status=AcademicStatus.ARCHIVED.value)
        _assign(group, teacher)
        unit = _unit(group)
        _lesson(unit, title="Historical Lesson", status=LessonStatus.PUBLISHED.value)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    resp = client.get(_lessons_url(gpid, upid))
    assert resp.status_code == 200
    assert b"Historical Lesson" in resp.data
    assert b"not operational" in resp.data.lower()


def test_get_still_available_when_unit_archived(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group, status=AcademicStatus.ARCHIVED.value)
        _lesson(unit, title="Under Archived Unit", status=LessonStatus.PUBLISHED.value)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    resp = client.get(_lessons_url(gpid, upid))
    assert resp.status_code == 200
    assert b"Under Archived Unit" in resp.data
    assert b"hidden from students" in resp.data.lower()


# ===========================================================================
# Create -- always draft, appends after highest order
# ===========================================================================


def test_create_defaults_to_draft(app, client):
    with app.app_context():
        _, group, unit = _setup()
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, upid, title="Fresh")
    assert b"draft" in resp.data.lower()
    with app.app_context():
        lsn = Lesson.query.filter_by(title="Fresh").first()
        assert lsn.status == "draft"
        assert lsn.published_at is None
        assert lsn.display_order == 0


def test_create_appends_after_highest_display_order(app, client):
    with app.app_context():
        _, group, unit = _setup()
        _lesson(unit, title="First", display_order=0)
        _lesson(unit, title="Second", display_order=5, status=LessonStatus.PUBLISHED.value)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    _create(client, gpid, upid, title="Third")
    with app.app_context():
        assert Lesson.query.filter_by(title="Third").first().display_order == 6


def test_create_rejects_duplicate_title_in_same_unit(app, client):
    with app.app_context():
        _, group, unit = _setup()
        _lesson(unit, title="Grammar", status=LessonStatus.PUBLISHED.value)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, upid, title="Grammar")
    assert b"already exists" in resp.data.lower()
    with app.app_context():
        assert Lesson.query.filter_by(title="Grammar").count() == 1


def test_create_allows_same_title_in_a_different_unit(app, client):
    with app.app_context():
        _, group, unit = _setup()
        u2 = _unit(group, title="Unit 2", display_order=1)
        _lesson(unit, title="Shared")
        gpid, u2pid = group.public_id, u2.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, u2pid, title="Shared")
    assert b"created" in resp.data.lower()


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_create_blocked_when_hierarchy_not_operational(app, client, archived):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        _assign(group, teacher)
        unit = _unit(group)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, upid, title="Nope")
    assert resp.status_code == 200
    with app.app_context():
        assert Lesson.query.count() == 0


def test_create_blocked_when_unit_archived(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group, status=AcademicStatus.ARCHIVED.value)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, upid, title="Nope")
    assert resp.status_code == 200
    with app.app_context():
        assert Lesson.query.count() == 0


def test_create_required_title(app, client):
    with app.app_context():
        _, group, unit = _setup()
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, upid, title="")
    assert b"this field is required" in resp.data.lower()
    with app.app_context():
        assert Lesson.query.count() == 0


def test_create_display_order_not_trusted_from_client(app, client):
    with app.app_context():
        _, group, unit = _setup()
        _lesson(unit, title="Existing", display_order=3)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/new",
        data={"title": "Injected", "description": "", "display_order": "0", "status": "published"},
        follow_redirects=True,
    )
    with app.app_context():
        lsn = Lesson.query.filter_by(title="Injected").first()
        assert lsn.display_order == 4
        assert lsn.status == "draft"


# ===========================================================================
# Edit + signed stale-form snapshot
# ===========================================================================


def test_edit_happy_path_draft_keeps_status_and_order(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Old", display_order=2)
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid, lpid)
    resp = _edit(client, gpid, upid, lpid, snap, title="New Title", description="Body here")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        r = Lesson.query.first()
        assert r.title == "New Title" and r.description == "Body here"
        assert r.display_order == 2 and r.status == "draft" and r.published_at is None


def test_edit_published_lesson_stays_published(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Pub", display_order=0, status=LessonStatus.PUBLISHED.value)
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    with app.app_context():
        original_ts = Lesson.query.first().published_at
    snap = _get_edit_snapshot(client, gpid, upid, lpid)
    resp = _edit(client, gpid, upid, lpid, snap, title="Pub", description="revised text")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        r = Lesson.query.first()
        assert r.status == "published"
        assert r.published_at == original_ts
        assert r.description == "revised text"


def test_edit_missing_snapshot_rejected(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Keep")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    resp = client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/edit",
        data={"title": "Hacked", "description": ""},
        follow_redirects=True,
    )
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Lesson.query.first().title == "Keep"


def test_edit_tampered_snapshot_rejected(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Keep")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid, lpid)
    resp = _edit(client, gpid, upid, lpid, snap + "x", title="Hacked")
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Lesson.query.first().title == "Keep"


def test_edit_wrong_object_snapshot_rejected(app, client):
    with app.app_context():
        _, group, unit = _setup()
        la = _lesson(unit, title="A", display_order=0)
        lb = _lesson(unit, title="B", display_order=1)
        gpid, upid = group.public_id, unit.public_id
        lapid, lbpid = la.public_id, lb.public_id
    login(client, "teacher@example.com")
    snap_b = _get_edit_snapshot(client, gpid, upid, lbpid)
    resp = _edit(client, gpid, upid, lapid, snap_b, title="A", description="x")
    assert b"changed since this form was opened" in resp.data.lower()


def test_edit_stale_after_concurrent_change_rejected(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Original")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid, lpid)
    with app.app_context():
        r = Lesson.query.first()
        r.title = "Changed by co-teacher"
        db.session.commit()
    resp = _edit(client, gpid, upid, lpid, snap, title="My Edit")
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Lesson.query.first().title == "Changed by co-teacher"


def test_edit_double_submit_second_is_stale(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Start")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid, lpid)
    first = _edit(client, gpid, upid, lpid, snap, title="First")
    assert b"updated" in first.data.lower()
    second = _edit(client, gpid, upid, lpid, snap, title="Second")
    assert b"changed since this form was opened" in second.data.lower()
    with app.app_context():
        assert Lesson.query.first().title == "First"


def test_edit_blocked_when_hierarchy_not_operational(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="L")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid, lpid)
    with app.app_context():
        g = Group.query.first()
        g.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    resp = _edit(client, gpid, upid, lpid, snap, title="Renamed")
    assert resp.status_code == 200
    with app.app_context():
        assert Lesson.query.first().title == "L"


def test_edit_stale_form_prg_discards_values(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Kept")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    resp = client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/edit",
        data={"title": "Attempted", "description": "attempted body"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    follow = client.get(resp.headers["Location"])
    assert b"Attempted" not in follow.data
    assert b"Kept" in follow.data


# ===========================================================================
# Publication: publish / unpublish / republish
# ===========================================================================


def test_publish_sets_status_and_timestamp(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="ToPublish")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    resp = _toggle_pub(client, gpid, upid, lpid)
    assert b"published" in resp.data.lower()
    with app.app_context():
        r = Lesson.query.first()
        assert r.status == "published" and r.published_at is not None


def test_unpublish_returns_to_draft_and_clears_timestamp(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Pub", status=LessonStatus.PUBLISHED.value)
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    resp = _toggle_pub(client, gpid, upid, lpid)
    assert b"draft" in resp.data.lower()
    with app.app_context():
        r = Lesson.query.first()
        assert r.status == "draft" and r.published_at is None


def test_republish_sets_a_new_timestamp(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="Cycle")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    _toggle_pub(client, gpid, upid, lpid)
    with app.app_context():
        first_ts = Lesson.query.first().published_at
    _toggle_pub(client, gpid, upid, lpid)  # unpublish
    _toggle_pub(client, gpid, upid, lpid)  # republish
    with app.app_context():
        r = Lesson.query.first()
        assert r.status == "published"
        assert r.published_at is not None and r.published_at >= first_ts


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_publish_blocked_by_inactive_hierarchy(app, client, archived):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        _assign(group, teacher)
        unit = _unit(group)
        lsn = _lesson(unit, title="L")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    resp = _toggle_pub(client, gpid, upid, lpid)
    assert resp.status_code == 200
    with app.app_context():
        assert Lesson.query.first().status == "draft"


def test_publish_blocked_by_archived_unit(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group, status=AcademicStatus.ARCHIVED.value)
        lsn = _lesson(unit, title="L")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    resp = _toggle_pub(client, gpid, upid, lpid)
    assert resp.status_code == 200
    with app.app_context():
        assert Lesson.query.first().status == "draft"


def test_unpublish_allowed_under_inactive_unit(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group, status=AcademicStatus.ARCHIVED.value)
        lsn = _lesson(unit, title="L", status=LessonStatus.PUBLISHED.value)
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    resp = _toggle_pub(client, gpid, upid, lpid)
    assert b"draft" in resp.data.lower()
    with app.app_context():
        r = Lesson.query.first()
        assert r.status == "draft" and r.published_at is None


def test_unpublish_allowed_under_inactive_hierarchy(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(course_status=AcademicStatus.ARCHIVED.value)
        _assign(group, teacher)
        unit = _unit(group)
        lsn = _lesson(unit, title="L", status=LessonStatus.PUBLISHED.value)
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    resp = _toggle_pub(client, gpid, upid, lpid)
    assert b"draft" in resp.data.lower()
    with app.app_context():
        assert Lesson.query.first().status == "draft"


# ===========================================================================
# Lifecycle non-cascade
# ===========================================================================


def test_unit_archive_does_not_rewrite_lesson_status(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group)
        _lesson(unit, title="Pub", display_order=0, status=LessonStatus.PUBLISHED.value)
        _lesson(unit, title="Draft", display_order=1)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    client.post(f"/teacher/groups/{gpid}/units/{upid}/toggle-status", follow_redirects=True)
    with app.app_context():
        assert Unit.query.first().status == "archived"
        by_title = {lsn.title: lsn for lsn in Lesson.query.all()}
        assert by_title["Pub"].status == "published"
        assert by_title["Pub"].published_at is not None
        assert by_title["Draft"].status == "draft"


def test_group_archive_does_not_cascade_to_lessons(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group)
        _lesson(unit, title="Pub", status=LessonStatus.PUBLISHED.value)
        gpid = group.public_id
    login(client, "admin@example.com")
    client.post(f"/admin/groups/{gpid}/toggle-status", follow_redirects=True)
    with app.app_context():
        assert Group.query.first().status == "archived"
        assert Lesson.query.first().status == "published"


def test_reactivation_restores_visibility_without_status_rewrite(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        unit = _unit(group)
        _lesson(unit, title="Pub", status=LessonStatus.PUBLISHED.value)
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    client.post(f"/teacher/groups/{gpid}/units/{upid}/toggle-status", follow_redirects=True)
    client.post(f"/teacher/groups/{gpid}/units/{upid}/toggle-status", follow_redirects=True)
    with app.app_context():
        u = Unit.query.first()
        assert u.status == "active"
        assert Lesson.query.first().status == "published"


# ===========================================================================
# Ordering across mixed draft/published
# ===========================================================================


def _make_three_lessons(app, statuses=None):
    statuses = statuses or ["draft", "draft", "draft"]
    with app.app_context():
        _, group, unit = _setup()
        a = _lesson(unit, title="A", display_order=0, status=statuses[0])
        b = _lesson(unit, title="B", display_order=1, status=statuses[1])
        c = _lesson(unit, title="C", display_order=2, status=statuses[2])
        return group.public_id, unit.public_id, a.public_id, b.public_id, c.public_id


def _titles_in_order(app):
    with app.app_context():
        return [lsn.title for lsn in Lesson.query.order_by(Lesson.display_order, Lesson.id).all()]


def test_move_down_swaps_with_next_regardless_of_status(app, client):
    gpid, upid, apid, bpid, cpid = _make_three_lessons(
        app, statuses=["published", "draft", "published"]
    )
    login(client, "teacher@example.com")
    client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{apid}/move-down",
        follow_redirects=True,
    )
    assert _titles_in_order(app) == ["B", "A", "C"]


def test_move_up_swaps_with_previous(app, client):
    gpid, upid, apid, bpid, cpid = _make_three_lessons(app)
    login(client, "teacher@example.com")
    client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{cpid}/move-up",
        follow_redirects=True,
    )
    assert _titles_in_order(app) == ["A", "C", "B"]


def test_move_up_at_boundary_is_safe_no_op(app, client):
    gpid, upid, apid, bpid, cpid = _make_three_lessons(app)
    login(client, "teacher@example.com")
    resp = client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{apid}/move-up",
        follow_redirects=True,
    )
    assert b"already first" in resp.data.lower()
    assert _titles_in_order(app) == ["A", "B", "C"]


def test_move_down_at_boundary_is_safe_no_op(app, client):
    gpid, upid, apid, bpid, cpid = _make_three_lessons(app)
    login(client, "teacher@example.com")
    resp = client.post(
        f"/teacher/groups/{gpid}/units/{upid}/lessons/{cpid}/move-down",
        follow_redirects=True,
    )
    assert b"already last" in resp.data.lower()


def test_students_see_published_lessons_in_relative_order(app, client):
    """A published lesson keeps its position within the complete Teacher
    order -- the published-only view is a filtered subsequence."""
    with app.app_context():
        _, group, unit = _setup()
        _lesson(unit, title="P1", display_order=0, status=LessonStatus.PUBLISHED.value)
        _lesson(unit, title="D1", display_order=1)
        _lesson(unit, title="P2", display_order=2, status=LessonStatus.PUBLISHED.value)
        from app.services.lesson_queries import published_lessons_ordered
        titles = [lsn.title for lsn in published_lessons_ordered(unit.id)]
        assert titles == ["P1", "P2"]


# ===========================================================================
# Structural: canonical lock order + single reset + post-lock recheck
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


def test_create_locks_canonical_order_single_reset(app, client):
    with app.app_context():
        _, group, unit = _setup()
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    events = _capture_locks(lambda: _create(client, gpid, upid, title="Locked"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit",
    ]


def test_edit_locks_canonical_order_including_lesson(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="L")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid, lpid)
    events = _capture_locks(lambda: _edit(client, gpid, upid, lpid, snap, title="L2"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit", "lock:Lesson",
    ]


def test_toggle_publication_locks_canonical_order(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="L")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    events = _capture_locks(lambda: _toggle_pub(client, gpid, upid, lpid))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit", "lock:Lesson",
    ]


def test_reorder_locks_lessons_ascending_id(app, client):
    with app.app_context():
        _, group, unit = _setup()
        a = _lesson(unit, title="A", display_order=0)
        b = _lesson(unit, title="B", display_order=1)
        gpid, upid, apid = group.public_id, unit.public_id, a.public_id
        a_id, b_id = a.id, b.id
    login(client, "teacher@example.com")
    events = _capture_locks(
        lambda: client.post(
            f"/teacher/groups/{gpid}/units/{upid}/lessons/{apid}/move-down"
        )
    )
    locks = [e for e in events if e.startswith("lock:")]
    assert locks[:7] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit",
    ]
    assert locks[7:] == ["lock:Lesson", "lock:Lesson"]
    assert a_id < b_id


def test_post_lock_recheck_rejects_concurrently_removed_assignment(app, client):
    import app.blueprints.teacher.lessons as lessons_mod

    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        assignment = _assign(group, teacher)
        unit = _unit(group)
        gpid, upid, assignment_id = group.public_id, unit.public_id, assignment.id
    login(client, "teacher@example.com")

    original = lessons_mod.lock_group_in_open_transaction

    def remove_then_lock(pid):
        row = db.session.get(GroupTeacherAssignment, assignment_id)
        row.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()
        return original(pid)

    from unittest.mock import patch

    with patch.object(lessons_mod, "lock_group_in_open_transaction", side_effect=remove_then_lock):
        resp = _create(client, gpid, upid, title="Race", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert Lesson.query.count() == 0


def test_post_lock_recheck_rejects_concurrently_archived_unit(app, client):
    import app.blueprints.teacher.lessons as lessons_mod

    with app.app_context():
        _, group, unit = _setup()
        gpid, upid, unit_id = group.public_id, unit.public_id, unit.id
    login(client, "teacher@example.com")

    original = lessons_mod.lock_group_in_open_transaction

    def archive_unit_then_lock(pid):
        row = db.session.get(Unit, unit_id)
        row.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        return original(pid)

    from unittest.mock import patch

    with patch.object(lessons_mod, "lock_group_in_open_transaction", side_effect=archive_unit_then_lock):
        resp = _create(client, gpid, upid, title="Race", follow=True)
    assert resp.status_code == 200
    with app.app_context():
        assert Lesson.query.count() == 0


def test_unit_lifecycle_and_lesson_mutation_use_compatible_locks(app, client):
    """The Unit toggle route (M10) and every Lesson mutation (M11) both
    take AcademicTerm -> Level -> Course -> Group -> Teacher User ->
    GroupTeacherAssignment -> Unit under one deliberate reset, so a
    same-Unit Unit lifecycle change and a Lesson create/reorder/publish
    serialise on the shared Group + Unit locks."""
    with app.app_context():
        _, group, unit = _setup()
        gpid, upid = group.public_id, unit.public_id
    login(client, "teacher@example.com")
    lesson_events = _capture_locks(lambda: _create(client, gpid, upid, title="X"))
    lesson_locks = [e for e in lesson_events if e.startswith("lock:")]
    unit_locks = [
        e for e in _capture_locks(
            lambda: client.post(f"/teacher/groups/{gpid}/units/{upid}/toggle-status")
        ) if e.startswith("lock:")
    ]
    prefix = [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit",
    ]
    assert unit_locks[:7] == prefix
    assert lesson_locks == prefix
    assert lesson_events.count("reset") == 1


# ===========================================================================
# CSRF / POST-only / no leaked IDs / dashboard link
# ===========================================================================


def test_csrf_enforced_on_lesson_mutations():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        db.create_all()
        try:
            teacher = _user("teacher@example.com", UserRole.TEACHER.value)
            group = _hierarchy()
            _assign(group, teacher)
            unit = _unit(group)
            lsn = _lesson(unit, title="L")
            gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"',
                login_page.get_data(as_text=True),
            ).group(1)
            client.post("/auth/login", data={"email": "teacher@example.com", "password": PW, "csrf_token": token})

            assert client.post(
                f"/teacher/groups/{gpid}/units/{upid}/lessons/new", data={"title": "X"}
            ).status_code == 400
            assert client.post(
                f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/toggle-publication"
            ).status_code == 400
            assert client.post(
                f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}/move-up"
            ).status_code == 400
            assert Lesson.query.count() == 1
            assert Lesson.query.first().status == "draft"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_toggle_and_move_are_post_only(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="L")
        gpid, upid, lpid = group.public_id, unit.public_id, lsn.public_id
    login(client, "teacher@example.com")
    base = f"/teacher/groups/{gpid}/units/{upid}/lessons/{lpid}"
    assert client.get(f"{base}/toggle-publication").status_code == 405
    assert client.get(f"{base}/move-up").status_code == 405
    assert client.get(f"{base}/move-down").status_code == 405


def test_units_page_shows_manage_lessons_link(app, client):
    with app.app_context():
        _, group, unit = _setup()
        arch = _unit(group, title="Archived Unit", display_order=1,
                     status=AcademicStatus.ARCHIVED.value)
        gpid, upid, arch_pid = group.public_id, unit.public_id, arch.public_id
    login(client, "teacher@example.com")
    html = client.get(f"/teacher/groups/{gpid}/units").get_data(as_text=True)
    assert f"/teacher/groups/{gpid}/units/{upid}/lessons" in html
    assert f"/teacher/groups/{gpid}/units/{arch_pid}/lessons" in html


def test_no_internal_ids_in_lessons_html(app, client):
    with app.app_context():
        _, group, unit = _setup()
        lsn = _lesson(unit, title="L")
        gpid, gid, upid, uid, lid = (
            group.public_id, group.id, unit.public_id, unit.id, lsn.id
        )
        lpid = lsn.public_id
    login(client, "teacher@example.com")
    for path in (
        _lessons_url(gpid, upid),
        f"{_lessons_url(gpid, upid)}/new",
        f"{_lessons_url(gpid, upid)}/{lpid}/edit",
    ):
        html = client.get(path).get_data(as_text=True)
        assert f"/units/{uid}/" not in html
        assert f"/groups/{gid}/" not in html
        assert f'value="{uid}"' not in html
        assert f'value="{lid}"' not in html
