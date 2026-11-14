"""Teachers edit only the independent curriculum of an assigned Group."""
from datetime import datetime, timezone

from flask import abort, current_app, flash, redirect, render_template, request
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.units import _teacher_group_or_404
from app.extensions import db
from app.models import Lesson, Material, Unit
from app.models.curriculum_release import LessonRelease
from app.security.decorators import roles_required
from app.services import curriculum_editor as editor
from app.services.curriculum_editor_scope import EditorScope, teacher_editor
from app.services.curriculum_notifications import notify_curriculum_updated, visible_curriculum_groups
from app.services.group_curriculum import prepare_group_curriculum
from app.services.material_serving import serve_uploaded_file


@teacher_bp.post("/groups/<group_public_id>/curriculum/edit")
@roles_required("teacher")
def curriculum_begin(group_public_id):
    group = _teacher_group_or_404(group_public_id)
    mode = request.form.get("editor")
    if mode not in {"division", "content"}:
        abort(400)
    scope = EditorScope(group.course, group)
    try:
        prepare_group_curriculum(scope)
    except (ValueError, IntegrityError, OSError) as error:
        db.session.rollback()
        flash(str(error) if isinstance(error, ValueError) else
              "The group copy could not be prepared. Check that the course files are available.", "danger")
        return redirect(scope.context()["editor_back_url"])
    return redirect(scope.url(mode))


@teacher_bp.route("/groups/<group_public_id>/curriculum/division", methods=["GET", "POST"])
@roles_required("teacher")
def curriculum_division(group_public_id):
    return editor.division(teacher_editor(group_public_id))


@teacher_bp.post("/groups/<group_public_id>/curriculum/document")
@roles_required("teacher")
def curriculum_document_upload(group_public_id):
    return editor.document_upload(teacher_editor(group_public_id))


@teacher_bp.get("/groups/<group_public_id>/curriculum/document/<document_id>/pages/<int:page_number>")
@roles_required("teacher")
def curriculum_document_page(group_public_id, document_id, page_number):
    return editor.document_page(teacher_editor(group_public_id), document_id, page_number)


@teacher_bp.get("/groups/<group_public_id>/curriculum/content")
@roles_required("teacher")
def curriculum_content(group_public_id):
    return editor.content(teacher_editor(group_public_id))


@teacher_bp.post("/groups/<group_public_id>/curriculum/content/<lesson_id>/media")
@roles_required("teacher")
def curriculum_media_save(group_public_id, lesson_id):
    return editor.media_save(teacher_editor(group_public_id), lesson_id)


@teacher_bp.post("/groups/<group_public_id>/curriculum/publish")
@roles_required("teacher")
def curriculum_publish(group_public_id):
    scope = teacher_editor(group_public_id)
    try:
        scope.lock()
        document = editor._document(scope, required=True)
        editor._verify_revision(document)
        units = editor._units(scope)
        if not editor._ready(units):
            raise ValueError("Finish the page division first.")
        drafts = [lesson for unit in units if unit.status == "active"
                  for lesson in unit.lessons if lesson.status == "draft"]
        for lesson in drafts:
            if db.session.get(LessonRelease, (scope.group_id, lesson.id)) is None:
                db.session.add(LessonRelease(group_id=scope.group_id, lesson_id=lesson.id))
            lesson.status = "published"
            lesson.published_at = datetime.now(timezone.utc)
        if drafts:
            document.revision += 1
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(error) if isinstance(error, ValueError) else "The lessons could not be marked ready.", "danger")
    return redirect(scope.url("division"))


def _material(scope, item_id):
    return (Material.query.join(Lesson).join(Unit)
            .filter(scope.unit_scope, Material.public_id == item_id).first_or_404())


@teacher_bp.get("/groups/<group_public_id>/curriculum/material/<item_id>/file")
@roles_required("teacher")
def curriculum_file(group_public_id, item_id):
    material = _material(teacher_editor(group_public_id), item_id)
    if material.kind != "file":
        abort(404)
    return serve_uploaded_file(material.uploaded_file, current_user.id, force_attachment=False, course_captions=True)


@teacher_bp.route("/groups/<group_public_id>/curriculum/material/<item_id>/edit", methods=["GET", "POST"])
@roles_required("teacher")
def curriculum_edit(group_public_id, item_id):
    from app.blueprints.admin.curriculum import _item_values, _snapshot

    scope = teacher_editor(group_public_id)
    material = _material(scope, item_id)
    back = scope.url("content", lesson=request.args.get("return_lesson") or material.lesson.public_id,
                     page=request.args.get("return_page", type=int))
    serializer = URLSafeSerializer(current_app.secret_key, salt="teacher.group-curriculum-material.v1")
    if request.method == "POST":
        try:
            supplied = serializer.loads(request.form.get("snapshot", ""))
            scope.lock()
            material = _material(scope, item_id)
            if supplied != [scope.group_id, _snapshot(material)]:
                raise BadSignature("stale")
            previous = visible_curriculum_groups([scope.course_id], "material", material.id)
            for name, value in _item_values("material", material).items():
                setattr(material, name, value)
            changed = db.session.is_modified(material, include_collections=False)
            saved_id = material.id
            db.session.commit()
            if changed:
                notify_curriculum_updated([scope.course_id], group_id=scope.group_id,
                    kind="material", item_id=saved_id, prior_student_groups=previous)
            return redirect(back)
        except BadSignature:
            db.session.rollback()
            flash("This content changed while you were editing it. Review the current version.", "warning")
            return redirect(request.full_path)
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else "This material could not be saved.", "danger")
    return render_template("teacher/curriculum_material.html", material=material, content_url=back,
        snapshot=serializer.dumps([scope.group_id, _snapshot(material)]), **scope.context())
