"""Student authorized file serving for `file` Materials (M12).

Two fully-nested, GET-only routes under the Student's own Lesson path
(``/student/groups/<gp>/units/<up>/lessons/<lp>/materials/<mp>/open`` and
``.../download``). Rich text and external links render directly on the
Lesson page (``app/blueprints/student/learning.py`` /
``app/services/student_lessons.py``) -- these two routes exist only to
serve `file` Material bytes.

**Authorization is fully SQL-scoped** in
``app/services/student_lessons.student_file_material`` -- own active
Enrollment, active AcademicTerm/Level/Course/Group/Unit, published
Lesson, **active** Material, and ``kind == 'file'`` are all in one query.
Any break -- draft Lesson, archived Material, a non-file Material's
public_id, withdrawn Enrollment, cross-Group access, mismatched nested
ids, or a missing object -- yields no row and this route 404s without
disclosure. Student access does not depend on Schedule existence or on
any current Teacher assignment.
"""

from flask import abort
from flask_login import current_user

from app.blueprints.student import student_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.material_serving import serve_uploaded_file
from app.services.student_lessons import student_file_material


def _authorized_uploaded_file(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    row = student_file_material(
        current_user.id, group_public_id, unit_public_id, lesson_public_id, material_public_id
    )
    if row is None:
        abort(404)
    _material, uploaded_file = row
    return uploaded_file


@student_bp.get(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>"
    "/materials/<material_public_id>/open"
)
@roles_required(UserRole.STUDENT.value)
def material_open(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    uploaded_file = _authorized_uploaded_file(
        group_public_id, unit_public_id, lesson_public_id, material_public_id
    )
    return serve_uploaded_file(uploaded_file, current_user.id, force_attachment=False)


@student_bp.get(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>"
    "/materials/<material_public_id>/download"
)
@roles_required(UserRole.STUDENT.value)
def material_download(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    uploaded_file = _authorized_uploaded_file(
        group_public_id, unit_public_id, lesson_public_id, material_public_id
    )
    return serve_uploaded_file(uploaded_file, current_user.id, force_attachment=True)
