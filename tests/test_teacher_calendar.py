"""Phase 4 / M10 -- the Teacher calendar surface.

Covers the role authorization, the complete visibility formula (active
teacher assignment, operational academic chain, published sources,
scheduled center events), the immediate effect of a removed assignment /
archived chain / cancelled event, the complete absence of any
CalendarEvent mutation endpoint and of any event-management control, the
server-generated authorized links, the range navigation, the response
headers and the bounded query count.
"""

import re
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import event as sa_event

import tests.calendar_fixtures as fx
from app.extensions import db
from app.models import (
    AcademicStatus,
    AssignmentStatus,
    Course,
    Group,
    QuizStatus,
    User,
    UserRole,
    UserStatus,
)
from app.services.calendar_queries import MAX_RANGE_DAYS


def _setup(label="A"):
    """One operational Group with one actively assigned Teacher."""
    group = fx.hierarchy(label)
    teacher = fx.assign(group, "t@example.com")
    return group, teacher


# ===========================================================================
# Role authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    response = client.get(fx.TEACHER_CALENDAR)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_every_other_role_is_forbidden(app, client, role):
    with app.app_context():
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    assert client.get(fx.TEACHER_CALENDAR).status_code == 403


def test_a_suspended_teacher_cannot_sign_in_at_all(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.assign(group, "t@example.com", account_status=UserStatus.SUSPENDED.value)
    fx.login_as(client, "t@example.com")
    assert client.get(fx.TEACHER_CALENDAR).status_code in (302, 401)


def test_there_is_no_teacher_calendar_event_mutation_endpoint(app, client):
    """A Teacher reads center events and can do nothing else to one --
    not disabled in a template: no such route is registered."""
    with app.app_context():
        creator = fx.admin()
        group, _teacher = _setup()
        ep = fx.event(creator).public_id
    fx.login_as(client, "t@example.com")
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(fx.TEACHER_CALENDAR).status_code == 405
    for url in (
        "/teacher/calendar/events/new",
        f"/teacher/calendar/events/{ep}",
        f"/teacher/calendar/events/{ep}/edit",
        f"/teacher/calendar/events/{ep}/cancel",
    ):
        assert client.get(url).status_code == 404, url
        assert client.post(url).status_code in (404, 405), url


def test_a_teacher_cannot_reach_the_administrator_event_surfaces(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _teacher = _setup()
        ep = fx.event(creator).public_id
    fx.login_as(client, "t@example.com")
    for url in (
        fx.ADMIN_CALENDAR,
        fx.ADMIN_EVENT_NEW,
        fx.admin_event_detail(ep),
        fx.admin_event_edit(ep),
    ):
        assert client.get(url).status_code == 403, url
    assert client.post(
        fx.admin_event_cancel(ep), data={"confirm": "yes"}
    ).status_code == 403


def test_the_page_carries_no_event_management_control_at_all(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _teacher = _setup()
        fx.event(creator, title="Center holiday")
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "Center holiday" in html
    # No token, no POST form, no management wording anywhere.
    assert "event_state" not in html
    assert "New centre event" not in html
    assert "Cancel event" not in html
    assert 'method="post"' not in html.replace(
        # the shared portal header's logout form is the only POST on the page
        '<form method="post" action="/auth/logout">', ""
    )


# ===========================================================================
# Visibility -- classes
# ===========================================================================


def test_a_class_of_an_actively_assigned_operational_group_appears(app, client):
    with app.app_context():
        group, _teacher = _setup()
        fx.schedule(group, day_of_week=0, location="Room 4")
        course_title = db.session.get(Course, group.course_id).title
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert course_title in html
    assert "Room 4" in html
    assert ">Class<" in html


def test_a_removed_assignment_removes_the_class_immediately(app, client):
    with app.app_context():
        group, teacher = _setup()
        fx.schedule(group, day_of_week=0)
        course_title = db.session.get(Course, group.course_id).title
        group_id, teacher_id = group.id, teacher.id
    fx.login_as(client, "t@example.com")
    assert course_title in client.get(fx.teacher_month()).get_data(as_text=True)

    with app.app_context():
        fx.remove_assignment(
            db.session.get(Group, group_id), db.session.get(User, teacher_id)
        )
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert course_title not in html
    assert "Nothing in this range" in html


def test_a_teacher_never_sees_an_unassigned_groups_entries(app, client):
    with app.app_context():
        group, _teacher = _setup("A")
        other = fx.hierarchy("B")
        fx.assign(other, "stranger@example.com")
        fx.schedule(other, day_of_week=0)
        fx.assignment(other, title="Not mine")
        fx.quiz(other, title="Not my quiz")
        other_course = db.session.get(Course, other.course_id).title
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert other_course not in html
    assert "Not mine" not in html
    assert "Not my quiz" not in html


@pytest.mark.parametrize(
    "link", ["term_status", "level_status", "course_status", "group_status"]
)
def test_an_archived_link_anywhere_in_the_chain_hides_the_scoped_entries(
    app, client, link
):
    with app.app_context():
        group = fx.hierarchy("A", **{link: AcademicStatus.ARCHIVED.value})
        fx.assign(group, "t@example.com")
        fx.schedule(group, day_of_week=0)
        fx.assignment(group, title="Hidden essay")
        course_title = db.session.get(Course, group.course_id).title
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert course_title not in html
    assert "Hidden essay" not in html


# ===========================================================================
# Visibility -- published sources, including the ones not yet open
# ===========================================================================


def test_a_published_assignment_contributes_both_of_its_dates(app, client):
    with app.app_context():
        group, _teacher = _setup()
        fx.assignment(group, title="Essay one")
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert html.count("Essay one") == 2
    assert ">Assignment opens<" in html
    assert ">Assignment due<" in html


def test_a_teacher_sees_a_published_source_whose_opening_is_still_ahead(app, client):
    """The existing M01 "Scheduled" state is the Teacher's to see, and a
    Student's calendar deliberately hides the same row until it opens.

    The window is placed in the real future relative to the request's own
    reference moment, because "has this opened yet?" is answered against
    that moment and not against a date the test picked.
    """
    with app.app_context():
        group, _teacher = _setup()
        fx.enroll(group, "s@example.com")
        opens = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=400)
        fx.assignment(
            group,
            title="Opens later",
            opens_at=opens,
            due_at=opens + timedelta(days=3),
        )
        window = (
            f"?from={(opens - timedelta(days=2)).date().isoformat()}"
            f"&to={(opens + timedelta(days=5)).date().isoformat()}"
        )
    fx.login_as(client, "t@example.com")
    assert "Opens later" in client.get(
        fx.TEACHER_CALENDAR + window
    ).get_data(as_text=True)
    fx.login_as(client, "s@example.com")
    assert "Opens later" not in client.get(
        fx.STUDENT_CALENDAR + window
    ).get_data(as_text=True)


def test_a_draft_source_is_never_a_calendar_entry_for_anybody(app, client):
    with app.app_context():
        group, _teacher = _setup()
        fx.assignment(group, title="My draft", status=AssignmentStatus.DRAFT.value)
        fx.quiz(group, title="My draft quiz", status=QuizStatus.DRAFT.value)
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "My draft" not in html
    assert "My draft quiz" not in html
    assert "Nothing in this range" in html


def test_a_listening_activity_appears_once_and_not_as_a_quiz(app, client):
    with app.app_context():
        group, teacher = _setup()
        fx.listening(group, teacher, title="Airport dialogue")
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert html.count("Airport dialogue") == 2
    assert ">Listening opens<" in html
    assert ">Listening deadline<" in html
    assert ">Quiz opens<" not in html
    assert ">Quiz deadline<" not in html


def test_a_speaking_activity_is_not_on_the_calendar(app, client):
    with app.app_context():
        group, _teacher = _setup()
        fx.speaking_assignment(group, title="Describe your weekend")
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "Describe your weekend" not in html


# ===========================================================================
# Visibility -- center events
# ===========================================================================


def test_a_scheduled_center_event_is_visible_to_an_active_teacher(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _teacher = _setup()
        fx.event(creator, title="Center holiday", details="The center is closed.")
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "Center holiday" in html
    assert "The center is closed." in html
    assert ">Center event<" in html


def test_a_center_event_is_visible_to_a_teacher_with_no_assignment_at_all(app, client):
    with app.app_context():
        creator = fx.admin()
        fx.user("t@example.com", UserRole.TEACHER.value)
        fx.event(creator, title="Center holiday")
    fx.login_as(client, "t@example.com")
    assert "Center holiday" in client.get(fx.teacher_month()).get_data(as_text=True)


def test_a_cancelled_center_event_is_never_visible(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _teacher = _setup()
        fx.cancelled_event(creator, title="Called off")
        fx.event(creator, title="Still on")
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "Called off" not in html
    assert "Still on" in html


def test_a_center_event_carries_no_link_for_a_teacher(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _teacher = _setup()
        ep = fx.event(creator, title="Center holiday").public_id
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "Center holiday" in html
    assert ep not in html


# ===========================================================================
# Links are server-generated, authorized and re-authorized
# ===========================================================================


def test_each_source_links_to_its_own_authorized_teacher_surface(app, client):
    with app.app_context():
        group, teacher = _setup()
        fx.assignment(group, title="Essay one")
        quiz = fx.quiz(group, title="Unit quiz")
        _backing, activity = fx.listening(group, teacher, title="Airport dialogue")
        fx.schedule(group, day_of_week=0)
        gp = group.public_id
        expected = {
            f"/teacher/groups/{gp}/attendance",
            f"/teacher/groups/{gp}/assignments",
            f"/teacher/groups/{gp}/quizzes/{quiz.public_id}",
            f"/teacher/groups/{gp}/listening/{activity.public_id}",
        }
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    for url in expected:
        assert f'href="{url}"' in html, url


def test_every_followed_link_is_reachable_while_the_assignment_stands(app, client):
    with app.app_context():
        group, teacher = _setup()
        fx.assignment(group, title="Essay one")
        fx.quiz(group, title="Unit quiz")
        fx.listening(group, teacher, title="Airport dialogue")
        fx.schedule(group, day_of_week=0)
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    links = sorted(set(re.findall(r'href="(/teacher/groups/[^"]+)"', html)))
    assert links
    for link in links:
        assert client.get(link).status_code == 200, link


def test_a_link_stops_working_the_moment_the_assignment_is_removed(app, client):
    with app.app_context():
        group, teacher = _setup()
        fx.quiz(group, title="Unit quiz")
        group_id, teacher_id = group.id, teacher.id
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    link = re.search(r'href="(/teacher/groups/[^"]+/quizzes/[^"]+)"', html).group(1)
    assert client.get(link).status_code == 200
    with app.app_context():
        fx.remove_assignment(
            db.session.get(Group, group_id), db.session.get(User, teacher_id)
        )
    assert client.get(link).status_code == 404


def test_a_cross_group_or_forged_public_id_in_a_destination_fails_safely(app, client):
    with app.app_context():
        group, _teacher = _setup("A")
        other = fx.hierarchy("B")
        fx.assign(other, "stranger@example.com")
        other_quiz = fx.quiz(other, title="Not yours")
        gp, other_qp = group.public_id, other_quiz.public_id
    fx.login_as(client, "t@example.com")
    for url in (
        f"/teacher/groups/{gp}/quizzes/{other_qp}",
        f"/teacher/groups/{gp}/quizzes/00000000-0000-0000-0000-000000000000",
        "/teacher/groups/not-a-group/attendance",
    ):
        assert client.get(url).status_code == 404, url


# ===========================================================================
# Range navigation, empty state, headers, navigation entry
# ===========================================================================


def test_the_default_view_is_the_current_month_in_app_timezone(app, client):
    from app.services.schedule_occurrences import app_now

    with app.app_context():
        fx.user("t@example.com", UserRole.TEACHER.value)
        today = app_now(app.config["APP_TIMEZONE"]).date()
    fx.login_as(client, "t@example.com")
    html = client.get(fx.TEACHER_CALENDAR).get_data(as_text=True)
    assert f'value="{today.replace(day=1).isoformat()}"' in html


def test_navigation_really_moves_the_entries(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _teacher = _setup()
        fx.event(creator, title="April thing", event_date=date(2026, 4, 15))
        fx.event(creator, title="May thing", event_date=date(2026, 5, 15))
    fx.login_as(client, "t@example.com")
    may = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "May thing" in may and "April thing" not in may
    april = client.get(
        fx.TEACHER_CALENDAR + "?from=2026-04-01&to=2026-04-30"
    ).get_data(as_text=True)
    assert "April thing" in april and "May thing" not in april


def test_a_malformed_or_oversized_range_normalises_safely(app, client):
    with app.app_context():
        fx.user("t@example.com", UserRole.TEACHER.value)
    fx.login_as(client, "t@example.com")
    for query in ("?from=oops&to=also-oops", "?from=2026-01-01&to=2026-12-31"):
        response = client.get(fx.TEACHER_CALENDAR + query)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "nearest range it can" in html
        days = int(re.search(r"\((\d+) days?\)", html).group(1))
        assert days <= MAX_RANGE_DAYS


def test_a_teacher_with_nothing_at_all_sees_a_clear_empty_state(app, client):
    with app.app_context():
        fx.user("t@example.com", UserRole.TEACHER.value)
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "Nothing in this range" in html
    assert "0 entries" in html


def test_the_response_is_private_and_no_store(app, client):
    with app.app_context():
        fx.user("t@example.com", UserRole.TEACHER.value)
    fx.login_as(client, "t@example.com")
    response = client.get(fx.teacher_month())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.headers["Vary"]


def test_the_teacher_dashboard_carries_a_calendar_entry(app, client):
    with app.app_context():
        fx.user("t@example.com", UserRole.TEACHER.value)
    fx.login_as(client, "t@example.com")
    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert f'href="{fx.TEACHER_CALENDAR}"' in html
    assert ">Calendar<" in html


# ===========================================================================
# Nothing private reaches the markup
# ===========================================================================


def test_no_student_level_data_reaches_the_teacher_calendar(app, client):
    with app.app_context():
        group, teacher = _setup()
        fx.enroll(group, "learner@example.com", name="A Learner")
        fx.assignment(group, title="Essay one")
        fx.quiz(group, title="Unit quiz")
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    assert "learner@example.com" not in html
    assert "A Learner" not in html


def test_no_internal_numeric_id_reaches_the_markup(app, client):
    with app.app_context():
        creator = fx.admin()
        group, teacher = _setup()
        assignment = fx.assignment(group, title="Essay one")
        quiz = fx.quiz(group, title="Unit quiz")
        _backing, activity = fx.listening(group, teacher, title="Airport dialogue")
        schedule = fx.schedule(group, day_of_week=0)
        row = fx.event(creator, title="Center holiday")
        internal_ids = {
            group.id, teacher.id, creator.id, assignment.id, quiz.id, activity.id,
            schedule.id, row.id, group.course_id, group.academic_term_id,
        }
    fx.login_as(client, "t@example.com")
    html = client.get(fx.teacher_month()).get_data(as_text=True)
    for segment in re.findall(
        r"/teacher/groups/([^\"'/?# ]+)(?:/(?:quizzes|listening)/([^\"'/?# ]+))?", html
    ):
        for part in segment:
            if part:
                assert not part.isdigit(), part
                assert len(part) == 36, part
    values = set(re.findall(r'value="([^"]*)"', html))
    assert not (values & {str(i) for i in internal_ids})


# ===========================================================================
# Bounded, and free of N+1
# ===========================================================================


def test_the_calendar_cost_does_not_grow_with_the_number_of_entries(app, client):
    def query_count(size):
        with app.app_context():
            db.drop_all()
            db.create_all()
            creator = fx.admin()
            group, teacher = _setup()
            for index in range(size):
                fx.assignment(group, title=f"Essay {index:03d}")
                fx.quiz(group, title=f"Quiz {index:03d}")
                fx.listening(group, teacher, title=f"Listening {index:03d}")
                fx.event(creator, title=f"Event {index:03d}")
                fx.schedule(
                    group,
                    day_of_week=index % 7,
                    start_time=time(8 + (index % 5), 0),
                    end_time=time(9 + (index % 5), 0),
                )
        fx.login_as(client, "t@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            sa_event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.teacher_month()).status_code == 200
        finally:
            with app.app_context():
                sa_event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(6)
