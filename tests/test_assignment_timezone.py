"""Phase 4 / M01 timezone inverse: ``from_app_local`` and
``utc_reference_now``.

These sit beside the existing M09 ``to_app_local`` / ``app_now`` and
deliberately reuse its resolution policy, so the tests here also assert
that the two directions agree and that the existing Schedule occurrence
behaviour is untouched.

**Host limitation.** The development machine running this suite has no
IANA time-zone database (``zoneinfo`` raises ``ZoneInfoNotFoundError``
for every named zone, which is exactly why the deployment fixed-offset
fallback exists). The real-IANA DST tests below are therefore skipped
here and only run where ``tzdata`` is installed. To keep the DST logic
genuinely covered on *every* host, the gap/fold behaviour is additionally
proven against a purpose-built fold-aware ``tzinfo`` injected in place of
the zone lookup -- that exercises the real code path, not a mock of it.
"""

from datetime import date, datetime, time, timedelta, timezone, tzinfo

import pytest

from app.services import schedule_occurrences as tzmod
from app.services.schedule_occurrences import (
    LocalTimeError,
    TimezoneConfigError,
    app_now,
    current_or_next_occurrence,
    from_app_local,
    slot_spec,
    to_app_local,
    utc_reference_now,
)


def _iana_available(name="America/New_York"):
    return tzmod._resolve_zoneinfo(name) is not None


requires_iana = pytest.mark.skipif(
    not _iana_available(),
    reason="host has no IANA time-zone database (no tzdata installed)",
)


# ---------------------------------------------------------------------------
# A fold-aware zone with DST, so the gap/ambiguity paths are covered on
# every host -- including this one, which has no IANA database.
# ---------------------------------------------------------------------------

_STD = timedelta(hours=-5)
_DST = timedelta(hours=-4)
#: Local standard wall clock at which the clocks jump forward to 03:00.
_GAP_START = datetime(2026, 3, 8, 2, 0)
#: Local daylight wall clock at which the clocks fall back to 01:00.
_FOLD_END = datetime(2026, 11, 1, 2, 0)
_FOLD_START = _FOLD_END - timedelta(hours=1)


class _DstZone(tzinfo):
    """A minimal US-style DST zone that honours ``fold`` the same way
    ``zoneinfo`` does: in a gap, ``fold=0`` reports the offset *before*
    the transition and ``fold=1`` the offset *after*; in a repeat,
    ``fold=0`` is the first (daylight) pass and ``fold=1`` the second."""

    def utcoffset(self, dt):
        if dt is None:
            return _STD
        naive = dt.replace(tzinfo=None)
        if _GAP_START <= naive < _GAP_START + timedelta(hours=1):
            return _STD if dt.fold == 0 else _DST
        if _FOLD_START <= naive < _FOLD_END:
            return _DST if dt.fold == 0 else _STD
        if _GAP_START + timedelta(hours=1) <= naive < _FOLD_START:
            return _DST
        return _STD

    def dst(self, dt):
        offset = self.utcoffset(dt)
        return _DST - _STD if offset == _DST else timedelta(0)

    def tzname(self, dt):
        return "EDT" if self.dst(dt) else "EST"

    def fromutc(self, dt):
        naive_utc = dt.replace(tzinfo=None)
        if (_GAP_START - _STD) <= naive_utc < (_FOLD_END - _DST):
            return (naive_utc + _DST).replace(tzinfo=self)
        return (naive_utc + _STD).replace(tzinfo=self)


@pytest.fixture
def dst_zone(monkeypatch):
    """Make ``_resolve_zoneinfo`` return the fold-aware zone above for
    one specific name, so ``from_app_local`` runs its *real* IANA branch."""
    zone = _DstZone()
    original = tzmod._resolve_zoneinfo

    def resolve(tz_name):
        return zone if tz_name == "Test/DstZone" else original(tz_name)

    monkeypatch.setattr(tzmod, "_resolve_zoneinfo", resolve)
    return "Test/DstZone"


# ---------------------------------------------------------------------------
# fixed offsets, deployment fallback, and round trips
# ---------------------------------------------------------------------------


LOCAL = datetime(2026, 5, 1, 12, 0)


@pytest.mark.parametrize("tz_name", ["UTC", "utc", "GMT", "+00:00"])
def test_utc_is_an_identity(tz_name):
    assert from_app_local(tz_name, LOCAL) == LOCAL


@pytest.mark.parametrize(
    "tz_name, expected",
    [
        ("+02:00", datetime(2026, 5, 1, 10, 0)),
        ("UTC+2", datetime(2026, 5, 1, 10, 0)),
        ("-05:30", datetime(2026, 5, 1, 17, 30)),
        ("GMT-0530", datetime(2026, 5, 1, 17, 30)),
        ("+14:00", datetime(2026, 4, 30, 22, 0)),
    ],
)
def test_explicit_fixed_offsets_both_signs(tz_name, expected):
    assert from_app_local(tz_name, LOCAL) == expected


def test_deployment_fallback_is_reused_not_reimplemented():
    """``Africa/Tripoli`` has no IANA entry on this host, so it must go
    through the same +02:00 deployment table ``to_app_local`` uses."""
    assert from_app_local("Africa/Tripoli", LOCAL) == datetime(2026, 5, 1, 10, 0)


@pytest.mark.parametrize(
    "tz_name", ["UTC", "+02:00", "-05:30", "Africa/Tripoli", "GMT+3"]
)
def test_local_to_utc_to_local_round_trip(tz_name):
    assert to_app_local(tz_name, from_app_local(tz_name, LOCAL)) == LOCAL


@pytest.mark.parametrize(
    "tz_name", ["UTC", "+02:00", "-05:30", "Africa/Tripoli", "GMT+3"]
)
def test_utc_to_local_to_utc_round_trip(tz_name):
    utc = datetime(2026, 5, 1, 9, 15)
    assert from_app_local(tz_name, to_app_local(tz_name, utc)) == utc


# ---------------------------------------------------------------------------
# fail-closed configuration and contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tz_name", ["", "   ", None])
def test_empty_timezone_fails_closed(tz_name):
    with pytest.raises(TimezoneConfigError):
        from_app_local(tz_name, LOCAL)


def test_unresolvable_named_timezone_fails_closed():
    with pytest.raises(TimezoneConfigError):
        from_app_local("Not/AZone", LOCAL)


@pytest.mark.parametrize("tz_name", ["+15:00", "UTC+99", "+abc"])
def test_malformed_or_out_of_range_offset_fails_closed(tz_name):
    with pytest.raises(TimezoneConfigError):
        from_app_local(tz_name, LOCAL)


def test_aware_input_is_rejected_not_silently_converted():
    """An aware value already carries its own offset; treating it as a
    wall clock in APP_TIMEZONE would discard that silently."""
    aware = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError) as exc:
        from_app_local("UTC", aware)
    assert not isinstance(exc.value, LocalTimeError)


# ---------------------------------------------------------------------------
# DST -- never guessed
# ---------------------------------------------------------------------------


def test_nonexistent_local_time_is_rejected(dst_zone):
    with pytest.raises(LocalTimeError) as exc:
        from_app_local(dst_zone, datetime(2026, 3, 8, 2, 30))
    assert "does not exist" in str(exc.value)
    # A safe, form-ready message: no offsets, no zone internals.
    assert "-05:00" not in str(exc.value) and "tzinfo" not in str(exc.value)


def test_ambiguous_local_time_is_rejected(dst_zone):
    with pytest.raises(LocalTimeError) as exc:
        from_app_local(dst_zone, datetime(2026, 11, 1, 1, 30))
    assert "happens twice" in str(exc.value)


def test_neither_fold_is_silently_chosen(dst_zone):
    """Both DST edge cases raise -- there is no code path that returns
    the fold=0 or fold=1 instant for them."""
    for value in (datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)):
        with pytest.raises(LocalTimeError):
            from_app_local(dst_zone, value)


@pytest.mark.parametrize(
    "local, expected_utc",
    [
        (datetime(2026, 1, 15, 12, 0), datetime(2026, 1, 15, 17, 0)),   # standard
        (datetime(2026, 7, 1, 12, 0), datetime(2026, 7, 1, 16, 0)),     # daylight
        (datetime(2026, 3, 8, 3, 0), datetime(2026, 3, 8, 7, 0)),       # just after the gap
        (datetime(2026, 11, 1, 2, 0), datetime(2026, 11, 1, 7, 0)),     # just after the repeat
    ],
)
def test_unambiguous_dst_zone_values_convert(dst_zone, local, expected_utc):
    assert from_app_local(dst_zone, local) == expected_utc


@requires_iana
def test_real_iana_zone_converts():
    assert from_app_local("America/New_York", datetime(2026, 7, 1, 12, 0)) == datetime(
        2026, 7, 1, 16, 0
    )


@requires_iana
def test_real_iana_nonexistent_local_time_is_rejected():
    with pytest.raises(LocalTimeError):
        from_app_local("America/New_York", datetime(2026, 3, 8, 2, 30))


@requires_iana
def test_real_iana_ambiguous_local_time_is_rejected():
    with pytest.raises(LocalTimeError):
        from_app_local("America/New_York", datetime(2026, 11, 1, 1, 30))


@requires_iana
def test_real_iana_round_trip():
    utc = datetime(2026, 7, 1, 16, 0)
    assert from_app_local("America/New_York", to_app_local("America/New_York", utc)) == utc


# ---------------------------------------------------------------------------
# utc_reference_now -- one injectable moment
# ---------------------------------------------------------------------------


def test_reference_now_is_naive_utc_from_an_aware_value():
    aware = datetime(2026, 5, 1, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    reference = utc_reference_now(aware)
    assert reference == datetime(2026, 5, 1, 9, 0)
    assert reference.tzinfo is None


def test_reference_now_accepts_a_naive_utc_value_unchanged():
    assert utc_reference_now(datetime(2026, 5, 1, 12, 0)) == datetime(2026, 5, 1, 12, 0)


def test_reference_now_reads_the_clock_only_when_nothing_is_injected():
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    reference = utc_reference_now()
    after = datetime.now(timezone.utc).replace(tzinfo=None)
    assert before <= reference <= after
    assert reference.tzinfo is None


def test_reference_and_app_now_describe_the_same_instant():
    """The Student dashboard derives both from one ``utc_now``; they must
    stay the same moment expressed in two frames."""
    utc_now = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    local = app_now("+02:00", utc_now)
    reference = utc_reference_now(utc_now)
    assert local == datetime(2026, 5, 1, 11, 0)
    assert from_app_local("+02:00", local) == reference


# ---------------------------------------------------------------------------
# no regression to the M08/M09 Schedule wall-clock semantics
# ---------------------------------------------------------------------------


def test_schedule_occurrences_still_use_local_wall_clock():
    """Schedules store local civil times and are never converted to UTC
    (the M08 decision). Adding an inverse conversion must not change
    that."""

    class _Slot:
        day_of_week = 2  # Wednesday
        start_time = time(9, 0)
        end_time = time(10, 30)
        effective_start_date = date(2026, 5, 1)
        effective_end_date = date(2026, 6, 30)

    spec = slot_spec(_Slot())
    occurrence = current_or_next_occurrence(spec, datetime(2026, 5, 4, 8, 0))
    assert occurrence.start == datetime(2026, 5, 6, 9, 0)
    assert occurrence.end == datetime(2026, 5, 6, 10, 30)
    assert occurrence.start.tzinfo is None
