"""Teacher management of Group-owned Units (M10).

Group-centered routes only (``/teacher/groups/<group_public_id>/units/...``)
-- there is deliberately no flat ``/teacher/units`` collection. Every
object is addressed by ``public_id``; no internal numeric id ever appears
in a URL, form value, or the rendered HTML.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then
server-side: the current Teacher must hold an **active**
``GroupTeacherAssignment`` to the Group in the URL. An unassigned
Teacher, a removed assignment, a Unit public_id that belongs to another
Group, or a missing object all return **404** -- never a 403 or any hint
that the object exists. Multiple active assigned Teachers are equal
collaborators. GET stays available to an actively assigned Teacher even
when the Group or an ancestor is archived, so historical Units can be
read.

**Mutations** (create / edit / reorder / reactivate) additionally
require the Group *and* its AcademicTerm, Course, and Level to be active,
plus an active Teacher account. **Archiving** is allowed while the
assignment is active even under an archived Group/ancestor, to permit
safe cleanup. Editing an archived Unit keeps it archived; a Group /
ancestor lifecycle change never cascades into Unit status; nothing is
ever hard-deleted.

**Concurrency.** Every mutation follows the canonical lock order

    AcademicTerm -> Level -> Course -> Group -> Teacher User ->
    GroupTeacherAssignment -> Unit rows (ascending internal id)

via ``lock_academic_hierarchy`` (which owns the single deliberate reset)
then the Group / User / assignment / Unit ``SELECT ... FOR UPDATE`` in
the same open transaction. The Group lock serializes same-Group creation
and reordering (and Group retargeting in the admin section, which takes
the same Group lock -- so a concurrent Unit create cannot bypass the
identity freeze). All authoritative conditions are re-checked against the
locked rows; a signed snapshot protects the edit form against a
time-separated overwrite. ``IntegrityError`` is caught, rolled back, and
reported generically.
"""

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.admin.utils import move_within_siblings, normalize_optional_text
from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.forms import UnitForm
from app.extensions import db
from app.models import (
    AcademicStatus,
    Course,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.unit_queries import (
    active_units_ordered,
    archived_units_ordered,
    next_unit_display_order,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value


# ----------------------------------------------------------------------
# Object authorization (non-locking preview) + nested lookups
# ----------------------------------------------------------------------


def _teacher_group_or_404(group_public_id):
    """The Group named by `group_public_id`, but only when the current
    Teacher holds an ACTIVE assignment to it. Every other case -- no such
    Group, unassigned Teacher, removed assignment -- aborts 404 without
    revealing whether the Group exists. Does NOT check the Group's own
    status: GET stays available for historical Units under an archived
    Group.
    """
    group = (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=group_public_id)
        .first()
    )
    if group is None:
        abort(404)
    has_active_assignment = db.session.query(
        GroupTeacherAssignment.query.filter_by(
            group_id=group.id, teacher_id=current_user.id, status=_ASSIGNMENT_ACTIVE
        ).exists()
    ).scalar()
    if not has_active_assignment:
        abort(404)
    return group


def _unit_for_group_or_404(group, unit_public_id):
    """A Unit by its own public_id, constrained to `group`. A Unit
    public_id valid only for another Group 404s here."""
    return Unit.query.filter_by(public_id=unit_public_id, group_id=group.id).first_or_404()


def _group_is_operational(group):
    return (
        group.status == _ACTIVE
        and group.academic_term.status == _ACTIVE
        and group.course.status == _ACTIVE
        and group.course.level.status == _ACTIVE
    )


def _archived_chain_labels(group):
    return [
        label
        for label, ok in (
            ("academic term", group.academic_term.status == _ACTIVE),
            ("level", group.course.level.status == _ACTIVE),
            ("course", group.course.status == _ACTIVE),
            ("group", group.status == _ACTIVE),
        )
        if not ok
    ]


def _join_labels(items):
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _redirect_units(group_public_id):
    return redirect(url_for("teacher.group_units", group_public_id=group_public_id))


# ----------------------------------------------------------------------
# Canonical lock chain + post-lock re-checks
# ----------------------------------------------------------------------


def _lock_unit_chain(group_public_id, term_id, level_id, course_id, teacher_id, unit_ids=()):
    """Acquire the canonical M10 lock order in one open transaction:

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group -> Teacher User -> GroupTeacherAssignment
        -> Unit rows (ascending internal id)

    Returns ``(hierarchy, group, teacher, assignment, {unit_id: unit})``.
    Any of group / teacher / assignment / a unit may be ``None`` -- the
    caller must treat that as a business/authorization rejection, roll
    back, and 404 or redirect; it must never "keep going".
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    teacher = User.query.filter_by(id=teacher_id).with_for_update().first()
    assignment = None
    if group is not None:
        assignment = (
            GroupTeacherAssignment.query.filter_by(group_id=group.id, teacher_id=teacher_id)
            .with_for_update()
            .first()
        )
    units = {}
    for uid in sorted({u for u in unit_ids if u is not None}):
        units[uid] = Unit.query.filter_by(id=uid).with_for_update().first()
    return hierarchy, group, teacher, assignment, units


def _authz_broken(group, teacher, assignment):
    """True when the locked rows no longer authorize a Unit mutation for
    the current Teacher -- caller rolls back and 404s (no disclosure)."""
    if group is None or teacher is None or assignment is None:
        return True
    if teacher.role != UserRole.TEACHER.value or teacher.status != UserStatus.ACTIVE.value:
        return True
    return assignment.status != _ASSIGNMENT_ACTIVE


def _hierarchy_active_error(hierarchy, group, term_id, level_id, course_id):
    """`None` if the locked Group and its locked AcademicTerm / Level /
    Course all exist and are active (so a create / edit / reorder /
    reactivate may proceed), else a Teacher-facing message."""
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    if (
        course is None
        or group.course_id != course.id
        or group.academic_term_id != term_id
        or course.level_id != level_id
    ):
        return "This group changed while you were working. Reload the page and try again."
    labels = [
        label
        for label, row in (
            ("academic term", term),
            ("level", level),
            ("course", course),
            ("group", group),
        )
        if row is None or row.status != _ACTIVE
    ]
    if labels:
        verb = "is" if len(labels) == 1 else "are"
        return (
            "Units can only be created, edited, or reordered while the group and its academic "
            f"term, course, and level are all active. The {_join_labels(labels)} {verb} archived."
        )
    return None


# ----------------------------------------------------------------------
# List page
# ----------------------------------------------------------------------


@teacher_bp.get("/groups/<group_public_id>/units")
@roles_required(UserRole.TEACHER.value)
def group_units(group_public_id):
    group = _teacher_group_or_404(group_public_id)
    operational = _group_is_operational(group)
    return render_template(
        "teacher/units/list.html",
        group=group,
        active_units=active_units_ordered(group.id),
        archived_units=archived_units_ordered(group.id),
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
    )


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------


@teacher_bp.route("/groups/<group_public_id>/units/new", methods=["GET", "POST"])
@roles_required(UserRole.TEACHER.value)
def unit_create(group_public_id):
    preview_group = _teacher_group_or_404(group_public_id)

    if not _group_is_operational(preview_group):
        flash(
            "Units can only be created while the group and its academic term, course, and level "
            "are all active.",
            "danger",
        )
        return _redirect_units(group_public_id)

    form = UnitForm(group_id=preview_group.id)
    if form.validate_on_submit():
        title = form.title.data.strip()
        description = normalize_optional_text(form.description.data)
        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy, group, teacher, assignment, _ = _lock_unit_chain(
            group_public_id, term_id, level_id, course_id, current_user.id
        )
        if _authz_broken(group, teacher, assignment):
            db.session.rollback()
            abort(404)

        blocked = _hierarchy_active_error(hierarchy, group, term_id, level_id, course_id)
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_units(group_public_id)

        unit = Unit(
            group_id=group.id,
            title=title,
            description=description,
            display_order=next_unit_display_order(group.id),
            status=_ACTIVE,
        )
        db.session.add(unit)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This unit could not be saved. A unit with this title may already exist in the "
                "group, or the group may have just changed. Please reload and try again.",
                "danger",
            )
            return _render_unit_form(form, group_public_id, unit=None, snapshot_token=None)

        flash(f"Unit '{unit.title}' created.", "success")
        return _redirect_units(group_public_id)

    return _render_unit_form(form, group_public_id, unit=None, snapshot_token=None)


def _render_unit_form(form, group_public_id, unit, snapshot_token):
    """Re-render the create/edit form -- releases any write lock first
    (harmless on GET / a plain form failure) and refetches a display copy
    of the Group. `unit` is None for create."""
    db.session.rollback()
    group = _teacher_group_or_404(group_public_id)
    return render_template(
        "teacher/units/form.html",
        form=form,
        group=group,
        unit=unit,
        edit_snapshot_token=snapshot_token,
    )


# ----------------------------------------------------------------------
# Edit -- with a signed stale-form snapshot
# ----------------------------------------------------------------------

_UNIT_EDIT_SNAPSHOT_SALT = "teacher.unit-edit-snapshot.v1"
_UNIT_EDIT_SNAPSHOT_FIELDS = ("public_id", "title", "description")


def _unit_snapshot_serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_UNIT_EDIT_SNAPSHOT_SALT)


def _unit_snapshot_payload(unit):
    return {field: getattr(unit, field) for field in _UNIT_EDIT_SNAPSHOT_FIELDS}


def _make_unit_snapshot_token(unit):
    return _unit_snapshot_serializer().dumps(_unit_snapshot_payload(unit))


def _load_unit_snapshot(token):
    if not token:
        return None
    try:
        payload = _unit_snapshot_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_UNIT_EDIT_SNAPSHOT_FIELDS):
        return None
    return payload


def _unit_edit_is_stale(snapshot, unit_public_id, current_unit):
    if snapshot is None:
        return True
    if snapshot["public_id"] != unit_public_id:
        return True
    return snapshot != _unit_snapshot_payload(current_unit)


def _redirect_stale_unit_edit(group_public_id, unit_public_id):
    db.session.rollback()
    flash(
        "This unit was changed since this form was opened. Please review the current values "
        "and try again.",
        "danger",
    )
    return redirect(
        url_for(
            "teacher.unit_edit",
            group_public_id=group_public_id,
            unit_public_id=unit_public_id,
        )
    )


@teacher_bp.route(
    "/groups/<group_public_id>/units/<unit_public_id>/edit", methods=["GET", "POST"]
)
@roles_required(UserRole.TEACHER.value)
def unit_edit(group_public_id, unit_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)

    if not _group_is_operational(preview_group):
        flash(
            "Units can only be edited while the group and its academic term, course, and level "
            "are all active.",
            "danger",
        )
        return _redirect_units(group_public_id)

    submitted_token = None
    if request.method == "POST":
        submitted_token = request.form.get("edit_snapshot", "")
        if _unit_edit_is_stale(_load_unit_snapshot(submitted_token), unit_public_id, preview_unit):
            return _redirect_stale_unit_edit(group_public_id, unit_public_id)

    form = UnitForm(
        obj=preview_unit if request.method == "GET" else None,
        group_id=preview_group.id,
        unit_id=preview_unit.id,
    )

    if form.validate_on_submit():
        title = form.title.data.strip()
        description = normalize_optional_text(form.description.data)
        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy, group, teacher, assignment, units = _lock_unit_chain(
            group_public_id, term_id, level_id, course_id, current_user.id,
            unit_ids=[preview_unit.id],
        )
        if _authz_broken(group, teacher, assignment):
            db.session.rollback()
            abort(404)
        unit = units.get(preview_unit.id)
        if unit is None or unit.group_id != group.id:
            db.session.rollback()
            abort(404)

        if _unit_edit_is_stale(_load_unit_snapshot(submitted_token), unit_public_id, unit):
            return _redirect_stale_unit_edit(group_public_id, unit_public_id)

        blocked = _hierarchy_active_error(hierarchy, group, term_id, level_id, course_id)
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_units(group_public_id)

        # `status` and `display_order` are never assigned here -- editing
        # an archived Unit keeps it archived, and never reorders.
        unit.title = title
        unit.description = description
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This unit could not be saved. A unit with this title may already exist in the "
                "group. Please reload and try again.",
                "danger",
            )
            return _render_unit_form(form, group_public_id, preview_unit, submitted_token)

        flash(f"Unit '{unit.title}' updated.", "success")
        return _redirect_units(group_public_id)

    edit_snapshot_token = (
        submitted_token
        if request.method == "POST"
        else _make_unit_snapshot_token(preview_unit)
    )
    return render_template(
        "teacher/units/form.html",
        form=form,
        group=preview_group,
        unit=preview_unit,
        edit_snapshot_token=edit_snapshot_token,
    )


# ----------------------------------------------------------------------
# Status toggle -- POST only, sole owner of Unit status
# ----------------------------------------------------------------------


@teacher_bp.post("/groups/<group_public_id>/units/<unit_public_id>/toggle-status")
@roles_required(UserRole.TEACHER.value)
def unit_toggle_status(group_public_id, unit_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    hierarchy, group, teacher, assignment, units = _lock_unit_chain(
        group_public_id, term_id, level_id, course_id, current_user.id,
        unit_ids=[preview_unit.id],
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    unit = units.get(preview_unit.id)
    if unit is None or unit.group_id != group.id:
        db.session.rollback()
        abort(404)

    if unit.status == _ACTIVE:
        # Archiving is always allowed while the assignment is active --
        # even under an archived Group/ancestor (safe cleanup). The
        # stored display_order is left untouched.
        unit.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        flash(f"Unit '{unit.title}' archived.", "success")
        return _redirect_units(group_public_id)

    # Reactivation -- requires the operational chain, and appends the Unit
    # after the Group's current highest display_order.
    blocked = _hierarchy_active_error(hierarchy, group, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_units(group_public_id)

    unit.status = _ACTIVE
    unit.display_order = next_unit_display_order(group.id)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This unit could not be reactivated. Please reload and try again.", "danger")
        return _redirect_units(group_public_id)

    flash(f"Unit '{unit.title}' reactivated.", "success")
    return _redirect_units(group_public_id)


# ----------------------------------------------------------------------
# Reordering -- move up / down among ACTIVE siblings
# ----------------------------------------------------------------------


def _move_unit(group_public_id, unit_public_id, offset, direction_word):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)

    if preview_unit.status != _ACTIVE:
        flash("Only active units can be reordered.", "warning")
        return _redirect_units(group_public_id)
    if not _group_is_operational(preview_group):
        flash(
            "Units can only be reordered while the group and its academic term, course, and "
            "level are all active.",
            "danger",
        )
        return _redirect_units(group_public_id)

    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    hierarchy, group, teacher, assignment, _ = _lock_unit_chain(
        group_public_id, term_id, level_id, course_id, current_user.id
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)

    blocked = _hierarchy_active_error(hierarchy, group, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_units(group_public_id)

    # Re-read the active teaching order under the held Group lock (which
    # serializes every same-Group create / reorder / toggle), then lock
    # the target Unit and its swap neighbour FOR UPDATE, ascending id.
    ordered = active_units_ordered(group.id)
    index = next((i for i, u in enumerate(ordered) if u.public_id == unit_public_id), None)
    if index is None:
        db.session.rollback()
        flash("This unit is no longer active.", "warning")
        return _redirect_units(group_public_id)
    target_index = index + offset
    if target_index < 0 or target_index >= len(ordered):
        db.session.rollback()
        flash(f"This unit is already {direction_word}.", "warning")
        return _redirect_units(group_public_id)

    for uid in sorted((ordered[index].id, ordered[target_index].id)):
        Unit.query.filter_by(id=uid).with_for_update().first()

    _, moved = move_within_siblings(ordered, unit_public_id, offset)
    if not moved:  # defensive -- bounds already checked above
        db.session.rollback()
        flash(f"This unit is already {direction_word}.", "warning")
        return _redirect_units(group_public_id)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The units could not be reordered. Please reload and try again.", "danger")
        return _redirect_units(group_public_id)

    flash("Unit order updated.", "success")
    return _redirect_units(group_public_id)


@teacher_bp.post("/groups/<group_public_id>/units/<unit_public_id>/move-up")
@roles_required(UserRole.TEACHER.value)
def unit_move_up(group_public_id, unit_public_id):
    return _move_unit(group_public_id, unit_public_id, -1, "first")


@teacher_bp.post("/groups/<group_public_id>/units/<unit_public_id>/move-down")
@roles_required(UserRole.TEACHER.value)
def unit_move_down(group_public_id, unit_public_id):
    return _move_unit(group_public_id, unit_public_id, 1, "last")
