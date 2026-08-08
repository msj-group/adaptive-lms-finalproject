from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import GroupForm
from app.blueprints.admin.utils import normalize_optional_text
from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, Course, Group, Level, UserRole
from app.security.decorators import roles_required


def _course_choices():
    return Course.query.join(Level).order_by(Level.display_order, Course.display_order).all()


_MAX_BIGINT = 9223372036854775807


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


@admin_bp.get("/groups")
@roles_required(UserRole.ADMINISTRATOR.value)
def groups_list():
    search = request.args.get("q", "").strip()
    term_id = _safe_id_arg("term_id")
    course_id = _safe_id_arg("course_id")
    level_id = _safe_id_arg("level_id")
    status = request.args.get("status", "").strip()

    query = Group.query.options(
        joinedload(Group.course).joinedload(Course.level),
        joinedload(Group.academic_term),
    )
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Group.name.ilike(like), Group.code.ilike(like)))
    if term_id:
        query = query.filter(Group.academic_term_id == term_id)
    if course_id:
        query = query.filter(Group.course_id == course_id)
    if level_id:
        query = query.join(Course, Group.course_id == Course.id).filter(Course.level_id == level_id)
    if status in {s.value for s in AcademicStatus}:
        query = query.filter(Group.status == status)

    groups = query.order_by(Group.created_at.desc()).all()
    return render_template(
        "admin/groups/list.html",
        groups=groups,
        search=search,
        terms=AcademicTerm.query.order_by(AcademicTerm.start_date.desc()).all(),
        courses=_course_choices(),
        levels=Level.query.order_by(Level.display_order, Level.id).all(),
        selected_term_id=term_id,
        selected_course_id=course_id,
        selected_level_id=level_id,
        selected_status=status,
    )


@admin_bp.get("/groups/<public_id>")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_detail(public_id):
    group = (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=public_id)
        .first_or_404()
    )
    return render_template("admin/groups/detail.html", group=group)


@admin_bp.route("/groups/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def group_create():
    if AcademicTerm.query.count() == 0 or Course.query.count() == 0:
        flash("Create at least one Academic Term and one Course before creating a Group.", "warning")
        return redirect(url_for("admin.groups_list"))

    form = GroupForm()
    if form.validate_on_submit():
        group = Group(
            academic_term_id=form.academic_term_id.data,
            course_id=form.course_id.data,
            name=form.name.data.strip(),
            code=normalize_optional_text(form.code.data),
            capacity=form.capacity.data,
            status=form.status.data,
        )
        db.session.add(group)
        db.session.commit()
        flash(f"Group '{group.name}' created.", "success")
        return redirect(url_for("admin.groups_list"))

    return render_template("admin/groups/form.html", form=form, courses=_course_choices(), group=None)


@admin_bp.route("/groups/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def group_edit(public_id):
    group = Group.query.filter_by(public_id=public_id).first_or_404()
    form = GroupForm(obj=group, group_id=group.id)

    if form.validate_on_submit():
        group.academic_term_id = form.academic_term_id.data
        group.course_id = form.course_id.data
        group.name = form.name.data.strip()
        group.code = normalize_optional_text(form.code.data)
        group.capacity = form.capacity.data
        group.status = form.status.data
        db.session.commit()
        flash(f"Group '{group.name}' updated.", "success")
        return redirect(url_for("admin.groups_list"))

    return render_template("admin/groups/form.html", form=form, courses=_course_choices(), group=group)


@admin_bp.post("/groups/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_toggle_status(public_id):
    group = Group.query.filter_by(public_id=public_id).first_or_404()
    group.status = (
        AcademicStatus.ARCHIVED.value
        if group.status == AcademicStatus.ACTIVE.value
        else AcademicStatus.ACTIVE.value
    )
    db.session.commit()
    flash(f"Group '{group.name}' is now {group.status}.", "success")
    return redirect(url_for("admin.groups_list"))
