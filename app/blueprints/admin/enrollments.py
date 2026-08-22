from flask import render_template, request
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.models import AcademicTerm, Course, Enrollment, EnrollmentStatus, Group, User, UserRole
from app.security.decorators import roles_required

_MAX_BIGINT = 9223372036854775807


def _escape_like(value):
    """Escape LIKE/ILIKE metacharacters so a search term containing a
    literal '%' or '_' is matched as those literal characters instead of
    being interpreted as a SQL wildcard.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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


@admin_bp.get("/enrollments")
@roles_required(UserRole.ADMINISTRATOR.value)
def enrollments_list():
    search = request.args.get("q", "").strip()
    raw_status = request.args.get("status", "").strip()
    status = raw_status if raw_status in {s.value for s in EnrollmentStatus} else ""
    term_id = _safe_id_arg("term_id")

    # Enrollment.student_id is a plain FK to the shared users table and
    # can technically reference any role at the database level (see the
    # model's docstring for the role-integrity boundary). This listing is
    # Student Enrollment data specifically, so it constrains the joined
    # User to role=student -- a row that somehow references a
    # Teacher/Administrator/Researcher (the FK cannot prevent that) is
    # excluded here rather than displayed as if it were a valid Student
    # enrollment.
    query = Enrollment.query.join(User, Enrollment.student_id == User.id).filter(
        User.role == UserRole.STUDENT.value
    ).options(
        joinedload(Enrollment.student),
        joinedload(Enrollment.group).joinedload(Group.course).joinedload(Course.level),
        joinedload(Enrollment.group).joinedload(Group.academic_term),
    )

    if search:
        # Same prefix-match + escaping rules already used for Student and
        # Teacher search: the beginning of the full name, the beginning
        # of any individual word within it, or the beginning of the
        # email -- never an arbitrary substring.
        escaped = _escape_like(search)
        name_prefix = f"{escaped}%"
        name_word_prefix = f"% {escaped}%"
        email_prefix = f"{escaped}%"
        query = query.filter(
            or_(
                User.full_name.ilike(name_prefix, escape="\\"),
                User.full_name.ilike(name_word_prefix, escape="\\"),
                User.email.ilike(email_prefix, escape="\\"),
            )
        )
    if status:
        query = query.filter(Enrollment.status == status)
    if term_id:
        query = query.join(Group, Enrollment.group_id == Group.id).filter(Group.academic_term_id == term_id)

    enrollments = query.order_by(Enrollment.created_at.desc(), Enrollment.id.desc()).all()

    return render_template(
        "admin/enrollments/list.html",
        enrollments=enrollments,
        search=search,
        selected_status=status,
        selected_term_id=term_id,
        terms=AcademicTerm.query.order_by(AcademicTerm.start_date.desc()).all(),
    )
