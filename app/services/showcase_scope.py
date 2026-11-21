"""Keep public student experiences outside the real research population."""
from flask import current_app, has_request_context, request
from sqlalchemy import exists, func, or_, select

from app.extensions import db
from app.models import Enrollment, EnrollmentMembership, Group, User


def showcase_account_clause(user_id=User.id):
    """Use the reserved account identity and immutable enrollment history."""
    identity = User.email == func.concat('visitor.', User.public_id, '@study.invalid')
    groups = (current_app.config.get('STUDY_ENTRY_SETTINGS') or {}).get('groups', ())
    if not groups:
        return identity
    membership = exists(select(1).select_from(EnrollmentMembership)
        .join(Enrollment, Enrollment.id == EnrollmentMembership.enrollment_id)
        .join(Group, Group.id == EnrollmentMembership.group_id)
        .where(Enrollment.student_id == user_id, Group.public_id.in_(groups)))
    return or_(identity, membership)


def is_showcase_student(user, *, cached=False):
    if user is None or user.role != 'student':
        return False
    if user.email.casefold() == 'visitor.' + user.public_id.casefold() + '@study.invalid':
        return True
    if not (current_app.config.get('STUDY_ENTRY_SETTINGS') or {}).get('groups'):
        return False
    cache = request.environ.setdefault('aelms.showcase_accounts', {}) if cached and has_request_context() else {}
    if user.id not in cache:
        cache[user.id] = bool(db.session.query(User.id)
            .filter(User.id == user.id, showcase_account_clause()).first())
    return cache[user.id]
