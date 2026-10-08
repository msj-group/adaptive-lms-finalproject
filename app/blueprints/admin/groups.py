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
    Enrollment,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.assignment_queries import group_has_assignment_history
from app.services.grade_queries import group_has_gradebook_history
from app.services.group_memberships import (
    active_student_enrollment_count,
    active_student_enrollment_rows,
    conflicting_active_enrollment,
    eligible_active_teacher_count,
    group_has_membership_history,
    teacher_assignment_rows,
)
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.quiz_queries import group_has_quiz_history
from app.services.schedule_queries import group_has_schedule_history
from app.services.unit_queries import group_has_unit_history
from app.services.schedule_occurrences import to_app_local, utc_reference_now


def _group_identity_frozen(group_id):
    """A Group's academic identity (academic_term_id / course_id) is
    frozen once it has ANY of:

    - Enrollment or GroupTeacherAssignment history
      (`group_has_membership_history`);
    - a Schedule row, active or archived (`group_has_schedule_history`, M08);
    - a Unit row, active or archived (`group_has_unit_history`, M10);
    - an Assignment row, draft or published
      (`group_has_assignment_history`, Phase 4 / M01);
    - a Quiz row -- every Quiz is a draft in Phase 4 / M04A, and an
      **empty** one still counts (`group_has_quiz_history`);
    - a GradeCategory row, and therefore any GradeItem or GradeRecord
      hanging off one -- an **empty** category still counts
      (`group_has_gradebook_history`, Phase 4 / M08).

    Every one of those rows was authored against this Group's Term/Course,
    so retargeting the Group afterwards would silently reinterpret it.
    An Assignment's publication status is deliberately irrelevant for the
    same reason a draft Unit still freezes identity: the work was already
    written for *this* Course in *this* Term. A quiz draft with no
    questions yet is frozen on exactly that reasoning -- its title and
    instructions were already written for this Course in this Term, and
    waiting for questions (which M04A does not have at all) would leave
    every M04A draft unprotected. A gradebook category freezes identity on
    the same principle and for a sharper reason still: its weight is part
    of how already-released grades were calculated, and a GradeItem's
    captured roster is a statement about who was enrolled in *this* Group
    under *this* Course in *this* Term. Retargeting afterwards would
    silently reinterpret somebody's released result.

    This is an identity freeze only. It deliberately adds **no** new
    archive blocker: a Group with Assignments, Quizzes or a gradebook can
    still be archived, and a Group or ancestor lifecycle change never
    cascades into an Assignment, a Quiz or a grade.
    Same-Term/same-Course resubmissions and non-identity edits stay
    allowed -- that exemption lives in `_group_identity_change_error`.
    """
    return (
        group_has_membership_history(group_id)
        or group_has_schedule_history(group_id)
        or group_has_unit_history(group_id)
        or group_has_assignment_history(group_id)
        or group_has_quiz_history(group_id)
        or group_has_gradebook_history(group_id)
    )


def _course_choices():
    return (
        Course.query.options(joinedload(Course.level))
        .join(Level)
        .order_by(Level.display_order, Course.display_order)
        .all()
    )


def _group_form_course_choices(current_course_id=None):
    """The Courses offered in the Group create/edit form (Part M07C3):
    active Courses under active Levels only -- plus, for an edit, the
    Group's own current Course even if it or its Level is archived, so a
    legacy inconsistent Group can still have its metadata corrected
    (submitting its unchanged current course). Server-side guards remain
    authoritative regardless of what this list contains.
    """
    return [
        course
        for course in _course_choices()
        if (
            course.status == AcademicStatus.ACTIVE.value
            and course.level.status == AcademicStatus.ACTIVE.value
        )
        or course.id == current_course_id
    ]


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
        # Capture only the plain scalar values the protected transaction
        # needs -- the request's raw form data must not be trusted for
        # the actual decision past this point. `status` is deliberately
        # not read from the request: every new Group starts `active`,
        # decided server-side below (Part M07C2). A forged `status` field
        # in the POST body has no effect.
        academic_term_id = form.academic_term_id.data
        course_id = form.course_id.data
        name = form.name.data.strip()
        code = normalize_optional_text(form.code.data)
        capacity = form.capacity.data

        # Non-locking preview -- only to discover the submitted Course's
        # Level id so it can be locked in hierarchy order. Never trusted
        # for the decision.
        preview_course = db.session.get(Course, course_id)
        level_id = preview_course.level_id if preview_course is not None else None

        # Part M07C3 -- parent-first creation. One deliberate reset, then
        # AcademicTerm -> Level -> Course locked in that fixed order (see
        # app/services/academic_hierarchy_transactions.py), before the new
        # Group referencing them is created. This serializes the creation
        # against a concurrent archive of any of the three ancestors (each
        # of those toggle routes locks the same row) and against a
        # concurrent Course-level move.
        hierarchy = lock_academic_hierarchy(
            term_ids=[academic_term_id], level_ids=[level_id], course_ids=[course_id]
        )
        error_field, error_message = _group_create_ancestor_error(
            hierarchy, academic_term_id, level_id, course_id
        )
        if error_message is not None:
            db.session.rollback()
            getattr(form, error_field).errors.append(error_message)
            return render_template(
                "admin/groups/form.html",
                form=form,
                courses=_group_form_course_choices(),
                group=None,
            )

        from app.services.study_start import study_start_error
        start_error = study_start_error(hierarchy.term(academic_term_id), form.study_starts_at_utc)
        if start_error:
            db.session.rollback()
            form.study_starts_at.errors.append(start_error)
            return render_template("admin/groups/form.html",form=form,courses=_group_form_course_choices(),group=None)
        group = Group(
            academic_term_id=academic_term_id,
            course_id=course_id,
            name=name,
            code=code,
            capacity=capacity,
            study_starts_at=form.study_starts_at_utc,
            status=AcademicStatus.ACTIVE.value,
        )
        db.session.add(group)
        db.session.commit()
        flash(f"Group '{group.name}' created.", "success")
        return redirect(url_for("admin.groups_list"))

    return render_template(
        "admin/groups/form.html", form=form, courses=_group_form_course_choices(), group=None
    )


def _group_identity_change_error(current_group, has_history, academic_term_id, course_id):
    """Return an error message if changing Course/AcademicTerm on
    `current_group` to the submitted values is not allowed, else None.

    Academic Term and Course are this Group's academic identity (see
    "Group model" and the Group-centered membership management sections
    in docs/DECISIONS.md). Once any Enrollment or GroupTeacherAssignment
    row -- or, since M08, any Schedule row, or, since M10, any Unit row,
    or, since Phase 4 / M01, any Assignment row, or, since Phase 4 / M04A,
    any Quiz row -- has ever existed for the Group (active, withdrawn/removed/archived, or
    even one referencing a corrupted non-Student/non-Teacher User, all
    equally count as real history), that history was created under this
    specific Course/AcademicTerm combination, and changing either field
    afterward would silently reinterpret it: the same-Course-same-Term
    Enrollment conflict rule (`conflicting_active_enrollment`) reads the
    Group's *current* course_id/academic_term_id, every Schedule effective
    range is validated against the Group's *current* AcademicTerm, and
    every Unit is teaching content authored for this Group's *current*
    Course -- so retargeting the Group would retroactively change what
    those rows mean. Posting the Group's own current values back (no
    actual change) is always allowed, and while no such history exists at
    all, both fields remain freely editable.

    `has_history` is supplied by the caller from `_group_identity_frozen`
    (membership history OR Schedule history OR Unit history OR Assignment
    history OR Quiz history OR, since Phase 4 / M08, gradebook history);
    this function only decides the "unchanged current values" exemption
    and the message.

    Shared by the early, pre-lock friendly check and the authoritative
    post-lock recheck in `group_edit` so the rule cannot drift between
    the two.
    """
    if not has_history:
        return None
    if academic_term_id == current_group.academic_term_id and course_id == current_group.course_id:
        return None
    return (
        "Academic Term and Course cannot be changed once this group has enrollment, "
        "teacher-assignment, schedule, unit, assignment, quiz, or gradebook history. "
        "Create a new group instead and archive this one."
    )


# ----------------------------------------------------------------------
# Part M07C3 -- guarded academic-lifecycle checks for the Group routes.
#
# Every one of these runs only against rows already locked FOR UPDATE by
# `lock_academic_hierarchy` (AcademicTerm -> Level -> Course) plus the
# Group lock -- never against a pre-lock preview. The preview reads that
# discover ancestor ids are only used to decide *which* rows to lock.
# ----------------------------------------------------------------------


def _join_human(items):
    """['a'] -> 'a'; ['a', 'b'] -> 'a and b'; ['a', 'b', 'c'] -> 'a, b and c'."""
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _archived_ancestor_labels(term, level, course):
    """Which of the three locked ancestor rows are missing or archived,
    as human labels in hierarchy order."""
    return [
        label
        for label, row in (("academic term", term), ("level", level), ("course", course))
        if row is None or row.status != AcademicStatus.ACTIVE.value
    ]


def _group_create_ancestor_error(hierarchy, term_id, level_id, course_id):
    """`(form_field_name, message)` if a new Group may not be created
    under the locked (term, level, course), else `(None, None)`. A Group
    is created only when all three ancestors exist and are active."""
    term = hierarchy.term(term_id)
    course = hierarchy.course(course_id)
    level = hierarchy.level(level_id) if level_id is not None else None
    if course is None:
        return ("course_id", "Selected course no longer exists. Please choose another.")
    if level is None or course.level_id != level.id:
        return ("course_id", "The selected course changed while saving. Please reload and try again.")
    if term is None:
        return ("academic_term_id", "Selected academic term no longer exists. Please choose another.")
    archived = _archived_ancestor_labels(term, level, course)
    if archived:
        verb = "is" if len(archived) == 1 else "are"
        return (
            "course_id",
            "A group can only be created while its academic term, course, and level are all "
            f"active. The selected {_join_human(archived)} {verb} archived.",
        )
    return (None, None)


def _group_retarget_ancestor_error(hierarchy, term_id, course_id, target_level_id):
    """Message if a Group may not be *retargeted* to the locked (term,
    course, its level), else None. Retargeting to an archived academic
    term, archived course, or a course under an archived level is
    rejected. Metadata-only edits (unchanged current parent) never reach
    this check."""
    term = hierarchy.term(term_id)
    course = hierarchy.course(course_id)
    level = hierarchy.level(target_level_id) if target_level_id is not None else None
    archived = _archived_ancestor_labels(term, level, course)
    if archived:
        verb = "is" if len(archived) == 1 else "are"
        return (
            "A group cannot be moved to an archived academic term, course, or level. The chosen "
            f"{_join_human(archived)} {verb} archived."
        )
    return None


def _group_reactivation_roster_error(group):
    """Part M07C3 roster/capacity/teacher/conflict guard for reactivating
    an archived Group. Locks every relevant User row (ascending numeric
    id), then every relevant Enrollment and GroupTeacherAssignment row
    (ascending numeric id) -- the tail of the global lock order -- then
    runs each check against that locked, current data. Returns an
    Administrator-facing message on the first failure, else None.

    No roster row is ever modified here: reactivation keeps the archived
    Group's closure roster exactly as it stands. The caller already holds
    the AcademicTerm/Level/Course/Group locks.
    """
    enrollment_rows = db.session.query(Enrollment.id, Enrollment.student_id).filter_by(group_id=group.id, status="active").order_by(Enrollment.id).with_for_update().all()
    assignment_rows = db.session.query(GroupTeacherAssignment.id, GroupTeacherAssignment.teacher_id).filter_by(group_id=group.id).order_by(GroupTeacherAssignment.id).with_for_update().all()

    for user_id in sorted(
        {student_id for _, student_id in enrollment_rows}
        | {teacher_id for _, teacher_id in assignment_rows}
    ):
        User.query.filter_by(id=user_id).with_for_update().first()
    for enrollment_id, _ in enrollment_rows:
        Enrollment.query.filter_by(id=enrollment_id).with_for_update().first()
    for assignment_id, _ in assignment_rows:
        GroupTeacherAssignment.query.filter_by(id=assignment_id).with_for_update().first()

    from app.services.schedule_resources import group_reactivation_schedule_error
    resource_error = group_reactivation_schedule_error(group)
    if resource_error is not None:
        return resource_error
    enrollment_rows = [(episode_id, student_id) for episode_id, student_id in enrollment_rows
                       if db.session.get(User, student_id).role == UserRole.STUDENT.value]

    active_count = active_student_enrollment_count(group.id)
    if active_count == 0:
        # Zero active Student enrollments -- capacity is trivially
        # sufficient and no eligible Teacher is required.
        return None

    if active_count > group.capacity:
        return (
            f"This group cannot be reactivated: it has {active_count} active students but its "
            f"capacity is {group.capacity}. Withdraw students or raise the capacity first."
        )
    if eligible_active_teacher_count(group.id) == 0:
        return (
            "This group cannot be reactivated: it has active students but no eligible active "
            "teacher assignment. Assign an eligible active teacher first."
        )
    for _, student_id in enrollment_rows:
        conflict = conflicting_active_enrollment(student_id, group.id)
        if conflict is not None:
            return (
                f"This group cannot be reactivated: a student is already actively enrolled in "
                f"group '{conflict.group.name}' for the same course and academic term. Resolve "
                f"that conflict first."
            )
    return None


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
#
# The snapshot covers exactly the SIX fields `group_edit` can write
# (`public_id` binds the token to one Group; it is never written). It
# deliberately excludes `status`: since Part M07C2, `group_edit` neither
# reads nor writes status -- `group_toggle_status` solely owns Group
# lifecycle transitions -- so a status-only toggle after this form was
# opened must NOT make it stale, and this edit can never overwrite the
# toggled status. Removing status from the snapshot is only safe because
# edit no longer touches it; locking and stale-form protection still
# solve different problems for the six fields that remain.
# ----------------------------------------------------------------------

_GROUP_EDIT_SNAPSHOT_SALT = "admin.group-edit-snapshot.v1"
_GROUP_EDIT_SNAPSHOT_FIELDS = (
    "public_id",
    "academic_term_id",
    "course_id",
    "name",
    "code",
    "capacity",
    "study_starts_at",
)


def _group_snapshot_value(group, field):
    value = getattr(group, field)
    return value.isoformat() if field == "study_starts_at" and value is not None else value


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
    payload = {field: _group_snapshot_value(group, field) for field in _GROUP_EDIT_SNAPSHOT_FIELDS}
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
    rejected rather than silently overwriting whatever changed since.

    A completed Group *status toggle* is deliberately NOT one of those
    cases: `status` is not among the six compared fields (see
    `_GROUP_EDIT_SNAPSHOT_FIELDS`), because `group_edit` no longer reads
    or writes status. An edit form opened before a toggle therefore stays
    valid as long as its six editable fields still match.
    """
    if snapshot is None:
        return True
    if snapshot["public_id"] != group_public_id:
        return True
    return any(snapshot[field] != _group_snapshot_value(locked_group, field) for field in _GROUP_EDIT_SNAPSHOT_FIELDS)


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


def _lock_group_in_open_transaction_or_404(public_id):
    """Thin, route-local 404 wrapper around
    `lock_group_in_open_transaction` -- the Group lock that follows the
    `lock_academic_hierarchy` ancestor locks inside `group_edit` and
    `group_toggle_status`, deliberately without a second transaction
    reset (which would release the ancestor locks just acquired).
    """
    group = lock_group_in_open_transaction(public_id)
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
        courses=_group_form_course_choices(display_group.course_id),
        group=display_group,
        identity_locked=_group_identity_frozen(display_group.id),
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
    identity_locked = _group_identity_frozen(preview_group.id)

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

    form = GroupForm(
        obj=preview_group,
        group_id=preview_group.id,
        current_academic_term_id=preview_group.academic_term_id,
        current_course_id=preview_group.course_id,
    )
    if request.method == "GET" and preview_group.study_starts_at is not None:
        form.study_starts_at.data = to_app_local(current_app.config["APP_TIMEZONE"], preview_group.study_starts_at)

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
        # about to be ended. `status` is intentionally absent: this route
        # never writes Group status (Part M07C2 -- `group_toggle_status`
        # solely owns lifecycle transitions), so a forged `status` field
        # in the POST body has no effect here.
        academic_term_id = form.academic_term_id.data
        course_id = form.course_id.data
        name = form.name.data.strip()
        code = normalize_optional_text(form.code.data)
        capacity = form.capacity.data

        # Non-locking previews -- only to discover which ancestor rows to
        # lock. The Group's *current* term/course/level come from
        # `preview_group`; the submitted *target* course's level from a
        # cheap `get`. Neither is trusted for the decision.
        target_course_preview = db.session.get(Course, course_id)
        target_level_id = (
            target_course_preview.level_id if target_course_preview is not None else None
        )

        # Part M07C3 -- one deliberate transaction reset, then the union
        # of the Group's current and submitted-target AcademicTerm, Level
        # and Course rows, each set deduplicated and locked in ascending
        # id order (AcademicTerm -> Level -> Course), then the Group row
        # with no further reset. This fixed order is shared by
        # `group_toggle_status` and every Course/Level/Term toggle, so a
        # concurrent archive of any ancestor, a Course-level move, or a
        # concurrent Group edit/toggle/membership change all serialize
        # against this edit.
        hierarchy = lock_academic_hierarchy(
            term_ids=[preview_group.academic_term_id, academic_term_id],
            level_ids=[preview_group.course.level_id, target_level_id],
            course_ids=[preview_group.course_id, course_id],
        )
        group = _lock_group_in_open_transaction_or_404(public_id)

        # Every critical rule is rechecked here against the freshly
        # locked Group and current, just-queried relationship/count data
        # -- nothing from the pre-lock preview or form validation above
        # is trusted as final. Staleness first, repeating the same check
        # already performed above against the preview read, now against
        # the locked, current one -- this is what closes the window
        # between that earlier preview and this lock. `academic_term_id`
        # and `course_id` are snapshot fields, so a concurrent retarget
        # is caught here as stale (discard everything, PRG).
        snapshot = _load_group_edit_snapshot(submitted_snapshot_token)
        if _group_edit_is_stale(snapshot, public_id, group):
            # No rollback here: `_redirect_stale_group_edit` is the single
            # owner of stale-rejection rollback and releases the write
            # lock itself before redirecting.
            return _redirect_stale_group_edit(public_id)

        target_course = hierarchy.course(course_id)
        if target_course is None:
            form.course_id.errors.append("Selected course no longer exists. Please choose another.")
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)
        if target_level_id is None or target_course.level_id != target_level_id:
            # A concurrent Course level-move changed the target course's
            # Level between our preview and our locks -- we are holding
            # the wrong Level. Treat it exactly like a stale form.
            return _redirect_stale_group_edit(public_id)

        has_history = _group_identity_frozen(group.id)
        error = _group_identity_change_error(group, has_history, academic_term_id, course_id)
        if error is not None:
            form.academic_term_id.errors.append(error)
            form.course_id.errors.append(error)
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)

        # Part M07C3 -- parent-first retargeting. A retarget (a change to
        # the term or course) may only land on active ancestors. A
        # metadata-only edit (unchanged current term *and* course) is
        # exempt, so a legacy Group under an archived parent can still
        # have its name/code/capacity corrected.
        retargeting = (
            academic_term_id != group.academic_term_id or course_id != group.course_id
        )
        if retargeting:
            retarget_error = _group_retarget_ancestor_error(
                hierarchy, academic_term_id, course_id, target_course.level_id
            )
            if retarget_error is not None:
                form.academic_term_id.errors.append(retarget_error)
                form.course_id.errors.append(retarget_error)
                return _render_group_edit_validation_failure(
                    form, public_id, submitted_snapshot_token
                )

        active_count = active_student_enrollment_count(group.id)
        if capacity < active_count:
            form.capacity.errors.append(
                f"Capacity cannot be lower than the current active student count ({active_count})."
            )
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)

        # No field is assigned until every check above has passed, so a
        # rejection here never leaves a partial update -- either every
        # field below is written and committed together, or none are.
        # `group.status` is never assigned here (Part M07C2): a status
        # toggle that committed while this form was open is left intact.
        from app.services.schedule_resources import lock_group_rooms
        room_error = lock_group_rooms(group.id, capacity)
        if room_error:
            form.capacity.errors.append(room_error)
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)
        if (group.study_starts_at is not None and group.study_starts_at <= utc_reference_now()
                and form.study_starts_at_utc > group.study_starts_at):
            form.study_starts_at.errors.append("Study has started. Its recorded start cannot be moved later.")
            return _render_group_edit_validation_failure(form, public_id, submitted_snapshot_token)
        group.academic_term_id = academic_term_id
        group.course_id = course_id
        group.name = name
        group.code = code
        group.capacity = capacity
        from app.services.study_start import study_start_error
        start_error = study_start_error(hierarchy.term(academic_term_id),form.study_starts_at_utc,group.id)
        if start_error:
            db.session.rollback()
            form.study_starts_at.errors.append(start_error)
            return render_template("admin/groups/form.html",form=form,courses=_group_form_course_choices(),group=group)
        group.study_starts_at = form.study_starts_at_utc
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
        courses=_group_form_course_choices(preview_group.course_id),
        group=preview_group,
        identity_locked=identity_locked,
        edit_snapshot_token=edit_snapshot_token,
    )


@admin_bp.post("/groups/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_toggle_status(public_id):
    # Non-locking preview -- discovers this Group's current ancestor ids
    # (and 404s if the Group is gone). Not trusted for any decision.
    preview_group = _load_group_for_display(public_id)
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    # Part M07C3 -- one deliberate reset, then AcademicTerm -> Level ->
    # Course -> Group in the fixed global order. `group_edit` and every
    # ancestor toggle use the same order, so this route serializes
    # against a concurrent ancestor archive, Course-level move, Group
    # edit, and (for reactivation) every membership mutation on this
    # Group.
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = _lock_group_in_open_transaction_or_404(public_id)

    course = hierarchy.course(course_id)
    if (
        course is None
        or group.course_id != course.id
        or group.academic_term_id != term_id
        or course.level_id != level_id
    ):
        # The Group's ancestor set changed between our preview and our
        # locks -- we hold the wrong rows. Bail safely.
        db.session.rollback()
        flash(
            "This group was changed by someone else. Please reload the groups list and try again.",
            "danger",
        )
        return redirect(url_for("admin.groups_list"))

    if group.status == AcademicStatus.ACTIVE.value:
        # Archiving is always allowed -- no descendant guard. Every
        # Enrollment and GroupTeacherAssignment row is left exactly as
        # it stands; the roster becomes a frozen closure record and
        # membership management for the Group becomes read-only.
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        flash(f"Group '{group.name}' is now archived.", "success")
        return redirect(url_for("admin.groups_list"))

    # Reactivation -- parent-first, then the full roster guard.
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    archived = _archived_ancestor_labels(term, level, course)
    if archived:
        db.session.rollback()
        verb = "is" if len(archived) == 1 else "are"
        flash(
            f"This group cannot be reactivated while its {_join_human(archived)} {verb} archived. "
            "Reactivate the parent first.",
            "danger",
        )
        return redirect(url_for("admin.groups_list"))

    roster_error = _group_reactivation_roster_error(group)
    if roster_error is not None:
        db.session.rollback()
        flash(roster_error, "danger")
        return redirect(url_for("admin.groups_list"))

    group.status = AcademicStatus.ACTIVE.value
    db.session.commit()
    flash(f"Group '{group.name}' is now active.", "success")
    return redirect(url_for("admin.groups_list"))
