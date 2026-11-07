"""Public install metadata; private application content is never offline-cached."""
from flask import Blueprint, jsonify, send_from_directory, current_app, url_for

pwa_bp = Blueprint("pwa", __name__)


@pwa_bp.get("/manifest.webmanifest")
def manifest():
    response = jsonify({
        "id": "/", "name": "Youth Centre LMS", "short_name": "Youth LMS",
        "description": "Your Youth Centre learning workspace",
        "start_url": "/", "scope": "/", "lang": "en",
        "display": "fullscreen", "display_override": ["fullscreen", "standalone"],
        "orientation": "any", "theme_color": "#eaf0fa", "background_color": "#eaf0fa",
        "categories": ["education"],
        "icons": [{"src": url_for("static", filename=f"pwa/icon-clear-{size}.png"),
                   "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"}
                  for size in (192, 512)],
    })
    response.mimetype = "application/manifest+json"
    response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@pwa_bp.get("/service-worker.js")
def service_worker():
    response = send_from_directory(current_app.static_folder, "js/service_worker.js",
                                   mimetype="application/javascript", conditional=True)
    response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
