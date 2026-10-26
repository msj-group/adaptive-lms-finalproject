"""Read-only, centre-wide directory totals for the Administrator Setup Hub."""
from flask import g, request
from flask_login import current_user
from sqlalchemy import func, select

from app.extensions import db
from app.models import AcademicTerm, Course, Level, Room, UserRole


ACADEMIC_DIRECTORY_ENDPOINTS = frozenset({
    "admin.courses_list", "admin.academic_terms_list", "admin.levels_list", "admin.rooms_list",
})


def academic_setup_totals_statement():
    """One scalar row; totals include all statuses and ignore directory filters."""
    return select(*(
        select(func.count()).select_from(model).scalar_subquery().label(key)
        for key, model in (("courses", Course), ("terms", AcademicTerm), ("levels", Level), ("rooms", Room))
    ))


def academic_setup_totals():
    """Called lazily by full directory templates, never by query-result fragments."""
    if (
        request.endpoint not in ACADEMIC_DIRECTORY_ENDPOINTS
        or not current_user.is_authenticated
        or current_user.role != UserRole.ADMINISTRATOR.value
        or not current_user.is_active_account()
    ):
        return {}
    if not hasattr(g, "academic_setup_totals"):
        row = db.session.execute(academic_setup_totals_statement()).mappings().one()
        g.academic_setup_totals = {key: int(value) for key, value in row.items()}
    return g.academic_setup_totals
