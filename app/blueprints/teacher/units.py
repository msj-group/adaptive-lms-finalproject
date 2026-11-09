"""Read-only course outline and group access helpers for teacher routes."""
from flask import abort, redirect, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.blueprints.teacher import teacher_bp
from app.extensions import db
from app.models import AcademicStatus, Course, Group, GroupTeacherAssignment, GroupTeacherAssignmentStatus, Unit, User, UserRole, UserStatus
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction

_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value


def _teacher_group_or_404(group_public_id):
    """The Group named by `group_public_id`, but only when the current
    Teacher holds an ACTIVE assignment to it. Every other case -- no such
    Group, unassigned Teacher, removed assignment -- aborts 404 without
    revealing whether the Group exists. Does NOT check the Group's own
    status: GET stays available for historical Units under an archived
    Group.
    """
    group = (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=group_public_id)
        .first()
    )
    if group is None:
        abort(404)
    has_active_assignment = db.session.query(
        GroupTeacherAssignment.query.filter_by(
            group_id=group.id, teacher_id=current_user.id, status=_ASSIGNMENT_ACTIVE
        ).exists()
    ).scalar()
    if not has_active_assignment:
        abort(404)
    return group


def _unit_for_group_or_404(group, unit_public_id, *, include_shared=False):
    scope = Unit.in_group(group.id, group.course_id) if include_shared else Unit.group_id == group.id
    return Unit.query.filter(Unit.public_id == unit_public_id, scope).first_or_404()


def _group_is_operational(group):
    return (
        group.status == _ACTIVE
        and group.academic_term.status == _ACTIVE
        and group.course.status == _ACTIVE
        and group.course.level.status == _ACTIVE
    )


def _archived_chain_labels(group):
    return [
        label
        for label, ok in (
            ("academic term", group.academic_term.status == _ACTIVE),
            ("level", group.course.level.status == _ACTIVE),
            ("course", group.course.status == _ACTIVE),
            ("group", group.status == _ACTIVE),
        )
        if not ok
    ]


def _join_labels(items):
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _lock_unit_chain(group_public_id, term_id, level_id, course_id, teacher_id, unit_ids=()):
    """Acquire the canonical M10 lock order in one open transaction:

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group -> Teacher User -> GroupTeacherAssignment
        -> Unit rows (ascending internal id)

    Returns ``(hierarchy, group, teacher, assignment, {unit_id: unit})``.
    Any of group / teacher / assignment / a unit may be ``None`` -- the
    caller must treat that as a business/authorization rejection, roll
    back, and 404 or redirect; it must never "keep going".
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
    units = {}
    for uid in sorted({u for u in unit_ids if u is not None}):
        units[uid] = Unit.query.filter_by(id=uid).with_for_update().first()
    return hierarchy, group, teacher, assignment, units


def _authz_broken(group, teacher, assignment):
    """True when the locked rows no longer authorize a Unit mutation for
    the current Teacher -- caller rolls back and 404s (no disclosure)."""
    if group is None or teacher is None or assignment is None:
        return True
    if teacher.role != UserRole.TEACHER.value or teacher.status != UserStatus.ACTIVE.value:
        return True
    return assignment.status != _ASSIGNMENT_ACTIVE


def _hierarchy_active_error(hierarchy, group, term_id, level_id, course_id):
    """`None` if the locked Group and its locked AcademicTerm / Level /
    Course all exist and are active (so a create / edit / reorder /
    reactivate may proceed), else a Teacher-facing message."""
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
        )
        if row is None or row.status != _ACTIVE
    ]
    if labels:
        verb = "is" if len(labels) == 1 else "are"
        return (
            "Curriculum can only be released while the group and its academic "
            f"term, course, and level are all active. The {_join_labels(labels)} {verb} archived."
        )
    return None


@teacher_bp.get("/groups/<group_public_id>/units")
@roles_required(UserRole.TEACHER.value)
def group_units(group_public_id):
    group = _teacher_group_or_404(group_public_id)
    return redirect(url_for("workspace.teacher_group", group_public_id=group.public_id,
                            _anchor="group-content"))
