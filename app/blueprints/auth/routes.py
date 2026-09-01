from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth.forms import LoginForm
from app.extensions import limiter
from app.models.user import User, UserRole
from app.security.passwords import verify_password
from app.security.redirects import get_safe_redirect_target

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Only roles with a real dashboard get a dedicated entry here. Other roles
# (currently only Researcher) fall back to DEFAULT_HOME_ENDPOINT until
# their dashboards are built -- the Researcher dashboard is deferred to
# Phase 6 (see docs/DECISIONS.md, "Role dashboards (Phase 3, Part M09)").
ROLE_HOME_ENDPOINT = {
    UserRole.ADMINISTRATOR.value: "admin.dashboard",
    UserRole.TEACHER.value: "teacher.dashboard",
    UserRole.STUDENT.value: "student.dashboard",
}
DEFAULT_HOME_ENDPOINT = "design_system.index"


def _home_endpoint_for(user):
    return ROLE_HOME_ENDPOINT.get(user.role, DEFAULT_HOME_ENDPOINT)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for(_home_endpoint_for(current_user)))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user and user.is_active_account() and verify_password(user.password_hash, form.password.data):
            login_user(user)
            next_url = get_safe_redirect_target(request.args.get("next"))
            return redirect(next_url or url_for(_home_endpoint_for(user)))
        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
