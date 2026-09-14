from flask import abort, flash, redirect, url_for
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import GroupEnrollmentForm
from app.blueprints.admin.group_members import _get_group_locked_or_404, _redirect_to_group_members
from app.extensions import db
from app.models import AcademicStatus, Enrollment, EnrollmentStatus, Group, User, UserRole, UserStatus
from app.security.decorators import roles_required
from app.services.group_memberships import (
    active_student_enrollment_count,
    conflicting_active_enrollment,
    eligible_active_teacher_count,
)
from app.services.notification_delivery import (
    notify_enrollment_activated,
    notify_enrollment_withdrawn,
)
from app.services.student_fee_assignment_queries import enrollment_has_assigned_fee_plan

#: Phase 5 / M03. Withdrawal never cancels a fee assignment on its own.
_ASSIGNED_FEE_PLAN_BLOCKS_WITHDRAWAL = (
    "This enrollment has an assigned fee plan. Cancel the fee assignment explicitly from the "
    "enrollment's Fee assignments page before withdrawing the student."
)


def _get_group_or_404(group_public_id):
    """Ordinary, non-locking Group lookup.

    Used only for 404 handling and to scope the pre-validation work that
    has to happen before the protected transaction begins: building a
    form's choices, running its early friendly validation, and (for
    Withdraw/Reactivate) the initial nested Enrollment lookup. The Group
    object this returns is never used to decide a business rule -- every
    mutation route below re-fetches and locks the Group via
    `_get_group_locked_or_404` before deciding anything, and discards
    this one.
    """
    return Group.query.filter_by(public_id=group_public_id).first_or_404()


def _get_enrollment_for_group_or_404(group, enrollment_public_id):
    """Look up an Enrollment by its own public_id, constrained to the
    Group already resolved from the URL AND to rows whose referenced
    User is actually a Student.

    Enrollment.student_id is a plain FK to the shared users table and can
    technically reference any role at the database level (see the
    model's docstring for the role-integrity boundary). An enrollment
    public_id that is only valid for a *different* Group, or that
    references a non-Student (the FK cannot prevent that), 404s here --
    exactly like the compound (public_id, group_id) lookup used for
    GroupTeacherAssignment.
    """
    return (
        Enrollment.query.join(User, Enrollment.student_id == User.id)
        .filter(
            Enrollment.public_id == enrollment_public_id,
            Enrollment.group_id == group.id,
            User.role == UserRole.STUDENT.value,
        )
        .options(joinedload(Enrollment.student))
        .first_or_404()
    )


def _lock_student_and_enrollment_for_group(group, enrollment_id, student_id):
    """The Group is already locked by the caller (via
    `_get_group_locked_or_404`, which also performed the transaction-
    boundary reset -- see its docstring for the full rationale), and this
    is called as the very next statement after that lock, before any
    ordinary SELECT runs in the fresh transaction. Locks Student, then
    the Enrollment row itself, in that fixed order -- `SELECT ... FOR
    UPDATE` on the Enrollment row is what guarantees a current read on
    MySQL, unlike `db.session.refresh()` against a possibly already-open
    transaction.

    Verifies the locked Enrollment still belongs to the captured Group
    and Student (defensive: no route in this app can currently change
    those on an existing row, but nothing here assumes that stays true)
    and that the Student is still Student-role, aborting 404 on any
    mismatch rather than proceeding. Returns (student, enrollment).

    `enrollment_id` and `student_id` must be plain scalar values captured
    from an earlier, ordinary (unlocked) lookup -- never ORM objects
    reused across the transaction reset in `_get_group_locked_or_404`.

    Shared by group_enrollment_withdraw and group_enrollment_reactivate.
    """
    student = User.query.filter_by(id=student_id).with_for_update().first()
    enrollment = (
        Enrollment.query.options(joinedload(Enrollment.student))
        .filter_by(id=enrollment_id)
        .with_for_update()
        .first()
    )
    if student is None or enrollment is None:
        abort(404)
    if enrollment.group_id != group.id or enrollment.student_id != student.id:
        abort(404)
    if student.role != UserRole.STUDENT.value:
        abort(404)
    return student, enrollment


def _create_or_reactivate_precondition_error(student, group):
    """Return an error message if `student` cannot receive a new/
    reactivated ACTIVE Enrollment in `group` right now, else None.

    Must only be called with a `student` and `group` that were already
    locked (via `_get_group_locked_or_404` and an immediately-following
    Student lock) in the current fresh transaction -- the checks below
    issue ordinary SELECT queries (teacher/capacity counts) that are only
    safe to trust once that lock order has already been established.

    Shared by group_enrollment_create and group_enrollment_reactivate so
    the same rules apply in both directions; deliberately does not check
    for an existing (student, group) pair or a conflicting Enrollment in
    another Group, since those two callers need different follow-up
    behaviour for those specific cases.
    """
    if student.role != UserRole.STUDENT.value:
        return "Selected student does not exist."
    if student.status != UserStatus.ACTIVE.value:
        return "Selected student's account is not active."
    if group.status != AcademicStatus.ACTIVE.value:
        return "Selected group is not active."
    if eligible_active_teacher_count(group.id) == 0:
        return "This group must have at least one active teacher before students can be enrolled."
    if active_student_enrollment_count(group.id) >= group.capacity:
        return "This group is at full capacity."
    return None


@admin_bp.post("/groups/<group_public_id>/enrollments")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_enrollment_create(group_public_id):
    # Ordinary, non-locking Group lookup -- only for 404 handling, early
    # friendly validation, and building the form's Student choices. This
    # read (and everything the form's own validators query) runs against
    # whatever snapshot already existed when the request arrived; nothing
    # here is trusted for a business decision.
    preview_group = _get_group_or_404(group_public_id)

    if preview_group.status != AcademicStatus.ACTIVE.value:
        flash("This group is archived and cannot be modified.", "danger")
        return _redirect_to_group_members(preview_group)

    form = GroupEnrollmentForm(group=preview_group)
    if not form.validate_on_submit():
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")
        return _redirect_to_group_members(preview_group)

    # Capture only the plain scalar value the protected transaction needs
    # -- `preview_group` and anything the form loaded must not be reused
    # past this point; they belong to the transaction/snapshot that is
    # about to be ended.
    student_public_id = form.student_public_id.data

    # Deliberate transaction-boundary reset (see _get_group_locked_or_404
    # docstring for the full MySQL/InnoDB REPEATABLE READ rationale):
    # ends the read-only snapshot the lookup and form validation above
    # may have established, and re-fetches + locks the *current* Group
    # row in a fresh transaction.
    group = _get_group_locked_or_404(group_public_id)

    # Lock the Student row as the very next statement -- no ordinary
    # SELECT runs between the fresh Group lock and this Student lock.
    # This is what guarantees the first consistent-read snapshot in this
    # fresh transaction is only established after any competing
    # transaction that holds this same Student's row lock (e.g. another
    # group_enrollment_create for a *different* Group, same Student) has
    # already committed or rolled back -- so every ordinary SELECT below,
    # including the cross-Group conflict check, sees that transaction's
    # result rather than stale data.
    student = User.query.filter_by(public_id=student_public_id).with_for_update().first()

    # Every critical rule is rechecked here against the newly locked
    # Group and Student -- nothing from the pre-lock lookup or form
    # validation above is trusted as final.
    if group.status != AcademicStatus.ACTIVE.value:
        flash("This group is archived and cannot be modified.", "danger")
        return _redirect_to_group_members(group)

    if student is None:
        flash("Selected student no longer exists.", "danger")
        return _redirect_to_group_members(group)

    error = _create_or_reactivate_precondition_error(student, group)
    if error is not None:
        flash(error, "danger")
        return _redirect_to_group_members(group)

    existing = Enrollment.query.filter_by(student_id=student.id, group_id=group.id).first()
    if existing is not None:
        if existing.status == EnrollmentStatus.ACTIVE.value:
            flash("Student is already enrolled in this group.", "danger")
        else:
            flash(
                "A withdrawn enrollment already exists for this student and group. "
                "Reactivate it instead of creating a new one.",
                "danger",
            )
        return _redirect_to_group_members(group)

    # Checked here, after the Student lock -- see the comment above the
    # Student lock for why that ordering is what makes this read current
    # rather than a possibly-stale snapshot.
    conflict = conflicting_active_enrollment(student.id, group.id)
    if conflict is not None:
        flash(
            f"Student is already actively enrolled in Group '{conflict.group.name}' for the same "
            "Course and Academic Term. Withdraw that enrollment first.",
            "danger",
        )
        return _redirect_to_group_members(group)

    # The form only ever offers a Student choice -- status is forced to
    # ACTIVE here regardless of anything else submitted in the request
    # body, and public_id/created_at/updated_at come only from the
    # model's own server-side defaults, so there is no field through
    # which a client can mass-assign them.
    enrollment = Enrollment(student_id=student.id, group_id=group.id, status=EnrollmentStatus.ACTIVE.value)
    db.session.add(enrollment)
    try:
        db.session.commit()
    except IntegrityError:
        # Belt-and-braces net: the duplicate-pair check above and this
        # insert are two separate steps, so even with the Group/Student
        # locks a concurrent request that does not itself take those
        # locks could theoretically still race here. The database's
        # unique constraint is the real guarantee; this only turns the
        # resulting error into a safe, generic message instead of a 500.
        db.session.rollback()
        flash("Student is already enrolled in this group.", "danger")
        return _redirect_to_group_members(group)

    # M14: the domain mutation is committed and its response is already
    # decided. Capture plain scalars, build the response, and only then
    # attempt best-effort notification delivery in its own transaction --
    # so a notification failure can never turn this successful enrollment
    # into an error, and no ORM row is touched afterwards.
    student_id, group_name = student.id, group.name
    flash(f"Student '{student.full_name}' enrolled in '{group.name}'.", "success")
    response = _redirect_to_group_members(group)
    notify_enrollment_activated(student_id, group_name)
    return response


@admin_bp.post("/groups/<group_public_id>/enrollments/<enrollment_public_id>/withdraw")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_enrollment_withdraw(group_public_id, enrollment_public_id):
    # Ordinary, non-locking nested lookup: 404s if the Group doesn't
    # exist, or if the Enrollment doesn't exist / doesn't belong to this
    # Group / doesn't reference a Student. Runs before the protected
    # transaction -- nothing here is trusted for a business decision.
    preview_group = _get_group_or_404(group_public_id)
    initial = _get_enrollment_for_group_or_404(preview_group, enrollment_public_id)

    # Capture plain scalar values only -- `preview_group` and `initial`
    # must not be reused past this point; they belong to the transaction/
    # snapshot that is about to be ended.
    enrollment_id = initial.id
    student_id = initial.student_id

    # Deliberate transaction-boundary reset (see _get_group_locked_or_404
    # docstring): ends the earlier snapshot and locks the *current* Group
    # row in a fresh transaction.
    group = _get_group_locked_or_404(group_public_id)

    # Locks Student then Enrollment as the very next statements -- no
    # ordinary SELECT runs between the fresh Group lock and the Student
    # lock. See `_lock_student_and_enrollment_for_group` and the matching
    # comment in group_enrollment_create for the full rationale.
    student, enrollment = _lock_student_and_enrollment_for_group(group, enrollment_id, student_id)

    if group.status != AcademicStatus.ACTIVE.value:
        flash("This group is archived. Reactivate the group before changing its membership.", "danger")
        return _redirect_to_group_members(group)

    if enrollment.status != EnrollmentStatus.ACTIVE.value:
        flash("This enrollment is already withdrawn.", "warning")
        return _redirect_to_group_members(group)

    # Phase 5 / M03: an assigned fee plan must be cancelled explicitly
    # first -- withdrawal never cancels, deletes or changes one itself. Read
    # only after the Group -> Student -> Enrollment locks above: assignment
    # and cancellation take the same Enrollment lock before writing, so this
    # answer is current rather than a stale snapshot.
    if enrollment_has_assigned_fee_plan(enrollment.id):
        flash(_ASSIGNED_FEE_PLAN_BLOCKS_WITHDRAWAL, "danger")
        return _redirect_to_group_members(group)

    enrollment.status = EnrollmentStatus.WITHDRAWN.value
    db.session.commit()
    # M14 post-commit delivery -- see group_enrollment_create.
    student_id, group_name = student.id, group.name
    flash(f"Enrollment for '{enrollment.student.full_name}' withdrawn.", "success")
    response = _redirect_to_group_members(group)
    notify_enrollment_withdrawn(student_id, group_name)
    return response


@admin_bp.post("/groups/<group_public_id>/enrollments/<enrollment_public_id>/reactivate")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_enrollment_reactivate(group_public_id, enrollment_public_id):
    # Ordinary, non-locking nested lookup -- see group_enrollment_withdraw
    # above for the identical rationale.
    preview_group = _get_group_or_404(group_public_id)
    initial = _get_enrollment_for_group_or_404(preview_group, enrollment_public_id)

    enrollment_id = initial.id
    student_id = initial.student_id

    # Deliberate transaction-boundary reset, then lock Student and
    # Enrollment as the very next statements -- same rationale as
    # group_enrollment_withdraw and group_enrollment_create.
    group = _get_group_locked_or_404(group_public_id)
    student, enrollment = _lock_student_and_enrollment_for_group(group, enrollment_id, student_id)

    if enrollment.status == EnrollmentStatus.ACTIVE.value:
        flash("This enrollment is already active.", "warning")
        return _redirect_to_group_members(group)

    error = _create_or_reactivate_precondition_error(student, group)
    if error is not None:
        flash(f"This enrollment cannot be reactivated. {error}", "danger")
        return _redirect_to_group_members(group)

    # Checked here, only after Group, Student, and Enrollment are all
    # locked in the fresh transaction -- so this read is current rather
    # than a possibly-stale snapshot from before the reset.
    conflict = conflicting_active_enrollment(student.id, group.id)
    if conflict is not None:
        flash(
            f"This enrollment cannot be reactivated: student is already actively enrolled in group "
            f"'{conflict.group.name}' for the same course and academic term. Withdraw that enrollment first.",
            "danger",
        )
        return _redirect_to_group_members(group)

    enrollment.status = EnrollmentStatus.ACTIVE.value
    db.session.commit()
    # M14 post-commit delivery -- see group_enrollment_create.
    student_id, group_name = student.id, group.name
    flash(f"Enrollment for '{enrollment.student.full_name}' reactivated.", "success")
    response = _redirect_to_group_members(group)
    notify_enrollment_activated(student_id, group_name)
    return response
