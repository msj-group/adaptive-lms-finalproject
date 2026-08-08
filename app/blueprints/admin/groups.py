from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import GroupForm
from app.blueprints.admin.utils import normalize_optional_text
from app.extensions import db
from app.models import AcademicTerm, Course, Group, Level, UserRole
from app.security.decorators import roles_required


@admin_bp.get("/groups")
@roles_required(UserRole.ADMINISTRATOR.value)
def groups_list():
    search = request.args.get("q", "").strip()

    query = Group.query.options(
        joinedload(Group.course).joinedload(Course.level),
        joinedload(Group.academic_term),
    )
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Group.name.ilike(like), Group.code.ilike(like)))

    groups = query.order_by(Group.created_at.desc()).all()
    return render_template("admin/groups/list.html", groups=groups, search=search)


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

    courses = Course.query.join(Level).order_by(Level.display_order, Course.display_order).all()
    return render_template("admin/groups/form.html", form=form, courses=courses)
