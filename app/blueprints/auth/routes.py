from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth.forms import LoginForm
from app.extensions import limiter
from app.models.user import User
from app.security.passwords import verify_password

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Placeholder until role dashboards exist (Phase 3). Every role currently
# lands on the design system page after login.
ROLE_HOME_ENDPOINT = "design_system.index"


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for(ROLE_HOME_ENDPOINT))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user and user.is_active_account() and verify_password(user.password_hash, form.password.data):
            login_user(user)
            next_url = request.args.get("next")
            return redirect(next_url or url_for(ROLE_HOME_ENDPOINT))
        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
