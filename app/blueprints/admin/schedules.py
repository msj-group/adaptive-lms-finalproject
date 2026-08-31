"""Administrator management of recurring weekly Group schedules (M08).

A Schedule is a child of Group. Every mutation here follows the approved
global lock order

    AcademicTerm -> Level -> Course -> Group -> Schedule

with one deliberate transaction reset (owned by ``lock_academic_hierarchy``)
before the first lock and no reset while locks are held. Non-locking
preview reads only discover which rows to lock and give friendly early
errors; existence, ancestor/Group/Schedule status, the signed edit
snapshot, Academic Term containment, the weekday-occurrence rule, and the
active-overlap rule are all re-checked against the locked, current rows
before anything is written.

The center-wide overview (``GET /admin/schedules``) is read-only
aggregation; all mutations are Group-centered nested routes.
"""

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import WEEKDAY_CHOICES, WEEKDAY_NAMES, ScheduleForm
from app.blueprints.admin.utils import normalize_optional_text
from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, Course, Group, Level, Schedule, UserRole
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.schedule_queries import (
    conflicting_active_schedule,
    range_contains_weekday,
    term_contains_range,
)
from app.services.schedule_transactions import lock_schedule_in_open_transaction_by_id

_MAX_BIGINT = 9223372036854775807


def _safe_id_arg(name):
    """Parse a positive integer query-string filter, discarding values
    that are not a valid id (missing, non-numeric, zero/negative, or too
    large for the BIGINT columns) rather than letting them reach the
    database.
    """
    value = request.args.get(name, type=int)
    if value is None or value < 1 or value > _MAX_BIGINT:
        return None
    return value


def _app_timezone():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _weekday_label(day_of_week):
    if day_of_week is None or day_of_week < 0 or day_of_week > 6:
        return "?"
    return WEEKDAY_NAMES[day_of_week]


def _load_group_or_404(group_public_id):
    """Ordinary, non-locking Group lookup with the ancestor relationships
    the schedule pages and guards need eagerly loaded. Used for the GET
    pages, for early friendly validation, and to refetch a display copy
    after a post-lock rejection has released the write lock. Never call
    this while a write lock is held.
    """
    return (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=group_public_id)
        .first_or_404()
    )


def _get_schedule_for_group_or_404(group, schedule_public_id):
    """Look up a Schedule by its own public_id, constrained to the Group
    resolved from the URL -- a schedule public_id that is only valid for a
    *different* Group 404s here (nested-IDOR protection), the same pattern
    used for Enrollment/GroupTeacherAssignment nested lookups.
    """
    return Schedule.query.filter_by(
        public_id=schedule_public_id, group_id=group.id
    ).first_or_404()


def _lock_group_in_open_transaction_or_404(group_public_id):
    """Thin route-local 404 wrapper around ``lock_group_in_open_transaction``
    -- the Group lock that follows the ``lock_academic_hierarchy`` ancestor
    locks, deliberately without a second reset (which would release the
    ancestor locks just acquired).
    """
    group = lock_group_in_open_transaction(group_public_id)
    if group is None:
        abort(404)
    return group


def _ancestors_active(term, level, course, group):
    """Every row in the locked chain exists and is active."""
    return all(
        row is not None and row.status == AcademicStatus.ACTIVE.value
        for row in (term, level, course, group)
    )


def _archived_chain_labels(term, level, course, group):
    return [
        label
        for label, row in (
            ("academic term", term),
            ("level", level),
            ("course", course),
            ("group", group),
        )
        if row is None or row.status != AcademicStatus.ACTIVE.value
    ]


def _redirect_to_group_schedules(group_public_id):
    # Fixed destination only -- no caller-supplied return URL is ever
    # accepted.
    return redirect(url_for("admin.group_schedules", group_public_id=group_public_id))


# ----------------------------------------------------------------------
# Center-wide overview -- read-only aggregation
# ----------------------------------------------------------------------


@admin_bp.get("/schedules")
@roles_required(UserRole.ADMINISTRATOR.value)
def schedules_overview():
    search = request.args.get("q", "").strip()
    term_id = _safe_id_arg("term_id")
    course_id = _safe_id_arg("course_id")
    level_id = _safe_id_arg("level_id")
    group_id = _safe_id_arg("group_id")
    day_arg = request.args.get("day_of_week", "").strip()
    day_of_week = int(day_arg) if day_arg.isdigit() and 0 <= int(day_arg) <= 6 else None
    status = request.args.get("status", "").strip()

    query = Schedule.query.options(
        joinedload(Schedule.group).joinedload(Group.course).joinedload(Course.level),
        joinedload(Schedule.group).joinedload(Group.academic_term),
    ).join(Group, Schedule.group_id == Group.id)

    if search:
        query = query.filter(Schedule.location.ilike(f"%{search}%"))
    if term_id:
        query = query.filter(Group.academic_term_id == term_id)
    if course_id:
        query = query.filter(Group.course_id == course_id)
    if level_id:
        query = query.join(Course, Group.course_id == Course.id).filter(Course.level_id == level_id)
    if group_id:
        query = query.filter(Schedule.group_id == group_id)
    if day_of_week is not None:
        query = query.filter(Schedule.day_of_week == day_of_week)
    if status in {s.value for s in AcademicStatus}:
        query = query.filter(Schedule.status == status)

    schedules = query.order_by(
        Group.name, Schedule.day_of_week, Schedule.start_time, Schedule.id
    ).all()

    return render_template(
        "admin/schedules/overview.html",
        schedules=schedules,
        search=search,
        app_timezone=_app_timezone(),
        weekday_names=WEEKDAY_NAMES,
        weekday_choices=WEEKDAY_CHOICES,
        terms=AcademicTerm.query.order_by(AcademicTerm.start_date.desc()).all(),
        courses=Course.query.options(joinedload(Course.level))
        .join(Level)
        .order_by(Level.display_order, Course.display_order)
        .all(),
        levels=Level.query.order_by(Level.display_order, Level.id).all(),
        groups=Group.query.order_by(Group.name, Group.id).all(),
        selected_term_id=term_id,
        selected_course_id=course_id,
        selected_level_id=level_id,
        selected_group_id=group_id,
        selected_day_of_week=day_of_week,
        selected_status=status,
        has_filters=bool(
            search
            or term_id
            or course_id
            or level_id
            or group_id
            or day_of_week is not None
            or status
        ),
    )


# ----------------------------------------------------------------------
# Group-centered management page
# ----------------------------------------------------------------------


def _sorted_group_schedules(group_id):
    rows = Schedule.query.filter_by(group_id=group_id).all()
    rows.sort(
        key=lambda s: (
            s.status != AcademicStatus.ACTIVE.value,
            s.day_of_week,
            s.start_time,
            s.id,
        )
    )
    return rows


@admin_bp.get("/groups/<group_public_id>/schedules")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_schedules(group_public_id):
    group = _load_group_or_404(group_public_id)
    ancestor_archived = (
        group.academic_term.status != AcademicStatus.ACTIVE.value
        or group.course.status != AcademicStatus.ACTIVE.value
        or group.course.level.status != AcademicStatus.ACTIVE.value
    )
    return render_template(
        "admin/schedules/group.html",
        group=group,
        schedules=_sorted_group_schedules(group.id),
        weekday_names=WEEKDAY_NAMES,
        app_timezone=_app_timezone(),
        group_is_active=group.status == AcademicStatus.ACTIVE.value,
        ancestor_archived=ancestor_archived,
        can_create=group.status == AcademicStatus.ACTIVE.value and not ancestor_archived,
    )


# ----------------------------------------------------------------------
# Nested create
# ----------------------------------------------------------------------


@admin_bp.route("/groups/<group_public_id>/schedules/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def group_schedule_create(group_public_id):
    preview_group = _load_group_or_404(group_public_id)

    early_block = _create_edit_lifecycle_block(preview_group)
    if early_block is not None:
        flash(early_block, "danger")
        return _redirect_to_group_schedules(group_public_id)

    form = ScheduleForm(group=preview_group)
    if form.validate_on_submit():
        day_of_week = form.day_of_week.data
        start_time = form.start_time.data
        end_time = form.end_time.data
        effective_start_date = form.effective_start_date.data
        effective_end_date = form.effective_end_date.data
        location = normalize_optional_text(form.location.data)

        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy = lock_academic_hierarchy(
            term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
        )
        group = _lock_group_in_open_transaction_or_404(group_public_id)
        term = hierarchy.term(term_id)
        level = hierarchy.level(level_id)
        course = hierarchy.course(course_id)

        stale = _schedule_hierarchy_changed(group, term_id, level_id, course_id, course)
        if stale is not None:
            db.session.rollback()
            flash(stale, "danger")
            return _redirect_to_group_schedules(group_public_id)

        blocked = _create_edit_lifecycle_block(group, term=term, level=level, course=course)
        if blocked is not None:
            # Concurrent archive of the Group or an ancestor between the
            # preview and the locks -- match how every other route handles
            # a concurrent-archive race: flash and redirect, no re-render.
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_to_group_schedules(group_public_id)

        rule_error = _schedule_rule_error(
            group,
            term,
            day_of_week,
            start_time,
            end_time,
            effective_start_date,
            effective_end_date,
        )
        if rule_error is not None:
            db.session.rollback()
            form.effective_end_date.errors.append(rule_error)
            return _render_create(form, group_public_id)

        schedule = Schedule(
            group_id=group.id,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            effective_start_date=effective_start_date,
            effective_end_date=effective_end_date,
            location=location,
            status=AcademicStatus.ACTIVE.value,
        )
        db.session.add(schedule)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This schedule could not be saved. An identical slot may already exist, or the "
                "group may have just been changed by someone else. Please reload and try again.",
                "danger",
            )
            return _render_create(form, group_public_id)

        flash("Schedule slot added.", "success")
        return _redirect_to_group_schedules(group_public_id)

    return _render_create(form, group_public_id)


def _render_create(form, group_public_id):
    db.session.rollback()
    group = _load_group_or_404(group_public_id)
    return render_template(
        "admin/schedules/form.html",
        form=form,
        group=group,
        schedule=None,
        weekday_names=WEEKDAY_NAMES,
        app_timezone=_app_timezone(),
        edit_snapshot_token=None,
    )


# ----------------------------------------------------------------------
# Nested edit -- with signed snapshot stale-form protection
# ----------------------------------------------------------------------

_SCHEDULE_EDIT_SNAPSHOT_SALT = "admin.schedule-edit-snapshot.v1"
_SCHEDULE_EDIT_SNAPSHOT_FIELDS = (
    "public_id",
    "day_of_week",
    "start_time",
    "end_time",
    "effective_start_date",
    "effective_end_date",
    "location",
)


def _schedule_edit_snapshot_serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_SCHEDULE_EDIT_SNAPSHOT_SALT)


def _schedule_snapshot_payload(schedule):
    """Deterministic, JSON-safe view of a Schedule's editable persisted
    state plus ``public_id``. Times and dates are serialized as ISO
    strings so the token content is stable and comparable; ``status`` is
    excluded because edit never writes it.
    """
    return {
        "public_id": schedule.public_id,
        "day_of_week": schedule.day_of_week,
        "start_time": schedule.start_time.isoformat(),
        "end_time": schedule.end_time.isoformat(),
        "effective_start_date": schedule.effective_start_date.isoformat(),
        "effective_end_date": schedule.effective_end_date.isoformat(),
        "location": schedule.location,
    }


def _make_schedule_edit_snapshot_token(schedule):
    return _schedule_edit_snapshot_serializer().dumps(_schedule_snapshot_payload(schedule))


def _load_schedule_edit_snapshot(token):
    if not token:
        return None
    try:
        payload = _schedule_edit_snapshot_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_SCHEDULE_EDIT_SNAPSHOT_FIELDS):
        return None
    return payload


def _schedule_edit_is_stale(snapshot, schedule_public_id, current_schedule):
    if snapshot is None:
        return True
    if snapshot["public_id"] != schedule_public_id:
        return True
    return snapshot != _schedule_snapshot_payload(current_schedule)


def _redirect_stale_schedule_edit(group_public_id, schedule_public_id):
    """PRG rejection for a missing/invalid/wrong-object/stale snapshot
    token -- ends the transaction (releasing any write lock), discards
    every submitted value, and redirects to a plain GET that renders the
    current persisted values with a freshly paired token. A fresh token
    is never paired with stale/attempted values in the same response (see
    the Group/Course edit rejection helpers for why).
    """
    db.session.rollback()
    flash(
        "This schedule was changed since this form was opened. Please review the current "
        "values and try again.",
        "danger",
    )
    return redirect(
        url_for(
            "admin.group_schedule_edit",
            group_public_id=group_public_id,
            schedule_public_id=schedule_public_id,
        )
    )


def _render_schedule_edit_failure(form, group_public_id, schedule_public_id, snapshot_token):
    """Shared tail for edit business-rule rejections reached only after
    the submitted snapshot token was confirmed valid and non-stale.
    Releases the write lock before any display query, refetches a fresh
    display copy, and re-embeds the *original* token unchanged.
    """
    db.session.rollback()
    group = _load_group_or_404(group_public_id)
    schedule = _get_schedule_for_group_or_404(group, schedule_public_id)
    return render_template(
        "admin/schedules/form.html",
        form=form,
        group=group,
        schedule=schedule,
        weekday_names=WEEKDAY_NAMES,
        app_timezone=_app_timezone(),
        edit_snapshot_token=snapshot_token,
    )


@admin_bp.route(
    "/groups/<group_public_id>/schedules/<schedule_public_id>/edit", methods=["GET", "POST"]
)
@roles_required(UserRole.ADMINISTRATOR.value)
def group_schedule_edit(group_public_id, schedule_public_id):
    preview_group = _load_group_or_404(group_public_id)
    preview_schedule = _get_schedule_for_group_or_404(preview_group, schedule_public_id)

    early_block = _create_edit_lifecycle_block(preview_group)
    if early_block is not None:
        flash(early_block, "danger")
        return _redirect_to_group_schedules(group_public_id)

    submitted_snapshot_token = None
    if request.method == "POST":
        submitted_snapshot_token = request.form.get("edit_snapshot", "")
        preview_snapshot = _load_schedule_edit_snapshot(submitted_snapshot_token)
        if _schedule_edit_is_stale(preview_snapshot, schedule_public_id, preview_schedule):
            return _redirect_stale_schedule_edit(group_public_id, schedule_public_id)

    form = ScheduleForm(
        obj=preview_schedule if request.method == "GET" else None,
        group=preview_group,
        schedule_id=preview_schedule.id,
    )

    if form.validate_on_submit():
        day_of_week = form.day_of_week.data
        start_time = form.start_time.data
        end_time = form.end_time.data
        effective_start_date = form.effective_start_date.data
        effective_end_date = form.effective_end_date.data
        location = normalize_optional_text(form.location.data)

        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy = lock_academic_hierarchy(
            term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
        )
        group = _lock_group_in_open_transaction_or_404(group_public_id)
        schedule = lock_schedule_in_open_transaction_by_id(preview_schedule.id)
        if schedule is None or schedule.group_id != group.id:
            abort(404)

        snapshot = _load_schedule_edit_snapshot(submitted_snapshot_token)
        if _schedule_edit_is_stale(snapshot, schedule_public_id, schedule):
            return _redirect_stale_schedule_edit(group_public_id, schedule_public_id)

        term = hierarchy.term(term_id)
        level = hierarchy.level(level_id)
        course = hierarchy.course(course_id)

        stale = _schedule_hierarchy_changed(group, term_id, level_id, course_id, course)
        if stale is not None:
            db.session.rollback()
            flash(stale, "danger")
            return _redirect_to_group_schedules(group_public_id)

        # Editing (like creating and reactivating) requires the Group and
        # every ancestor active. Editing an *archived* schedule keeps it
        # archived -- status is never assigned here -- but still requires
        # an active chain, and never silently reactivates the row.
        blocked = _create_edit_lifecycle_block(group, term=term, level=level, course=course)
        if blocked is not None:
            # Concurrent archive between the preview and the locks --
            # flash and redirect, consistent with every other route's
            # concurrent-archive handling.
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_to_group_schedules(group_public_id)

        rule_error = _schedule_rule_error(
            group,
            term,
            day_of_week,
            start_time,
            end_time,
            effective_start_date,
            effective_end_date,
            exclude_schedule_id=schedule.id,
        )
        if rule_error is not None:
            db.session.rollback()
            form.effective_end_date.errors.append(rule_error)
            return _render_schedule_edit_failure(
                form, group_public_id, schedule_public_id, submitted_snapshot_token
            )

        # No field is assigned until every check passed -- a rejection
        # never leaves a partial update. ``schedule.status`` is never
        # among the assigned fields.
        schedule.day_of_week = day_of_week
        schedule.start_time = start_time
        schedule.end_time = end_time
        schedule.effective_start_date = effective_start_date
        schedule.effective_end_date = effective_end_date
        schedule.location = location
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This schedule could not be saved. An identical slot may already exist, or it may "
                "have just been changed by someone else. Please reload and try again.",
                "danger",
            )
            return _render_schedule_edit_failure(
                form, group_public_id, schedule_public_id, submitted_snapshot_token
            )

        flash("Schedule slot updated.", "success")
        return _redirect_to_group_schedules(group_public_id)

    edit_snapshot_token = (
        submitted_snapshot_token
        if request.method == "POST"
        else _make_schedule_edit_snapshot_token(preview_schedule)
    )
    return render_template(
        "admin/schedules/form.html",
        form=form,
        group=preview_group,
        schedule=preview_schedule,
        weekday_names=WEEKDAY_NAMES,
        app_timezone=_app_timezone(),
        edit_snapshot_token=edit_snapshot_token,
    )


# ----------------------------------------------------------------------
# Nested status toggle -- POST only, sole owner of Schedule status
# ----------------------------------------------------------------------


@admin_bp.post("/groups/<group_public_id>/schedules/<schedule_public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_schedule_toggle_status(group_public_id, schedule_public_id):
    preview_group = _load_group_or_404(group_public_id)
    preview_schedule = _get_schedule_for_group_or_404(preview_group, schedule_public_id)
    schedule_id = preview_schedule.id

    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = _lock_group_in_open_transaction_or_404(group_public_id)
    schedule = lock_schedule_in_open_transaction_by_id(schedule_id)
    if schedule is None or schedule.group_id != group.id:
        abort(404)

    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    stale = _schedule_hierarchy_changed(group, term_id, level_id, course_id, course)
    if stale is not None:
        db.session.rollback()
        flash(stale, "danger")
        return _redirect_to_group_schedules(group_public_id)

    if schedule.status == AcademicStatus.ACTIVE.value:
        # Archiving is always allowed -- even when the Group or an
        # ancestor is archived. The row stays as historical record but is
        # not operational.
        schedule.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        flash("Schedule slot archived.", "success")
        return _redirect_to_group_schedules(group_public_id)

    # Reactivation -- parent-first, then re-check the operational rules
    # against current active rows.
    archived_labels = _archived_chain_labels(term, level, course, group)
    if archived_labels:
        db.session.rollback()
        verb = "is" if len(archived_labels) == 1 else "are"
        flash(
            f"This schedule cannot be reactivated while its {_join_labels(archived_labels)} "
            f"{verb} archived. Reactivate the parent first.",
            "danger",
        )
        return _redirect_to_group_schedules(group_public_id)

    rule_error = _schedule_rule_error(
        group,
        term,
        schedule.day_of_week,
        schedule.start_time,
        schedule.end_time,
        schedule.effective_start_date,
        schedule.effective_end_date,
        exclude_schedule_id=schedule.id,
    )
    if rule_error is not None:
        db.session.rollback()
        flash(f"This schedule cannot be reactivated. {rule_error}", "danger")
        return _redirect_to_group_schedules(group_public_id)

    schedule.status = AcademicStatus.ACTIVE.value
    db.session.commit()
    flash("Schedule slot reactivated.", "success")
    return _redirect_to_group_schedules(group_public_id)


# ----------------------------------------------------------------------
# Shared guards -- run only against locked rows (except the friendly
# pre-lock call in create, whose result is re-verified after the lock)
# ----------------------------------------------------------------------


def _join_labels(items):
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _schedule_hierarchy_changed(group, term_id, level_id, course_id, locked_course):
    """The Group's ancestor set moved between the preview read and the
    locks -- we are holding the wrong rows. Returns a message, or None if
    the chain still matches.
    """
    if group.academic_term_id != term_id or group.course_id != course_id:
        return "This group was changed by someone else. Please reload and try again."
    if locked_course is None or locked_course.level_id != level_id:
        return "This group's course changed while saving. Please reload and try again."
    return None


def _create_edit_lifecycle_block(group, term=None, level=None, course=None):
    """Return an Administrator-facing message if a Schedule may not be
    created/edited/reactivated under this Group right now, else None.

    Pre-lock (``term``/``level``/``course`` omitted) it checks the Group
    plus its loaded ancestor relationships for a friendly early error.
    Post-lock it checks the locked ancestor rows.
    """
    if term is None and level is None and course is None:
        chain = (
            group.academic_term.status != AcademicStatus.ACTIVE.value,
            group.course.status != AcademicStatus.ACTIVE.value,
            group.course.level.status != AcademicStatus.ACTIVE.value,
            group.status != AcademicStatus.ACTIVE.value,
        )
        if any(chain):
            return (
                "Schedules can only be created or changed while the group and its academic term, "
                "course, and level are all active."
            )
        return None

    if not _ancestors_active(term, level, course, group):
        labels = _archived_chain_labels(term, level, course, group)
        verb = "is" if len(labels) == 1 else "are"
        return (
            "Schedules can only be created or changed while the group and its academic term, "
            f"course, and level are all active. The {_join_labels(labels)} {verb} archived."
        )
    return None


def _schedule_rule_error(
    group,
    term,
    day_of_week,
    start_time,
    end_time,
    effective_start_date,
    effective_end_date,
    exclude_schedule_id=None,
):
    """The authoritative slot rules, re-checked against locked rows:
    weekday in range, time order, effective-date order, at least one
    actual weekday occurrence, Academic Term containment, and the
    active-overlap rule. Returns a message on the first failure, else
    None. The exact-duplicate ``UniqueConstraint`` remains the database's
    final defense and surfaces as a caught ``IntegrityError``.
    """
    if day_of_week is None or day_of_week < 0 or day_of_week > 6:
        return "Select a valid day of the week."
    if start_time >= end_time:
        return "End time must be after the start time. Overnight slots are not supported."
    if effective_end_date < effective_start_date:
        return "Effective end date cannot be before the effective start date."
    if not range_contains_weekday(day_of_week, effective_start_date, effective_end_date):
        return (
            f"The effective date range contains no {_weekday_label(day_of_week)}. "
            "Widen the range or pick another day."
        )
    if term is not None and not term_contains_range(
        term.start_date, term.end_date, effective_start_date, effective_end_date
    ):
        return (
            "The effective date range must fall within this group's academic term "
            f"({term.start_date.isoformat()} to {term.end_date.isoformat()})."
        )
    conflict = conflicting_active_schedule(
        group.id,
        day_of_week,
        start_time,
        end_time,
        effective_start_date,
        effective_end_date,
        exclude_schedule_id=exclude_schedule_id,
    )
    if conflict is not None:
        return (
            f"This slot overlaps an existing active {_weekday_label(day_of_week)} schedule "
            f"({conflict.start_time.strftime('%H:%M')}-{conflict.end_time.strftime('%H:%M')}, "
            f"effective {conflict.effective_start_date.isoformat()} to "
            f"{conflict.effective_end_date.isoformat()})."
        )
    return None
