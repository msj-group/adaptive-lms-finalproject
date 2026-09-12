"""Phase 4 / M10 -- the Student calendar surface.

Covers the role authorization, the complete visibility formula (active
enrollment, operational academic chain, published and already-open
sources, scheduled center events), the immediate effect of a withdrawn
enrollment / archived chain / cancelled event, the absence of any
mutation endpoint, the server-generated authorized links, the range
navigation and normalisation, the response headers, the bounded query
count, and the absence of internal ids and private data in the markup.
"""

import re
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import event as sa_event

import tests.calendar_fixtures as fx
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
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
    """One operational Group with one actively enrolled Student."""
    group = fx.hierarchy(label)
    student = fx.enroll(group, "s@example.com")
    return group, student


# ===========================================================================
# Role authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    response = client.get(fx.STUDENT_CALENDAR)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.TEACHER.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_every_other_role_is_forbidden(app, client, role):
    with app.app_context():
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    assert client.get(fx.STUDENT_CALENDAR).status_code == 403


def test_a_suspended_student_cannot_sign_in_at_all(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com", account_status=UserStatus.SUSPENDED.value)
        fx.schedule(group, day_of_week=0)
    fx.login_as(client, "s@example.com")
    response = client.get(fx.STUDENT_CALENDAR)
    assert response.status_code in (302, 401)


def test_there_is_no_student_mutation_endpoint_anywhere_on_the_calendar(app, client):
    """No event creation, no RSVP, no acknowledgement, no dismiss -- and
    no Student route that can touch a ``calendar_events`` row at all."""
    with app.app_context():
        creator = fx.admin()
        group, _student = _setup()
        row = fx.event(creator)
        ep = row.public_id
    fx.login_as(client, "s@example.com")
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(fx.STUDENT_CALENDAR).status_code == 405
    # And no Student-side event URL exists to aim a write at.
    for url in (
        f"/student/calendar/events/{ep}",
        f"/student/calendar/events/{ep}/cancel",
        "/student/calendar/events/new",
    ):
        assert client.get(url).status_code == 404, url
        assert client.post(url).status_code in (404, 405), url


def test_no_student_route_can_reach_the_administrator_event_surfaces(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _student = _setup()
        ep = fx.event(creator).public_id
    fx.login_as(client, "s@example.com")
    for url in (
        fx.ADMIN_CALENDAR,
        fx.ADMIN_EVENT_NEW,
        fx.admin_event_detail(ep),
        fx.admin_event_edit(ep),
    ):
        assert client.get(url).status_code == 403, url
    assert client.post(fx.admin_event_cancel(ep), data={"confirm": "yes"}).status_code == 403


# ===========================================================================
# Visibility -- classes
# ===========================================================================


def test_a_class_of_an_actively_enrolled_operational_group_appears(app, client):
    with app.app_context():
        group, _student = _setup()
        fx.schedule(group, day_of_week=0, location="Room 4")
        course_title = db.session.get(Course, group.course_id).title
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert course_title in html
    assert "Room 4" in html
    assert ">Class<" in html


def test_a_withdrawn_enrollment_removes_the_class_immediately(app, client):
    with app.app_context():
        group, student = _setup()
        fx.schedule(group, day_of_week=0)
        course_title = db.session.get(Course, group.course_id).title
        group_id, student_id = group.id, student.id
    fx.login_as(client, "s@example.com")
    assert course_title in client.get(fx.student_month()).get_data(as_text=True)

    with app.app_context():
        fx.withdraw_enrollment(
            db.session.get(Group, group_id), db.session.get(User, student_id)
        )
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert course_title not in html
    assert "Nothing in this range" in html


def test_another_groups_class_never_appears(app, client):
    with app.app_context():
        group, _student = _setup("A")
        other = fx.hierarchy("B")
        fx.enroll(other, "stranger@example.com")
        fx.schedule(other, day_of_week=0)
        other_course = db.session.get(Course, other.course_id).title
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert other_course not in html
    assert "Nothing in this range" in html


@pytest.mark.parametrize(
    "link", ["term_status", "level_status", "course_status", "group_status"]
)
def test_an_archived_link_anywhere_in_the_chain_hides_the_scoped_entries(
    app, client, link
):
    with app.app_context():
        group = fx.hierarchy("A", **{link: AcademicStatus.ARCHIVED.value})
        fx.enroll(group, "s@example.com")
        fx.schedule(group, day_of_week=0)
        fx.assignment(group, title="Hidden essay")
        fx.quiz(group, title="Hidden quiz")
        course_title = db.session.get(Course, group.course_id).title
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert course_title not in html
    assert "Hidden essay" not in html
    assert "Hidden quiz" not in html


def test_archiving_a_link_after_the_page_was_rendered_takes_effect_at_once(app, client):
    with app.app_context():
        group, _student = _setup()
        fx.assignment(group, title="Essay one")
        term_id = group.academic_term_id
    fx.login_as(client, "s@example.com")
    assert "Essay one" in client.get(fx.student_month()).get_data(as_text=True)
    with app.app_context():
        fx.archive(db.session.get(AcademicTerm, term_id))
    assert "Essay one" not in client.get(fx.student_month()).get_data(as_text=True)


# ===========================================================================
# Visibility -- assignments, quizzes, listening
# ===========================================================================


def test_a_published_and_open_assignment_contributes_both_of_its_dates(app, client):
    with app.app_context():
        group, _student = _setup()
        fx.assignment(group, title="Essay one")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert html.count("Essay one") == 2
    assert ">Assignment opens<" in html
    assert ">Assignment due<" in html


def test_a_draft_assignment_never_appears(app, client):
    with app.app_context():
        group, _student = _setup()
        fx.assignment(group, title="Secret draft", status=AssignmentStatus.DRAFT.value)
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "Secret draft" not in html
    assert "Nothing in this range" in html


def test_a_published_assignment_that_has_not_opened_yet_never_appears(app, client):
    """The existing M01 rule: a Student cannot see a published Assignment
    before ``opens_at`` at all, so neither of its dates is a calendar
    entry for them yet."""
    with app.app_context():
        group, _student = _setup()
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=400)
        fx.assignment(
            group,
            title="Not yet open",
            opens_at=future,
            due_at=future + timedelta(days=1),
            published_at=datetime(2026, 5, 1, 8, 0),
        )
        window = (
            f"?from={(future - timedelta(days=2)).date().isoformat()}"
            f"&to={(future + timedelta(days=2)).date().isoformat()}"
        )
    fx.login_as(client, "s@example.com")
    assert "Not yet open" not in client.get(
        fx.STUDENT_CALENDAR + window
    ).get_data(as_text=True)


def test_a_published_quiz_contributes_its_opening_and_deadline(app, client):
    with app.app_context():
        group, _student = _setup()
        fx.quiz(group, title="Unit quiz")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert html.count("Unit quiz") == 2
    assert ">Quiz opens<" in html
    assert ">Quiz deadline<" in html


def test_a_draft_quiz_never_appears(app, client):
    with app.app_context():
        group, _student = _setup()
        fx.quiz(group, title="Draft quiz", status=QuizStatus.DRAFT.value)
    fx.login_as(client, "s@example.com")
    assert "Draft quiz" not in client.get(fx.student_month()).get_data(as_text=True)


def test_a_listening_activity_appears_once_and_not_as_a_quiz(app, client):
    with app.app_context():
        group, _student = _setup()
        teacher = fx.assign(group, "t@example.com")
        fx.listening(group, teacher, title="Airport dialogue")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert html.count("Airport dialogue") == 2
    assert ">Listening opens<" in html
    assert ">Listening deadline<" in html
    assert ">Quiz opens<" not in html
    assert ">Quiz deadline<" not in html


def test_a_speaking_activity_is_not_on_the_calendar(app, client):
    with app.app_context():
        group, _student = _setup()
        fx.speaking_assignment(group, title="Describe your weekend")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "Describe your weekend" not in html
    assert "Nothing in this range" in html


# ===========================================================================
# Visibility -- center events
# ===========================================================================


def test_a_scheduled_center_event_is_visible_to_an_active_student(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _student = _setup()
        fx.event(creator, title="Center holiday", details="The center is closed.",
                 location="Whole center")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "Center holiday" in html
    assert "The center is closed." in html
    assert "Whole center" in html
    assert ">Center event<" in html
    assert "All day" in html


def test_a_center_event_is_visible_to_a_student_with_no_enrollment_at_all(app, client):
    """A Student between terms still belongs to the center."""
    with app.app_context():
        creator = fx.admin()
        fx.user("s@example.com", UserRole.STUDENT.value)
        fx.event(creator, title="Center holiday")
    fx.login_as(client, "s@example.com")
    assert "Center holiday" in client.get(fx.student_month()).get_data(as_text=True)


def test_a_cancelled_center_event_is_never_visible(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _student = _setup()
        fx.cancelled_event(creator, title="Called off")
        fx.event(creator, title="Still on")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "Called off" not in html
    assert "Still on" in html


def test_a_center_event_carries_no_link(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _student = _setup()
        row = fx.event(creator, title="Center holiday")
        ep = row.public_id
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "Center holiday" in html
    assert ep not in html
    assert "/calendar/events/" not in html


def test_a_timed_center_event_shows_its_local_window(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _student = _setup()
        fx.timed_event(
            creator,
            title="Parents evening",
            event_date=date(2026, 5, 20),
            start_time=time(17, 30),
            end_time=time(19, 0),
        )
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "17:30" in html and "19:00" in html
    assert app.config["APP_TIMEZONE"] in html


# ===========================================================================
# Links are server-generated, authorized and re-authorized
# ===========================================================================


def test_each_source_links_to_its_own_authorized_student_surface(app, client):
    with app.app_context():
        group, _student = _setup()
        teacher = fx.assign(group, "t@example.com")
        assignment = fx.assignment(group, title="Essay one")
        quiz = fx.quiz(group, title="Unit quiz")
        _backing, activity = fx.listening(group, teacher, title="Airport dialogue")
        fx.schedule(group, day_of_week=0)
        gp = group.public_id
        expected = {
            f"/student/groups/{gp}/units",
            f"/student/groups/{gp}/assignments/{assignment.public_id}",
            f"/student/groups/{gp}/quizzes/{quiz.public_id}",
            f"/student/groups/{gp}/listening/{activity.public_id}",
        }
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    for url in expected:
        assert f'href="{url}"' in html, url


def test_every_followed_link_is_reachable_while_the_enrollment_stands(app, client):
    with app.app_context():
        group, _student = _setup()
        teacher = fx.assign(group, "t@example.com")
        fx.assignment(group, title="Essay one")
        fx.quiz(group, title="Unit quiz")
        fx.listening(group, teacher, title="Airport dialogue")
        fx.schedule(group, day_of_week=0)
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    links = sorted(set(re.findall(r'href="(/student/groups/[^"]+)"', html)))
    assert links
    for link in links:
        assert client.get(link).status_code == 200, link


def test_a_link_stops_working_the_moment_the_enrollment_is_withdrawn(app, client):
    """The destination re-authorizes from scratch, so a page rendered a
    minute ago can only ever lead to the ordinary non-disclosing 404."""
    with app.app_context():
        group, student = _setup()
        fx.assignment(group, title="Essay one")
        group_id, student_id = group.id, student.id
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    link = re.search(r'href="(/student/groups/[^"]+/assignments/[^"]+)"', html).group(1)
    assert client.get(link).status_code == 200
    with app.app_context():
        fx.withdraw_enrollment(
            db.session.get(Group, group_id), db.session.get(User, student_id)
        )
    assert client.get(link).status_code == 404


def test_a_forged_or_cross_group_public_id_in_a_destination_fails_safely(app, client):
    with app.app_context():
        group, _student = _setup("A")
        other = fx.hierarchy("B")
        fx.enroll(other, "stranger@example.com")
        other_assignment = fx.assignment(other, title="Not yours")
        gp, other_ap = group.public_id, other_assignment.public_id
    fx.login_as(client, "s@example.com")
    for url in (
        f"/student/groups/{gp}/assignments/{other_ap}",
        f"/student/groups/{gp}/assignments/00000000-0000-0000-0000-000000000000",
        "/student/groups/not-a-group/units",
    ):
        assert client.get(url).status_code == 404, url


# ===========================================================================
# Range navigation and normalisation
# ===========================================================================


def test_the_default_view_is_the_current_month_in_app_timezone(app, client):
    from app.services.schedule_occurrences import app_now

    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
        today = app_now(app.config["APP_TIMEZONE"]).date()
    fx.login_as(client, "s@example.com")
    html = client.get(fx.STUDENT_CALENDAR).get_data(as_text=True)
    assert f'value="{today.replace(day=1).isoformat()}"' in html
    assert today.strftime("%B %Y") in html


def test_previous_and_next_links_are_normalised_public_urls(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "from=2026-04-01&amp;to=2026-04-30" in html
    assert "from=2026-06-01&amp;to=2026-06-30" in html
    # And following one really shows that month.
    april = client.get(fx.STUDENT_CALENDAR + "?from=2026-04-01&to=2026-04-30")
    assert "01 April 2026" in april.get_data(as_text=True)


def test_navigation_really_moves_the_entries(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _student = _setup()
        fx.event(creator, title="April thing", event_date=date(2026, 4, 15))
        fx.event(creator, title="May thing", event_date=date(2026, 5, 15))
        fx.event(creator, title="June thing", event_date=date(2026, 6, 15))
    fx.login_as(client, "s@example.com")
    may = client.get(fx.student_month()).get_data(as_text=True)
    assert "May thing" in may and "April thing" not in may and "June thing" not in may
    april = client.get(
        fx.STUDENT_CALENDAR + "?from=2026-04-01&to=2026-04-30"
    ).get_data(as_text=True)
    assert "April thing" in april and "May thing" not in april
    june = client.get(
        fx.STUDENT_CALENDAR + "?from=2026-06-01&to=2026-06-30"
    ).get_data(as_text=True)
    assert "June thing" in june and "May thing" not in june


@pytest.mark.parametrize(
    "query",
    [
        "?from=oops&to=2026-05-31",
        "?from=2026-05-01&to=nonsense",
        "?from=2026-02-30&to=2026-03-05",
        "?from=2026-05-01",
        "?to=2026-05-31",
        "?from=%00&to=%00",
        "?from=1;DROP TABLE calendar_events&to=2026-05-31",
    ],
)
def test_a_malformed_range_is_rejected_safely_and_shows_the_default(app, client, query):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    response = client.get(fx.STUDENT_CALENDAR + query)
    assert response.status_code == 200
    assert "nearest range it can" in response.get_data(as_text=True)
    with app.app_context():
        # Nothing was destroyed by the injection attempt.
        assert db.session.execute(
            db.text("SELECT count(*) FROM calendar_events")
        ).scalar() == 0


def test_a_reversed_range_is_normalised_rather_than_erroring(app, client):
    with app.app_context():
        creator = fx.admin()
        fx.user("s@example.com", UserRole.STUDENT.value)
        fx.event(creator, title="Mid-May thing", event_date=date(2026, 5, 15))
    fx.login_as(client, "s@example.com")
    html = client.get(
        fx.STUDENT_CALENDAR + "?from=2026-05-31&to=2026-05-01"
    ).get_data(as_text=True)
    assert "Mid-May thing" in html
    assert "01 May 2026" in html and "31 May 2026" in html


def test_an_oversized_range_is_capped_at_the_documented_maximum(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    html = client.get(
        fx.STUDENT_CALENDAR + "?from=2026-01-01&to=2026-12-31"
    ).get_data(as_text=True)
    assert f"({MAX_RANGE_DAYS} days)" in html
    assert "nearest range it can" in html


def test_an_absurd_range_is_normalised_and_never_scans_unbounded(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    for query in ("?from=0001-01-01&to=9999-12-31", "?from=9999-01-01&to=9999-12-31"):
        response = client.get(fx.STUDENT_CALENDAR + query)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        days = int(re.search(r"\((\d+) days?\)", html).group(1))
        assert days <= MAX_RANGE_DAYS


# ===========================================================================
# Empty state, headers, navigation entry
# ===========================================================================


def test_a_student_with_nothing_at_all_sees_a_clear_empty_state(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "Nothing in this range" in html
    assert "0 entries" in html


def test_the_response_is_private_and_no_store(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    response = client.get(fx.student_month())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.headers["Vary"]


def test_the_student_portal_nav_carries_a_calendar_entry(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert f'href="{fx.STUDENT_CALENDAR}"' in html
    assert ">Calendar<" in html


def test_the_calendar_page_marks_its_own_nav_entry_active(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    html = client.get(fx.STUDENT_CALENDAR).get_data(as_text=True)
    # The one active nav link on this page is the Calendar one.
    active = re.findall(
        r'class="portal-nav__link portal-nav__link--active"\s+href="([^"]+)"', html
    )
    assert active == [fx.STUDENT_CALENDAR]


# ===========================================================================
# Nothing internal or private reaches the markup
# ===========================================================================


def test_no_internal_numeric_id_reaches_the_markup(app, client):
    with app.app_context():
        creator = fx.admin()
        group, student = _setup()
        teacher = fx.assign(group, "t@example.com")
        assignment = fx.assignment(group, title="Essay one")
        quiz = fx.quiz(group, title="Unit quiz")
        _backing, activity = fx.listening(group, teacher, title="Airport dialogue")
        schedule = fx.schedule(group, day_of_week=0)
        row = fx.event(creator, title="Center holiday")
        internal_ids = {
            group.id, student.id, teacher.id, creator.id, assignment.id, quiz.id,
            activity.id, schedule.id, row.id, group.course_id, group.academic_term_id,
        }
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    # Every object-identity path segment the page emits is a public id.
    for segment in re.findall(
        r"/student/groups/([^\"'/?# ]+)(?:/(?:assignments|quizzes|listening)/([^\"'/?# ]+))?",
        html,
    ):
        for part in segment:
            if part:
                assert not part.isdigit(), part
                assert len(part) == 36, part
    # And no internal id appears as a form value.
    values = set(re.findall(r'value="([^"]*)"', html))
    assert not (values & {str(i) for i in internal_ids})


def test_no_private_or_author_only_data_reaches_the_markup(app, client):
    with app.app_context():
        creator = fx.admin("boss@example.com", name="Head Of Center")
        group, _student = _setup()
        fx.assignment(group, title="Essay one")
        fx.event(creator, title="Center holiday")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    for forbidden in (
        "boss@example.com",
        "Head Of Center",
        "version",
        "cancelled_at",
        "created_by",
        "Do the work.",  # the assignment's instructions are not a calendar field
    ):
        assert forbidden not in html, forbidden


def test_no_other_students_data_is_reachable_from_the_calendar(app, client):
    with app.app_context():
        group, _student = _setup()
        fx.enroll(group, "classmate@example.com", name="Someone Else")
        fx.assignment(group, title="Essay one")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_month()).get_data(as_text=True)
    assert "classmate@example.com" not in html
    assert "Someone Else" not in html


# ===========================================================================
# Bounded, and free of N+1
# ===========================================================================


def test_the_calendar_cost_does_not_grow_with_the_number_of_entries(app, client):
    def query_count(size):
        with app.app_context():
            db.drop_all()
            db.create_all()
            creator = fx.admin()
            group, _student = _setup()
            teacher = fx.assign(group, "t@example.com")
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
        fx.login_as(client, "s@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            sa_event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.student_month()).status_code == 200
        finally:
            with app.app_context():
                sa_event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(6)


def test_a_group_scoped_read_costs_the_same_for_one_group_and_for_many(app, client):
    def query_count(groups):
        with app.app_context():
            db.drop_all()
            db.create_all()
            student = fx.user("s@example.com", UserRole.STUDENT.value)
            for index in range(groups):
                group = fx.hierarchy(f"G{index}")
                fx.enroll_existing(group, student)
                fx.schedule(group, day_of_week=index % 7)
                fx.assignment(group, title=f"Essay {index}")
        fx.login_as(client, "s@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            sa_event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.student_month()).status_code == 200
        finally:
            with app.app_context():
                sa_event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(5)


def test_the_calendar_uses_no_count_for_range_navigation(app, client):
    with app.app_context():
        creator = fx.admin()
        group, _student = _setup()
        fx.event(creator)
        fx.assignment(group)
    fx.login_as(client, "s@example.com")

    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append(" ".join(statement.split()).upper())

    with app.app_context():
        sa_event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(fx.student_month()).status_code == 200
    finally:
        with app.app_context():
            sa_event.remove(db.engine, "before_cursor_execute", _rec)

    calendar_statements = [
        s for s in recorded
        if "CALENDAR_EVENTS" in s or "SCHEDULES" in s or "ASSIGNMENTS" in s or "QUIZZES" in s
    ]
    assert calendar_statements
    assert not any("COUNT(" in s for s in calendar_statements)
