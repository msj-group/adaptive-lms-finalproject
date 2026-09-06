"""Teacher Assignment management (Phase 4 / M01): nested authorization,
draft/published lifecycle, publication timestamps, the local/UTC form
boundary, bounded pagination, the signed stale-edit snapshot, the
canonical lock order and single reset, post-lock rechecks, CSRF /
POST-only / PRG behaviour, and non-disclosure 404s.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the structural lock tests here
prove only the *requested* reset/lock order -- never that a real InnoDB
lock blocks a concurrent transaction.
"""

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
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

PW = "Sup3rSecret!123"

#: Form input is always a LOCAL wall clock in APP_TIMEZONE; the database
#: always holds naive UTC. This project's configured timezone is not UTC,
#: so the two genuinely differ -- the expectations below are derived
#: through the same shared helper the form uses rather than hard-coded,
#: so these tests stay correct under any configured timezone. The
#: conversion itself is covered in tests/test_assignment_timezone.py.
OPENS_LOCAL = "2026-05-01T08:00"
DUE_LOCAL = "2026-05-08T23:59"


def _utc(app, local_text):
    """The naive-UTC instant a submitted local wall-clock string means."""
    from app.services.schedule_occurrences import from_app_local

    return from_app_local(
        app.config["APP_TIMEZONE"], datetime.strptime(local_text, "%Y-%m-%dT%H:%M")
    )


def _local_text(app, utc_moment):
    """The local wall-clock string a stored UTC instant renders as.

    Seconds included: the form renders ``%Y-%m-%dT%H:%M:%S`` so an edit
    round trip cannot truncate a stored second-bearing value.
    """
    from app.services.schedule_occurrences import to_app_local

    return to_app_local(app.config["APP_TIMEZONE"], utc_moment).strftime("%Y-%m-%dT%H:%M:%S")


#: Alias kept for readability where a test is specifically about seconds.
_local_seconds_text = _local_text


def _utc_seconds(app, local_text):
    """`_utc` for a second-bearing local wall-clock string."""
    from app.services.schedule_occurrences import from_app_local

    return from_app_local(
        app.config["APP_TIMEZONE"], datetime.strptime(local_text, "%Y-%m-%dT%H:%M:%S")
    )


#: Naive-UTC fixtures for rows written straight to the database.
OPENS_UTC = datetime(2026, 5, 1, 6, 0)
DUE_UTC = datetime(2026, 5, 8, 21, 59)


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


def _assignment(group, title="Task 1", opens_at=OPENS_UTC, due_at=DUE_UTC,
                status=AssignmentStatus.DRAFT.value, instructions="Do the work."):
    published_at = datetime.now(timezone.utc) if status == AssignmentStatus.PUBLISHED.value else None
    row = Assignment(group_id=group.id, title=title, instructions=instructions,
                     opens_at=opens_at, due_at=due_at, status=status,
                     published_at=published_at)
    db.session.add(row)
    db.session.commit()
    return row


def _setup(email="teacher@example.com", **hkw):
    """(teacher, group) with an active assignment."""
    teacher = _user(email, UserRole.TEACHER.value)
    group = _hierarchy(**hkw)
    _assign(group, teacher)
    return teacher, group


def _url(gpid):
    return f"/teacher/groups/{gpid}/assignments"


def _create(client, gpid, title="Task A", instructions="Body", opens=OPENS_LOCAL,
            due=DUE_LOCAL, follow=True, **extra):
    data = {"title": title, "instructions": instructions, "opens_at": opens, "due_at": due}
    data.update(extra)
    return client.post(f"{_url(gpid)}/new", data=data, follow_redirects=follow)


def _get_edit_snapshot(client, gpid, apid):
    html = client.get(f"{_url(gpid)}/{apid}/edit").get_data(as_text=True)
    match = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _edit(client, gpid, apid, snapshot, title="Renamed", instructions="Body",
          opens=OPENS_LOCAL, due=DUE_LOCAL, follow=True):
    return client.post(
        f"{_url(gpid)}/{apid}/edit",
        data={"title": title, "instructions": instructions, "opens_at": opens,
              "due_at": due, "edit_snapshot": snapshot},
        follow_redirects=follow,
    )


def _toggle(client, gpid, apid, follow=True):
    return client.post(f"{_url(gpid)}/{apid}/toggle-publication", follow_redirects=follow)


# ===========================================================================
# Authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    resp = client.get(_url(gpid), follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value]
)
def test_non_teacher_roles_get_403(app, client, role):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
        _user(f"{role}@example.com", role)
    login(client, f"{role}@example.com")
    assert client.get(_url(gpid)).status_code == 403


def test_unassigned_teacher_gets_404(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
        _user("other@example.com", UserRole.TEACHER.value)
    login(client, "other@example.com")
    assert client.get(_url(gpid)).status_code == 404


def test_removed_assignment_gets_404(app, client):
    with app.app_context():
        teacher, group = _setup()
        row = GroupTeacherAssignment.query.first()
        row.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()
        gpid = group.public_id
    login(client, "teacher@example.com")
    assert client.get(_url(gpid)).status_code == 404


def test_suspended_teacher_cannot_obtain_a_session(app, client):
    """A suspended account cannot log in at all, so it never reaches the
    Assignment routes. (Suspending an *already* authenticated session is
    covered by the auth layer's ``bump_auth_version`` and cannot be
    exercised faithfully here: the test backend's StaticPool makes every
    request reuse the fixture's session, so a row mutated from a nested
    app context is not re-read by the user loader.)"""
    with app.app_context():
        group = _hierarchy()
        teacher = _user("suspended@example.com", UserRole.TEACHER.value,
                        status=UserStatus.SUSPENDED.value)
        _assign(group, teacher)
        gpid = group.public_id
    login(client, "suspended@example.com")
    resp = client.get(_url(gpid), follow_redirects=False)
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_missing_group_and_missing_assignment_404_identically(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")
    missing_group = client.get(_url("does-not-exist"))
    missing_assignment = client.get(f"{_url(gpid)}/does-not-exist/edit")
    assert missing_group.status_code == missing_assignment.status_code == 404


def test_cross_group_assignment_public_id_404s(app, client):
    with app.app_context():
        teacher, first = _setup()
        second = _hierarchy(group_name="Group B", course_title="Other")
        _assign(second, teacher)
        foreign = _assignment(second, title="Elsewhere")
        gpid, apid = first.public_id, foreign.public_id
    login(client, "teacher@example.com")
    assert client.get(f"{_url(gpid)}/{apid}/edit").status_code == 404
    assert client.post(f"{_url(gpid)}/{apid}/toggle-publication").status_code == 404


def test_co_teachers_have_equal_rights(app, client):
    with app.app_context():
        _, group = _setup("first@example.com")
        second = _user("second@example.com", UserRole.TEACHER.value)
        _assign(group, second)
        gpid = group.public_id
    login(client, "first@example.com")
    _create(client, gpid, title="By first")
    client.get("/auth/logout")
    login(client, "second@example.com")
    with app.app_context():
        apid = Assignment.query.first().public_id
    snapshot = _get_edit_snapshot(client, gpid, apid)
    resp = _edit(client, gpid, apid, snapshot, title="By second")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        assert Assignment.query.first().title == "By second"


def test_list_stays_available_under_an_archived_chain(app, client):
    with app.app_context():
        _, group = _setup(group_status=AcademicStatus.ARCHIVED.value)
        _assignment(group, title="History")
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = client.get(_url(gpid))
    assert resp.status_code == 200
    assert b"History" in resp.data


def test_no_internal_ids_in_urls_or_html(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Task")
        gpid, gid, aid = group.public_id, group.id, row.id
    login(client, "teacher@example.com")
    html = client.get(_url(gpid)).get_data(as_text=True)
    for leaked in (f"/groups/{gid}/", f"/assignments/{aid}/", f'value="{aid}"'):
        assert leaked not in html


# ===========================================================================
# List + pagination
# ===========================================================================


def test_list_shows_drafts_and_published_newest_deadline_first(app, client):
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="Earliest", due_at=datetime(2026, 5, 2, 12, 0))
        _assignment(group, title="Latest", due_at=datetime(2026, 6, 2, 12, 0),
                    status=AssignmentStatus.PUBLISHED.value)
        gpid = group.public_id
    login(client, "teacher@example.com")
    html = client.get(_url(gpid)).get_data(as_text=True)
    assert html.index("Latest") < html.index("Earliest")
    assert "Draft" in html and "Published" in html


def test_list_is_paginated_with_a_fixed_bound(app, client):
    from app.services.assignment_queries import PAGE_SIZE

    with app.app_context():
        _, group = _setup()
        for i in range(PAGE_SIZE + 3):
            _assignment(group, title=f"Task {i:02d}",
                        due_at=datetime(2026, 5, 1, 9, 0) + timedelta(days=i + 1))
        gpid = group.public_id
    login(client, "teacher@example.com")
    first = client.get(_url(gpid)).get_data(as_text=True)
    assert first.count("<tr>") == PAGE_SIZE + 1  # + the header row
    assert "Next" in first
    second = client.get(f"{_url(gpid)}?page=2").get_data(as_text=True)
    assert second.count("<tr>") == 3 + 1
    assert "Previous" in second


@pytest.mark.parametrize("value", ["0", "-4", "abc", "", "999999999", "1e9"])
def test_invalid_page_values_normalize_to_page_one(app, client, value):
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="Only")
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = client.get(f"{_url(gpid)}?page={value}")
    assert resp.status_code == 200
    assert b"Only" in resp.data


def test_page_past_the_end_falls_back_to_page_one(app, client):
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="Only")
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = client.get(f"{_url(gpid)}?page=9")
    assert resp.status_code == 200
    assert b"Only" in resp.data
    assert b"Previous" not in resp.data


# ===========================================================================
# Create
# ===========================================================================


def test_create_starts_as_a_draft_with_utc_times(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, title="New Task")
    assert b"created as a draft" in resp.data.lower()
    with app.app_context():
        row = Assignment.query.first()
        assert row.status == AssignmentStatus.DRAFT.value
        assert row.published_at is None
        assert row.opens_at == _utc(app, OPENS_LOCAL)
        assert row.due_at == _utc(app, DUE_LOCAL)
        # The stored values are naive UTC, not the local wall clock typed in.
        assert row.opens_at.tzinfo is None and row.due_at.tzinfo is None


def test_forged_status_and_published_at_cannot_publish(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")
    _create(client, gpid, title="Injected", status="published",
            published_at="2026-01-01T00:00", display_order="0")
    with app.app_context():
        row = Assignment.query.filter_by(title="Injected").first()
        assert row.status == AssignmentStatus.DRAFT.value
        assert row.published_at is None


def test_create_requires_every_field(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")
    for missing in ("title", "instructions", "opens_at", "due_at"):
        data = {"title": "T", "instructions": "I", "opens_at": OPENS_LOCAL, "due_at": DUE_LOCAL}
        data[missing] = ""
        resp = client.post(f"{_url(gpid)}/new", data=data, follow_redirects=True)
        assert b"this field is required" in resp.data.lower(), missing
    with app.app_context():
        assert Assignment.query.count() == 0


@pytest.mark.parametrize(
    "opens, due",
    [(DUE_LOCAL, OPENS_LOCAL), (OPENS_LOCAL, OPENS_LOCAL)],
)
def test_due_must_be_after_opens(app, client, opens, due):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, opens=opens, due=due)
    assert b"must be after the opening time" in resp.data
    with app.app_context():
        assert Assignment.query.count() == 0


def test_duplicate_title_is_rejected_with_a_friendly_message(app, client):
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="Taken")
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, title="Taken")
    assert b"already exists in this group" in resp.data
    with app.app_context():
        assert Assignment.query.count() == 1


def test_racing_duplicate_title_produces_a_safe_generic_error(app, client):
    """The pre-lock uniqueness check passes, then a concurrent write
    takes the title. The DB constraint is the final defense and its
    IntegrityError must surface as generic prose."""
    import app.blueprints.teacher.assignments as mod
    from unittest.mock import patch

    with app.app_context():
        _, group = _setup()
        gpid, gid = group.public_id, group.id
    login(client, "teacher@example.com")

    original = mod.lock_group_in_open_transaction

    def take_title_then_lock(pid):
        locked = original(pid)
        db.session.add(Assignment(
            group_id=gid, title="Race", instructions="I",
            opens_at=OPENS_UTC, due_at=DUE_UTC, status=AssignmentStatus.DRAFT.value,
        ))
        db.session.commit()
        return original(pid)

    with patch.object(mod, "lock_group_in_open_transaction", side_effect=take_title_then_lock):
        resp = _create(client, gpid, title="Race")
    assert resp.status_code == 200
    body = resp.data.lower()
    assert b"could not be saved" in body
    for leaked in (b"integrityerror", b"unique constraint", b"sqlite", b"select ", b"insert into"):
        assert leaked not in body, leaked
    with app.app_context():
        assert Assignment.query.filter_by(title="Race").count() == 1


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_create_blocked_when_hierarchy_not_operational(app, client, archived):
    with app.app_context():
        _, group = _setup(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        gpid = group.public_id
    login(client, "teacher@example.com")
    resp = _create(client, gpid, title="Nope")
    assert resp.status_code == 200
    with app.app_context():
        assert Assignment.query.count() == 0


# ===========================================================================
# Edit
# ===========================================================================


def test_edit_updates_only_editable_fields(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Old", status=AssignmentStatus.PUBLISHED.value)
        gpid, apid = group.public_id, row.public_id
        original_published_at = row.published_at
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    resp = _edit(client, gpid, apid, snapshot, title="New", instructions="Rewritten",
                 opens=OPENS_LOCAL, due="2026-06-01T10:00")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        row = Assignment.query.first()
        assert row.title == "New" and row.instructions == "Rewritten"
        assert row.due_at == _utc(app, "2026-06-01T10:00")
        # Publication state is untouched.
        assert row.status == AssignmentStatus.PUBLISHED.value
        assert row.published_at == original_published_at


def test_edit_form_renders_stored_times_as_local_wall_clock(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    html = client.get(f"{_url(gpid)}/{apid}/edit").get_data(as_text=True)
    with app.app_context():
        assert f'value="{_local_text(app, OPENS_UTC)}"' in html
        assert f'value="{_local_text(app, DUE_UTC)}"' in html


def test_edit_form_renders_stored_seconds_exactly(app, client):
    """WTForms renders with the FIRST accepted format. A minute-only
    render would silently truncate a stored second-bearing value."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(
            group, title="T",
            opens_at=OPENS_UTC.replace(second=37),
            due_at=DUE_UTC.replace(second=59),
        )
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    html = client.get(f"{_url(gpid)}/{apid}/edit").get_data(as_text=True)
    with app.app_context():
        opens_local = _local_seconds_text(app, OPENS_UTC.replace(second=37))
        due_local = _local_seconds_text(app, DUE_UTC.replace(second=59))
    assert opens_local.endswith(":37") and due_local.endswith(":59")
    assert f'value="{opens_local}"' in html
    assert f'value="{due_local}"' in html


def test_both_datetime_controls_render_with_step_one(app, client):
    """``step="1"`` is what makes a second-bearing value a valid control
    value rather than one the browser rounds away."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    for url in (f"{_url(gpid)}/new", f"{_url(gpid)}/{apid}/edit"):
        html = client.get(url).get_data(as_text=True)
        for field in ("opens_at", "due_at"):
            control = re.search(rf'<input[^>]*name="{field}"[^>]*>', html)
            assert control is not None, (url, field)
            assert 'step="1"' in control.group(0), (url, field)
            assert 'type="datetime-local"' in control.group(0), (url, field)


def test_editing_only_the_title_preserves_the_stored_seconds(app, client):
    """The real failure mode: a Teacher fixes a typo and the deadline
    silently moves back by up to 59 seconds."""
    with app.app_context():
        _, group = _setup()
        opens_at = OPENS_UTC.replace(second=37)
        due_at = DUE_UTC.replace(second=59)
        row = _assignment(group, title="Old", opens_at=opens_at, due_at=due_at)
        gpid, apid = group.public_id, row.public_id

    login(client, "teacher@example.com")
    html = client.get(f"{_url(gpid)}/{apid}/edit").get_data(as_text=True)
    snapshot = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
    # Resubmit exactly what the form rendered, changing only the title --
    # what a browser sends when the Teacher touches nothing else.
    submitted = {
        field: re.search(rf'<input[^>]*name="{field}"[^>]*value="([^"]*)"', html).group(1)
        for field in ("opens_at", "due_at")
    }
    resp = _edit(client, gpid, apid, snapshot, title="New",
                 instructions="Do the work.",
                 opens=submitted["opens_at"], due=submitted["due_at"])
    assert b"updated" in resp.data.lower()
    with app.app_context():
        row = Assignment.query.first()
        assert row.title == "New"
        assert row.opens_at == opens_at
        assert row.due_at == due_at


def test_minute_only_input_is_still_accepted(app, client):
    """A browser that omits seconds must keep working, on create and on
    edit."""
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")

    assert b"created as a draft" in _create(client, gpid, title="Minutes only").data.lower()
    with app.app_context():
        row = Assignment.query.filter_by(title="Minutes only").first()
        assert row.opens_at == _utc(app, OPENS_LOCAL)
        assert row.opens_at.second == 0
        apid = row.public_id

    snapshot = _get_edit_snapshot(client, gpid, apid)
    resp = _edit(client, gpid, apid, snapshot, title="Still minutes",
                 instructions="Body", opens=OPENS_LOCAL, due=DUE_LOCAL)
    assert b"updated" in resp.data.lower()
    with app.app_context():
        row = Assignment.query.filter_by(title="Still minutes").first()
        assert row.opens_at == _utc(app, OPENS_LOCAL)
        assert row.due_at == _utc(app, DUE_LOCAL)


def test_second_bearing_input_still_converts_and_is_ordered(app, client):
    """Seconds survive the local -> UTC conversion, and ``opens_at <
    due_at`` is still enforced at second precision."""
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")

    resp = _create(client, gpid, title="Seconds",
                   opens="2026-05-01T08:00:37", due="2026-05-08T23:59:59")
    assert b"created as a draft" in resp.data.lower()
    with app.app_context():
        row = Assignment.query.filter_by(title="Seconds").first()
        assert row.opens_at == _utc_seconds(app, "2026-05-01T08:00:37")
        assert row.due_at == _utc_seconds(app, "2026-05-08T23:59:59")

    # One second apart is valid; the same instant is not.
    assert b"created as a draft" in _create(
        client, gpid, title="One second",
        opens="2026-06-01T08:00:00", due="2026-06-01T08:00:01",
    ).data.lower()
    resp = _create(client, gpid, title="Same instant",
                   opens="2026-07-01T08:00:37", due="2026-07-01T08:00:37")
    assert b"must be after the opening time" in resp.data
    with app.app_context():
        assert Assignment.query.filter_by(title="Same instant").first() is None


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_edit_blocked_when_hierarchy_not_operational(app, client, archived):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Kept")
        gpid, apid, gid = group.public_id, row.public_id, group.id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    with app.app_context():
        group = db.session.get(Group, gid)
        target = {
            "term": group.academic_term, "level": group.course.level,
            "course": group.course, "group": group,
        }[archived]
        target.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    resp = _edit(client, gpid, apid, snapshot, title="Renamed")
    assert resp.status_code == 200
    with app.app_context():
        assert Assignment.query.first().title == "Kept"


def test_edit_never_writes_lifecycle_fields_even_when_forged(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Draft One")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    client.post(
        f"{_url(gpid)}/{apid}/edit",
        data={"title": "Draft One", "instructions": "Body", "opens_at": OPENS_LOCAL,
              "due_at": DUE_LOCAL, "edit_snapshot": snapshot,
              "status": "published", "published_at": "2026-01-01T00:00"},
        follow_redirects=True,
    )
    with app.app_context():
        row = Assignment.query.first()
        assert row.status == AssignmentStatus.DRAFT.value
        assert row.published_at is None


# ===========================================================================
# Stale-edit snapshot
# ===========================================================================


def _stale_case(app, client, mutate):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Original", instructions="Original body")
        gpid, apid, aid = group.public_id, row.public_id, row.id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    with app.app_context():
        mutate(db.session.get(Assignment, aid))
        db.session.commit()
    resp = _edit(client, gpid, apid, snapshot, title="Attempted",
                 instructions="Attempted body", follow=False)
    return resp, gpid, apid, aid


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda a: setattr(a, "title", "Changed"), id="title"),
        pytest.param(lambda a: setattr(a, "instructions", "Changed body"), id="instructions"),
        pytest.param(lambda a: setattr(a, "opens_at", datetime(2026, 4, 1, 8, 0)), id="opens_at"),
        pytest.param(lambda a: setattr(a, "due_at", datetime(2026, 7, 1, 8, 0)), id="due_at"),
    ],
)
def test_changed_editable_field_makes_the_form_stale(app, client, mutate):
    resp, gpid, apid, _ = _stale_case(app, client, mutate)
    assert resp.status_code == 302
    follow = client.get(resp.headers["Location"])
    assert b"Attempted" not in follow.data
    with app.app_context():
        assert Assignment.query.first().title != "Attempted"


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "abc.def.ghi"],
    ids=["missing", "malformed", "invalid-signature"],
)
def test_missing_or_malformed_token_is_stale(app, client, token):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Kept")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    resp = _edit(client, gpid, apid, token, title="Attempted", follow=False)
    assert resp.status_code == 302
    follow = client.get(resp.headers["Location"])
    assert b"Attempted" not in follow.data and b"Kept" in follow.data


def test_wrong_shaped_token_is_stale(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Kept")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    with app.app_context():
        import app.blueprints.teacher.assignments as mod
        token = mod._assignment_snapshot_serializer().dumps({"public_id": apid})
    resp = _edit(client, gpid, apid, token, title="Attempted", follow=False)
    assert resp.status_code == 302
    with app.app_context():
        assert Assignment.query.first().title == "Kept"


def test_token_bound_to_another_assignment_is_stale(app, client):
    with app.app_context():
        _, group = _setup()
        first = _assignment(group, title="First")
        second = _assignment(group, title="Second")
        gpid, first_pid, second_pid = group.public_id, first.public_id, second.public_id
    login(client, "teacher@example.com")
    foreign_token = _get_edit_snapshot(client, gpid, second_pid)
    resp = _edit(client, gpid, first_pid, foreign_token, title="Attempted", follow=False)
    assert resp.status_code == 302
    with app.app_context():
        assert Assignment.query.filter_by(public_id=first_pid).first().title == "First"


def test_publication_change_alone_does_not_stale_the_form(app, client):
    """``status`` and ``published_at`` are excluded from the snapshot, so
    a co-teacher publishing while this form was open must not discard the
    Teacher's work -- the edit route cannot overwrite either field."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Original")
        gpid, apid, aid = group.public_id, row.public_id, row.id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    with app.app_context():
        published = db.session.get(Assignment, aid)
        published.status = AssignmentStatus.PUBLISHED.value
        published.published_at = datetime(2026, 4, 1, 9, 0)
        db.session.commit()
    resp = _edit(client, gpid, apid, snapshot, title="Renamed")
    assert b"updated" in resp.data.lower()
    with app.app_context():
        row = Assignment.query.first()
        assert row.title == "Renamed"
        assert row.status == AssignmentStatus.PUBLISHED.value
        assert row.published_at == datetime(2026, 4, 1, 9, 0)


def test_ordinary_validation_failure_preserves_the_original_token(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Original")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    resp = _edit(client, gpid, apid, snapshot, title="")  # fails DataRequired
    assert resp.status_code == 200
    match = re.search(r'name="edit_snapshot" value="([^"]*)"', resp.get_data(as_text=True))
    assert match and match.group(1) == snapshot


def test_stale_rejection_uses_prg_and_discards_attempted_values(app, client):
    resp, gpid, apid, _ = _stale_case(app, client, lambda a: setattr(a, "title", "Changed"))
    assert resp.status_code == 302
    follow = client.get(resp.headers["Location"])
    assert b"Attempted body" not in follow.data
    assert b"Changed" in follow.data


# ===========================================================================
# Publication toggle
# ===========================================================================


def test_publish_stamps_utc_and_unpublish_clears_it(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")

    assert b"published" in _toggle(client, gpid, apid).data.lower()
    with app.app_context():
        row = Assignment.query.first()
        assert row.status == AssignmentStatus.PUBLISHED.value
        assert row.published_at is not None
        first_stamp = row.published_at

    assert b"unpublished" in _toggle(client, gpid, apid).data.lower()
    with app.app_context():
        row = Assignment.query.first()
        assert row.status == AssignmentStatus.DRAFT.value
        assert row.published_at is None

    _toggle(client, gpid, apid)  # republish
    with app.app_context():
        row = Assignment.query.first()
        assert row.status == AssignmentStatus.PUBLISHED.value
        assert row.published_at is not None and row.published_at >= first_stamp


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_publish_blocked_by_inactive_hierarchy(app, client, archived):
    with app.app_context():
        _, group = _setup(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        row = _assignment(group, title="T")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    _toggle(client, gpid, apid)
    with app.app_context():
        assert Assignment.query.first().status == AssignmentStatus.DRAFT.value


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_unpublish_allowed_under_an_archived_hierarchy(app, client, archived):
    with app.app_context():
        _, group = _setup(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        row = _assignment(group, title="T", status=AssignmentStatus.PUBLISHED.value)
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    resp = _toggle(client, gpid, apid)
    assert b"unpublished" in resp.data.lower()
    with app.app_context():
        row = Assignment.query.first()
        assert row.status == AssignmentStatus.DRAFT.value and row.published_at is None


def test_archiving_the_group_does_not_cascade_into_assignments(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T", status=AssignmentStatus.PUBLISHED.value)
        gid, stamp = group.id, row.published_at
    with app.app_context():
        db.session.get(Group, gid).status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        row = Assignment.query.first()
        assert row.status == AssignmentStatus.PUBLISHED.value
        assert row.published_at == stamp


def test_toggle_is_post_only(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    assert client.get(f"{_url(gpid)}/{apid}/toggle-publication").status_code == 405
    with app.app_context():
        assert Assignment.query.first().status == AssignmentStatus.DRAFT.value


def test_toggle_requires_csrf(app, client):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        _, group = _setup()
        row = _assignment(group, title="T")
        gpid, apid = group.public_id, row.public_id
        csrf_client = csrf_app.test_client()
        login(csrf_client, "teacher@example.com")
        resp = csrf_client.post(f"{_url(gpid)}/{apid}/toggle-publication")
        assert resp.status_code == 400
        assert Assignment.query.first().status == AssignmentStatus.DRAFT.value
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_there_is_no_delete_route(app):
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert not any("assignments" in r and "delete" in r for r in rules)
    for rule in app.url_map.iter_rules():
        if "assignments" in str(rule):
            assert "DELETE" not in rule.methods


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


_PREFIX = [
    "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
    "lock:User", "lock:GroupTeacherAssignment",
]


def test_create_locks_canonical_order_single_reset(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")
    events = _capture_locks(lambda: _create(client, gpid, title="Locked"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == _PREFIX


def test_edit_locks_canonical_order_including_the_assignment(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    events = _capture_locks(lambda: _edit(client, gpid, apid, snapshot, title="T2"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == _PREFIX + ["lock:Assignment"]


def test_toggle_publication_locks_canonical_order(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T")
        gpid, apid = group.public_id, row.public_id
    login(client, "teacher@example.com")
    events = _capture_locks(lambda: _toggle(client, gpid, apid))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == _PREFIX + ["lock:Assignment"]


def test_post_lock_recheck_rejects_a_concurrently_removed_assignment(app, client):
    import app.blueprints.teacher.assignments as mod
    from unittest.mock import patch

    with app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
        row_id = GroupTeacherAssignment.query.first().id
    login(client, "teacher@example.com")

    original = mod.lock_group_in_open_transaction

    def remove_then_lock(pid):
        db.session.get(GroupTeacherAssignment, row_id).status = (
            GroupTeacherAssignmentStatus.REMOVED.value
        )
        db.session.commit()
        return original(pid)

    with patch.object(mod, "lock_group_in_open_transaction", side_effect=remove_then_lock):
        resp = _create(client, gpid, title="Race", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert Assignment.query.count() == 0


def test_post_lock_recheck_rejects_a_concurrently_suspended_teacher(app, client):
    import app.blueprints.teacher.assignments as mod
    from unittest.mock import patch

    with app.app_context():
        teacher, group = _setup()
        gpid, tid = group.public_id, teacher.id
    login(client, "teacher@example.com")

    original = mod.lock_group_in_open_transaction

    def suspend_then_lock(pid):
        db.session.get(User, tid).status = UserStatus.SUSPENDED.value
        db.session.commit()
        return original(pid)

    with patch.object(mod, "lock_group_in_open_transaction", side_effect=suspend_then_lock):
        resp = _create(client, gpid, title="Race", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert Assignment.query.count() == 0


def test_post_lock_recheck_rejects_a_concurrently_archived_group(app, client):
    import app.blueprints.teacher.assignments as mod
    from unittest.mock import patch

    with app.app_context():
        _, group = _setup()
        gpid, gid = group.public_id, group.id
    login(client, "teacher@example.com")

    original = mod.lock_group_in_open_transaction

    def archive_then_lock(pid):
        db.session.get(Group, gid).status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        return original(pid)

    with patch.object(mod, "lock_group_in_open_transaction", side_effect=archive_then_lock):
        resp = _create(client, gpid, title="Race", follow=True)
    assert resp.status_code == 200
    with app.app_context():
        assert Assignment.query.count() == 0


def test_group_retarget_and_assignment_create_use_compatible_locks(app, client):
    """The Administrator Group edit and every Assignment mutation both
    take AcademicTerm -> Level -> Course -> Group under one deliberate
    reset, so a same-Group retarget and an Assignment create serialise on
    the shared Group lock -- which is what stops a new Assignment
    slipping past the identity freeze.

    The Administrator half runs in its own app context: Flask-Login
    caches the resolved user on the app context, which the test fixture
    keeps pushed for the whole test, so a second client would otherwise
    keep seeing the Teacher. Production pushes a fresh app context per
    request and has no such carry-over.
    """
    with app.app_context():
        _, group = _setup()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        gpid, term_id, course_id = group.public_id, group.academic_term_id, group.course_id
    login(client, "teacher@example.com")
    assignment_events = _capture_locks(lambda: _create(client, gpid, title="X"))
    assignment_locks = [e for e in assignment_events if e.startswith("lock:")]

    with app.app_context():
        admin_client = app.test_client()
        login(admin_client, "admin@example.com")
        snapshot_html = admin_client.get(f"/admin/groups/{gpid}/edit").get_data(as_text=True)
        token = re.search(r'name="edit_snapshot" value="([^"]*)"', snapshot_html).group(1)
        group_locks = [
            e for e in _capture_locks(
                lambda: admin_client.post(
                    f"/admin/groups/{gpid}/edit",
                    data={"academic_term_id": term_id, "course_id": course_id,
                          "name": "Group A", "code": "", "capacity": 20,
                          "edit_snapshot": token},
                    follow_redirects=True,
                )
            ) if e.startswith("lock:")
        ]

    shared = ["lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group"]
    assert assignment_locks[:4] == shared
    assert group_locks[:4] == shared
    assert assignment_events.count("reset") == 1


# ===========================================================================
# Teacher dashboard integration
# ===========================================================================


def test_dashboard_card_links_to_the_assignments_page(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    login(client, "teacher@example.com")
    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert f'href="{_url(gpid)}"' in html
    assert "Manage Assignments" in html
    # The link works -- no dead links.
    assert client.get(_url(gpid)).status_code == 200


def test_dashboard_gains_no_pending_review_data(app, client):
    with app.app_context():
        _, group = _setup()
    login(client, "teacher@example.com")
    body = client.get("/teacher/dashboard").get_data(as_text=True).lower()
    for absent in ("pending review", "submissions", "to grade", "needs grading"):
        assert absent not in body, absent
