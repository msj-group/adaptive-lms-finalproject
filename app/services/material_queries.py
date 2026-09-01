"""Query-only helpers for Lesson-owned Materials (M12).

Flask-independent: no ``request`` / ``flash`` / route decorators -- plain
functions over the ORM, mirroring ``app/services/unit_queries.py``
(active/archived Materials follow the *same* active-only-ordering,
skip-archived-on-move pattern as M10 Units -- not the M11 Lesson
draft/published pattern, where every sibling participates in ordering).
Nothing here takes a lock or writes; the Teacher write path in
``app/blueprints/teacher/materials.py`` does the locking and re-checks
these against the locked rows.
"""

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import AcademicStatus, LessonStatus, Material, MaterialKind
from app.services.material_content import sanitize_rich_text_html

_ACTIVE = AcademicStatus.ACTIVE.value
_RICH_TEXT = MaterialKind.RICH_TEXT.value
_EXTERNAL_LINK = MaterialKind.EXTERNAL_LINK.value
_FILE = MaterialKind.FILE.value
_PUBLISHED = LessonStatus.PUBLISHED.value


def next_material_display_order(lesson_id):
    """The ``display_order`` a newly created or reactivated Material
    should take: strictly after the Lesson's current highest order
    (active and archived), or ``1`` for the first Material --
    ``display_order`` is positive (``>= 1``), never ``0``. Gaps are
    acceptable, so this never renumbers existing rows.
    """
    highest = (
        db.session.query(func.max(Material.display_order))
        .filter(Material.lesson_id == lesson_id)
        .scalar()
    )
    return 1 if highest is None else highest + 1


def active_materials_ordered(lesson_id):
    """Every ACTIVE Material of the Lesson in display order
    (``display_order`` then ``id`` as a stable tie-break). Archived
    Materials are excluded -- they do not participate in active
    reordering."""
    return (
        Material.query.filter_by(lesson_id=lesson_id, status=_ACTIVE)
        .options(joinedload(Material.uploaded_file))
        .order_by(Material.display_order, Material.id)
        .all()
    )


def archived_materials_ordered(lesson_id):
    """Every ARCHIVED Material of the Lesson, in their stored order."""
    return (
        Material.query.filter(Material.lesson_id == lesson_id, Material.status != _ACTIVE)
        .options(joinedload(Material.uploaded_file))
        .order_by(Material.display_order, Material.id)
        .all()
    )


def material_by_nonce(creation_nonce):
    """The Material created by a given signed-token nonce, or ``None``.
    Used to resolve an ordinary or concurrent replay of a create request
    to the single already-created Material (Part M12 section 7)."""
    return Material.query.filter_by(creation_nonce=creation_nonce).first()


def _teacher_material_row(material):
    """Plain presentation dict for one Material on the Teacher list page
    -- public identifiers only, no internal id, no storage key/path."""
    row = {
        "public_id": material.public_id,
        "title": material.title,
        "kind": material.kind,
        "status": material.status,
    }
    if material.kind == _EXTERNAL_LINK:
        row["external_url"] = material.external_url
    elif material.kind == _FILE:
        uploaded = material.uploaded_file
        row["category"] = uploaded.category
        row["extension"] = uploaded.extension
        row["original_filename"] = uploaded.original_filename
        row["byte_size"] = uploaded.byte_size
    return row


def teacher_lesson_materials_view(group, unit, lesson):
    """Plain, eager-loaded presentation data for the Teacher materials
    list page (Part M12 review finding 6): a context dict of display
    strings + public ids, plus the active and archived Materials each as
    plain dicts. The template navigates **no** ORM relationship.

    Bounded query count regardless of Material count: the ancestor
    strings come from the already-eager-loaded ``group`` (M10/M11
    ``_teacher_group_or_404`` joinedloads course/level/term), and the two
    Material lists are one query each (``uploaded_file`` joined in).
    """
    context = {
        "group_name": group.name,
        "group_public_id": group.public_id,
        "unit_title": unit.title,
        "unit_public_id": unit.public_id,
        "unit_active": unit.status == _ACTIVE,
        "lesson_title": lesson.title,
        "lesson_public_id": lesson.public_id,
        "lesson_published": lesson.status == _PUBLISHED,
        "course_title": group.course.title,
        "level_name": group.course.level.name,
        "term_name": group.academic_term.name,
    }
    active = [_teacher_material_row(m) for m in active_materials_ordered(lesson.id)]
    archived = [_teacher_material_row(m) for m in archived_materials_ordered(lesson.id)]
    return context, active, archived


def student_visible_materials(lesson_id):
    """The active Materials of an **already-authorized** Lesson, as plain
    view dicts for the Student Lesson page -- one batched, eager-loaded
    query, never one query per Material.

    Rich text is re-sanitised here (defense in depth, Part M12 section 8)
    immediately before being handed to the template, which is the only
    place allowed to mark it safe HTML. No Teacher identity and no
    internal numeric id ever appears in the returned dicts.
    """
    materials = active_materials_ordered(lesson_id)
    out = []
    for material in materials:
        item = {"public_id": material.public_id, "title": material.title, "kind": material.kind}
        if material.kind == _RICH_TEXT:
            item["content_html"] = sanitize_rich_text_html(material.content_html) or ""
        elif material.kind == _EXTERNAL_LINK:
            item["external_url"] = material.external_url
        elif material.kind == _FILE:
            uploaded = material.uploaded_file
            item.update(
                {
                    "category": uploaded.category,
                    "extension": uploaded.extension,
                    "content_type": uploaded.content_type,
                    "original_filename": uploaded.original_filename,
                    "byte_size": uploaded.byte_size,
                }
            )
        out.append(item)
    return out
