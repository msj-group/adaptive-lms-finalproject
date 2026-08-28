from flask import abort, current_app, flash, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import GroupForm
from app.blueprints.admin.utils import normalize_optional_text
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.group_memberships import active_student_enrollment_count, group_has_membership_history
from app.services.group_transactions import lock_group_for_write


def _course_choices():
    return Course.query.join(Level).order_by(Level.display_order, Course.display_order).all()


_MAX_BIGINT = 9223372036854775807


def _safe_id_arg(name):
    """Parse a positive integer query-string filter, safely discarding
    values that are not a valid id (missing, non-numeric, zero/negative,
    or too large for the BIGINT columns) instead of letting them reach
    the database and raise an unhandled error.
    """
    value = request.args.get(name, type=int)
    if value is None or value < 1 or value > _MAX_BIGINT:
        return None
    return value


@admin_bp.get("/groups")
@roles_required(UserRole.ADMINISTRATOR.value)
def groups_list():
    search = request.args.get("q", "").strip()
    term_id = _safe_id_arg("term_id")
    course_id = _safe_id_arg("course_id")
    level_id = _safe_id_arg("level_id")
    status = request.args.get("status", "").strip()

    query = Group.query.options(
        joinedload(Group.course).joinedload(Course.level),
        joinedload(Group.academic_term),
    )
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Group.name.ilike(like), Group.code.ilike(like)))
    if term_id:
        query = query.filter(Group.academic_term_id == term_id)
    if course_id:
        query = query.filter(Group.course_id == course_id)
    if level_id:
        query = query.join(Course, Group.course_id == Course.id).filter(Course.level_id == level_id)
    if status in {s.value for s in AcademicStatus}:
        query = query.filter(Group.status == status)

    groups = query.order_by(Group.created_at.desc()).all()
    return render_template(
        "admin/groups/list.html",
        groups=groups,
        search=search,
        terms=AcademicTerm.query.order_by(AcademicTerm.start_date.desc()).all(),
        courses=_course_choices(),
        levels=Level.query.order_by(Level.display_order, Level.id).all(),
        selected_term_id=term_id,
        selected_course_id=course_id,
        selected_level_id=level_id,
        selected_status=status,
    )


@admin_bp.get("/groups/<public_id>")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_detail(public_id):
    group = (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=public_id)
        .first_or_404()
    )
    # One focused query for the names of currently-eligible active
    # Teachers (status=active assignment, role=teacher, active account)
    # -- not a full Teacher-assignment listing, so no N+1 risk regardless
    # of how many assignments this Group has.
    eligible_teacher_names = [
        row[0]
        for row in db.session.query(User.full_name)
        .join(GroupTeacherAssignment, GroupTeacherAssignment.teacher_id == User.id)
        .filter(
            GroupTeacherAssignment.group_id == group.id,
            GroupTeacherAssignment.status == GroupTeacherAssignmentStatus.ACTIVE.value,
            User.role == UserRole.TEACHER.value,
            User.status == UserStatus.ACTIVE.value,
        )
        .order_by(User.full_name)
        .all()
    ]
    return render_template(
        "admin/groups/detail.html",
        group=group,
        eligible_teacher_names=eligible_teacher_names,
        active_student_count=active_student_enrollment_count(group.id),
    )


@admin_bp.route("/groups/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def group_create():
    if AcademicTerm.query.count() == 0 or Course.query.count() == 0:
        flash("Create at least one Academic Term and one Course before creating a Group.", "warning")
        return redirect(url_for("admin.groups_list"))

    form = GroupForm()
    if form.validate_on_submit():
        group = Group(
            academic_term_id=form.academic_term_id.data,
            course_id=form.course_id.data,
            name=form.name.data.strip(),
            code=normalize_optional_text(form.code.data),
            capacity=form.capacity.data,
            status=form.status.data,
        )
        db.session.add(group)
        db.session.commit()
        flash(f"Group '{group.name}' created.", "success")
        return redirect(url_for("admin.groups_list"))

    return render_template("admin/groups/form.html", form=form, courses=_course_choices(), group=None)


def _group_identity_change_error(current_group, has_history, academic_term_id, course_id):
    """Return an error message if changing Course/AcademicTerm on
    `current_group` to the submitted values is not allowed, else None.

    Academic Term and Course are this Group's academic identity (see
    "Group model" and the Group-centered membership management sections
    in docs/DECISIONS.md). Once any Enrollment or GroupTeacherAssignment
    row has ever existed for the Group -- active, withdrawn/removed, or
    even one referencing a corrupted (non-Student/non-Teacher) User, all
    equally count as real relationship history -- that history was
    created under this specific Course/AcademicTerm combination, and
    changing either field afterward would silently reinterpret it:
    the same-Course-same-Term Enrollment conflict rule
    (`conflicting_active_enrollment`) reads the Group's *current*
    course_id/academic_term_id, not whatever it was when each Enrollment
    was created, so retargeting the Group would retroactively change
    what counts as a conflict for every existing Enrollment. Posting the
    Group's own current values back (no actual change) is always
    allowed, and once no membership history exists at all, both fields
    remain freely editable.

    Shared by the early, pre-lock friendly check and the authoritative
    post-lock recheck in `group_edit` so the rule cannot drift between
    the two.
    """
    if not has_history:
        return None
    if academic_term_id == current_group.academic_term_id and course_id == current_group.course_id:
        return None
    return (
        "Academic Term and Course cannot be changed once this group has enrollment or "
        "teacher-assignment history. Create a new group instead and archive this one."
    )


# ----------------------------------------------------------------------
# Group-edit stale-form protection
#
# The Group row lock (`lock_group_for_write`) only protects two
# transactions that are genuinely open at the same time -- it does
# nothing for a form an Administrator opened minutes ago, submitted after
# someone else's edit already committed and moved on. That is a
# staleness problem, not a concurrency one: the two requests never
# overlap, so there is no lock contention to catch it. A signed snapshot
# of the Group's persisted editable state at render time, verified
# against the *locked, current* state after the fresh lock, is what
# catches it instead -- no model change or migration needed, since the
# snapshot lives only in the rendered form, signed so it cannot be
# forged into claiming an original state that never existed.
# ----------------------------------------------------------------------

_GROUP_EDIT_SNAPSHOT_SALT = "admin.group-edit-snapshot.v1"
_GROUP_EDIT_SNAPSHOT_FIELDS = (
    "public_id",
    "academic_term_id",
    "course_id",
    "name",
    "code",
    "capacity",
    "status",
)


def _group_edit_snapshot_serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_GROUP_EDIT_SNAPSHOT_SALT)


def _make_group_edit_snapshot_token(group):
    """A signed, tamper-evident snapshot of `group`'s persisted editable
    state at the moment an edit form is rendered.

    Used only to detect, after the fresh Group lock, whether anything
    about the Group changed since this particular form was opened --
    never as a source of the values to write. The requested new values
    always come from the submitted form fields themselves; decoding this
    token never produces "new" data, only the original state to compare
    against.
    """
    payload = {field: getattr(group, field) for field in _GROUP_EDIT_SNAPSHOT_FIELDS}
    return _group_edit_snapshot_serializer().dumps(payload)


def _load_group_edit_snapshot(token):
    """Decode and verify `token`'s signature. Returns the payload dict,
    or None if the token is missing, empty, malformed, or has been
    tampered with.
    """
    if not token:
        return None
    try:
        payload = _group_edit_snapshot_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_GROUP_EDIT_SNAPSHOT_FIELDS):
        return None
    return payload


def _group_edit_is_stale(snapshot, group_public_id, locked_group):
    """True if `snapshot` (the decoded original-state token) is missing,
    belongs to a different Group than the one just locked, or no longer
    matches the locked Group's current persisted state.

    In every one of those cases the submitted edit form was opened
    against data that is no longer current, so the whole update must be
    rejected rather than silently overwriting whatever changed since --
    including a change made by a completed Group status toggle, which
    updates `status`, one of the compared fields.
    """
    if snapshot is None:
        return True
    if snapshot["public_id"] != group_public_id:
        return True
    return any(snapshot[field] != getattr(locked_group, field) for field in _GROUP_EDIT_SNAPSHOT_FIELDS)


def _load_group_for_display(public_id):
    """Ordinary, non-locking Group lookup with the relationships the edit
    page and the snapshot both need eagerly loaded -- used for the
    initial GET, and to refetch a display copy after a post-lock
    rejection has already released the write lock via
    `db.session.rollback()`. Never call this while a write lock from
    `lock_group_for_write`/`_lock_group_or_404` is still held: it is a
    plain read, not a lock, and issuing it beforehand would be exactly
    the kind of extra query between the lock and the authoritative
    checks this module's transaction design deliberately avoids.
    """
    return (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=public_id)
        .first_or_404()
    )


def _lock_group_or_404(public_id):
    """Thin, route-local 404 wrapper around the shared
    `lock_group_for_write` primitive (see its docstring in
    `app/services/group_transactions.py`) -- used by both `group_edit`
    and `group_toggle_status` so a Group edit and a Group status toggle
    serialize against each other, and against every Enrollment/
    GroupTeacherAssignment mutation, through the exact same lock.
    """
    group = lock_group_for_write(public_id)
    if group is None:
        abort(404)
    return group


def _redirect_stale_group_edit(public_id):
    """Post/Redirect/Get rejection for a snapshot token that is missing,
    empty, invalidly signed, wrong-shaped, belongs to a different Group,
    or is validly signed but no longer matches the Group's current
    persisted state -- whether caught by the early check (against the
    unlocked preview read) or the late one (against the locked, current
    read).

    Ends the current transaction -- releasing any write lock the caller
    may be holding; harmless if none is held, since nothing has been
    mutated yet either way -- and discards every submitted editable
    value entirely via a redirect to a plain GET of the edit page, which
    renders current persisted values together with a freshly, correctly
    paired snapshot token.

    Never render the submitted (attempted) values here. A token that
    just failed this check proves nothing about the request can be
    trusted; pairing a *fresh* token with those *stale/attempted* values
    in the same response would let an unmodified resubmission of that
    response slip straight past the staleness check on whatever the
    Group's state is by then -- silently overwriting it. This is exactly
    the bypass this function exists to close: a fresh token may only
    ever be paired with form values loaded from that same fresh
    persisted Group state, which only a fresh GET (not this rejection)
    provides.
    """
    db.session.rollback()
    flash(
        "This group was changed by someone else since this form was opened. "
        "Please review the current values and try again.",
        "danger",
    )
    return redirect(url_for("admin.group_edit", public_id=public_id))


def _render_group_edit_validation_failure(form, public_id, edit_snapshot_token):
    """Shared tail for `group_edit` business-rule rejections (identity
    change, capacity, IntegrityError) reached only once the submitted
    snapshot token has already been confirmed valid and non-stale.

    Releases the write lock immediately -- before any display query or
    template rendering runs, not merely at request teardown -- refetches
    a fresh, non-locking display copy of the Group for rendering, and
    re-renders `form` (with whatever field errors the caller already
    attached) so the Administrator's attempted values are preserved. The
    caller-supplied `edit_snapshot_token` -- the *original* token that was
    just proven valid, never a freshly generated one -- is re-embedded
    unchanged: a fresh token could only correctly pair with fresh
    (current, not attempted) field values, exactly like
    `_redirect_stale_group_edit` avoids for the same reason. Preserving
    the original token instead means that if the Group changes again
    before the next submission, that same token will correctly be caught
    as stale then, rather than silently validating against whatever the
    Group has become.
    """
    db.session.rollback()
    display_group = _load_group_for_display(public_id)
    return render_template(
        "admin/groups/form.html",
        form=form,
        courses=_course_choices(),
        group=display_group,
        identity_locked=group_has_membership_history(display_group.id),
        edit_snapshot_token=edit_snapshot_token,
    )


@admin_bp.route("/groups/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def group_edit(public_id):
    # Ordinary, non-locking Group lookup -- 404 handling and early
    # friendly validation. Nothing decided here is trusted as final; the
    # authoritative identity/capacity/staleness decision is made below,
    # only after the fresh Group lock.
    preview_group = _load_group_for_display(public_id)
    identity_locked = group_has_membership_history(preview_group.id)

    submitted_snapshot_token = None
    if request.method == "POST":
        # Snapshot-token validation runs before the GroupForm is even
        # constructed and before any ordinary WTForms field validation,
        # and is never skipped merely because another field is also
        # invalid -- see _redirect_stale_group_edit's docstring for why a
        # stale/missing/invalid/cross-Group token means nothing else
        # about the submission can be trusted. This is only an early,
        # no-mutation check against the unlocked preview read; the
        # authoritative recheck against the locked, current Group still
        # happens further below regardless of this one's outcome.
        submitted_snapshot_token = request.form.get("edit_snapshot", "")
        preview_snapshot = _load_group_edit_snapshot(submitted_snapshot_token)
        if _group_edit_is_stale(preview_snapshot, public_id, preview_group):
            return _redirect_stale_group_edit(public_id)

    form = GroupForm(obj=preview_group, group_id=preview_group.id)

    if form.validate_on_submit():
        error = _group_identity_change_error(
            preview_group, identity_locked, form.academic_term_id.data, form.course_id.data
        )
        if error is not None:
            form.academic_term_id.errors.append(error)
            form.course_id.errors.append(error)
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)

        # Capture only the plain scalar values the protected transaction
        # needs -- `preview_group`, `identity_locked`, and the request's
        # raw form data above must not be trusted for the actual decision
        # past this point; they belong to the read-only snapshot that is
        # about to be ended.
        academic_term_id = form.academic_term_id.data
        course_id = form.course_id.data
        name = form.name.data.strip()
        code = normalize_optional_text(form.code.data)
        capacity = form.capacity.data
        status = form.status.data

        # Deliberate transaction-boundary reset (see
        # lock_group_for_write's docstring for the full MySQL/InnoDB
        # REPEATABLE READ rationale) then lock the *current* Group row --
        # the first query of this fresh transaction, and the same shared
        # primitive every Group-affecting mutation route locks the Group
        # with, so a concurrent edit, status toggle, or Enrollment/
        # Assignment change on the same Group serialize against each
        # other.
        group = _lock_group_or_404(public_id)

        # Every critical rule is rechecked here against the freshly
        # locked Group and current, just-queried relationship/count data
        # -- nothing from the pre-lock preview or form validation above
        # is trusted as final. Staleness first, repeating the same check
        # already performed above against the preview read, now against
        # the locked, current one -- this is what closes the window
        # between that earlier preview and this lock. If the form was
        # opened (or has since become) against data that is no longer
        # current, nothing else about the submission is trustworthy
        # either, so this is a stale-token rejection (discard everything,
        # PRG), not a business-validation one.
        snapshot = _load_group_edit_snapshot(submitted_snapshot_token)
        if _group_edit_is_stale(snapshot, public_id, group):
            # No rollback here: `_redirect_stale_group_edit` is the single
            # owner of stale-rejection rollback and releases the write
            # lock itself before redirecting.
            return _redirect_stale_group_edit(public_id)

        has_history = group_has_membership_history(group.id)
        error = _group_identity_change_error(group, has_history, academic_term_id, course_id)
        if error is not None:
            form.academic_term_id.errors.append(error)
            form.course_id.errors.append(error)
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)

        active_count = active_student_enrollment_count(group.id)
        if capacity < active_count:
            form.capacity.errors.append(
                f"Capacity cannot be lower than the current active student count ({active_count})."
            )
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)

        # No field is assigned until every check above has passed, so a
        # rejection here never leaves a partial update -- either every
        # field below is written and committed together, or none are.
        group.academic_term_id = academic_term_id
        group.course_id = course_id
        group.name = name
        group.code = code
        group.capacity = capacity
        group.status = status
        try:
            db.session.commit()
        except IntegrityError:
            # Do not assume this is specifically a duplicate name/code --
            # the staleness and identity/capacity checks above already
            # cover the conditions this route can identify with
            # confidence, so anything that still reaches the database's
            # own constraint here is reported generically rather than
            # guessed at. No raw SQL, parameters, or driver text ever
            # reaches the user.
            flash(
                "This group could not be saved. It may conflict with another group's name or "
                "code, or it may have just been changed by someone else. Please reload and try "
                "again.",
                "danger",
            )
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)
        flash(f"Group '{group.name}' updated.", "success")
        return redirect(url_for("admin.groups_list"))

    # GET, or a POST whose token passed both no-mutation checks above but
    # failed ordinary WTForms field validation: the submitted token (for
    # POST) or a freshly generated one bound to the current preview (for
    # GET) is re-embedded unchanged -- never regenerated here, for the
    # same reason `_redirect_stale_group_edit` and
    # `_render_group_edit_validation_failure` never regenerate one either.
    edit_snapshot_token = (
        submitted_snapshot_token
        if request.method == "POST"
        else _make_group_edit_snapshot_token(preview_group)
    )
    return render_template(
        "admin/groups/form.html",
        form=form,
        courses=_course_choices(),
        group=preview_group,
        identity_locked=identity_locked,
        edit_snapshot_token=edit_snapshot_token,
    )


@admin_bp.post("/groups/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_toggle_status(public_id):
    group = _lock_group_or_404(public_id)
    group.status = (
        AcademicStatus.ARCHIVED.value
        if group.status == AcademicStatus.ACTIVE.value
        else AcademicStatus.ACTIVE.value
    )
    db.session.commit()
    flash(f"Group '{group.name}' is now {group.status}.", "success")
    return redirect(url_for("admin.groups_list"))
