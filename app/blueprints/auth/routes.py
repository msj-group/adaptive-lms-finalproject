from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth.forms import LoginForm
from app.blueprints.collector.hooks import authenticated_user_id, end_session_on_logout
from app.extensions import limiter
from app.models.user import User, UserRole
from app.security.passwords import verify_password
from app.security.redirects import get_safe_redirect_target

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Every role has a real home. A Researcher's home is the separate research
# workspace, which a Researcher reaches only through its own login entry
# (``/research/login``); this table is still what sends an already signed-in
# Researcher who opens the LMS login back to that workspace. The fallback is
# kept for a role added later that has no dashboard yet.
ROLE_HOME_ENDPOINT = {
    UserRole.ADMINISTRATOR.value: "admin.dashboard",
    UserRole.TEACHER.value: "teacher.dashboard",
    UserRole.STUDENT.value: "student.dashboard",
    UserRole.RESEARCHER.value: "research.dashboard",
}
DEFAULT_HOME_ENDPOINT = "design_system.index"


def home_endpoint_for(user):
    return ROLE_HOME_ENDPOINT.get(user.role, DEFAULT_HOME_ENDPOINT)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        # Phase 6: Researcher accounts sign in only through the dedicated
        # research login, keeping the workspace's authentication entry separate.
        # The refusal is the same generic message as a wrong password.
        if (
            user
            and user.role != UserRole.RESEARCHER.value
            and user.is_active_account()
            and verify_password(user.password_hash, form.password.data)
        ):
            login_user(user)
            next_url = get_safe_redirect_target(request.args.get("next"))
            return redirect(next_url or url_for(home_endpoint_for(user)))
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
