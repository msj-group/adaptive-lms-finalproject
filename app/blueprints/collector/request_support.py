"""Authenticated, bounded, private collector HTTP helpers."""
from functools import wraps
from flask import jsonify, request, session
from flask_limiter.util import get_remote_address
from flask_login import current_user
from app.models import UserRole
_STUDENT = UserRole.STUDENT.value

def _account_key():
    """Rate-limit per signed-in account, not per address: Students at the
    centre may share one public IP, and a shared budget would silently drop
    everyone's data. Anonymous requests (refused anyway) fall back to the
    address."""
    raw = session.get("_user_id")
    return f"account:{str(raw).split('.', 1)[0]}" if raw else get_remote_address()


def _json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def _student_json(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return _json({"error": "authentication_required"}, 401)
        if current_user.role != _STUDENT:
            return _json({"error": "forbidden"}, 403)
        return view_func(*args, **kwargs)

    return wrapped


def _read_json_body(limit):
    """``(bytes, None)`` or ``(None, response)`` for a bounded JSON body."""
    if not request.is_json:
        return None, _json({"error": "unsupported_media_type"}, 415)
    length = request.content_length
    if length is None:
        return None, _json({"error": "length_required"}, 411)
    if length > limit:
        return None, _json({"error": "too_large"}, 413)
    request.max_content_length = limit
    return request.get_data(cache=False), None
