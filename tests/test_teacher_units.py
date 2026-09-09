"""Teacher Unit management (M10): authorization, lifecycle, ordering,
signed stale-form protection, the canonical lock order, the Group
identity-freeze extension, and non-disclosure 404 behaviour.

SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
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


def _hierarchy(
    term_status=AcademicStatus.ACTIVE.value,
    level_status=AcademicStatus.ACTIVE.value,
    course_status=AcademicStatus.ACTIVE.value,
    group_status=AcademicStatus.ACTIVE.value,
    group_name="Group A",
    course_title="English",
):
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


def _setup_teacher_group(email="teacher@example.com", **hkw):
    """Returns (teacher, group) with an active assignment."""
    teacher = _user(email, UserRole.TEACHER.value)
    group = _hierarchy(**hkw)
    _assign(group, teacher)
    return teacher, group


def _units_url(gpid):
    return f"/teacher/groups/{gpid}/units"


def _create(client, gpid, title="Unit A", description="", follow=True):
    return client.post(
        f"/teacher/groups/{gpid}/units/new",
        data={"title": title, "description": description},
        follow_redirects=follow,
    )


def _get_edit_snapshot(client, gpid, upid):
    html = client.get(f"/teacher/groups/{gpid}/units/{upid}/edit").get_data(as_text=True)
    m = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return m.group(1) if m else ""


def _edit(client, gpid, upid, snapshot, title="Unit A", description="", follow=True):
    return client.post(
        f"/teacher/groups/{gpid}/units/{upid}/edit",
        data={"title": title, "description": description, "edit_snapshot": snapshot},
        follow_redirects=follow,
    )


def _toggle(client, gpid, upid):
    return client.post(f"/teacher/groups/{gpid}/units/{upid}/toggle-status", follow_redirects=True)


# ===========================================================================
# Authorization / routing
# ===========================================================================


def test_anonymous_redirected_to_login(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        gpid = group.public_id
    resp = client.get(_units_url(gpid))
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_student_and_admin_and_researcher_get_403(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        gpid = group.public_id
        make_user("s@example.com", UserRole.STUDENT.value)
    login(client, "s@example.com")
    assert client.get(_units_url(gpid)).status_code == 403

    with app.app_context():
        make_user("a@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "a@example.com")
    assert client.get(_units_url(gpid)).status_code == 403


def test_active_assigned_teacher_can_view(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = client.get(_units_url(gpid))
    assert resp.status_code == 200
    assert b"Units" in resp.data


def test_co_teacher_is_an_equal_collaborator(app, client):
    with app.app_context():
        _, group = _setup_teacher_group("t1@example.com")
        t2 = _user("t2@example.com", UserRole.TEACHER.value)
        _assign(group, t2)
        u = _unit(group, title="Made by t1")
        gpid, upid = group.public_id, u.public_id
    login(client, "t2@example.com")
    assert client.get(_units_url(gpid)).status_code == 200
    snap = _get_edit_snapshot(client, gpid, upid)
    resp = _edit(client, gpid, upid, snap, title="Edited by t2")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        assert Unit.query.first().title == "Edited by t2"


def test_unassigned_teacher_gets_404_no_disclosure(app, client):
    with app.app_context():
        _, group = _setup_teacher_group("assigned@example.com")
        _user("outsider@example.com", UserRole.TEACHER.value)
        u = _unit(group, title="Secret Unit")
        gpid, upid = group.public_id, u.public_id
    login(client, "outsider@example.com")
    assert client.get(_units_url(gpid)).status_code == 404
    r = client.get(f"/teacher/groups/{gpid}/units/{upid}/edit")
    assert r.status_code == 404
    assert b"Secret Unit" not in r.data
    assert _create(client, gpid, title="X", follow=False).status_code == 404


def test_removed_assignment_gets_404(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher, status=GroupTeacherAssignmentStatus.REMOVED.value)
        gpid = group.public_id
    login(client, "teacher@example.com")
    assert client.get(_units_url(gpid)).status_code == 404


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
    assert client.get(f"/teacher/groups/{g2pid}/units/{u1pid}/edit").status_code == 404
    assert client.post(f"/teacher/groups/{g2pid}/units/{u1pid}/toggle-status").status_code == 404


def test_invalid_uuids_return_404(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        gpid = group.public_id
    login(client, "teacher@example.com")
    assert client.get("/teacher/groups/not-a-uuid/units").status_code == 404
    assert client.get(f"/teacher/groups/{gpid}/units/not-a-uuid/edit").status_code == 404


def test_get_still_available_when_group_archived(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(group_status=AcademicStatus.ARCHIVED.value)
        _assign(group, teacher)
        _unit(group, title="Historical Unit")
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = client.get(_units_url(gpid))
    assert resp.status_code == 200
    assert b"Historical Unit" in resp.data
    assert b"read-only history" in resp.data


# ===========================================================================
# Create
# ===========================================================================


def test_create_appends_after_highest_display_order(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        _unit(group, title="First", display_order=0)
        _unit(group, title="Second", display_order=5)  # deliberate gap
        gpid = group.public_id
    login(client, "teacher@example.com")
    _create(client, gpid, title="Third")
    with app.app_context():
        third = Unit.query.filter_by(title="Third").first()
        assert third.display_order == 6


def test_create_rejects_duplicate_title_in_same_group(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        _unit(group, title="Grammar", status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, title="Grammar")
    assert b"already exists" in resp.data.lower()
    with app.app_context():
        assert Unit.query.filter_by(title="Grammar").count() == 1


def test_create_allows_same_title_in_a_different_group(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        g1 = _hierarchy(group_name="G1")
        g2 = _hierarchy(group_name="G2")
        _assign(g1, teacher)
        _assign(g2, teacher)
        _unit(g1, title="Shared")
        g2pid = g2.public_id
    login(client, "teacher@example.com")
    resp = _create(client, g2pid, title="Shared")
    assert b"created" in resp.data.lower()


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_create_blocked_when_hierarchy_not_operational(app, client, archived):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        kw = {f"{archived}_status": AcademicStatus.ARCHIVED.value}
        group = _hierarchy(**kw)
        _assign(group, teacher)
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, title="Nope")
    assert resp.status_code == 200
    with app.app_context():
        assert Unit.query.count() == 0


def test_create_required_title(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, title="")
    assert b"this field is required" in resp.data.lower()
    with app.app_context():
        assert Unit.query.count() == 0


# ===========================================================================
# Edit + signed stale-form snapshot
# ===========================================================================


def test_edit_happy_path(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="Old", display_order=0)
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid)
    resp = _edit(client, gpid, upid, snap, title="New Title", description="Now with detail")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        r = Unit.query.first()
        assert r.title == "New Title" and r.description == "Now with detail"
        assert r.display_order == 0 and r.status == "active"


def test_edit_missing_snapshot_rejected(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="Keep")
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    resp = client.post(
        f"/teacher/groups/{gpid}/units/{upid}/edit",
        data={"title": "Hacked", "description": ""},
        follow_redirects=True,
    )
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Unit.query.first().title == "Keep"


def test_edit_tampered_snapshot_rejected(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="Keep")
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid)
    resp = _edit(client, gpid, upid, snap + "x", title="Hacked")
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Unit.query.first().title == "Keep"


def test_edit_wrong_object_snapshot_rejected(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        ua = _unit(group, title="A", display_order=0)
        ub = _unit(group, title="B", display_order=1)
        gpid, uapid, ubpid = group.public_id, ua.public_id, ub.public_id
    login(client, "teacher@example.com")
    snap_b = _get_edit_snapshot(client, gpid, ubpid)
    resp = _edit(client, gpid, uapid, snap_b, title="A", description="x")
    assert b"changed since this form was opened" in resp.data.lower()


def test_edit_stale_after_concurrent_change_rejected(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="Original")
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid)
    with app.app_context():
        r = Unit.query.first()
        r.title = "Changed by co-teacher"
        db.session.commit()
    resp = _edit(client, gpid, upid, snap, title="My Edit")
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Unit.query.first().title == "Changed by co-teacher"


def test_edit_double_submit_second_is_stale(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="Start")
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid)
    first = _edit(client, gpid, upid, snap, title="First")
    assert b"updated" in first.data.lower()
    second = _edit(client, gpid, upid, snap, title="Second")
    assert b"changed since this form was opened" in second.data.lower()
    with app.app_context():
        assert Unit.query.first().title == "First"


def test_edit_archived_unit_stays_archived(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="Archived One", status=AcademicStatus.ARCHIVED.value)
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid)
    resp = _edit(client, gpid, upid, snap, title="Archived One", description="fixed a typo")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        r = Unit.query.first()
        assert r.status == "archived"
        assert r.description == "fixed a typo"


def test_edit_blocked_when_hierarchy_not_operational(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="U")
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid)
    with app.app_context():
        g = Group.query.first()
        g.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    resp = _edit(client, gpid, upid, snap, title="Renamed")
    assert resp.status_code == 200
    with app.app_context():
        assert Unit.query.first().title == "U"


# ===========================================================================
# Toggle status
# ===========================================================================


def test_archive_always_allowed_even_under_archived_hierarchy(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(course_status=AcademicStatus.ARCHIVED.value)
        _assign(group, teacher)
        u = _unit(group, title="Cleanup Me")
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    resp = _toggle(client, gpid, upid)
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Unit.query.first().status == "archived"


def test_reactivate_requires_operational_hierarchy(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(term_status=AcademicStatus.ARCHIVED.value)
        _assign(group, teacher)
        u = _unit(group, title="U", status=AcademicStatus.ARCHIVED.value)
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    resp = _toggle(client, gpid, upid)
    assert resp.status_code == 200
    with app.app_context():
        assert Unit.query.first().status == "archived"


def test_reactivate_appends_after_highest_display_order(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        _unit(group, title="Active 1", display_order=0)
        _unit(group, title="Active 2", display_order=1)
        arch = _unit(group, title="Was Archived", display_order=0, status=AcademicStatus.ARCHIVED.value)
        gpid, upid = group.public_id, arch.public_id
    login(client, "teacher@example.com")
    _toggle(client, gpid, upid)
    with app.app_context():
        r = Unit.query.filter_by(title="Was Archived").first()
        assert r.status == "active"
        assert r.display_order == 2  # max(0, 1) + 1


def test_group_archive_does_not_cascade_to_units(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _assign(group, teacher)
        _unit(group, title="Active", status=AcademicStatus.ACTIVE.value)
        _unit(group, title="Archived", status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id
    login(client, "admin@example.com")
    client.post(f"/admin/groups/{gpid}/toggle-status", follow_redirects=True)
    with app.app_context():
        assert Group.query.first().status == "archived"
        assert sorted(u.status for u in Unit.query.all()) == ["active", "archived"]


# ===========================================================================
# Ordering
# ===========================================================================


def _make_three_units(app):
    with app.app_context():
        _, group = _setup_teacher_group()
        a = _unit(group, title="A", display_order=0)
        b = _unit(group, title="B", display_order=1)
        c = _unit(group, title="C", display_order=2)
        return group.public_id, a.public_id, b.public_id, c.public_id


def test_move_down_swaps_with_next_active(app, client):
    gpid, apid, bpid, cpid = _make_three_units(app)
    login(client, "teacher@example.com")
    client.post(f"/teacher/groups/{gpid}/units/{apid}/move-down", follow_redirects=True)
    with app.app_context():
        order = [u.title for u in Unit.query.order_by(Unit.display_order, Unit.id).all()]
        assert order == ["B", "A", "C"]


def test_move_up_swaps_with_previous_active(app, client):
    gpid, apid, bpid, cpid = _make_three_units(app)
    login(client, "teacher@example.com")
    client.post(f"/teacher/groups/{gpid}/units/{cpid}/move-up", follow_redirects=True)
    with app.app_context():
        order = [u.title for u in Unit.query.order_by(Unit.display_order, Unit.id).all()]
        assert order == ["A", "C", "B"]


def test_move_up_at_boundary_is_safe_no_op(app, client):
    gpid, apid, bpid, cpid = _make_three_units(app)
    login(client, "teacher@example.com")
    resp = client.post(f"/teacher/groups/{gpid}/units/{apid}/move-up", follow_redirects=True)
    assert b"already first" in resp.data.lower()
    with app.app_context():
        order = [u.title for u in Unit.query.order_by(Unit.display_order, Unit.id).all()]
        assert order == ["A", "B", "C"]


def test_move_down_at_boundary_is_safe_no_op(app, client):
    gpid, apid, bpid, cpid = _make_three_units(app)
    login(client, "teacher@example.com")
    resp = client.post(f"/teacher/groups/{gpid}/units/{cpid}/move-down", follow_redirects=True)
    assert b"already last" in resp.data.lower()


def test_move_skips_archived_and_swaps_nearest_active(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        a = _unit(group, title="A", display_order=0)
        _unit(group, title="ARCH", display_order=1, status=AcademicStatus.ARCHIVED.value)
        c = _unit(group, title="C", display_order=2)
        gpid, apid = group.public_id, a.public_id
    login(client, "teacher@example.com")
    client.post(f"/teacher/groups/{gpid}/units/{apid}/move-down", follow_redirects=True)
    with app.app_context():
        active_order = [
            u.title for u in Unit.query.filter_by(status="active").order_by(Unit.display_order, Unit.id).all()
        ]
        assert active_order == ["C", "A"]
        assert Unit.query.filter_by(title="ARCH").first().display_order == 1  # untouched


def test_reordering_an_archived_unit_is_rejected(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        _unit(group, title="A", display_order=0)
        arch = _unit(group, title="ARCH", display_order=1, status=AcademicStatus.ARCHIVED.value)
        gpid, apid = group.public_id, arch.public_id
    login(client, "teacher@example.com")
    resp = client.post(f"/teacher/groups/{gpid}/units/{apid}/move-up", follow_redirects=True)
    assert b"only active units can be reordered" in resp.data.lower()


def test_move_controls_only_shown_for_active_units(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        _unit(group, title="ActiveOne", display_order=0)
        _unit(group, title="ActiveTwo", display_order=1)
        _unit(group, title="ArchivedRow", display_order=2, status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id
    login(client, "teacher@example.com")
    html = client.get(_units_url(gpid)).get_data(as_text=True)
    active_section, archived_section = html.split("<h2>Archived units", 1)
    assert "/move-up" in active_section or "/move-down" in active_section
    # the archived section row must not carry move controls
    assert "/move-up" not in archived_section and "/move-down" not in archived_section


# ===========================================================================
# Group identity freeze extension
# ===========================================================================


def _edit_group(client, gpid, term_id, course_id, name="Group A", capacity="20"):
    html = client.get(f"/admin/groups/{gpid}/edit").get_data(as_text=True)
    snap = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    return client.post(
        f"/admin/groups/{gpid}/edit",
        data={
            "academic_term_id": term_id, "course_id": course_id, "name": name,
            "code": "", "capacity": capacity, "edit_snapshot": snap,
        },
        follow_redirects=True,
    )


@pytest.mark.parametrize("unit_status", [AcademicStatus.ACTIVE.value, AcademicStatus.ARCHIVED.value])
def test_unit_history_freezes_group_identity(app, client, unit_status):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term_a = _hierarchy(group_name="GA").academic_term
        term_b = AcademicTerm(name="Term B", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        db.session.add(term_b)
        db.session.commit()
        group = Group.query.filter_by(name="GA").first()
        _unit(group, title="Only History Row", status=unit_status)
        gpid, term_b_id, course_id = group.public_id, term_b.id, group.course_id

    login(client, "admin@example.com")
    resp = _edit_group(client, gpid, term_b_id, course_id, name="GA")
    body = resp.data.lower()
    assert b"cannot be changed" in body
    # Phase 4 / M01 and then Phase 4 / M04A each extended the enumeration.
    assert b"schedule, unit, assignment, or quiz history" in body
    with app.app_context():
        assert Group.query.filter_by(public_id=gpid).first().academic_term_id != term_b_id


def test_group_form_locked_notice_mentions_unit_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _hierarchy(group_name="GA")
        _unit(group, title="U")
        gpid = group.public_id
    login(client, "admin@example.com")
    html = client.get(f"/admin/groups/{gpid}/edit").get_data(as_text=True).lower()
    # Phase 4 / M01 and then Phase 4 / M04A each extended the enumeration.
    assert "schedule, unit, assignment, or quiz history" in html


def test_non_identity_group_edit_still_allowed_with_unit_history(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = _hierarchy(group_name="GA")
        _unit(group, title="U")
        gpid, term_id, course_id = group.public_id, group.academic_term_id, group.course_id
    login(client, "admin@example.com")
    resp = _edit_group(client, gpid, term_id, course_id, name="Renamed GA", capacity="12")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        g = Group.query.filter_by(public_id=gpid).first()
        assert g.name == "Renamed GA" and g.capacity == 12


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
        _, group = _setup_teacher_group()
        gpid = group.public_id
    login(client, "teacher@example.com")
    events = _capture_locks(lambda: _create(client, gpid, title="Locked"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment",
    ]


def test_edit_locks_canonical_order_including_unit(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="U")
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    snap = _get_edit_snapshot(client, gpid, upid)
    events = _capture_locks(lambda: _edit(client, gpid, upid, snap, title="U2"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Unit",
    ]


def test_reorder_locks_units_ascending_id(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        a = _unit(group, title="A", display_order=0)
        b = _unit(group, title="B", display_order=1)
        gpid, apid = group.public_id, a.public_id
        a_id, b_id = a.id, b.id
    login(client, "teacher@example.com")
    events = _capture_locks(
        lambda: client.post(f"/teacher/groups/{gpid}/units/{apid}/move-down")
    )
    locks = [e for e in events if e.startswith("lock:")]
    assert locks[:6] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment",
    ]
    assert locks[6:] == ["lock:Unit", "lock:Unit"]  # target + neighbour
    assert a_id < b_id  # sanity: helper locks ascending


def test_post_lock_recheck_rejects_concurrently_removed_assignment(app, client):
    """History injected between the pre-lock preview and the lock: the
    assignment is removed, so the post-lock re-check must 404."""
    import app.blueprints.teacher.units as units_mod

    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        assignment = _assign(group, teacher)
        gpid, assignment_id = group.public_id, assignment.id
    login(client, "teacher@example.com")

    original = units_mod.lock_group_in_open_transaction

    def remove_assignment_then_lock(pid):
        row = db.session.get(GroupTeacherAssignment, assignment_id)
        row.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()
        return original(pid)

    from unittest.mock import patch

    with patch.object(units_mod, "lock_group_in_open_transaction", side_effect=remove_assignment_then_lock):
        resp = _create(client, gpid, title="Race", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert Unit.query.count() == 0


def test_concurrent_unit_create_vs_group_retarget_serialize_on_group_lock(app, client):
    """A Unit created (committed) before a Group retarget makes the
    retarget's post-lock `_group_identity_frozen` recheck see the Unit
    and reject -- proving the two use a compatible Group lock."""
    import app.blueprints.admin.groups as groups_mod

    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        term_a = _hierarchy(group_name="GA").academic_term
        term_b = AcademicTerm(name="Term B", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        db.session.add(term_b)
        db.session.commit()
        group = Group.query.filter_by(name="GA").first()
        _assign(group, teacher)
        gpid, gid, tid = group.public_id, group.id, teacher.id
        term_b_id, course_id = term_b.id, group.course_id

    login(client, "admin@example.com")

    original = groups_mod._lock_group_in_open_transaction_or_404

    def create_unit_then_lock(pid):
        db.session.add(Unit(group_id=gid, title="Concurrent Unit", display_order=0, status="active"))
        db.session.commit()
        return original(pid)

    from unittest.mock import patch

    with patch.object(groups_mod, "_lock_group_in_open_transaction_or_404", side_effect=create_unit_then_lock):
        resp = _edit_group(client, gpid, term_b_id, course_id, name="GA")
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        assert Group.query.filter_by(public_id=gpid).first().academic_term_id != term_b_id


# ===========================================================================
# CSRF / dashboard link / no leaked IDs
# ===========================================================================


def test_csrf_enforced_on_unit_mutations():
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
            u = _unit(group, title="U")
            gpid, upid = group.public_id, u.public_id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post("/auth/login", data={"email": "teacher@example.com", "password": PW, "csrf_token": token})

            assert client.post(f"/teacher/groups/{gpid}/units/new", data={"title": "X"}).status_code == 400
            assert client.post(f"/teacher/groups/{gpid}/units/{upid}/toggle-status").status_code == 400
            assert client.post(f"/teacher/groups/{gpid}/units/{upid}/move-up").status_code == 400
            assert Unit.query.count() == 1
            assert Unit.query.first().status == "active"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_toggle_and_move_are_post_only(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="U")
        gpid, upid = group.public_id, u.public_id
    login(client, "teacher@example.com")
    assert client.get(f"/teacher/groups/{gpid}/units/{upid}/toggle-status").status_code == 405
    assert client.get(f"/teacher/groups/{gpid}/units/{upid}/move-up").status_code == 405


def test_dashboard_manage_units_link_is_scoped(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        mine = _hierarchy(group_name="Mine")
        other = _hierarchy(group_name="Other")
        _assign(mine, teacher)
        _assign(other, _user("other@example.com", UserRole.TEACHER.value))
        mine_pid, other_pid = mine.public_id, other.public_id
    login(client, "teacher@example.com")
    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert f"/teacher/groups/{mine_pid}/units" in html
    assert f"/teacher/groups/{other_pid}/units" not in html


def test_no_internal_ids_in_units_html(app, client):
    with app.app_context():
        _, group = _setup_teacher_group()
        u = _unit(group, title="U")
        gpid, gid, uid = group.public_id, group.id, u.id
    login(client, "teacher@example.com")
    for path in (_units_url(gpid), f"/teacher/groups/{gpid}/units/new",
                 f"/teacher/groups/{gpid}/units/{u.public_id}/edit"):
        html = client.get(path).get_data(as_text=True)
        assert f"/groups/{gid}/" not in html
        assert f'value="{uid}"' not in html
        assert f'value="{gid}"' not in html
