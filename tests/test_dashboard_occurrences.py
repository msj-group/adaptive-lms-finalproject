"""M09 recurring-occurrence service -- pure functions over naive local
datetimes plus the APP_TIMEZONE resolution helper.

Fully deterministic: every test injects an explicit ``now`` (or an
explicit UTC instant for the timezone helper), so nothing here depends on
the wall clock or on the host having an IANA time-zone database.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest

import app.services.schedule_occurrences as occ
from app.services.schedule_occurrences import (
    SlotSpec,
    TimezoneConfigError,
    app_now,
    current_or_next_occurrence,
    earliest_occurrence,
    iter_occurrences_between,
    slot_spec,
    to_app_local,
    upcoming_occurrences,
)

MON, TUE, WED, THU = 0, 1, 2, 3


def _spec(day=MON, start="09:00", end="10:30", eff_start=date(2026, 1, 1), eff_end=date(2026, 12, 31), ref="s"):
    return SlotSpec(
        day_of_week=day,
        start_time=datetime.strptime(start, "%H:%M").time(),
        end_time=datetime.strptime(end, "%H:%M").time(),
        effective_start_date=eff_start,
        effective_end_date=eff_end,
        ref=ref,
    )


# ---------------------------------------------------------------------------
# APP_TIMEZONE resolution
# ---------------------------------------------------------------------------


UTC_MOMENT = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)


def test_to_app_local_utc_is_identity():
    assert to_app_local("UTC", UTC_MOMENT) == datetime(2026, 9, 1, 7, 0)
    assert to_app_local("utc", UTC_MOMENT).tzinfo is None
    assert to_app_local("GMT", UTC_MOMENT) == datetime(2026, 9, 1, 7, 0)


def test_to_app_local_explicit_fixed_offsets():
    assert to_app_local("+02:00", UTC_MOMENT) == datetime(2026, 9, 1, 9, 0)
    assert to_app_local("UTC+2", UTC_MOMENT) == datetime(2026, 9, 1, 9, 0)
    assert to_app_local("-0530", UTC_MOMENT) == datetime(2026, 9, 1, 1, 30)
    assert to_app_local("GMT-05:30", UTC_MOMENT) == datetime(2026, 9, 1, 1, 30)
    assert to_app_local("+14:00", UTC_MOMENT) == datetime(2026, 9, 1, 21, 0)  # boundary is valid


def test_to_app_local_accepts_naive_utc():
    assert to_app_local("+01:00", datetime(2026, 9, 1, 7, 0)) == datetime(2026, 9, 1, 8, 0)


def test_resolvable_iana_zone_takes_precedence_over_deployment_fallback(monkeypatch):
    """When the host CAN resolve an IANA name, that wins over the
    built-in deployment fixed-offset table."""
    fake_plus_three = timezone(timedelta(hours=3))
    monkeypatch.setattr(
        occ, "_resolve_zoneinfo", lambda name: fake_plus_three if name == "Africa/Tripoli" else None
    )
    assert to_app_local("Africa/Tripoli", UTC_MOMENT) == datetime(2026, 9, 1, 10, 0)  # +3, not +2


def test_deployment_fallback_used_only_when_iana_unavailable(monkeypatch):
    """No IANA database -> the documented Africa/Tripoli +02:00 fallback."""
    monkeypatch.setattr(occ, "_resolve_zoneinfo", lambda name: None)
    utc = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)
    assert to_app_local("Africa/Tripoli", utc) == datetime(2027, 1, 1, 1, 30)


def test_empty_timezone_raises_config_error():
    with pytest.raises(TimezoneConfigError):
        to_app_local("", UTC_MOMENT)
    with pytest.raises(TimezoneConfigError):
        to_app_local("   ", UTC_MOMENT)
    with pytest.raises(TimezoneConfigError):
        to_app_local(None, UTC_MOMENT)


def test_unresolvable_timezone_raises_instead_of_returning_utc(monkeypatch):
    monkeypatch.setattr(occ, "_resolve_zoneinfo", lambda name: None)
    with pytest.raises(TimezoneConfigError):
        to_app_local("Mars/Olympus_Mons", UTC_MOMENT)
    with pytest.raises(TimezoneConfigError):
        to_app_local("America/New_York", UTC_MOMENT)  # real name, but host can't resolve it here


@pytest.mark.parametrize("bad", ["+15:00", "+1401", "UTC+14:30", "-2000", "+abc", "GMT+", "UTC-99"])
def test_invalid_fixed_offsets_are_rejected(bad):
    with pytest.raises(TimezoneConfigError):
        to_app_local(bad, UTC_MOMENT)


def test_app_now_propagates_config_error():
    with pytest.raises(TimezoneConfigError):
        app_now("", utc_now=UTC_MOMENT)
    with pytest.raises(TimezoneConfigError):
        app_now("+99:00", utc_now=UTC_MOMENT)


def test_app_now_valid_offset_is_deterministic_with_injected_utc():
    assert app_now("+02:00", utc_now=UTC_MOMENT) == datetime(2026, 9, 1, 9, 0)


# ---------------------------------------------------------------------------
# current_or_next_occurrence -- before / during / after a same-day class
# ---------------------------------------------------------------------------


def test_before_class_today_returns_todays_occurrence():
    spec = _spec(MON, "09:00", "10:30")
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 7, 8, 0))  # Mon
    assert occ.start == datetime(2026, 9, 7, 9, 0)
    assert occ.end == datetime(2026, 9, 7, 10, 30)


def test_during_class_is_current():
    spec = _spec(MON, "09:00", "10:30")
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 7, 9, 30))
    assert occ.start == datetime(2026, 9, 7, 9, 0)
    assert occ.start <= datetime(2026, 9, 7, 9, 30) < occ.end


def test_exactly_at_start_is_current():
    spec = _spec(MON, "09:00", "10:30")
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 7, 9, 0))
    assert occ.start == datetime(2026, 9, 7, 9, 0)


def test_exactly_at_end_rolls_to_next_week():
    spec = _spec(MON, "09:00", "10:30")
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 7, 10, 30))
    assert occ.start == datetime(2026, 9, 14, 9, 0)


def test_after_class_today_rolls_to_next_week():
    spec = _spec(MON, "09:00", "10:30")
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 7, 12, 0))
    assert occ.start == datetime(2026, 9, 14, 9, 0)


def test_weekly_rollover_from_a_non_class_weekday():
    spec = _spec(MON, "09:00", "10:30")
    # Wednesday -> next occurrence is the following Monday
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 9, 15, 0))
    assert occ.start == datetime(2026, 9, 14, 9, 0)


# ---------------------------------------------------------------------------
# inclusive effective-date boundaries / expired / future
# ---------------------------------------------------------------------------


def test_effective_start_boundary_is_inclusive():
    # 2026-09-07 is a Monday; effective range starts exactly that day
    spec = _spec(MON, eff_start=date(2026, 9, 7), eff_end=date(2026, 12, 31))
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 7, 8, 0))
    assert occ.start.date() == date(2026, 9, 7)


def test_effective_end_boundary_is_inclusive():
    # last Monday of the range is 2026-09-28
    spec = _spec(MON, eff_start=date(2026, 9, 1), eff_end=date(2026, 9, 28))
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 28, 8, 0))
    assert occ.start.date() == date(2026, 9, 28)


def test_expired_range_returns_none():
    spec = _spec(MON, eff_start=date(2026, 1, 5), eff_end=date(2026, 1, 26))
    assert current_or_next_occurrence(spec, datetime(2026, 9, 7, 8, 0)) is None


def test_range_after_last_occurrence_but_within_end_date_returns_none():
    # range ends Wed 2026-09-30; last Monday is 2026-09-28; now is Tue 09-29
    spec = _spec(MON, eff_start=date(2026, 9, 1), eff_end=date(2026, 9, 30))
    assert current_or_next_occurrence(spec, datetime(2026, 9, 29, 8, 0)) is None


def test_future_range_returns_first_occurrence():
    spec = _spec(WED, eff_start=date(2026, 11, 1), eff_end=date(2026, 12, 31))
    occ = current_or_next_occurrence(spec, datetime(2026, 9, 7, 8, 0))
    assert occ.start.date().weekday() == WED
    assert occ.start.date() >= date(2026, 11, 1)


def test_range_containing_no_occurrence_of_weekday_returns_none():
    # Mon slot whose effective range is Tue..Sun only
    spec = _spec(MON, eff_start=date(2026, 9, 8), eff_end=date(2026, 9, 13))
    assert current_or_next_occurrence(spec, datetime(2026, 9, 1, 0, 0)) is None


# ---------------------------------------------------------------------------
# iter_occurrences_between / upcoming_occurrences / earliest_occurrence
# ---------------------------------------------------------------------------


def test_iter_occurrences_between_is_inclusive_and_bounded():
    spec = _spec(MON, eff_start=date(2026, 1, 1), eff_end=date(2026, 12, 31))
    got = list(iter_occurrences_between(spec, date(2026, 9, 7), date(2026, 9, 28)))
    assert [o.start.date() for o in got] == [
        date(2026, 9, 7),
        date(2026, 9, 14),
        date(2026, 9, 21),
        date(2026, 9, 28),
    ]


def test_upcoming_occurrences_window_and_sort():
    now = datetime(2026, 9, 7, 8, 0)  # Monday morning
    mon = _spec(MON, "09:00", "10:00", ref="MON")
    wed = _spec(WED, "11:00", "12:00", ref="WED")
    tue_far = _spec(TUE, "09:00", "10:00", eff_start=date(2026, 10, 1), eff_end=date(2026, 12, 31), ref="TUE")
    rows = upcoming_occurrences([wed, mon, tue_far], now)
    # only this week's Monday and Wednesday fall inside now .. now+7d
    assert [o.ref for o in rows] == ["MON", "WED"]
    assert rows[0].start < rows[1].start


def test_upcoming_occurrences_excludes_already_ended_today():
    now = datetime(2026, 9, 7, 10, 30)  # Monday, class 09:00-10:00 already over
    spec = _spec(MON, "09:00", "10:00", ref="MON")
    rows = upcoming_occurrences([spec], now)
    assert [o.start.date() for o in rows] == [date(2026, 9, 14)]


def test_upcoming_occurrences_keeps_in_progress_class():
    now = datetime(2026, 9, 7, 9, 30)  # during the class
    spec = _spec(MON, "09:00", "10:00", ref="MON")
    rows = upcoming_occurrences([spec], now)
    assert rows[0].start == datetime(2026, 9, 7, 9, 0)


def test_upcoming_occurrences_capped():
    now = datetime(2026, 9, 7, 8, 0)
    specs = [_spec(MON, f"{h:02d}:00", f"{h:02d}:30", ref=f"s{h}") for h in range(6, 20)]
    rows = upcoming_occurrences(specs, now, cap=3)
    assert len(rows) == 3


def test_earliest_occurrence_picks_the_soonest_across_specs():
    now = datetime(2026, 9, 7, 8, 0)  # Monday
    mon_late = _spec(MON, "15:00", "16:00", ref="MON")
    tue_early = _spec(TUE, "08:00", "09:00", ref="TUE")
    assert earliest_occurrence([mon_late, tue_early], now).ref == "MON"  # same day, sooner


def test_earliest_occurrence_none_when_all_expired():
    now = datetime(2026, 9, 7, 8, 0)
    expired = _spec(MON, eff_start=date(2026, 1, 1), eff_end=date(2026, 2, 1))
    assert earliest_occurrence([expired], now) is None


def test_slot_spec_reads_schedule_like_object():
    class FakeSchedule:
        day_of_week = 2
        start_time = time(9, 0)
        end_time = time(10, 0)
        effective_start_date = date(2026, 1, 1)
        effective_end_date = date(2026, 6, 1)

    fs = FakeSchedule()
    spec = slot_spec(fs)
    assert spec.day_of_week == 2 and spec.ref is fs
    spec2 = slot_spec(fs, ref={"x": 1})
    assert spec2.ref == {"x": 1}
