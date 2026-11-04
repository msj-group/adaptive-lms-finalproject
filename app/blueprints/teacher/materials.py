"""Read-only teacher materials and private file access."""
from flask import abort, current_app, render_template
from flask_login import current_user

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.lessons import _lesson_for_unit_or_404
from app.blueprints.teacher.units import _archived_chain_labels, _group_is_operational, _teacher_group_or_404, _unit_for_group_or_404
from app.models import Material, UserRole
from app.security.decorators import roles_required
from app.services.file_storage import delete_stored_file
from app.services.material_queries import teacher_lesson_materials_view
from app.services.material_serving import serve_uploaded_file

_ACTIVE = "active"
_FILE = "file"


def _material_for_lesson_or_404(lesson, material_public_id):
    """A Material by its own public_id, constrained to `lesson`. A
    Material public_id valid only for another Lesson 404s here."""
    return Material.query.filter_by(
        public_id=material_public_id, lesson_id=lesson.id
    ).first_or_404()


@teacher_bp.get("/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/materials")
@roles_required(UserRole.TEACHER.value)
def lesson_materials(group_public_id, unit_public_id, lesson_public_id):
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id, include_shared=True)
    lesson = _lesson_for_unit_or_404(unit, lesson_public_id)
    operational = _group_is_operational(group)
    unit_active = unit.status == _ACTIVE
    ctx, active_materials, archived_materials = teacher_lesson_materials_view(group, unit, lesson)
    return render_template(
        "teacher/materials/list.html",
        ctx=ctx,
        active_materials=active_materials,
        archived_materials=archived_materials,
        operational=operational,
        unit_active=unit_active,
        archived_labels=[]
        if operational and unit_active
        else _archived_chain_labels(group) + ([] if unit_active else ["unit"]),
    )


def _cleanup_orphan_upload(material_config, storage_key):
    """Attempt containment-checked deletion of a just-stored file whose
    Material was **not** committed (a handled failure, not the documented
    process-crash reconciliation gap).

    Never raises into the response. ``file_storage._safe_unlink`` already
    logs any OS-level deletion failure with a traceback; this adds one
    request-context line naming the now-unreferenced key so an operator
    can reconcile it, and swallows the unexpected
    ``StorageContainmentError`` (a bad key would be a programming error,
    not user input).
    """
    try:
        removed = delete_stored_file(material_config, storage_key)
    except Exception:
        current_app.logger.exception(
            "Orphan-upload cleanup raised for stored key %s", storage_key
        )
        return
    if not removed:
        current_app.logger.error(
            "Orphaned upload file could not be deleted (stored key %s) after a failed "
            "Material create; it is now unreferenced and needs manual reconciliation",
            storage_key,
        )


def _serve_material_file(material, force_attachment):
    """Authorize-then-serve a `file` Material's bytes via the shared
    ``material_serving`` core. Fails closed (no bytes served) if the
    Material is not a `file` Material, if the physical file is missing,
    or if the audit log cannot be committed. Never exposes the resolved
    filesystem path."""
    if material.kind != _FILE or material.uploaded_file is None:
        abort(404)
    return serve_uploaded_file(material.uploaded_file, current_user.id, force_attachment)


@teacher_bp.get(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>"
    "/materials/<material_public_id>/open"
)
@roles_required(UserRole.TEACHER.value)
def material_open(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id, include_shared=True)
    lesson = _lesson_for_unit_or_404(unit, lesson_public_id)
    material = _material_for_lesson_or_404(lesson, material_public_id)
    return _serve_material_file(material, force_attachment=False)


@teacher_bp.get(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>"
    "/materials/<material_public_id>/download"
)
@roles_required(UserRole.TEACHER.value)
def material_download(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id, include_shared=True)
    lesson = _lesson_for_unit_or_404(unit, lesson_public_id)
    material = _material_for_lesson_or_404(lesson, material_public_id)
    return _serve_material_file(material, force_attachment=True)
