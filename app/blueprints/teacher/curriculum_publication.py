"""Teacher publication decisions apply only to the selected teaching group."""
from datetime import datetime
from hashlib import sha256
from uuid import uuid4

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.units import (
    _authz_broken, _group_is_operational, _hierarchy_active_error, _lock_unit_chain,
    _teacher_group_or_404,
)
from app.extensions import db
from app.models import CourseReferenceBook, WorkbookLessonPage, Lesson, Unit, UserRole
from app.models.curriculum_release import MaterialRelease
from app.security.decorators import roles_required
from app.services.curriculum_notifications import notify_curriculum_hidden
from app.services.curriculum_visibility import curriculum_now, lesson_release_model
from app.services.schedule_occurrences import from_app_local, to_app_local


def _catalogue(group, book):
    units = (
        Unit.query.filter(Unit.in_group(group.id, group.course_id), Unit.status == "active")
        .options(selectinload(Unit.lessons).selectinload(Lesson.materials))
        .order_by(Unit.display_order, Unit.id).all()
    )
    mapped = None
    if book == "workbook":
        reference = db.session.get(CourseReferenceBook, (group.course_id, "workbook"))
        mapped = set(db.session.scalars(db.select(WorkbookLessonPage.lesson_id).where(
            WorkbookLessonPage.course_id == group.course_id,
            WorkbookLessonPage.book_kind == "workbook",
            WorkbookLessonPage.book_revision == reference.revision,
            WorkbookLessonPage.page_number <= reference.page_count))) if reference else set()
    return [
        (unit, [
            (lesson, sorted((m for m in lesson.materials if m.status == "active"),
                            key=lambda m: (m.display_order, m.id)) if book == "main" else [])
            for lesson in sorted(unit.lessons, key=lambda l: (l.display_order, l.id))
            if lesson.status == "published" and (mapped is None or lesson.id in mapped)
        ])
        for unit in units
    ]


def _release_rows(group_id, book):
    return (
        {row.lesson_id: row for row in lesson_release_model(book).query.filter_by(group_id=group_id)},
        {row.material_id: row for row in MaterialRelease.query.filter_by(group_id=group_id)} if book == "main" else {},
    )


def _snapshot(catalogue, release_rows, book, group):
    lessons, materials = release_rows
    digest = sha256(book.encode())
    if book == "workbook":
        reference = db.session.get(CourseReferenceBook, (group.course_id, "workbook"))
        digest.update(repr((reference.revision, reference.uploaded_file_id, reference.page_count)
                           if reference else None).encode())
        lesson_ids = [lesson.id for _, entries in catalogue for lesson, _ in entries]
        pages = db.session.query(WorkbookLessonPage.lesson_id, WorkbookLessonPage.page_number).filter(
            WorkbookLessonPage.lesson_id.in_(lesson_ids), WorkbookLessonPage.course_id == group.course_id,
            WorkbookLessonPage.book_kind == "workbook",
            WorkbookLessonPage.book_revision == reference.revision).order_by(
                WorkbookLessonPage.lesson_id, WorkbookLessonPage.page_number).all() if reference else []
        digest.update(repr([tuple(row) for row in pages]).encode())
    for unit, entries in catalogue:
        for lesson, children in entries:
            row = lessons.get(lesson.id)
            digest.update(repr((unit.public_id, lesson.public_id, lesson.status,
                                row.opens_at if row else None, row.notice_key if row else None)).encode())
            for material in children:
                row = materials.get(material.id)
                digest.update(repr((material.public_id, row.opens_at if row else None,
                                    row.notice_key if row else None)).encode())
    return digest.hexdigest()


def _state(row, now, tz_name, fallback):
    if row is None:
        return fallback
    if row.opens_at is None:
        return "Hidden"
    if row.opens_at <= now:
        return "Published"
    return "Scheduled for " + to_app_local(tz_name, row.opens_at).strftime("%d %b %Y %H:%M")


def _save_release(model, key, item_id, rows, group_id, opens_at, now, notice_key, fallback):
    row = rows.get(item_id)
    if row is None and (
        (opens_at == now and fallback != "Hidden") or (opens_at is None and fallback == "Hidden")
    ):
        return 0
    if row and (row.opens_at == opens_at or (
        opens_at == now and row.opens_at is not None and row.opens_at <= now
    )):
        return 0
    if row is None:
        row = model(group_id=group_id, **{key: item_id})
        db.session.add(row)
    row.opens_at = opens_at
    row.notice_key = notice_key if opens_at is not None else None
    return 1


def _apply(group, catalogue, release_rows, action, tz_name, book):
    now = curriculum_now()
    opens_at = now
    if action == "hide":
        opens_at = None
    elif action == "schedule":
        value = request.form.get("opens_at", "")
        try:
            opens_at = from_app_local(tz_name, datetime.strptime(value, "%Y-%m-%dT%H:%M"))
        except ValueError:
            raise ValueError("Enter a valid date and time in the displayed timezone.") from None
        if opens_at <= now:
            raise ValueError("Schedule the publication for a future time.")

    selected_units = set(request.form.getlist("unit"))
    selected_lessons = set(request.form.getlist("lesson"))
    selected_materials = set(request.form.getlist("material"))
    available_units = {unit.public_id for unit, entries in catalogue if entries}
    available_lessons = {lesson.public_id for _, entries in catalogue for lesson, _ in entries}
    available_materials = {m.public_id for _, entries in catalogue for _, children in entries for m in children}
    if action != "publish_all" and not (
        (selected_units or selected_lessons or selected_materials)
        and selected_units <= available_units and selected_lessons <= available_lessons
        and selected_materials <= available_materials
    ):
        raise ValueError("Select units, lessons or materials in this group.")

    all_items = action == "publish_all"
    lesson_rows, material_rows = release_rows
    notice_key = str(uuid4())
    count = 0
    for unit, entries in catalogue:
        for lesson, children in entries:
            if all_items or unit.public_id in selected_units or lesson.public_id in selected_lessons:
                fallback = "Published" if book == "main" and unit.group_id is not None and lesson.status == "published" else "Hidden"
                count += _save_release(lesson_release_model(book), "lesson_id", lesson.id, lesson_rows, group.id,
                                       opens_at, now, notice_key, fallback)
            for material in children:
                if material.public_id in selected_materials or (all_items and material.id in material_rows):
                    count += _save_release(MaterialRelease, "material_id", material.id, material_rows, group.id,
                                           opens_at, now, notice_key, "Follows lesson")
    return count


@teacher_bp.route("/groups/<group_public_id>/curriculum/publication", methods=["GET", "POST"])
@roles_required(UserRole.TEACHER.value)
def curriculum_publication(group_public_id):
    book = request.form.get("book", "main") if request.method == "POST" else request.args.get("book", "main")
    if book not in {"main", "workbook"}:
        abort(404)
    group = _teacher_group_or_404(group_public_id)
    tz_name = current_app.config["APP_TIMEZONE"]
    if request.method == "POST":
        destination = url_for("teacher.curriculum_publication", group_public_id=group_public_id, book=book)
        action = request.form.get("action")
        if action not in {"publish_all", "publish", "schedule", "hide"}:
            abort(400)
        term_id, level_id, course_id = group.academic_term_id, group.course.level_id, group.course_id
        hierarchy, group, teacher, assignment, _ = _lock_unit_chain(
            group_public_id, term_id, level_id, course_id, current_user.id
        )
        if _authz_broken(group, teacher, assignment):
            db.session.rollback()
            abort(404)
        blocked = _hierarchy_active_error(hierarchy, group, term_id, level_id, course_id)
        try:
            if blocked:
                raise ValueError(blocked)
            catalogue = _catalogue(group, book)
            rows = _release_rows(group.id, book)
            if request.form.get("snapshot") != _snapshot(catalogue, rows, book, group):
                raise ValueError("Publication changed while this page was open. Review the current selections.")
            count = _apply(group, catalogue, rows, action, tz_name, book)
            saved_group_id = group.id
            db.session.commit()
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else "The publication could not be saved.", "danger")
        else:
            if count and action == "hide":
                notify_curriculum_hidden(saved_group_id, book=book)
            flash(f"Updated visibility for {count} items." if count else "The selected visibility is already set.",
                  "success")
        return redirect(destination)

    catalogue = _catalogue(group, book)
    rows = _release_rows(group.id, book)
    now = curriculum_now()
    lesson_states = {
        lesson.id: _state(rows[0].get(lesson.id), now, tz_name,
                          "Published" if book == "main" and unit.group_id is not None and lesson.status == "published" else "Hidden")
        for unit, entries in catalogue for lesson, _ in entries
    }
    material_states = {m.id: _state(rows[1].get(m.id), now, tz_name, "Follows lesson")
                       for _, entries in catalogue for _, children in entries for m in children}
    return render_template("teacher/curriculum_publication.html", group=group, catalogue=catalogue, book=book,
                           book_label="Workbook" if book == "workbook" else "Student book",
                           snapshot=_snapshot(catalogue, rows, book, group), operational=_group_is_operational(group),
                           lesson_states=lesson_states, material_states=material_states, tz_name=tz_name)
