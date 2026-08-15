from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import StudentCreateForm, StudentEditForm, StudentPasswordResetForm
from app.extensions import db
from app.models import User, UserRole, UserStatus
from app.security.decorators import roles_required
from app.security.passwords import hash_password


def _get_student_or_404(public_id):
    return User.query.filter_by(public_id=public_id, role=UserRole.STUDENT.value).first_or_404()


@admin_bp.get("/students")
@roles_required(UserRole.ADMINISTRATOR.value)
def students_list():
    search = request.args.get("q", "").strip()
    raw_status = request.args.get("status", "").strip()
    status = raw_status if raw_status in {s.value for s in UserStatus} else ""

    query = User.query.filter(User.role == UserRole.STUDENT.value)
    if search:
        like = f"%{search}%"
        query = query.filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
    if status:
        query = query.filter(User.status == status)

    students = query.order_by(User.created_at.desc(), User.id.desc()).all()
    return render_template(
        "admin/students/list.html",
        students=students,
        search=search,
        selected_status=status,
    )


@admin_bp.route("/students/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def student_create():
    form = StudentCreateForm()
    if form.validate_on_submit():
        student = User(
            full_name=form.full_name.data.strip(),
            email=form.email.data.strip().lower(),
            password_hash=hash_password(form.password.data),
            role=UserRole.STUDENT.value,
            status=UserStatus.ACTIVE.value,
        )
        db.session.add(student)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.email.errors.append("A user with this email already exists.")
            return render_template("admin/students/form.html", form=form, student=None)
        flash(f"Student '{student.full_name}' created.", "success")
        return redirect(url_for("admin.student_detail", public_id=student.public_id))
    return render_template("admin/students/form.html", form=form, student=None)


@admin_bp.get("/students/<public_id>")
@roles_required(UserRole.ADMINISTRATOR.value)
def student_detail(public_id):
    student = _get_student_or_404(public_id)
    return render_template("admin/students/detail.html", student=student)


@admin_bp.route("/students/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def student_edit(public_id):
    student = _get_student_or_404(public_id)
    form = StudentEditForm(obj=student, student_id=student.id)

    if form.validate_on_submit():
        new_email = form.email.data.strip().lower()
        email_changed = new_email != student.email
        student.full_name = form.full_name.data.strip()
        student.email = new_email
        if email_changed:
            student.bump_auth_version()
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.email.errors.append("A user with this email already exists.")
            return render_template("admin/students/form.html", form=form, student=student)
        flash(f"Student '{student.full_name}' updated.", "success")
        return redirect(url_for("admin.student_detail", public_id=student.public_id))

    return render_template("admin/students/form.html", form=form, student=student)


@admin_bp.post("/students/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def student_toggle_status(public_id):
    student = _get_student_or_404(public_id)
    student.status = (
        UserStatus.SUSPENDED.value
        if student.status == UserStatus.ACTIVE.value
        else UserStatus.ACTIVE.value
    )
    student.bump_auth_version()
    db.session.commit()
    flash(f"Student '{student.full_name}' is now {student.status}.", "success")
    return redirect(url_for("admin.student_detail", public_id=student.public_id))


@admin_bp.route("/students/<public_id>/reset-password", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def student_reset_password(public_id):
    student = _get_student_or_404(public_id)
    form = StudentPasswordResetForm()
    if form.validate_on_submit():
        student.password_hash = hash_password(form.password.data)
        student.bump_auth_version()
        db.session.commit()
        flash(f"Password reset for '{student.full_name}'.", "success")
        return redirect(url_for("admin.student_detail", public_id=student.public_id))
    return render_template("admin/students/reset_password.html", form=form, student=student)
