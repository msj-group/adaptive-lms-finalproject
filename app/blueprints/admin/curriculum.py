"""Administrator editing of shared Course curricula."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_

from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models import Course, Group, Lesson, Material, Unit, UploadedFile, User, UserRole
from app.models.curriculum_release import LessonRelease
from app.security.decorators import roles_required
from app.services.course_transactions import lock_course_in_open_transaction_by_id
from app.services.curriculum_notifications import notify_curriculum_updated, visible_curriculum_groups
from app.services.file_storage import delete_stored_file, store_validated_upload
from app.services.material_content import sanitize_rich_text_html, validate_external_url
from app.services.material_serving import serve_uploaded_file
from app.services.search_terms import normalize_search_keywords

_ADMIN = UserRole.ADMINISTRATOR.value
_MODELS = {"unit": Unit, "lesson": Lesson, "material": Material}
_STATUSES = {"unit": ("active", "archived"), "lesson": ("draft", "published"),
             "material": ("active", "archived")}


def _lock_course(course_id, actor_id):
    db.session.rollback()
    course = lock_course_in_open_transaction_by_id(course_id)
    actor = User.query.filter_by(id=actor_id).with_for_update().first()
    if actor is None or actor.role != _ADMIN or not actor.is_active_account():
        abort(403)
    if course is None:
        abort(404)
    return course


def _clean_text(raw, limit, label, *, required=False):
    if not isinstance(raw, str) or "\x00" in raw:
        raise ValueError(f"{label} must be text.")
    value = raw.strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f"{label} must contain {'1 to' if required else 'at most'} {limit} characters.")
    return value or None


def _content_fields(data):
    return {
        "title": _clean_text(data.get("title", ""), 150, "Title", required=True),
        "search_keywords": normalize_search_keywords(_clean_text(data.get("search_keywords", ""), 500, "Search keywords")),
    }


def _description(data):
    return _clean_text(data.get("description", ""), 10000, "Description")


def _rich_text(raw):
    html = sanitize_rich_text_html(_clean_text(raw, 10000, "Material HTML", required=True))
    if not html or len(html.encode("utf-8")) > 65000:
        raise ValueError("Enter usable material text within the storage limit.")
    return html


def _stored_material_file(config, stream, filename, actor_id, stored_keys):
    upload = store_validated_upload(config, stream, filename)
    stored_keys.append(upload.storage_key)
    return UploadedFile(
        storage_key=upload.storage_key, original_filename=upload.original_filename,
        extension=upload.extension, category=upload.category, content_type=upload.content_type,
        byte_size=upload.byte_size, sha256=upload.sha256, uploaded_by_id=actor_id,
    )


@contextmanager
def _staged_material_files(config):
    stored_keys = []
    try:
        yield stored_keys
    except BaseException:
        for key in stored_keys:
            delete_stored_file(config, key)
        raise


def _curriculum_url(course):
    return url_for("admin.course_curriculum", public_id=course.public_id)


@admin_bp.get("/courses/<public_id>/curriculum")
@roles_required(_ADMIN)
def course_curriculum(public_id):
    return redirect(url_for("admin.curriculum_division", public_id=public_id))


@admin_bp.post("/courses/<public_id>/curriculum/publish")
@roles_required(_ADMIN)
def curriculum_publish(public_id):
    from app.services.curriculum_editor import _document, _ready, _units
    from app.services.curriculum_editor_scope import admin_editor

    course = Course.query.filter_by(public_id=public_id).first_or_404()
    course_id = course.id
    _lock_course(course_id, current_user.id)
    scope = admin_editor(public_id)
    document = _document(scope)
    if document is None or not _ready(_units(scope)):
        flash("Finish the page division first.", "warning")
        return redirect(url_for("admin.curriculum_division", public_id=public_id))
    active_units = db.select(Unit.id).where(Unit.course_id == course_id, Unit.status == "active")
    drafts = Lesson.query.filter(Lesson.unit_id.in_(active_units), Lesson.status == "draft")
    count = drafts.update({"status": "published", "published_at": datetime.now(timezone.utc)},
                          synchronize_session=False)
    if count:
        document.revision += 1
    db.session.commit()
    if count:
        notify_curriculum_updated([course_id])
    flash(f"Marked {count} lessons ready for teacher publication.", "success")
    return redirect(url_for("admin.course_curriculum", public_id=public_id))


def _item_query(course_id, kind):
    model = _MODELS[kind]
    query = model.query
    if kind == "material":
        query = query.join(Lesson, Material.lesson_id == Lesson.id)
    if kind != "unit":
        query = query.join(Unit, Lesson.unit_id == Unit.id)
    return query.filter(or_(Unit.course_id == course_id, Unit.group.has(Group.course_id == course_id)))


def _snapshot(item):
    fields = ["public_id", "title", "status", "display_order", "search_keywords", "updated_at"]
    fields += ["kind", "content_html", "external_url", "pin_page", "pin_x", "pin_y", "pin_show_name"] if isinstance(item, Material) else ["description"]
    if isinstance(item, Lesson):
        fields.append("published_at")
    payload = json.dumps([getattr(item, field) for field in fields], default=str)
    return sha256(payload.encode()).hexdigest()


def _item_values(kind, item):
    values = _content_fields(request.form)
    values["status"] = request.form.get("status")
    if values["status"] not in _STATUSES[kind]:
        raise ValueError("Select a valid status.")
    order = request.form.get("display_order", type=int)
    if order is None or not 1 <= order <= 100000:
        raise ValueError("Enter a position from 1 to 100000.")
    values["display_order"] = order
    if kind == "material":
        material_kind = item.kind
        if material_kind == "rich_text":
            values["content_html"] = _rich_text(request.form.get("content_html", ""))
        elif material_kind == "external_link":
            values["external_url"] = validate_external_url(request.form.get("external_url"))
    else:
        values["description"] = _description(request.form)
        if kind == "lesson":
            values["published_at"] = (
                item.published_at or datetime.now(timezone.utc)
            ) if values["status"] == "published" else None
    return values


@admin_bp.route("/courses/<public_id>/curriculum/<kind>/<item_id>/edit", methods=["GET", "POST"])
@roles_required(_ADMIN)
def curriculum_edit(public_id, kind, item_id):
    if kind not in _MODELS:
        abort(404)
    course = Course.query.filter_by(public_id=public_id).first_or_404()
    course_id = course.id
    query = _item_query(course_id, kind).filter(_MODELS[kind].public_id == item_id)
    item = query.first_or_404()
    if kind in ("unit", "lesson") and (item.course_id if kind == "unit" else item.unit.course_id):
        if request.method == "POST":
            abort(405)
        return redirect(url_for("admin.curriculum_division", public_id=public_id, kind=kind, item=item_id))
    serializer = URLSafeSerializer(current_app.secret_key, salt="admin.curriculum-edit.v1")
    content_url = None
    if kind == "material" and request.args.get("next") == "content":
        content_url = url_for("admin.curriculum_content", public_id=public_id,
                              lesson=request.args.get("return_lesson", item.lesson.public_id) or None,
                              page=request.args.get("return_page", type=int))
    if request.method == "POST":
        actor_id = current_user.id
        try:
            values = _item_values(kind, item)
            snapshot = serializer.loads(request.form.get("snapshot", ""))
            course = _lock_course(course_id, actor_id)
            item = query.first_or_404()
            if snapshot != _snapshot(item):
                raise BadSignature("stale")
            prior_student_groups = visible_curriculum_groups([course_id], kind, item.id)
            lesson_became_ready = kind == "lesson" and values["status"] == "published" and item.status != "published"
            for key, value in values.items():
                setattr(item, key, value)
            changed = db.session.is_modified(item)
            saved_id = item.id
            if lesson_became_ready:
                group_id = item.unit.group_id
                existing = db.session.get(LessonRelease, (group_id, item.id))
                if existing is None:
                    db.session.add(LessonRelease(group_id=group_id, lesson_id=item.id))
                else:
                    existing.opens_at = None
                    existing.notice_key = None
            db.session.commit()
            if changed:
                notify_curriculum_updated([course_id], kind=kind, item_id=saved_id,
                                          prior_student_groups=prior_student_groups)
            flash(f"Saved {kind}.", "success")
            if content_url:
                return redirect(content_url)
            return redirect(_curriculum_url(course))
        except BadSignature:
            db.session.rollback()
            flash("This content changed while you were editing it. Review the current version.", "warning")
            return redirect(request.full_path if content_url else request.path)
        except (ValueError, IntegrityError, OSError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else
                  "This item could not be saved. Check that its title is unique.", "danger")

    return render_template(
        "admin/curriculum/edit.html", course=course, item=item, kind=kind,
        statuses=_STATUSES[kind], position=item.display_order, content_url=content_url,
        snapshot=serializer.dumps(_snapshot(item)),
    )


@admin_bp.get("/courses/<public_id>/curriculum/material/<item_id>/file")
@roles_required(_ADMIN)
def curriculum_file(public_id, item_id):
    course = Course.query.filter_by(public_id=public_id).first_or_404()
    material = _item_query(course.id, "material").filter(
        Material.public_id == item_id, Material.kind == "file"
    ).first_or_404()
    return serve_uploaded_file(material.uploaded_file, current_user.id, force_attachment=False)
