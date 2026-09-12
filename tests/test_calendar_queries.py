"""Phase 4 / M10 -- the calendar read model itself.

Covers the date-range parsing, normalisation and navigation; the
deterministic same-day ordering; the fact that recurring class
occurrences come from the shared canonical service rather than from a
second implementation; the range boundaries in local civil time; which
of an Assignment's / Quiz's two moments appear; that a Listening
activity never duplicates its backing Quiz; that one source object is
always exactly one entry; and that nothing private or internal is
carried in a row.
"""

from datetime import date, datetime, time, timedelta

import pytest

import tests.calendar_fixtures as fx
from app.extensions import db
from app.models import AcademicTerm, Course
from app.services import calendar_queries as cq
from app.services.schedule_occurrences import to_app_local

MAY = cq.CalendarRange(fx.MONTH_START, fx.MONTH_END)


def _reference(app, moment=None):
    """The naive-UTC reference moment the query layer compares against."""
    return moment or datetime(2026, 6, 30, 12, 0, 0)


def _student_rows(app, student_id, a_range=MAY, reference=None):
    rows, _ = cq.build_calendar(
        a_range,
        app.config["APP_TIMEZONE"],
        _reference(app, reference),
        student_id=student_id,
    )
    return rows


def _teacher_rows(app, teacher_id, a_range=MAY, reference=None):
    rows, _ = cq.build_calendar(
        a_range,
        app.config["APP_TIMEZONE"],
        _reference(app, reference),
        teacher_id=teacher_id,
    )
    return rows


def _sources(rows):
    return [row["source"] for row in rows]


# ===========================================================================
# The date range
# ===========================================================================


def test_the_default_view_is_the_whole_current_month():
    assert cq.month_range(date(2026, 5, 13)) == cq.CalendarRange(
        date(2026, 5, 1), date(2026, 5, 31)
    )
    assert cq.month_range(date(2026, 2, 14)) == cq.CalendarRange(
        date(2026, 2, 1), date(2026, 2, 28)
    )
    # A leap February, so the month length is really computed.
    assert cq.month_range(date(2028, 2, 14)) == cq.CalendarRange(
        date(2028, 2, 1), date(2028, 2, 29)
    )


@pytest.mark.parametrize(
    "raw_from, raw_to",
    [
        (None, None),
        ("", ""),
        ("oops", "2026-05-31"),
        ("2026-05-01", "not-a-date"),
        ("2026-02-30", "2026-03-05"),  # impossible day
        ("2026/05/01", "2026/05/31"),  # wrong separators
        ("13-05-2026", "2026-05-31"),  # wrong order
        ("2026-05-01T00:00", "2026-05-31"),  # a datetime, not a date
    ],
)
def test_a_missing_or_malformed_range_falls_back_to_the_current_month(raw_from, raw_to):
    today = date(2026, 5, 13)
    answer = cq.normalize_range(raw_from, raw_to, today)
    assert answer.range == cq.month_range(today)


def test_a_completely_absent_range_is_not_reported_as_normalised():
    """Somebody who asked for nothing got exactly what they asked for."""
    answer = cq.normalize_range(None, None, date(2026, 5, 13))
    assert answer.normalized is False


def test_a_half_written_or_malformed_range_is_reported_as_normalised():
    for raw_from, raw_to in (("2026-05-01", ""), ("", "2026-05-31"), ("x", "y")):
        answer = cq.normalize_range(raw_from, raw_to, date(2026, 5, 13))
        assert answer.normalized is True, (raw_from, raw_to)


def test_a_reversed_range_is_swapped_rather_than_rejected():
    answer = cq.normalize_range("2026-05-31", "2026-05-01", date(2026, 5, 13))
    assert answer.range == cq.CalendarRange(date(2026, 5, 1), date(2026, 5, 31))
    assert answer.normalized is True


def test_an_oversized_range_is_capped_and_keeps_its_start():
    answer = cq.normalize_range("2026-01-01", "2026-12-31", date(2026, 5, 13))
    assert answer.range.start == date(2026, 1, 1)
    assert cq.span_days(answer.range) == cq.MAX_RANGE_DAYS == 62
    assert answer.range.end == date(2026, 1, 1) + timedelta(days=61)
    assert answer.normalized is True


def test_a_range_exactly_at_the_cap_is_left_alone():
    start = date(2026, 5, 1)
    end = start + timedelta(days=cq.MAX_RANGE_DAYS - 1)
    answer = cq.normalize_range(start.isoformat(), end.isoformat(), date(2026, 5, 13))
    assert answer.range == cq.CalendarRange(start, end)
    assert answer.normalized is False


def test_a_single_day_range_is_legal():
    answer = cq.normalize_range("2026-05-13", "2026-05-13", date(2026, 5, 13))
    assert cq.span_days(answer.range) == 1
    assert answer.normalized is False


def test_an_absurdly_distant_range_is_clamped_into_the_navigable_window():
    today = date(2026, 5, 13)
    far = cq.normalize_range("9999-01-01", "9999-03-01", today)
    assert far.normalized is True
    assert far.range.end <= today + timedelta(days=cq.NAVIGATION_WINDOW_DAYS)
    ancient = cq.normalize_range("0001-01-01", "0001-03-01", today)
    assert ancient.normalized is True
    assert ancient.range.start >= today - timedelta(days=cq.NAVIGATION_WINDOW_DAYS)
    # Clamping is monotone, so the answer is still an ordered, bounded range.
    for answer in (far, ancient):
        assert answer.range.start <= answer.range.end
        assert cq.span_days(answer.range) <= cq.MAX_RANGE_DAYS


def test_previous_and_next_move_by_whole_months_for_a_whole_month_view():
    today = date(2026, 5, 13)
    may = cq.month_range(today)
    assert cq.previous_range(may, today) == cq.CalendarRange(
        date(2026, 4, 1), date(2026, 4, 30)
    )
    assert cq.next_range(may, today) == cq.CalendarRange(
        date(2026, 6, 1), date(2026, 6, 30)
    )
    # Across a year boundary, in both directions.
    january = cq.month_range(date(2026, 1, 10))
    assert cq.previous_range(january, today).start == date(2025, 12, 1)
    december = cq.month_range(date(2026, 12, 10))
    assert cq.next_range(december, today).start == date(2027, 1, 1)


def test_previous_and_next_move_by_the_span_for_a_hand_made_range():
    today = date(2026, 5, 13)
    week = cq.CalendarRange(date(2026, 5, 11), date(2026, 5, 17))
    assert cq.previous_range(week, today) == cq.CalendarRange(
        date(2026, 5, 4), date(2026, 5, 10)
    )
    assert cq.next_range(week, today) == cq.CalendarRange(
        date(2026, 5, 18), date(2026, 5, 24)
    )


def test_navigation_never_leaves_the_navigable_window():
    today = date(2026, 5, 13)
    edge = cq.month_range(today + timedelta(days=cq.NAVIGATION_WINDOW_DAYS))
    assert cq.next_range(edge, today) == edge  # it stops rather than overflowing
    early = cq.month_range(today - timedelta(days=cq.NAVIGATION_WINDOW_DAYS))
    assert cq.previous_range(early, today) == early


def test_navigation_urls_are_built_from_normalised_values():
    """Every link the application emits is one it would accept back
    unchanged -- no half-written, reversed or oversized range is ever
    produced by the server itself."""
    today = date(2026, 5, 13)
    a_range = cq.month_range(today)
    for candidate in (
        a_range,
        cq.previous_range(a_range, today),
        cq.next_range(a_range, today),
    ):
        args = cq.range_args(candidate)
        assert set(args) == {"from", "to"}
        round_trip = cq.normalize_range(args["from"], args["to"], today)
        assert round_trip.range == candidate
        assert round_trip.normalized is False


# ===========================================================================
# Class occurrences come from the shared canonical service
# ===========================================================================


def test_class_entries_are_produced_by_the_shared_occurrence_service(app, monkeypatch):
    """M10 must not re-implement recurrence. Replacing the canonical
    generator with a spy removes every class entry, which is only
    possible if the calendar really goes through it."""
    calls = []

    def _spy(spec, from_date, to_date):
        calls.append((spec.day_of_week, from_date, to_date))
        return iter(())

    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.schedule(group, day_of_week=0)
        monkeypatch.setattr(cq, "iter_occurrences_between", _spy)
        rows = _student_rows(app, student.id)

    assert calls == [(0, fx.MONTH_START, fx.MONTH_END)]
    assert cq.SOURCE_CLASS not in _sources(rows)


def test_every_weekly_occurrence_inside_the_range_becomes_one_entry(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.schedule(group, day_of_week=0, start_time=time(9, 0), end_time=time(11, 0))
        rows = [r for r in _student_rows(app, student.id) if r["source"] == cq.SOURCE_CLASS]
        dates = [row["event_date"] for row in rows]

    # Mondays in May 2026: the 4th, 11th, 18th and 25th.
    assert dates == [date(2026, 5, 4), date(2026, 5, 11), date(2026, 5, 18), date(2026, 5, 25)]
    assert all(row["start_time"] == time(9, 0) for row in rows)
    assert all(row["end_time"] == time(11, 0) for row in rows)


def test_a_schedule_whose_effective_period_misses_the_range_is_never_fetched(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.schedule(
            group,
            day_of_week=0,
            effective_start_date=date(2026, 7, 1),
            effective_end_date=date(2026, 7, 31),
        )
        assert cq.schedule_source_rows(MAY, student_id=student.id) == []
        assert _sources(_student_rows(app, student.id)) == []


def test_occurrences_are_clipped_to_both_the_range_and_the_effective_period(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.schedule(
            group,
            day_of_week=0,
            effective_start_date=date(2026, 5, 12),
            effective_end_date=date(2026, 5, 20),
        )
        rows = [r for r in _student_rows(app, student.id) if r["source"] == cq.SOURCE_CLASS]

    # Only the Monday the effective period actually contains: the 18th.
    assert [row["event_date"] for row in rows] == [date(2026, 5, 18)]


def test_an_archived_schedule_produces_no_occurrence(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.schedule(group, day_of_week=0, status="archived")
        assert _sources(_student_rows(app, student.id)) == []


# ===========================================================================
# Range boundaries, in local civil time
# ===========================================================================


def test_the_range_is_inclusive_at_both_ends(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        # A Friday slot: 2026-05-01 and 2026-05-29 are both Fridays.
        fx.schedule(group, day_of_week=4)
        rows = [r for r in _student_rows(app, student.id) if r["source"] == cq.SOURCE_CLASS]
        dates = [row["event_date"] for row in rows]
    assert fx.MONTH_START in dates
    assert date(2026, 5, 29) in dates

    with app.app_context():
        one_day = cq.CalendarRange(date(2026, 5, 1), date(2026, 5, 1))
        rows = [
            r for r in _student_rows(app, student.id, one_day)
            if r["source"] == cq.SOURCE_CLASS
        ]
    assert [row["event_date"] for row in rows] == [date(2026, 5, 1)]


def test_a_utc_instant_is_placed_on_its_local_civil_day(app):
    """``assignments`` stores UTC; a calendar range is local civil dates.
    An instant late on one UTC day can therefore belong to the *next*
    local day, and the entry must follow the local one."""
    with app.app_context():
        tz_name = app.config["APP_TIMEZONE"]
        group, student, _teacher = fx.setup_group("A")
        # 23:30 UTC on 2026-05-19. Under any positive offset this is the
        # 20th locally; under UTC itself it stays the 19th. Either way the
        # entry must land on whatever `to_app_local` says.
        due = datetime(2026, 5, 19, 23, 30)
        fx.assignment(group, opens_at=datetime(2026, 5, 4, 8, 0), due_at=due)
        rows = [
            r for r in _student_rows(app, student.id)
            if r["source"] == cq.SOURCE_ASSIGNMENT_DUE
        ]
        expected = to_app_local(tz_name, due)

    assert len(rows) == 1
    assert rows[0]["event_date"] == expected.date()
    assert rows[0]["start_time"] == expected.time()


def test_an_instant_whose_local_day_falls_outside_the_range_is_dropped(app):
    """The SQL window is deliberately widened by more than any real UTC
    offset, so the exact decision is made on the local civil date. A
    source just outside the range must not sneak in through that
    widening."""
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.assignment(
            group,
            title="Well before",
            opens_at=datetime(2026, 3, 1, 8, 0),
            due_at=datetime(2026, 3, 10, 8, 0),
        )
        rows = _student_rows(app, student.id)
    assert rows == []

    with app.app_context():
        one_day = cq.CalendarRange(date(2026, 5, 20), date(2026, 5, 20))
        rows = _student_rows(app, student.id, one_day)
    assert rows == []


def test_a_center_event_date_is_a_local_civil_value_and_is_not_converted(app):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        fx.event(creator, event_date=date(2026, 5, 31), start_time=time(0, 30),
                 end_time=time(1, 30))
        rows = _student_rows(app, student.id)

    assert len(rows) == 1
    # 00:30 on the last day of the range stays 00:30 on that day: an
    # event's own date and times are already local, so converting them
    # would be exactly the "compare a local value to UTC" mistake.
    assert rows[0]["event_date"] == date(2026, 5, 31)
    assert rows[0]["start_time"] == time(0, 30)


# ===========================================================================
# Which of a source's moments appear
# ===========================================================================


def test_both_of_an_assignments_moments_appear_when_both_are_in_range(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.assignment(group)
        rows = _student_rows(app, student.id)
    assert sorted(_sources(rows)) == [
        cq.SOURCE_ASSIGNMENT_DUE,
        cq.SOURCE_ASSIGNMENT_OPENS,
    ]


def test_only_the_moment_that_is_in_range_appears(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        # Opens in April, due in May: only the due entry is in the range.
        fx.assignment(
            group,
            opens_at=datetime(2026, 4, 10, 8, 0),
            due_at=datetime(2026, 5, 20, 20, 0),
            published_at=datetime(2026, 4, 1, 8, 0),
        )
        rows = _student_rows(app, student.id)
    assert _sources(rows) == [cq.SOURCE_ASSIGNMENT_DUE]


def test_the_two_moments_carry_distinct_labels(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.assignment(group)
        labels = {row["source"]: row["source_label"] for row in _student_rows(app, student.id)}
    assert labels[cq.SOURCE_ASSIGNMENT_OPENS] == "Assignment opens"
    assert labels[cq.SOURCE_ASSIGNMENT_DUE] == "Assignment due"
    assert labels[cq.SOURCE_ASSIGNMENT_OPENS] != labels[cq.SOURCE_ASSIGNMENT_DUE]


def test_a_quiz_contributes_an_opening_and_a_deadline_entry(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.quiz(group)
        rows = _student_rows(app, student.id)
    assert sorted(_sources(rows)) == [cq.SOURCE_QUIZ_DEADLINE, cq.SOURCE_QUIZ_OPENS]


def test_every_source_label_is_declared_and_distinct():
    assert set(cq.SOURCE_LABELS) == {
        cq.SOURCE_CLASS,
        cq.SOURCE_ASSIGNMENT_OPENS,
        cq.SOURCE_ASSIGNMENT_DUE,
        cq.SOURCE_QUIZ_OPENS,
        cq.SOURCE_QUIZ_DEADLINE,
        cq.SOURCE_LISTENING_OPENS,
        cq.SOURCE_LISTENING_DEADLINE,
        cq.SOURCE_CENTER_EVENT,
    }
    assert len(set(cq.SOURCE_LABELS.values())) == len(cq.SOURCE_LABELS)


# ===========================================================================
# Listening never duplicates its backing Quiz
# ===========================================================================


def test_a_listening_activity_is_not_also_an_ordinary_quiz_entry(app):
    with app.app_context():
        group, student, teacher = fx.setup_group("A")
        fx.listening(group, teacher, title="Listen A")
        rows = _student_rows(app, student.id)

    assert sorted(_sources(rows)) == [
        cq.SOURCE_LISTENING_DEADLINE,
        cq.SOURCE_LISTENING_OPENS,
    ]
    assert cq.SOURCE_QUIZ_OPENS not in _sources(rows)
    assert cq.SOURCE_QUIZ_DEADLINE not in _sources(rows)
    assert [row["title"] for row in rows] == ["Listen A", "Listen A"]


def test_an_ordinary_quiz_and_a_listening_activity_stay_separate(app):
    with app.app_context():
        group, student, teacher = fx.setup_group("A")
        fx.quiz(group, title="Plain quiz")
        fx.listening(group, teacher, title="Listen A")
        rows = _student_rows(app, student.id)
        by_source = {}
        for row in rows:
            by_source.setdefault(row["source"], []).append(row["title"])

    assert by_source[cq.SOURCE_QUIZ_OPENS] == ["Plain quiz"]
    assert by_source[cq.SOURCE_QUIZ_DEADLINE] == ["Plain quiz"]
    assert by_source[cq.SOURCE_LISTENING_OPENS] == ["Listen A"]
    assert by_source[cq.SOURCE_LISTENING_DEADLINE] == ["Listen A"]


def test_a_listening_entry_carries_the_activitys_public_id_not_the_quizzes(app):
    with app.app_context():
        group, student, teacher = fx.setup_group("A")
        backing, activity = fx.listening(group, teacher)
        quiz_public_id, activity_public_id = backing.public_id, activity.public_id
        rows = _student_rows(app, student.id)

    carried = {row["source_public_id"] for row in rows}
    assert carried == {activity_public_id}
    assert quiz_public_id not in carried


def test_a_speaking_activity_is_not_a_calendar_source(app):
    """An Assignment carrying the M06 Speaking extension is a Speaking
    activity, not an ordinary Assignment, and M10's approved source list
    names no speaking source."""
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.speaking_assignment(group, title="Speak about your week")
        assert _student_rows(app, student.id) == []
        fx.assignment(group, title="Ordinary essay")
        rows = _student_rows(app, student.id)
    assert {row["title"] for row in rows} == {"Ordinary essay"}


# ===========================================================================
# One source object is exactly one entry
# ===========================================================================


def test_a_student_in_two_groups_of_one_course_sees_each_groups_own_entries_once(app):
    """The membership join cannot multiply a source row:
    ``uq_enrollments_student_group`` means one Enrollment per Group, and
    each Assignment belongs to exactly one Group."""
    with app.app_context():
        first = fx.hierarchy("A")
        second = fx.group(
            db.session.get(AcademicTerm, first.academic_term_id),
            db.session.get(Course, first.course_id),
            "B",
        )
        student = fx.enroll(first, "s@example.com")
        fx.enroll_existing(second, student)
        fx.assignment(first, title="Only in A")
        fx.assignment(second, title="Only in B")
        rows = _student_rows(app, student.id)
        titles = [row["title"] for row in rows]

    assert titles.count("Only in A") == 2  # opens + due, and no more
    assert titles.count("Only in B") == 2


def test_a_group_with_two_active_teachers_shows_each_entry_once_per_teacher(app):
    with app.app_context():
        group = fx.hierarchy("A")
        first = fx.assign(group, "t1@example.com")
        second = fx.assign(group, "t2@example.com")
        fx.assignment(group, title="Shared essay")
        fx.schedule(group, day_of_week=0)
        for teacher in (first, second):
            rows = _teacher_rows(app, teacher.id)
            titles = [row["title"] for row in rows]
            assert titles.count("Shared essay") == 2
            assert len([r for r in rows if r["source"] == cq.SOURCE_CLASS]) == 4


def test_one_center_event_is_one_entry_however_many_groups_exist(app):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        second = fx.hierarchy("B")
        fx.enroll_existing(second, student)
        fx.event(creator, title="One holiday")
        rows = _student_rows(app, student.id)
    assert [row["title"] for row in rows] == ["One holiday"]


# ===========================================================================
# Deterministic ordering
# ===========================================================================


def test_timed_entries_come_before_all_day_entries_on_the_same_date(app):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        fx.event(creator, title="All day thing", event_date=date(2026, 5, 11))
        fx.timed_event(
            creator,
            title="Evening thing",
            event_date=date(2026, 5, 11),
            start_time=time(17, 0),
            end_time=time(19, 0),
        )
        fx.schedule(group, day_of_week=0, start_time=time(9, 0), end_time=time(11, 0))
        course_title = db.session.get(Course, group.course_id).title
        rows = [
            r for r in _student_rows(app, student.id)
            if r["event_date"] == date(2026, 5, 11)
        ]

    # The class at 09:00, then the 17:00 event, and the all-day event LAST
    # -- an all-day entry is a property of the whole day, so it reads
    # correctly after the day's timetable rather than at an invented
    # midnight.
    assert [row["title"] for row in rows] == [
        course_title,
        "Evening thing",
        "All day thing",
    ]
    assert [row["all_day"] for row in rows] == [False, False, True]


def test_entries_are_ordered_by_date_then_by_local_start_time(app):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        fx.timed_event(
            creator, title="Late", event_date=date(2026, 5, 12),
            start_time=time(20, 0), end_time=time(21, 0),
        )
        fx.timed_event(
            creator, title="Early", event_date=date(2026, 5, 12),
            start_time=time(8, 0), end_time=time(9, 0),
        )
        fx.timed_event(
            creator, title="Next day", event_date=date(2026, 5, 13),
            start_time=time(1, 0), end_time=time(2, 0),
        )
        rows = _student_rows(app, student.id)

    assert [row["title"] for row in rows] == ["Early", "Late", "Next day"]


def test_two_entries_at_the_same_moment_are_broken_by_source_then_public_id(app):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        first = fx.timed_event(
            creator, title="A", event_date=date(2026, 5, 12),
            start_time=time(8, 0), end_time=time(9, 0),
        )
        second = fx.timed_event(
            creator, title="B", event_date=date(2026, 5, 12),
            start_time=time(8, 0), end_time=time(9, 0),
        )
        expected = [
            title
            for _pid, title in sorted(
                [(first.public_id, "A"), (second.public_id, "B")]
            )
        ]
        rows = _student_rows(app, student.id)

    assert [row["title"] for row in rows] == expected
    # And the order is stable across repeated reads.
    with app.app_context():
        assert [row["title"] for row in _student_rows(app, student.id)] == expected


def test_the_sort_key_puts_a_single_instant_before_a_span_starting_together():
    instant = cq.calendar_row(
        cq.SOURCE_ASSIGNMENT_DUE, date(2026, 5, 11), "Due", start_time=time(9, 0),
        source_public_id="a",
    )
    span = cq.calendar_row(
        cq.SOURCE_CLASS, date(2026, 5, 11), "Class", start_time=time(9, 0),
        end_time=time(11, 0), source_public_id="b",
    )
    assert cq.sort_key(instant) < cq.sort_key(span)


def test_group_by_day_emits_only_days_that_have_entries(app):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        fx.event(creator, event_date=date(2026, 5, 4))
        fx.event(creator, event_date=date(2026, 5, 20))
        rows = _student_rows(app, student.id)
        days = cq.group_by_day(rows)

    assert [day["date"] for day in days] == [date(2026, 5, 4), date(2026, 5, 20)]
    assert all(len(day["rows"]) == 1 for day in days)


# ===========================================================================
# What a row may and may not carry
# ===========================================================================


def test_a_row_carries_only_safe_presentation_fields(app):
    with app.app_context():
        creator = fx.admin()
        group, student, teacher = fx.setup_group("A")
        fx.schedule(group, day_of_week=0)
        fx.assignment(group)
        fx.quiz(group)
        fx.listening(group, teacher)
        fx.event(creator, details="Some details.", location="Hall")
        rows = _student_rows(app, student.id)

    assert rows
    expected = {
        "source",
        "source_label",
        "event_date",
        "start_time",
        "end_time",
        "all_day",
        "title",
        "context",
        "location",
        "details",
        "group_public_id",
        "source_public_id",
        "cancelled",
        "status_label",
        "url",
    }
    for row in rows:
        assert set(row) == expected
        # Nothing that could be an internal id, a score or a secret.
        for value in row.values():
            assert not isinstance(value, (float,)), row
        for public_id in (row["group_public_id"], row["source_public_id"]):
            if public_id is not None:
                assert not public_id.isdigit(), row


def test_a_group_scoped_row_names_its_group_and_course_and_nothing_else(app):
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        group_name = group.name
        course_title = db.session.get(Course, group.course_id).title
        fx.assignment(group)
        rows = _student_rows(app, student.id)

    assert all(row["context"] == f"{group_name} · {course_title}" for row in rows)


def test_a_class_entry_is_titled_by_its_course_and_contexted_by_its_group(app):
    """A class *is* its Course meeting, so the Course title is the
    entry's own name; repeating it in the context would print the same
    word twice on one card. The Group is what tells two Groups of one
    Course apart."""
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        group_name = group.name
        course_title = db.session.get(Course, group.course_id).title
        fx.schedule(group, day_of_week=0)
        rows = [
            r for r in _student_rows(app, student.id) if r["source"] == cq.SOURCE_CLASS
        ]

    assert rows
    assert all(row["title"] == course_title for row in rows)
    assert all(row["context"] == group_name for row in rows)


def test_two_groups_of_one_course_produce_distinguishable_class_entries(app):
    with app.app_context():
        first = fx.hierarchy("A")
        second = fx.group(
            db.session.get(AcademicTerm, first.academic_term_id),
            db.session.get(Course, first.course_id),
            "B",
        )
        student = fx.enroll(first, "s@example.com")
        fx.enroll_existing(second, student)
        fx.schedule(first, day_of_week=0)
        fx.schedule(second, day_of_week=0)
        names = {first.name, second.name}
        rows = [
            r for r in _student_rows(app, student.id) if r["source"] == cq.SOURCE_CLASS
        ]

    assert len(rows) == 8  # four Mondays, two Groups
    assert {row["context"] for row in rows} == names


def test_a_center_event_row_names_no_group(app):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        fx.event(creator)
        rows = _student_rows(app, student.id)

    assert len(rows) == 1
    assert rows[0]["context"] == ""
    assert rows[0]["group_public_id"] is None


def test_every_row_starts_with_no_url_until_a_blueprint_attaches_one(app):
    """The query layer is Flask-independent, and where an entry leads is
    a property of *who is looking* -- so a row leaves this layer with
    ``url = None`` and each role's blueprint fills it in."""
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.assignment(group)
        rows = _student_rows(app, student.id)
    assert rows and all(row["url"] is None for row in rows)


# ===========================================================================
# Bounds
# ===========================================================================


def test_a_student_read_without_a_reference_moment_fails_loudly(app):
    """``opens_at <= NULL`` is never true, so a missing reference moment
    would silently return an empty calendar -- indistinguishable from a
    Student with nothing on. It raises instead."""
    with app.app_context():
        group, student, _teacher = fx.setup_group("A")
        fx.assignment(group)
        for fetch in (cq.assignment_source_rows, cq.quiz_source_rows, cq.listening_source_rows):
            with pytest.raises(ValueError):
                fetch(MAY, student_id=student.id, reference_utc=None)
        # A Teacher and an Administrator read needs no such moment.
        teacher_rows = cq.assignment_source_rows(MAY, teacher_id=None, reference_utc=None)
        assert isinstance(teacher_rows, list)


def test_the_declared_bounds_are_what_the_part_requires():
    assert cq.MAX_RANGE_DAYS == 62
    assert cq.SOURCE_ROW_CAP > 0
    assert cq.MAX_CALENDAR_ROWS > 0


def test_build_calendar_reports_truncation_rather_than_hiding_it(app, monkeypatch):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        for index in range(5):
            fx.event(creator, title=f"Event {index}", event_date=date(2026, 5, 4))
        monkeypatch.setattr(cq, "MAX_CALENDAR_ROWS", 3)
        rows, truncated = cq.build_calendar(
            MAY, app.config["APP_TIMEZONE"], _reference(app), student_id=student.id
        )
    assert len(rows) == 3
    assert truncated is True


def test_build_calendar_does_not_report_truncation_when_nothing_was_cut(app):
    with app.app_context():
        creator = fx.admin()
        group, student, _teacher = fx.setup_group("A")
        fx.event(creator)
        rows, truncated = cq.build_calendar(
            MAY, app.config["APP_TIMEZONE"], _reference(app), student_id=student.id
        )
    assert len(rows) == 1 and truncated is False
