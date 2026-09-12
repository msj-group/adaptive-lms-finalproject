"""Phase 4 / M10 -- the ``calendar_events`` table and the CalendarEvent
model.

Covers the columns, the four CHECK constraints, the one plain foreign
key, the three indexes, the absence of any cascade, the all-day versus
timed shape, the time ordering, the cancellation state truth table, and
the permanent immutability of a cancelled event as the *application*
enforces it (the database stores the state; nothing in the schema can
forbid a future UPDATE, which is why the route-level proof lives in
``tests/test_admin_calendar.py``).
"""

from datetime import date, datetime, time

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

import tests.calendar_fixtures as fx
from app.extensions import db
from app.models import (
    CALENDAR_EVENT_DETAILS_MAX_LENGTH,
    CALENDAR_EVENT_LOCATION_MAX_LENGTH,
    CALENDAR_EVENT_TITLE_MAX_LENGTH,
    CalendarEvent,
    CalendarEventStatus,
)

_SCHEDULED = CalendarEventStatus.SCHEDULED.value
_CANCELLED = CalendarEventStatus.CANCELLED.value


def _creator():
    return fx.admin("creator@example.com")


def _insert(**kwargs):
    """Insert one row with the given overrides, bypassing the fixture's
    helpful defaults so an illegal shape really reaches the database."""
    fields = dict(
        title="Event",
        details=None,
        event_date=date(2026, 5, 18),
        start_time=None,
        end_time=None,
        location=None,
        status=_SCHEDULED,
        cancelled_at=None,
        version=1,
        created_at=fx.NOW,
        updated_at=fx.NOW,
    )
    fields.update(kwargs)
    row = CalendarEvent(**fields)
    db.session.add(row)
    db.session.commit()
    return row


# ===========================================================================
# Shape
# ===========================================================================


def test_the_table_has_exactly_the_expected_columns(app):
    with app.app_context():
        assert {c["name"] for c in inspect(db.engine).get_columns("calendar_events")} == {
            "id",
            "public_id",
            "created_by_id",
            "title",
            "details",
            "event_date",
            "start_time",
            "end_time",
            "location",
            "status",
            "cancelled_at",
            "version",
            "created_at",
            "updated_at",
        }


def test_there_is_no_audience_scope_recurrence_or_link_column(app):
    """M10 stores a center event, and only that.

    Each of these absences is a decision: a Course- or Group-targeted
    event, a repeating event, an attachment, a link and a notification
    are different objects with different rules, and none is approved. A
    nullable placeholder column would be the start of one.
    """
    with app.app_context():
        names = {c["name"] for c in inspect(db.engine).get_columns("calendar_events")}
    for forbidden in (
        "course_id",
        "group_id",
        "level_id",
        "academic_term_id",
        "schedule_id",
        "audience",
        "scope",
        "recurrence",
        "rrule",
        "repeat_until",
        "url",
        "external_link",
        "uploaded_file_id",
        "notify",
        "reminder_at",
        "timezone",
        "published_at",
        "deleted_at",
        "restored_at",
    ):
        assert forbidden not in names, forbidden


def test_the_three_expected_indexes_exist_and_no_more(app):
    with app.app_context():
        assert {
            index["name"] for index in inspect(db.engine).get_indexes("calendar_events")
        } == {
            "ix_calendar_events_status_date_id",
            "ix_calendar_events_date_id",
            "ix_calendar_events_created_by_date_id",
        }


def test_the_indexes_lead_with_the_columns_their_queries_filter_on(app):
    with app.app_context():
        columns = {
            index["name"]: index["column_names"]
            for index in inspect(db.engine).get_indexes("calendar_events")
        }
    # The reader read: equality on status, then the event_date range.
    assert columns["ix_calendar_events_status_date_id"] == ["status", "event_date", "id"]
    # The administrator read spans the same range but includes cancelled
    # rows, so status must NOT lead.
    assert columns["ix_calendar_events_date_id"] == ["event_date", "id"]
    # The leftmost prefix the foreign key requires.
    assert columns["ix_calendar_events_created_by_date_id"][0] == "created_by_id"


def test_the_only_foreign_key_is_created_by_and_it_has_no_cascade(app):
    with app.app_context():
        keys = inspect(db.engine).get_foreign_keys("calendar_events")
        assert len(keys) == 1
        assert keys[0]["constrained_columns"] == ["created_by_id"]
        assert keys[0]["referred_table"] == "users"
        options = keys[0].get("options") or {}
        assert not options.get("ondelete")
        assert not options.get("onupdate")


def test_no_relationship_is_declared_in_either_direction(app):
    """The same choice M02 / M06 / M07 / M08 / M09 made: every read goes
    through the column-projected queries in ``calendar_queries``, so no
    page can trigger a lazy load and no ``cascade`` configuration can
    remove an event when an account is touched."""
    with app.app_context():
        assert list(inspect(CalendarEvent).relationships.keys()) == []


def test_public_id_is_unique_and_is_a_uuid(app):
    with app.app_context():
        creator = _creator()
        first = fx.event(creator)
        assert len(first.public_id) == 36
        with pytest.raises(IntegrityError):
            _insert(created_by_id=creator.id, public_id=first.public_id)
        db.session.rollback()


def test_the_declared_text_bounds_match_the_columns(app):
    with app.app_context():
        widths = {
            c["name"]: getattr(c["type"], "length", None)
            for c in inspect(db.engine).get_columns("calendar_events")
        }
    assert widths["title"] == CALENDAR_EVENT_TITLE_MAX_LENGTH == 150
    assert widths["details"] == CALENDAR_EVENT_DETAILS_MAX_LENGTH == 5000
    assert widths["location"] == CALENDAR_EVENT_LOCATION_MAX_LENGTH == 255


# ===========================================================================
# All-day versus timed, and the time ordering
# ===========================================================================


def test_an_all_day_event_carries_neither_time(app):
    with app.app_context():
        row = fx.event(_creator())
        assert row.start_time is None and row.end_time is None
        assert row.is_all_day is True


def test_a_timed_event_carries_both_times_in_order(app):
    with app.app_context():
        row = fx.timed_event(_creator())
        assert (row.start_time, row.end_time) == (time(17, 0), time(19, 0))
        assert row.is_all_day is False


@pytest.mark.parametrize(
    "start, end",
    [
        (time(9, 0), None),  # a start with no end
        (None, time(11, 0)),  # an end with no start
    ],
)
def test_the_database_refuses_a_half_configured_window(app, start, end):
    with app.app_context():
        creator = _creator()
        with pytest.raises(IntegrityError):
            _insert(created_by_id=creator.id, start_time=start, end_time=end)
        db.session.rollback()


@pytest.mark.parametrize(
    "start, end",
    [
        (time(11, 0), time(9, 0)),  # reversed
        (time(9, 0), time(9, 0)),  # zero length
        (time(22, 0), time(1, 0)),  # overnight -- out of scope
    ],
)
def test_the_database_refuses_an_unordered_or_overnight_window(app, start, end):
    with app.app_context():
        creator = _creator()
        with pytest.raises(IntegrityError):
            _insert(created_by_id=creator.id, start_time=start, end_time=end)
        db.session.rollback()


def test_a_one_minute_window_is_legal(app):
    with app.app_context():
        creator = _creator()
        row = _insert(
            created_by_id=creator.id, start_time=time(9, 0), end_time=time(9, 1)
        )
        assert row.id is not None


# ===========================================================================
# Status and the cancellation state truth table
# ===========================================================================


def test_the_status_enum_has_exactly_two_members(app):
    assert [status.value for status in CalendarEventStatus] == ["scheduled", "cancelled"]


@pytest.mark.parametrize("bad", ["draft", "archived", "deleted", "completed", ""])
def test_the_model_rejects_an_unknown_status(app, bad):
    with app.app_context():
        with pytest.raises(ValueError):
            CalendarEvent(status=bad)


def test_the_database_refuses_an_unknown_status(app):
    with app.app_context():
        creator = _creator()
        row = CalendarEvent(
            created_by_id=creator.id,
            title="Event",
            event_date=date(2026, 5, 18),
            status=_SCHEDULED,
            version=1,
            created_at=fx.NOW,
            updated_at=fx.NOW,
        )
        db.session.add(row)
        db.session.commit()
        # Bypass the @validates guard the way a manual row would, so the
        # CHECK is what has to refuse it.
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text("UPDATE calendar_events SET status = 'archived' WHERE id = :i"),
                {"i": row.id},
            )
        db.session.rollback()


def test_a_scheduled_event_must_have_no_cancellation_moment(app):
    with app.app_context():
        creator = _creator()
        with pytest.raises(IntegrityError):
            _insert(
                created_by_id=creator.id,
                status=_SCHEDULED,
                cancelled_at=datetime(2026, 5, 14, 10, 0),
            )
        db.session.rollback()


def test_a_cancelled_event_must_have_a_cancellation_moment(app):
    with app.app_context():
        creator = _creator()
        with pytest.raises(IntegrityError):
            _insert(created_by_id=creator.id, status=_CANCELLED, cancelled_at=None)
        db.session.rollback()


def test_both_legitimate_lifecycle_shapes_are_accepted(app):
    with app.app_context():
        creator = _creator()
        scheduled = fx.event(creator)
        cancelled = fx.cancelled_event(creator)
        assert scheduled.status == _SCHEDULED and scheduled.cancelled_at is None
        assert cancelled.status == _CANCELLED and cancelled.cancelled_at is not None
        assert scheduled.is_scheduled and not scheduled.is_cancelled
        assert cancelled.is_cancelled and not cancelled.is_scheduled


def test_a_cancellation_moment_in_the_past_relative_to_the_event_date_is_legal(app):
    """A calendar is also a record: an event may be called off long
    before -- or long after -- the day it was going to happen, and no
    ordering between ``cancelled_at`` and ``event_date`` is imposed,
    because the two are not even the same kind of value (a UTC instant
    and a local civil date)."""
    with app.app_context():
        creator = _creator()
        row = fx.cancelled_event(
            creator,
            event_date=date(2026, 1, 5),
            cancelled_at=datetime(2026, 9, 1, 12, 0),
        )
        assert row.status == _CANCELLED


# ===========================================================================
# Version
# ===========================================================================


def test_version_starts_at_one_and_must_stay_positive(app):
    with app.app_context():
        creator = _creator()
        assert fx.event(creator).version == 1
        with pytest.raises(ValueError):
            CalendarEvent(version=0)
        with pytest.raises(ValueError):
            CalendarEvent(version=-3)


def test_version_rejects_a_boolean(app):
    """``bool`` is an ``int`` subclass in Python, and ``True`` must never
    be accepted as version 1."""
    with app.app_context():
        with pytest.raises(ValueError):
            CalendarEvent(version=True)


def test_the_database_refuses_a_non_positive_version(app):
    with app.app_context():
        creator = _creator()
        row = fx.event(creator)
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text("UPDATE calendar_events SET version = 0 WHERE id = :i"),
                {"i": row.id},
            )
        db.session.rollback()


# ===========================================================================
# No cascade -- the parent cannot be removed out from under an event
# ===========================================================================


def test_the_creator_account_cannot_be_deleted_while_an_event_references_it(app):
    with app.app_context():
        creator = _creator()
        fx.event(creator)
        with pytest.raises(IntegrityError):
            db.session.execute(
                db.text("DELETE FROM users WHERE id = :i"), {"i": creator.id}
            )
        db.session.rollback()


# ===========================================================================
# Text is stored exactly as typed, and nothing is HTML
# ===========================================================================


def test_details_and_location_are_nullable_and_absent_has_one_spelling(app):
    """The normaliser never stores an empty string, so a NULL and an
    empty string can never come to mean two different things."""
    with app.app_context():
        creator = _creator()
        row = fx.event(creator, details=None, location=None)
        assert row.details is None and row.location is None


def test_markup_in_the_text_is_stored_verbatim_and_never_interpreted(app):
    with app.app_context():
        creator = _creator()
        row = fx.event(
            creator, title="<b>Exam</b>", details="<script>x</script>", location="<i>A</i>"
        )
        stored = CalendarEvent.query.filter_by(public_id=row.public_id).one()
        assert stored.title == "<b>Exam</b>"
        assert stored.details == "<script>x</script>"
        assert stored.location == "<i>A</i>"


# ===========================================================================
# There is no numeric column a grade could hide in
# ===========================================================================


def test_no_numeric_or_score_like_column_exists(app):
    with app.app_context():
        types = {
            c["name"]: str(c["type"]).upper()
            for c in inspect(db.engine).get_columns("calendar_events")
        }
    for name, sql_type in types.items():
        if name in ("id", "created_by_id", "version"):
            continue
        assert "FLOAT" not in sql_type, name
        assert "DECIMAL" not in sql_type, name
        assert "NUMERIC" not in sql_type, name
