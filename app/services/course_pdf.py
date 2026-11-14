"""Read private course PDFs and render individual authorized pages as images."""
from dataclasses import replace
from functools import lru_cache
from io import BytesIO
from math import isfinite
from threading import Lock

import pypdfium2 as pdfium
from flask import abort, request, send_file

from app.extensions import db
from app.models.course_document import CourseDocument
from app.models.group_curriculum import GroupCurriculum
from app.services.file_storage import open_stored_file
from app.services.material_config import MULTIPART_OVERHEAD_BYTES, current_material_config


DOCUMENT_MAX_BYTES = 500 * 1024 * 1024
DOCUMENT_REQUEST_BYTES = DOCUMENT_MAX_BYTES + MULTIPART_OVERHEAD_BYTES
_MAX_PAGES = 1000
# PDFium's C API is not thread-safe, even for calls on different documents.
_PDFIUM_LOCK = Lock()


def course_pdf_config():
    config = current_material_config()
    return replace(
        config, allowed_extensions=frozenset({"pdf"}),
        max_bytes_by_category={**config.max_bytes_by_category, "document": DOCUMENT_MAX_BYTES},
    )


def pdf_page_count(uploaded):
    path = open_stored_file(current_material_config(), uploaded.storage_key)
    try:
        with _PDFIUM_LOCK, pdfium.PdfDocument(path) as document:
            count = len(document)
            if 1 <= count <= _MAX_PAGES:
                return count
    except pdfium.PdfiumError as error:
        raise ValueError("Choose a readable PDF without password protection.") from error
    except MemoryError as error:
        raise ValueError("This PDF is too complex to process. Choose a smaller PDF.") from error
    raise ValueError(f"The PDF must have between 1 and {_MAX_PAGES} pages.")


def lesson_document(lesson):
    if lesson.page_start is None:
        return None
    document = (db.session.get(GroupCurriculum, lesson.unit.group_id) if lesson.unit.group_id
                else db.session.get(CourseDocument, lesson.unit.course_id))
    if document is None or not document.uploaded_file_id or lesson.page_end > document.page_count:
        return None
    return document


@lru_cache(maxsize=16)
def _page_sizes(path):
    with _PDFIUM_LOCK, pdfium.PdfDocument(path) as pdf:
        sizes = []
        for index in range(len(pdf)):
            try:
                width, height = pdf.get_page_size(index)
                if isfinite(width) and isfinite(height) and width > 0 and height > 0:
                    sizes.append((max(1, round(width)), max(1, round(height))))
                    continue
            except pdfium.PdfiumError:
                pass
            sizes.append((800, 1100))
        return tuple(sizes)


def pdf_page_sizes(document):
    try:
        path = open_stored_file(current_material_config(), document.uploaded_file.storage_key)
        return _page_sizes(path)
    except (OSError, pdfium.PdfiumError):
        return ()


def serve_pdf_page(document, page_number, *, thumbnail=False, allow_notes=False):
    if not 1 <= page_number <= document.page_count:
        abort(404)
    if allow_notes and request.args.get("notes") == "1":
        from app.services.book_annotations import serve_book_annotations
        return serve_book_annotations(document, page_number)
    if request.method not in {"GET", "HEAD"}:
        abort(400)
    try:
        path = open_stored_file(current_material_config(), document.uploaded_file.storage_key)
        output = BytesIO()
        with _PDFIUM_LOCK, pdfium.PdfDocument(path) as pdf:
            page = pdf[page_number - 1]
            try:
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    abort(404)
                max_edge = 220 if thumbnail else 1600
                bitmap = page.render(scale=max_edge / max(width, height), draw_annots=False)
                try:
                    with bitmap.to_pil() as image:
                        image.save(output, format="JPEG", quality=88)
                finally:
                    bitmap.close()
            finally:
                page.close()
        output.seek(0)
    except MemoryError:
        abort(503)
    except (OSError, ValueError, IndexError, pdfium.PdfiumError):
        abort(404)
    response = send_file(output, mimetype="image/jpeg", conditional=False)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.vary.add("Cookie")
    return response
