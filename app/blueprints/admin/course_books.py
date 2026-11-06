"""Administrators manage the private reference books of a Course."""
from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.curriculum import _lock_course, _staged_material_files, _stored_material_file
from app.extensions import db
from app.models import Course, CourseReferenceBook
from app.security.decorators import roles_required
from app.services.actor_authorization import require_current_actor
from app.services.course_books import (BOOK_LABELS, REFERENCE_KINDS, book_page, books_for_course,
                                      download_book, private_render, read_book)
from app.services.course_pdf import course_pdf_config, pdf_page_count
from app.services.curriculum_editor_scope import admin_editor


def _course(public_id):
    return Course.query.filter_by(public_id=public_id).first_or_404()


def _urls(course):
    return lambda action, **values: url_for("admin.course_book_" + action, public_id=course.public_id, **values)


@admin_bp.get("/courses/<public_id>/books")
@roles_required("administrator")
def course_book_list(public_id):
    from app.services.teaching_assets import asset_urls, assets_for_course
    course = _course(public_id)
    books = books_for_course(course.id)
    available_kinds = [kind for kind in BOOK_LABELS if not books.get(kind)]
    replacement = request.args.get("replace")
    if replacement is not None and (replacement not in BOOK_LABELS or not books.get(replacement)):
        abort(404)
    selected = replacement or request.args.get("kind")
    if replacement is None and selected not in available_kinds:
        selected = None
    return private_render("admin/course_books.html", course=course, books=books,
                          book_labels=BOOK_LABELS, book_urls=_urls(course), document=books["main"],
                          available_kinds=available_kinds, selected_kind=selected,
                          replacement=books.get(replacement) if replacement else None,
                          show_upload=bool(replacement) or ("add" in request.args and bool(available_kinds)),
                          teaching_assets=assets_for_course(course.id), resource_urls=asset_urls(course),
                          **admin_editor(public_id).context())


@admin_bp.post("/courses/<public_id>/books")
@roles_required("administrator")
def course_book_save(public_id):
    kind = request.form.get("kind")
    operation = request.form.get("operation")
    revision = request.form.get("revision", type=int)
    if (kind not in BOOK_LABELS or operation not in {"add", "replace"}
            or revision is None or (operation == "add" and revision != 0)
            or (operation == "replace" and revision < 1)):
        abort(400)
    if kind == "main":
        from app.services.curriculum_editor import document_upload
        return document_upload(admin_editor(public_id), return_to_books=True)
    return _save_reference(_course(public_id), kind, request.files.get("document"), revision)


@admin_bp.post("/courses/<public_id>/books/<kind>")
@roles_required("administrator")
def course_book_upload(public_id, kind):
    if kind not in REFERENCE_KINDS:
        abort(404)
    return _save_reference(_course(public_id), kind, request.files.get("book"), request.form.get("revision", type=int))


def _save_reference(course, kind, upload, revision):
    public_id = course.public_id
    course_id, actor_id = course.id, current_user.id
    config = course_pdf_config()
    try:
        if revision is None or revision < 0:
            raise ValueError("Reload the books page before uploading.")
        if not upload or not upload.filename:
            raise ValueError("Choose a PDF book.")
        db.session.rollback()
        with _staged_material_files(config) as stored:
            uploaded = _stored_material_file(config, upload, upload.filename, actor_id, stored)
            count = pdf_page_count(uploaded)
            _lock_course(course_id, actor_id)
            require_current_actor(actor_id, "administrator")
            book = CourseReferenceBook.query.filter_by(course_id=course_id, kind=kind).populate_existing().with_for_update().first()
            if revision != (book.revision if book else 0):
                raise ValueError("This book changed. Reload the page before replacing it.")
            if book is None:
                book = CourseReferenceBook(course_id=course_id, kind=kind, uploaded_file=uploaded, page_count=count)
                db.session.add(book)
            else:
                book.uploaded_file = uploaded
                book.page_count = count
                book.revision += 1
            db.session.commit()
    except (ValueError, IntegrityError, OSError) as error:
        db.session.rollback()
        flash(str(error) if isinstance(error, ValueError) else "The book could not be saved. Try again.", "danger")
    else:
        flash("Teaching book saved.", "success")
    return redirect(url_for("admin.course_book_list", public_id=public_id))


@admin_bp.get("/courses/<public_id>/books/<kind>")
@roles_required("administrator")
def course_book_read(public_id, kind):
    course = _course(public_id)
    return read_book(course, kind, _urls(course))


@admin_bp.get("/courses/<public_id>/books/<kind>/<file_id>/pages/<int:page_number>")
@roles_required("administrator")
def course_book_page(public_id, kind, file_id, page_number):
    return book_page(_course(public_id).id, kind, file_id, page_number)


@admin_bp.get("/courses/<public_id>/books/<kind>/<file_id>/download")
@roles_required("administrator")
def course_book_download(public_id, kind, file_id):
    return download_book(_course(public_id).id, kind, file_id)


@admin_bp.get("/courses/<public_id>/book-media/<material_public_id>")
@roles_required("administrator")
def course_book_material_file(public_id, material_public_id):
    from app.services.course_books import main_material_file
    return main_material_file(_course(public_id).id, material_public_id)


@admin_bp.get("/courses/<public_id>/resources/<material_public_id>")
@roles_required("administrator")
def teaching_asset_read(public_id, material_public_id):
    from app.services.teaching_assets import read_asset
    return read_asset(_course(public_id), material_public_id)


@admin_bp.get("/courses/<public_id>/resources/<material_public_id>/file")
@roles_required("administrator")
def teaching_asset_file(public_id, material_public_id):
    from app.services.teaching_assets import asset_file
    return asset_file(_course(public_id).id, material_public_id)


@admin_bp.get("/courses/<public_id>/resources/<material_public_id>/download")
@roles_required("administrator")
def teaching_asset_download(public_id, material_public_id):
    from app.services.teaching_assets import asset_file
    return asset_file(_course(public_id).id, material_public_id, download=True)


@admin_bp.get("/courses/<public_id>/resources/<material_public_id>/<file_id>/pages/<int:page_number>")
@roles_required("administrator")
def teaching_asset_page(public_id, material_public_id, file_id, page_number):
    from app.services.teaching_assets import asset_page
    return asset_page(_course(public_id).id, material_public_id, file_id, page_number)
