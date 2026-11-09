"""Authenticated, CSRF-protected device opt-in and opt-out."""
from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from flask_wtf.csrf import generate_csrf
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db, limiter
from app.services.device_push import COOKIE, SubscriptionConflict, current_device, device_cookie, save_subscription, settings, update_language, now

device_push_bp = Blueprint("device_push", __name__, url_prefix="/device-notifications")


def enforce_push_body_limits():
    if request.endpoint and request.endpoint.startswith("device_push."):
        request.max_content_length = 8192
        request.max_form_memory_size = 8192
        request.max_form_parts = 4


@device_push_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@device_push_bp.get("")
@login_required
def status():
    try:
        enabled, public, _, _ = settings()
        device = current_device(current_user.id) if enabled else None
        subscribed = bool(device and device.enabled and device.auth_version == current_user.auth_version)
        from datetime import timedelta
        response = jsonify(enabled=enabled, publicKey=public if enabled else "", subscribed=subscribed,
            language=device.language if device else None,
            renew=bool(subscribed and device.expires_at <= now() + timedelta(days=7)), csrfToken=generate_csrf())
        if subscribed:
            response.set_cookie(COOKIE, device_cookie(device), max_age=90 * 86400, httponly=True,
                secure=current_app.config['SESSION_COOKIE_SECURE'], samesite='Lax', path='/')
        return response
    except (ValueError, SQLAlchemyError):
        db.session.rollback()
        return jsonify(enabled=False, subscribed=False), 503


@device_push_bp.post('/language')
@login_required
@limiter.limit('30 per minute')
def language():
    from app.i18n import ui_language
    update_language(ui_language(), current_user.id)
    return jsonify(saved=True)


@device_push_bp.post("")
@login_required
@limiter.limit("20 per minute")
def subscribe():
    try:
        if not settings()[0]:
            return jsonify(error="Device notifications are not available yet."), 503
        device = save_subscription(current_user, request.get_json(silent=True))
        response = jsonify(subscribed=True)
        response.set_cookie(COOKIE, device_cookie(device), max_age=90 * 86400, httponly=True,
                            secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax", path="/")
        return response
    except SubscriptionConflict as error:
        db.session.rollback()
        return jsonify(error=str(error), code="renew_subscription"), 409
    except ValueError as error:
        db.session.rollback()
        return jsonify(error=str(error)), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Device notifications could not be saved. Please try again."), 503


@device_push_bp.post("/disable")
@login_required
@limiter.limit("20 per minute")
def disable():
    try:
        device = current_device(current_user.id) if current_app.config["DEVICE_PUSH_ENABLED"] else None
        if device:
            device.enabled = False
            device.version += 1
            db.session.commit()
        response = jsonify(subscribed=False)
        response.delete_cookie(COOKIE, path="/")
        return response
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Device notifications could not be disabled. Please try again."), 503
