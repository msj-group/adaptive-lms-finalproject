"""Create a Group's curriculum once, without editing its Course or history."""
import json
from uuid import uuid4

from sqlalchemy import case, insert, select
from sqlalchemy.orm import selectinload
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Lesson, LessonProgress, Material, Unit, WorkbookLessonPage
from app.models.course_document import CourseDocument
from app.models.curriculum_release import LessonRelease, MaterialRelease, WorkbookLessonRelease
from app.models.group_curriculum import GroupCurriculum
from app.models.teaching_asset import GroupMaterialSource
from app.services.file_storage import open_stored_file

UNIT_FIELDS = ("title", "description", "search_keywords", "page_start", "page_end", "display_order", "status")
LESSON_FIELDS = UNIT_FIELDS + ("published_at",)
MATERIAL_FIELDS = ("title", "kind", "search_keywords", "content_html", "external_url", "status",
                   "display_order", "pin_page", "pin_x", "pin_y", "pin_show_name")


def _source(course_id):
    document = db.session.get(CourseDocument, course_id)
    units = (Unit.query.filter_by(course_id=course_id)
             .options(selectinload(Unit.lessons).selectinload(Lesson.materials).selectinload(Material.uploaded_file))
             .order_by(Unit.id).all())
    data = []
    for unit in units:
        entry = dict(id=unit.id, values={k: getattr(unit, k) for k in UNIT_FIELDS}, lessons=[])
        for lesson in sorted(unit.lessons, key=lambda row: row.id):
            child = dict(id=lesson.id, values={k: getattr(lesson, k) for k in LESSON_FIELDS}, materials=[])
            for material in sorted(lesson.materials, key=lambda row: row.id):
                uploaded = material.uploaded_file
                child["materials"].append(dict(
                    id=material.id, values={k: getattr(material, k) for k in MATERIAL_FIELDS},
                    file=None if uploaded is None else dict(storage_key=uploaded.storage_key,
                        filename=uploaded.original_filename, sha256=uploaded.sha256, byte_size=uploaded.byte_size),
                ))
            entry["lessons"].append(child)
        data.append(entry)
    pdf = None if document is None else dict(uploaded_file_id=document.uploaded_file_id,
                                            page_count=document.page_count, revision=document.revision)
    return dict(pdf=pdf, units=data)


def _snapshot(source):
    return json.dumps(source, default=str, sort_keys=True)


def _unique_title(title, used):
    candidate, number = title, 1
    while candidate.casefold() in used:
        number += 1
        suffix = f" ({number})"
        candidate = title[:150-len(suffix)] + suffix
    used.add(candidate.casefold())
    return candidate


class _CopyExists(Exception):
    pass


def prepare_group_curriculum(scope):
    """POST-only caller. Stage immutable file copies before the canonical lock.

    Recheck the source after Course -> Group -> Teacher -> Assignment locks.
    The Group lock serializes completion/publication and prevents a second copy.
    Existing legacy Group units stay in place. Progress identities are never
    retargeted: current snapshots are copied while original history is retained.
    """
    from app.blueprints.admin.curriculum import _staged_material_files, _stored_material_file
    from app.blueprints.teacher.units import _authz_broken, _hierarchy_active_error, _lock_unit_chain
    from flask import abort
    from flask_login import current_user
    from app.services.material_config import current_material_config

    actor_id = current_user.id
    if db.session.get(GroupCurriculum, scope.group_id):
        scope.lock()
        db.session.commit()
        return
    source = _source(scope.course_id)
    expected = _snapshot(source)
    db.session.rollback()
    config = current_material_config()
    try:
        with _staged_material_files(config) as stored:
            uploads = {}
            for unit in source["units"]:
                for lesson in unit["lessons"]:
                    for material in lesson["materials"]:
                        meta = material["file"]
                        if meta:
                            with open(open_stored_file(config, meta["storage_key"]), "rb") as stream:
                                uploaded = _stored_material_file(config, FileStorage(stream=stream),
                                                                 meta["filename"], actor_id, stored)
                            if uploaded.sha256 != meta["sha256"] or uploaded.byte_size != meta["byte_size"]:
                                raise ValueError("A course file changed. Reload the group and try again.")
                            uploads[material["id"]] = uploaded
            hierarchy, group, teacher, assignment, _ = _lock_unit_chain(
                scope.group_public_id, scope.term_id, scope.level_id, scope.course_id, actor_id
            )
            if _authz_broken(group, teacher, assignment):
                abort(404)
            error = _hierarchy_active_error(hierarchy, group, scope.term_id, scope.level_id, scope.course_id)
            if error:
                raise ValueError(error)
            if db.session.get(GroupCurriculum, group.id):
                raise _CopyExists
            if _snapshot(_source(scope.course_id)) != expected:
                raise ValueError("The main course changed. Open the editor again to copy its latest version.")
            pdf = source["pdf"]
            db.session.add(GroupCurriculum(group_id=group.id, course_id=scope.course_id,
                uploaded_file_id=pdf["uploaded_file_id"] if pdf else None,
                page_count=pdf["page_count"] if pdf else None))
            lesson_releases = {r.lesson_id: r for r in LessonRelease.query.filter_by(group_id=group.id)}
            workbook_releases = {r.lesson_id: r for r in WorkbookLessonRelease.query.filter_by(group_id=group.id)}
            material_releases = {r.material_id: r for r in MaterialRelease.query.filter_by(group_id=group.id)}
            titles = {title.casefold() for (title,) in db.session.query(Unit.title).filter_by(group_id=group.id)}
            lesson_ids = {}
            for unit in source["units"]:
                values = {**unit["values"], "title": _unique_title(unit["values"]["title"], titles)}
                copied_unit = Unit(group_id=group.id, **values)
                db.session.add(copied_unit)
                db.session.flush()
                for lesson in unit["lessons"]:
                    copied_lesson = Lesson(unit_id=copied_unit.id, **lesson["values"])
                    db.session.add(copied_lesson)
                    db.session.flush()
                    lesson_ids[lesson["id"]] = copied_lesson.id
                    release = lesson_releases.get(lesson["id"])
                    # Group-owned lessons normally default to visible. An explicit
                    # row retains the shared source's hidden/scheduled/published state.
                    db.session.add(LessonRelease(group_id=group.id, lesson_id=copied_lesson.id,
                        opens_at=release.opens_at if release else None,
                        notice_key=release.notice_key if release else None))
                    workbook_release = workbook_releases.get(lesson["id"])
                    if workbook_release is not None:
                        db.session.add(WorkbookLessonRelease(group_id=group.id, lesson_id=copied_lesson.id,
                            opens_at=workbook_release.opens_at, notice_key=workbook_release.notice_key))
                    for page in WorkbookLessonPage.query.filter_by(lesson_id=lesson["id"]):
                        db.session.add(WorkbookLessonPage(lesson_id=copied_lesson.id,
                            page_number=page.page_number, course_id=page.course_id,
                            book_kind=page.book_kind, book_revision=page.book_revision))
                    for material in lesson["materials"]:
                        copied_material = Material(lesson_id=copied_lesson.id, creation_nonce=uuid4().hex,
                                                   **material["values"])
                        if material["file"]:
                            copied_material.uploaded_file = uploads[material["id"]]
                        db.session.add(copied_material)
                        db.session.flush()
                        db.session.add(GroupMaterialSource(material_id=copied_material.id,
                                                           course_material_id=material["id"]))
                        release = material_releases.get(material["id"])
                        if release:
                            db.session.add(MaterialRelease(group_id=group.id, material_id=copied_material.id,
                                opens_at=release.opens_at, notice_key=release.notice_key))
            db.session.flush()
            if lesson_ids:
                # Snapshot existing episode-bound state, including closed episodes;
                # this is not a new Student opening/completion and emits no activity.
                # Original rows, immutable episode IDs and timestamps remain intact.
                progress = LessonProgress.__table__
                columns = ("enrollment_id", "student_id", "group_id", "lesson_id", "created_at",
                           "completed_at", "last_opened_at", "version")
                db.session.execute(insert(progress).from_select(columns, select(
                    progress.c.enrollment_id, progress.c.student_id, progress.c.group_id,
                    case(lesson_ids, value=progress.c.lesson_id), progress.c.created_at,
                    progress.c.completed_at, progress.c.last_opened_at, progress.c.version,
                ).where(progress.c.group_id == group.id, progress.c.lesson_id.in_(lesson_ids))))
            db.session.commit()
    except _CopyExists:
        db.session.rollback()
