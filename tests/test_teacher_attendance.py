"""Teacher Attendance: occurrence rules, roster capture, draft marking,
finalization and authorization (Phase 4 / M07).

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* reset and lock order, and they
exercise the post-lock rechecks by injecting a state change at an exact
transaction boundary. They are **not** a demonstration of real InnoDB
blocking. No browser, accessibility, real-clock, DST or MySQL query-plan
verification is performed anywhere.
"""

import re
import uuid
from datetime import date, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from sqlalchemy import event

from app import create_app
from app.blueprints.teacher import attendance as att_mod
from app.extensions import db
from app.models import (
    AcademicStatus,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    Enrollment,
    EnrollmentStatus,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    UserRole,
    UserStatus,
)
from app.services.attendance_queries import PAGE_SIZE
from tests import attendance_fixtures as fx
from tests.conftest import make_user

PRESENT = AttendanceStatus.PRESENT.value
ABSENT = AttendanceStatus.ABSENT.value
LATE = AttendanceStatus.LATE.value
EXCUSED = AttendanceStatus.EXCUSED.value


def _at(*moments):
    """Patch the Teacher Attendance blueprint's clock."""
    return patch.object(att_mod, "utc_reference_now", fx.Clock(*moments))


def _inject(target_name, mutate):
    """Patch one of the blueprint's lock helpers so `mutate` runs at the
    exact transaction boundary -- i.e. after the page the Teacher was
    looking at was rendered, and immediately before the locks are taken.

    That is what makes the post-lock rechecks observable on a backend
    with no real locking: the injected change is committed first, so the
    route's own re-reads see it.
    """
    original = getattr(att_mod, target_name)

    def wrapper(*args, **kwargs):
        mutate()
        return original(*args, **kwargs)

    return patch.object(att_mod, target_name, wrapper)


def _setup(app, label="A", students=("alice@example.com", "bob@example.com"), **statuses):
    """One Group, one active Teacher, one Schedule, and some Students."""
    teacher, group = fx.setup_group(label, **statuses)
    schedule = fx.schedule_for(group)
    enrolled = [
        fx.enroll(group, email, name=email.split("@")[0].title()) for email in students
    ]
    return teacher, group, schedule, enrolled


def _statements():
    """A recorder for every SQL statement one request issues."""
    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append((statement, parameters))

    return recorded, _rec


# ===========================================================================
# Access control
# ===========================================================================


def test_anonymous_is_redirected_to_login_everywhere(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        session, _ = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
    for url in (
        "/teacher/attendance",
        fx.teacher_base(gpid),
        fx.teacher_base(gpid) + "/new",
        fx.teacher_detail(gpid, spid),
        fx.teacher_mark(gpid, spid),
    ):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value]
)
def test_a_non_teacher_is_forbidden(app, client, role):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        session, _ = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
        make_user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    assert client.get("/teacher/attendance").status_code == 403
    assert client.get(fx.teacher_base(gpid)).status_code == 403
    assert client.get(fx.teacher_detail(gpid, spid)).status_code == 403
    assert client.get(fx.teacher_mark(gpid, spid)).status_code == 403


def test_an_unassigned_teacher_gets_a_non_disclosing_404(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        session, _ = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
        fx.user("stranger@example.com", UserRole.TEACHER.value)
    fx.login_as(client, "stranger@example.com")
    assert client.get(fx.teacher_base(gpid)).status_code == 404
    assert client.get(fx.teacher_detail(gpid, spid)).status_code == 404
    assert client.get(fx.teacher_mark(gpid, spid)).status_code == 404
    assert client.get(fx.teacher_base(gpid) + "/new").status_code == 404


def test_a_removed_assignment_ends_access(app, client):
    with app.app_context():
        teacher, group, schedule, students = _setup(app)
        session, _ = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
        assignment = GroupTeacherAssignment.query.filter_by(
            group_id=group.id, teacher_id=teacher.id
        ).one()
        assignment.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.teacher_base(gpid)).status_code == 404
    assert client.get(fx.teacher_detail(gpid, spid)).status_code == 404


def test_another_groups_session_public_id_404s_here(app, client):
    """Nested-IDOR: every lookup is constrained to the Group in the URL."""
    with app.app_context():
        _, group_a, schedule_a, students_a = _setup(app, "A")
        teacher_b, group_b = fx.setup_group("B", teacher_email="teacher-b@example.com")
        schedule_b = fx.schedule_for(group_b)
        students_b = [fx.enroll(group_b, "carol@example.com")]
        session_b, _ = fx.full_session(group_b, schedule_b, students_b)
        gpid_a, spid_b = group_a.public_id, session_b.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.teacher_detail(gpid_a, spid_b)).status_code == 404
    assert client.get(fx.teacher_mark(gpid_a, spid_b)).status_code == 404


def test_a_nonexistent_group_and_session_both_404(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        session, _ = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.teacher_base("no-such-group")).status_code == 404
    assert client.get(fx.teacher_detail(gpid, "no-such-session")).status_code == 404


def test_a_suspended_teacher_cannot_sign_in_at_all(app, client):
    with app.app_context():
        teacher, group, schedule, students = _setup(app)
        gpid = group.public_id
        teacher.status = UserStatus.SUSPENDED.value
        db.session.commit()
    fx.login_as(client, "teacher@example.com")
    resp = client.get(fx.teacher_base(gpid))
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


# ===========================================================================
# Occurrence validation
# ===========================================================================


def _post_choice(client, gpid, schedule_public_id, session_date):
    return client.post(
        fx.teacher_base(gpid) + "/new",
        data={
            "schedule": schedule_public_id,
            "session_date": session_date.isoformat()
            if hasattr(session_date, "isoformat")
            else session_date,
        },
    )


def test_a_real_past_occurrence_is_accepted(app, client):
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid, spid, fx.SESSION_DATE)
    assert resp.status_code == 200
    assert "Confirm attendance session" in resp.get_data(as_text=True)


def test_today_is_an_acceptable_class_date(app, client):
    """A Teacher records attendance during or right after the class;
    requiring the meeting to have ended would make the common case
    impossible."""
    with app.app_context():
        _, group = fx.setup_group()
        today = fx.today_local(app)
        schedule = fx.schedule_for(group, on_date=today)
        fx.enroll(group)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid, spid, today)
    assert resp.status_code == 200
    assert "Confirm attendance session" in resp.get_data(as_text=True)


def test_a_future_class_date_is_refused(app, client):
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid, spid, fx.NEXT_WEEK)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "has not arrived yet" in html
    assert "Confirm attendance session" not in html


def test_a_date_on_the_wrong_weekday_is_refused(app, client):
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
        wrong_day = fx.SESSION_DATE - timedelta(days=1)
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid, spid, wrong_day)
    assert "not a day this class meets" in resp.get_data(as_text=True)


def test_a_date_before_the_effective_range_is_refused(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(
            group, effective_start=fx.SESSION_DATE, effective_end=fx.EFFECTIVE_END
        )
        fx.enroll(group)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        # One whole week earlier: the right weekday, but outside the range.
        resp = _post_choice(client, gpid, spid, fx.PREVIOUS_WEEK)
    assert "outside the period this class runs for" in resp.get_data(as_text=True)


def test_a_date_after_the_effective_range_is_refused(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(
            group, effective_start=fx.EFFECTIVE_START, effective_end=fx.PREVIOUS_WEEK
        )
        fx.enroll(group)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid, spid, fx.SESSION_DATE)
    assert "outside the period this class runs for" in resp.get_data(as_text=True)


def test_the_first_and_last_days_of_the_effective_range_are_inside_it(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(
            group, effective_start=fx.SESSION_DATE, effective_end=fx.SESSION_DATE
        )
        fx.enroll(group)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid, spid, fx.SESSION_DATE)
    assert "Confirm attendance session" in resp.get_data(as_text=True)


def test_an_archived_schedule_is_not_offered_and_is_refused(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group, status=AcademicStatus.ARCHIVED.value)
        fx.enroll(group)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        page = client.get(fx.teacher_base(gpid) + "/new")
        assert spid not in page.get_data(as_text=True)
        assert "No active scheduled class" in page.get_data(as_text=True)
        # And submitting it anyway is refused by the select's own choices.
        resp = _post_choice(client, gpid, spid, fx.SESSION_DATE)
    assert "Confirm attendance session" not in resp.get_data(as_text=True)
    with app.app_context():
        assert AttendanceSession.query.count() == 0


def test_another_groups_schedule_cannot_be_used(app, client):
    with app.app_context():
        _, group_a, _, _ = _setup(app, "A")
        _, group_b = fx.setup_group("B", teacher_email="teacher-b@example.com")
        schedule_b = fx.schedule_for(group_b)
        gpid_a, spid_b = group_a.public_id, schedule_b.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid_a, spid_b, fx.SESSION_DATE)
    html = resp.get_data(as_text=True)
    assert "Confirm attendance session" not in html
    with app.app_context():
        assert AttendanceSession.query.count() == 0


def test_an_exact_duplicate_occurrence_resolves_to_the_existing_session(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        first = fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
        assert first.status_code == 302
        second = _post_choice(client, gpid, spid, fx.SESSION_DATE)
    assert second.status_code == 302
    with app.app_context():
        session = AttendanceSession.query.one()
        assert second.headers["Location"].endswith(session.public_id)


def test_a_replayed_confirmation_never_creates_a_second_session(app, client):
    """The duplicate defense is the unique constraint, exercised through a
    genuinely replayed POST of the same signed token."""
    with app.app_context():
        _, group, schedule, students = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    base = fx.teacher_base(gpid)
    with _at(fx.NOW):
        page = _post_choice(client, gpid, spid, fx.SESSION_DATE)
        token = fx._extract_hidden(page.get_data(as_text=True), "attendance_state")
        first = client.post(base + "/new/confirm", data={"attendance_state": token})
        second = client.post(base + "/new/confirm", data={"attendance_state": token})
    assert first.status_code == 302
    assert second.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.count() == 1
        session = AttendanceSession.query.one()
        assert second.headers["Location"].endswith(session.public_id)


def test_an_occurrence_that_becomes_invalid_after_the_lock_is_refused(app, client):
    """The pre-lock preview is friendly only: the authoritative decision is
    made against the locked Schedule."""
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        gpid, spid, schedule_id = group.public_id, schedule.public_id, schedule.id
    fx.login_as(client, "teacher@example.com")
    base = fx.teacher_base(gpid)

    def archive_schedule():
        from app.models import Schedule

        row = db.session.get(Schedule, schedule_id)
        row.status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _at(fx.NOW):
        page = _post_choice(client, gpid, spid, fx.SESSION_DATE)
        token = fx._extract_hidden(page.get_data(as_text=True), "attendance_state")
        with _inject("lock_creation_chain", archive_schedule):
            resp = client.post(base + "/new/confirm", data={"attendance_state": token})
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.count() == 0


# ===========================================================================
# Roster capture
# ===========================================================================


def test_creation_captures_every_eligible_student_as_absent(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
        expected = {student.id for student in students}
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
    with app.app_context():
        session = AttendanceSession.query.one()
        records = AttendanceRecord.query.all()
        assert {record.student_id for record in records} == expected
        assert {record.status for record in records} == {ABSENT}
        assert {record.version for record in records} == {1}
        assert all(record.note is None for record in records)
        # The session and every record carry the SAME authoritative moment.
        assert session.created_at == session.updated_at == fx.NOW
        assert {record.created_at for record in records} == {fx.NOW}
        assert {record.updated_at for record in records} == {fx.NOW}


def test_the_snapshot_columns_come_from_the_schedule(app, client):
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
    with app.app_context():
        session = AttendanceSession.query.one()
        assert session.session_date == fx.SESSION_DATE
        assert session.start_time == fx.START_TIME
        assert session.end_time == fx.END_TIME
        assert session.location == fx.LOCATION
        assert session.version == 1
        assert session.finalized_at is None


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"status": EnrollmentStatus.WITHDRAWN.value}, "withdrawn enrollment"),
        ({"account_status": UserStatus.SUSPENDED.value}, "suspended account"),
        ({"role": UserRole.TEACHER.value}, "not a student"),
    ],
)
def test_an_ineligible_member_is_not_captured(app, client, kwargs, reason):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        eligible = fx.enroll(group, "alice@example.com")
        fx.enroll(group, "excluded@example.com", **kwargs)
        gpid, spid, eligible_id = group.public_id, schedule.public_id, eligible.id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
    with app.app_context():
        records = AttendanceRecord.query.all()
        assert [record.student_id for record in records] == [eligible_id], reason


def test_a_student_enrolled_in_another_group_is_not_captured(app, client):
    with app.app_context():
        _, group_a, schedule_a, _ = _setup(app, "A", students=("alice@example.com",))
        _, group_b = fx.setup_group("B", teacher_email="teacher-b@example.com")
        fx.enroll(group_b, "carol@example.com")
        gpid, spid = group_a.public_id, schedule_a.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
    with app.app_context():
        from app.models import User

        captured = {
            db.session.get(User, record.student_id).email
            for record in AttendanceRecord.query.all()
        }
        assert captured == {"alice@example.com"}


def test_a_group_with_no_eligible_student_cannot_open_a_session(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        fx.enroll(group, "gone@example.com", status=EnrollmentStatus.WITHDRAWN.value)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid, spid, fx.SESSION_DATE)
    html = resp.get_data(as_text=True)
    assert "no active enrolled students" in html
    assert "Confirm attendance session" not in html
    with app.app_context():
        assert AttendanceSession.query.count() == 0


def test_a_roster_that_empties_after_the_lock_refuses_to_create(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app, students=("alice@example.com",))
        gpid, spid = group.public_id, schedule.public_id
        enrollment_id = Enrollment.query.filter_by(group_id=group.id).one().id
    fx.login_as(client, "teacher@example.com")
    base = fx.teacher_base(gpid)

    def withdraw():
        row = db.session.get(Enrollment, enrollment_id)
        row.status = EnrollmentStatus.WITHDRAWN.value
        db.session.commit()

    with _at(fx.NOW):
        page = _post_choice(client, gpid, spid, fx.SESSION_DATE)
        token = fx._extract_hidden(page.get_data(as_text=True), "attendance_state")
        with _inject("lock_creation_chain", withdraw):
            resp = client.post(base + "/new/confirm", data={"attendance_state": token})
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.count() == 0
        assert AttendanceRecord.query.count() == 0


def test_the_roster_is_frozen_against_later_enrolment_changes(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
        original = {student.id for student in students}
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
    with app.app_context():
        # Somebody joins, somebody leaves, somebody is suspended.
        fx.enroll(group, "late-joiner@example.com")
        first = Enrollment.query.order_by(Enrollment.id).first()
        first.status = EnrollmentStatus.WITHDRAWN.value
        db.session.commit()
        records = AttendanceRecord.query.all()
        assert {record.student_id for record in records} == original


def test_creation_is_atomic_no_session_survives_a_failed_roster(app, client):
    """A failure leaves nothing behind -- never a session with a partial
    roster."""
    with app.app_context():
        _, group, schedule, students = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    base = fx.teacher_base(gpid)
    with _at(fx.NOW):
        page = _post_choice(client, gpid, spid, fx.SESSION_DATE)
        token = fx._extract_hidden(page.get_data(as_text=True), "attendance_state")
        with patch.object(
            att_mod, "create_session_with_roster", side_effect=RuntimeError("boom")
        ):
            with pytest.raises(RuntimeError):
                client.post(base + "/new/confirm", data={"attendance_state": token})
    with app.app_context():
        db.session.rollback()
        assert AttendanceSession.query.count() == 0
        assert AttendanceRecord.query.count() == 0


# ===========================================================================
# Create authorization and tokens
# ===========================================================================


def test_a_create_token_minted_for_another_group_is_refused(app, client):
    with app.app_context():
        _, group_a, schedule_a, _ = _setup(app, "A")
        _, group_b = fx.setup_group("B", teacher_email="teacher-b@example.com")
        schedule_b = fx.schedule_for(group_b)
        fx.enroll(group_b, "carol@example.com")
        gpid_a = group_a.public_id
        gpid_b, spid_b = group_b.public_id, schedule_b.public_id
    fx.login_as(client, "teacher-b@example.com")
    with _at(fx.NOW):
        page = _post_choice(client, gpid_b, spid_b, fx.SESSION_DATE)
        token_b = fx._extract_hidden(page.get_data(as_text=True), "attendance_state")
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = client.post(
            fx.teacher_base(gpid_a) + "/new/confirm", data={"attendance_state": token_b}
        )
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.count() == 0


def test_a_create_token_minted_by_another_teacher_is_refused(app, client):
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        fx.assign_teacher(group, "cover@example.com")
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "cover@example.com")
    with _at(fx.NOW):
        page = _post_choice(client, gpid, spid, fx.SESSION_DATE)
        token = fx._extract_hidden(page.get_data(as_text=True), "attendance_state")
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = client.post(
            fx.teacher_base(gpid) + "/new/confirm", data={"attendance_state": token}
        )
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.count() == 0


@pytest.mark.parametrize("token", ["", "not-a-token", "x.y.z"])
def test_a_missing_or_forged_create_token_is_refused(app, client, token):
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = client.post(
            fx.teacher_base(gpid) + "/new/confirm", data={"attendance_state": token}
        )
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.count() == 0


def test_creation_is_blocked_under_an_archived_chain(app, client):
    for archived in ("term_status", "level_status", "course_status", "group_status"):
        with app.app_context():
            db.drop_all()
            db.create_all()
            _, group, schedule, _ = _setup(
                app, **{archived: AcademicStatus.ARCHIVED.value}
            )
            gpid = group.public_id
        fx.login_as(client, "teacher@example.com")
        with _at(fx.NOW):
            resp = client.get(fx.teacher_base(gpid) + "/new")
        assert resp.status_code == 302, archived
        assert resp.headers["Location"].endswith("/attendance"), archived


def test_a_chain_archived_after_the_lock_refuses_to_create(app, client):
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        gpid, spid, group_id = group.public_id, schedule.public_id, group.id
    fx.login_as(client, "teacher@example.com")
    base = fx.teacher_base(gpid)

    def archive_group():
        from app.models import Group

        row = db.session.get(Group, group_id)
        row.status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _at(fx.NOW):
        page = _post_choice(client, gpid, spid, fx.SESSION_DATE)
        token = fx._extract_hidden(page.get_data(as_text=True), "attendance_state")
        with _inject("lock_creation_chain", archive_group):
            resp = client.post(base + "/new/confirm", data={"attendance_state": token})
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.count() == 0


# ===========================================================================
# Draft marking
# ===========================================================================


def _open_session(app, client, label="A", students=("alice@example.com", "bob@example.com")):
    """Create a session through the real routes and return the ids."""
    with app.app_context():
        _, group, schedule, enrolled = _setup(app, label, students=students)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
    with app.app_context():
        session = AttendanceSession.query.one()
        return gpid, session.public_id


@pytest.mark.parametrize("status", [PRESENT, ABSENT, LATE, EXCUSED])
def test_every_approved_status_can_be_saved(app, client, status):
    gpid, spid = _open_session(app, client, students=("alice@example.com",))
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        payload = fx.marking_payload(client, url, statuses={ids[0]: status})
        resp = client.post(url, data=payload)
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceRecord.query.one().status == status


def test_a_meaningful_save_bumps_each_changed_record_and_the_session_once(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        payload = fx.marking_payload(
            client, url, statuses={ids[0]: PRESENT, ids[1]: LATE}
        )
        client.post(url, data=payload)
    with app.app_context():
        session = AttendanceSession.query.one()
        assert session.version == 2
        assert session.updated_at == fx.LATER
        assert session.created_at == fx.NOW
        for record in AttendanceRecord.query.all():
            assert record.version == 2
            assert record.updated_at == fx.LATER
            assert record.created_at == fx.NOW


def test_only_the_records_that_actually_changed_are_versioned(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        client.post(url, data=fx.marking_payload(client, url, statuses={ids[0]: PRESENT}))
    with app.app_context():
        by_id = {r.public_id: r for r in AttendanceRecord.query.all()}
        assert by_id[ids[0]].version == 2
        assert by_id[ids[1]].version == 1
        assert by_id[ids[1]].updated_at == fx.NOW
        assert AttendanceSession.query.one().version == 2


def test_a_no_op_save_changes_no_version_and_no_timestamp(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        resp = client.post(url, data=fx.marking_payload(client, url))
    assert resp.status_code == 302
    with app.app_context():
        session = AttendanceSession.query.one()
        assert session.version == 1
        assert session.updated_at == fx.NOW
        for record in AttendanceRecord.query.all():
            assert record.version == 1
            assert record.updated_at == fx.NOW


def test_re_saving_identical_text_after_a_real_edit_is_still_a_no_op(app, client):
    gpid, spid = _open_session(app, client, students=("alice@example.com",))
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        client.post(url, data=fx.marking_payload(
            client, url, statuses={ids[0]: PRESENT}, notes={ids[0]: "  Arrived late.  "}
        ))
        with app.app_context():
            assert AttendanceRecord.query.one().note == "Arrived late."
        resp = client.post(url, data=fx.marking_payload(client, url))
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceRecord.query.one().version == 2
        assert AttendanceSession.query.one().version == 2


def test_a_whitespace_only_note_is_stored_as_no_note(app, client):
    gpid, spid = _open_session(app, client, students=("alice@example.com",))
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        resp = client.post(url, data=fx.marking_payload(
            client, url, notes={ids[0]: "   \n  "}
        ))
    assert resp.status_code == 302
    with app.app_context():
        record = AttendanceRecord.query.one()
        assert record.note is None
        assert record.version == 1  # nothing changed: it was already None


def test_an_oversized_note_is_refused_and_nothing_is_written(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        resp = client.post(url, data=fx.marking_payload(
            client,
            url,
            statuses={ids[0]: PRESENT},
            notes={ids[0]: "n" * 1001},
        ))
    assert resp.status_code == 200
    assert "limited to 1000 characters" in resp.get_data(as_text=True)
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}
        assert AttendanceSession.query.one().version == 1


def test_a_note_of_exactly_the_limit_is_accepted(app, client):
    gpid, spid = _open_session(app, client, students=("alice@example.com",))
    url = fx.teacher_mark(gpid, spid)
    text = "n" * 1000
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        resp = client.post(url, data=fx.marking_payload(client, url, notes={ids[0]: text}))
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceRecord.query.one().note == text


def test_an_invalid_status_is_refused_and_nothing_is_written(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        resp = client.post(url, data=fx.marking_payload(
            client, url, statuses={ids[0]: "sick", ids[1]: PRESENT}
        ))
    assert resp.status_code == 200
    assert "does not exist" in resp.get_data(as_text=True)
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}
        assert AttendanceSession.query.one().version == 1


def test_a_partial_submission_is_refused_whole(app, client):
    """One missing field rejects the entire save -- there is no partial
    write."""
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        payload = fx.marking_payload(client, url, statuses={ids[0]: PRESENT})
        payload.pop(f"status__{ids[1]}")
        resp = client.post(url, data=payload)
    assert resp.status_code == 200
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}


def test_a_missing_note_field_never_silently_clears_a_colleagues_note(app, client):
    gpid, spid = _open_session(app, client, students=("alice@example.com",))
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        client.post(url, data=fx.marking_payload(client, url, notes={ids[0]: "Sat at the back."}))
        payload = fx.marking_payload(client, url)
        payload.pop(f"note__{ids[0]}")
        resp = client.post(url, data=payload)
    assert resp.status_code == 200
    with app.app_context():
        assert AttendanceRecord.query.one().note == "Sat at the back."


def test_a_submission_naming_an_unknown_record_changes_nothing(app, client):
    """Only the captured public ids are ever looked up, so an extra field
    adds, replaces and reorders nothing."""
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        payload = fx.marking_payload(client, url)
        payload["status__not-a-record"] = PRESENT
        payload["note__not-a-record"] = "injected"
        resp = client.post(url, data=payload)
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceRecord.query.count() == 2
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}


def test_a_draft_token_is_stale_after_a_co_teacher_saves(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with app.app_context():
        from app.models import Group

        group = db.session.query(Group).filter_by(public_id=gpid).one()
        fx.assign_teacher(group, "cover@example.com")
    with _at(fx.LATER):
        stale_payload = fx.marking_payload(client, url)
        ids = sorted(k[len("status__"):] for k in stale_payload if k.startswith("status__"))
        stale_payload[f"status__{ids[0]}"] = PRESENT
    # The co-teacher saves first, invalidating the token above.
    fx.login_as(client, "cover@example.com")
    with _at(fx.LATER):
        client.post(url, data=fx.marking_payload(client, url, statuses={ids[1]: LATE}))
    fx.login_as(client, "teacher@example.com")
    with _at(fx.LATER):
        resp = client.post(url, data=stale_payload)
    assert resp.status_code == 302
    with app.app_context():
        by_id = {r.public_id: r for r in AttendanceRecord.query.all()}
        assert by_id[ids[0]].status == ABSENT  # the stale save was rejected
        assert by_id[ids[1]].status == LATE


def test_a_draft_token_from_another_session_is_refused(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
        fx.create_session_via_routes(client, gpid, spid, fx.PREVIOUS_WEEK)
    with app.app_context():
        sessions = AttendanceSession.query.order_by(AttendanceSession.id).all()
        first_pid, second_pid = sessions[0].public_id, sessions[1].public_id
    first_url = fx.teacher_mark(gpid, first_pid)
    second_url = fx.teacher_mark(gpid, second_pid)
    with _at(fx.LATER):
        foreign_token = fx.hidden_value(client, second_url, "attendance_state")
        payload = fx.marking_payload(client, first_url, token=foreign_token)
        ids = sorted(k[len("status__"):] for k in payload if k.startswith("status__"))
        payload[f"status__{ids[0]}"] = PRESENT
        resp = client.post(first_url, data=payload)
    assert resp.status_code == 302
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}


@pytest.mark.parametrize("token", ["", "forged", "a.b.c"])
def test_a_missing_or_forged_draft_token_is_refused(app, client, token):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        payload = fx.marking_payload(client, url, token=token)
        ids = sorted(k[len("status__"):] for k in payload if k.startswith("status__"))
        payload[f"status__{ids[0]}"] = PRESENT
        resp = client.post(url, data=payload)
    assert resp.status_code == 302
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}


def test_a_session_finalized_after_the_lock_refuses_the_draft_save(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with app.app_context():
        session_id = AttendanceSession.query.one().id

    def finalize():
        row = db.session.get(AttendanceSession, session_id)
        row.finalized_at = fx.NOW
        db.session.commit()

    with _at(fx.LATER):
        payload = fx.marking_payload(client, url)
        ids = sorted(k[len("status__"):] for k in payload if k.startswith("status__"))
        payload[f"status__{ids[0]}"] = PRESENT
        with _inject("lock_session_chain", finalize):
            resp = client.post(url, data=payload)
    assert resp.status_code == 302
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}


def test_an_assignment_removed_after_the_lock_404s_the_draft_save(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with app.app_context():
        assignment_id = GroupTeacherAssignment.query.one().id

    def remove_assignment():
        row = db.session.get(GroupTeacherAssignment, assignment_id)
        row.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()

    with _at(fx.LATER):
        payload = fx.marking_payload(client, url)
        ids = sorted(k[len("status__"):] for k in payload if k.startswith("status__"))
        payload[f"status__{ids[0]}"] = PRESENT
        with _inject("lock_session_chain", remove_assignment):
            resp = client.post(url, data=payload)
    assert resp.status_code == 404
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}


def test_a_draft_save_is_blocked_under_an_archived_chain(app, client):
    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with app.app_context():
        from app.models import Group

        group = db.session.query(Group).filter_by(public_id=gpid).one()
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    with _at(fx.LATER):
        resp = client.get(url)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(spid)


def test_an_integrity_error_on_save_is_reported_generically(app, client):
    """Rolled back first, re-authorized, and reported without SQL, driver
    or parameter detail."""
    from sqlalchemy.exc import IntegrityError

    gpid, spid = _open_session(app, client)
    url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        payload = fx.marking_payload(client, url)
        ids = sorted(k[len("status__"):] for k in payload if k.startswith("status__"))
        payload[f"status__{ids[0]}"] = PRESENT
        with patch.object(
            db.session,
            "commit",
            side_effect=IntegrityError("INSERT INTO secret", {"p": 1}, Exception("driver")),
        ):
            resp = client.post(url, data=payload, follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "could not be saved" in html
    for leak in ("INSERT INTO", "IntegrityError", "driver", "sqlalchemy"):
        assert leak not in html, leak
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}


# ===========================================================================
# Finalization
# ===========================================================================


def _finalize_token(client, mark_url):
    html = client.get(mark_url).get_data(as_text=True)
    tokens = re.findall(r'name="attendance_state" value="([^"]*)"', html)
    assert len(tokens) == 2, "the marking page mints a draft AND a finalize token"
    return tokens[1]


def test_finalization_freezes_the_session_and_bumps_the_version_once(app, client):
    gpid, spid = _open_session(app, client)
    mark_url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        token = _finalize_token(client, mark_url)
        resp = client.post(
            fx.teacher_finalize(gpid, spid),
            data={"attendance_state": token, "confirm_finalize": "yes"},
        )
    assert resp.status_code == 302
    with app.app_context():
        session = AttendanceSession.query.one()
        assert session.finalized_at == fx.LATER
        assert session.finalized_at.microsecond == 0
        assert session.version == 2
        assert session.updated_at == fx.LATER


def test_finalization_requires_the_explicit_confirmation(app, client):
    gpid, spid = _open_session(app, client)
    mark_url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        token = _finalize_token(client, mark_url)
        resp = client.post(fx.teacher_finalize(gpid, spid), data={"attendance_state": token})
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.one().finalized_at is None


def test_a_finalization_replay_changes_nothing(app, client):
    gpid, spid = _open_session(app, client)
    mark_url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        token = _finalize_token(client, mark_url)
        client.post(
            fx.teacher_finalize(gpid, spid),
            data={"attendance_state": token, "confirm_finalize": "yes"},
        )
    with app.app_context():
        session = AttendanceSession.query.one()
        before = (session.finalized_at, session.version, session.updated_at)
        records_before = {
            (r.public_id, r.status, r.note, r.version, r.updated_at)
            for r in AttendanceRecord.query.all()
        }
    with _at(fx.NOW + timedelta(days=1)):
        replay = client.post(
            fx.teacher_finalize(gpid, spid),
            data={"attendance_state": token, "confirm_finalize": "yes"},
        )
    assert replay.status_code == 302
    assert replay.headers["Location"].endswith(spid)
    with app.app_context():
        session = AttendanceSession.query.one()
        assert (session.finalized_at, session.version, session.updated_at) == before
        assert {
            (r.public_id, r.status, r.note, r.version, r.updated_at)
            for r in AttendanceRecord.query.all()
        } == records_before


def test_a_stale_finalize_token_is_refused(app, client):
    gpid, spid = _open_session(app, client)
    mark_url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        token = _finalize_token(client, mark_url)
        # A co-teacher saves in between, moving the session's version.
        payload = fx.marking_payload(client, mark_url)
        ids = sorted(k[len("status__"):] for k in payload if k.startswith("status__"))
        payload[f"status__{ids[0]}"] = PRESENT
        client.post(mark_url, data=payload)
        resp = client.post(
            fx.teacher_finalize(gpid, spid),
            data={"attendance_state": token, "confirm_finalize": "yes"},
        )
    assert resp.status_code == 302
    with app.app_context():
        assert AttendanceSession.query.one().finalized_at is None


def test_a_finalized_session_cannot_be_marked_again(app, client):
    gpid, spid = _open_session(app, client)
    mark_url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        token = _finalize_token(client, mark_url)
        payload = fx.marking_payload(client, mark_url)
        ids = sorted(k[len("status__"):] for k in payload if k.startswith("status__"))
        payload[f"status__{ids[0]}"] = PRESENT
        client.post(
            fx.teacher_finalize(gpid, spid),
            data={"attendance_state": token, "confirm_finalize": "yes"},
        )
        get_resp = client.get(mark_url)
        post_resp = client.post(mark_url, data=payload)
    assert get_resp.status_code == 302
    assert post_resp.status_code == 302
    with app.app_context():
        assert {r.status for r in AttendanceRecord.query.all()} == {ABSENT}
        assert {r.version for r in AttendanceRecord.query.all()} == {1}


def test_a_finalized_session_detail_offers_no_controls(app, client):
    gpid, spid = _open_session(app, client)
    mark_url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        token = _finalize_token(client, mark_url)
        client.post(
            fx.teacher_finalize(gpid, spid),
            data={"attendance_state": token, "confirm_finalize": "yes"},
        )
        html = client.get(fx.teacher_detail(gpid, spid)).get_data(as_text=True)
    # The only POST form on the page is the shared portal header's logout.
    actions = re.findall(r'<form[^>]*action="([^"]*)"', html)
    assert actions == ["/auth/logout"]
    assert "Mark attendance" not in html
    assert "attendance_state" not in html
    assert "confirm_finalize" not in html
    assert 'name="status__' not in html
    assert "Finalized" in html


def test_there_is_no_reopen_delete_or_duplicate_endpoint(app, client):
    gpid, spid = _open_session(app, client)
    for suffix in ("/reopen", "/delete", "/duplicate", "/unlock", "/export"):
        assert client.post(fx.teacher_detail(gpid, spid) + suffix).status_code == 404
        assert client.get(fx.teacher_detail(gpid, spid) + suffix).status_code == 404
    # And DELETE on the session itself is not a supported method.
    assert client.delete(fx.teacher_detail(gpid, spid)).status_code == 405


def test_historical_reading_survives_an_archived_chain(app, client):
    gpid, spid = _open_session(app, client)
    mark_url = fx.teacher_mark(gpid, spid)
    with _at(fx.LATER):
        token = _finalize_token(client, mark_url)
        client.post(
            fx.teacher_finalize(gpid, spid),
            data={"attendance_state": token, "confirm_finalize": "yes"},
        )
    with app.app_context():
        from app.models import Group, Schedule

        group = db.session.query(Group).filter_by(public_id=gpid).one()
        group.status = AcademicStatus.ARCHIVED.value
        group.academic_term.status = AcademicStatus.ARCHIVED.value
        db.session.query(Schedule).one().status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    with _at(fx.LATER):
        assert client.get(fx.teacher_base(gpid)).status_code == 200
        detail = client.get(fx.teacher_detail(gpid, spid))
    assert detail.status_code == 200
    assert "Alice" in detail.get_data(as_text=True)


# ===========================================================================
# Notes
# ===========================================================================


def test_a_note_is_escaped_not_rendered_as_markup(app, client):
    gpid, spid = _open_session(app, client, students=("alice@example.com",))
    url = fx.teacher_mark(gpid, spid)
    payload_note = "<script>alert('x')</script> & \"quotes\""
    with _at(fx.LATER):
        ids = fx.record_public_ids(client, url)
        client.post(url, data=fx.marking_payload(client, url, notes={ids[0]: payload_note}))
        html = client.get(fx.teacher_detail(gpid, spid)).get_data(as_text=True)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    with app.app_context():
        assert AttendanceRecord.query.one().note == payload_note


def test_a_student_name_is_escaped(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        fx.enroll(group, "x@example.com", name="<b>Bold</b> Student")
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        fx.create_session_via_routes(client, gpid, spid, fx.SESSION_DATE)
    with app.app_context():
        session_pid = AttendanceSession.query.one().public_id
    with _at(fx.NOW):
        html = client.get(fx.teacher_detail(gpid, session_pid)).get_data(as_text=True)
    assert "<b>Bold</b> Student" not in html
    assert "&lt;b&gt;Bold&lt;/b&gt; Student" in html


# ===========================================================================
# Pagination, ordering and query bounds
# ===========================================================================


def _many_sessions(app, group, schedule, count):
    dates = []
    a_date = fx.SESSION_DATE
    for _ in range(count):
        dates.append(a_date)
        fx.attendance_session(group, schedule, session_date=a_date)
        a_date = a_date - timedelta(days=7)
    return dates


def test_the_session_list_is_paginated_at_twenty_newest_first(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        dates = _many_sessions(app, group, schedule, PAGE_SIZE + 3)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        page1 = client.get(fx.teacher_base(gpid)).get_data(as_text=True)
        page2 = client.get(fx.teacher_base(gpid) + "?page=2").get_data(as_text=True)
    shown1 = re.findall(r"<td><strong>(\d{4}-\d{2}-\d{2})</strong></td>", page1)
    shown2 = re.findall(r"<td><strong>(\d{4}-\d{2}-\d{2})</strong></td>", page2)
    assert len(shown1) == PAGE_SIZE
    assert len(shown2) == 3
    assert shown1 == sorted(shown1, reverse=True)
    assert shown1 == [d.isoformat() for d in dates[:PAGE_SIZE]]
    assert shown2 == [d.isoformat() for d in dates[PAGE_SIZE:]]
    assert "Next" in page1
    assert "Previous" in page2


def test_a_page_past_the_end_falls_back_to_page_one(app, client):
    """A stale bookmark shows page 1 rather than a confusing empty page
    with a "Previous" button."""
    with app.app_context():
        _, group, schedule, students = _setup(app)
        fx.attendance_session(group, schedule)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = client.get(fx.teacher_base(gpid) + "?page=99")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert fx.SESSION_DATE.isoformat() in html
    assert "No attendance recorded yet" not in html
    assert "Previous" not in html


@pytest.mark.parametrize("value", ["0", "-4", "abc", "", "99999999999999999999"])
def test_a_hostile_page_argument_is_normalized(app, client, value):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        fx.attendance_session(group, schedule)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = client.get(fx.teacher_base(gpid) + f"?page={value}")
    assert resp.status_code == 200


def test_the_list_fetches_page_size_plus_one_and_never_counts_sessions(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        _many_sessions(app, group, schedule, PAGE_SIZE + 5)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    recorded, listener = _statements()
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", listener)
    try:
        with _at(fx.NOW):
            assert client.get(fx.teacher_base(gpid)).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", listener)

    session_selects = [
        (statement, parameters)
        for statement, parameters in recorded
        if "FROM attendance_sessions" in statement
    ]
    assert session_selects
    assert any(
        "LIMIT" in statement.upper() and 21 in tuple(parameters or ())
        for statement, parameters in session_selects
    )
    # No unbounded total count of sessions anywhere.
    for statement, _ in session_selects:
        assert "count(" not in statement.lower()


def test_the_list_cost_does_not_grow_with_the_number_of_sessions(app, client):
    """One bounded aggregate resolves the whole page's status counts, so
    there is no per-row lookup."""

    def query_count(session_count, record_count):
        with app.app_context():
            db.drop_all()
            db.create_all()
            _, group, schedule, students = _setup(
                app, students=tuple(f"s{i}@example.com" for i in range(record_count))
            )
            for index in range(session_count):
                session = fx.attendance_session(
                    group,
                    schedule,
                    session_date=fx.SESSION_DATE - timedelta(days=7 * index),
                )
                for student in students:
                    fx.attendance_record(session, student)
            gpid = group.public_id
        fx.login_as(client, "teacher@example.com")
        recorded, listener = _statements()
        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", listener)
        try:
            with _at(fx.NOW):
                assert client.get(fx.teacher_base(gpid)).status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", listener)
        return len([s for s, _ in recorded if s.strip().upper().startswith("SELECT")])

    small = query_count(1, 2)
    large = query_count(PAGE_SIZE, 5)
    assert small == large


def test_reading_the_list_issues_no_write_statement(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        session, _ = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
    fx.login_as(client, "teacher@example.com")
    recorded, listener = _statements()
    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", listener)
    try:
        with _at(fx.NOW):
            assert client.get(fx.teacher_base(gpid)).status_code == 200
            assert client.get(fx.teacher_detail(gpid, spid)).status_code == 200
            assert client.get("/teacher/attendance").status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", listener)
    for statement, _ in recorded:
        assert not statement.strip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE")
        ), statement


# ===========================================================================
# Response headers, URLs and leakage
# ===========================================================================


def test_every_attendance_page_is_private_no_store_and_varies_on_cookie(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        session, _ = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        for url in (
            "/teacher/attendance",
            fx.teacher_base(gpid),
            fx.teacher_base(gpid) + "/new",
            fx.teacher_detail(gpid, spid),
            fx.teacher_mark(gpid, spid),
        ):
            resp = client.get(url)
            assert resp.status_code == 200, url
            assert resp.headers["Cache-Control"] == "private, no-store", url
            assert "Cookie" in resp.headers.get("Vary", ""), url


def test_the_confirmation_page_also_carries_the_headers(app, client):
    with app.app_context():
        _, group, schedule, _ = _setup(app)
        gpid, spid = group.public_id, schedule.public_id
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        resp = _post_choice(client, gpid, spid, fx.SESSION_DATE)
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def _url_path_segments(html):
    """Every complete path segment of every link and form target in `html`.

    Compared whole, never as a raw-HTML substring: a public UUID beginning
    with "1" makes "/attendance/1" a substring of a perfectly correct link.
    """
    return {
        segment
        for target in re.findall(r'(?:href|action)="([^"]*)"', html)
        for segment in urlsplit(target).path.split("/")
    }


def test_a_public_id_beginning_with_an_internal_id_is_not_that_id():
    public_id = "1" + str(uuid.uuid4())[1:]
    html = (f'<a href="/teacher/groups/g/attendance/{public_id}">Open</a>'
            f'<form action="/teacher/groups/g/attendance/{public_id}/mark"></form>')
    assert "/attendance/1" in html  # what the old substring check tripped on
    assert "1" not in _url_path_segments(html)
    assert public_id in _url_path_segments(html)
    # A real numeric segment is still found, in any position.
    assert "7" in _url_path_segments('<a href="/teacher/groups/7/attendance/x">')
    assert "7" in _url_path_segments('<form action="/teacher/groups/g/attendance/7?page=2">')


def test_no_internal_numeric_id_appears_in_any_url_or_field(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        session, records = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
        ids = {
            "group": group.id,
            "schedule": schedule.id,
            "session": session.id,
            "record": records[0].id,
            "student": students[0].id,
        }
    fx.login_as(client, "teacher@example.com")
    with _at(fx.NOW):
        for url in (
            fx.teacher_base(gpid),
            fx.teacher_detail(gpid, spid),
            fx.teacher_mark(gpid, spid),
        ):
            html = client.get(url).get_data(as_text=True)
            segments = _url_path_segments(html)
            names = re.findall(r'name="([^"]*)"', html)
            for label, value in ids.items():
                assert str(value) not in segments, (url, label)
                assert not any(
                    str(value) in name.split("__") for name in names
                ), (url, label)
            assert spid in segments, url


def test_unsupported_methods_are_refused(app, client):
    with app.app_context():
        _, group, schedule, students = _setup(app)
        session, _ = fx.full_session(group, schedule, students)
        gpid, spid = group.public_id, session.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.post("/teacher/attendance").status_code == 405
    assert client.post(fx.teacher_base(gpid)).status_code == 405
    assert client.post(fx.teacher_detail(gpid, spid)).status_code == 405
    assert client.get(fx.teacher_finalize(gpid, spid)).status_code == 405
    assert client.get(fx.teacher_base(gpid) + "/new/confirm").status_code == 405


def test_csrf_remains_enforced_on_every_attendance_post():
    csrf_app = create_app("testing")
    csrf_app.config["WTF_CSRF_ENABLED"] = True
    csrf_client = csrf_app.test_client()
    with csrf_app.app_context():
        db.create_all()
        try:
            _, group, schedule, students = _setup(csrf_app)
            session, _ = fx.full_session(group, schedule, students)
            gpid, spid = group.public_id, session.public_id

            login_page = csrf_client.get("/auth/login")
            token = re.search(
                r'name="csrf_token" type="hidden" value="([^"]+)"',
                login_page.get_data(as_text=True),
            ).group(1)
            csrf_client.post(
                "/auth/login",
                data={
                    "email": "teacher@example.com",
                    "password": fx.PW,
                    "csrf_token": token,
                },
            )
            for url, data in (
                (fx.teacher_base(gpid) + "/new", {"schedule": schedule.public_id}),
                (fx.teacher_base(gpid) + "/new/confirm", {"attendance_state": "x"}),
                (fx.teacher_mark(gpid, spid), {"attendance_state": "x"}),
                (fx.teacher_finalize(gpid, spid), {"confirm_finalize": "yes"}),
            ):
                assert csrf_client.post(url, data=data).status_code == 400, url
            assert AttendanceSession.query.count() == 1
            assert AttendanceSession.query.one().finalized_at is None
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


# ===========================================================================
# Lock order (structural only)
# ===========================================================================


def _locked_tables(app, call):
    """The tables one lock chain touches, in the order it touches them.

    The chain is exercised **directly** rather than through a request, so
    the pre-lock preview reads a route legitimately performs cannot be
    mistaken for part of the lock order.
    """
    seen = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        match = re.search(r"\bFROM ([a-z_]+)", " ".join(statement.split()))
        if match:
            seen.append(match.group(1))

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        call()
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    ordered = []
    for name in seen:
        if name not in ordered:
            ordered.append(name)
    return ordered


def test_the_creation_chain_requests_the_documented_lock_order(app):
    """Structural: SQLite honours neither ``FOR UPDATE`` nor REPEATABLE
    READ, so this asserts what the code *requests*, not that anything
    blocks."""
    from app.services.attendance_transactions import lock_creation_chain

    with app.app_context():
        teacher, group, schedule, students = _setup(app)
        enrollments = [
            row.id for row in Enrollment.query.order_by(Enrollment.id).all()
        ]
        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
            schedule.id,
            fx.SESSION_DATE,
        )
        student_ids = [student.id for student in students]
        ordered = _locked_tables(
            app,
            lambda: lock_creation_chain(
                *args, student_ids=student_ids, enrollment_ids=enrollments
            ),
        )
    assert ordered == [
        "academic_terms",
        "levels",
        "courses",
        "groups",
        "users",
        "group_teacher_assignments",
        "schedules",
        "enrollments",
        "attendance_sessions",
    ]


def test_the_session_chain_requests_the_documented_lock_order(app):
    from app.services.attendance_transactions import lock_session_chain

    with app.app_context():
        teacher, group, schedule, students = _setup(app)
        session, records = fx.full_session(group, schedule, students)
        # Every scalar is resolved BEFORE recording starts, so a lazy
        # relationship load in the test's own argument list cannot be
        # mistaken for one of the chain's lock statements.
        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
            schedule.id,
            session.id,
        )
        student_ids = [student.id for student in students]
        record_ids = [record.id for record in records]
        ordered = _locked_tables(
            app,
            lambda: lock_session_chain(
                *args, student_ids=student_ids, record_ids=record_ids
            ),
        )
    assert ordered == [
        "academic_terms",
        "levels",
        "courses",
        "groups",
        "users",
        "group_teacher_assignments",
        "schedules",
        "attendance_sessions",
        "attendance_records",
    ]


def test_both_chains_lock_rows_of_one_type_in_ascending_internal_id(app):
    """Ascending internal id at every level -- never display order and
    never submission order -- is what keeps two concurrent writers from
    deadlocking against each other."""
    from app.services.attendance_transactions import lock_session_chain

    with app.app_context():
        teacher, group, schedule, students = _setup(
            app, students=("c@example.com", "a@example.com", "b@example.com")
        )
        session, records = fx.full_session(group, schedule, students)
        student_ids = [student.id for student in students]
        record_ids = [record.id for record in records]
        teacher_id = teacher.id
        group_public_id = group.public_id
        term_id = group.academic_term_id
        level_id = group.course.level_id
        course_id = group.course_id
        schedule_id = schedule.id
        session_id = session.id

        seen = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            text = " ".join(statement.split())
            for table in ("users", "attendance_records"):
                if f"FROM {table}" in text and parameters:
                    seen.append((table, tuple(parameters)))

        event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            lock_session_chain(
                group_public_id,
                term_id,
                level_id,
                course_id,
                teacher_id,
                schedule_id,
                session_id,
                # Deliberately shuffled: the chain must sort them itself.
                student_ids=list(reversed(student_ids)),
                record_ids=list(reversed(record_ids)),
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", _rec)

    locked_users = [p[0] for table, p in seen if table == "users"]
    locked_records = [p[0] for table, p in seen if table == "attendance_records"]
    assert locked_users == sorted({teacher_id, *student_ids})
    assert locked_records == sorted(record_ids)


def test_both_chains_sort_teacher_with_students_when_a_student_has_the_lower_id(app):
    """The global User order must not depend on role. This is the shape
    that used to invert against M11 on a different Group: Attendance took
    the higher-id Teacher first while messaging held the lower-id Student."""
    from app.services.attendance_transactions import lock_creation_chain, lock_session_chain

    with app.app_context():
        group = fx.hierarchy("Participant order")
        student = fx.user("low-student@example.com", UserRole.STUDENT.value)
        teacher = fx.assign_teacher(group, "high-teacher@example.com")
        schedule = fx.schedule_for(group)
        enrollment = Enrollment(group_id=group.id, student_id=student.id)
        db.session.add(enrollment)
        db.session.commit()
        session, records = fx.full_session(group, schedule, [student])

        student_id = student.id
        teacher_id = teacher.id
        enrollment_id = enrollment.id
        session_id = session.id
        record_ids = [record.id for record in records]

        common = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher_id,
            schedule.id,
        )
        calls = (
            lambda: lock_creation_chain(
                *common,
                fx.NEXT_WEEK,
                student_ids=(value for value in [student_id]),
                enrollment_ids=[enrollment_id],
            ),
            lambda: lock_session_chain(
                *common,
                session_id,
                student_ids=(value for value in [student_id]),
                record_ids=record_ids,
            ),
        )
        orders = []
        for call in calls:
            seen = []

            def _rec(conn, cursor, statement, parameters, context, executemany):
                if re.search(r"\bFROM users WHERE users\.id = \?", " ".join(statement.split())):
                    seen.append(tuple(parameters)[0])

            event.listen(db.engine, "before_cursor_execute", _rec)
            try:
                locks = call()
            finally:
                event.remove(db.engine, "before_cursor_execute", _rec)
            orders.append(seen)
            assert list(locks.students) == [student_id]
            assert locks.students[student_id] is not None

    assert student_id < teacher_id
    assert orders == [sorted((student_id, teacher_id))] * 2


@pytest.mark.parametrize("chain", ["lock_creation_chain", "lock_session_chain"])
def test_each_chain_resets_the_transaction_exactly_once(app, chain):
    """One deliberate reset, owned by ``lock_academic_hierarchy`` and
    taken before the first lock -- and no second reset while locks are
    held, which would release them."""
    import app.services.attendance_transactions as tx_mod

    with app.app_context():
        teacher, group, schedule, students = _setup(app)
        session, records = fx.full_session(group, schedule, students)
        kwargs = (
            {"student_ids": [s.id for s in students], "enrollment_ids": []}
            if chain == "lock_creation_chain"
            else {"student_ids": [s.id for s in students], "record_ids": [r.id for r in records]}
        )
        seventh = fx.SESSION_DATE if chain == "lock_creation_chain" else session.id
        with patch.object(db.session, "rollback", wraps=db.session.rollback) as reset:
            getattr(tx_mod, chain)(
                group.public_id,
                group.academic_term_id,
                group.course.level_id,
                group.course_id,
                teacher.id,
                schedule.id,
                seventh,
                **kwargs,
            )
        assert reset.call_count == 1
