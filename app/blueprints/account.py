"""Shared settings and private photo delivery for the authenticated account."""
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, logout_user
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import SQLAlchemyError

from app.blueprints.account_forms import ChangePasswordForm, ProfilePhotoForm
from app.blueprints.collector.hooks import end_session_on_logout
from app.extensions import db, limiter
from app.security.decorators import roles_required
from app.security.passwords import hash_password
from app.services.account_operations import AccountConflict
from app.services.file_storage import StorageContainmentError, delete_stored_file, open_stored_file
from app.services.file_validation import FileValidationError
from app.services.material_config import current_material_config
from app.services.profile_photos import (
    PHOTO_ACTION, PROFILE_PHOTO_MAX_BYTES, account_photo_url, current_profile_photo, store_profile_photo,
)
from app.services.self_service_accounts import (
    ACCOUNT_ROLES, change_own_password, change_own_photo, read_self_service_token,
    self_service_snapshot, self_service_token,
)

account_bp = Blueprint("account", __name__, url_prefix="/account")


def enforce_account_body_limits():
    """Registered before global CSRF so multipart parsing is already bounded."""
    if request.method != "POST":
        return
    if request.endpoint == "account.upload_photo":
        request.max_content_length = PROFILE_PHOTO_MAX_BYTES + 64 * 1024
        request.max_form_memory_size = 128 * 1024
        request.max_form_parts = 8
    elif request.endpoint == "account.password":
        request.max_content_length = 16 * 1024
        request.max_form_memory_size = 16 * 1024
        request.max_form_parts = 8


@account_bp.app_context_processor
def account_context():
    return {"account_photo_url": lambda: account_photo_url(current_user)}


@account_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.vary.add("Cookie")
    return response


def _settings(password_form=None, photo_form=None, status=200):
    from app.blueprints.admin.navigation import ADMIN_NAV_SECTIONS
    password_form = password_form or ChangePasswordForm(formdata=None, prefix="password-")
    photo_form = photo_form or ProfilePhotoForm(formdata=None, prefix="photo-")
    password_form.state.data = self_service_token(current_user, "password")
    photo_form.state.data = self_service_token(current_user, PHOTO_ACTION)
    return render_template("workspace/account.html", password_form=password_form, photo_form=photo_form,
        admin_nav_sections=ADMIN_NAV_SECTIONS if current_user.role == "administrator" else (),
        active_nav="account", tz_name=current_app.config.get("APP_TIMEZONE", "UTC")), status


def _expected(form, purpose):
    expected = read_self_service_token(form.state.data, purpose)
    if expected != self_service_snapshot(current_user):
        raise AccountConflict("Your account changed or this form expired. Review the page and try again.")
    return expected


def _limit_key():
    # Flask-Limiter can evaluate keys before the view's login decorator.
    if current_user.is_authenticated:
        return f"account:{current_user.public_id}"
    return f"account:anonymous:{get_remote_address()}"


@account_bp.get("")
@roles_required(*ACCOUNT_ROLES)
def settings():
    return _settings()


@account_bp.post("/password")
@roles_required(*ACCOUNT_ROLES)
@limiter.limit("5 per minute", key_func=_limit_key)
def password():
    form = ChangePasswordForm(prefix="password-")
    if not form.validate_on_submit():
        return _settings(password_form=form, status=400)
    actor_id, public_id, role = current_user.id, current_user.public_id, current_user.role
    try:
        expected = _expected(form, "password")
        # Costly new hashing precedes write locks; old credential is verified under them.
        new_hash = hash_password(form.new_password.data)
        change_own_password(public_id, role, actor_id, expected,
                            form.current_password.data, form.new_password.data, new_hash)
        db.session.commit()
    except AccountConflict as error:
        db.session.rollback()
        flash(str(error), "danger")
        return _settings(password_form=form, status=409)
    except SQLAlchemyError:
        db.session.rollback()
        flash("Your password could not be saved. Please try again.", "danger")
        return _settings(password_form=form, status=503)
    except BaseException:
        db.session.rollback()
        raise
    # A successful auth_version bump invalidates all older browser sessions.
    # Finish this browser's collector reference just as ordinary logout does.
    try:
        end_session_on_logout(actor_id)
    except Exception:
        current_app.logger.warning("Could not close the research session after a password change")
    session.pop("workspace_opening_target", None)
    logout_user()
    flash("Your password was changed. Sign in again with your new password.", "success")
    return redirect(url_for("auth.login"))


@account_bp.post("/photo")
@roles_required(*ACCOUNT_ROLES)
@limiter.limit("10 per minute", key_func=_limit_key)
def upload_photo():
    form = ProfilePhotoForm(prefix="photo-")
    if not form.validate_on_submit():
        return _settings(photo_form=form, status=400)
    actor_id, public_id, role = current_user.id, current_user.public_id, current_user.role
    config = current_material_config()
    stored, committed = None, False
    try:
        expected = _expected(form, PHOTO_ACTION)
        stored = store_profile_photo(config, form.photo.data)
        change_own_photo(public_id, role, actor_id, expected, stored)
        db.session.commit()
        committed = True
    except FileValidationError as error:
        db.session.rollback()
        form.photo.errors.append(str(error))
        return _settings(photo_form=form, status=400)
    except AccountConflict as error:
        db.session.rollback()
        flash(str(error), "danger")
        return _settings(photo_form=form, status=409)
    except (SQLAlchemyError, OSError, StorageContainmentError):
        db.session.rollback()
        flash("Your photo could not be saved. Please try again.", "danger")
        return _settings(photo_form=form, status=503)
    except BaseException:
        db.session.rollback()
        raise
    finally:
        if stored is not None and not committed:
            delete_stored_file(config, stored.storage_key)
    flash("Your profile photo was updated.", "success")
    return redirect(url_for("account.settings"))


@account_bp.get("/photo")
@roles_required(*ACCOUNT_ROLES)
def photo():
    # No user/file identity is accepted from the URL. v is only a cache buster.
    uploaded = current_profile_photo(current_user.id)
    if uploaded is None:
        abort(404)
    try:
        path = open_stored_file(current_material_config(), uploaded.storage_key)
    except (FileNotFoundError, StorageContainmentError):
        abort(404)
    response = send_file(path, mimetype="image/png", download_name="profile-photo.png",
                         as_attachment=False, conditional=False, etag=False)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
