"""Shared PDF editor handlers; every read and write uses an explicit owner scope."""
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.blueprints.admin.curriculum import _clean_text, _staged_material_files, _stored_material_file
from app.extensions import db
from app.i18n import gettext
from app.models import Lesson, Material, Unit
from app.models.curriculum_release import LessonRelease
from app.services.course_pdf import course_pdf_config, pdf_page_count, pdf_page_sizes, serve_pdf_page
from app.services.curriculum_notifications import notify_curriculum_updated, visible_curriculum_groups
from app.services.material_config import current_material_config
from app.services.material_queries import next_material_display_order

def _render(scope, template, **context):
    return render_template(template, **scope.context(),
                           media_limits=current_material_config().max_bytes_by_category, **context)


def _document(scope, *, required=False):
    document = scope.document()
    if required and document is None:
        abort(404)
    return document


def _units(scope):
    return (Unit.query.filter(scope.unit_scope).options(selectinload(Unit.lessons))
            .order_by(Unit.page_start.is_(None), Unit.page_start, Unit.id).all())


def _lesson_order(unit):
    return sorted(unit.lessons, key=lambda lesson: (lesson.page_start is None, lesson.page_start or 0, lesson.id))


def _ready(units):
    active = [unit for unit in units if unit.status == "active"]
    return bool(active) and all(
        unit.page_start is not None and unit.lessons
        and all(lesson.page_start is not None for lesson in unit.lessons)
        for unit in active
    )


def _division_url(scope, **query):
    return scope.url("division", **query)


def _content_url(scope, lesson=None, *, marker=None):
    return scope.url("content", lesson=lesson,
                   _anchor=f"material-{marker}" if marker else None)


def _verify_revision(document):
    if request.form.get("revision", type=int) != (document.revision if document else 0):
        raise ValueError("The division changed while you were editing. Review the saved version and try again.")


def _shared_item(scope, kind, item_id, parent_id):
    if kind == "unit":
        item = (Unit.query.filter(scope.unit_scope, Unit.public_id == item_id).first_or_404()
                if item_id else None)
        return item, None
    if kind != "lesson":
        abort(404)
    if item_id:
        item = (Lesson.query.join(Unit).filter(scope.unit_scope, Lesson.public_id == item_id)
                .first_or_404())
        return item, item.unit
    parent = Unit.query.filter(scope.unit_scope, Unit.public_id == parent_id).first_or_404()
    return None, parent


def _page_range(document, scope, kind, item, parent, status):
    first = request.form.get("page_start", type=int)
    last = request.form.get("page_end", type=int)
    if first is None or last is None or not 1 <= first <= last <= document.page_count:
        raise ValueError(f"Enter a page range between 1 and {document.page_count}.")

    if kind == "unit":
        if item and any(lesson.page_start is not None and
                        (lesson.page_start < first or lesson.page_end > last)
                        for lesson in item.lessons):
            raise ValueError("The unit must still contain every saved lesson range.")
        siblings = Unit.query.filter(scope.unit_scope, Unit.status == "active")
    else:
        if parent.page_start is None or not parent.page_start <= first <= last <= parent.page_end:
            raise ValueError("Choose pages inside the saved unit range.")
        if item and Material.query.filter(
            Material.lesson_id == item.id, Material.pin_page.is_not(None),
            ((Material.pin_page < first) | (Material.pin_page > last)),
        ).first():
            raise ValueError("Move the media markers into the new lesson range before changing its pages.")
        siblings = Lesson.query.filter_by(unit_id=parent.id)

    model = Unit if kind == "unit" else Lesson
    if item:
        siblings = siblings.filter(model.id != item.id)
    if status != "archived" and siblings.filter(model.page_start <= last, model.page_end >= first).first():
        raise ValueError("This range overlaps another saved item.")
    return first, last


def division(scope):
    course = scope.course
    course_id = course.id
    if request.method == "POST":
        kind = request.form.get("kind")
        item_id = request.form.get("item")
        parent_id = request.form.get("parent")
        try:
            scope.lock()
            document = _document(scope, required=True)
            _verify_revision(document)
            item, parent = _shared_item(scope, kind, item_id, parent_id)
            previous = visible_curriculum_groups([course_id], kind, item.id) if item else ()
            title = _clean_text(request.form.get("title", ""), 150, "Title", required=True)
            status = request.form.get("status")
            choices = ("active", "archived") if kind == "unit" else ("draft", "published")
            if status not in choices:
                raise ValueError("Choose a valid status.")
            first, last = _page_range(document, scope, kind, item, parent, status)
            if item is None:
                item = (Unit(group_id=scope.group_id) if scope.group_id else Unit(course_id=course_id)) if kind == "unit" else Lesson(unit_id=parent.id)
                db.session.add(item)
            item.title = title
            item.page_start, item.page_end = first, last
            item.display_order = first
            item.status = status
            if kind == "lesson":
                item.published_at = (item.published_at or datetime.now(timezone.utc)) if status == "published" else None
            changed = item in db.session.new or db.session.is_modified(item, include_collections=False)
            if changed:
                document.revision += 1
            db.session.flush()
            if scope.group_id and kind == "lesson" and db.session.get(LessonRelease, (scope.group_id, item.id)) is None:
                db.session.add(LessonRelease(group_id=scope.group_id, lesson_id=item.id))
            changed_id = item.id
            db.session.commit()
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError)
                  else "The division could not be saved. Check that the title is unique.", "danger")
            return redirect(_division_url(scope, kind=kind, item=item_id, parent=parent_id))
        if changed:
            notify_curriculum_updated([course_id], group_id=scope.group_id, kind=kind, item_id=changed_id, prior_student_groups=previous)
        flash("Page range saved.", "success")
        return redirect(_division_url(scope, page=first))

    document = _document(scope)
    units = _units(scope)
    kind = request.args.get("kind")
    item, parent = (None, None)
    if document and kind:
        item, parent = _shared_item(scope, kind, request.args.get("item"), request.args.get("parent"))
    if request.args.get("fragment") == "form":
        if not document or not kind:
            abort(404)
        return _render(scope, "admin/curriculum/_range_form.html", course=course, document=document,
                               kind=kind, item=item, parent=parent)
    page = request.args.get("page", type=int) or (item.page_start if item else None) or (
        parent.page_start if parent else None
    ) or 1
    assigned_pages = sum(
        lesson.page_end - lesson.page_start + 1
        for unit in units if unit.status == "active"
        for lesson in unit.lessons if lesson.page_start is not None
    )
    if document:
        page = max(1, min(page, document.page_count))
    ready = _ready(units)
    has_drafts = any(
        lesson.status == "draft" for unit in units if unit.status == "active" for lesson in unit.lessons
    )
    return _render(scope,
        "admin/curriculum/division.html", course=course, document=document, units=units,
        ready=ready, has_drafts=has_drafts, kind=kind, item=item, parent=parent, lesson_order=_lesson_order,
        assigned_pages=assigned_pages,
        page=page if document else 1, sizes=pdf_page_sizes(document) if document else (),
    )


def document_upload(scope, *, return_to_books=False):
    course_id = scope.course_id
    actor_id = current_user.id
    upload = request.files.get("document")
    config = course_pdf_config()
    try:
        if not upload or not upload.filename:
            raise ValueError("Choose the main PDF for this course.")
        db.session.rollback()
        with _staged_material_files(config) as stored:
            uploaded = _stored_material_file(config, upload, upload.filename, actor_id, stored)
            page_count = pdf_page_count(uploaded)
            scope.lock()
            document = _document(scope)
            _verify_revision(document)
            if document:
                if request.form.get("keep_ranges") != "yes":
                    raise ValueError("Confirm that the existing ranges and media positions refer to the same pages.")
                if Unit.query.filter(scope.unit_scope, Unit.page_end > page_count).first():
                    raise ValueError("The new PDF is shorter than a saved range. Adjust the division first.")
            scope.save_document(uploaded, page_count)
            db.session.commit()
    except (ValueError, IntegrityError, OSError) as error:
        db.session.rollback()
        flash(str(error) if isinstance(error, ValueError) else "The main PDF could not be saved.", "danger")
    else:
        notify_curriculum_updated([course_id], group_id=scope.group_id)
        flash("Main PDF saved.", "success")
    if return_to_books and not scope.group_id:
        return redirect(url_for("admin.course_book_list", public_id=scope.public_id))
    return redirect(_division_url(scope))


def document_page(scope, document_id, page_number):
    document = _document(scope, required=True)
    if document.uploaded_file.public_id != document_id:
        abort(404)
    return serve_pdf_page(document, page_number, thumbnail=request.args.get("thumbnail") == "1")


def content(scope):
    course = scope.course
    document = _document(scope)
    units = _units(scope)
    if not document or not _ready(units):
        flash("Save the unit and lesson ranges to open the content editor.", "warning")
        return redirect(_division_url(scope))
    units = [unit for unit in units if unit.status == "active"]
    lessons = [lesson for unit in units for lesson in _lesson_order(unit)]
    selected = request.args.get("lesson")
    lesson = next((lesson for lesson in lessons if lesson.public_id == selected), None) if selected else lessons[0]
    if lesson is None:
        abort(404)
    preview = request.args.get("preview") == "1"
    requested_page = request.args.get("page", type=int)
    preview_origin = {"return_lesson": selected or "", "return_page": requested_page}
    editor_url = scope.url("content",
                         lesson=request.args.get("return_lesson", selected) or None,
                         page=request.args.get("return_page", type=int))
    included = [lesson] if preview else lessons
    materials = (
        Material.query.filter(Material.lesson_id.in_([row.id for row in included]), Material.status == "active")
        .options(joinedload(Material.uploaded_file)).order_by(Material.display_order, Material.id).all()
    )
    lesson_by_id = {row.id: row for row in included}
    page_pins = defaultdict(list)
    page_owners = {}
    for row in included:
        owner = {
            "save": scope.url("media_save", lesson_id=row.public_id,
                            **preview_origin),
            "preview": scope.url("content", lesson=row.public_id,
                               preview=1, **preview_origin),
        }
        for page in range(row.page_start, row.page_end + 1):
            page_owners[page] = owner
    for material in materials:
        if material.pin_page:
            page_pins[material.pin_page].append(material)
    first, last = (lesson.page_start, lesson.page_end) if preview else (1, document.page_count)
    page = requested_page or (lesson.page_start if selected or preview else 1)
    page = max(first, min(page, last))
    owner = page_owners.get(page)
    return _render(scope,
        "admin/curriculum/content.html", course=course, document=document, units=units,
        lesson=lesson, lesson_order=_lesson_order, materials=materials, nonce=uuid4().hex,
        preview=preview, preview_url=owner["preview"] if owner else None, editor_url=editor_url,
        preview_origin=preview_origin,
        pin_version=_pin_version, page_pins=page_pins,
        page_owners=page_owners, lesson_by_id=lesson_by_id, sizes=pdf_page_sizes(document),
        first=first, last=last, page=page,
    )


def _pin_version(material):
    state = (material.public_id, material.updated_at.isoformat(), material.pin_page,
             material.pin_x, material.pin_y, material.pin_show_name, material.status)
    return sha256(repr(state).encode()).hexdigest()


def _pin_values(lesson):
    page = request.form.get("pin_page", type=int)
    x = request.form.get("pin_x", type=int)
    y = request.form.get("pin_y", type=int)
    if (page is None or x is None or y is None or
            not lesson.page_start <= page <= lesson.page_end or
            not 0 <= x <= 10000 or not 0 <= y <= 10000):
        raise ValueError("Choose a position on a page in this lesson.")
    return dict(pin_page=page, pin_x=x, pin_y=y, pin_show_name=False)


def _available_title(lesson_id, filename):
    stem = Path(filename).stem.strip() or "Media"
    existing = {title.casefold() for (title,) in db.session.query(Material.title).filter_by(lesson_id=lesson_id)}
    title = stem[:150]
    number = 1
    while title.casefold() in existing:
        number += 1
        suffix = f" ({number})"
        title = stem[:150 - len(suffix)] + suffix
    return title


def media_save(scope, lesson_id):
    course_id = scope.course_id
    actor_id = current_user.id
    config = current_material_config()
    material_id = request.form.get("material")
    archive = request.form.get("action") == "archive"
    inline = not archive and request.accept_mimetypes.best == "application/json"
    destination = _content_url(scope, lesson_id)
    try:
        if archive and not material_id:
            abort(404)
        nonce = None if material_id else UUID(hex=request.form.get("nonce", "")).hex
        db.session.rollback()
        with _staged_material_files(config) as stored:
            uploaded = None
            if not material_id:
                upload = request.files.get("media")
                if not upload or not upload.filename:
                    raise ValueError("Choose an audio or video file.")
                uploaded = _stored_material_file(config, upload, upload.filename, actor_id, stored)
                if uploaded.category not in ("audio", "video"):
                    raise ValueError("Choose an audio or video file.")
            scope.lock()
            document = _document(scope, required=True)
            lesson, _ = _shared_item(scope, "lesson", lesson_id, None)
            if nonce:
                material = Material.query.filter_by(lesson_id=lesson.id, creation_nonce=nonce).first()
                if material:
                    if material.status != "active":
                        raise ValueError("This media has been archived. Choose a file to add it again.")
                    # Exit the staged-file context through an exception to clean a retried upload.
                    raise _AlreadySaved
            _verify_revision(document)
            if lesson.unit.status != "active" or not _ready(_units(scope)):
                raise ValueError("Finish the page division before editing this lesson's media.")
            previous = ()
            if material_id:
                material = Material.query.filter_by(
                    lesson_id=lesson.id, public_id=material_id, kind="file", status="active"
                ).first_or_404()
                if material.uploaded_file.category not in ("audio", "video"):
                    abort(404)
                if request.form.get("material_version") != _pin_version(material):
                    raise ValueError("This media changed while you were editing. Try again from the saved version.")
                previous = visible_curriculum_groups([course_id], "material", material.id)
                if archive:
                    material.status = "archived"
                else:
                    for name, value in _pin_values(lesson).items():
                        setattr(material, name, value)
            else:
                material = Material(
                    lesson_id=lesson.id, kind="file", uploaded_file=uploaded,
                    title=_available_title(lesson.id, uploaded.original_filename),
                    display_order=next_material_display_order(lesson.id), creation_nonce=nonce,
                    **_pin_values(lesson),
                )
                db.session.add(material)
            db.session.flush()
            changed_id = material.id
            db.session.commit()
    except _AlreadySaved:
        db.session.rollback()
    except (ValueError, IntegrityError, OSError) as error:
        db.session.rollback()
        message = str(error) if isinstance(error, ValueError) else "The media could not be saved. Try again."
        if inline:
            return jsonify(error=gettext(message)), 400
        flash(message, "danger")
        return redirect(destination)
    else:
        notify_curriculum_updated([course_id], group_id=scope.group_id, kind="material", item_id=changed_id, prior_student_groups=previous)
    if archive:
        flash("Media archived.", "success")
        return redirect(destination)
    if inline:
        origin = {"return_lesson": request.args.get("return_lesson", lesson_id),
                  "return_page": request.args.get("return_page", type=int)}
        fragment = _render(scope,
            "admin/curriculum/_media_update.html", material=material, lesson=lesson, course_id=scope.public_id,
            revision=document.revision, pin_version=_pin_version, origin=origin,
        )
        return jsonify(html=fragment, page=material.pin_page)
    return redirect(_content_url(scope, lesson_id, marker=material.public_id))


class _AlreadySaved(Exception):
    """Stop a duplicate upload without leaving an unlinked storage file."""
