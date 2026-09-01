"""Teacher management of Lesson-owned Materials (M12).

Group- / Unit- / Lesson-centered routes only
(``/teacher/groups/<gp>/units/<up>/lessons/<lp>/materials/...``) -- no
flat ``/teacher/materials`` collection, no internal numeric id in any
URL, form value, or rendered page.

**Authorization** reuses the M10/M11 pattern exactly:
``roles_required(TEACHER)`` handles anonymous/non-Teacher; object
authorization is server-side and nested (Group -> active
GroupTeacherAssignment -> Unit belongs to Group -> Lesson belongs to
Unit -> Material belongs to Lesson), returning **404** -- never 403 or
any disclosure -- on any break in that chain. GET (including
open/download) stays available to an actively assigned Teacher under an
archived Unit/Group/ancestor, matching the M11 historical-access policy;
create/edit/reorder/reactivate require the *whole* chain active
(re-using M11's ``_operational_block``, which already covers
Term/Level/Course/Group/Unit). Archiving is always allowed for cleanup
while the assignment is active, exactly like M10/M11.

**Concurrency** extends the M11 canonical lock order with two more
links: ``... -> Unit -> Lesson -> Material rows (ascending id)``, always
preceded by the same single ``lock_academic_hierarchy`` reset. The
locked Lesson row serializes same-Lesson Material creation and ordering,
and is compatible with M11's own Lesson lock (publish/unpublish).

**Duplicate-request protection.** Every create form carries a signed
one-time token binding a random nonce to the Teacher and the Lesson
(``creation_nonce``, ``materials.creation_nonce`` UNIQUE). An ordinary
replay (the nonce already names a committed Material) redirects to it
without creating anything; a genuinely concurrent replay is caught by
the UNIQUE constraint at commit and the loser deletes its own
just-written file (if any) rather than leaving an orphan. Uploading a
file happens **before** any database lock is taken -- streaming a large
upload must never hold a write lock.
"""

import secrets

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from app.blueprints.admin.utils import move_within_siblings
from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.forms import (
    ExternalLinkMaterialForm,
    FileMaterialEditForm,
    FileMaterialForm,
    RichTextMaterialForm,
)
from app.blueprints.teacher.lessons import _lesson_for_unit_or_404, _operational_block
from app.blueprints.teacher.units import (
    _archived_chain_labels,
    _authz_broken,
    _group_is_operational,
    _teacher_group_or_404,
    _unit_for_group_or_404,
)
from app.extensions import db
from app.models import (
    AcademicStatus,
    FileAccessAction,
    FileAccessLog,
    GroupTeacherAssignment,
    Lesson,
    Material,
    MaterialKind,
    Unit,
    UploadedFile,
    User,
    UserRole,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.file_storage import delete_stored_file, store_validated_upload
from app.services.file_validation import FileValidationError
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.material_config import current_material_config
from app.services.material_queries import (
    active_materials_ordered,
    material_by_nonce,
    next_material_display_order,
    teacher_lesson_materials_view,
)
from app.services.material_serving import serve_uploaded_file

_ACTIVE = AcademicStatus.ACTIVE.value
_RICH_TEXT = MaterialKind.RICH_TEXT.value
_EXTERNAL_LINK = MaterialKind.EXTERNAL_LINK.value
_FILE = MaterialKind.FILE.value


# ----------------------------------------------------------------------
# Nested lookups
# ----------------------------------------------------------------------


def _material_for_lesson_or_404(lesson, material_public_id):
    """A Material by its own public_id, constrained to `lesson`. A
    Material public_id valid only for another Lesson 404s here."""
    return Material.query.filter_by(
        public_id=material_public_id, lesson_id=lesson.id
    ).first_or_404()


def _redirect_materials(group_public_id, unit_public_id, lesson_public_id):
    return redirect(
        url_for(
            "teacher.lesson_materials",
            group_public_id=group_public_id,
            unit_public_id=unit_public_id,
            lesson_public_id=lesson_public_id,
        )
    )


# ----------------------------------------------------------------------
# Canonical lock chain (extends M11 with Lesson -> Material)
# ----------------------------------------------------------------------


def _lock_material_chain(
    group_public_id, term_id, level_id, course_id, teacher_id, unit_id, lesson_id,
    material_ids=(),
):
    """Acquire the canonical M12 lock order in one open transaction:

        AcademicTerm -> Level -> Course  (lock_academic_hierarchy, which
        owns the single deliberate reset)
        -> Group -> Teacher User -> GroupTeacherAssignment
        -> Unit -> Lesson -> Material rows (ascending internal id)

    Returns ``(hierarchy, group, teacher, assignment, unit, lesson,
    {id: material})``. Any of these may be ``None`` -- the caller must
    treat that as a business/authorization rejection, roll back, and
    404/redirect; never "keep going".
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    teacher = User.query.filter_by(id=teacher_id).with_for_update().first()
    assignment = None
    if group is not None:
        assignment = (
            GroupTeacherAssignment.query.filter_by(group_id=group.id, teacher_id=teacher_id)
            .with_for_update()
            .first()
        )
    unit = Unit.query.filter_by(id=unit_id).with_for_update().first()
    lesson = Lesson.query.filter_by(id=lesson_id).with_for_update().first()
    materials = {}
    for mid in sorted({m for m in material_ids if m is not None}):
        materials[mid] = Material.query.filter_by(id=mid).with_for_update().first()
    return hierarchy, group, teacher, assignment, unit, lesson, materials


def _unit_ownership_broken(group, unit):
    return unit is None or unit.group_id != group.id


def _lesson_ownership_broken(unit, lesson):
    return lesson is None or lesson.unit_id != unit.id


def _material_ownership_broken(lesson, material):
    return material is None or material.lesson_id != lesson.id


def _hierarchy_context(group):
    return (
        group.academic_term_id,
        group.course.level_id,
        group.course_id,
    )


# ----------------------------------------------------------------------
# List page
# ----------------------------------------------------------------------


@teacher_bp.get("/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/materials")
@roles_required(UserRole.TEACHER.value)
def lesson_materials(group_public_id, unit_public_id, lesson_public_id):
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id)
    lesson = _lesson_for_unit_or_404(unit, lesson_public_id)
    operational = _group_is_operational(group)
    unit_active = unit.status == _ACTIVE
    can_manage = operational and unit_active
    ctx, active_materials, archived_materials = teacher_lesson_materials_view(group, unit, lesson)
    return render_template(
        "teacher/materials/list.html",
        ctx=ctx,
        active_materials=active_materials,
        archived_materials=archived_materials,
        operational=operational,
        unit_active=unit_active,
        can_manage=can_manage,
        archived_labels=[]
        if can_manage
        else _archived_chain_labels(group) + ([] if unit_active else ["unit"]),
    )


# ----------------------------------------------------------------------
# Create -- signed one-time token (nonce bound to Teacher + Lesson + kind)
# ----------------------------------------------------------------------

_CREATE_TOKEN_SALT = "teacher.material-create-token.v1"
_CREATE_TOKEN_FIELDS = ("nonce", "teacher_id", "lesson_id", "kind")


def _create_token_serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_CREATE_TOKEN_SALT)


def _make_create_token(teacher_id, lesson_id, kind, nonce=None):
    nonce = nonce or secrets.token_hex(32)
    payload = {"nonce": nonce, "teacher_id": teacher_id, "lesson_id": lesson_id, "kind": kind}
    return _create_token_serializer().dumps(payload)


def _load_create_nonce(token, teacher_id, lesson_id, kind):
    """The nonce bound to this exact Teacher/Lesson/kind, or ``None`` if
    the token is missing, malformed, wrong-signature, or bound to a
    different Teacher/Lesson/kind."""
    if not token:
        return None
    try:
        payload = _create_token_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_CREATE_TOKEN_FIELDS):
        return None
    if payload["teacher_id"] != teacher_id or payload["lesson_id"] != lesson_id:
        return None
    if payload["kind"] != kind:
        return None
    return payload["nonce"]


def _redirect_stale_create(group_public_id, unit_public_id, lesson_public_id, new_url):
    db.session.rollback()
    flash(
        "This form could not be verified (it may be old or was opened in another tab). "
        "Please try again.",
        "danger",
    )
    return redirect(new_url)


def _creation_replay_response(group_public_id, unit_public_id, lesson_public_id, nonce):
    """An ordinary or concurrent replay of an already-succeeded create
    request: redirect to the materials list without creating anything
    new -- idempotent by design."""
    existing = material_by_nonce(nonce)
    if existing is not None:
        flash(f"Material '{existing.title}' was already created.", "success")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)
    return None


_CREATE_ROUTE_BY_KIND = {
    _RICH_TEXT: "teacher.material_create_rich_text",
    _EXTERNAL_LINK: "teacher.material_create_external_link",
    _FILE: "teacher.material_create_file",
}


def _fresh_create_url(group_public_id, unit_public_id, lesson_public_id, kind):
    return url_for(
        _CREATE_ROUTE_BY_KIND[kind],
        group_public_id=group_public_id,
        unit_public_id=unit_public_id,
        lesson_public_id=lesson_public_id,
    )


def _render_material_form(template, form, group, unit, lesson, material, token):
    """Render a create/edit material form. `token` is the create token
    on a create form and the signed edit-snapshot token on an edit form
    -- the templates pick the right hidden field by whether `material`
    is set, so both names carry the same value here."""
    return render_template(
        f"teacher/materials/{template}",
        form=form,
        group=group,
        unit=unit,
        lesson=lesson,
        material=material,
        create_token=token,
        edit_snapshot_token=token,
    )


def _material_creation_precheck(group_public_id, unit_public_id, lesson_public_id):
    """Shared preview + operational gate for every create route. Returns
    ``(group, unit, lesson)`` or a Flask response if creation is blocked
    right now (no lock has been taken yet)."""
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id)
    lesson = _lesson_for_unit_or_404(unit, lesson_public_id)
    if not (_group_is_operational(group) and unit.status == _ACTIVE):
        flash(
            "Materials can only be added while the group, its academic term, course, and "
            "level, and the unit are all active.",
            "danger",
        )
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)
    return group, unit, lesson


def _lock_and_authorize_for_create(
    group_public_id, unit_public_id, lesson_public_id, group, unit, lesson
):
    """Acquire the canonical lock chain and re-check authorization + the
    operational chain. Returns ``(hierarchy, locked_group, locked_unit,
    locked_lesson)`` on success, or a Flask response to return directly
    on rejection (the caller must ``return`` it immediately).

    Redirects always use the caller's trusted URL string parameters,
    never a ``.public_id`` re-read off a row after the lock's rollback
    -- the same convention every other M10/M11 route follows.
    """
    term_id, level_id, course_id = _hierarchy_context(group)
    hierarchy, locked_group, teacher, assignment, locked_unit, locked_lesson, _ = (
        _lock_material_chain(
            group_public_id, term_id, level_id, course_id, current_user.id, unit.id, lesson.id
        )
    )
    if _authz_broken(locked_group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _unit_ownership_broken(locked_group, locked_unit):
        db.session.rollback()
        abort(404)
    if _lesson_ownership_broken(locked_unit, locked_lesson):
        db.session.rollback()
        abort(404)
    blocked = _operational_block(hierarchy, locked_group, locked_unit, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)
    return hierarchy, locked_group, locked_unit, locked_lesson


@teacher_bp.route(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/materials/new/rich-text",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def material_create_rich_text(group_public_id, unit_public_id, lesson_public_id):
    precheck = _material_creation_precheck(group_public_id, unit_public_id, lesson_public_id)
    if not isinstance(precheck, tuple):
        return precheck
    group, unit, lesson = precheck

    submitted_token = request.form.get("create_token", "") if request.method == "POST" else None
    nonce = _load_create_nonce(submitted_token, current_user.id, lesson.id, _RICH_TEXT)

    if request.method == "POST" and nonce is None:
        return _redirect_stale_create(
            group_public_id, unit_public_id, lesson_public_id,
            _fresh_create_url(group_public_id, unit_public_id, lesson_public_id, _RICH_TEXT),
        )

    if request.method == "POST":
        replay = _creation_replay_response(group_public_id, unit_public_id, lesson_public_id, nonce)
        if replay is not None:
            return replay

    form = RichTextMaterialForm(lesson_id=lesson.id)
    if form.validate_on_submit():
        title = form.title.data.strip()
        content_html = form.content_html.data  # already sanitized by validate_content_html

        result = _lock_and_authorize_for_create(
            group_public_id, unit_public_id, lesson_public_id, group, unit, lesson
        )
        if not isinstance(result, tuple):
            return result
        hierarchy, locked_group, locked_unit, locked_lesson = result

        replay = _creation_replay_response(
            group_public_id, unit_public_id, lesson_public_id, nonce
        )
        if replay is not None:
            db.session.rollback()
            return replay

        material = Material(
            lesson_id=locked_lesson.id,
            title=title,
            kind=_RICH_TEXT,
            content_html=content_html,
            status=_ACTIVE,
            display_order=next_material_display_order(locked_lesson.id),
            creation_nonce=nonce,
        )
        db.session.add(material)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            replay = _creation_replay_response(
                group_public_id, unit_public_id, lesson_public_id, nonce
            )
            if replay is not None:
                return replay
            flash(
                "This material could not be saved. A material with this title may already "
                "exist in the lesson. Please reload and try again.",
                "danger",
            )
            return _render_material_form(
                "form_rich_text.html", form, group, unit, lesson, None,
                _make_create_token(current_user.id, lesson.id, _RICH_TEXT, nonce),
            )

        flash(f"Material '{material.title}' created.", "success")
        _warn_if_published(lesson)
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    token = submitted_token or _make_create_token(current_user.id, lesson.id, _RICH_TEXT)
    return _render_material_form("form_rich_text.html", form, group, unit, lesson, None, token)


@teacher_bp.route(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/materials/new/external-link",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def material_create_external_link(group_public_id, unit_public_id, lesson_public_id):
    precheck = _material_creation_precheck(group_public_id, unit_public_id, lesson_public_id)
    if not isinstance(precheck, tuple):
        return precheck
    group, unit, lesson = precheck

    submitted_token = request.form.get("create_token", "") if request.method == "POST" else None
    nonce = _load_create_nonce(submitted_token, current_user.id, lesson.id, _EXTERNAL_LINK)

    if request.method == "POST" and nonce is None:
        return _redirect_stale_create(
            group_public_id, unit_public_id, lesson_public_id,
            _fresh_create_url(group_public_id, unit_public_id, lesson_public_id, _EXTERNAL_LINK),
        )

    if request.method == "POST":
        replay = _creation_replay_response(group_public_id, unit_public_id, lesson_public_id, nonce)
        if replay is not None:
            return replay

    form = ExternalLinkMaterialForm(lesson_id=lesson.id)
    if form.validate_on_submit():
        title = form.title.data.strip()
        external_url = form.external_url.data  # already validated + cleaned

        result = _lock_and_authorize_for_create(
            group_public_id, unit_public_id, lesson_public_id, group, unit, lesson
        )
        if not isinstance(result, tuple):
            return result
        hierarchy, locked_group, locked_unit, locked_lesson = result

        replay = _creation_replay_response(
            group_public_id, unit_public_id, lesson_public_id, nonce
        )
        if replay is not None:
            db.session.rollback()
            return replay

        material = Material(
            lesson_id=locked_lesson.id,
            title=title,
            kind=_EXTERNAL_LINK,
            external_url=external_url,
            status=_ACTIVE,
            display_order=next_material_display_order(locked_lesson.id),
            creation_nonce=nonce,
        )
        db.session.add(material)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            replay = _creation_replay_response(
                group_public_id, unit_public_id, lesson_public_id, nonce
            )
            if replay is not None:
                return replay
            flash(
                "This material could not be saved. A material with this title may already "
                "exist in the lesson. Please reload and try again.",
                "danger",
            )
            return _render_material_form(
                "form_link.html", form, group, unit, lesson, None,
                _make_create_token(current_user.id, lesson.id, _EXTERNAL_LINK, nonce),
            )

        flash(f"Material '{material.title}' created.", "success")
        _warn_if_published(lesson)
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    token = submitted_token or _make_create_token(current_user.id, lesson.id, _EXTERNAL_LINK)
    return _render_material_form("form_link.html", form, group, unit, lesson, None, token)


@teacher_bp.route(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/materials/new/file",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def material_create_file(group_public_id, unit_public_id, lesson_public_id):
    precheck = _material_creation_precheck(group_public_id, unit_public_id, lesson_public_id)
    if not isinstance(precheck, tuple):
        return precheck
    group, unit, lesson = precheck

    submitted_token = request.form.get("create_token", "") if request.method == "POST" else None
    nonce = _load_create_nonce(submitted_token, current_user.id, lesson.id, _FILE)

    if request.method == "POST" and nonce is None:
        return _redirect_stale_create(
            group_public_id, unit_public_id, lesson_public_id,
            _fresh_create_url(group_public_id, unit_public_id, lesson_public_id, _FILE),
        )

    if request.method == "POST":
        replay = _creation_replay_response(group_public_id, unit_public_id, lesson_public_id, nonce)
        if replay is not None:
            return replay

    form = FileMaterialForm(lesson_id=lesson.id)
    if form.validate_on_submit():
        title = form.title.data.strip()

        # Stream + validate + store the file to disk BEFORE taking any
        # database lock (Part M12 section 7/12) -- never hold a write
        # lock while streaming a large upload.
        material_config = current_material_config()
        try:
            stored = store_validated_upload(
                material_config, form.file.data, form.file.data.filename
            )
        except FileValidationError as exc:
            # User-caused: this message is deliberately safe (no path, no
            # SQL, no exception internals -- see file_validation.py).
            flash(str(exc), "danger")
            return _render_material_form(
                "form_file.html", form, group, unit, lesson, None,
                _make_create_token(current_user.id, lesson.id, _FILE, nonce),
            )
        # Any other exception from store_validated_upload (containment,
        # OSError, ...) propagates: it has already deleted its own
        # temp/final files, Flask logs it server-side, and the generic
        # 500 handler responds without leaking details.

        # From here a final file exists on disk. Every non-success exit
        # before a confirmed commit must delete it; `committed` gates
        # that so a successfully-saved file is never removed.
        committed = False
        try:
            result = _lock_and_authorize_for_create(
                group_public_id, unit_public_id, lesson_public_id, group, unit, lesson
            )
            if not isinstance(result, tuple):
                return result  # blocked redirect -- `finally` cleans the file
            hierarchy, locked_group, locked_unit, locked_lesson = result

            # Concurrent-replay re-check under the lock: another request
            # may have committed the same nonce while this one was
            # streaming its (now-redundant) file.
            replay = _creation_replay_response(
                group_public_id, unit_public_id, lesson_public_id, nonce
            )
            if replay is not None:
                db.session.rollback()
                return replay  # loser -- `finally` cleans its own file

            uploaded_file = UploadedFile(
                storage_key=stored.storage_key,
                original_filename=stored.original_filename,
                extension=stored.extension,
                category=stored.category,
                content_type=stored.content_type,
                byte_size=stored.byte_size,
                sha256=stored.sha256,
                uploaded_by_id=current_user.id,
            )
            material = Material(
                lesson_id=locked_lesson.id,
                title=title,
                kind=_FILE,
                uploaded_file=uploaded_file,
                status=_ACTIVE,
                display_order=next_material_display_order(locked_lesson.id),
                creation_nonce=nonce,
            )
            upload_log = FileAccessLog(
                uploaded_file=uploaded_file,
                actor_id=current_user.id,
                action=FileAccessAction.UPLOAD.value,
            )
            db.session.add_all([uploaded_file, material, upload_log])
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                replay = _creation_replay_response(
                    group_public_id, unit_public_id, lesson_public_id, nonce
                )
                if replay is not None:
                    return replay  # concurrent winner committed -- `finally` cleans
                flash(
                    "This material could not be saved. A material with this title may already "
                    "exist in the lesson. Please reload and try again.",
                    "danger",
                )
                return _render_material_form(
                    "form_file.html", form, group, unit, lesson, None,
                    _make_create_token(current_user.id, lesson.id, _FILE, nonce),
                )
            committed = True
        except HTTPException:
            # An intentional abort() (e.g. a non-disclosing 404 from the
            # post-lock authorization re-check). Not an error to log --
            # but the file still has no Material, so `finally` cleans it.
            db.session.rollback()
            raise
        except Exception:
            # Unexpected (lock/DB failure, programming error). Roll back,
            # log server-side, and let the generic 500 handler respond --
            # never surface the exception text; `finally` cleans the file.
            db.session.rollback()
            current_app.logger.exception(
                "Unexpected error while creating a file Material"
            )
            raise
        finally:
            if not committed:
                _cleanup_orphan_upload(material_config, stored.storage_key)

        flash(f"Material '{material.title}' created.", "success")
        _warn_if_published(lesson)
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    token = submitted_token or _make_create_token(current_user.id, lesson.id, _FILE)
    return _render_material_form("form_file.html", form, group, unit, lesson, None, token)


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


def _warn_if_published(lesson):
    if lesson.status == "published":
        flash(
            "This lesson is published: the new material is immediately visible to enrolled "
            "students.",
            "warning",
        )


# ----------------------------------------------------------------------
# Edit -- stale-form snapshot (fields depend on the immutable kind)
# ----------------------------------------------------------------------

_EDIT_SNAPSHOT_SALT = "teacher.material-edit-snapshot.v1"

_FORM_BY_KIND = {
    _RICH_TEXT: RichTextMaterialForm,
    _EXTERNAL_LINK: ExternalLinkMaterialForm,
    _FILE: FileMaterialEditForm,
}
_EDIT_TEMPLATE_BY_KIND = {
    _RICH_TEXT: "form_rich_text.html",
    _EXTERNAL_LINK: "form_link.html",
    _FILE: "form_file_edit.html",
}


def _snapshot_fields(kind):
    if kind == _RICH_TEXT:
        return ("public_id", "title", "content_html")
    if kind == _EXTERNAL_LINK:
        return ("public_id", "title", "external_url")
    return ("public_id", "title")


def _edit_snapshot_serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_EDIT_SNAPSHOT_SALT)


def _snapshot_payload(material):
    return {field: getattr(material, field) for field in _snapshot_fields(material.kind)}


def _make_edit_snapshot_token(material):
    return _edit_snapshot_serializer().dumps(_snapshot_payload(material))


def _load_edit_snapshot(token):
    if not token:
        return None
    try:
        payload = _edit_snapshot_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _edit_is_stale(snapshot, material_public_id, current_material):
    if snapshot is None:
        return True
    if snapshot.get("public_id") != material_public_id:
        return True
    if set(snapshot.keys()) != set(_snapshot_fields(current_material.kind)):
        return True
    return snapshot != _snapshot_payload(current_material)


def _redirect_stale_material_edit(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    db.session.rollback()
    flash(
        "This material was changed since this form was opened. Please review the current "
        "values and try again.",
        "danger",
    )
    return redirect(
        url_for(
            "teacher.material_edit",
            group_public_id=group_public_id,
            unit_public_id=unit_public_id,
            lesson_public_id=lesson_public_id,
            material_public_id=material_public_id,
        )
    )


@teacher_bp.route(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>"
    "/materials/<material_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def material_edit(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)
    preview_lesson = _lesson_for_unit_or_404(preview_unit, lesson_public_id)
    preview_material = _material_for_lesson_or_404(preview_lesson, material_public_id)

    if not (_group_is_operational(preview_group) and preview_unit.status == _ACTIVE):
        flash(
            "Materials can only be edited while the group, its academic term, course, and "
            "level, and the unit are all active.",
            "danger",
        )
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    kind = preview_material.kind
    form_cls = _FORM_BY_KIND[kind]
    template = _EDIT_TEMPLATE_BY_KIND[kind]

    submitted_token = None
    if request.method == "POST":
        submitted_token = request.form.get("edit_snapshot", "")
        if _edit_is_stale(
            _load_edit_snapshot(submitted_token), material_public_id, preview_material
        ):
            return _redirect_stale_material_edit(
                group_public_id, unit_public_id, lesson_public_id, material_public_id
            )

    form = form_cls(
        obj=preview_material if request.method == "GET" else None,
        lesson_id=preview_lesson.id,
        material_id=preview_material.id,
    )

    if form.validate_on_submit():
        title = form.title.data.strip()
        term_id, level_id, course_id = _hierarchy_context(preview_group)

        hierarchy, group, teacher, assignment, unit, lesson, materials = _lock_material_chain(
            group_public_id, term_id, level_id, course_id, current_user.id,
            preview_unit.id, preview_lesson.id, material_ids=[preview_material.id],
        )
        if _authz_broken(group, teacher, assignment):
            db.session.rollback()
            abort(404)
        if _unit_ownership_broken(group, unit) or _lesson_ownership_broken(unit, lesson):
            db.session.rollback()
            abort(404)
        material = materials.get(preview_material.id)
        if _material_ownership_broken(lesson, material):
            db.session.rollback()
            abort(404)

        if _edit_is_stale(_load_edit_snapshot(submitted_token), material_public_id, material):
            return _redirect_stale_material_edit(
                group_public_id, unit_public_id, lesson_public_id, material_public_id
            )

        blocked = _operational_block(hierarchy, group, unit, term_id, level_id, course_id)
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

        # `kind`, `status`, `display_order`, and the uploaded file are
        # never assigned here -- kind is immutable, publication-visible
        # status/order are owned by the toggle/move routes, and file
        # bytes never change after creation.
        material.title = title
        if kind == _RICH_TEXT:
            material.content_html = form.content_html.data
        elif kind == _EXTERNAL_LINK:
            material.external_url = form.external_url.data
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This material could not be saved. A material with this title may already "
                "exist in the lesson. Please reload and try again.",
                "danger",
            )
            group = _teacher_group_or_404(group_public_id)
            unit = _unit_for_group_or_404(group, unit_public_id)
            lesson = _lesson_for_unit_or_404(unit, lesson_public_id)
            material = _material_for_lesson_or_404(lesson, material_public_id)
            return _render_material_form(
                template, form, group, unit, lesson, material, submitted_token
            )

        flash(f"Material '{material.title}' updated.", "success")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    edit_snapshot_token = (
        submitted_token if request.method == "POST" else _make_edit_snapshot_token(preview_material)
    )
    return _render_material_form(
        template, form, preview_group, preview_unit, preview_lesson, preview_material,
        edit_snapshot_token,
    )


# ----------------------------------------------------------------------
# Status toggle -- POST only, sole owner of Material status
# ----------------------------------------------------------------------


@teacher_bp.post(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>"
    "/materials/<material_public_id>/toggle-status"
)
@roles_required(UserRole.TEACHER.value)
def material_toggle_status(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)
    preview_lesson = _lesson_for_unit_or_404(preview_unit, lesson_public_id)
    preview_material = _material_for_lesson_or_404(preview_lesson, material_public_id)
    term_id, level_id, course_id = _hierarchy_context(preview_group)

    hierarchy, group, teacher, assignment, unit, lesson, materials = _lock_material_chain(
        group_public_id, term_id, level_id, course_id, current_user.id,
        preview_unit.id, preview_lesson.id, material_ids=[preview_material.id],
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _unit_ownership_broken(group, unit) or _lesson_ownership_broken(unit, lesson):
        db.session.rollback()
        abort(404)
    material = materials.get(preview_material.id)
    if _material_ownership_broken(lesson, material):
        db.session.rollback()
        abort(404)

    if material.status == _ACTIVE:
        # Archiving is always allowed while the assignment is active --
        # even under an archived Unit/Group/ancestor -- and never
        # touches the physical file or the UploadedFile row.
        material.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        flash(f"Material '{material.title}' archived.", "success")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    # Reactivation requires the operational chain, and appends the
    # Material after the Lesson's current highest display_order.
    blocked = _operational_block(hierarchy, group, unit, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    material.status = _ACTIVE
    material.display_order = next_material_display_order(lesson.id)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This material could not be reactivated. Please reload and try again.", "danger")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    flash(f"Material '{material.title}' reactivated.", "success")
    _warn_if_published(lesson)
    return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)


# ----------------------------------------------------------------------
# Reordering -- move up / down among ACTIVE siblings only
# ----------------------------------------------------------------------


def _move_material(group_public_id, unit_public_id, lesson_public_id, material_public_id, offset, direction_word):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)
    preview_lesson = _lesson_for_unit_or_404(preview_unit, lesson_public_id)
    preview_material = _material_for_lesson_or_404(preview_lesson, material_public_id)

    if preview_material.status != _ACTIVE:
        flash("Only active materials can be reordered.", "warning")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)
    if not (_group_is_operational(preview_group) and preview_unit.status == _ACTIVE):
        flash(
            "Materials can only be reordered while the group, its academic term, course, and "
            "level, and the unit are all active.",
            "danger",
        )
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    term_id, level_id, course_id = _hierarchy_context(preview_group)
    hierarchy, group, teacher, assignment, unit, lesson, _ = _lock_material_chain(
        group_public_id, term_id, level_id, course_id, current_user.id,
        preview_unit.id, preview_lesson.id,
    )
    if _authz_broken(group, teacher, assignment):
        db.session.rollback()
        abort(404)
    if _unit_ownership_broken(group, unit) or _lesson_ownership_broken(unit, lesson):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(hierarchy, group, unit, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    # Re-read the active order under the held Group + Unit + Lesson
    # locks, then lock the target Material and its swap neighbour FOR
    # UPDATE, ascending id, before swapping display_order.
    ordered = active_materials_ordered(lesson.id)
    index = next(
        (i for i, m in enumerate(ordered) if m.public_id == material_public_id), None
    )
    if index is None:
        db.session.rollback()
        flash("This material is no longer active.", "warning")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)
    target_index = index + offset
    if target_index < 0 or target_index >= len(ordered):
        db.session.rollback()
        flash(f"This material is already {direction_word}.", "warning")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    for mid in sorted((ordered[index].id, ordered[target_index].id)):
        Material.query.filter_by(id=mid).with_for_update().first()

    _, moved = move_within_siblings(ordered, material_public_id, offset)
    if not moved:  # defensive -- bounds already checked above
        db.session.rollback()
        flash(f"This material is already {direction_word}.", "warning")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The materials could not be reordered. Please reload and try again.", "danger")
        return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)

    flash("Material order updated.", "success")
    return _redirect_materials(group_public_id, unit_public_id, lesson_public_id)


@teacher_bp.post(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>"
    "/materials/<material_public_id>/move-up"
)
@roles_required(UserRole.TEACHER.value)
def material_move_up(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    return _move_material(
        group_public_id, unit_public_id, lesson_public_id, material_public_id, -1, "first"
    )


@teacher_bp.post(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>"
    "/materials/<material_public_id>/move-down"
)
@roles_required(UserRole.TEACHER.value)
def material_move_down(group_public_id, unit_public_id, lesson_public_id, material_public_id):
    return _move_material(
        group_public_id, unit_public_id, lesson_public_id, material_public_id, 1, "last"
    )


# ----------------------------------------------------------------------
# Authorized file serving + audit -- Teacher (historical access allowed)
# ----------------------------------------------------------------------


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
    unit = _unit_for_group_or_404(group, unit_public_id)
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
    unit = _unit_for_group_or_404(group, unit_public_id)
    lesson = _lesson_for_unit_or_404(unit, lesson_public_id)
    material = _material_for_lesson_or_404(lesson, material_public_id)
    return _serve_material_file(material, force_attachment=True)
