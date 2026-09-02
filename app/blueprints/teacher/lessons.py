"""Teacher management of Unit-owned Lessons (M11).

Group- and Unit-centered routes only
(``/teacher/groups/<group_public_id>/units/<unit_public_id>/lessons/...``)
-- there is deliberately no flat ``/teacher/lessons`` collection. Every
object is addressed by ``public_id``; no internal numeric id ever appears
in a URL, form value, or the rendered HTML.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then
server-side and nested: the current Teacher must hold an **active**
``GroupTeacherAssignment`` to the Group in the URL, the Unit must belong
to that Group, and the Lesson must belong to that Unit. An unassigned
Teacher, a removed assignment, a Unit public_id from another Group, a
Lesson public_id from another Unit, or a missing object all return
**404** -- never a 403 or any hint that the object exists. Multiple
active assigned Teachers are equal collaborators. GET stays available to
an actively assigned Teacher even when the Unit, Group, or an academic
ancestor is archived, so historical Lessons can be read.

**Mutations.** Creation, editing, reordering, and publishing require --
re-checked against the locked rows -- an active Teacher account with role
``teacher``, an active assignment to the Group, an active
AcademicTerm / Level / Course / Group, and an active Unit belonging to
that Group. **Unpublishing** is allowed while the assignment is active
even when the Unit, Group, or an ancestor is archived, so published
content can always be withdrawn. Editing never alters ``status``,
``published_at``, or ``display_order``. A Unit / Group / ancestor
lifecycle change never rewrites a Lesson's publication status; nothing is
ever hard-deleted.

**Concurrency.** Every mutation follows the canonical M11 lock order

    AcademicTerm -> Level -> Course -> Group -> Teacher User ->
    GroupTeacherAssignment -> Unit -> Lesson rows (ascending internal id)

via ``lock_academic_hierarchy`` (which owns the single deliberate reset)
then the Group / User / assignment / Unit / Lesson ``SELECT ... FOR
UPDATE`` in the same open transaction. The locked Unit row serializes
same-Unit Lesson creation, ordering, and publication (and compatible
Unit lifecycle operations, which take the same Group + Unit locks). For a
reorder the full Lesson order is re-read under the held locks, then the
target and its swap neighbour are locked lowest-id-first before the
swap. All authoritative conditions are re-checked post-lock; a signed
snapshot protects the edit form against a time-separated co-teacher
overwrite. ``IntegrityError`` is caught, rolled back, and reported
generically.
"""

from datetime import datetime, timezone

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin.utils import move_within_siblings, normalize_optional_text
from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.forms import LessonForm
from app.blueprints.teacher.units import (
    _archived_chain_labels,
    _authz_broken,
    _group_is_operational,
    _join_labels,
    _teacher_group_or_404,
    _unit_for_group_or_404,
)
from app.extensions import db
from app.models import (
    AcademicStatus,
    GroupTeacherAssignment,
    Lesson,
    LessonStatus,
    Unit,
    User,
    UserRole,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.lesson_queries import lessons_ordered, next_lesson_display_order
from app.services.notification_delivery import notify_lesson_published

_ACTIVE = AcademicStatus.ACTIVE.value
_DRAFT = LessonStatus.DRAFT.value
_PUBLISHED = LessonStatus.PUBLISHED.value


# ----------------------------------------------------------------------
# Nested lookups
# ----------------------------------------------------------------------


def _lesson_for_unit_or_404(unit, lesson_public_id):
    """A Lesson by its own public_id, constrained to `unit`. A Lesson
    public_id valid only for another Unit 404s here."""
    return Lesson.query.filter_by(
        public_id=lesson_public_id, unit_id=unit.id
    ).first_or_404()


def _redirect_lessons(group_public_id, unit_public_id):
    return redirect(
        url_for(
            "teacher.group_lessons",
            group_public_id=group_public_id,
            unit_public_id=unit_public_id,
        )
    )


# ----------------------------------------------------------------------
# Canonical lock chain + post-lock re-checks
# ----------------------------------------------------------------------


def _lock_lesson_chain(
    group_public_id, term_id, level_id, course_id, teacher_id, unit_id, lesson_ids=()
):
    """Acquire the canonical M11 lock order in one open transaction:

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group -> Teacher User -> GroupTeacherAssignment
        -> Unit -> Lesson rows (ascending internal id)

    Returns ``(hierarchy, group, teacher, assignment, unit, {id: lesson})``.
    Any of group / teacher / assignment / unit / a lesson may be
    ``None`` -- the caller must treat that as a business/authorization
    rejection, roll back, and 404 or redirect; it must never "keep going".
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    teacher = User.query.filter_by(id=teacher_id).with_for_update().first()
    assignment = None
    if group is not None:
        assignment = (
            GroupTeacherAssignment.query.filter_by(
                group_id=group.id, teacher_id=teacher_id
            )
            .with_for_update()
            .first()
        )
    unit = Unit.query.filter_by(id=unit_id).with_for_update().first()
    lessons = {}
    for lid in sorted({lesson_id for lesson_id in lesson_ids if lesson_id is not None}):
        lessons[lid] = Lesson.query.filter_by(id=lid).with_for_update().first()
    return hierarchy, group, teacher, assignment, unit, lessons


def _unit_ownership_broken(group, unit):
    """True when the locked Unit no longer exists or no longer belongs to
    the locked Group -- caller rolls back and 404s (no disclosure)."""
    return unit is None or unit.group_id != group.id


def _operational_block(hierarchy, group, unit, term_id, level_id, course_id):
    """`None` if the locked Group, its locked AcademicTerm / Level /
    Course, and the locked Unit all exist and are active (so a create /
    edit / reorder / publish may proceed), else a Teacher-facing message.
    """
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    if (
        course is None
        or group.course_id != course.id
        or group.academic_term_id != term_id
        or course.level_id != level_id
    ):
        return "This group changed while you were working. Reload the page and try again."
    labels = [
        label
        for label, row in (
            ("academic term", term),
            ("level", level),
            ("course", course),
            ("group", group),
            ("unit", unit),
        )
        if row is None or row.status != _ACTIVE
    ]
    if labels:
        verb = "is" if len(labels) == 1 else "are"
        return (
            "Lessons can only be created, edited, reordered, or published while the group, its "
            "academic term, course, and level, and the unit are all active. "
            f"The {_join_labels(labels)} {verb} archived."
        )
    return None


# ----------------------------------------------------------------------
# List page
# ----------------------------------------------------------------------


@teacher_bp.get("/groups/<group_public_id>/units/<unit_public_id>/lessons")
@roles_required(UserRole.TEACHER.value)
def group_lessons(group_public_id, unit_public_id):
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id)
    operational = _group_is_operational(group)
    unit_active = unit.status == _ACTIVE
    can_manage = operational and unit_active
    return render_template(
        "teacher/lessons/list.html",
        group=group,
        unit=unit,
        lessons=lessons_ordered(unit.id),
        operational=operational,
        unit_active=unit_active,
        can_manage=can_manage,
        archived_labels=[] if can_manage else _archived_chain_labels(group)
        + ([] if unit_active else ["unit"]),
    )


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------


@teacher_bp.route(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/new",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def lesson_create(group_public_id, unit_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)

    if not (_group_is_operational(preview_group) and preview_unit.status == _ACTIVE):
        flash(
            "Lessons can only be created while the group, its academic term, course, and level, "
            "and the unit are all active.",
            "danger",
        )
        return _redirect_lessons(group_public_id, unit_public_id)

    form = LessonForm(unit_id=preview_unit.id)
    if form.validate_on_submit():
        title = form.title.data.strip()
        description = normalize_optional_text(form.description.data)
        search_keywords = form.search_keywords.data  # canonical str or None
        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy, group, teacher, assignment, unit, _ = _lock_lesson_chain(
            group_public_id, term_id, level_id, course_id, current_user.id, preview_unit.id
        )
        if _authz_broken(group, teacher, assignment) or _unit_ownership_broken(group, unit):
            db.session.rollback()
            abort(404)

        blocked = _operational_block(
            hierarchy, group, unit, term_id, level_id, course_id
        )
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_lessons(group_public_id, unit_public_id)

        lesson = Lesson(
            unit_id=unit.id,
            title=title,
            description=description,
            search_keywords=search_keywords,
            display_order=next_lesson_display_order(unit.id),
            status=_DRAFT,
            published_at=None,
        )
        db.session.add(lesson)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This lesson could not be saved. A lesson with this title may already exist in "
                "the unit, or the unit may have just changed. Please reload and try again.",
                "danger",
            )
            return _render_lesson_form(form, group_public_id, unit_public_id, lesson=None, snapshot_token=None)

        flash(f"Lesson '{lesson.title}' created as a draft.", "success")
        return _redirect_lessons(group_public_id, unit_public_id)

    return _render_lesson_form(form, group_public_id, unit_public_id, lesson=None, snapshot_token=None)


def _render_lesson_form(form, group_public_id, unit_public_id, lesson, snapshot_token):
    """Re-render the create/edit form -- releases any write lock first
    (harmless on GET / a plain form failure) and refetches display copies
    of the Group and Unit. `lesson` is None for create."""
    db.session.rollback()
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id)
    return render_template(
        "teacher/lessons/form.html",
        form=form,
        group=group,
        unit=unit,
        lesson=lesson,
        edit_snapshot_token=snapshot_token,
    )


# ----------------------------------------------------------------------
# Edit -- with a signed stale-form snapshot
# ----------------------------------------------------------------------

_LESSON_EDIT_SNAPSHOT_SALT = "teacher.lesson-edit-snapshot.v1"
_LESSON_EDIT_SNAPSHOT_FIELDS = ("public_id", "title", "description", "search_keywords")


def _lesson_snapshot_serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_LESSON_EDIT_SNAPSHOT_SALT)


def _lesson_snapshot_payload(lesson):
    return {field: getattr(lesson, field) for field in _LESSON_EDIT_SNAPSHOT_FIELDS}


def _make_lesson_snapshot_token(lesson):
    return _lesson_snapshot_serializer().dumps(_lesson_snapshot_payload(lesson))


def _load_lesson_snapshot(token):
    if not token:
        return None
    try:
        payload = _lesson_snapshot_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_LESSON_EDIT_SNAPSHOT_FIELDS):
        return None
    return payload


def _lesson_edit_is_stale(snapshot, lesson_public_id, current_lesson):
    if snapshot is None:
        return True
    if snapshot["public_id"] != lesson_public_id:
        return True
    return snapshot != _lesson_snapshot_payload(current_lesson)


def _redirect_stale_lesson_edit(group_public_id, unit_public_id, lesson_public_id):
    db.session.rollback()
    flash(
        "This lesson was changed since this form was opened. Please review the current values "
        "and try again.",
        "danger",
    )
    return redirect(
        url_for(
            "teacher.lesson_edit",
            group_public_id=group_public_id,
            unit_public_id=unit_public_id,
            lesson_public_id=lesson_public_id,
        )
    )


@teacher_bp.route(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def lesson_edit(group_public_id, unit_public_id, lesson_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)
    preview_lesson = _lesson_for_unit_or_404(preview_unit, lesson_public_id)

    if not (_group_is_operational(preview_group) and preview_unit.status == _ACTIVE):
        flash(
            "Lessons can only be edited while the group, its academic term, course, and level, "
            "and the unit are all active.",
            "danger",
        )
        return _redirect_lessons(group_public_id, unit_public_id)

    submitted_token = None
    if request.method == "POST":
        submitted_token = request.form.get("edit_snapshot", "")
        if _lesson_edit_is_stale(
            _load_lesson_snapshot(submitted_token), lesson_public_id, preview_lesson
        ):
            return _redirect_stale_lesson_edit(group_public_id, unit_public_id, lesson_public_id)

    form = LessonForm(
        obj=preview_lesson if request.method == "GET" else None,
        unit_id=preview_unit.id,
        lesson_id=preview_lesson.id,
    )

    if form.validate_on_submit():
        title = form.title.data.strip()
        description = normalize_optional_text(form.description.data)
        search_keywords = form.search_keywords.data  # canonical str or None
        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy, group, teacher, assignment, unit, lessons = _lock_lesson_chain(
            group_public_id, term_id, level_id, course_id, current_user.id,
            preview_unit.id, lesson_ids=[preview_lesson.id],
        )
        if _authz_broken(group, teacher, assignment) or _unit_ownership_broken(group, unit):
            db.session.rollback()
            abort(404)
        lesson = lessons.get(preview_lesson.id)
        if lesson is None or lesson.unit_id != unit.id:
            db.session.rollback()
            abort(404)

        if _lesson_edit_is_stale(
            _load_lesson_snapshot(submitted_token), lesson_public_id, lesson
        ):
            return _redirect_stale_lesson_edit(group_public_id, unit_public_id, lesson_public_id)

        blocked = _operational_block(
            hierarchy, group, unit, term_id, level_id, course_id
        )
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_lessons(group_public_id, unit_public_id)

        # `status`, `published_at`, and `display_order` are never assigned
        # here -- editing a published Lesson keeps it published, and never
        # reorders or re-times it.
        lesson.title = title
        lesson.description = description
        lesson.search_keywords = search_keywords
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This lesson could not be saved. A lesson with this title may already exist in "
                "the unit. Please reload and try again.",
                "danger",
            )
            return _render_lesson_form(
                form, group_public_id, unit_public_id, preview_lesson, submitted_token
            )

        flash(f"Lesson '{lesson.title}' updated.", "success")
        return _redirect_lessons(group_public_id, unit_public_id)

    edit_snapshot_token = (
        submitted_token
        if request.method == "POST"
        else _make_lesson_snapshot_token(preview_lesson)
    )
    return render_template(
        "teacher/lessons/form.html",
        form=form,
        group=preview_group,
        unit=preview_unit,
        lesson=preview_lesson,
        edit_snapshot_token=edit_snapshot_token,
    )


# ----------------------------------------------------------------------
# Publication toggle -- POST only, sole owner of Lesson status
# ----------------------------------------------------------------------


@teacher_bp.post(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/toggle-publication"
)
@roles_required(UserRole.TEACHER.value)
def lesson_toggle_publication(group_public_id, unit_public_id, lesson_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)
    preview_lesson = _lesson_for_unit_or_404(preview_unit, lesson_public_id)

    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    hierarchy, group, teacher, assignment, unit, lessons = _lock_lesson_chain(
        group_public_id, term_id, level_id, course_id, current_user.id,
        preview_unit.id, lesson_ids=[preview_lesson.id],
    )
    if _authz_broken(group, teacher, assignment) or _unit_ownership_broken(group, unit):
        db.session.rollback()
        abort(404)
    lesson = lessons.get(preview_lesson.id)
    if lesson is None or lesson.unit_id != unit.id:
        db.session.rollback()
        abort(404)

    if lesson.status == _PUBLISHED:
        # Unpublish -- allowed while the assignment is active even under an
        # archived Unit / Group / ancestor, so published content can
        # always be withdrawn. Returns the Lesson to draft.
        lesson.status = _DRAFT
        lesson.published_at = None
        db.session.commit()
        flash(f"Lesson '{lesson.title}' unpublished (back to draft).", "success")
        return _redirect_lessons(group_public_id, unit_public_id)

    # Publish / republish -- requires the operational chain and an active
    # Unit, and always stamps a fresh current-publication timestamp.
    blocked = _operational_block(hierarchy, group, unit, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_lessons(group_public_id, unit_public_id)

    lesson.status = _PUBLISHED
    lesson.published_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("This lesson could not be published. Please reload and try again.", "danger")
        return _redirect_lessons(group_public_id, unit_public_id)

    # M14: the publication is committed and the response is already
    # decided. Capture the plain id, build the response, and only then
    # attempt best-effort delivery in its own transaction. The producer
    # re-derives the audience itself (own active Enrollment + fully
    # active Term/Level/Course/Group/Unit + published Lesson), so nothing
    # about who can see this Lesson is decided here. Unpublishing above
    # deliberately notifies nobody.
    published_lesson_id = lesson.id
    flash(f"Lesson '{lesson.title}' published.", "success")
    response = _redirect_lessons(group_public_id, unit_public_id)
    notify_lesson_published(published_lesson_id)
    return response


# ----------------------------------------------------------------------
# Reordering -- move up / down among ALL siblings (draft + published)
# ----------------------------------------------------------------------


def _move_lesson(group_public_id, unit_public_id, lesson_public_id, offset, direction_word):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_unit = _unit_for_group_or_404(preview_group, unit_public_id)
    preview_lesson = _lesson_for_unit_or_404(preview_unit, lesson_public_id)

    if not (_group_is_operational(preview_group) and preview_unit.status == _ACTIVE):
        flash(
            "Lessons can only be reordered while the group, its academic term, course, and "
            "level, and the unit are all active.",
            "danger",
        )
        return _redirect_lessons(group_public_id, unit_public_id)

    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    hierarchy, group, teacher, assignment, unit, _ = _lock_lesson_chain(
        group_public_id, term_id, level_id, course_id, current_user.id, preview_unit.id
    )
    if _authz_broken(group, teacher, assignment) or _unit_ownership_broken(group, unit):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(hierarchy, group, unit, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_lessons(group_public_id, unit_public_id)

    # Re-read the full Lesson order (draft + published) under the held
    # Group + Unit locks, then lock the target Lesson and its swap
    # neighbour FOR UPDATE, ascending id, before swapping display_order.
    ordered = lessons_ordered(unit.id)
    index = next(
        (i for i, lesson in enumerate(ordered) if lesson.public_id == lesson_public_id),
        None,
    )
    if index is None:
        db.session.rollback()
        flash("This lesson is no longer in this unit.", "warning")
        return _redirect_lessons(group_public_id, unit_public_id)
    target_index = index + offset
    if target_index < 0 or target_index >= len(ordered):
        db.session.rollback()
        flash(f"This lesson is already {direction_word}.", "warning")
        return _redirect_lessons(group_public_id, unit_public_id)

    for lid in sorted((ordered[index].id, ordered[target_index].id)):
        Lesson.query.filter_by(id=lid).with_for_update().first()

    _, moved = move_within_siblings(ordered, lesson_public_id, offset)
    if not moved:  # defensive -- bounds already checked above
        db.session.rollback()
        flash(f"This lesson is already {direction_word}.", "warning")
        return _redirect_lessons(group_public_id, unit_public_id)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The lessons could not be reordered. Please reload and try again.", "danger")
        return _redirect_lessons(group_public_id, unit_public_id)

    flash("Lesson order updated.", "success")
    return _redirect_lessons(group_public_id, unit_public_id)


@teacher_bp.post(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/move-up"
)
@roles_required(UserRole.TEACHER.value)
def lesson_move_up(group_public_id, unit_public_id, lesson_public_id):
    return _move_lesson(group_public_id, unit_public_id, lesson_public_id, -1, "first")


@teacher_bp.post(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/move-down"
)
@roles_required(UserRole.TEACHER.value)
def lesson_move_down(group_public_id, unit_public_id, lesson_public_id):
    return _move_lesson(group_public_id, unit_public_id, lesson_public_id, 1, "last")
