"""Legacy enrollments URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from flask import redirect, url_for

from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp

from app.models import Enrollment, Group, User, UserRole

from app.security.decorators import roles_required

def _get_group_or_404(group_public_id):
    """Resolve an old bookmark's Group before the current workflow redirect."""
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
    return Enrollment.query.join(User, Enrollment.student_id == User.id).filter(Enrollment.public_id == enrollment_public_id, Enrollment.group_id == group.id, User.role == UserRole.STUDENT.value).options(joinedload(Enrollment.student)).first_or_404()

@admin_bp.post('/groups/<group_public_id>/enrollments')
@roles_required(UserRole.ADMINISTRATOR.value)
def group_enrollment_create(group_public_id):
    return redirect(url_for('admin.enrollment_operation', group_public_id=group_public_id, action='enroll'), code=303)

@admin_bp.post('/groups/<group_public_id>/enrollments/<enrollment_public_id>/withdraw')
@roles_required(UserRole.ADMINISTRATOR.value)
def group_enrollment_withdraw(group_public_id, enrollment_public_id):
    return redirect(url_for('admin.enrollment_operation', group_public_id=group_public_id, action='withdraw', episode=enrollment_public_id), code=303)

@admin_bp.post('/groups/<group_public_id>/enrollments/<enrollment_public_id>/reactivate')
@roles_required(UserRole.ADMINISTRATOR.value)
def group_enrollment_reactivate(group_public_id, enrollment_public_id):
    group = _get_group_or_404(group_public_id)
    episode = _get_enrollment_for_group_or_404(group, enrollment_public_id)
    return redirect(url_for('admin.enrollment_operation', group_public_id=group_public_id, action='enroll', student=episode.student.public_id), code=303)
