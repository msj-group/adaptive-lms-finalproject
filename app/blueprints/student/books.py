"""Own-enrollment Workbook pages follow the same native Lesson releases."""
from collections import defaultdict

from flask import abort, redirect, url_for, current_app
from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.extensions import db
from app.models import CourseReferenceBook, UploadedFile, WorkbookLessonPage, Lesson, Unit
from app.models.teaching_asset import TeachingAsset, TeachingAssetPin
from app.security.decorators import roles_required
from app.services.course_pdf import serve_pdf_page
from app.services.material_serving import serve_uploaded_file
from app.services.student_lessons import student_outline_group, outline_units, student_lesson_detail
from app.services.lesson_progress_queries import build_lesson_progress_view
from app.services.lesson_progress_tokens import ACTION_COMPLETE, ACTION_UNDO, make_progress_token


def workbook_context(student_id, group_public_id, document_id=None):
    # Recheck active enrollment and the complete academic hierarchy for
    # every HTML, image and media request. Book kind is never user-selected.
    group = student_outline_group(student_id, group_public_id)
    if group is None:
        abort(404)
    book = (CourseReferenceBook.query.options(joinedload(CourseReferenceBook.uploaded_file))
            .filter_by(course_id=group.course_id, kind="workbook").first())
    if (book is None or book.uploaded_file.extension != "pdf"
            or (document_id is not None and book.uploaded_file.public_id != document_id)):
        abort(404)
    units = outline_units(group.id, student_id)
    visible = {lesson["public_id"] for unit in units for lesson in unit["lessons"]}
    rows = (db.session.query(WorkbookLessonPage.page_number, Lesson.public_id)
            .join(Lesson, Lesson.id == WorkbookLessonPage.lesson_id)
            .join(Unit, Unit.id == Lesson.unit_id)
            .filter(WorkbookLessonPage.course_id == group.course_id,
                    WorkbookLessonPage.book_kind == "workbook",
                    WorkbookLessonPage.book_revision == book.revision,
                    WorkbookLessonPage.page_number <= book.page_count,
                    Unit.in_group(group.id, group.course_id)).all())
    owners = defaultdict(set)
    for page_number, lesson_id in rows:
        owners[page_number].add(lesson_id)
    # A shared physical page is withheld until ALL its mapped Lessons are
    # visible. Rendering another part of that page must not bypass a release.
    allowed = {page_number for page_number, lessons in owners.items() if lessons <= visible}
    pages = defaultdict(list)
    for page_number, lesson_id in rows:
        if page_number in allowed:
            pages[lesson_id].append(page_number)
    for lesson_id in pages:
        pages[lesson_id] = sorted(set(pages[lesson_id]))
    outline = [dict(unit, lessons=[lesson for lesson in unit["lessons"] if pages[lesson["public_id"]]])
               for unit in units]
    outline = [unit for unit in outline if unit["lessons"]]
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
    group, book, units, pages, _ = workbook_context(current_user.id, group_public_id)
    page = student_lesson_detail(current_user.id, group_public_id, unit_public_id, lesson_public_id)
    selected = pages.get(lesson_public_id, [])
    if page is None or not selected:
        abort(404)
    pins = {}
    for pin, asset in _workbook_media(group.course_id, selected).order_by(TeachingAssetPin.id):
        pins.setdefault(pin.page_number, []).append(dict(
            public_id=asset.public_id, category=asset.uploaded_file.category,
            anchor_id=f"{asset.public_id}-{pin.page_number}-{pin.pin_x}-{pin.pin_y}",
            original_filename=asset.title, pin_x=pin.pin_x, pin_y=pin.pin_y))
    links = [dict(lesson, unit_public_id=unit["public_id"]) for unit in units for lesson in unit["lessons"]]
    index = next(i for i, lesson in enumerate(links) if lesson["public_id"] == lesson_public_id)
    progress = page.progress
    action = ACTION_UNDO if progress.completed_at is not None else ACTION_COMPLETE
    view = dict(page.view, course_document=book, materials=[], page_start=selected[0], page_end=selected[-1])
    return private_no_store("student/learning/lesson.html", **view,
        reader_book="workbook", reader_lesson_endpoint="student.workbook_lesson",
        workbook_pages=selected, workbook_pins=pins, lesson_units=units,
        next_lesson=links[index+1] if index+1 < len(links) else None,
        progress=build_lesson_progress_view(progress, current_app.config.get("APP_TIMEZONE", "UTC")),
        progress_action=action,
        progress_state=make_progress_token(current_user.public_id, progress.group_public_id,
            progress.unit_public_id, progress.lesson_public_id, action, progress.version))


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
