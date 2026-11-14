"""Administrator/Teacher book tools; Student Workbook has separate scoped routes."""
from flask import abort, current_app, render_template, request
from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import CourseReferenceBook, Lesson, Material, Unit
from app.models.course_document import CourseDocument
from app.models.group_curriculum import GroupCurriculum
from app.services.course_pdf import serve_pdf_page
from app.services.material_serving import serve_uploaded_file

BOOK_LABELS = {"main": "Main course book", "teacher_book": "Teacher's book", "workbook": "Workbook"}
REFERENCE_KINDS = frozenset({"teacher_book", "workbook"})


def books_for_course(course_id, group=None):
    main = (db.session.get(GroupCurriculum, group.id) if group is not None else None)
    if main is not None and main.course_id != course_id:
        abort(404)
    if main is None:
        main = db.session.get(CourseDocument, course_id)
    return {
        "main": main if main is not None and main.uploaded_file_id else None,
        **{row.kind: row for row in CourseReferenceBook.query.options(joinedload(CourseReferenceBook.uploaded_file))
           .filter_by(course_id=course_id).all()},
    }


def book_or_404(course_id, kind, group=None, file_id=None):
    if kind not in BOOK_LABELS:
        abort(404)
    book = books_for_course(course_id, group).get(kind)
    if (book is None or book.uploaded_file.extension != "pdf"
            or (file_id is not None and book.uploaded_file.public_id != file_id)):
        abort(404)
    return book


def private_render(template, **context):
    response = current_app.make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def read_book(course, kind, urls, group=None):
    book = book_or_404(course.id, kind, group)
    page = request.args.get("page", default=1, type=int)
    if page is None or not 1 <= page <= book.page_count:
        abort(404)
    from app.services.teaching_assets import reference_pins
    if group is not None:
        from collections import defaultdict
        from app.services.course_pdf import pdf_page_sizes
        pages = defaultdict(list)
        if kind == "main":
            pins = _main_materials(course.id, group).filter(Material.pin_page.is_not(None)).all()
            for pin in pins:
                if 1 <= pin.pin_page <= book.page_count:
                    pages[pin.pin_page].append(pin)
            endpoint = "teacher.course_book_material_file"
        else:
            for pin in reference_pins(course.id, book_kind=kind):
                if 1 <= pin["pin_page"] <= book.page_count:
                    pages[pin["pin_page"]].append(pin)
            endpoint = "teacher.teaching_asset_file"
        return private_render("teacher/book_reader.html", course=course, group=group,
            book=book, kind=kind, label=BOOK_LABELS[kind], page=page,
            book_sizes=pdf_page_sizes(book), page_pins=pages, pin_endpoint=endpoint,
            pin_args={"group_public_id":group.public_id}, book_urls=urls, enable_book_notes=True)
    if kind == "main":
        pins = _main_materials(course.id, group).filter(Material.pin_page == page).all()
        pin_endpoint = ("teacher" if group else "admin") + ".course_book_material_file"
    else:
        pins = reference_pins(course.id, page, book_kind=kind)
        pin_endpoint = ("teacher" if group else "admin") + ".teaching_asset_file"
    return private_render("curriculum/book_reader.html", course=course, group=group,
                          book=book, kind=kind, label=BOOK_LABELS[kind], page=page,
                          page_pins=pins, pin_endpoint=pin_endpoint,
                          pin_args={"group_public_id": group.public_id} if group else {"public_id": course.public_id},
                          book_urls=urls, enable_book_notes=True,
                          reader_layout="layouts/portal_base.html" if group else "layouts/admin_base.html")


def book_page(course_id, kind, file_id, page_number, group=None):
    book = book_or_404(course_id, kind, group, file_id)
    return serve_pdf_page(book, page_number, allow_notes=True)


def download_book(course_id, kind, file_id, group=None):
    book = book_or_404(course_id, kind, group, file_id)
    return serve_uploaded_file(book.uploaded_file, current_user.id, True)


def _main_materials(course_id, group=None):
    copy = db.session.get(GroupCurriculum, group.id) if group else None
    if copy and copy.course_id != course_id:
        abort(404)
    scope = Unit.group_id == group.id if copy else Unit.course_id == course_id
    return (Material.query.join(Lesson).join(Unit).options(joinedload(Material.uploaded_file))
            .filter(scope, Unit.status == "active", Material.status == "active", Material.kind == "file"))


def main_material_file(course_id, public_id, group=None):
    material = _main_materials(course_id, group).filter(Material.public_id == public_id).first_or_404()
    if material.uploaded_file.category not in {"audio", "video"}:
        abort(404)
    return serve_uploaded_file(material.uploaded_file, current_user.id, False, course_captions=True)
