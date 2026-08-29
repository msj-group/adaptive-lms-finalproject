from flask import abort, current_app, flash, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import CourseForm
from app.blueprints.admin.utils import move_within_siblings, normalize_optional_text
from app.extensions import db
from app.models import AcademicStatus, Course, Level, UserRole
from app.security.decorators import roles_required
from app.services.course_integrity import course_has_group_reference
from app.services.course_transactions import lock_course_for_write


def _courses_query(level_id=None):
    query = Course.query
    if level_id is not None:
        query = query.filter(Course.level_id == level_id)
    return query.order_by(Course.level_id, Course.display_order, Course.id)


def _next_display_order(level_id):
    max_order = (
        db.session.query(db.func.max(Course.display_order)).filter(Course.level_id == level_id).scalar()
    )
    return (max_order + 1) if max_order is not None else 0


@admin_bp.get("/courses")
@roles_required(UserRole.ADMINISTRATOR.value)
def courses_list():
    level_id = request.args.get("level_id", type=int)
    courses = _courses_query(level_id).all()
    levels = Level.query.order_by(Level.display_order, Level.id).all()
    return render_template(
        "admin/courses/list.html", courses=courses, levels=levels, selected_level_id=level_id
    )


@admin_bp.route("/courses/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def course_create():
    form = CourseForm()
    if request.method == "GET":
        preselect_level_id = request.args.get("level_id", type=int)
        if preselect_level_id is not None:
            form.level_id.data = preselect_level_id

    if form.validate_on_submit():
        course = Course(
            level_id=form.level_id.data,
            title=form.title.data.strip(),
            code=normalize_optional_text(form.code.data),
            description=normalize_optional_text(form.description.data),
            display_order=_next_display_order(form.level_id.data),
        )
        db.session.add(course)
        db.session.commit()
        flash(f"Course '{course.title}' created.", "success")
        return redirect(url_for("admin.courses_list"))
    return render_template("admin/courses/form.html", form=form, course=None)


def _load_course_for_display(public_id):
    """Ordinary, non-locking Course lookup with `level` eagerly loaded --
    used for the initial GET, and to refetch a display copy after a
    post-lock rejection has already released the write lock via
    `db.session.rollback()`. Never call this while a write lock from
    `lock_course_for_write`/`_lock_course_or_404` is still held.
    """
    return Course.query.options(joinedload(Course.level)).filter_by(public_id=public_id).first_or_404()


def _lock_course_or_404(public_id):
    """Thin, route-local 404 wrapper around the shared
    `lock_course_for_write` primitive -- used by both `course_edit` and
    `course_toggle_status` so a Course edit and a Course status toggle
    serialize against each other through the exact same lock.
    """
    course = lock_course_for_write(public_id)
    if course is None:
        abort(404)
    return course


def _course_level_change_error(current_course, requested_level_id):
    """Return an error message if changing `current_course`'s level_id to
    `requested_level_id` is not allowed, else None.

    Approved Part 7B1 Policy A (documented in docs/DECISIONS.md,
    "Course-level identity integrity (Phase 3, Part 7B1)"): once any
    Group -- active or archived, empty or with real
    Enrollment/GroupTeacherAssignment history -- currently references
    this Course, its level_id is frozen. A single Course can back many
    Groups at once, so moving its level would silently reinterpret every
    one of them simultaneously, unlike Group's own
    academic_term_id/course_id freeze, which only ever needed to wait for
    that one Group's own membership history (see
    `group_has_membership_history`). Submitting the Course's own current
    level_id back (no actual change) is always allowed regardless of any
    Group reference; once no Group currently references the Course at
    all, level_id remains freely editable -- see `course_has_group_reference`
    for why this is a *current*-references check, not a historical one.

    Shared by the early, pre-lock friendly check and the authoritative
    post-lock recheck in `course_edit` so the rule cannot drift between
    the two.
    """
    if requested_level_id == current_course.level_id:
        return None
    if course_has_group_reference(current_course.id):
        return (
            "Level cannot be changed because one or more groups currently reference this "
            "course. Archive this course and create a new one under the correct level instead."
        )
    return None


# ----------------------------------------------------------------------
# Course-edit stale-form protection
#
# Mirrors Group edit's own signed-snapshot design (see the "Group-edit
# stale-form protection" section of app/blueprints/admin/groups.py) --
# the Course row lock alone does nothing for a form opened minutes ago
# and submitted after someone else's edit already committed and moved
# on, since the two requests never overlap in time. The snapshot covers
# only the fields `course_edit` may overwrite: `status` is excluded
# because this route never writes it (a separate toggle route owns it),
# and `display_order` is excluded because a same-Level edit never writes
# it, while a valid Level move intentionally computes a *new* destination
# display order rather than preserving whatever was snapshotted.
# ----------------------------------------------------------------------

_COURSE_EDIT_SNAPSHOT_SALT = "admin.course-edit-snapshot.v1"
_COURSE_EDIT_SNAPSHOT_FIELDS = ("public_id", "level_id", "title", "code", "description")


def _course_edit_snapshot_serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_COURSE_EDIT_SNAPSHOT_SALT)


def _make_course_edit_snapshot_token(course):
    """A signed, tamper-evident snapshot of `course`'s persisted editable
    state at the moment an edit form is rendered. Used only to detect,
    after the fresh Course lock, whether anything about the Course
    changed since this particular form was opened -- never as a source of
    the values to write.
    """
    payload = {field: getattr(course, field) for field in _COURSE_EDIT_SNAPSHOT_FIELDS}
    return _course_edit_snapshot_serializer().dumps(payload)


def _load_course_edit_snapshot(token):
    """Decode and verify `token`'s signature. Returns the payload dict,
    or None if the token is missing, empty, malformed, or has been
    tampered with.
    """
    if not token:
        return None
    try:
        payload = _course_edit_snapshot_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_COURSE_EDIT_SNAPSHOT_FIELDS):
        return None
    return payload


def _course_edit_is_stale(snapshot, course_public_id, locked_course):
    """True if `snapshot` (the decoded original-state token) is missing,
    belongs to a different Course than the one just locked, or no longer
    matches the locked Course's current persisted state.

    In every one of those cases the submitted edit form was opened
    against data that is no longer current -- including a change made by
    a completed Course status toggle only if it also touched one of the
    snapshotted fields, which it never does, since `status` is not one of
    them (see the module-level note above).
    """
    if snapshot is None:
        return True
    if snapshot["public_id"] != course_public_id:
        return True
    return any(snapshot[field] != getattr(locked_course, field) for field in _COURSE_EDIT_SNAPSHOT_FIELDS)


def _redirect_stale_course_edit(public_id):
    """Post/Redirect/Get rejection for a snapshot token that is missing,
    empty, invalidly signed, wrong-shaped, belongs to a different Course,
    or is validly signed but no longer matches the Course's current
    persisted state. Mirrors `_redirect_stale_group_edit` exactly --
    ends the current transaction (releasing any write lock the caller may
    be holding) and discards every submitted value via a redirect to a
    plain GET, which renders current persisted values with a freshly,
    correctly paired snapshot token. See that function's docstring for
    why a fresh token must never be paired with stale/attempted values.
    """
    db.session.rollback()
    flash(
        "This course was changed by someone else since this form was opened. "
        "Please review the current values and try again.",
        "danger",
    )
    return redirect(url_for("admin.course_edit", public_id=public_id))


def _render_course_edit_validation_failure(form, public_id, edit_snapshot_token):
    """Shared tail for `course_edit` business-rule rejections (level
    change, IntegrityError) reached only once the submitted snapshot
    token has already been confirmed valid and non-stale. Mirrors
    `_render_group_edit_validation_failure` exactly -- releases the write
    lock immediately, refetches a fresh non-locking display copy, and
    re-renders `form` with the original (never regenerated) token so the
    Administrator's attempted values are preserved without smuggling a
    fresh token past a future staleness check.
    """
    db.session.rollback()
    display_course = _load_course_for_display(public_id)
    return render_template(
        "admin/courses/form.html",
        form=form,
        course=display_course,
        level_locked=course_has_group_reference(display_course.id),
        edit_snapshot_token=edit_snapshot_token,
    )


@admin_bp.route("/courses/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def course_edit(public_id):
    # Ordinary, non-locking Course lookup -- 404 handling and early
    # friendly validation. Nothing decided here is trusted as final; the
    # authoritative level-change/staleness decision is made below, only
    # after the fresh Course lock.
    preview_course = _load_course_for_display(public_id)
    level_locked = course_has_group_reference(preview_course.id)

    submitted_snapshot_token = None
    if request.method == "POST":
        submitted_snapshot_token = request.form.get("edit_snapshot", "")
        preview_snapshot = _load_course_edit_snapshot(submitted_snapshot_token)
        if _course_edit_is_stale(preview_snapshot, public_id, preview_course):
            return _redirect_stale_course_edit(public_id)

    form = CourseForm(obj=preview_course, course_id=preview_course.id)

    if form.validate_on_submit():
        error = _course_level_change_error(preview_course, form.level_id.data)
        if error is not None:
            form.level_id.errors.append(error)
            return _render_course_edit_validation_failure(form, public_id, submitted_snapshot_token)

        # Capture only the plain scalar values the protected transaction
        # needs -- `preview_course` and the request's raw form data above
        # must not be trusted for the actual decision past this point.
        level_id = form.level_id.data
        title = form.title.data.strip()
        code = normalize_optional_text(form.code.data)
        description = normalize_optional_text(form.description.data)

        # Deliberate transaction-boundary reset then lock the *current*
        # Course row -- the first query of this fresh transaction, and
        # the same shared primitive Course edit and Course status toggle
        # both lock the Course with, so they serialize against each
        # other.
        course = _lock_course_or_404(public_id)

        # Staleness first, repeating the same check already performed
        # above against the preview read, now against the locked,
        # current one -- this is what closes the window between that
        # earlier preview and this lock.
        snapshot = _load_course_edit_snapshot(submitted_snapshot_token)
        if _course_edit_is_stale(snapshot, public_id, course):
            # No rollback here: _redirect_stale_course_edit is the single
            # owner of stale-rejection rollback and releases the write
            # lock itself before redirecting.
            return _redirect_stale_course_edit(public_id)

        error = _course_level_change_error(course, level_id)
        if error is not None:
            form.level_id.errors.append(error)
            return _render_course_edit_validation_failure(form, public_id, submitted_snapshot_token)

        # No field is assigned until every check above has passed, so a
        # rejection here never leaves a partial update -- either every
        # field below is written and committed together, or none are.
        moving_to_new_level = level_id != course.level_id
        course.title = title
        course.code = code
        course.description = description
        if moving_to_new_level:
            # Computed only here, in the protected write phase, after the
            # Course lock -- never trusted from any pre-lock read.
            course.display_order = _next_display_order(level_id)
            course.level_id = level_id
        try:
            db.session.commit()
        except IntegrityError:
            # Do not assume this is specifically a duplicate title/code --
            # the staleness and level-change checks above already cover
            # the conditions this route can identify with confidence, so
            # anything that still reaches the database's own constraints
            # here is reported generically rather than guessed at. No raw
            # SQL, parameters, or driver text ever reaches the user.
            flash(
                "This course could not be saved. It may conflict with another course's title or "
                "code in the selected level, or it may have just been changed by someone else. "
                "Please reload and try again.",
                "danger",
            )
            return _render_course_edit_validation_failure(form, public_id, submitted_snapshot_token)
        flash(f"Course '{course.title}' updated.", "success")
        return redirect(url_for("admin.courses_list"))

    # GET, or a POST whose token passed both no-mutation checks above but
    # failed ordinary WTForms field validation: the submitted token (for
    # POST) or a freshly generated one bound to the current preview (for
    # GET) is re-embedded unchanged -- never regenerated here, for the
    # same reason the two rejection helpers above never regenerate one
    # either.
    edit_snapshot_token = (
        submitted_snapshot_token
        if request.method == "POST"
        else _make_course_edit_snapshot_token(preview_course)
    )
    return render_template(
        "admin/courses/form.html",
        form=form,
        course=preview_course,
        level_locked=level_locked,
        edit_snapshot_token=edit_snapshot_token,
    )


@admin_bp.post("/courses/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def course_toggle_status(public_id):
    course = _lock_course_or_404(public_id)
    course.status = (
        AcademicStatus.ARCHIVED.value
        if course.status == AcademicStatus.ACTIVE.value
        else AcademicStatus.ACTIVE.value
    )
    db.session.commit()
    flash(f"Course '{course.title}' is now {course.status}.", "success")
    return redirect(url_for("admin.courses_list"))


@admin_bp.post("/courses/<public_id>/move-up")
@roles_required(UserRole.ADMINISTRATOR.value)
def course_move_up(public_id):
    course = Course.query.filter_by(public_id=public_id).first_or_404()
    _, moved = move_within_siblings(_courses_query(course.level_id).all(), public_id, -1)
    if moved:
        db.session.commit()
        flash(f"Course '{course.title}' moved up.", "success")
    else:
        flash("This course is already first in its level.", "warning")
    return redirect(url_for("admin.courses_list"))


@admin_bp.post("/courses/<public_id>/move-down")
@roles_required(UserRole.ADMINISTRATOR.value)
def course_move_down(public_id):
    course = Course.query.filter_by(public_id=public_id).first_or_404()
    _, moved = move_within_siblings(_courses_query(course.level_id).all(), public_id, 1)
    if moved:
        db.session.commit()
        flash(f"Course '{course.title}' moved down.", "success")
    else:
        flash("This course is already last in its level.", "warning")
    return redirect(url_for("admin.courses_list"))
