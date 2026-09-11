"""Administrator **review** of recorded attendance (Phase 4 / M07).

Three routes, and every one of them is a ``GET``::

    GET  /admin/attendance
    GET  /admin/groups/<group_public_id>/attendance
    GET  /admin/groups/<group_public_id>/attendance/<session_public_id>

**There is no Administrator mutation route in M07, at all.** No create,
edit, mark, finalize, reopen, unlock, delete, archive, restore,
duplicate, export, bulk action or grade action exists here -- not hidden
behind a permission check, not disabled in a template: the endpoints do
not exist, so a POST to any attendance URL under ``/admin`` returns 405
or 404 rather than being refused by a check somebody could later relax.
Attendance is recorded by the Teacher who taught the class; an
Administrator reviews what was recorded.

**What an Administrator may read.** Everything, including historical
sessions under archived Schedules, Groups and academic ancestors,
including **draft** sessions, and including the Teacher-only private
``note`` on each record. That is the point of a review surface: an
administrator investigating a dispute must be able to see exactly what
the Teacher wrote. The Student that a note is about still never sees it.

**Filters are validated into known shapes before they reach SQL.** The
Group filter is a *public* id resolved to an internal one by a lookup
(an unknown value simply matches nothing); the date filter must parse as
a real ISO date; the state filter must be exactly ``final`` or ``draft``.
Anything else is dropped rather than guessed at, and no raw query-string
value is ever interpolated into a query.

Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: an attendance page names Students, their marks and
private notes, and a shared or reused cache entry must never be able to
hand it to somebody else.
"""

from datetime import date

from flask import abort, current_app, make_response, render_template, request

from app.blueprints.admin import admin_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.attendance_queries import (
    PAGE_SIZE,
    admin_sessions_page,
    build_admin_session_list_view,
    build_record_view,
    group_by_public_id,
    normalize_page,
    ordered_counts,
    session_for_group,
    session_records,
    session_schedule_weekday,
    status_counts_for_sessions,
)

#: The two accepted values of the ``state`` filter, mapped to what the
#: query layer takes. Anything else -- including an empty string and a
#: cleverly cased variant -- is dropped, which is what "filters limited to
#: known, validated values" means.
_STATE_FILTER = {"final": True, "draft": False}


def _private_no_store(template, **context):
    """Render a **personalized** Administrator page with the two headers
    every content-bearing attendance response must carry.

    Same contract as the Teacher and Student surfaces
    (``teacher/assignments._private_no_store``,
    ``student/routes.private_no_store``), applied here rather than
    imported across blueprints, matching how every other module in this
    project already sets it.
    """
    response = make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _date_arg(name):
    """Parse an ISO ``YYYY-MM-DD`` filter, or ``None``.

    A missing, empty, malformed or impossible value (``2026-02-30``) all
    become ``None`` -- the filter is simply not applied -- rather than
    reaching the database or raising.
    """
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _state_arg():
    """``True`` for finalized-only, ``False`` for drafts-only, ``None``
    for either."""
    return _STATE_FILTER.get((request.args.get("state") or "").strip())


def _group_filter():
    """``(raw_public_id, resolved_group_or_None)`` for the Group filter.

    The raw value is kept only so the form can render what was typed; it
    is never used in a query. An unknown public id resolves to ``None``
    and the page says so, rather than silently listing the whole center.
    """
    raw = (request.args.get("group") or "").strip()
    if not raw:
        return "", None
    return raw, group_by_public_id(raw)


@admin_bp.get("/attendance")
@roles_required(UserRole.ADMINISTRATOR.value)
def attendance_overview():
    """One bounded page of attendance sessions across the whole center,
    newest class date first.

    Fixed page size, deterministic SQL ordering
    (``session_date DESC, id DESC``), ``LIMIT PAGE_SIZE + 1`` for the
    has-next flag and **no** ``COUNT``. One extra bounded aggregate
    resolves the status counts for the at-most-``PAGE_SIZE`` sessions on
    the page, so the cost does not grow with the size of a roster or with
    how much history exists.
    """
    page = normalize_page(request.args.get("page"))
    raw_group, group = _group_filter()
    session_date = _date_arg("session_date")
    finalized = _state_arg()

    if raw_group and group is None:
        # A filter that names nothing must narrow to nothing, never widen.
        rows, has_next = [], False
    else:
        rows, has_next = admin_sessions_page(
            page,
            group_id=None if group is None else group.id,
            session_date=session_date,
            finalized=finalized,
        )
        if not rows and page > 1:
            page = 1
            rows, has_next = admin_sessions_page(
                page,
                group_id=None if group is None else group.id,
                session_date=session_date,
                finalized=finalized,
            )

    counts = status_counts_for_sessions([row.id for row in rows])
    return _private_no_store(
        "admin/attendance/overview.html",
        sessions=build_admin_session_list_view(rows, counts),
        filter_group=raw_group,
        filter_group_row=group,
        filter_group_unknown=bool(raw_group) and group is None,
        filter_date=session_date,
        filter_state=(request.args.get("state") or "").strip()
        if (request.args.get("state") or "").strip() in _STATE_FILTER
        else "",
        tz_name=_tz_name(),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


def _group_or_404(group_public_id):
    group = group_by_public_id(group_public_id)
    if group is None:
        abort(404)
    return group


@admin_bp.get("/groups/<group_public_id>/attendance")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_attendance(group_public_id):
    """One Group's attendance sessions, newest class date first.

    The same bounded, deterministic page as the center-wide list, scoped
    in SQL to this Group.
    """
    group = _group_or_404(group_public_id)
    page = normalize_page(request.args.get("page"))
    rows, has_next = admin_sessions_page(page, group_id=group.id)
    if not rows and page > 1:
        page = 1
        rows, has_next = admin_sessions_page(page, group_id=group.id)

    counts = status_counts_for_sessions([row.id for row in rows])
    return _private_no_store(
        "admin/attendance/group.html",
        group=group,
        sessions=build_admin_session_list_view(rows, counts),
        tz_name=_tz_name(),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@admin_bp.get("/groups/<group_public_id>/attendance/<session_public_id>")
@roles_required(UserRole.ADMINISTRATOR.value)
def attendance_session_detail(group_public_id, session_public_id):
    """One attendance session in full -- every captured Student, their
    mark and the Teacher's private note.

    Nested and SQL-scoped: the session is looked up **by its own public
    id constrained to this Group**, so a session public id valid only for
    another Group 404s here exactly as it does on every other nested
    route in this project. Read-only: the page renders no form, no token
    and no control of any kind, because no endpoint exists to aim one at.
    """
    group = _group_or_404(group_public_id)
    session = session_for_group(group.id, session_public_id)
    if session is None:
        abort(404)
    counts = status_counts_for_sessions([session.id])
    return _private_no_store(
        "admin/attendance/detail.html",
        group=group,
        session=session,
        weekday=session_schedule_weekday(session.id),
        records=build_record_view(session_records(session.id)),
        counts=ordered_counts(counts.get(session.id)),
        is_finalized=session.is_finalized(),
        tz_name=_tz_name(),
    )
