"""Locked account mutations and domain history, separate from auth invalidation."""
from app.extensions import db
from app.models import AccountRevision, Enrollment, Group, GroupTeacherAssignment, User
from app.models.submission_feedback import whole_second_utc
from app.services.group_memberships import active_student_enrollment_count, eligible_active_teacher_count


class AccountConflict(ValueError):
    pass


def account_snapshot(account):
    return {"public_id": account.public_id, "role": account.role,
            "full_name": account.full_name, "email": account.email,
            "status": account.status, "version": account.version,
            "auth_version": account.auth_version}


def _teacher_group_ids(user_id):
    return {row[0] for row in db.session.query(GroupTeacherAssignment.group_id).filter(
        GroupTeacherAssignment.teacher_id == user_id, GroupTeacherAssignment.status == "active"
    ).all()}


def _lock_account(public_id, role):
    """Group-before-User matches membership writers; rediscover added assignments."""
    preview = User.query.filter_by(public_id=public_id, role=role).first()
    if preview is None:
        raise AccountConflict("This account is no longer available.")
    user_id = preview.id
    group_ids = _teacher_group_ids(user_id) if role == "teacher" else set()
    for _attempt in range(3):
        db.session.rollback()
        groups = Group.query.filter(Group.id.in_(sorted(group_ids))).order_by(Group.id).with_for_update().all() if group_ids else []
        account = User.query.filter_by(id=user_id, role=role).populate_existing().with_for_update().first()
        if account is None:
            raise AccountConflict("This account is no longer available.")
        # No ordinary SELECT has established a consistent snapshot before User locks.
        current_ids = _teacher_group_ids(user_id) if role == "teacher" else set()
        if current_ids <= {group.id for group in groups}:
            return account, groups
        group_ids |= current_ids
    db.session.rollback()
    raise AccountConflict("Teaching assignments changed while saving. Please reload and try again.")


def change_account(public_id, role, actor_id, expected, action, *, full_name=None, email=None, password_hash=None):
    account, groups = _lock_account(public_id, role)
    from app.services.actor_authorization import require_current_actor
    require_current_actor(actor_id, "administrator")
    if expected != account_snapshot(account):
        raise AccountConflict("This account changed since this form was opened. Review its current values and try again.")
    before = account_snapshot(account)
    if action == "profile":
        account.full_name = full_name
        if account.email != email:
            account.email = email
            account.bump_auth_version()
    elif action == "password":
        if not password_hash:
            raise AccountConflict("A password is required.")
        account.password_hash = password_hash
        account.bump_auth_version()
    elif action == "status":
        if account.status == "active":
            if role == "student" and Enrollment.query.filter_by(student_id=account.id, status="active").first() is not None:
                raise AccountConflict("Withdraw all active enrollments before suspending this student.")
            if role == "teacher":
                for group in groups:
                    if group.status == "active" and active_student_enrollment_count(group.id) > 0 and eligible_active_teacher_count(group.id) <= 1:
                        raise AccountConflict("Assign a replacement teacher before suspending the last eligible teacher of a group with active students.")
            account.status = "suspended"
        else:
            if role == "teacher":
                from app.services.schedule_resources import teacher_assignment_conflict
                for group in groups:
                    if group.status == "active" and teacher_assignment_conflict(account.id, group.id) is not None:
                        raise AccountConflict("This teacher's active group schedules overlap. Resolve the conflict before reactivation.")
            account.status = "active"
        account.bump_auth_version()
    else:
        raise AccountConflict("Unsupported account operation.")
    account.version += 1
    account.updated_at = whole_second_utc()
    db.session.add(AccountRevision(user_id=account.id, actor_id=actor_id, version=account.version,
                                  action=action, before_snapshot=before, after_snapshot=account_snapshot(account)))
    return account
