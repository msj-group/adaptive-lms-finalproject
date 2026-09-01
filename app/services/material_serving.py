"""Shared, authorization-agnostic file-serving core for `file` Materials
(M12, section 13).

Both the Teacher and Student blueprints authorize a request *first*
(their own, different rules -- see ``app/blueprints/teacher/materials.py``
and ``app/blueprints/student/materials.py``) and only then call
:func:`serve_uploaded_file` with the already-authorized
:class:`app.models.UploadedFile` row. Nothing here makes an
authorization decision.
"""

from flask import abort, send_file

from app.extensions import db
from app.models import FileAccessAction, FileAccessLog
from app.services.file_storage import open_stored_file
from app.services.material_config import current_material_config

#: Categories safe to render inline in a browser. PDF/DOCX (category
#: "document") always download as an attachment, even via the "open"
#: route -- Part M12 section 13.
INLINE_SAFE_CATEGORIES = {"image", "audio", "video"}


def serve_uploaded_file(uploaded_file, actor_id, force_attachment):
    """Authorize-then-serve an already-authorized `uploaded_file`'s
    bytes, persisting the access-log entry in the same request before
    any bytes are sent.

    Fails closed: a missing physical file 404s (never leaking the
    resolved path), and if the audit log cannot be committed the file is
    **not** served. Supports HTTP Range requests via Flask's
    ``send_file(conditional=True)``, which itself issues exactly one
    request/response cycle per HTTP request -- Range sub-requests each
    call this function once, so exactly one log row is written per
    authorized HTTP request.
    """
    material_config = current_material_config()
    try:
        path = open_stored_file(material_config, uploaded_file.storage_key)
    except FileNotFoundError:
        abort(404)

    as_attachment = force_attachment or uploaded_file.category not in INLINE_SAFE_CATEGORIES
    action = FileAccessAction.DOWNLOAD.value if as_attachment else FileAccessAction.INLINE.value

    db.session.add(
        FileAccessLog(uploaded_file_id=uploaded_file.id, actor_id=actor_id, action=action)
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        abort(500)

    response = send_file(
        path,
        mimetype=uploaded_file.content_type,
        as_attachment=as_attachment,
        download_name=uploaded_file.original_filename,
        conditional=True,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers.pop("Expires", None)
    return response
