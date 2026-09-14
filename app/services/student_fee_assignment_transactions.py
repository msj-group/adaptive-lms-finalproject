"""The single deterministic lock chain for every student fee assignment
write (Phase 5 / M03).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering. Route-level 404 / redirect / flash handling belongs in
``app/blueprints/admin/fee_assignments.py``.

**Assignment**::

    AcademicTerm -> Level -> Course     (lock_academic_hierarchy, which owns
                                         the one deliberate reset)
    -> Group
    -> enrolled Student User
    -> Enrollment
    -> acting Administrator User
    -> FeePlan
    -> active FeePlanItems (ascending internal id)
    -> the Enrollment's existing StudentFeeAssignments (ascending internal id)

**Cancellation**::

    AcademicTerm -> Level -> Course -> Group
    -> enrolled Student User -> Enrollment
    -> acting Administrator User
    -> StudentFeeAssignment

There is one function rather than one per route: the order is the thing that
must not vary, and a caller passes only the links its write has.

- The prefix up to the Enrollment is the one Enrollment withdrawal and
  reactivation already take (``Group -> Student -> Enrollment``), preceded by
  the Group's ancestors in the project's global order. **The Enrollment lock
  serializes assignment, cancellation and withdrawal** of the same
  Enrollment, so none of them decides against a stale picture of the others.
- The acting Administrator follows the Enrollment, then the FeePlan: the same
  ``Administrator -> FeePlan`` order M02 takes, so assignment serializes
  against a plan's archival or restoration on the **FeePlan lock**.
- The id-only reads that choose which items and which assignments to lock run
  **after** the FeePlan and Enrollment locks are held. Every item change takes
  the FeePlan lock first and every assignment insert or cancellation takes the
  Enrollment lock first, so neither set can change between the read and the
  row locks.

**Nothing here authorizes anything.** Every value may come back ``None`` -- a
suspended actor, a vanished row -- and the helpers below only report what the
locked rows say. The caller rolls back and 404s or redirects.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no REPEATABLE
READ snapshot isolation, so this runs correctly in tests without locking
anything. Tests assert the *requested* lock set and order (structural); they
prove nothing about real InnoDB blocking.
"""

from app.extensions import db
from app.models import (
    MAX_ACTIVE_FEE_PLAN_ITEMS,
    Enrollment,
    FeePlan,
    FeePlanItem,
    FeePlanItemKind,
    FeePlanItemStatus,
    FeePlanStatus,
    StudentFeeAssignment,
    StudentFeeAssignmentStatus,
    User,
    UserRole,
)
from app.models.fee_plan_item import normalize_fee_plan_item_label
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.fee_plan_transactions import labels_are_unique
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.money import validate_amount

_STUDENT = UserRole.STUDENT.value
_ITEM_ACTIVE = FeePlanItemStatus.ACTIVE.value
_PLAN_ACTIVE = FeePlanStatus.ACTIVE.value
_ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
_KIND_VALUES = frozenset(kind.value for kind in FeePlanItemKind)


class FeeAssignmentLocks:
    """The rows one M03 lock chain returned.

    ``items`` and ``assignments`` map an internal id to the locked row (or
    ``None`` when the id no longer names a row). ``actor``, ``plan`` and
    ``items`` carry the same meaning as in
    :class:`~app.services.fee_plan_transactions.FeePlanLocks`, so M02's
    ``administrator_authz_broken`` and ``locked_active_items`` apply to this
    object unchanged. Deliberately ``__slots__``-ed.
    """

    __slots__ = (
        "hierarchy",
        "group",
        "student",
        "enrollment",
        "actor",
        "plan",
        "items",
        "assignments",
    )

    def __init__(
        self,
        hierarchy,
        group=None,
        student=None,
        enrollment=None,
        actor=None,
        plan=None,
        items=None,
        assignments=None,
    ):
        self.hierarchy = hierarchy
        self.group = group
        self.student = student
        self.enrollment = enrollment
        self.actor = actor
        self.plan = plan
        self.items = {} if items is None else items
        self.assignments = {} if assignments is None else assignments


def _lock_rows(model, ids):
    return {
        row_id: model.query.filter_by(id=row_id).with_for_update().first()
        for row_id in sorted({row_id for row_id in ids if row_id is not None})
    }


def lock_fee_assignment_chain(
    group_public_id,
    term_id,
    level_id,
    course_id,
    student_id,
    enrollment_id,
    actor_id,
    plan_id=None,
    include_plan_items=False,
    include_enrollment_assignments=False,
    assignment_ids=(),
):
    """Take the M03 lock order in one open transaction. Returns a
    :class:`FeeAssignmentLocks`.

    Every id except `group_public_id` is **internal**, discovered by a
    non-locking read of the URL's public ids before this call. Those reads
    only decide *which* rows to lock: nesting, lifecycle, versions and every
    business rule are re-proved against the locked rows afterwards.

    `include_plan_items` adds every active item of the locked plan;
    `include_enrollment_assignments` adds every existing assignment of the
    locked Enrollment, whatever its status.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    student = None
    if student_id is not None:
        student = User.query.filter_by(id=student_id).with_for_update().first()
    enrollment = None
    if enrollment_id is not None:
        enrollment = Enrollment.query.filter_by(id=enrollment_id).with_for_update().first()
    actor = None
    if actor_id is not None:
        actor = User.query.filter_by(id=actor_id).with_for_update().first()
    plan = None
    if plan_id is not None:
        plan = FeePlan.query.filter_by(id=plan_id).with_for_update().first()

    items = {}
    if plan is not None and include_plan_items:
        items = _lock_rows(
            FeePlanItem,
            (
                row.id
                for row in db.session.query(FeePlanItem.id)
                .filter(FeePlanItem.fee_plan_id == plan.id, FeePlanItem.status == _ITEM_ACTIVE)
                .all()
            ),
        )

    wanted = set(assignment_ids)
    if enrollment is not None and include_enrollment_assignments:
        wanted.update(
            row.id
            for row in db.session.query(StudentFeeAssignment.id)
            .filter(StudentFeeAssignment.enrollment_id == enrollment.id)
            .all()
        )
    assignments = _lock_rows(StudentFeeAssignment, wanted)

    return FeeAssignmentLocks(
        hierarchy,
        group=group,
        student=student,
        enrollment=enrollment,
        actor=actor,
        plan=plan,
        items=items,
        assignments=assignments,
    )


def enrollment_nesting_broken(locks, group_public_id, enrollment_id):
    """``True`` unless the locked Enrollment still belongs to the URL's
    Group and to a Student-role account -- the same nesting the Manage
    Members routes require."""
    group, student, enrollment = locks.group, locks.student, locks.enrollment
    return (
        group is None
        or group.public_id != group_public_id
        or enrollment is None
        or enrollment.id != enrollment_id
        or enrollment.group_id != group.id
        or student is None
        or enrollment.student_id != student.id
        or student.role != _STUDENT
    )


def hierarchy_moved(locks, term_id, level_id, course_id):
    """``True`` when the locked Group no longer sits under the ancestors the
    chain locked -- the chain holds the wrong rows, so nothing may be decided
    from them."""
    group = locks.group
    term = locks.hierarchy.term(term_id)
    level = locks.hierarchy.level(level_id)
    course = locks.hierarchy.course(course_id)
    return (
        group is None
        or term is None
        or level is None
        or course is None
        or group.academic_term_id != term.id
        or group.course_id != course.id
        or course.level_id != level.id
    )


def locked_academic_statuses(locks, term_id, level_id, course_id):
    """The locked Group, Course, Level and Academic Term statuses."""
    return (
        locks.group.status,
        locks.hierarchy.course(course_id).status,
        locks.hierarchy.level(level_id).status,
        locks.hierarchy.term(term_id).status,
    )


def fee_plan_assignable(plan):
    """Whether `plan` may be assigned now: ``active``, and frozen by a first
    activation that is still recorded."""
    return (
        plan is not None
        and plan.status == _PLAN_ACTIVE
        and plan.first_activated_at is not None
        and plan.first_activated_by_id is not None
    )


def fee_plan_items_valid(items):
    """Whether `items` -- one plan's active items, ascending id -- are a
    complete, valid fee definition: one to
    :data:`~app.models.fee_plan_item.MAX_ACTIVE_FEE_PLAN_ITEMS` items, each
    with a known kind, a normalized label and an exact amount inside the
    money bounds, and no two sharing a label.

    Activation already proved all of this and froze the plan; it is proved
    again because a row the application did not write must never become a
    Student's charge.
    """
    if not 1 <= len(items) <= MAX_ACTIVE_FEE_PLAN_ITEMS:
        return False
    for item in items:
        if item.status != _ITEM_ACTIVE or item.kind not in _KIND_VALUES:
            return False
        label, error = normalize_fee_plan_item_label(item.label)
        if error is not None or label != item.label:
            return False
        try:
            validate_amount(item.amount)
        except ValueError:
            return False
    return labels_are_unique(items)


def locked_enrollment_assignments(locks):
    """The locked assignment rows that still belong to the locked
    Enrollment, ascending internal id."""
    enrollment = locks.enrollment
    if enrollment is None:
        return []
    return [
        row
        for _row_id, row in sorted(locks.assignments.items())
        if row is not None and row.enrollment_id == enrollment.id
    ]


def assigned_rows(rows):
    return [row for row in rows if row.status == _ASSIGNED]


def latest_change(rows):
    """The latest ``updated_at`` among `rows`, or ``None``."""
    return max((row.updated_at for row in rows), default=None)
