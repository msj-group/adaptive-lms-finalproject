"""The Student's own finalized attendance summary (Phase 4 / M07).

What a Student may read, what they may never read, and that the surface
has no write path at all.
"""

import re
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (
    AcademicStatus,
    AttendanceStatus,
    Enrollment,
    EnrollmentStatus,
    UserRole,
    UserStatus,
)
from app.services.attendance_queries import PAGE_SIZE
from tests import attendance_fixtures as fx
from tests.conftest import make_user

URL = "/student/attendance"

PRESENT = AttendanceStatus.PRESENT.value
ABSENT = AttendanceStatus.ABSENT.value
LATE = AttendanceStatus.LATE.value
EXCUSED = AttendanceStatus.EXCUSED.value


def _finalized_session(group, schedule, students, session_date=fx.SESSION_DATE, marks=None):
    session, records = fx.full_session(
        group, schedule, students, session_date=session_date, finalized_at=fx.LATER
    )
    for record, student in zip(records, students):
        if marks and student.email in marks:
            record.status = marks[student.email]
    db.session.commit()
    return session, records


def _rows(html):
    """The class dates the page listed, in order."""
    return re.findall(r"<td><strong>(\d{4}-\d{2}-\d{2})</strong></td>", html)


# ===========================================================================
# Access control
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    resp = client.get(URL)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.TEACHER.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_a_non_student_is_forbidden(app, client, role):
    with app.app_context():
        make_user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    assert client.get(URL).status_code == 403


def test_the_surface_has_no_write_route_at_all(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student])
    fx.login_as(client, "s@example.com")
    assert client.post(URL).status_code == 405
    assert client.put(URL).status_code == 405
    assert client.delete(URL).status_code == 405
    for suffix in ("/mark", "/dispute", "/correct", "/excuse"):
        assert client.post(URL + suffix).status_code == 404
        assert client.get(URL + suffix).status_code == 404


# ===========================================================================
# Visibility
# ===========================================================================


def test_a_student_sees_their_own_finalized_record(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com", name="Sam")
        _finalized_session(group, schedule, [student], marks={"s@example.com": LATE})
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert fx.SESSION_DATE.isoformat() in html
    assert "Group A" in html
    assert "18:00" in html and "20:00" in html
    assert "Room 3" in html
    assert "Late" in html


def test_a_draft_session_is_invisible_to_the_student(app, client):
    """A draft is a Teacher's work in progress -- a roster that starts out
    entirely absent. Showing it would tell a Student they were marked
    absent for a class nobody has finished recording."""
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        fx.full_session(group, schedule, [student])  # draft: finalized_at is NULL
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert fx.SESSION_DATE.isoformat() not in html
    assert "No attendance recorded yet" in html


def test_another_students_record_is_never_shown(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        mine = fx.enroll(group, "mine@example.com", name="Mine Student")
        theirs = fx.enroll(group, "theirs@example.com", name="Theirs Student")
        _finalized_session(
            group,
            schedule,
            [mine, theirs],
            marks={"mine@example.com": PRESENT, "theirs@example.com": EXCUSED},
        )
    fx.login_as(client, "mine@example.com")
    html = client.get(URL).get_data(as_text=True)
    # Scoped to the listing itself: the shared portal header legitimately
    # greets the signed-in student by name, and the totals panel names all
    # four statuses with an explicit zero.
    rows = html.split("<tbody>")[1].split("</tbody>")[0]
    assert rows.count("<tr>") == 1
    assert "Theirs Student" not in rows
    assert "Mine Student" not in rows
    assert "Present" in rows
    assert "Excused" not in rows
    assert "Excused 0" in html  # their own total, not the other student's mark


def test_a_teacher_only_note_is_never_disclosed(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _, records = _finalized_session(group, schedule, [student])
        records[0].note = "Kept disrupting the class."
        db.session.commit()
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert "Kept disrupting" not in html
    assert "note" not in html.lower().split("<body")[1] or "Private note" not in html


def test_the_teacher_identity_and_group_roster_are_never_disclosed(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        teacher.full_name = "Mr Teacher Person"
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        classmate = fx.enroll(group, "c@example.com", name="Classmate Person")
        db.session.commit()
        _finalized_session(group, schedule, [student, classmate])
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert "Mr Teacher Person" not in html
    assert "Classmate Person" not in html


def test_a_withdrawn_student_still_reads_their_own_history(app, client):
    """History survives: the record was captured while they were enrolled,
    and it stays theirs."""
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student], marks={"s@example.com": PRESENT})
        Enrollment.query.one().status = EnrollmentStatus.WITHDRAWN.value
        db.session.commit()
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert fx.SESSION_DATE.isoformat() in html
    assert "Present" in html


def test_an_archived_group_and_academic_chain_do_not_hide_history(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student])
        group.status = AcademicStatus.ARCHIVED.value
        group.academic_term.status = AcademicStatus.ARCHIVED.value
        group.course.status = AcademicStatus.ARCHIVED.value
        schedule.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert fx.SESSION_DATE.isoformat() in html


def test_a_suspended_student_cannot_sign_in_at_all(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student])
        student.status = UserStatus.SUSPENDED.value
        db.session.commit()
    fx.login_as(client, "s@example.com")
    resp = client.get(URL)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_records_from_several_groups_all_appear(app, client):
    with app.app_context():
        _, group_a = fx.setup_group("A")
        _, group_b = fx.setup_group("B", teacher_email="teacher-b@example.com")
        schedule_a = fx.schedule_for(group_a)
        schedule_b = fx.schedule_for(group_b, on_date=fx.PREVIOUS_WEEK)
        student = fx.enroll(group_a, "s@example.com")
        db.session.add(Enrollment(group_id=group_b.id, student_id=student.id))
        db.session.commit()
        _finalized_session(group_a, schedule_a, [student])
        _finalized_session(group_b, schedule_b, [student], session_date=fx.PREVIOUS_WEEK)
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert "Group A" in html and "Group B" in html
    assert _rows(html) == [fx.SESSION_DATE.isoformat(), fx.PREVIOUS_WEEK.isoformat()]


# ===========================================================================
# Totals
# ===========================================================================


def test_the_totals_count_only_this_students_own_finalized_records(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        student = fx.enroll(group, "s@example.com")
        other = fx.enroll(group, "o@example.com")
        schedule = fx.schedule_for(group)
        a_date = fx.SESSION_DATE
        for status in (PRESENT, PRESENT, LATE, EXCUSED):
            session, records = fx.full_session(
                group, schedule, [student, other], session_date=a_date,
                finalized_at=fx.LATER,
            )
            records[0].status = status
            records[1].status = ABSENT
            db.session.commit()
            a_date = a_date - timedelta(days=7)
        # One more, left as a DRAFT: it must not reach the totals.
        fx.full_session(group, schedule, [student, other], session_date=a_date)
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert "Present 2" in html
    assert "Late 1" in html
    assert "Excused 1" in html
    assert "Absent 0" in html
    assert "4 finalized classes in total" in html


def test_the_page_shows_counts_and_never_a_percentage_or_score(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student], marks={"s@example.com": PRESENT})
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    attendance_html = html.split("<h1", 1)[1].lower()
    assert "%" not in attendance_html
    for word in ("score", "percentage", "rate", "grade", "pass mark"):
        assert word not in attendance_html.split("these are")[0], word


def test_no_totals_panel_is_rendered_when_there_is_nothing_recorded(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        fx.schedule_for(group)
        fx.enroll(group, "s@example.com")
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    assert "Your recorded classes" not in html
    assert "No attendance recorded yet" in html


# ===========================================================================
# Pagination, ordering and bounds
# ===========================================================================


def test_the_summary_is_paginated_at_twenty_newest_first(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        dates = []
        a_date = fx.SESSION_DATE
        for _ in range(PAGE_SIZE + 2):
            dates.append(a_date)
            _finalized_session(group, schedule, [student], session_date=a_date)
            a_date = a_date - timedelta(days=7)
    fx.login_as(client, "s@example.com")
    page1 = _rows(client.get(URL).get_data(as_text=True))
    page2 = _rows(client.get(URL + "?page=2").get_data(as_text=True))
    assert len(page1) == PAGE_SIZE
    assert len(page2) == 2
    assert page1 == [d.isoformat() for d in dates[:PAGE_SIZE]]
    assert page2 == [d.isoformat() for d in dates[PAGE_SIZE:]]
    assert page1 == sorted(page1, reverse=True)


def test_a_page_past_the_end_falls_back_to_page_one(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student])
    fx.login_as(client, "s@example.com")
    resp = client.get(URL + "?page=77")
    assert resp.status_code == 200
    assert fx.SESSION_DATE.isoformat() in resp.get_data(as_text=True)


@pytest.mark.parametrize("value", ["0", "-2", "nope", "", "10" * 12])
def test_a_hostile_page_argument_is_normalized(app, client, value):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student])
    fx.login_as(client, "s@example.com")
    assert client.get(URL + f"?page={value}").status_code == 200


def test_the_summary_fetches_page_size_plus_one_and_never_counts_rows(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        a_date = fx.SESSION_DATE
        for _ in range(PAGE_SIZE + 3):
            _finalized_session(group, schedule, [student], session_date=a_date)
            a_date = a_date - timedelta(days=7)
    fx.login_as(client, "s@example.com")

    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append((statement, parameters))

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(URL).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    listing = [
        (statement, parameters)
        for statement, parameters in recorded
        if "FROM attendance_records" in statement and "count(" not in statement.lower()
    ]
    assert listing
    assert any(
        "LIMIT" in statement.upper() and 21 in tuple(parameters or ())
        for statement, parameters in listing
    )
    # The only attendance aggregate is the bounded per-status GROUP BY --
    # never an unbounded total count of the Student's rows. (The shared
    # portal header's own unread-notification count is a different
    # milestone's query and is deliberately not in scope here.)
    aggregates = [
        statement
        for statement, _ in recorded
        if "count(" in statement.lower() and "attendance_" in statement
    ]
    assert aggregates
    assert all("GROUP BY" in statement for statement in aggregates), aggregates


def test_reading_the_summary_issues_no_write_statement(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student])
    fx.login_as(client, "s@example.com")

    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(URL).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)
    for statement in recorded:
        assert not statement.strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))


# ===========================================================================
# Headers, navigation and leakage
# ===========================================================================


def test_the_summary_is_private_no_store_and_varies_on_cookie(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        _finalized_session(group, schedule, [student])
    fx.login_as(client, "s@example.com")
    resp = client.get(URL)
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_no_internal_id_reaches_the_page(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        session, records = _finalized_session(group, schedule, [student])
        ids = (group.id, schedule.id, session.id, records[0].id, student.id)
        publics = (group.public_id, session.public_id, records[0].public_id)
    fx.login_as(client, "s@example.com")
    html = client.get(URL).get_data(as_text=True)
    for public_id in publics:
        assert public_id not in html
    hrefs = re.findall(r'href="([^"]*)"', html)
    for value in ids:
        assert not any(href.rstrip("/").endswith(f"/{value}") for href in hrefs), value


def test_the_portal_navigation_links_attendance_for_students(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        fx.schedule_for(group)
        fx.enroll(group, "s@example.com")
    fx.login_as(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    nav = html[html.index('<nav class="portal-nav"') : html.index("</nav>")]
    assert 'href="/student/attendance"' in nav
