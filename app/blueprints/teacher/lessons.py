"""Read-only teacher lesson index."""
from flask import render_template

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.units import _archived_chain_labels, _group_is_operational, _teacher_group_or_404, _unit_for_group_or_404
from app.models import Lesson, UserRole
from app.security.decorators import roles_required
from app.services.lesson_queries import lessons_ordered

_ACTIVE = "active"


def _lesson_for_unit_or_404(unit, lesson_public_id):
    """A Lesson by its own public_id, constrained to `unit`. A Lesson
    public_id valid only for another Unit 404s here."""
    return Lesson.query.filter_by(
        public_id=lesson_public_id, unit_id=unit.id
    ).first_or_404()


@teacher_bp.get("/groups/<group_public_id>/units/<unit_public_id>/lessons")
@roles_required(UserRole.TEACHER.value)
def group_lessons(group_public_id, unit_public_id):
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id, include_shared=True)
    operational = _group_is_operational(group)
    unit_active = unit.status == _ACTIVE
    return render_template(
        "teacher/lessons/list.html",
        group=group,
        unit=unit,
        lessons=lessons_ordered(unit.id),
        operational=operational,
        unit_active=unit_active,
        archived_labels=[] if operational and unit_active else _archived_chain_labels(group)
        + ([] if unit_active else ["unit"]),
    )
