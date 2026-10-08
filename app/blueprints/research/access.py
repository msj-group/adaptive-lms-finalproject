"""Research login compatibility and role-protected workspace logout.

All accounts authenticate at ``/auth/login``. Anonymous GET requests to
the old login URL redirect there; old POST requests invoke the same shared
login handler, including its shared rate limit and credential checks. No
separate research login form remains. The stored role authorizes access.
"""

from flask import abort, redirect, request, url_for
from flask_login import current_user, logout_user

from app.blueprints.auth.routes import login as shared_login
from app.blueprints.research import research_bp
from app.models import UserRole
from app.security.redirects import get_safe_redirect_target

_RESEARCHER = UserRole.RESEARCHER.value


@research_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST" or current_user.is_authenticated:
        return shared_login()
    target = get_safe_redirect_target(request.args.get("next"))
    return redirect(url_for("auth.login", next=target) if target else url_for("auth.login"))


@research_bp.post("/logout")
def logout():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if current_user.role != _RESEARCHER:
        abort(403)
    logout_user()
    return redirect(url_for("auth.login"))
