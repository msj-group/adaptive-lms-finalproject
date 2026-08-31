"""Schedule model constraints/relationships and the M08 query-only +
pure-date helpers in app/services/schedule_queries.py.

SQLite (the test backend) enforces CHECK constraints and foreign keys
(the project turns `PRAGMA foreign_keys=ON`), so the model-level
constraint tests here are meaningful for application logic -- but SQLite
has no `SELECT ... FOR UPDATE` and no REPEATABLE READ isolation, so
nothing here proves MySQL/InnoDB locking or blocking.
"""

from datetime import date, time

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, Course, Group, Level, Schedule
from app.services.schedule_queries import (
    conflicting_active_schedule,
    effective_ranges_share_weekday,
    first_weekday_on_or_after,
    group_has_schedule_history,
    range_contains_weekday,
    term_contains_range,
    term_schedule_range_outside,
    times_overlap,
)

TERM_START = date(2026, 9, 1)
TERM_END = date(2026, 12, 31)
MONDAY = 0
TUESDAY = 1
WEDNESDAY = 2


def _term(name="Fall 2026", start=TERM_START, end=TERM_END):
    term = AcademicTerm(name=name, start_date=start, end_date=end)
    db.session.add(term)
    db.session.commit()
    return term


def _group(term=None, name="Group A"):
    term = term or _term()
    level = Level(name=f"Level {name}", display_order=0)
    db.session.add(level)
    db.session.commit()
    course = Course(title=f"Course {name}", level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=name, capacity=20)
    db.session.add(group)
    db.session.commit()
    return group


def _schedule(
    group,
    day_of_week=MONDAY,
    start_time=time(9, 0),
    end_time=time(10, 30),
    effective_start_date=TERM_START,
    effective_end_date=TERM_END,
    location=None,
    status=AcademicStatus.ACTIVE.value,
    commit=True,
):
    row = Schedule(
        group_id=group.id,
        day_of_week=day_of_week,
        start_time=start_time,
        end_time=end_time,
        effective_start_date=effective_start_date,
        effective_end_date=effective_end_date,
        location=location,
        status=status,
    )
    db.session.add(row)
    if commit:
        db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Model: defaults, relationships, constraints
# ---------------------------------------------------------------------------


def test_schedule_defaults_and_relationship(app):
    with app.app_context():
        group = _group()
        row = _schedule(group, location="Room 3")
        assert row.id is not None
        assert row.public_id is not None
        assert row.status == "active"
        assert row.created_at is not None and row.updated_at is not None
        assert row.group.id == group.id
        assert group.schedules[0].id == row.id


def test_schedule_invalid_status_rejected(app):
    with app.app_context():
        group = _group()
        with pytest.raises(ValueError):
            Schedule(
                group_id=group.id,
                day_of_week=MONDAY,
                start_time=time(9, 0),
                end_time=time(10, 0),
                effective_start_date=TERM_START,
                effective_end_date=TERM_END,
                status="not-a-status",
            )


@pytest.mark.parametrize("bad_day", [-1, 7, 9])
def test_schedule_weekday_out_of_range_rejected_by_db(app, bad_day):
    with app.app_context():
        group = _group()
        _schedule(group, day_of_week=bad_day, commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_schedule_start_not_before_end_rejected_by_db(app):
    with app.app_context():
        group = _group()
        _schedule(group, start_time=time(11, 0), end_time=time(10, 0), commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_schedule_equal_start_end_rejected_by_db(app):
    with app.app_context():
        group = _group()
        _schedule(group, start_time=time(10, 0), end_time=time(10, 0), commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_schedule_effective_start_after_end_rejected_by_db(app):
    with app.app_context():
        group = _group()
        _schedule(
            group,
            effective_start_date=date(2026, 10, 1),
            effective_end_date=date(2026, 9, 1),
            commit=False,
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_schedule_exact_slot_uniqueness_enforced_by_db(app):
    with app.app_context():
        group = _group()
        _schedule(group)
        _schedule(group, location="different location does not matter", commit=False)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_schedule_public_id_uniqueness_enforced(app):
    with app.app_context():
        group = _group()
        first = _schedule(group)
        dup = _schedule(group, day_of_week=TUESDAY, commit=False)
        dup.public_id = first.public_id
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_schedule_fk_to_group_is_non_cascading(app):
    """The FK to groups.id must not cascade-delete: a Group that still
    has Schedule rows cannot be hard-deleted."""
    with app.app_context():
        group = _group()
        _schedule(group)
        db.session.delete(group)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_multiple_weekly_slots_for_one_group(app):
    with app.app_context():
        group = _group()
        _schedule(group, day_of_week=MONDAY, start_time=time(9, 0), end_time=time(10, 30))
        _schedule(group, day_of_week=WEDNESDAY, start_time=time(9, 0), end_time=time(10, 30))
        _schedule(group, day_of_week=MONDAY, start_time=time(11, 0), end_time=time(12, 0))
        assert Schedule.query.filter_by(group_id=group.id).count() == 3


# ---------------------------------------------------------------------------
# Pure date helpers
# ---------------------------------------------------------------------------


def test_first_weekday_on_or_after():
    # 2026-09-01 is a Tuesday (weekday 1)
    assert first_weekday_on_or_after(TUESDAY, date(2026, 9, 1)) == date(2026, 9, 1)
    assert first_weekday_on_or_after(MONDAY, date(2026, 9, 1)) == date(2026, 9, 7)
    assert first_weekday_on_or_after(0, date(2026, 9, 7)) == date(2026, 9, 7)


def test_range_contains_weekday():
    assert range_contains_weekday(MONDAY, date(2026, 9, 1), date(2026, 9, 7)) is True
    assert range_contains_weekday(MONDAY, date(2026, 9, 1), date(2026, 9, 6)) is False
    # single-day range that IS the weekday
    assert range_contains_weekday(MONDAY, date(2026, 9, 7), date(2026, 9, 7)) is True
    # empty range
    assert range_contains_weekday(MONDAY, date(2026, 9, 8), date(2026, 9, 7)) is False


def test_term_contains_range():
    assert term_contains_range(TERM_START, TERM_END, TERM_START, TERM_END) is True
    assert term_contains_range(TERM_START, TERM_END, date(2026, 9, 15), date(2026, 10, 15)) is True
    assert term_contains_range(TERM_START, TERM_END, date(2026, 8, 31), TERM_END) is False
    assert term_contains_range(TERM_START, TERM_END, TERM_START, date(2027, 1, 1)) is False


def test_times_overlap_half_open_and_adjacency():
    assert times_overlap(time(9, 0), time(10, 0), time(9, 30), time(10, 30)) is True
    # adjacent -> not overlapping
    assert times_overlap(time(9, 0), time(10, 0), time(10, 0), time(11, 0)) is False
    assert times_overlap(time(10, 0), time(11, 0), time(9, 0), time(10, 0)) is False
    # containment
    assert times_overlap(time(9, 0), time(12, 0), time(10, 0), time(11, 0)) is True


def test_effective_ranges_share_weekday_edges():
    # ranges touch on exactly one Monday
    assert effective_ranges_share_weekday(
        MONDAY, date(2026, 9, 1), date(2026, 9, 7), date(2026, 9, 7), date(2026, 9, 30)
    ) is True
    # ranges touch on a single day that is NOT a Monday
    assert effective_ranges_share_weekday(
        MONDAY, date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 8), date(2026, 9, 30)
    ) is False
    # disjoint ranges never share
    assert effective_ranges_share_weekday(
        MONDAY, date(2026, 9, 1), date(2026, 9, 6), date(2026, 9, 14), date(2026, 9, 30)
    ) is False


# ---------------------------------------------------------------------------
# conflicting_active_schedule -- the authoritative overlap rule
# ---------------------------------------------------------------------------


def test_conflict_exact_duplicate(app):
    with app.app_context():
        group = _group()
        _schedule(group, day_of_week=MONDAY, start_time=time(9, 0), end_time=time(10, 30))
        hit = conflicting_active_schedule(
            group.id, MONDAY, time(9, 0), time(10, 30), TERM_START, TERM_END
        )
        assert hit is not None


def test_conflict_time_overlap_same_weekday_and_dates(app):
    with app.app_context():
        group = _group()
        _schedule(group, day_of_week=MONDAY, start_time=time(9, 0), end_time=time(10, 30))
        assert (
            conflicting_active_schedule(
                group.id, MONDAY, time(10, 0), time(11, 0), TERM_START, TERM_END
            )
            is not None
        )


def test_no_conflict_for_adjacent_times(app):
    with app.app_context():
        group = _group()
        _schedule(group, day_of_week=MONDAY, start_time=time(9, 0), end_time=time(10, 0))
        assert (
            conflicting_active_schedule(
                group.id, MONDAY, time(10, 0), time(11, 0), TERM_START, TERM_END
            )
            is None
        )


def test_no_conflict_for_different_weekday(app):
    with app.app_context():
        group = _group()
        _schedule(group, day_of_week=MONDAY, start_time=time(9, 0), end_time=time(10, 30))
        assert (
            conflicting_active_schedule(
                group.id, TUESDAY, time(9, 0), time(10, 30), TERM_START, TERM_END
            )
            is None
        )


def test_no_conflict_when_effective_periods_do_not_intersect(app):
    with app.app_context():
        group = _group()
        _schedule(
            group,
            day_of_week=MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 30),
            effective_start_date=date(2026, 9, 1),
            effective_end_date=date(2026, 9, 30),
        )
        assert (
            conflicting_active_schedule(
                group.id,
                MONDAY,
                time(9, 0),
                time(10, 30),
                date(2026, 10, 1),
                date(2026, 10, 31),
            )
            is None
        )


def test_no_conflict_when_periods_intersect_only_on_other_weekday(app):
    with app.app_context():
        group = _group()
        # existing Monday slot effective only through Mon 2026-09-07
        _schedule(
            group,
            day_of_week=MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 30),
            effective_start_date=date(2026, 9, 1),
            effective_end_date=date(2026, 9, 7),
        )
        # candidate Monday slot starting Tue 2026-09-08 -> the two ranges
        # do not share any Monday
        assert (
            conflicting_active_schedule(
                group.id,
                MONDAY,
                time(9, 0),
                time(10, 30),
                date(2026, 9, 8),
                date(2026, 12, 31),
            )
            is None
        )


def test_archived_schedule_never_conflicts(app):
    with app.app_context():
        group = _group()
        _schedule(
            group,
            day_of_week=MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 30),
            status=AcademicStatus.ARCHIVED.value,
        )
        assert (
            conflicting_active_schedule(
                group.id, MONDAY, time(9, 0), time(10, 30), TERM_START, TERM_END
            )
            is None
        )


def test_conflict_excludes_self(app):
    with app.app_context():
        group = _group()
        row = _schedule(group, day_of_week=MONDAY, start_time=time(9, 0), end_time=time(10, 30))
        assert (
            conflicting_active_schedule(
                group.id,
                MONDAY,
                time(9, 0),
                time(10, 30),
                TERM_START,
                TERM_END,
                exclude_schedule_id=row.id,
            )
            is None
        )


def test_conflict_scoped_to_group(app):
    with app.app_context():
        term = _term()
        group_a = _group(term, name="A")
        group_b = _group(term, name="B")
        _schedule(group_a, day_of_week=MONDAY, start_time=time(9, 0), end_time=time(10, 30))
        assert (
            conflicting_active_schedule(
                group_b.id, MONDAY, time(9, 0), time(10, 30), TERM_START, TERM_END
            )
            is None
        )


# ---------------------------------------------------------------------------
# group_has_schedule_history / term_schedule_range_outside
# ---------------------------------------------------------------------------


def test_group_has_schedule_history_counts_any_status(app):
    with app.app_context():
        group = _group()
        assert group_has_schedule_history(group.id) is False
        _schedule(group, status=AcademicStatus.ARCHIVED.value)
        assert group_has_schedule_history(group.id) is True


def test_term_schedule_range_outside_detects_out_of_range_rows(app):
    with app.app_context():
        term = _term()
        group = _group(term)
        _schedule(
            group,
            day_of_week=MONDAY,
            effective_start_date=date(2026, 9, 7),
            effective_end_date=date(2026, 12, 28),
        )
        # shrink that still contains the range -> nothing outside
        assert term_schedule_range_outside(term.id, date(2026, 9, 1), date(2026, 12, 31)) is None
        # shrink the end before the schedule's end -> detected
        assert (
            term_schedule_range_outside(term.id, date(2026, 9, 1), date(2026, 12, 1)) is not None
        )
        # move the start after the schedule's start -> detected
        assert (
            term_schedule_range_outside(term.id, date(2026, 9, 8), date(2026, 12, 31)) is not None
        )


def test_term_schedule_range_outside_includes_archived_and_archived_group(app):
    with app.app_context():
        term = _term()
        group = _group(term)
        _schedule(
            group,
            day_of_week=MONDAY,
            effective_start_date=date(2026, 9, 7),
            effective_end_date=date(2026, 12, 28),
            status=AcademicStatus.ARCHIVED.value,
        )
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        assert (
            term_schedule_range_outside(term.id, date(2026, 9, 1), date(2026, 12, 1)) is not None
        )
