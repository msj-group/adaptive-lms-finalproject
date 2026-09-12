"""Phase 4 / M10 -- the Administrator calendar and center-event
management.

Covers the role authorization, the center-wide read, the cancelled-event
asymmetry (visible here, never to a Student or Teacher), the complete
create / edit / cancel lifecycle with CSRF and purpose-specific signed
tokens, the no-op edit, the permanent immutability of a cancelled event,
the deterministic lock chain, the absence of every forbidden control,
the response headers and the absence of any student-level data.
"""

import re
from datetime import date, time

import pytest
from sqlalchemy import event as sa_event

import tests.calendar_fixtures as fx
from app.extensions import db
from app.models import (
    CALENDAR_EVENT_DETAILS_MAX_LENGTH,
    CALENDAR_EVENT_TITLE_MAX_LENGTH,
    CalendarEvent,
    CalendarEventStatus,
    Course,
    User,
    UserRole,
    UserStatus,
)

_SCHEDULED = CalendarEventStatus.SCHEDULED.value
_CANCELLED = CalendarEventStatus.CANCELLED.value


def _admin(client, app, email="admin@example.com"):
    with app.app_context():
        row = fx.admin(email)
        actor_id = row.id
    fx.login_as(client, email)
    return actor_id


def _stored(public_id):
    return CalendarEvent.query.filter_by(public_id=public_id).one()


# ===========================================================================
# Role authorization
# ===========================================================================


@pytest.mark.parametrize(
    "url",
    [fx.ADMIN_CALENDAR, fx.ADMIN_EVENT_NEW],
)
def test_anonymous_is_redirected_to_login(app, client, url):
    response = client.get(url)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.STUDENT.value, UserRole.TEACHER.value, UserRole.RESEARCHER.value],
)
def test_every_other_role_is_forbidden_on_every_route(app, client, role):
    with app.app_context():
        creator = fx.admin("boss@example.com")
        ep = fx.event(creator).public_id
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    for url in (
        fx.ADMIN_CALENDAR,
        fx.ADMIN_EVENT_NEW,
        fx.admin_event_detail(ep),
        fx.admin_event_edit(ep),
    ):
        assert client.get(url).status_code == 403, url
    assert client.post(fx.ADMIN_EVENT_NEW).status_code == 403
    assert client.post(fx.admin_event_edit(ep)).status_code == 403
    assert client.post(fx.admin_event_cancel(ep)).status_code == 403


def test_a_researcher_reaches_no_calendar_surface_at_all(app, client):
    with app.app_context():
        creator = fx.admin("boss@example.com")
        ep = fx.event(creator).public_id
        fx.user("r@example.com", UserRole.RESEARCHER.value)
    fx.login_as(client, "r@example.com")
    for url in (
        fx.STUDENT_CALENDAR,
        fx.TEACHER_CALENDAR,
        fx.ADMIN_CALENDAR,
        fx.admin_event_detail(ep),
    ):
        assert client.get(url).status_code == 403, url


def test_an_anonymous_visitor_reaches_no_calendar_surface_at_all(app, client):
    with app.app_context():
        creator = fx.admin("boss@example.com")
        ep = fx.event(creator).public_id
    for url in (
        fx.STUDENT_CALENDAR,
        fx.TEACHER_CALENDAR,
        fx.ADMIN_CALENDAR,
        fx.admin_event_detail(ep),
    ):
        response = client.get(url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"], url


# ===========================================================================
# The center-wide read
# ===========================================================================


def test_the_administrator_calendar_spans_the_whole_center(app, client):
    with app.app_context():
        creator = fx.admin("boss@example.com")
        first, _s1, t1 = fx.setup_group("A")
        second, _s2, _t2 = fx.setup_group("B")
        fx.schedule(first, day_of_week=0)
        fx.schedule(second, day_of_week=1)
        fx.assignment(first, title="Essay A")
        fx.quiz(second, title="Quiz B")
        fx.listening(first, t1, title="Listening A")
        fx.event(creator, title="Center holiday")
        titles = [
            db.session.get(Course, first.course_id).title,
            db.session.get(Course, second.course_id).title,
        ]
    fx.login_as(client, "boss@example.com")
    html = client.get(fx.admin_month()).get_data(as_text=True)
    for needle in titles + ["Essay A", "Quiz B", "Listening A", "Center holiday"]:
        assert needle in html, needle


def test_an_archived_chain_is_excluded_from_the_administrator_calendar_too(app, client):
    with app.app_context():
        creator = fx.admin("boss@example.com")
        group = fx.hierarchy("A", group_status="archived")
        fx.schedule(group, day_of_week=0)
        fx.assignment(group, title="Archived essay")
        course_title = db.session.get(Course, group.course_id).title
    fx.login_as(client, "boss@example.com")
    html = client.get(fx.admin_month()).get_data(as_text=True)
    assert course_title not in html
    assert "Archived essay" not in html


def test_a_draft_source_never_appears_on_the_administrator_calendar(app, client):
    with app.app_context():
        fx.admin("boss@example.com")
        group, _s, _t = fx.setup_group("A")
        fx.assignment(group, title="Draft essay", status="draft")
        fx.quiz(group, title="Draft quiz", status="draft")
    fx.login_as(client, "boss@example.com")
    html = client.get(fx.admin_month()).get_data(as_text=True)
    assert "Draft essay" not in html
    assert "Draft quiz" not in html


def test_a_cancelled_event_is_visible_here_and_clearly_marked(app, client):
    with app.app_context():
        creator = fx.admin("boss@example.com")
        fx.cancelled_event(creator, title="Called off", event_date=date(2026, 5, 14))
    fx.login_as(client, "boss@example.com")
    html = client.get(fx.admin_month()).get_data(as_text=True)
    assert "Called off" in html
    assert ">Cancelled<" in html


def test_no_student_level_data_is_exposed_by_the_administrator_calendar(app, client):
    with app.app_context():
        fx.admin("boss@example.com")
        group, student, teacher = fx.setup_group("A")
        fx.enroll(group, "learner@example.com", name="A Learner")
        fx.assignment(group, title="Essay A")
        fx.quiz(group, title="Quiz A")
        fx.schedule(group, day_of_week=0)
    fx.login_as(client, "boss@example.com")
    html = client.get(fx.admin_month()).get_data(as_text=True)
    for forbidden in (
        "learner@example.com",
        "A Learner",
        "student.a@example.com",
        "Enrollment",
        "attempt",
        "submission",
    ):
        assert forbidden not in html, forbidden


def test_academic_entries_carry_no_link_and_class_entries_link_to_the_schedules_page(
    app, client
):
    """No Administrator surface for an Assignment, a Quiz or a Listening
    activity exists in this project, so those entries deliberately have
    no destination. A class entry leads to the Group's schedules page,
    which is the Administrator surface that owns a recurring class."""
    with app.app_context():
        fx.admin("boss@example.com")
        group, _s, teacher = fx.setup_group("A")
        fx.schedule(group, day_of_week=0)
        assignment = fx.assignment(group, title="Essay A")
        quiz = fx.quiz(group, title="Quiz A")
        _backing, activity = fx.listening(group, teacher, title="Listening A")
        gp = group.public_id
        absent = [assignment.public_id, quiz.public_id, activity.public_id]
    fx.login_as(client, "boss@example.com")
    html = client.get(fx.admin_month()).get_data(as_text=True)
    assert f'href="/admin/groups/{gp}/schedules"' in html
    for public_id in absent:
        assert public_id not in html, public_id


def test_the_administrator_response_is_private_and_no_store(app, client):
    with app.app_context():
        creator = fx.admin("boss@example.com")
        ep = fx.event(creator).public_id
    fx.login_as(client, "boss@example.com")
    for url in (
        fx.admin_month(),
        fx.ADMIN_EVENT_NEW,
        fx.admin_event_detail(ep),
        fx.admin_event_edit(ep),
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in response.headers["Vary"], url


def test_the_administrator_navigation_carries_a_calendar_entry(app, client):
    with app.app_context():
        fx.admin("boss@example.com")
    fx.login_as(client, "boss@example.com")
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert f'href="{fx.ADMIN_CALENDAR}"' in html
    assert ">Calendar<" in html


# ===========================================================================
# Creation
# ===========================================================================


def test_an_administrator_creates_an_all_day_center_event(app, client):
    _admin(client, app)
    ep = fx.create_event(client, start_time="", end_time="")
    assert ep is not None
    with app.app_context():
        row = _stored(ep)
        assert row.title == "Staff meeting"
        assert row.details == "Everyone attends."
        assert row.event_date == date(2026, 5, 18)
        assert row.start_time is None and row.end_time is None
        assert row.location == "Main hall"
        assert row.status == _SCHEDULED
        assert row.cancelled_at is None
        assert row.version == 1
        assert row.created_at == row.updated_at


def test_an_administrator_creates_a_timed_center_event(app, client):
    _admin(client, app)
    ep = fx.create_event(client, start_time="14:00", end_time="15:30")
    assert ep is not None
    with app.app_context():
        row = _stored(ep)
        assert (row.start_time, row.end_time) == (time(14, 0), time(15, 30))


def test_the_creator_is_the_acting_administrator_and_is_immutable(app, client):
    actor_id = _admin(client, app)
    ep = fx.create_event(client)
    with app.app_context():
        assert _stored(ep).created_by_id == actor_id
    # A second Administrator editing it does not become its creator.
    with app.app_context():
        fx.admin("second@example.com")
    fx.login_as(client, "second@example.com")
    fx.edit_event(client, ep, title="Retitled by somebody else")
    with app.app_context():
        row = _stored(ep)
        assert row.title == "Retitled by somebody else"
        assert row.created_by_id == actor_id


def test_a_new_event_is_immediately_visible_to_students_and_teachers(app, client):
    _admin(client, app)
    with app.app_context():
        group, _student, _teacher = fx.setup_group("A")
    fx.login_as(client, "admin@example.com")
    ep = fx.create_event(client, title="Open day")
    assert ep is not None
    fx.login_as(client, "student.a@example.com")
    assert "Open day" in client.get(fx.student_month()).get_data(as_text=True)
    fx.login_as(client, "teacher.a@example.com")
    assert "Open day" in client.get(fx.teacher_month()).get_data(as_text=True)


@pytest.mark.parametrize(
    "overrides, message_fragment",
    [
        ({"title": ""}, "Give this event a title."),
        ({"title": "   "}, "Give this event a title."),
        ({"event_date": ""}, "Choose the date this event happens on."),
        ({"event_date": "not-a-date"}, "Choose the date this event happens on."),
        (
            {"start_time": "09:00", "end_time": ""},
            "Give both a start time and an end time",
        ),
        (
            {"start_time": "", "end_time": "11:00"},
            "Give both a start time and an end time",
        ),
        (
            {"start_time": "11:00", "end_time": "09:00"},
            "The end time must be after the start time",
        ),
        (
            {"start_time": "09:00", "end_time": "09:00"},
            "The end time must be after the start time",
        ),
    ],
)
def test_an_invalid_event_is_rejected_with_a_clear_message(
    app, client, overrides, message_fragment
):
    _admin(client, app)
    token = fx.token_from(client, fx.ADMIN_EVENT_NEW)
    response = client.post(
        fx.ADMIN_EVENT_NEW,
        data=fx.event_form_data(token=token, **overrides),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert message_fragment in response.get_data(as_text=True)
    with app.app_context():
        assert CalendarEvent.query.count() == 0


def test_an_oversized_title_or_details_is_rejected(app, client):
    _admin(client, app)
    assert fx.create_event(client, title="x" * (CALENDAR_EVENT_TITLE_MAX_LENGTH + 1)) is None
    assert (
        fx.create_event(client, details="y" * (CALENDAR_EVENT_DETAILS_MAX_LENGTH + 1))
        is None
    )
    with app.app_context():
        assert CalendarEvent.query.count() == 0


def test_a_control_character_is_rejected_rather_than_silently_dropped(app, client):
    _admin(client, app)
    assert fx.create_event(client, title="Exam\x00day") is None
    assert fx.create_event(client, details="A\x07B") is None
    with app.app_context():
        assert CalendarEvent.query.count() == 0


def test_absent_optional_text_is_stored_as_null_not_as_an_empty_string(app, client):
    _admin(client, app)
    ep = fx.create_event(client, details="   ", location="")
    with app.app_context():
        row = _stored(ep)
        assert row.details is None
        assert row.location is None


def test_creation_requires_csrf(app, client):
    """CSRF is disabled in the testing config, so this asserts the
    protection is *declared* -- the form renders a token field and the
    form class is a ``FlaskForm``, which is what enforces it wherever
    CSRF is enabled."""
    from app.blueprints.admin.calendar_forms import CalendarEventForm
    from flask_wtf import FlaskForm

    assert issubclass(CalendarEventForm, FlaskForm)
    _admin(client, app)
    html = client.get(fx.ADMIN_EVENT_NEW).get_data(as_text=True)
    assert 'name="csrf_token"' in html


def test_creation_requires_a_valid_create_token(app, client):
    _admin(client, app)
    for bad in ("", "not-a-token", "x" * 40):
        response = client.post(
            fx.ADMIN_EVENT_NEW,
            data=fx.event_form_data(token=bad),
            follow_redirects=True,
        )
        assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        assert CalendarEvent.query.count() == 0


def test_another_administrators_create_token_is_rejected(app, client):
    _admin(client, app, "first@example.com")
    token = fx.token_from(client, fx.ADMIN_EVENT_NEW)
    with app.app_context():
        fx.admin("second@example.com")
    fx.login_as(client, "second@example.com")
    response = client.post(
        fx.ADMIN_EVENT_NEW,
        data=fx.event_form_data(token=token),
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        assert CalendarEvent.query.count() == 0


# ===========================================================================
# Tokens -- three distinct purposes
# ===========================================================================


def test_the_three_token_purposes_cannot_be_replayed_as_one_another(app, client):
    from app.services.calendar_tokens import load_token, make_token

    with app.app_context():
        creator = fx.admin("boss@example.com")
        row = fx.event(creator)
        payloads = {
            "calendar-event-create": {"actor_public_id": creator.public_id},
            "calendar-event-edit": {
                "actor_public_id": creator.public_id,
                "event_public_id": row.public_id,
                "event_version": row.version,
                "event_status": row.status,
            },
            "calendar-event-cancel": {
                "actor_public_id": creator.public_id,
                "event_public_id": row.public_id,
                "event_version": row.version,
                "event_status": row.status,
            },
        }
        for purpose, payload in payloads.items():
            token = make_token("admin", purpose, **payload)
            assert load_token("admin", token, purpose) is not None
            for other in payloads:
                if other != purpose:
                    assert load_token("admin", token, other) is None


def test_an_m09_announcement_token_is_not_a_calendar_token(app, client):
    from app.services.announcement_tokens import make_token as announcement_token
    from app.services.calendar_tokens import load_token

    with app.app_context():
        creator = fx.admin("boss@example.com")
        foreign = announcement_token(
            "admin",
            "announcement-withdraw",
            actor_public_id=creator.public_id,
            announcement_public_id="x",
            announcement_version=1,
        )
        for purpose in (
            "calendar-event-create",
            "calendar-event-edit",
            "calendar-event-cancel",
        ):
            assert load_token("admin", foreign, purpose) is None


def test_a_token_carries_no_private_event_text_and_no_internal_id(app, client):
    from app.services.calendar_tokens import load_token

    _admin(client, app)
    # A few earlier events first, so the target's internal id is a number
    # that cannot coincide with its own version (1) -- otherwise "the id
    # is not in the payload" would be untestable.
    for index in range(4):
        fx.create_event(client, title=f"Filler {index}")
    ep = fx.create_event(
        client, title="Secret plan", details="Do not tell anybody.", location="Vault"
    )
    with app.app_context():
        row = _stored(ep)
        internal_id, public_id = row.id, row.public_id
        assert internal_id != row.version
    edit_token = fx.token_from(client, fx.admin_event_edit(ep))
    cancel_token = fx.token_from(client, fx.admin_event_detail(ep))
    with app.app_context():
        for token, purpose in (
            (edit_token, "calendar-event-edit"),
            (cancel_token, "calendar-event-cancel"),
        ):
            payload = load_token("admin", token, purpose)
            assert payload is not None
            assert set(payload) == {
                "purpose",
                "actor_public_id",
                "event_public_id",
                "event_version",
                "event_status",
            }
            assert "Secret plan" not in str(payload)
            assert "Do not tell anybody." not in str(payload)
            assert "Vault" not in str(payload)
            # The event is named by its PUBLIC id, and the internal id is
            # not among the payload's values at all -- neither as a number
            # nor as its string form.
            assert payload["event_public_id"] == public_id
            assert internal_id not in payload.values()
            assert str(internal_id) not in payload.values()
            # The actor, too, is named only by a public identifier.
            assert len(payload["actor_public_id"]) == 36


# ===========================================================================
# Editing
# ===========================================================================


def test_an_administrator_edits_a_scheduled_event(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    with app.app_context():
        before = _stored(ep)
        created_at, version = before.created_at, before.version
    response = fx.edit_event(
        client,
        ep,
        title="Staff meeting (moved)",
        event_date="2026-05-19",
        start_time="10:00",
        end_time="11:00",
        location="Room 2",
        details="New details.",
    )
    assert response.status_code == 302
    with app.app_context():
        row = _stored(ep)
        assert row.title == "Staff meeting (moved)"
        assert row.event_date == date(2026, 5, 19)
        assert (row.start_time, row.end_time) == (time(10, 0), time(11, 0))
        assert row.location == "Room 2"
        assert row.details == "New details."
        assert row.version == version + 1
        assert row.created_at == created_at  # creation is never rewritten
        assert row.status == _SCHEDULED


def test_an_edit_can_turn_a_timed_event_into_an_all_day_one_and_back(app, client):
    _admin(client, app)
    ep = fx.create_event(client, start_time="14:00", end_time="15:00")
    fx.edit_event(client, ep, start_time="", end_time="")
    with app.app_context():
        row = _stored(ep)
        assert row.start_time is None and row.end_time is None and row.version == 2
    fx.edit_event(client, ep, start_time="09:00", end_time="10:00")
    with app.app_context():
        row = _stored(ep)
        assert (row.start_time, row.end_time) == (time(9, 0), time(10, 0))
        assert row.version == 3


def test_a_no_op_edit_moves_neither_the_version_nor_a_timestamp(app, client):
    _admin(client, app)
    ep = fx.create_event(client, start_time="14:00", end_time="15:30")
    with app.app_context():
        before = _stored(ep)
        snapshot = (before.version, before.created_at, before.updated_at)
    response = fx.edit_event(
        client,
        ep,
        title="Staff meeting",
        details="Everyone attends.",
        event_date="2026-05-18",
        start_time="14:00",
        end_time="15:30",
        location="Main hall",
    )
    assert response.status_code == 302
    with app.app_context():
        after = _stored(ep)
        assert (after.version, after.created_at, after.updated_at) == snapshot
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Nothing was changed" in body


def test_a_no_op_edit_is_a_no_op_even_when_the_text_only_differs_by_padding(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    with app.app_context():
        snapshot = _stored(ep).version
    fx.edit_event(
        client,
        ep,
        title="  Staff   meeting  ",
        details="  Everyone attends.  ",
        location="  Main hall  ",
    )
    with app.app_context():
        assert _stored(ep).version == snapshot


def test_a_stale_edit_is_rejected_and_changes_nothing(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    stale = fx.token_from(client, fx.admin_event_edit(ep))
    # Somebody else changes the event, moving its version under the form.
    fx.edit_event(client, ep, title="Changed by somebody else")
    response = client.post(
        fx.admin_event_edit(ep),
        data=fx.event_form_data(title="My stale change", token=stale),
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        row = _stored(ep)
        assert row.title == "Changed by somebody else"
        assert row.version == 2


def test_an_absent_or_forged_edit_token_is_rejected(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    for bad in ("", "forged", "x" * 60):
        response = client.post(
            fx.admin_event_edit(ep),
            data=fx.event_form_data(title="Nope", token=bad),
            follow_redirects=True,
        )
        assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        row = _stored(ep)
        assert row.title == "Staff meeting" and row.version == 1


def test_an_invalid_edit_re_renders_the_form_and_changes_nothing(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    token = fx.token_from(client, fx.admin_event_edit(ep))
    response = client.post(
        fx.admin_event_edit(ep),
        data=fx.event_form_data(title="", token=token),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Give this event a title." in response.get_data(as_text=True)
    with app.app_context():
        assert _stored(ep).version == 1


def test_a_missing_or_forged_event_public_id_is_a_non_disclosing_404(app, client):
    _admin(client, app)
    for ep in (
        "00000000-0000-0000-0000-000000000000",
        "not-a-uuid",
        "1",
    ):
        assert client.get(fx.admin_event_detail(ep)).status_code == 404, ep
        assert client.get(fx.admin_event_edit(ep)).status_code == 404, ep
        assert client.post(
            fx.admin_event_cancel(ep), data={"confirm": "yes"}
        ).status_code == 404, ep


# ===========================================================================
# Cancellation
# ===========================================================================


def test_an_administrator_cancels_a_scheduled_event(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    response = fx.cancel_event(client, ep)
    assert response.status_code == 302
    with app.app_context():
        row = _stored(ep)
        assert row.status == _CANCELLED
        assert row.cancelled_at is not None
        assert row.cancelled_at.microsecond == 0
        assert row.version == 2


def test_cancellation_requires_the_confirmation_box(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    response = fx.cancel_event(client, ep, confirm=False)
    assert response.status_code == 302
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "tick the confirmation box" in body
    with app.app_context():
        assert _stored(ep).status == _SCHEDULED


def test_cancellation_requires_a_valid_cancel_token(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    for bad in ("", "forged"):
        response = client.post(
            fx.admin_event_cancel(ep),
            data={"confirm": "yes", "event_state": bad},
            follow_redirects=True,
        )
        assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        assert _stored(ep).status == _SCHEDULED


def test_a_stale_cancel_token_is_rejected(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    stale = fx.token_from(client, fx.admin_event_detail(ep))
    fx.edit_event(client, ep, title="Changed first")
    response = client.post(
        fx.admin_event_cancel(ep),
        data={"confirm": "yes", "event_state": stale},
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        assert _stored(ep).status == _SCHEDULED


def test_cancellation_is_only_ever_a_post(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    assert client.get(fx.admin_event_cancel(ep)).status_code == 405
    with app.app_context():
        assert _stored(ep).status == _SCHEDULED


def test_a_cancelled_event_disappears_from_student_and_teacher_calendars_at_once(
    app, client
):
    with app.app_context():
        fx.admin("boss@example.com")
        group, _student, _teacher = fx.setup_group("A")
    fx.login_as(client, "boss@example.com")
    ep = fx.create_event(client, title="Open day")

    fx.login_as(client, "student.a@example.com")
    assert "Open day" in client.get(fx.student_month()).get_data(as_text=True)
    fx.login_as(client, "teacher.a@example.com")
    assert "Open day" in client.get(fx.teacher_month()).get_data(as_text=True)

    fx.login_as(client, "boss@example.com")
    assert fx.cancel_event(client, ep).status_code == 302

    fx.login_as(client, "student.a@example.com")
    assert "Open day" not in client.get(fx.student_month()).get_data(as_text=True)
    fx.login_as(client, "teacher.a@example.com")
    assert "Open day" not in client.get(fx.teacher_month()).get_data(as_text=True)
    fx.login_as(client, "boss@example.com")
    admin_html = client.get(fx.admin_month()).get_data(as_text=True)
    assert "Open day" in admin_html
    assert ">Cancelled<" in admin_html


def test_a_cancelled_event_is_permanently_immutable(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    fx.cancel_event(client, ep)
    with app.app_context():
        before = _stored(ep)
        snapshot = (
            before.title,
            before.details,
            before.event_date,
            before.version,
            before.updated_at,
            before.cancelled_at,
        )

    # The edit form refuses to open at all...
    response = client.get(fx.admin_event_edit(ep), follow_redirects=True)
    assert "cancelled event is permanent" in response.get_data(as_text=True)
    # ...and a POST aimed straight at it changes nothing.
    posted = client.post(
        fx.admin_event_edit(ep),
        data=fx.event_form_data(title="Trying anyway", token="anything"),
        follow_redirects=True,
    )
    assert "cancelled event is permanent" in posted.get_data(as_text=True)
    with app.app_context():
        after = _stored(ep)
        assert (
            after.title,
            after.details,
            after.event_date,
            after.version,
            after.updated_at,
            after.cancelled_at,
        ) == snapshot


def test_a_cancelled_event_cannot_be_cancelled_again(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    fx.cancel_event(client, ep)
    with app.app_context():
        snapshot = (_stored(ep).version, _stored(ep).cancelled_at)
    response = client.post(
        fx.admin_event_cancel(ep),
        data={"confirm": "yes", "event_state": "anything"},
        follow_redirects=True,
    )
    assert "cancelled event is permanent" in response.get_data(as_text=True)
    with app.app_context():
        assert (_stored(ep).version, _stored(ep).cancelled_at) == snapshot


def test_a_past_event_can_still_be_cancelled(app, client):
    """A calendar is also a record: something that should no longer be
    expected must always be markable as called off."""
    _admin(client, app)
    ep = fx.create_event(client, event_date="2024-01-15")
    assert ep is not None
    response = fx.cancel_event(client, ep)
    assert response.status_code == 302
    with app.app_context():
        assert _stored(ep).status == _CANCELLED


def test_the_detail_page_offers_no_control_once_an_event_is_cancelled(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    fx.cancel_event(client, ep)
    html = client.get(fx.admin_event_detail(ep)).get_data(as_text=True)
    assert "event_state" not in html
    assert "Cancel event" not in html
    assert "Edit event" not in html


# ===========================================================================
# Controls that must not exist anywhere
# ===========================================================================


@pytest.mark.parametrize(
    "suffix",
    ["/delete", "/restore", "/uncancel", "/duplicate", "/publish", "/export", "/repeat"],
)
def test_no_forbidden_event_endpoint_exists(app, client, suffix):
    _admin(client, app)
    ep = fx.create_event(client)
    url = f"/admin/calendar/events/{ep}{suffix}"
    assert client.get(url).status_code == 404
    assert client.post(url, data={"confirm": "yes"}).status_code in (404, 405)
    with app.app_context():
        assert _stored(ep).status == _SCHEDULED


def test_no_calendar_page_offers_a_delete_restore_duplicate_or_export_control(
    app, client
):
    _admin(client, app)
    ep = fx.create_event(client)
    for url in (fx.admin_month(), fx.admin_event_detail(ep), fx.admin_event_edit(ep)):
        html = client.get(url).get_data(as_text=True)
        for forbidden in ("Delete", "Restore", "Duplicate", "Export", "Repeat", ".ics"):
            assert forbidden not in html, (url, forbidden)


def test_the_form_offers_no_audience_scope_recurrence_or_status_input(app, client):
    _admin(client, app)
    html = client.get(fx.ADMIN_EVENT_NEW).get_data(as_text=True)
    for forbidden in (
        'name="scope"',
        'name="course"',
        'name="group"',
        'name="audience"',
        'name="status"',
        'name="repeat"',
        'name="recurrence"',
        'name="notify"',
    ):
        assert forbidden not in html, forbidden


def test_extra_submitted_fields_are_ignored_rather_than_honoured(app, client):
    """A crafted POST cannot set a field the form does not declare."""
    _admin(client, app)
    token = fx.token_from(client, fx.ADMIN_EVENT_NEW)
    data = fx.event_form_data(token=token)
    data.update(
        {
            "status": _CANCELLED,
            "cancelled_at": "2026-05-01 00:00:00",
            "version": "99",
            "created_by_id": "1",
            "public_id": "forged-public-id",
        }
    )
    response = client.post(fx.ADMIN_EVENT_NEW, data=data, follow_redirects=False)
    assert response.status_code == 302
    ep = fx.public_id_from_redirect(response)
    with app.app_context():
        row = _stored(ep)
        assert row.status == _SCHEDULED
        assert row.cancelled_at is None
        assert row.version == 1
        assert row.public_id != "forged-public-id"


# ===========================================================================
# The lock chain -- structural
# ===========================================================================
#
# SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so everything
# below asserts what the code *requests*, not that anything blocks.


def _locked_tables(app, call):
    """The distinct tables one lock chain reads from, in first-touch
    order. Every scalar the call needs is resolved before recording
    starts, so nothing in the test's own argument list can be mistaken
    for part of the lock order."""
    seen = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        match = re.search(r"\bFROM ([a-z_]+)", " ".join(statement.split()))
        if match:
            seen.append(match.group(1))

    with app.app_context():
        sa_event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        call()
    finally:
        with app.app_context():
            sa_event.remove(db.engine, "before_cursor_execute", _rec)
    ordered = []
    for name in seen:
        if name not in ordered:
            ordered.append(name)
    return ordered


def test_the_chain_requests_the_documented_lock_order(app):
    from app.services.calendar_transactions import lock_calendar_event_chain

    with app.app_context():
        creator = fx.admin("boss@example.com")
        row = fx.event(creator)
        actor_id, event_id = creator.id, row.id
        ordered = _locked_tables(
            app, lambda: lock_calendar_event_chain(actor_id, event_id=event_id)
        )
    assert ordered == ["users", "calendar_events"]


def test_a_create_chain_locks_only_the_acting_administrator(app):
    from app.services.calendar_transactions import lock_calendar_event_chain

    with app.app_context():
        creator = fx.admin("boss@example.com")
        actor_id = creator.id
        ordered = _locked_tables(app, lambda: lock_calendar_event_chain(actor_id))
    assert ordered == ["users"]


def test_the_chain_locks_no_academic_row_because_a_center_event_has_none(app):
    from app.services.calendar_transactions import lock_calendar_event_chain

    with app.app_context():
        creator = fx.admin("boss@example.com")
        group, _s, _t = fx.setup_group("A")
        row = fx.event(creator)
        actor_id, event_id = creator.id, row.id
        ordered = _locked_tables(
            app, lambda: lock_calendar_event_chain(actor_id, event_id=event_id)
        )
    for academic in ("academic_terms", "levels", "courses", "groups", "schedules"):
        assert academic not in ordered, academic


def test_the_chain_resets_the_transaction_exactly_once_before_its_first_lock(app):
    """One deliberate reset, owned by ``lock_academic_hierarchy`` -- the
    project-wide rule every other milestone's chain also follows."""
    from app.services.calendar_transactions import lock_calendar_event_chain

    rollbacks = []

    with app.app_context():
        creator = fx.admin("boss@example.com")
        actor_id = creator.id

        def _rec(conn):
            rollbacks.append(1)

        sa_event.listen(db.engine, "rollback", _rec)
        try:
            lock_calendar_event_chain(actor_id)
        finally:
            sa_event.remove(db.engine, "rollback", _rec)
    assert len(rollbacks) <= 1


def test_the_locked_actor_is_re_checked_rather_than_trusted_from_the_session(app):
    from app.services.calendar_transactions import (
        administrator_authz_broken,
        lock_calendar_event_chain,
    )

    with app.app_context():
        creator = fx.admin("boss@example.com")
        assert not administrator_authz_broken(lock_calendar_event_chain(creator.id))
        creator.status = UserStatus.SUSPENDED.value
        db.session.commit()
        assert administrator_authz_broken(lock_calendar_event_chain(creator.id))
        creator.status = UserStatus.ACTIVE.value
        creator.role = UserRole.TEACHER.value
        db.session.commit()
        assert administrator_authz_broken(lock_calendar_event_chain(creator.id))
        # A vanished actor is a rejection, never "keep going".
        assert administrator_authz_broken(lock_calendar_event_chain(None))


def test_a_suspended_administrator_cannot_complete_a_write(app, client):
    _admin(client, app)
    ep = fx.create_event(client)
    with app.app_context():
        actor = User.query.filter_by(email="admin@example.com").one()
        actor.status = UserStatus.SUSPENDED.value
        db.session.commit()
    fx.fresh_identity()
    response = client.post(
        fx.admin_event_edit(ep),
        data=fx.event_form_data(title="Should not happen", token="anything"),
        follow_redirects=False,
    )
    assert response.status_code in (302, 401, 404)
    with app.app_context():
        assert _stored(ep).title == "Staff meeting"


# ===========================================================================
# Text is escaped, never rendered as markup
# ===========================================================================


def test_markup_in_an_event_is_escaped_on_every_surface(app, client):
    with app.app_context():
        fx.admin("boss@example.com")
        group, _student, _teacher = fx.setup_group("A")
    fx.login_as(client, "boss@example.com")
    ep = fx.create_event(
        client,
        title="<script>alert(1)</script>",
        details="<img src=x onerror=alert(2)>",
        location="<b>Hall</b>",
    )
    assert ep is not None
    surfaces = [
        ("boss@example.com", fx.admin_month()),
        ("boss@example.com", fx.admin_event_detail(ep)),
        ("student.a@example.com", fx.student_month()),
        ("teacher.a@example.com", fx.teacher_month()),
    ]
    for email, url in surfaces:
        fx.login_as(client, email)
        html = client.get(url).get_data(as_text=True)
        # The angle brackets are escaped, which is exactly what makes the
        # text inert: the characters are still there to read, but no tag
        # and no attribute is ever parsed out of them.
        assert "<script>alert(1)</script>" not in html, url
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html, url
        assert "<img src=x" not in html, url
        assert "&lt;img src=x onerror=alert(2)&gt;" in html, url
        assert "<b>Hall</b>" not in html, url


# ===========================================================================
# Bounded, and free of N+1
# ===========================================================================


def test_the_administrator_calendar_cost_does_not_grow_with_the_center(app, client):
    def query_count(groups):
        with app.app_context():
            db.drop_all()
            db.create_all()
            creator = fx.admin("boss@example.com")
            for index in range(groups):
                group, _student, teacher = fx.setup_group(f"G{index}")
                fx.schedule(group, day_of_week=index % 7)
                fx.assignment(group, title=f"Essay {index}")
                fx.quiz(group, title=f"Quiz {index}")
                fx.listening(group, teacher, title=f"Listening {index}")
                fx.event(creator, title=f"Event {index}")
        fx.login_as(client, "boss@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            sa_event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.admin_month()).status_code == 200
        finally:
            with app.app_context():
                sa_event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(4)
