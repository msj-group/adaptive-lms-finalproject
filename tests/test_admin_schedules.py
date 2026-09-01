"""Administrator management of recurring weekly Group schedules (M08):
the nested create/edit/toggle workflows, the center-wide overview, the
lifecycle/identity guards, the signed edit snapshot, and the structural
lock-order checks.

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
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login, make_user

TERM_START = date(2026, 9, 1)
TERM_END = date(2026, 12, 31)
MONDAY = 0
TUESDAY = 1


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _admin(client):
    make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")


def _term(name="Fall 2026", start=TERM_START, end=TERM_END, status=AcademicStatus.ACTIVE.value):
    term = AcademicTerm(name=name, start_date=start, end_date=end, status=status)
    db.session.add(term)
    db.session.commit()
    return term


def _level(name="Level 1", status=AcademicStatus.ACTIVE.value):
    level = Level(name=name, display_order=0, status=status)
    db.session.add(level)
    db.session.commit()
    return level


def _course(level, title="Course A", status=AcademicStatus.ACTIVE.value):
    course = Course(level_id=level.id, title=title, display_order=0, status=status)
    db.session.add(course)
    db.session.commit()
    return course


def _group(term=None, course=None, name="Group A", status=AcademicStatus.ACTIVE.value, capacity=20):
    term = term or _term()
    course = course or _course(_level())
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=name, capacity=capacity, status=status
    )
    db.session.add(group)
    db.session.commit()
    return group


def _schedule(
    group,
    day_of_week=MONDAY,
    start="09:00",
    end="10:30",
    eff_start=TERM_START,
    eff_end=TERM_END,
    location=None,
    status=AcademicStatus.ACTIVE.value,
):
    from datetime import datetime

    row = Schedule(
        group_id=group.id,
        day_of_week=day_of_week,
        start_time=datetime.strptime(start, "%H:%M").time(),
        end_time=datetime.strptime(end, "%H:%M").time(),
        effective_start_date=eff_start,
        effective_end_date=eff_end,
        location=location,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _teacher(email="t@example.com"):
    u = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name="Teacher",
        role=UserRole.TEACHER.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(u)
    db.session.commit()
    return u


def _student(email="s@example.com"):
    u = User(
        email=email,
        password_hash=hash_password("Sup3rSecret!123"),
        full_name="Student",
        role=UserRole.STUDENT.value,
        status=UserStatus.ACTIVE.value,
    )
    db.session.add(u)
    db.session.commit()
    return u


def _create_data(
    day_of_week=MONDAY,
    start="09:00",
    end="10:30",
    eff_start="2026-09-07",
    eff_end="2026-12-28",
    location="Room 1",
):
    return {
        "day_of_week": str(day_of_week),
        "start_time": start,
        "end_time": end,
        "effective_start_date": eff_start,
        "effective_end_date": eff_end,
        "location": location,
    }


def _post_create(client, group_pid, **overrides):
    data = _create_data(**overrides)
    return client.post(
        f"/admin/groups/{group_pid}/schedules/new", data=data, follow_redirects=True
    )


def _get_edit_snapshot(client, group_pid, schedule_pid):
    html = client.get(
        f"/admin/groups/{group_pid}/schedules/{schedule_pid}/edit"
    ).get_data(as_text=True)
    m = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return m.group(1) if m else ""


def _post_edit(client, group_pid, schedule_pid, snapshot, **overrides):
    data = _create_data(**overrides)
    data["edit_snapshot"] = snapshot
    return client.post(
        f"/admin/groups/{group_pid}/schedules/{schedule_pid}/edit",
        data=data,
        follow_redirects=True,
    )


def _toggle(client, group_pid, schedule_pid):
    return client.post(
        f"/admin/groups/{group_pid}/schedules/{schedule_pid}/toggle-status", follow_redirects=True
    )


# ===========================================================================
# Create -- happy path + field/rule validation
# ===========================================================================


def test_create_valid_slot(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        gpid = group.public_id

    resp = _post_create(client, gpid)
    assert b"schedule slot added" in resp.data.lower()
    with app.app_context():
        rows = Schedule.query.all()
        assert len(rows) == 1
        assert rows[0].status == "active"
        assert rows[0].day_of_week == MONDAY
        assert rows[0].public_id is not None


def test_create_multiple_weekly_slots(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    assert b"added" in _post_create(client, gpid, day_of_week=MONDAY, start="09:00", end="10:30").data.lower()
    assert b"added" in _post_create(client, gpid, day_of_week=2, start="09:00", end="10:30").data.lower()
    assert b"added" in _post_create(client, gpid, day_of_week=MONDAY, start="11:00", end="12:00").data.lower()
    with app.app_context():
        assert Schedule.query.count() == 3


@pytest.mark.parametrize("bad_day", ["7", "-1", "abc"])
def test_create_rejects_invalid_weekday(app, client, bad_day):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    resp = _post_create(client, gpid, day_of_week=bad_day)
    with app.app_context():
        assert Schedule.query.count() == 0
    assert resp.status_code == 200


def test_create_rejects_end_before_start(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    resp = _post_create(client, gpid, start="11:00", end="10:00")
    assert b"after the start time" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.count() == 0


def test_create_rejects_effective_end_before_start(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    resp = _post_create(client, gpid, eff_start="2026-11-01", eff_end="2026-10-01")
    assert b"effective end date" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.count() == 0


def test_create_rejects_range_with_no_occurrence_of_weekday(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    # 2026-09-08 (Tue) .. 2026-09-13 (Sun) contains no Monday
    resp = _post_create(
        client, gpid, day_of_week=MONDAY, eff_start="2026-09-08", eff_end="2026-09-13"
    )
    assert b"contains no monday" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.count() == 0


def test_create_rejects_range_outside_term(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    resp = _post_create(client, gpid, eff_start="2026-08-01", eff_end="2026-12-28")
    assert b"within this group" in resp.data.lower() and b"academic term" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.count() == 0


def test_create_exact_duplicate_rejected(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        _schedule(group, day_of_week=MONDAY, start="09:00", end="10:30",
                  eff_start=date(2026, 9, 7), eff_end=date(2026, 12, 28))
        gpid = group.public_id

    resp = _post_create(
        client, gpid, day_of_week=MONDAY, start="09:00", end="10:30",
        eff_start="2026-09-07", eff_end="2026-12-28",
    )
    assert b"identical schedule slot already exists" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.count() == 1


def test_create_overlap_rejected_and_adjacent_allowed(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        _schedule(group, day_of_week=MONDAY, start="09:00", end="10:00")
        gpid = group.public_id

    overlap = _post_create(client, gpid, day_of_week=MONDAY, start="09:30", end="10:30")
    assert b"overlaps an existing active" in overlap.data.lower()

    adjacent = _post_create(client, gpid, day_of_week=MONDAY, start="10:00", end="11:00")
    assert b"added" in adjacent.data.lower()
    with app.app_context():
        assert Schedule.query.count() == 2


def test_create_non_overlapping_effective_periods_allowed(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        _schedule(group, day_of_week=MONDAY, start="09:00", end="10:30",
                  eff_start=date(2026, 9, 7), eff_end=date(2026, 9, 28))
        gpid = group.public_id

    resp = _post_create(
        client, gpid, day_of_week=MONDAY, start="09:00", end="10:30",
        eff_start="2026-10-05", eff_end="2026-12-28",
    )
    assert b"added" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.count() == 2


def test_create_archived_slot_does_not_block_new_overlapping_slot(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        _schedule(group, day_of_week=MONDAY, start="09:00", end="10:30",
                  status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id

    resp = _post_create(client, gpid, day_of_week=MONDAY, start="09:00", end="10:30")
    assert b"added" in resp.data.lower()


# ===========================================================================
# Lifecycle: group / ancestor active requirements
# ===========================================================================


def test_create_blocked_when_group_archived(app, client):
    with app.app_context():
        _admin(client)
        group = _group(status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id

    resp = _post_create(client, gpid)
    assert b"active" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.count() == 0


@pytest.mark.parametrize("archived", ["term", "level", "course"])
def test_create_blocked_when_ancestor_archived(app, client, archived):
    with app.app_context():
        _admin(client)
        term = _term()
        level = _level()
        course = _course(level)
        if archived == "term":
            term.status = AcademicStatus.ARCHIVED.value
        elif archived == "level":
            level.status = AcademicStatus.ARCHIVED.value
        else:
            course.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        group = _group(term=term, course=course)
        gpid = group.public_id

    resp = _post_create(client, gpid)
    with app.app_context():
        assert Schedule.query.count() == 0
    assert resp.status_code == 200


def test_edit_blocked_when_group_archived(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group)
        gpid, spid = group.public_id, row.public_id
    snap = _get_edit_snapshot(client, gpid, spid)
    with app.app_context():
        g = Group.query.first()
        g.status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    resp = _post_edit(client, gpid, spid, snap, location="Room 99")
    with app.app_context():
        assert Schedule.query.first().location is None


def test_archiving_schedule_allowed_even_when_group_archived(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group)
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        gpid, spid = group.public_id, row.public_id

    resp = _toggle(client, gpid, spid)
    assert b"archived" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.first().status == "archived"


def test_reactivating_schedule_blocked_when_group_archived(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group, status=AcademicStatus.ARCHIVED.value)
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        gpid, spid = group.public_id, row.public_id

    resp = _toggle(client, gpid, spid)
    assert b"reactivate the parent first" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.first().status == "archived"


def test_reactivation_blocked_by_current_overlap(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        archived_row = _schedule(group, day_of_week=MONDAY, start="09:00", end="10:30",
                                 status=AcademicStatus.ARCHIVED.value)
        _schedule(group, day_of_week=MONDAY, start="09:30", end="11:00")  # active, overlaps
        gpid, spid = group.public_id, archived_row.public_id

    resp = _toggle(client, gpid, spid)
    assert b"overlaps an existing active" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.filter_by(public_id=spid).first().status == "archived"


def test_reactivation_succeeds_when_no_conflict(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group, status=AcademicStatus.ARCHIVED.value)
        gpid, spid = group.public_id, row.public_id

    resp = _toggle(client, gpid, spid)
    assert b"reactivated" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.first().status == "active"


# ===========================================================================
# No cascade on Group archive / reactivation
# ===========================================================================


def test_group_archive_does_not_cascade_to_schedules(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        _schedule(group, day_of_week=MONDAY)
        _schedule(group, day_of_week=TUESDAY, status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id

    client.post(f"/admin/groups/{gpid}/toggle-status", follow_redirects=True)
    with app.app_context():
        assert Group.query.first().status == "archived"
        statuses = sorted(s.status for s in Schedule.query.all())
        assert statuses == ["active", "archived"]


def test_group_reactivation_does_not_cascade_to_schedules(app, client):
    with app.app_context():
        _admin(client)
        group = _group(status=AcademicStatus.ARCHIVED.value)
        _schedule(group, day_of_week=MONDAY)
        _schedule(group, day_of_week=TUESDAY, status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id

    client.post(f"/admin/groups/{gpid}/toggle-status", follow_redirects=True)
    with app.app_context():
        assert Group.query.first().status == "active"
        statuses = sorted(s.status for s in Schedule.query.all())
        assert statuses == ["active", "archived"]


# ===========================================================================
# Schedule history freezes Group academic identity
# ===========================================================================


def _edit_group(client, group_pid, term_id, course_id, name="Group A", capacity="20"):
    html = client.get(f"/admin/groups/{group_pid}/edit").get_data(as_text=True)
    snap = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    return client.post(
        f"/admin/groups/{group_pid}/edit",
        data={
            "academic_term_id": term_id,
            "course_id": course_id,
            "name": name,
            "code": "",
            "capacity": capacity,
            "edit_snapshot": snap,
        },
        follow_redirects=True,
    )


def test_schedule_history_freezes_group_identity(app, client):
    with app.app_context():
        _admin(client)
        term_a = _term("Term A")
        term_b = _term("Term B")
        level = _level()
        course_a = _course(level, "Course A")
        course_b = _course(level, "Course B")
        group = _group(term=term_a, course=course_a, name="G")
        _schedule(group, status=AcademicStatus.ARCHIVED.value)
        gpid, term_b_id, course_b_id = group.public_id, term_b.id, course_b.id

    resp = _edit_group(client, gpid, term_b_id, course_b_id, name="G")
    assert b"cannot be changed" in resp.data.lower()
    with app.app_context():
        g = Group.query.filter_by(public_id=gpid).first()
        assert g.academic_term_id != term_b_id


def test_schedule_history_allows_same_identity_and_metadata_edits(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        course = _course(_level())
        group = _group(term=term, course=course, name="G", capacity=20)
        _schedule(group)
        gpid, term_id, course_id = group.public_id, term.id, course.id

    resp = _edit_group(client, gpid, term_id, course_id, name="G renamed", capacity="15")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        g = Group.query.filter_by(public_id=gpid).first()
        assert g.name == "G renamed"
        assert g.capacity == 15


def test_schedule_only_history_group_identity_error_text_is_accurate(app, client):
    """A Schedule is the ONLY history row -- the rejection message must
    still be truthful: it names schedule history, not only membership."""
    with app.app_context():
        _admin(client)
        term_a = _term("Term A")
        term_b = _term("Term B")
        level = _level()
        course = _course(level, "Course A")
        group = _group(term=term_a, course=course, name="G")
        _schedule(group)  # the only history row of any kind
        gpid, term_b_id, course_id = group.public_id, term_b.id, course.id
        assert Enrollment.query.count() == 0
        assert GroupTeacherAssignment.query.count() == 0

    resp = _edit_group(client, gpid, term_b_id, course_id, name="G")
    body = resp.data.lower()
    assert b"schedule" in body and b"unit history" in body  # M10: "..., schedule, or unit history"
    assert b"cannot be changed" in body


def test_schedule_only_history_group_form_locked_notice_is_accurate(app, client):
    with app.app_context():
        _admin(client)
        group = _group(name="G")
        _schedule(group)
        gpid = group.public_id
        assert Enrollment.query.count() == 0
        assert GroupTeacherAssignment.query.count() == 0

    html = client.get(f"/admin/groups/{gpid}/edit").get_data(as_text=True).lower()
    assert "locked because this group already has enrollment" in html
    assert "schedule, or unit history" in html  # M10 extended the triad


# ===========================================================================
# AcademicTerm date-edit guard
# ===========================================================================


def _edit_term(client, term_pid, start, end, name="Fall 2026"):
    return client.post(
        f"/admin/academic-terms/{term_pid}/edit",
        data={"name": name, "start_date": start, "end_date": end},
        follow_redirects=True,
    )


def test_term_shrink_blocked_when_schedule_would_fall_outside(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        group = _group(term=term)
        _schedule(group, day_of_week=MONDAY, eff_start=date(2026, 9, 7), eff_end=date(2026, 12, 28))
        term_pid = term.public_id

    resp = _edit_term(client, term_pid, "2026-09-01", "2026-12-01")
    body = resp.data.lower()
    assert b"outside the term" in body
    assert b"effective range" in body
    with app.app_context():
        assert AcademicTerm.query.filter_by(public_id=term_pid).first().end_date == TERM_END


def test_term_date_rejection_does_not_recommend_archiving_and_explains_archived_history(app, client):
    """The rejection must not suggest archiving the schedule as a fix
    (archived rows still count), and must say so."""
    with app.app_context():
        _admin(client)
        term = _term()
        group = _group(term=term)
        _schedule(group, day_of_week=MONDAY, eff_start=date(2026, 9, 7), eff_end=date(2026, 12, 28))
        term_pid = term.public_id

    resp = _edit_term(client, term_pid, "2026-09-01", "2026-12-01")
    body = resp.data.lower()
    assert b"archived schedules still count" in body
    assert b"or archive that schedule" not in body
    assert b"archive that schedule first" not in body
    # the guidance points at adjusting the schedule's effective range / term dates
    assert b"adjust that schedule" in body


def test_term_shrink_blocked_by_archived_schedule_under_archived_group(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        group = _group(term=term)
        _schedule(group, day_of_week=MONDAY, eff_start=date(2026, 9, 7), eff_end=date(2026, 12, 28),
                  status=AcademicStatus.ARCHIVED.value)
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        term_pid = term.public_id

    resp = _edit_term(client, term_pid, "2026-09-01", "2026-12-01")
    body = resp.data.lower()
    assert b"outside the term" in body
    assert b"archived schedules still count" in body


def test_term_shrink_allowed_when_schedules_still_contained(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        group = _group(term=term)
        _schedule(group, day_of_week=MONDAY, eff_start=date(2026, 9, 7), eff_end=date(2026, 10, 26))
        term_pid = term.public_id

    resp = _edit_term(client, term_pid, "2026-09-01", "2026-11-30")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        assert AcademicTerm.query.filter_by(public_id=term_pid).first().end_date == date(2026, 11, 30)


def test_term_expansion_allowed_with_schedule_history(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        group = _group(term=term)
        _schedule(group, day_of_week=MONDAY, eff_start=date(2026, 9, 7), eff_end=date(2026, 12, 28))
        term_pid = term.public_id

    resp = _edit_term(client, term_pid, "2026-08-01", "2027-01-31")
    assert b"updated" in resp.data.lower()


def test_term_name_only_edit_allowed_despite_legacy_out_of_range_schedule(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        group = _group(term=term)
        # legacy row outside the term (inserted directly)
        _schedule(group, day_of_week=MONDAY, eff_start=date(2026, 9, 7), eff_end=date(2026, 12, 28))
        term.end_date = date(2026, 12, 1)  # simulate legacy inconsistency
        db.session.commit()
        term_pid = term.public_id

    resp = _edit_term(client, term_pid, "2026-09-01", "2026-12-01", name="Renamed Fall")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        assert AcademicTerm.query.filter_by(public_id=term_pid).first().name == "Renamed Fall"


# ===========================================================================
# Signed edit snapshot
# ===========================================================================


def test_edit_happy_path_and_status_preserved(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group, location="Old Room")
        gpid, spid = group.public_id, row.public_id

    snap = _get_edit_snapshot(client, gpid, spid)
    resp = _post_edit(client, gpid, spid, snap, location="New Room")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        r = Schedule.query.first()
        assert r.location == "New Room"
        assert r.status == "active"


def test_edit_missing_snapshot_rejected(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group)
        gpid, spid = group.public_id, row.public_id

    resp = client.post(
        f"/admin/groups/{gpid}/schedules/{spid}/edit",
        data=_create_data(location="Nope"),
        follow_redirects=True,
    )
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.first().location is None


def test_edit_tampered_snapshot_rejected(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group)
        gpid, spid = group.public_id, row.public_id

    snap = _get_edit_snapshot(client, gpid, spid)
    resp = _post_edit(client, gpid, spid, snap + "x", location="Nope")
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.first().location is None


def test_edit_wrong_object_snapshot_rejected(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row_a = _schedule(group, day_of_week=MONDAY)
        row_b = _schedule(group, day_of_week=TUESDAY)
        gpid, spid_a, spid_b = group.public_id, row_a.public_id, row_b.public_id

    snap_b = _get_edit_snapshot(client, gpid, spid_b)
    resp = _post_edit(client, gpid, spid_a, snap_b, day_of_week=MONDAY, location="Nope")
    assert b"changed since this form was opened" in resp.data.lower()


def test_edit_stale_snapshot_after_concurrent_change_rejected(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group, location="Original")
        gpid, spid = group.public_id, row.public_id

    snap = _get_edit_snapshot(client, gpid, spid)
    with app.app_context():
        r = Schedule.query.first()
        r.location = "Changed Elsewhere"
        db.session.commit()

    resp = _post_edit(client, gpid, spid, snap, location="My Edit")
    assert b"changed since this form was opened" in resp.data.lower()
    with app.app_context():
        assert Schedule.query.first().location == "Changed Elsewhere"


def test_edit_double_submit_second_is_stale(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group, location="Start")
        gpid, spid = group.public_id, row.public_id

    snap = _get_edit_snapshot(client, gpid, spid)
    first = _post_edit(client, gpid, spid, snap, location="First")
    assert b"updated" in first.data.lower()
    second = _post_edit(client, gpid, spid, snap, location="Second")
    assert b"changed since this form was opened" in second.data.lower()
    with app.app_context():
        assert Schedule.query.first().location == "First"


def test_edit_status_toggle_not_overwritten_by_edit(app, client):
    """An edit form opened while the slot was active, then submitted after
    it was archived, must not silently reactivate it -- status is never
    written by edit, and the archived slot also requires an active chain
    which it still has, but the row stays archived."""
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group, location="Before")
        gpid, spid = group.public_id, row.public_id

    snap = _get_edit_snapshot(client, gpid, spid)
    with app.app_context():
        Schedule.query.first().status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    resp = _post_edit(client, gpid, spid, snap, location="After")
    # status is not a snapshot field, so this is not stale; the edit
    # applies but never touches status
    assert b"updated" in resp.data.lower()
    with app.app_context():
        r = Schedule.query.first()
        assert r.status == "archived"
        assert r.location == "After"


# ===========================================================================
# Authorization / CSRF / IDOR / forged input
# ===========================================================================


def test_all_schedule_endpoints_require_admin(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
        group = _group()
        row = _schedule(group)
        gpid, spid = group.public_id, row.public_id
    login(client, "teacher@example.com")

    assert client.get("/admin/schedules").status_code == 403
    assert client.get(f"/admin/groups/{gpid}/schedules").status_code == 403
    assert client.get(f"/admin/groups/{gpid}/schedules/new").status_code == 403
    assert client.get(f"/admin/groups/{gpid}/schedules/{spid}/edit").status_code == 403
    assert client.post(f"/admin/groups/{gpid}/schedules/{spid}/toggle-status").status_code == 403


def test_schedule_endpoints_redirect_anonymous(app, client):
    with app.app_context():
        group = _group()
        row = _schedule(group)
        gpid, spid = group.public_id, row.public_id

    for url in (
        "/admin/schedules",
        f"/admin/groups/{gpid}/schedules",
        f"/admin/groups/{gpid}/schedules/new",
        f"/admin/groups/{gpid}/schedules/{spid}/edit",
    ):
        resp = client.get(url)
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_toggle_is_post_only(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group)
        gpid, spid = group.public_id, row.public_id

    assert client.get(f"/admin/groups/{gpid}/schedules/{spid}/toggle-status").status_code == 405


def test_csrf_enforced_on_create_and_toggle():
    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    with app.app_context():
        db.create_all()
        try:
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
            group = _group()
            row = _schedule(group)
            gpid, spid = group.public_id, row.public_id

            login_page = client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"', login_page.get_data(as_text=True)
            ).group(1)
            client.post(
                "/auth/login",
                data={"email": "admin@example.com", "password": "Sup3rSecret!123", "csrf_token": token},
            )

            assert client.post(
                f"/admin/groups/{gpid}/schedules/new", data=_create_data()
            ).status_code == 400
            assert client.post(
                f"/admin/groups/{gpid}/schedules/{spid}/toggle-status"
            ).status_code == 400
            assert Schedule.query.count() == 1
            assert Schedule.query.first().status == "active"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_nested_schedule_lookup_scoped_to_group_idor(app, client):
    with app.app_context():
        _admin(client)
        term = _term()
        course = _course(_level())
        group_a = _group(term=term, course=course, name="A")
        group_b = _group(term=term, course=course, name="B")
        row_a = _schedule(group_a)
        gpid_b, spid_a = group_b.public_id, row_a.public_id

    assert client.get(f"/admin/groups/{gpid_b}/schedules/{spid_a}/edit").status_code == 404
    assert client.post(
        f"/admin/groups/{gpid_b}/schedules/{spid_a}/toggle-status"
    ).status_code == 404


def test_invalid_uuids_return_clean_404(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    assert client.get("/admin/groups/not-a-uuid/schedules").status_code == 404
    assert client.get(f"/admin/groups/{gpid}/schedules/not-a-uuid/edit").status_code == 404
    assert client.post(
        f"/admin/groups/{gpid}/schedules/00000000-0000-0000-0000-000000000000/toggle-status"
    ).status_code == 404


def test_forged_status_field_ignored_on_create(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    data = _create_data()
    data["status"] = "archived"
    client.post(f"/admin/groups/{gpid}/schedules/new", data=data, follow_redirects=True)
    with app.app_context():
        assert Schedule.query.first().status == "active"


def test_forged_status_field_ignored_on_edit(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group)
        gpid, spid = group.public_id, row.public_id

    snap = _get_edit_snapshot(client, gpid, spid)
    data = _create_data(location="Room X")
    data["edit_snapshot"] = snap
    data["status"] = "archived"
    client.post(f"/admin/groups/{gpid}/schedules/{spid}/edit", data=data, follow_redirects=True)
    with app.app_context():
        assert Schedule.query.first().status == "active"


# ===========================================================================
# Overview page
# ===========================================================================


def test_overview_empty_state(app, client):
    with app.app_context():
        _admin(client)

    resp = client.get("/admin/schedules")
    assert resp.status_code == 200
    assert b"no schedules yet" in resp.data.lower()


def test_overview_lists_and_filters(app, client):
    with app.app_context():
        _admin(client)
        term_a = _term("Term A")
        term_b = _term("Term B")
        level = _level()
        course = _course(level)
        g1 = _group(term=term_a, course=course, name="Alpha")
        g2 = _group(term=term_b, course=course, name="Beta")
        _schedule(g1, day_of_week=MONDAY, location="North Hall")
        _schedule(g2, day_of_week=TUESDAY, location="South Hall")
        _schedule(g2, day_of_week=MONDAY, status=AcademicStatus.ARCHIVED.value)
        term_a_id, g2_id = term_a.id, g2.id

    all_resp = client.get("/admin/schedules").get_data(as_text=True)
    assert "North Hall" in all_resp and "South Hall" in all_resp

    by_term = client.get(f"/admin/schedules?term_id={term_a_id}").get_data(as_text=True)
    assert "North Hall" in by_term and "South Hall" not in by_term

    by_group = client.get(f"/admin/schedules?group_id={g2_id}").get_data(as_text=True)
    assert "South Hall" in by_group and "North Hall" not in by_group

    by_day = client.get("/admin/schedules?day_of_week=1").get_data(as_text=True)
    assert "South Hall" in by_day and "North Hall" not in by_day

    by_status = client.get("/admin/schedules?status=archived").get_data(as_text=True)
    assert "North Hall" not in by_status and "South Hall" not in by_status

    by_loc = client.get("/admin/schedules?q=North").get_data(as_text=True)
    assert "North Hall" in by_loc and "South Hall" not in by_loc

    no_match = client.get("/admin/schedules?q=Nonexistent").get_data(as_text=True)
    assert "no schedules match your filters" in no_match.lower()


def test_overview_shows_timezone_and_navigation(app, client):
    with app.app_context():
        _admin(client)

    resp = client.get("/admin/schedules").get_data(as_text=True)
    assert "timezone" in resp.lower()
    # Schedules link present in admin navigation
    assert "/admin/schedules" in resp


def test_group_detail_links_to_manage_schedule(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    resp = client.get(f"/admin/groups/{gpid}").get_data(as_text=True)
    assert f"/admin/groups/{gpid}/schedules" in resp


def test_overview_bounded_query_no_n_plus_one(app, client):
    """One page render must issue a bounded number of SQL statements
    regardless of how many schedules exist (joined eager loading)."""
    from sqlalchemy import event

    with app.app_context():
        _admin(client)
        term = _term()
        for i in range(6):
            course = _course(_level(f"L{i}"), f"C{i}")
            group = _group(term=term, course=course, name=f"G{i}")
            _schedule(group, day_of_week=i % 5, location=f"Loc{i}")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        client.get("/admin/schedules")
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)

    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(selects) < 15, len(selects)


# ===========================================================================
# Structural: lock order + single reset + safe rollback
# ===========================================================================


def _capture_lock_events(fn):
    from unittest.mock import patch

    from sqlalchemy.orm import Query

    events = []
    original_rollback = db.session.rollback
    original_with_for_update = Query.with_for_update

    def rollback_spy(*a, **k):
        events.append("reset")
        return original_rollback(*a, **k)

    def lock_spy(self, *a, **k):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_with_for_update(self, *a, **k)

    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", lock_spy
    ):
        fn()
    return events


def test_create_locks_hierarchy_then_group_then_no_schedule_lock(app, client):
    with app.app_context():
        _admin(client)
        gpid = _group().public_id

    events = _capture_lock_events(lambda: _post_create(client, gpid))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm",
        "lock:Level",
        "lock:Course",
        "lock:Group",
    ]
    assert events[0] == "reset"
    assert events[1] == "lock:AcademicTerm"


def test_edit_locks_hierarchy_group_then_schedule_in_order(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group, location="A")
        gpid, spid = group.public_id, row.public_id

    snap = _get_edit_snapshot(client, gpid, spid)
    events = _capture_lock_events(lambda: _post_edit(client, gpid, spid, snap, location="B"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm",
        "lock:Level",
        "lock:Course",
        "lock:Group",
        "lock:Schedule",
    ]


def test_toggle_locks_full_chain_in_order(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        row = _schedule(group)
        gpid, spid = group.public_id, row.public_id

    events = _capture_lock_events(lambda: _toggle(client, gpid, spid))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm",
        "lock:Level",
        "lock:Course",
        "lock:Group",
        "lock:Schedule",
    ]


def test_failed_create_rule_check_rolls_back_and_writes_nothing(app, client):
    with app.app_context():
        _admin(client)
        group = _group()
        _schedule(group, day_of_week=MONDAY, start="09:00", end="10:30")
        gpid = group.public_id

    _post_create(client, gpid, day_of_week=MONDAY, start="09:30", end="10:30")
    with app.app_context():
        assert Schedule.query.count() == 1
