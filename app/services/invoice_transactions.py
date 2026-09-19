"""The deterministic lock chains for every invoice write (Phase 5 / M04).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering. Route-level 404 / redirect / flash handling belongs in
``app/blueprints/admin/invoices.py``.

**Draft creation**::

    AcademicTerm -> Level -> Course     (lock_academic_hierarchy, which owns
                                         the one deliberate reset)
    -> Group -> enrolled Student User -> Enrollment
    -> acting Administrator User
    -> StudentFeeAssignment
    -> FeePlan
    -> active FeePlanItems (ascending internal id)
    -> the assignment's existing Invoices (ascending internal id)

**Line changes and issue**::

    ... -> acting Administrator User -> StudentFeeAssignment
    -> Invoice
    -> the affected InvoiceItems and their active siblings (ascending id)
    -> InvoiceNumberSequence (issue only; see lock_invoice_number_sequence)

**Cancellation**::

    ... -> acting Administrator User -> StudentFeeAssignment -> Invoice

There is one chain function rather than one per route: the order is what must
not vary, and a caller passes only the links its write has.

- The prefix through the acting Administrator is Phase 5 / M03's, so invoice
  writes serialize with fee assignment, cancellation and Enrollment
  withdrawal on the **Enrollment** lock.
- **The StudentFeeAssignment lock** serializes draft creation against the
  assignment's cancellation (which, since M04, locks the assignment's
  invoices after the assignment -- :func:`lock_assignment_invoices`) and
  against an invoice's cancellation, so the one-open-invoice rule is decided
  against current rows.
- **The Invoice lock** is the aggregate's serialization point: every line
  change, the issue, the cancellation and the audit event of each take it
  first.
- The id-only reads that choose which plan items, invoices and lines to lock
  run **after** the lock of their owner is held. Every insert or status change
  of those rows takes the same owner lock first, so the set cannot change
  between the read and the row locks.

**Nothing here authorizes anything.** Every value may come back ``None`` and
the helpers only report what the locked rows say; the caller rolls back and
404s or redirects.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no REPEATABLE
READ snapshot isolation, so this runs correctly in tests without locking
anything. Tests assert the *requested* lock set and order (structural); they
prove nothing about real InnoDB blocking.
"""

from app.extensions import db
from app.models import (
    MAX_ACTIVE_INVOICE_ITEMS,
    MAX_INVOICE_ITEM_ROWS,
    MAX_INVOICE_SEQUENCE_NUMBER,
    Enrollment,
    FeePlan,
    FeePlanItem,
    FeePlanItemStatus,
    FeePlanStatus,
    Invoice,
    InvoiceItem,
    InvoiceItemKind,
    InvoiceItemStatus,
    InvoiceNumberSequence,
    InvoiceStatus,
    StudentFeeAssignment,
    User,
)
from app.models.invoice import format_invoice_number
from app.models.invoice_item import normalize_invoice_item_label
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.fee_plan_transactions import labels_are_unique
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.money import CURRENCY_CODE, validate_amount

_PLAN_ITEM_ACTIVE = FeePlanItemStatus.ACTIVE.value
_INVOICEABLE_PLAN_STATUSES = frozenset({FeePlanStatus.ACTIVE.value, FeePlanStatus.ARCHIVED.value})
_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value
_OPEN = frozenset({InvoiceStatus.DRAFT.value, InvoiceStatus.ISSUED.value})
_KIND_VALUES = frozenset(kind.value for kind in InvoiceItemKind)


class InvoiceLocks:
    """The rows one M04 lock chain returned.

    ``plan_items``, ``invoices`` and ``items`` map an internal id to the
    locked row (or ``None`` when the id no longer names a row). Deliberately
    ``__slots__``-ed, so a typo in a caller raises instead of reading
    ``None``. ``actor`` carries the same meaning as in M02's
    :class:`~app.services.fee_plan_transactions.FeePlanLocks`, so
    ``administrator_authz_broken`` applies to this object unchanged.
    """

    __slots__ = (
        "hierarchy",
        "group",
        "student",
        "enrollment",
        "actor",
        "assignment",
        "plan",
        "plan_items",
        "invoices",
        "invoice",
        "items",
    )

    def __init__(self, hierarchy, **rows):
        self.hierarchy = hierarchy
        for name in self.__slots__[1:]:
            default = {} if name in ("plan_items", "invoices", "items") else None
            setattr(self, name, rows.get(name, default))


def _lock_by_id(model, row_id):
    if row_id is None:
        return None
    return model.query.filter_by(id=row_id).with_for_update().first()


def _lock_rows(model, ids):
    return {
        row_id: model.query.filter_by(id=row_id).with_for_update().first()
        for row_id in sorted({row_id for row_id in ids if row_id is not None})
    }


def lock_invoice_chain(
    group_public_id,
    term_id,
    level_id,
    course_id,
    student_id,
    enrollment_id,
    actor_id,
    assignment_id,
    plan_id=None,
    include_plan_items=False,
    include_assignment_invoices=False,
    invoice_id=None,
    item_ids=(),
    include_active_items=False,
):
    """Take the M04 lock order in one open transaction. Returns an
    :class:`InvoiceLocks`.

    Every id except `group_public_id` is **internal**, discovered by a
    non-locking read of the URL's public ids before this call. Those reads
    only decide *which* rows to lock; nesting, lifecycle, versions and every
    business rule are re-proved against the locked rows afterwards.

    `include_plan_items` adds every active item of the locked plan;
    `include_assignment_invoices` every invoice of the locked assignment,
    whatever its status; `include_active_items` every active line of the
    locked invoice, besides `item_ids`.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    student = _lock_by_id(User, student_id)
    enrollment = _lock_by_id(Enrollment, enrollment_id)
    actor = _lock_by_id(User, actor_id)
    assignment = _lock_by_id(StudentFeeAssignment, assignment_id)
    plan = _lock_by_id(FeePlan, plan_id)

    plan_items = {}
    if plan is not None and include_plan_items:
        plan_items = _lock_rows(
            FeePlanItem,
            (
                row.id
                for row in db.session.query(FeePlanItem.id)
                .filter(FeePlanItem.fee_plan_id == plan.id, FeePlanItem.status == _PLAN_ITEM_ACTIVE)
                .all()
            ),
        )

    invoices = {}
    if assignment is not None and include_assignment_invoices:
        invoices = _lock_rows(Invoice, _assignment_invoice_ids(assignment.id))

    invoice = _lock_by_id(Invoice, invoice_id)
    items = {}
    if invoice is not None:
        wanted = set(item_ids)
        if include_active_items:
            wanted.update(
                row.id
                for row in db.session.query(InvoiceItem.id)
                .filter(InvoiceItem.invoice_id == invoice.id, InvoiceItem.status == _ITEM_ACTIVE)
                .all()
            )
        items = _lock_rows(InvoiceItem, wanted)

    return InvoiceLocks(
        hierarchy,
        group=group,
        student=student,
        enrollment=enrollment,
        actor=actor,
        assignment=assignment,
        plan=plan,
        plan_items=plan_items,
        invoices=invoices,
        invoice=invoice,
        items=items,
    )


def _assignment_invoice_ids(assignment_id):
    return [
        row.id
        for row in db.session.query(Invoice.id)
        .filter(Invoice.student_fee_assignment_id == assignment_id)
        .all()
    ]


def lock_assignment_invoices(assignment_id):
    """Every invoice of `assignment_id`, locked in ascending internal id.

    Student Fee Assignment cancellation calls this **after** its own chain
    has locked the assignment: every invoice insert and every invoice
    cancellation takes that assignment lock first, so the set read here is
    current.
    """
    return _lock_rows(Invoice, _assignment_invoice_ids(assignment_id))


def lock_invoice_number_sequence(calendar_year, moment):
    """The locked sequence row of `calendar_year`, created (at 0) if the year
    has none -- the last lock of an issue.

    The row is looked up by a plain read and locked by primary key, never
    locked by a range that matches nothing: on InnoDB two such gap locks
    would let two first issues of a year deadlock. Two first issues that both
    insert the row meet the unique ``calendar_year`` constraint instead; the
    flush raises ``IntegrityError``, which the caller rolls back and reports
    generically. Nothing is consumed either way.
    """
    sequence_id = (
        db.session.query(InvoiceNumberSequence.id)
        .filter(InvoiceNumberSequence.calendar_year == calendar_year)
        .scalar()
    )
    if sequence_id is None:
        row = InvoiceNumberSequence(
            calendar_year=calendar_year, last_number=0, created_at=moment, updated_at=moment
        )
        db.session.add(row)
        db.session.flush()
        sequence_id = row.id
    return _lock_by_id(InvoiceNumberSequence, sequence_id)


def allocate_invoice_number(sequence, moment):
    """Increment the **locked** `sequence` exactly once and return the new
    ``INV-YYYY-NNNNNN``, or ``None`` -- changing nothing -- when the year's
    six-digit capacity is exhausted."""
    if sequence is None or sequence.last_number >= MAX_INVOICE_SEQUENCE_NUMBER:
        return None
    sequence.last_number = sequence.last_number + 1
    sequence.updated_at = moment
    return format_invoice_number(sequence.calendar_year, sequence.last_number)


def invoice_number_taken(number):
    """Whether any invoice already holds `number`. ``uq_invoices_invoice_number``
    is the final defense."""
    query = db.session.query(Invoice.id).filter(Invoice.invoice_number == number)
    return bool(db.session.query(query.exists()).scalar())


# ---------------------------------------------------------------------------
# Re-proving facts against the locked rows
# ---------------------------------------------------------------------------


def assignment_nesting_broken(locks, assignment_id, assignment_public_id):
    """``True`` unless the locked assignment is the URL's and still belongs to
    the locked Enrollment."""
    assignment, enrollment = locks.assignment, locks.enrollment
    return (
        assignment is None
        or enrollment is None
        or assignment.id != assignment_id
        or assignment.public_id != assignment_public_id
        or assignment.enrollment_id != enrollment.id
    )


def invoice_nesting_broken(locks, invoice_id, invoice_public_id):
    """``True`` unless the locked invoice is the URL's, still belongs to the
    locked assignment and is not deleted (Phase 5 / M10): a deleted invoice is
    no longer anything a write may act on."""
    invoice, assignment = locks.invoice, locks.assignment
    return (
        invoice is None
        or assignment is None
        or invoice.id != invoice_id
        or invoice.public_id != invoice_public_id
        or invoice.student_fee_assignment_id != assignment.id
        or invoice.deleted_at is not None
    )


def locked_plan_items(locks):
    """The locked plan's active items, ascending internal id, re-proved from
    the locked rows."""
    plan = locks.plan
    if plan is None:
        return []
    return [
        item
        for _item_id, item in sorted(locks.plan_items.items())
        if item is not None and item.fee_plan_id == plan.id and item.status == _PLAN_ITEM_ACTIVE
    ]


def locked_assignment_invoices(locks):
    """The locked invoices that still belong to the locked assignment,
    ascending internal id."""
    assignment = locks.assignment
    if assignment is None:
        return []
    return [
        row
        for _row_id, row in sorted(locks.invoices.items())
        if row is not None and row.student_fee_assignment_id == assignment.id
    ]


def open_invoices(rows):
    """The live ``draft`` or ``issued`` rows among `rows`. A deleted invoice
    (Phase 5 / M10) is no assignment's open invoice."""
    return [
        row
        for row in rows
        if row is not None and row.status in _OPEN and row.deleted_at is None
    ]


def latest_change(rows):
    """The latest ``updated_at`` among `rows`, or ``None``."""
    return max((row.updated_at for row in rows), default=None)


def locked_item(locks, item_id, item_public_id):
    """The locked line `item_id`, only while it is the URL's line of the
    locked invoice; otherwise ``None``."""
    invoice, item = locks.invoice, locks.items.get(item_id)
    if (
        invoice is None
        or item is None
        or item.invoice_id != invoice.id
        or item.public_id != item_public_id
    ):
        return None
    return item


def locked_active_items(locks):
    """The locked invoice's active lines, ascending internal id, re-proved
    from the locked rows."""
    invoice = locks.invoice
    if invoice is None:
        return []
    return [
        item
        for _item_id, item in sorted(locks.items.items())
        if item is not None and item.invoice_id == invoice.id and item.status == _ITEM_ACTIVE
    ]


def invoice_rows_for_snapshot(invoice):
    """Every line of `invoice`, ascending internal id, read **after** the
    invoice lock (at most one past :data:`MAX_INVOICE_ITEM_ROWS`).

    Rows this transaction locked come back as the same objects, so a change
    made to them is what the "after" snapshot sees. A removed line never
    changes again, and every other line change takes the invoice lock first,
    so the rows read here are current.
    """
    return (
        InvoiceItem.query.filter(InvoiceItem.invoice_id == invoice.id)
        .order_by(InvoiceItem.id.asc())
        .limit(MAX_INVOICE_ITEM_ROWS + 1)
        .all()
    )


def fee_plan_invoiceable(plan):
    """Whether a draft may still be copied from `plan`: activated at least
    once -- hence frozen -- with its activation recorded, in ``LYD``, and
    ``active`` or ``archived``. Archiving the plan does not stop invoicing an
    assignment that already uses it."""
    return (
        plan is not None
        and plan.status in _INVOICEABLE_PLAN_STATUSES
        and plan.first_activated_at is not None
        and plan.first_activated_by_id is not None
        and plan.currency_code == CURRENCY_CODE
    )


def invoice_items_valid(items):
    """Whether `items` -- an invoice's active lines, ascending id -- are a
    complete, valid charge: one to
    :data:`~app.models.invoice_item.MAX_ACTIVE_INVOICE_ITEMS` lines, each with
    a known kind, a normalized label and an exact amount inside the money
    bounds, and no two sharing a label. The same definition a fee plan's items
    passed at activation."""
    if not 1 <= len(items) <= MAX_ACTIVE_INVOICE_ITEMS:
        return False
    for item in items:
        if item.status != _ITEM_ACTIVE or item.kind not in _KIND_VALUES:
            return False
        label, error = normalize_invoice_item_label(item.label)
        if error is not None or label != item.label:
            return False
        try:
            validate_amount(item.amount)
        except ValueError:
            return False
    return labels_are_unique(items)
