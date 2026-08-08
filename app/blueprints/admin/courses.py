from flask import flash, redirect, render_template, request, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import CourseForm
from app.blueprints.admin.utils import move_within_siblings, normalize_optional_text
from app.extensions import db
from app.models import AcademicStatus, Course, Level, UserRole
from app.security.decorators import roles_required


def _courses_query(level_id=None):
    query = Course.query
    if level_id is not None:
        query = query.filter(Course.level_id == level_id)
    return query.order_by(Course.level_id, Course.display_order, Course.id)


def _next_display_order(level_id):
    max_order = (
        db.session.query(db.func.max(Course.display_order)).filter(Course.level_id == level_id).scalar()
    )
    return (max_order + 1) if max_order is not None else 0


@admin_bp.get("/courses")
@roles_required(UserRole.ADMINISTRATOR.value)
def courses_list():
    level_id = request.args.get("level_id", type=int)
    courses = _courses_query(level_id).all()
    levels = Level.query.order_by(Level.display_order, Level.id).all()
    return render_template(
        "admin/courses/list.html", courses=courses, levels=levels, selected_level_id=level_id
    )


@admin_bp.route("/courses/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def course_create():
    form = CourseForm()
    if request.method == "GET":
        preselect_level_id = request.args.get("level_id", type=int)
        if preselect_level_id is not None:
            form.level_id.data = preselect_level_id

    if form.validate_on_submit():
        course = Course(
            level_id=form.level_id.data,
            title=form.title.data.strip(),
            code=normalize_optional_text(form.code.data),
            description=normalize_optional_text(form.description.data),
            display_order=_next_display_order(form.level_id.data),
        )
        db.session.add(course)
        db.session.commit()
        flash(f"Course '{course.title}' created.", "success")
        return redirect(url_for("admin.courses_list"))
    return render_template("admin/courses/form.html", form=form, course=None)


@admin_bp.route("/courses/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def course_edit(public_id):
    course = Course.query.filter_by(public_id=public_id).first_or_404()
    form = CourseForm(obj=course, course_id=course.id)

    if form.validate_on_submit():
        moving_to_new_level = form.level_id.data != course.level_id
        course.title = form.title.data.strip()
        course.code = normalize_optional_text(form.code.data)
        course.description = normalize_optional_text(form.description.data)
        if moving_to_new_level:
            course.display_order = _next_display_order(form.level_id.data)
            course.level_id = form.level_id.data
        db.session.commit()
        flash(f"Course '{course.title}' updated.", "success")
        return redirect(url_for("admin.courses_list"))
    return render_template("admin/courses/form.html", form=form, course=course)


@admin_bp.post("/courses/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def course_toggle_status(public_id):
    course = Course.query.filter_by(public_id=public_id).first_or_404()
    course.status = (
        AcademicStatus.ARCHIVED.value
        if course.status == AcademicStatus.ACTIVE.value
        else AcademicStatus.ACTIVE.value
    )
    db.session.commit()
    flash(f"Course '{course.title}' is now {course.status}.", "success")
    return redirect(url_for("admin.courses_list"))


@admin_bp.post("/courses/<public_id>/move-up")
@roles_required(UserRole.ADMINISTRATOR.value)
def course_move_up(public_id):
    course = Course.query.filter_by(public_id=public_id).first_or_404()
    _, moved = move_within_siblings(_courses_query(course.level_id).all(), public_id, -1)
    if moved:
        db.session.commit()
        flash(f"Course '{course.title}' moved up.", "success")
    else:
        flash("This course is already first in its level.", "warning")
    return redirect(url_for("admin.courses_list"))


@admin_bp.post("/courses/<public_id>/move-down")
@roles_required(UserRole.ADMINISTRATOR.value)
def course_move_down(public_id):
    course = Course.query.filter_by(public_id=public_id).first_or_404()
    _, moved = move_within_siblings(_courses_query(course.level_id).all(), public_id, 1)
    if moved:
        db.session.commit()
        flash(f"Course '{course.title}' moved down.", "success")
    else:
        flash("This course is already last in its level.", "warning")
    return redirect(url_for("admin.courses_list"))
