"""Read an optional WebVTT companion only after the media was authorized."""
import re

from flask import abort, send_file

from app.services.material_config import current_material_config


def serve_caption(uploaded_file):
    if uploaded_file.category not in {"audio", "video"} or not re.fullmatch("[a-f0-9]{64}", uploaded_file.sha256 or ""):
        abort(404)
    root = current_material_config().storage_root.resolve()
    path = root / "course-captions-v1" / (uploaded_file.sha256 + ".vtt")
    if path.resolve().parent != root / "course-captions-v1" or not path.is_file():
        return "", 204, {"Cache-Control": "private, no-store", "Vary": "Cookie"}
    response = send_file(path, mimetype="text/vtt", conditional=False, max_age=0)
    response.headers.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    response.vary.add("Cookie")
    return response
