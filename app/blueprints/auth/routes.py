from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth.forms import LoginForm
from app.blueprints.collector.hooks import authenticated_user_id, end_session_on_logout
from app.extensions import limiter
from app.models.user import User, UserRole
from app.security.passwords import verify_password
from app.security.redirects import get_safe_redirect_target

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# All roles authenticate through this entry and reach their own home.
# Researcher authorization stays inside the separate workspace. An unknown
# role is refused rather than sent to a sample page or another workspace.
ROLE_HOME_ENDPOINT = {
    UserRole.ADMINISTRATOR.value: "admin.dashboard",
    UserRole.TEACHER.value: "teacher.dashboard",
    UserRole.STUDENT.value: "student.dashboard",
    UserRole.RESEARCHER.value: "research.dashboard",
}


def home_endpoint_for(user):
    endpoint = ROLE_HOME_ENDPOINT.get(user.role)
    if endpoint is None:
        abort(403)
    return endpoint


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
            session["workspace_opening_target"] = login_target_for(user, request.args.get("next"))
            return redirect(url_for("auth.opening"))
        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.get("/opening")
@login_required
def opening():
    """A one-use presentation step after a successful ordinary sign-in."""
    home_endpoint = home_endpoint_for(current_user)
    candidate = session.pop("workspace_opening_target", None)
    if not isinstance(candidate, str):
        return redirect(url_for(home_endpoint))
    target = login_target_for(current_user, candidate)
    copy = {
        UserRole.STUDENT.value: ("STUDENT WORKSPACE", "Your next chapter", "Opening your learning space…"),
        UserRole.TEACHER.value: ("TEACHER WORKSPACE", "Your teaching space", "Opening your teaching workspace…"),
        UserRole.ADMINISTRATOR.value: ("ADMINISTRATOR WORKSPACE", "Your centre workspace", "Opening your centre workspace…"),
        UserRole.RESEARCHER.value: ("RESEARCH WORKSPACE", "Your research workspace", "Opening your research workspace…"),
    }
    eyebrow, heading, description = copy[current_user.role]
    response = make_response(render_template(
        "auth/opening.html", target=target, eyebrow=eyebrow,
        heading=heading, description=description,
        logout_endpoint="research.logout" if current_user.role == UserRole.RESEARCHER.value else "auth.logout",
    ))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    # Phase 6: close this browser's research session, if any. Best-effort:
    # it never raises, so research can never prevent a logout.
    end_session_on_logout(authenticated_user_id())
    session.pop("workspace_opening_target", None)
    logout_user()
    return redirect(url_for("auth.login"))
