"""Own-enrollment Workbook pages use independent book publication."""

from flask import abort, redirect, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.extensions import db
from app.models import UploadedFile
from app.models.teaching_asset import TeachingAsset, TeachingAssetPin
from app.security.decorators import roles_required
from app.services.course_pdf import serve_pdf_page
from app.services.material_serving import serve_uploaded_file
from app.services.student_lessons import student_outline_group


def workbook_context(student_id, group_public_id, document_id=None):
    # Recheck active enrollment and the complete academic hierarchy for
    # every HTML, image and media request. Book kind is never user-selected.
    group = student_outline_group(student_id, group_public_id)
    if group is None:
        abort(404)
    from app.services.workbook_access import workbook_access
    context = workbook_access(student_id, group)
    if context is None:
        abort(404)
    book, outline, pages, allowed = context
    if document_id is not None and book.uploaded_file.public_id != document_id:
        abort(404)
    return group, book, outline, pages, allowed


def _workbook_media(course_id, allowed):
    # Only audio/video actually pinned to an authorized Workbook page.
    # Teacher PDFs, answer keys and other documents have no Student route.
    return (db.session.query(TeachingAssetPin, TeachingAsset).join(TeachingAsset,
            (TeachingAsset.id == TeachingAssetPin.asset_id)
            & (TeachingAsset.course_id == TeachingAssetPin.course_id))
            .join(UploadedFile, UploadedFile.id == TeachingAsset.uploaded_file_id)
            .options(joinedload(TeachingAsset.uploaded_file))
            .filter(TeachingAssetPin.course_id == course_id,
                    TeachingAssetPin.book_kind == "workbook",
                    TeachingAssetPin.document_asset_id.is_(None),
                    TeachingAssetPin.page_number.in_(allowed),
                    UploadedFile.category.in_(("audio", "video"))))


@student_bp.get("/groups/<group_public_id>/workbook")
@roles_required("student")
def workbook(group_public_id):
    group, book, units, _, _ = workbook_context(current_user.id, group_public_id)
    destination = next(((unit["public_id"], lesson["public_id"])
        for unit in units for lesson in unit["lessons"]), None)
    if destination:
        response = redirect(url_for("student.workbook_lesson", group_public_id=group.public_id,
            unit_public_id=destination[0], lesson_public_id=destination[1]))
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
        return response
    return private_no_store("student/learning/workbook.html", group=group)


@student_bp.get("/groups/<group_public_id>/workbook/units/<unit_public_id>/lessons/<lesson_public_id>")
@roles_required("student")
def workbook_lesson(group_public_id, unit_public_id, lesson_public_id):
    from app.blueprints.student.unit_reader import unit_reader_context
    _, context = unit_reader_context(current_user.id, group_public_id, unit_public_id, lesson_public_id, 'workbook', actor_public_id=current_user.public_id)
    return private_no_store("student/learning/lesson.html", **context)



@student_bp.get("/groups/<group_public_id>/workbook/<document_id>/pages/<int:page_number>")
@roles_required("student")
def workbook_page(group_public_id, document_id, page_number):
    _, book, _, _, allowed = workbook_context(current_user.id, group_public_id, document_id)
    if page_number not in allowed:
        abort(404)
    return serve_pdf_page(book, page_number)


@student_bp.get("/groups/<group_public_id>/workbook/<document_id>/media/<material_public_id>")
@roles_required("student")
def workbook_media(group_public_id, document_id, material_public_id):
    group, _, _, _, allowed = workbook_context(current_user.id, group_public_id, document_id)
    row = _workbook_media(group.course_id, allowed).filter(
        TeachingAsset.public_id == material_public_id).first()
    if row is None:
        abort(404)
    return serve_uploaded_file(row[1].uploaded_file, current_user.id, False)
