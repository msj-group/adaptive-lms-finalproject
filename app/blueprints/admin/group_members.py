from app.services.schedule_resources import teacher_assignment_conflict
from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import GroupEnrollmentForm, GroupTeacherAssignmentForm
from app.extensions import db
from app.models import (
    AcademicStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.group_memberships import active_student_enrollment_count, eligible_active_teacher_count
from app.services.group_transactions import lock_group_for_write
from app.services.notification_delivery import (
    notify_teacher_assignment_activated,
    notify_teacher_assignment_removed,
)


def _get_group_locked_or_404(group_public_id):
    """Flask-aware wrapper around the shared, Flask-independent
    `lock_group_for_write` (see its docstring in
    `app/services/group_transactions.py` for the full deliberate-rollback
    and lock-order rationale) -- 404s here belong at the route/Blueprint
    layer, not in the shared service, so every membership mutation route
    below still gets its 404 for free by calling this thin wrapper rather
    than the service function directly.

    Every Teacher-assignment and Student-enrollment mutation, plus Group
    edit and Group status toggle (`app/blueprints/admin/groups.py`), lock
    the Group through this same shared primitive, in the same order, so
    concurrent requests touching the same Group serialize on this lock
    instead of racing independently.
    """
    group = lock_group_for_write(group_public_id)
    if group is None:
        abort(404)
    return group


def _get_assignment_for_group_or_404(group, assignment_public_id):
    """Look up a GroupTeacherAssignment by its own public_id, constrained
    to the Group already resolved from the URL -- an assignment public_id
    that is only valid for a *different* Group must 404 here, the same
    nested-IDOR protection used for Student/Teacher lookups elsewhere.

    Deliberately does NOT filter on the referenced User's role, unlike
    `_get_enrollment_for_group_or_404` in enrollments.py. A corrupted
    assignment (referencing a non-Teacher) is still reachable through
    this lookup on purpose -- see the comment above the corrupted-
    assignment handling in group_teacher_remove for why.
    """
    return (
        GroupTeacherAssignment.query.options(joinedload(GroupTeacherAssignment.teacher))
        .filter_by(public_id=assignment_public_id, group_id=group.id)
        .populate_existing().with_for_update()
        .first_or_404()
    )


def _redirect_to_group_members(group):
    # Fixed destination only -- no caller-supplied return URL is ever
    # accepted. Every Teacher-assignment and Student-enrollment mutation
    # redirects back to this same Group's Manage Members page.
    return redirect(url_for("admin.group_members", group_public_id=group.public_id))


def _flash_form_errors(form):
    for field_errors in form.errors.values():
        for error in field_errors:
            flash(error, "danger")


@admin_bp.get("/groups/<group_public_id>/members")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_members(group_public_id):
    """Read-only Manage Members page: combines what would otherwise be a
    separate "Manage Enrollment" and "View Roster" page into one --
    Teacher assignments and Student Enrollments for this Group, plus the
    forms to assign a Teacher / enroll a Student, and the actions to
    remove/reactivate a Teacher or withdraw/reactivate a Student.

    No row lock is taken here -- this is a read-only view, not a
    mutation, so there is nothing to protect with SELECT ... FOR UPDATE.
    """
    group = (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=group_public_id)
        .first_or_404()
    )

    # Role-integrity: a GroupTeacherAssignment/Enrollment row can
    # technically reference a non-Teacher/non-Student User (the FK
    # cannot prevent that -- see each model's docstring). Joining on
    # User and constraining the role here means a corrupted row is
    # simply never displayed or counted as a valid member, exactly like
    # the listing queries elsewhere in this admin section.
    teacher_assignments = (
        GroupTeacherAssignment.query.join(User, GroupTeacherAssignment.teacher_id == User.id)
        .filter(GroupTeacherAssignment.group_id == group.id, User.role == UserRole.TEACHER.value)
        .options(joinedload(GroupTeacherAssignment.teacher))
        .all()
    )
    teacher_assignments.sort(
        key=lambda a: (a.status != GroupTeacherAssignmentStatus.ACTIVE.value, a.teacher.full_name, a.id)
    )

    enrollments = (
        Enrollment.query.join(User, Enrollment.student_id == User.id)
        .filter(Enrollment.group_id == group.id, User.role == UserRole.STUDENT.value)
        .options(joinedload(Enrollment.student))
        .all()
    )
    enrollments.sort(key=lambda e: (e.status != EnrollmentStatus.ACTIVE.value, e.student.full_name, e.id))

    active_student_count = active_student_enrollment_count(group.id)
    is_active_group = group.status == AcademicStatus.ACTIVE.value

    teacher_form = GroupTeacherAssignmentForm(group=group) if is_active_group else None
    student_form = None  # Registration has its own atomic preview form.

    return render_template(
        "admin/groups/members.html",
        group=group,
        teacher_assignments=teacher_assignments,
        enrollments=enrollments,
        active_student_count=active_student_count,
        eligible_teacher_count=eligible_active_teacher_count(group.id),
        remaining_seats=max(group.capacity - active_student_count, 0),
        teacher_form=teacher_form,
        student_form=student_form,
    )


@admin_bp.post("/groups/<group_public_id>/teachers")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_teacher_assign(group_public_id):
    group = _get_group_locked_or_404(group_public_id)
    # Wait for the teacher before form queries can establish a read snapshot.
    teacher = User.query.filter_by(public_id=request.form.get("teacher_public_id", "")).populate_existing().with_for_update().first()

    if group.status != AcademicStatus.ACTIVE.value:
        flash("This group is archived and cannot be modified.", "danger")
        return _redirect_to_group_members(group)

    form = GroupTeacherAssignmentForm(group=group)
    if not form.validate_on_submit():
        _flash_form_errors(form)
        return _redirect_to_group_members(group)

    # The form already validated this Teacher against an unlocked read.
    # Lock the Teacher row too (Group already locked above -- same
    # Group-then-User order used throughout this module and in
    # enrollments.py) and re-check role/status against that locked,
    # current data before writing, rather than trusting the form's word
    # for it moments earlier.
    teacher = User.query.filter_by(public_id=form.teacher_public_id.data).populate_existing().with_for_update().first()
    if teacher is None or teacher.role != UserRole.TEACHER.value or teacher.status != UserStatus.ACTIVE.value:
        flash("Selected teacher is no longer eligible to be assigned.", "danger")
        return _redirect_to_group_members(group)

    if teacher_assignment_conflict(teacher.id, group.id) is not None:
        db.session.rollback()
        flash("This teacher is already scheduled in another group at the same time.", "danger")
        return _redirect_to_group_members(group)

    assignment = GroupTeacherAssignment(
        group_id=group.id, teacher_id=teacher.id, status=GroupTeacherAssignmentStatus.ACTIVE.value
    )
    db.session.add(assignment)
    try:
        db.session.commit()
    except IntegrityError:
        # The form already re-checks for a duplicate (group, teacher)
        # pair, but that check and this insert are two separate steps --
        # a concurrent request could create the same pair in between them
        # even with the Group row lock (the lock only serializes requests
        # that also take it, and this is the belt-and-braces net for
        # anything that still slips through). The unique constraint is
        # the real guarantee; this only turns the resulting error into a
        # safe, generic message instead of a 500.
        db.session.rollback()
        flash("This teacher is already assigned to this group.", "danger")
        return _redirect_to_group_members(group)

    # M14: the assignment is committed and the response is already
    # decided. Capture plain scalars, build the response, and only then
    # attempt best-effort notification delivery in its own transaction --
    # a notification failure never turns this successful assignment into
    # an error, and no ORM row is touched afterwards.
    teacher_id, group_name = teacher.id, group.name
    flash(f"Teacher '{teacher.full_name}' assigned to '{group.name}'.", "success")
    response = _redirect_to_group_members(group)
    notify_teacher_assignment_activated(teacher_id, group_name)
    return response


@admin_bp.post("/groups/<group_public_id>/teachers/<assignment_public_id>/remove")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_teacher_remove(group_public_id, assignment_public_id):
    group = _get_group_locked_or_404(group_public_id)
    assignment = _get_assignment_for_group_or_404(group, assignment_public_id)

    if group.status != AcademicStatus.ACTIVE.value:
        flash("This group is archived and cannot be modified.", "danger")
        return _redirect_to_group_members(group)

    if assignment.status != GroupTeacherAssignmentStatus.ACTIVE.value:
        flash("This assignment is already removed.", "warning")
        return _redirect_to_group_members(group)

    # A corrupted assignment (referencing a non-Teacher) or a suspended
    # Teacher's assignment is never "eligible" in the first place (see
    # eligible_active_teacher_count), so it never contributes to the
    # last-teacher protection below -- removing one is always allowed,
    # deliberately: blocking removal of an obviously-invalid row would
    # trap an administrator with no way to clean it up, and it never
    # protected any Student's access to begin with.
    #
    # This is a narrow, explicitly approved cleanup exception specific to
    # GroupTeacherAssignment: a corrupted row is still looked up and
    # reachable here (`_get_assignment_for_group_or_404` does not filter
    # on the Teacher's role), so an administrator can mark it removed
    # through this direct protected route. It does NOT mirror Enrollment:
    # a corrupted (non-Student) Enrollment is excluded from every listing
    # and its nested lookup (`_get_enrollment_for_group_or_404` in
    # enrollments.py, which does filter on the Student's role) 404s, so
    # it can never be withdrawn -- or reactivated -- through the
    # application at all.
    is_eligible = (
        assignment.teacher.role == UserRole.TEACHER.value
        and assignment.teacher.status == UserStatus.ACTIVE.value
    )
    if is_eligible and active_student_enrollment_count(group.id) > 0:
        remaining_eligible = eligible_active_teacher_count(group.id, exclude_assignment_id=assignment.id)
        if remaining_eligible == 0:
            flash(
                "Cannot remove the last active teacher while the group has active students. "
                "Assign a replacement teacher first.",
                "danger",
            )
            return _redirect_to_group_members(group)

    assignment.status = GroupTeacherAssignmentStatus.REMOVED.value
    db.session.commit()
    # M14 post-commit delivery -- see group_teacher_assign. A corrupted
    # assignment (referencing a non-Teacher) can legitimately be removed
    # here; the producer's own role/account re-check is what keeps it
    # from producing a notification for a non-Teacher recipient.
    teacher_id, group_name = assignment.teacher_id, group.name
    flash(f"Teacher '{assignment.teacher.full_name}' removed from '{group.name}'.", "success")
    response = _redirect_to_group_members(group)
    notify_teacher_assignment_removed(teacher_id, group_name)
    return response


@admin_bp.post("/groups/<group_public_id>/teachers/<assignment_public_id>/reactivate")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_teacher_reactivate(group_public_id, assignment_public_id):
    group = _get_group_locked_or_404(group_public_id)
    assignment = _get_assignment_for_group_or_404(group, assignment_public_id)

    if group.status != AcademicStatus.ACTIVE.value:
        flash("This group is archived and cannot be modified.", "danger")
        return _redirect_to_group_members(group)

    if assignment.status == GroupTeacherAssignmentStatus.ACTIVE.value:
        flash("This teacher is already actively assigned to this group.", "warning")
        return _redirect_to_group_members(group)

    # Historical/manually-seeded rows can reference a non-Teacher user
    # (the FK cannot prevent that). Reactivation must not assume a row it
    # can look up by public_id is safe to bring back -- it locks the
    # referenced User row too (Group-then-User, same order as everywhere
    # else) and re-verifies role and account status against that locked,
    # current data, exactly like Enrollment reactivation re-verifies the
    # Student side.
    teacher = User.query.filter_by(id=assignment.teacher_id).with_for_update().first()
    if teacher is None or teacher.role != UserRole.TEACHER.value:
        flash("This assignment cannot be reactivated: the referenced account is not a Teacher.", "danger")
        return _redirect_to_group_members(group)

    if teacher.status != UserStatus.ACTIVE.value:
        flash("This assignment cannot be reactivated: the teacher's account is not active.", "danger")
        return _redirect_to_group_members(group)

    if teacher_assignment_conflict(teacher.id, group.id) is not None:
        db.session.rollback()
        flash("This teacher is already scheduled in another group at the same time.", "danger")
        return _redirect_to_group_members(group)

    assignment.status = GroupTeacherAssignmentStatus.ACTIVE.value
    db.session.commit()
    # M14 post-commit delivery -- see group_teacher_assign.
    teacher_id, group_name = teacher.id, group.name
    flash(f"Teacher '{assignment.teacher.full_name}' reactivated for '{group.name}'.", "success")
    response = _redirect_to_group_members(group)
    notify_teacher_assignment_activated(teacher_id, group_name)
    return response
