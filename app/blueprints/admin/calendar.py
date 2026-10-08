"""Administrator center calendar and center-event management
(Phase 4 / M10).

Five routes, every object addressed by ``public_id``::

    GET       /admin/calendar
    GET|POST  /admin/calendar/events/new
    GET       /admin/calendar/events/<ep>
    GET|POST  /admin/calendar/events/<ep>/edit
    POST      /admin/calendar/events/<ep>/cancel

**This is the only surface in the project that can write a
CalendarEvent.** There is no Student and no Teacher endpoint for one to
be aimed at -- not disabled in a template, not refused by a permission
check somebody could later relax: no such route is registered, so a POST
from either role returns 404 or 405.

**What an Administrator may do, and what nobody may do.** They read the
whole center's calendar, create center events, edit scheduled ones and
cancel them. There is deliberately **no** delete, restore, un-cancel,
re-schedule, duplicate, publish, export, iCalendar feed, reminder,
recurrence or bulk action -- none exists server-side either, and no
placeholder is left for one. A cancelled event is permanently immutable;
correcting one means creating a new event, which is what keeps the
record of what was announced and then called off intact.

**The calendar is a read model.** ``GET /admin/calendar`` shows one
bounded date range assembled by
``app/services/calendar_queries.build_calendar`` from the *existing*
sources -- ``active`` Schedules expanded by the canonical occurrence
arithmetic, published Assignment opening/due moments, published Quiz and
Listening opening/deadline moments -- plus the center's own events.
Rendering it writes nothing and can alter no source record.

**What this surface deliberately does not show.** No individual
Student's enrollment, no quiz attempt, no submission, no grade record,
no attendance record, no notification recipient and no comment: none of
them is a calendar entry, and no query behind this module fetches one.
An academic entry here carries only its title, its Group's name and its
Course's title -- the same context the Administrator's own Groups and
Schedules pages already show.

**Range navigation is normalised, bounded and public.** ``from`` / ``to``
are validated into at most
``calendar_queries.MAX_RANGE_DAYS`` local civil days inside the
navigable window; a malformed, reversed, oversized or absurd pair shows
the default current month or the nearest legal range rather than
reaching SQL. Every Previous / Next / Today link this page emits is
built from the **normalised** range, so the application never produces a
URL it would have to normalise again. There is no ``COUNT`` anywhere:
navigation is arithmetic on dates.

**Every mutation requires CSRF and a purpose-specific signed token**, and
every one of them runs the single deterministic lock chain in
``app/services/calendar_transactions.py`` -- the acting Administrator,
then the event. The create, edit and cancel tokens are minted under
three different salts, so none of them can be replayed as another.

Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: a calendar page names the center's Groups, Courses and
deadlines, and a shared or reused cache entry must never be able to hand
it to somebody else.
"""

from datetime import datetime, timezone

from flask import (
    abort,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.calendar_forms import CalendarEventForm
from app.extensions import db
from app.models import (
    CALENDAR_EVENT_DETAILS_MAX_LENGTH,
    CALENDAR_EVENT_LOCATION_MAX_LENGTH,
    CALENDAR_EVENT_TITLE_MAX_LENGTH,
    CalendarEvent,
    CalendarEventStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.calendar_queries import (
    MAX_CALENDAR_ROWS,
    MAX_RANGE_DAYS,
    SOURCE_CENTER_EVENT,
    SOURCE_CLASS,
    admin_event,
    build_calendar,
    build_event_view,
    group_by_day,
    next_range,
    normalize_range,
    previous_range,
    range_args,
    span_days,
)
from app.services.calendar_tokens import make_token, token_is_stale
from app.services.calendar_transactions import (
    administrator_authz_broken,
    lock_calendar_event_chain,
)
from app.services.schedule_occurrences import app_now, utc_reference_now

_SCHEDULED = CalendarEventStatus.SCHEDULED.value
_CANCELLED = CalendarEventStatus.CANCELLED.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: This surface's token namespace. The only surface that mints an M10
#: token -- see ``app/services/calendar_tokens.py``.
_SURFACE = "admin"


def _private_no_store(template, **context):
    """Render a **personalized** Administrator page with the two headers
    every calendar response must carry.

    Same contract as the Teacher and Student surfaces, applied here
    rather than imported across blueprints, matching how every other
    module in this project already sets it.
    """
    response = make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _write_moment():
    """The **authoritative** naive-UTC moment for one calendar-event
    write, truncated to whole seconds.

    ``DATETIME`` on MySQL carries fractional precision 0 and *rounds* an
    excess fraction rather than truncating it, so a value carrying
    microseconds would be stored as a different instant from the one the
    request used. Read only **after** every lock that could have blocked,
    so a request that waited behind a competing Administrator records the
    moment it actually wrote.
    """
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# Administrator-facing sentences, declared once each
# ======================================================================

_STALE_MESSAGE = (
    "This event was changed by someone else since this page was opened. Your changes "
    "were not saved. Please reload, read the current event, and make your change "
    "against it."
)
_INTEGRITY_MESSAGE = (
    "That change could not be saved because the data changed at the same moment. "
    "Nothing was written. Please reload and try again."
)
_CANCELLED_MESSAGE = (
    "This event was cancelled. A cancelled event is permanent — it can never be edited, "
    "re-scheduled, or restored. Create a new event instead."
)
_CANCEL_CONFIRM_MESSAGE = (
    "Please tick the confirmation box before cancelling. Cancelling cannot be undone."
)
_NO_CHANGES_MESSAGE = "Nothing was changed, so nothing was saved."
_SAVED_MESSAGE = "Calendar event saved."
_CREATED_MESSAGE = (
    "Calendar event created. Everybody at the center can see it on their calendar now."
)
_CANCELLED_OK_MESSAGE = (
    "Event cancelled. It has disappeared from every student's and teacher's calendar. "
    "It is kept as a permanent record of what was planned and that it was called off."
)
_RANGE_NORMALIZED_MESSAGE = (
    f"That date range could not be used as asked for, so the calendar is showing the "
    f"nearest range it can. A calendar view covers at most {MAX_RANGE_DAYS} days."
)


# ======================================================================
# URLs
# ======================================================================


def _calendar_url(a_range=None):
    if a_range is None:
        return url_for("admin.calendar")
    return url_for("admin.calendar", **range_args(a_range))


def _new_url():
    return url_for("admin.calendar_event_create")


def _detail_url(public_id):
    return url_for("admin.calendar_event_detail", event_public_id=public_id)


def _edit_url(public_id):
    return url_for("admin.calendar_event_edit", event_public_id=public_id)


def _attach_urls(rows):
    """Fill in each calendar entry's **server-generated** destination.

    Done here rather than in the query layer, and per role rather than
    once: the query layer is Flask-independent, and where an entry leads
    is a property of *who is looking*. Every URL is built by ``url_for``
    from public identifiers the query already returned, so no path is
    ever assembled from user input, and every destination re-authorizes
    from scratch when it is followed.

    Only two of the eight sources have an Administrator destination:

    - a **class** entry leads to that Group's schedules page, which is
      the Administrator surface that owns a recurring class;
    - a **center event** leads to its own management page.

    Assignment, Quiz and Listening entries deliberately carry **no**
    link. There is no Administrator surface for an Assignment, a Quiz or
    a Listening activity anywhere in this project -- those objects are
    authored and read by the Group's Teachers -- and inventing a
    destination that does not exist, or pointing at a Teacher route an
    Administrator cannot open, would be worse than a plain entry. The
    entry still names what it is, which Group it belongs to and when it
    happens.
    """
    for row in rows:
        if row["source"] == SOURCE_CLASS and row["group_public_id"]:
            row["url"] = url_for(
                "admin.group_schedules", group_public_id=row["group_public_id"]
            )
        elif row["source"] == SOURCE_CENTER_EVENT and row["source_public_id"]:
            row["url"] = _detail_url(row["source_public_id"])


# ======================================================================
# Shared rejection handling
# ======================================================================


def _fresh_admin_authorization(actor_id):
    """Prove from **current database state** that `actor_id` is still an
    active Administrator, for a path that has rolled back and released
    its locks. The actor is identified by a **scalar id captured before
    the reset**, never by ``current_user``."""
    if actor_id is None:  # pragma: no cover -- an authenticated view always has one
        abort(404)
    actor = db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    if actor is None or actor.role != _ADMINISTRATOR or actor.status != _USER_ACTIVE:
        abort(404)


def _reject(message, url, level="danger"):
    """Release any lock, re-prove authorization from current state, then
    flash and redirect to a plain GET (PRG)."""
    actor_id = current_user.id
    db.session.rollback()
    _fresh_admin_authorization(actor_id)
    flash(message, level)
    return redirect(url)


def _event_or_404(public_id):
    row = admin_event(public_id)
    if row is None:
        abort(404)
    return row


# ======================================================================
# The calendar itself
# ======================================================================


@admin_bp.get("/calendar")
@roles_required(UserRole.ADMINISTRATOR.value)
def calendar():
    """One bounded date range of the whole center's calendar.

    Five bounded queries whatever the range holds: the operational
    Schedules, the published Assignments, the published ordinary Quizzes,
    the published Listening activities and the center's own events.
    Nothing is asked per entry or per day, and nothing is counted.

    This is the one calendar surface that shows **cancelled** center
    events, clearly marked. They are kept here and nowhere else: a
    Student's or a Teacher's calendar must show only what is actually
    happening, while an Administrator must be able to see that something
    was planned and called off.
    """
    tz_name = _tz_name()
    # ONE reference moment for the whole response: the local wall clock
    # the range and the class occurrences use, and the naive-UTC instant
    # the Assignment / Quiz moments are compared against, are both
    # derived from it. Reading the clock twice could straddle a deadline
    # and render a page that contradicts itself.
    utc_now = datetime.now(timezone.utc)
    today = app_now(tz_name, utc_now).date()
    reference_utc = utc_reference_now(utc_now)

    normalized = normalize_range(
        request.args.get("from"), request.args.get("to"), today
    )
    a_range = normalized.range
    rows, truncated = build_calendar(
        a_range, tz_name, reference_utc, include_cancelled_events=True
    )
    _attach_urls(rows)

    return _private_no_store(
        "admin/calendar/index.html",
        days=group_by_day(rows),
        entry_count=len(rows),
        truncated=truncated,
        max_rows=MAX_CALENDAR_ROWS,
        range_start=a_range.start,
        range_end=a_range.end,
        range_days=span_days(a_range),
        range_normalized=normalized.normalized,
        range_message=_RANGE_NORMALIZED_MESSAGE,
        max_range_days=MAX_RANGE_DAYS,
        previous_url=_calendar_url(previous_range(a_range, today)),
        next_url=_calendar_url(next_range(a_range, today)),
        today_url=_calendar_url(),
        new_event_url=_new_url(),
        tz_name=tz_name,
    )


# ======================================================================
# Center-event creation and editing
# ======================================================================


def _render_form(form, event=None, message=None, level="danger"):
    """Render the create / edit page against **current persisted state**,
    with a freshly minted token.

    The create token binds only the acting account -- there is no row yet
    -- while the edit token binds the **stored** event's public id,
    version and lifecycle, never whatever the form currently shows. So an
    edit attempted against a row somebody else has already changed or
    cancelled is caught rather than applied.
    """
    if message is not None:
        flash(message, level)
    if event is None:
        token = make_token(
            _SURFACE,
            "calendar-event-create",
            actor_public_id=current_user.public_id,
        )
    else:
        token = make_token(
            _SURFACE,
            "calendar-event-edit",
            actor_public_id=current_user.public_id,
            event_public_id=event.public_id,
            event_version=event.version,
            event_status=event.status,
        )
    return _private_no_store(
        "admin/calendar/form.html",
        form=form,
        event=None if event is None else build_event_view(event, _tz_name()),
        title_max=CALENDAR_EVENT_TITLE_MAX_LENGTH,
        details_max=CALENDAR_EVENT_DETAILS_MAX_LENGTH,
        location_max=CALENDAR_EVENT_LOCATION_MAX_LENGTH,
        cancel_url=_calendar_url() if event is None else _detail_url(event.public_id),
        event_state=token,
        tz_name=_tz_name(),
    )


@admin_bp.route("/calendar/events/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def calendar_event_create():
    """Create one center calendar event.

    A new event is ``scheduled`` immediately: there is no draft state to
    publish out of, and no "create and notify" path -- M10 produces no
    notification of any kind.
    """
    form = CalendarEventForm(formdata=request.form if request.method == "POST" else None)
    if request.method == "GET" or not form.validate_on_submit():
        return _render_form(form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("event_state")

    locks = lock_calendar_event_chain(actor_id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)

    if token_is_stale(
        _SURFACE,
        token,
        "calendar-event-create",
        actor_public_id=actor_public_id,
    ):
        return _reject(_STALE_MESSAGE, _new_url())

    moment = _write_moment()
    event = CalendarEvent(
        created_by_id=actor_id,
        title=form.normalized_title,
        details=form.normalized_details,
        event_date=form.event_date.data,
        start_time=form.start_time.data,
        end_time=form.end_time.data,
        location=form.normalized_location,
        status=_SCHEDULED,
        cancelled_at=None,
        version=1,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(event)
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, _new_url())

    created_public_id = event.public_id
    flash(_CREATED_MESSAGE, "success")
    return redirect(_detail_url(created_public_id))


@admin_bp.get("/calendar/events/<event_public_id>")
@roles_required(UserRole.ADMINISTRATOR.value)
def calendar_event_detail(event_public_id):
    """One center event in full, with whichever control its current state
    allows.

    A ``scheduled`` event offers editing and cancellation; a
    ``cancelled`` one offers neither, and no token is minted for it --
    the page is a record. Management data (the creator's name, the
    version, the lifecycle timestamps) appears here and only here; no
    reader surface fetches any of it.
    """
    row = _event_or_404(event_public_id)
    cancel_token = None
    if row.status == _SCHEDULED:
        cancel_token = make_token(
            _SURFACE,
            "calendar-event-cancel",
            actor_public_id=current_user.public_id,
            event_public_id=row.public_id,
            event_version=row.version,
            event_status=row.status,
        )
    return _private_no_store(
        "admin/calendar/detail.html",
        event=build_event_view(row, _tz_name()),
        cancel_token=cancel_token,
        edit_url=_edit_url(row.public_id),
        calendar_url=_calendar_url(),
        tz_name=_tz_name(),
    )


@admin_bp.route("/calendar/events/<event_public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def calendar_event_edit(event_public_id):
    """Change one **scheduled** center event's title, details, date,
    times or location.

    A cancelled event is permanently immutable, and that is re-proved
    against the **locked** row rather than only against the pre-lock
    read.

    A save whose normalized title, details, date, both times and
    location all equal the stored ones is a **no-op**: no version moves,
    no timestamp moves, and the transaction is rolled back.
    """
    row = _event_or_404(event_public_id)
    detail_url = _detail_url(event_public_id)

    if row.status == _CANCELLED:
        flash(_CANCELLED_MESSAGE, "warning")
        return redirect(detail_url)

    form = CalendarEventForm(
        formdata=request.form if request.method == "POST" else None,
        data={
            "title": row.title,
            "details": row.details or "",
            "event_date": row.event_date,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "location": row.location or "",
        },
    )
    if request.method == "GET" or not form.validate_on_submit():
        return _render_form(form, event=row)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("event_state")
    event_id = row.id
    edit_url = _edit_url(event_public_id)

    title = form.normalized_title
    details = form.normalized_details
    location = form.normalized_location
    event_date = form.event_date.data
    start_time = form.start_time.data
    end_time = form.end_time.data

    locks = lock_calendar_event_chain(actor_id, event_id=event_id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)
    locked = locks.event
    if locked is None or locked.public_id != event_public_id:
        db.session.rollback()
        abort(404)

    if token_is_stale(
        _SURFACE,
        token,
        "calendar-event-edit",
        actor_public_id=actor_public_id,
        event_public_id=event_public_id,
        event_version=locked.version,
        event_status=locked.status,
    ):
        return _reject(_STALE_MESSAGE, edit_url)

    if locked.status != _SCHEDULED:
        return _reject(_CANCELLED_MESSAGE, detail_url, "warning")

    if (
        locked.title == title
        and locked.details == details
        and locked.event_date == event_date
        and locked.start_time == start_time
        and locked.end_time == end_time
        and locked.location == location
    ):
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(detail_url)

    moment = _write_moment()
    locked.title = title
    locked.details = details
    locked.event_date = event_date
    locked.start_time = start_time
    locked.end_time = end_time
    locked.location = location
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, edit_url)

    flash(_SAVED_MESSAGE, "success")
    return redirect(detail_url)


# ======================================================================
# Cancellation
# ======================================================================


@admin_bp.post("/calendar/events/<event_public_id>/cancel")
@roles_required(UserRole.ADMINISTRATOR.value)
def calendar_event_cancel(event_public_id):
    """Cancel one scheduled center event, permanently.

    The event stops appearing on every Student and Teacher calendar the
    instant this commits, because their reads filter
    ``status = 'scheduled'`` in SQL. It remains visible, clearly marked,
    on the Administrator calendar and on its own page. There is no
    un-cancel, no restore and no re-schedule, and no endpoint exists for
    one.

    Deliberately **not** gated on the event's date still being ahead: a
    calendar is also a record, and something that should no longer be
    expected must always be markable as called off -- including after its
    date has passed.
    """
    row = _event_or_404(event_public_id)
    detail_url = _detail_url(event_public_id)

    if row.status != _SCHEDULED:
        flash(_CANCELLED_MESSAGE, "warning")
        return redirect(detail_url)
    if request.form.get("confirm") != "yes":
        flash(_CANCEL_CONFIRM_MESSAGE, "warning")
        return redirect(detail_url)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("event_state")

    locks = lock_calendar_event_chain(actor_id, event_id=row.id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)
    locked = locks.event
    if locked is None or locked.public_id != event_public_id:
        db.session.rollback()
        abort(404)

    if token_is_stale(
        _SURFACE,
        token,
        "calendar-event-cancel",
        actor_public_id=actor_public_id,
        event_public_id=event_public_id,
        event_version=locked.version,
        event_status=locked.status,
    ):
        return _reject(_STALE_MESSAGE, detail_url)

    if locked.status != _SCHEDULED:
        return _reject(_CANCELLED_MESSAGE, detail_url, "warning")

    moment = _write_moment()
    locked.status = _CANCELLED
    locked.cancelled_at = moment
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, detail_url)

    flash(_CANCELLED_OK_MESSAGE, "success")
    return redirect(detail_url)
