"""Public presentation preferences; no account or application data writes."""
import hashlib
import json
from functools import lru_cache
from urllib.parse import unquote, urlsplit

from flask import Blueprint, Response, abort, redirect, request, url_for

from app.i18n import LANGUAGE_COOKIE, THEME_COOKIE, LANGUAGES, THEMES, arabic_catalog

appearance_bp = Blueprint("appearance", __name__, url_prefix="/ui")


@lru_cache(maxsize=1)
def catalog_version():
    return hashlib.sha256(json.dumps(arabic_catalog(), ensure_ascii=True, sort_keys=True).encode()).hexdigest()[:16]


@appearance_bp.app_context_processor
def appearance_context():
    return {"ui_catalog_version": catalog_version}


@appearance_bp.after_app_request
def vary_presentation(response):
    if response.mimetype == "text/html":
        response.vary.add("Cookie")
    return response


def safe_return_path(value):
    if not isinstance(value, str) or len(value) > 2048 or not value.startswith("/") or value.startswith("//"):
        return url_for("auth.login")
    if "\\" in value or any(ord(char) < 32 for char in value):
        return url_for("auth.login")
    try:
        parsed = urlsplit(value)
    except ValueError:
        return url_for("auth.login")
    decoded = unquote(parsed.path)
    if decoded.startswith("//") or "\\" in decoded or any(ord(char) < 32 for char in decoded):
        return url_for("auth.login")
    return value if not parsed.scheme and not parsed.netloc else url_for("auth.login")


@appearance_bp.post("/preferences")
def update():
    # Global CSRF protection applies to anonymous and authenticated forms alike.
    language = request.form.get("language")
    theme = request.form.get("theme")
    if language not in LANGUAGES or theme not in THEMES:
        abort(400)
    response = redirect(safe_return_path(request.form.get("next")))
    response.set_cookie(LANGUAGE_COOKIE, language, max_age=31536000, secure=request.is_secure,
                        httponly=True, samesite="Lax", path="/")
    response.set_cookie(THEME_COOKIE, theme, max_age=31536000, secure=request.is_secure,
                        httponly=False, samesite="Lax", path="/")
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@appearance_bp.get("/arabic.js")
def arabic_script():
    # A cacheable, public dictionary contains only owned UI text, never user data.
    payload = "window.AELMS_AR=" + json.dumps(arabic_catalog(), ensure_ascii=True, separators=(",", ":")) + ";"
    response = Response(payload, content_type="application/javascript; charset=utf-8")
    response.headers["Cache-Control"] = "public, max-age=86400"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.set_etag(hashlib.sha256(payload.encode()).hexdigest())
    return response.make_conditional(request)
