from app.blueprints.admin.account_actions import edit_account, toggle_account, reset_account_password
from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import TeacherCreateForm, TeacherEditForm, TeacherPasswordResetForm
from app.extensions import db
from app.models import User, UserRole, UserStatus
from app.security.decorators import roles_required
from app.security.passwords import hash_password


def _get_teacher_or_404(public_id):
    return User.query.filter_by(public_id=public_id, role=UserRole.TEACHER.value).first_or_404()


def _escape_like(value):
    """Escape LIKE/ILIKE metacharacters so a search term containing a
    literal '%' or '_' is matched as those literal characters instead of
    being interpreted as a SQL wildcard.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Fixed, known marker the Teachers-list toggle-status form sends so the
# server can tell "this action started from the list" from "this action
# started from the detail page" -- deliberately NOT a caller-supplied
# next/return_to URL (that would be an open-redirect surface) and NOT the
# Referer header (unreliable, spoofable, sometimes stripped by browsers).
# Only this exact value is honoured; anything else falls back to the
# existing detail-page redirect. Same pattern as students.py, kept local
# to this module for this part rather than shared.
TEACHER_LIST_REDIRECT_SOURCE = "list"


def _redirect_after_toggle_status(teacher):
    if request.form.get("source") == TEACHER_LIST_REDIRECT_SOURCE:
        raw_q = request.form.get("q", "").strip()
        raw_status = request.form.get("status", "").strip()
        status = raw_status if raw_status in {s.value for s in UserStatus} else ""
        params = {}
        if raw_q:
            params["q"] = raw_q
        if status:
            params["status"] = status
        return redirect(url_for("admin.teachers_list", **params))
    return redirect(url_for("admin.teacher_detail", public_id=teacher.public_id))


@admin_bp.get("/teachers")
@roles_required(UserRole.ADMINISTRATOR.value)
def teachers_list():
    search = request.args.get("q", "").strip()
    raw_status = request.args.get("status", "").strip()
    status = raw_status if raw_status in {s.value for s in UserStatus} else ""

    query = User.query.filter(User.role == UserRole.TEACHER.value)
    if search:
        # Prefix match only: the beginning of the full name, the beginning
        # of any individual word within it, or the beginning of the email.
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
        query = query.filter(User.status == status)

    teachers = query.order_by(User.created_at.desc(), User.id.desc()).all()
    is_live_search_request = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    template = "admin/teachers/_results.html" if is_live_search_request else "admin/teachers/list.html"
    return render_template(
        template,
        teachers=teachers,
        search=search,
        selected_status=status,
    )


@admin_bp.route("/teachers/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def teacher_create():
    form = TeacherCreateForm()
    if form.validate_on_submit():
        teacher = User(
            full_name=form.full_name.data.strip(),
            email=form.email.data.strip().lower(),
            password_hash=hash_password(form.password.data),
            role=UserRole.TEACHER.value,
            status=UserStatus.ACTIVE.value,
        )
        db.session.add(teacher)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.email.errors.append("A user with this email already exists.")
            return render_template("admin/teachers/form.html", form=form, teacher=None)
        flash(f"Teacher '{teacher.full_name}' created.", "success")
        return redirect(url_for("admin.teacher_detail", public_id=teacher.public_id))
    return render_template("admin/teachers/form.html", form=form, teacher=None)


@admin_bp.get("/teachers/<public_id>")
@roles_required(UserRole.ADMINISTRATOR.value)
def teacher_detail(public_id):
    teacher = _get_teacher_or_404(public_id)
    from app.services.dashboard_queries import teacher_dashboard
    from app.services.schedule_occurrences import app_now
    from flask import current_app
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    data = teacher_dashboard(teacher.id, app_now(tz_name))
    return render_template("admin/teachers/detail.html", teacher=teacher, assigned_groups=data["cards"], tz_name=tz_name)


@admin_bp.route("/teachers/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def teacher_edit(public_id):
    return edit_account(public_id, "teacher", TeacherEditForm)


@admin_bp.post("/teachers/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def teacher_toggle_status(public_id):
    return toggle_account(public_id, "teacher", _redirect_after_toggle_status)


@admin_bp.route("/teachers/<public_id>/reset-password", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def teacher_reset_password(public_id):
    return reset_account_password(public_id, "teacher", TeacherPasswordResetForm)
