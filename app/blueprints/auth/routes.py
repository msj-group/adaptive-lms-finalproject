from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth.forms import LoginForm
from app.blueprints.collector.hooks import authenticated_user_id, end_session_on_logout
from app.extensions import limiter
from app.models.user import User, UserRole
from app.security.passwords import verify_password
from app.security.redirects import get_safe_redirect_target

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# All roles authenticate through this entry and reach their own home.
# Researcher authorization stays inside the separate workspace. The fallback
# is kept for a role added later that has no dashboard yet.
ROLE_HOME_ENDPOINT = {
    UserRole.ADMINISTRATOR.value: "admin.dashboard",
    UserRole.TEACHER.value: "teacher.dashboard",
    UserRole.STUDENT.value: "student.dashboard",
    UserRole.RESEARCHER.value: "research.dashboard",
}
DEFAULT_HOME_ENDPOINT = "design_system.index"


def home_endpoint_for(user):
    return ROLE_HOME_ENDPOINT.get(user.role, DEFAULT_HOME_ENDPOINT)


def login_target_for(user, candidate):
    """Honor safe return paths; Researchers return only to their workspace."""
    target = get_safe_redirect_target(candidate)
    if user.role == UserRole.RESEARCHER.value:
        if target and (target == "/research" or target.startswith("/research/")) \
                and not target.startswith(("/research/login", "/research/logout")):
            return target
        return url_for(home_endpoint_for(user))
    return target or url_for(home_endpoint_for(user))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.shared_limit("10 per minute", scope="account-login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if (
            user
            and user.is_active_account()
            and verify_password(user.password_hash, form.password.data)
        ):
            login_user(user)
            return redirect(login_target_for(user, request.args.get("next")))
        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    # Phase 6: close this browser's research session, if any. Best-effort:
    # it never raises, so research can never prevent a logout.
    end_session_on_logout(authenticated_user_id())
    logout_user()
    return redirect(url_for("auth.login"))
