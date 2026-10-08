"""Current / next occurrences of recurring weekly Group schedules (M09).

Flask-independent and (apart from an optional ``zoneinfo`` lookup)
stdlib-only: no ORM, no ``request``/``flash``, no route decorators. The
dashboard query layer eager-loads rows, builds :class:`SlotSpec` values
from them, and calls the pure functions here; nothing in this module
issues a database query, takes a lock, or writes anything.

Recurrence convention (unchanged from M08): ``day_of_week`` is an
integer, Monday=0 .. Sunday=6, matching ``datetime.date.weekday()``. A
slot's effective date range is inclusive at both ends, and a single
occurrence spans the half-open wall-clock interval
``[combine(date, start_time), combine(date, end_time))`` on that civil
day -- overnight slots are out of scope.

**Timezone.** Weekly ``start_time`` / ``end_time`` are local civil
values interpreted through ``APP_TIMEZONE`` -- they are never converted
to UTC (the M08 decision). All occurrence arithmetic here is done in
naive *local wall-clock* datetimes; callers pass a local ``now`` (real
callers via :func:`app_now`, tests inject a fixed value). :func:`to_app_local`
resolves ``APP_TIMEZONE`` **fail-closed**: an empty / invalid /
unresolvable value raises :class:`TimezoneConfigError` rather than being
silently treated as UTC (a wrong "now" on every dashboard is worse than a
loud error).
"""

from collections import namedtuple
from datetime import datetime, timedelta, timezone

try:  # pragma: no cover - availability depends on the host tz database
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # pragma: no cover
    ZoneInfo = None

    class ZoneInfoNotFoundError(Exception):
        pass


class TimezoneConfigError(RuntimeError):
    """``APP_TIMEZONE`` could not be resolved to a real timezone.

    Raised instead of falling back to UTC: a dashboard whose "now" is
    silently wrong by the center's offset is worse than a clear failure
    the operator must fix (install ``tzdata``, or set ``APP_TIMEZONE`` to
    ``UTC`` / an explicit offset).
    """


class LocalTimeError(ValueError):
    """A naive *local wall-clock* value that ``APP_TIMEZONE`` cannot map
    to exactly one UTC instant (Phase 4 / M01).

    Raised by :func:`from_app_local` for the two DST edge cases a real
    IANA zone produces, and only for those:

    - a **nonexistent** local time -- the hour skipped by a spring-forward
      transition, which no UTC instant maps back to;
    - an **ambiguous** local time -- the hour repeated by a fall-back
      transition, which two different UTC instants map to.

    Deliberately separate from :class:`TimezoneConfigError`: that one
    means the *deployment* is misconfigured and nothing can be rendered,
    while this one means *this submitted value* is not a usable moment
    and the person filling the form must pick another. It is a
    ``ValueError`` subclass carrying a message safe to show verbatim as a
    form validation error -- no timezone-database internals, no offsets,
    no driver text.

    The helper never resolves either case by guessing a ``fold``. A
    silently chosen fold would place a deadline an hour away from what
    the Teacher typed, in the one direction nobody would check.
    """


# The maximum real UTC offset (ISO 8601 / IANA tz database bound): ±14:00.
_MAX_OFFSET_MINUTES = 14 * 60

# Deployment-specific fixed offsets, consulted ONLY after an IANA lookup
# has been *attempted and failed* on this host (e.g. Windows dev without
# the `tzdata` package). Production Linux resolves the real zone via
# `zoneinfo` and never reaches this table. Africa/Tripoli is UTC+02:00
# year-round (Libya abolished DST in 2013). UTC/GMT are handled earlier
# by the explicit-offset parser, not here.
_DEPLOYMENT_FIXED_OFFSET_MINUTES = {
    "africa/tripoli": 120,
}


def _resolve_zoneinfo(tz_name):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _parse_fixed_offset_minutes(tz_name):
    """Minutes east of UTC for an explicit fixed-offset spec -- ``UTC`` /
    ``GMT``, ``UTC+2`` / ``GMT-05:30``, ``+02:00`` / ``-0530`` / ``+2``.

    Returns ``None`` when `tz_name` is not an offset spec at all (it may
    be an IANA name). Raises :class:`TimezoneConfigError` when it clearly
    *is* an offset spec but is malformed or out of range (beyond ±14:00,
    or ±14 with non-zero minutes).
    """
    raw = tz_name.strip()
    if raw.upper() in ("UTC", "GMT"):
        return 0
    body = raw
    for prefix in ("UTC", "GMT"):
        if body.upper().startswith(prefix):
            body = body[len(prefix):]
            break
    body = body.strip()
    if body[:1] not in ("+", "-"):
        return None  # not an offset spec -- try IANA / fallback next
    sign = 1 if body[0] == "+" else -1
    digits = body[1:].replace(":", "")
    if not digits.isdigit() or len(digits) not in (1, 2, 3, 4):
        raise TimezoneConfigError(f"APP_TIMEZONE={tz_name!r} is not a valid UTC offset.")
    if len(digits) <= 2:
        hours, minutes = int(digits), 0
    else:
        hours, minutes = int(digits[:-2]), int(digits[-2:])
    total = sign * (hours * 60 + minutes)
    if minutes >= 60 or abs(total) > _MAX_OFFSET_MINUTES or (hours == 14 and minutes != 0):
        raise TimezoneConfigError(
            f"APP_TIMEZONE={tz_name!r} is out of range -- a real UTC offset is within ±14:00."
        )
    return total


def to_app_local(tz_name, moment):
    """Return `moment` as a naive local wall-clock ``datetime`` in
    ``tz_name``.

    `moment` may be timezone-aware or a naive value already understood as
    UTC. Resolution order (**fail-closed** -- no silent UTC fallback):

    1. an explicit fixed-offset spec (``UTC`` / ``GMT`` / ``UTC+2`` /
       ``+02:00`` ...); an out-of-range or malformed offset raises
       :class:`TimezoneConfigError`;
    2. a named IANA zone the host's ``zoneinfo`` can load (production
       Linux, or any host with the ``tzdata`` package) -- attempted
       *before* any deployment fallback;
    3. only if step 2 could not resolve it: a small deployment-specific
       fixed-offset table (currently just ``Africa/Tripoli`` -> +02:00),
       so occurrence math stays deterministic on a host without an IANA
       database;
    4. otherwise :class:`TimezoneConfigError` -- an empty, unknown, or
       unresolvable ``APP_TIMEZONE`` is a configuration bug, not a reason
       to pretend the center runs on UTC.
    """
    if not tz_name or not tz_name.strip():
        raise TimezoneConfigError(
            "APP_TIMEZONE is empty -- set a real timezone (e.g. 'Africa/Tripoli', 'UTC', "
            "or an explicit offset like '+02:00')."
        )

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)

    fixed = _parse_fixed_offset_minutes(tz_name)
    if fixed is not None:
        return (moment + timedelta(minutes=fixed)).replace(tzinfo=None)

    tz = _resolve_zoneinfo(tz_name)
    if tz is not None:
        return moment.astimezone(tz).replace(tzinfo=None)

    fallback = _DEPLOYMENT_FIXED_OFFSET_MINUTES.get(tz_name.strip().lower())
    if fallback is not None:
        return (moment + timedelta(minutes=fallback)).replace(tzinfo=None)

    raise TimezoneConfigError(
        f"APP_TIMEZONE={tz_name!r} could not be resolved: it is not a fixed offset, this "
        "host has no IANA time-zone database entry for it, and it is not a known deployment "
        "fallback. Install `tzdata`, or set APP_TIMEZONE to 'UTC' or an explicit offset."
    )


def app_now(tz_name, utc_now=None):
    """The current local wall-clock ``datetime`` in ``tz_name``. Pass
    `utc_now` (aware or naive-UTC) to make it deterministic in tests.
    Propagates :class:`TimezoneConfigError` from :func:`to_app_local`.
    """
    return to_app_local(tz_name, utc_now or datetime.now(timezone.utc))


def from_app_local(tz_name, local_moment):
    """The inverse of :func:`to_app_local`: take a **naive local
    wall-clock** ``datetime`` in `tz_name` and return the naive **UTC**
    ``datetime`` that is the same instant (Phase 4 / M01).

    This is what turns what a Teacher typed into a form -- always a local
    civil time, shown with its timezone label -- into the canonical UTC
    value stored in ``assignments.opens_at`` / ``due_at``. It deliberately
    reuses this module's *existing* resolution policy rather than
    re-implementing it, so the fixed-offset parser, the IANA lookup, the
    deployment fallback table, and the fail-closed
    :class:`TimezoneConfigError` behave identically in both directions:

    1. an explicit fixed-offset spec (``UTC`` / ``GMT`` / ``UTC+2`` /
       ``+02:00`` ...) -- subtract the offset; a fixed offset has no DST,
       so every local value maps to exactly one instant;
    2. a named IANA zone the host's ``zoneinfo`` can load -- see the DST
       handling below;
    3. only if step 2 could not resolve it: the same small
       deployment-specific fixed-offset table
       (``Africa/Tripoli`` -> +02:00), treated exactly like step 1;
    4. otherwise :class:`TimezoneConfigError`.

    **DST is never guessed.** For a real IANA zone the value is
    interpreted at both folds and validated by round trip:

    - if converting back from the ``fold=0`` instant does not reproduce
      the submitted wall clock, the local time **does not exist** (it
      falls in a spring-forward gap) -- :class:`LocalTimeError`;
    - otherwise, if the two folds land on **different** UTC instants, the
      local time is genuinely **ambiguous** (a fall-back repeat) --
      :class:`LocalTimeError`.

    The order matters: both cases produce two different instants, and
    only the round trip separates a gap from a repeat. Neither is
    resolved by silently picking a fold.

    `local_moment` must be naive. An aware value is rejected with
    ``ValueError`` rather than being "helpfully" converted: it carries a
    timezone of its own, so treating it as a wall clock in `tz_name`
    would silently discard that information, and every caller in this
    project supplies a naive value parsed from a form.
    """
    if local_moment.tzinfo is not None:
        raise ValueError(
            "from_app_local expects a naive local wall-clock datetime; "
            "an aware value already knows its own offset."
        )
    if not tz_name or not tz_name.strip():
        raise TimezoneConfigError(
            "APP_TIMEZONE is empty -- set a real timezone (e.g. 'Africa/Tripoli', 'UTC', "
            "or an explicit offset like '+02:00')."
        )

    fixed = _parse_fixed_offset_minutes(tz_name)
    if fixed is not None:
        return local_moment - timedelta(minutes=fixed)

    tz = _resolve_zoneinfo(tz_name)
    if tz is not None:
        first_utc = local_moment.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
        second_utc = local_moment.replace(tzinfo=tz, fold=1).astimezone(timezone.utc)
        if first_utc.astimezone(tz).replace(tzinfo=None) != local_moment:
            raise LocalTimeError(
                "That date and time does not exist in the center timezone -- the clocks move "
                "forward across it. Please choose a different time."
            )
        if first_utc != second_utc:
            raise LocalTimeError(
                "That date and time happens twice in the center timezone -- the clocks move "
                "back across it. Please choose a different time."
            )
        return first_utc.replace(tzinfo=None)

    fallback = _DEPLOYMENT_FIXED_OFFSET_MINUTES.get(tz_name.strip().lower())
    if fallback is not None:
        return local_moment - timedelta(minutes=fallback)

    raise TimezoneConfigError(
        f"APP_TIMEZONE={tz_name!r} could not be resolved: it is not a fixed offset, this "
        "host has no IANA time-zone database entry for it, and it is not a known deployment "
        "fallback. Install `tzdata`, or set APP_TIMEZONE to 'UTC' or an explicit offset."
    )


def utc_reference_now(utc_now=None):
    """The single naive-UTC reference moment for one request or test.

    Phase 4 / M01 compares ``opens_at`` / ``due_at`` -- stored naive UTC
    -- in several places within one response (the visibility gate, the
    ordering, the derived Scheduled / Open / Past-due label). Every one
    of them must use the *same* instant, so a request that straddles a
    deadline can never render a self-contradictory page. Routes call this
    exactly once and pass the result down; tests inject `utc_now`
    (aware or naive-UTC) to make the whole page deterministic.
    """
    moment = utc_now or datetime.now(timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Occurrence arithmetic -- pure functions over naive local datetimes
# ---------------------------------------------------------------------------

SlotSpec = namedtuple(
    "SlotSpec",
    "day_of_week start_time end_time effective_start_date effective_end_date ref",
)

Occurrence = namedtuple("Occurrence", "start end ref")

DEFAULT_HORIZON = timedelta(days=7)
DEFAULT_CAP = 20


def slot_spec(schedule, ref=None):
    """Build a :class:`SlotSpec` from any object exposing the M08
    Schedule recurrence attributes. `ref` defaults to `schedule` itself
    so callers can carry display context (group, course, location) on the
    returned occurrences without this module knowing about it.
    """
    return SlotSpec(
        day_of_week=schedule.day_of_week,
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        effective_start_date=schedule.effective_start_date,
        effective_end_date=schedule.effective_end_date,
        ref=schedule if ref is None else ref,
    )


def _first_weekday_on_or_after(day_of_week, from_date):
    # Mirrors app.services.schedule_queries.first_weekday_on_or_after; kept
    # local so this module stays a pure-stdlib island.
    return from_date + timedelta(days=(day_of_week - from_date.weekday()) % 7)


def _occurrence_on(a_date, start_time, end_time, ref):
    return Occurrence(
        start=datetime.combine(a_date, start_time),
        end=datetime.combine(a_date, end_time),
        ref=ref,
    )


def current_or_next_occurrence(spec, now):
    """The single earliest occurrence of `spec` that has not yet ended at
    local wall-clock `now` -- i.e. the one with the smallest ``start``
    whose ``end > now``. It is "current" when ``start <= now < end`` and
    "next" when ``start > now``; the caller decides how to label it.

    Returns ``None`` when the effective date range holds no such
    occurrence (it has ended, or never contained the weekday).
    """
    d = _first_weekday_on_or_after(spec.day_of_week, spec.effective_start_date)
    if d > spec.effective_end_date:
        return None
    if d < now.date():
        # jump forward whole weeks to the first occurrence date >= today
        weeks = -(-(now.date() - d).days // 7)  # ceil division
        d += timedelta(weeks=weeks)
    if d > spec.effective_end_date:
        return None
    occ = _occurrence_on(d, spec.start_time, spec.end_time, spec.ref)
    if occ.end <= now:
        # today's occurrence already finished -- roll to next week
        d += timedelta(weeks=1)
        if d > spec.effective_end_date:
            return None
        occ = _occurrence_on(d, spec.start_time, spec.end_time, spec.ref)
    return occ


def iter_occurrences_between(spec, from_date, to_date):
    """Yield every occurrence of `spec` whose date is within the inclusive
    ``[from_date, to_date]`` window intersected with the slot's own
    effective range, ascending. Bounded: at most
    ``(window length / 7) + 1`` iterations.
    """
    lo = max(spec.effective_start_date, from_date)
    hi = min(spec.effective_end_date, to_date)
    d = _first_weekday_on_or_after(spec.day_of_week, lo)
    while d <= hi:
        yield _occurrence_on(d, spec.start_time, spec.end_time, spec.ref)
        d += timedelta(days=7)


def upcoming_occurrences(specs, now, horizon=DEFAULT_HORIZON, cap=DEFAULT_CAP):
    """A sorted, capped list of occurrences across every spec in `specs`
    whose ``end > now`` and whose ``start <= now + horizon``. Ascending by
    ``(start, end)``; ties broken by insertion order of `specs`.
    """
    window_end = now + horizon
    out = []
    for spec in specs:
        for occ in iter_occurrences_between(spec, now.date(), window_end.date()):
            if occ.end > now and occ.start <= window_end:
                out.append(occ)
    out.sort(key=lambda o: (o.start, o.end))
    return out[:cap]


def earliest_occurrence(specs, now):
    """The single earliest current/next occurrence across every spec in
    `specs` (no horizon), or ``None`` if none has a remaining occurrence.
    """
    best = None
    for spec in specs:
        occ = current_or_next_occurrence(spec, now)
        if occ is not None and (best is None or occ.start < best.start):
            best = occ
    return best
