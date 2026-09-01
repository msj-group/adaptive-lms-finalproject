"""Student lesson navigation (M11): the Group learning outline and a
single authorized Lesson page.

Group-centered, public-id-only routes
(``/student/groups/<group_public_id>/units`` and
``.../units/<unit_public_id>/lessons/<lesson_public_id>``). Both are
GET-only reads.

**Authorization is SQL-scoped** to ``current_user.id`` in
``app/services/student_lessons.py`` -- an object is never loaded broadly
and then authorized. A Student may reach the outline only through their
own **active** ``Enrollment`` for that exact Group, with an active
AcademicTerm / Level / Course / Group; a Lesson page additionally
requires an active Unit and a ``published`` Lesson. Every failure --
withdrawn / missing Enrollment, a different Group, mismatched nested ids,
an inactive Unit or ancestor, a draft or non-existent Lesson -- returns a
non-disclosing 404. Student access does not depend on Schedule existence
or on any current Teacher assignment.

``roles_required(STUDENT)`` gives the role guard (anonymous -> login,
any other role -> 403); a suspended Student cannot hold a session at all
(the Flask-Login ``user_loader`` rejects it).
"""

from flask import abort, render_template
from flask_login import current_user

from app.blueprints.student import student_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.student_lessons import (
    outline_units,
    student_lesson_detail,
    student_outline_group,
)


@student_bp.get("/groups/<group_public_id>/units")
@roles_required(UserRole.STUDENT.value)
def group_units(group_public_id):
    group = student_outline_group(current_user.id, group_public_id)
    if group is None:
        abort(404)
    return render_template(
        "student/learning/outline.html",
        group_name=group.name,
        group_public_id=group.public_id,
        course_title=group.course.title,
        level_name=group.course.level.name,
        term_name=group.academic_term.name,
        units=outline_units(group.id),
    )


@student_bp.get("/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>")
@roles_required(UserRole.STUDENT.value)
def lesson_detail(group_public_id, unit_public_id, lesson_public_id):
    data = student_lesson_detail(
        current_user.id, group_public_id, unit_public_id, lesson_public_id
    )
    if data is None:
        abort(404)
    return render_template(
        "student/learning/lesson.html",
        unit_public_id=unit_public_id,
        lesson_public_id=lesson_public_id,
        **data,
    )
