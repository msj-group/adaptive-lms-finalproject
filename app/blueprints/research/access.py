"""The dedicated Researcher login and logout (Phase 6 replacement)::

    GET|POST  /research/login[?next=/research/...]
    POST      /research/logout

**Only active Researcher accounts authenticate here**, and the LMS login
(``/auth/login``) refuses Researcher accounts -- both with the same generic
"Invalid email or password." whatever was wrong, so neither entry discloses
which accounts exist or what role they have. The role comes only from the
stored account: no typed email, email domain or allowlist grants it.

**Somebody already signed in.** A signed-in Researcher is sent to the
dashboard. A signed-in Administrator, Teacher or Student is sent back to
their own portal home, **without** being signed out and without the
submitted credentials being checked -- the research login never switches the
account of a browser silently.

**Redirects.** ``next`` is honoured only when it is a safe same-origin path
inside the workspace (``/research/...``); anything else falls back to the
dashboard.

Password verification reuses the project's Argon2 service; sessions reuse
Flask-Login with the account's ``auth_version``, so a suspension or password
reset ends a Researcher session exactly as it ends any other.

Researchers authenticate with their approved account's email and password.
The owners chose password authentication without a second factor.
"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from app.blueprints.auth.forms import LoginForm
from app.blueprints.auth.routes import home_endpoint_for
from app.blueprints.research import research_bp
from app.extensions import limiter
from app.models import User, UserRole
from app.security.passwords import verify_password
from app.security.redirects import get_safe_redirect_target

_RESEARCHER = UserRole.RESEARCHER.value
GENERIC_LOGIN_ERROR = "Invalid email or password."


def _workspace_target(candidate):
    target = get_safe_redirect_target(candidate)
    if target and (target == "/research" or target.startswith("/research/")) \
            and not target.startswith("/research/login"):
        return target
    return None


@research_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        if current_user.role == _RESEARCHER:
            return redirect(url_for("research.dashboard"))
        return redirect(url_for(home_endpoint_for(current_user)))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if (
            user is not None
            and user.role == _RESEARCHER
            and user.is_active_account()
            and verify_password(user.password_hash, form.password.data)
        ):
            login_user(user)
            return redirect(
                _workspace_target(request.args.get("next")) or url_for("research.dashboard")
            )
        flash(GENERIC_LOGIN_ERROR, "danger")
    return render_template("research/login.html", form=form)


@research_bp.post("/logout")
def logout():
    if not current_user.is_authenticated:
        return redirect(url_for("research.login"))
    if current_user.role != _RESEARCHER:
        abort(403)
    logout_user()
    return redirect(url_for("research.login"))
