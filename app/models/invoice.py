from app.models.code_types import CODE_COLLATION
import re
import uuid

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import InvoiceStatus
from app.models.submission_feedback import whole_second_utc
from app.services.money import CURRENCY_CODE

#: ``INV-YYYY-NNNNNN``: fifteen ASCII characters.
INVOICE_NUMBER_LENGTH = 15

#: The six-digit annual capacity. Issuing past it is refused, never wrapped.
MAX_INVOICE_SEQUENCE_NUMBER = 999999

#: ``YYYY`` is always four digits.
MIN_INVOICE_YEAR = 1000
MAX_INVOICE_YEAR = 9999

_NUMBER_SHAPE = re.compile(r"INV-(?P<year>[0-9]{4})-(?P<sequence>[0-9]{6})")

#: Phase 5 / M10: a visible deletion's reason is an audit reason's width.
DELETION_REASON_MAX_LENGTH = 500

#: Phase 5 / M10: the three tombstone columns an invoice, a payment
#: transaction and a receipt each carry -- all set together, once, or none.
DELETION_COLUMNS = ("deleted_at", "deleted_by_id", "deletion_reason")


class FinancialHistoryError(RuntimeError):
    """A write tried to delete financial history, or to change something the
    Phase 5 / M04 invoice rules say never changes.

    Raised by the ORM guards on the invoice tables. No route catches it: it
    marks a programming error, never a user mistake.
    """


def _bounded_int(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def format_invoice_number(calendar_year, sequence_number):
    """``INV-YYYY-NNNNNN`` for one allocated number, zero-padded."""
    if not _bounded_int(calendar_year, MIN_INVOICE_YEAR, MAX_INVOICE_YEAR) or not _bounded_int(
        sequence_number, 1, MAX_INVOICE_SEQUENCE_NUMBER
    ):
        raise ValueError(
            "An invoice number needs a four-digit year and a sequence number from 1 to "
            f"{MAX_INVOICE_SEQUENCE_NUMBER}"
        )
    return f"INV-{calendar_year:04d}-{sequence_number:06d}"


def invoice_number_is_valid(value):
    """Whether `value` is exactly a system invoice number: ASCII ``INV-``, a
    four-digit year from 1000, ``-`` and a six-digit sequence number from 1."""
    if not isinstance(value, str):
        return False
    match = _NUMBER_SHAPE.fullmatch(value)
    return (
        match is not None
        and int(match.group("year")) >= MIN_INVOICE_YEAR
        and int(match.group("sequence")) >= 1
    )


_STATUS_VALUES = tuple(status.value for status in InvoiceStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"
_CURRENCY_CHECK_SQL = f"currency_code = '{CURRENCY_CODE}'"

#: Who issued the invoice and when are recorded together or not at all.
_ISSUE_PAIR_SQL = (
    "(issued_at IS NULL AND issued_by_id IS NULL)"
    " OR (issued_at IS NOT NULL AND issued_by_id IS NOT NULL)"
)

#: Likewise the cancellation.
_CANCELLATION_PAIR_SQL = (
    "(cancelled_at IS NULL AND cancelled_by_id IS NULL)"
    " OR (cancelled_at IS NOT NULL AND cancelled_by_id IS NOT NULL)"
)

#: A number exists exactly when the invoice has been issued, so a draft --
#: and a draft cancelled before issue -- never holds one.
_NUMBER_ISSUE_SQL = (
    "(invoice_number IS NULL AND issued_at IS NULL)"
    " OR (invoice_number IS NOT NULL AND issued_at IS NOT NULL)"
)

#: The portable part of the ``INV-YYYY-NNNNNN`` shape. ``LENGTH`` counts
#: bytes on MySQL, so a non-ASCII character is refused there too; the digits
#: themselves are proved by :func:`invoice_number_is_valid`.
_NUMBER_FORMAT_SQL = (
    "invoice_number IS NULL"
    f" OR (invoice_number LIKE 'INV-____-______' AND LENGTH(invoice_number) = {INVOICE_NUMBER_LENGTH})"
)

#: The lifecycle truth table: a draft was neither issued nor cancelled, an
#: issued invoice was issued and not cancelled, and a cancelled invoice
#: carries its cancellation (and, if it was issued first, its issue).
_LIFECYCLE_STATE_SQL = (
    "(status = 'draft' AND issued_at IS NULL AND cancelled_at IS NULL)"
    " OR (status = 'issued' AND issued_at IS NOT NULL AND cancelled_at IS NULL)"
    " OR (status = 'cancelled' AND cancelled_at IS NOT NULL)"
)

_TIMESTAMPS_ORDERED_SQL = (
    "updated_at >= created_at"
    " AND (issued_at IS NULL OR (issued_at >= created_at AND updated_at >= issued_at))"
    " AND (cancelled_at IS NULL"
    " OR (cancelled_at >= created_at AND updated_at >= cancelled_at"
    " AND (issued_at IS NULL OR cancelled_at >= issued_at)))"
)


def deletion_state_sql(anchor, extra=""):
    """Phase 5 / M10: the tombstone truth table -- no deletion, or its moment,
    its Administrator and a non-empty reason together, no earlier than
    `anchor` and no later than ``updated_at``. `extra` narrows which rows may
    be deleted."""
    return (
        "(deleted_at IS NULL AND deleted_by_id IS NULL AND deletion_reason IS NULL)"
        " OR (deleted_at IS NOT NULL AND deleted_by_id IS NOT NULL"
        " AND deletion_reason IS NOT NULL AND LENGTH(deletion_reason) > 0"
        f"{extra} AND deleted_at >= {anchor} AND updated_at >= deleted_at)"
    )


#: A cancelled invoice is never deleted: cancellation and deletion stay two
#: distinct outcomes.
_DELETION_STATE_SQL = deletion_state_sql("created_at", " AND status IN ('draft', 'issued')")


class Invoice(db.Model):
    """One invoice for one Student Fee Assignment (Phase 5 / M04).

    **It belongs to the assignment, and only the assignment.**
    ``student_fee_assignment_id`` is a plain foreign key; the Enrollment,
    Student, Group, academic chain and fee plan are read through it and are
    not duplicated here. An assignment has at most one ``draft`` or
    ``issued`` invoice at a time -- an application invariant proved under the
    assignment's lock, because MySQL has no portable partial unique index.

    **The amounts are the invoice's own.** A draft copies the kind, label and
    amount of every active item of the assigned fee plan into
    :class:`~app.models.invoice_item.InvoiceItem` rows, which keep no
    reference to the plan's items. The visible total is added from the
    current active items in Python ``Decimal`` and is **not stored**.
    ``currency_code`` is stored, as on a fee plan, so no amount is ever read
    without its unit; it is always ``LYD``.

    **Lifecycle.** See :class:`~app.models.enums.InvoiceStatus`::

        draft -> issued -> cancelled
        draft -> cancelled

    ``invoice_number`` is allocated once, at the first issue, and never
    changes, is never reused and is kept by a cancelled invoice. Issued
    invoices stay editable in M04 because no payment mechanism exists yet;
    every change is recorded by a
    :class:`~app.models.payment_audit_event.PaymentAuditEvent` in the same
    transaction. A cancelled invoice is read-only forever.

    **Nothing is ever physically deleted.** No route, cascade or ``ondelete``
    exists, and the ORM guards below refuse a delete, any change to a
    cancelled invoice, and a change to the number, the issue attribution or
    the owning assignment once set.

    **Visible deletion (Phase 5 / M10).** A draft or issued invoice may be
    *deleted*: ``deleted_at``, ``deleted_by_id`` and ``deletion_reason`` are
    set together, once, with the version moved by one and nothing else
    changed (``ck_invoices_deletion_state``). The row, its lines and its
    events stay; a deleted invoice leaves every live list, balance and report,
    appears in Deleted Records, and never changes again. A cancelled invoice
    is never deleted -- cancellation keeps its own meaning.

    **``version``** is the aggregate's concurrency signal: it moves by
    exactly one per recorded change to the invoice or any of its items.

    Database invariants (final defense only):

    - ``public_id`` and ``uq_invoices_invoice_number`` unique (NULLs allowed);
    - ``ck_invoices_status_valid``, ``ck_invoices_currency_code`` and
      ``ck_invoices_version_positive``;
    - ``ck_invoices_issue_pair``, ``ck_invoices_cancellation_pair`` and
      ``ck_invoices_number_matches_issue``;
    - ``ck_invoices_number_format``;
    - ``ck_invoices_lifecycle_state`` and ``ck_invoices_timestamps_ordered``;
    - ``ck_invoices_deletion_state`` (M10).

    Indexes:

    - ``ix_invoices_assignment_status_id`` (``student_fee_assignment_id``,
      ``status``, ``id``) -- the open-invoice check, the id-only read that
      picks the rows to lock, one assignment's history and the foreign key;
    - ``ix_invoices_issued_by_id`` and ``ix_invoices_cancelled_by_id`` -- the
      two ``users`` foreign keys;
    - ``ix_invoices_deleted_at_id`` (``deleted_at``, ``id``) -- Deleted
      Records, newest deletion first -- and ``ix_invoices_deleted_by_id`` (M10).

    **No MySQL execution plan has been measured for this table.** No ORM
    relationship is declared in either direction.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        db.UniqueConstraint("invoice_number", name="uq_invoices_invoice_number"),
        db.UniqueConstraint("id", "student_id", name="uq_invoices_id_student"),
        db.ForeignKeyConstraint(["enrollment_id", "student_id"], ["enrollments.id", "enrollments.student_id"], name="fk_invoices_episode_student"),
        db.Index("ix_invoices_student_status", "student_id", "status", "id"),
        db.CheckConstraint("charge_amount IS NULL OR (charge_amount >= 0 AND MOD(charge_amount, 0.001) = 0)", name="ck_invoices_charge_amount"),
        db.CheckConstraint("discount_amount >= 0 AND (charge_amount IS NULL OR discount_amount <= charge_amount) AND MOD(discount_amount, 0.001) = 0", name="ck_invoices_discount_amount"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_invoices_status_valid"),
        db.CheckConstraint(_CURRENCY_CHECK_SQL, name="ck_invoices_currency_code"),
        db.CheckConstraint("version > 0", name="ck_invoices_version_positive"),
        db.CheckConstraint(_ISSUE_PAIR_SQL, name="ck_invoices_issue_pair"),
        db.CheckConstraint(_CANCELLATION_PAIR_SQL, name="ck_invoices_cancellation_pair"),
        db.CheckConstraint(_NUMBER_ISSUE_SQL, name="ck_invoices_number_matches_issue"),
        db.CheckConstraint(_NUMBER_FORMAT_SQL, name="ck_invoices_number_format"),
        db.CheckConstraint(_LIFECYCLE_STATE_SQL, name="ck_invoices_lifecycle_state"),
        db.CheckConstraint(_TIMESTAMPS_ORDERED_SQL, name="ck_invoices_timestamps_ordered"),
        db.CheckConstraint(_DELETION_STATE_SQL, name="ck_invoices_deletion_state"),
        db.Index(
            "ix_invoices_assignment_status_id", "student_fee_assignment_id", "status", "id"
        ),
        db.Index("ix_invoices_issued_by_id", "issued_by_id"),
        db.Index("ix_invoices_cancelled_by_id", "cancelled_by_id"),
        db.Index("ix_invoices_deleted_at_id", "deleted_at", "id"),
        db.Index("ix_invoices_deleted_by_id", "deleted_by_id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    student_fee_assignment_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("student_fee_assignments.id"),
        nullable=True,
    )
    student_id = db.Column(db.BigInteger, db.ForeignKey("users.id", name="fk_invoices_student_id"), nullable=False)
    enrollment_id = db.Column(db.BigInteger, db.ForeignKey("enrollments.id", name="fk_invoices_enrollment_id"), nullable=True, index=True)
    charge_amount = db.Column(db.Numeric(19, 4), nullable=True)
    discount_amount = db.Column(db.Numeric(19, 4), nullable=False, default=0)
    discount_kind = db.Column(db.String(16), nullable=False, default="none")
    discount_value = db.Column(db.String(32), nullable=False, default="0")
    discount_actor_id = db.Column(db.BigInteger, db.ForeignKey("users.id", name="fk_invoices_discount_actor_id"), nullable=True)
    discount_reason = db.Column(db.String(500), nullable=True)
    course_snapshot = db.Column(db.JSON, nullable=True)
    currency_code = db.Column(db.String(3, collation=CODE_COLLATION), nullable=False, default=CURRENCY_CODE)
    status = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False, default=InvoiceStatus.DRAFT.value)
    #: NULL exactly until the first issue; then permanent.
    invoice_number = db.Column(db.String(INVOICE_NUMBER_LENGTH), nullable=True)
    issued_at = db.Column(db.DateTime, nullable=True)
    issued_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancelled_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defaults are defense in depth only; every write supplies its own
    #: post-lock whole-second moment. Deliberately no ``onupdate`` hook.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    #: Phase 5 / M10: the visible deletion. Named foreign key, because the
    #: revision that added it to an existing table must be able to name it.
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id", name="fk_invoices_deleted_by_id"),
        nullable=True,
    )
    deletion_reason = db.Column(db.String(DELETION_REASON_MAX_LENGTH), nullable=True)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid invoice status: {value}")
        return value

    @validates("deletion_reason")
    def validate_deletion_reason(self, _key, value):
        return validate_deletion_reason(value)

    @validates("currency_code")
    def validate_currency_code(self, _key, value):
        if value != CURRENCY_CODE:
            raise ValueError(f"Invoice currency must be {CURRENCY_CODE}")
        return value

    @validates("invoice_number")
    def validate_invoice_number(self, _key, value):
        if value is not None and not invoice_number_is_valid(value):
            raise ValueError("An invoice number must be exactly INV-YYYY-NNNNNN")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Invoice version must be a positive integer")
        return value

    @property
    def is_draft(self):
        return self.status == InvoiceStatus.DRAFT.value

    @property
    def is_issued(self):
        return self.status == InvoiceStatus.ISSUED.value

    @property
    def is_cancelled(self):
        return self.status == InvoiceStatus.CANCELLED.value

    @property
    def is_open(self):
        """``draft`` or ``issued``, and not deleted: the one invoice an
        assignment may hold."""
        return self.deleted_at is None and self.status in (
            InvoiceStatus.DRAFT.value,
            InvoiceStatus.ISSUED.value,
        )

    @property
    def is_deleted(self):
        return self.deleted_at is not None


def validate_deletion_reason(value):
    """``None`` or normalized, non-empty audit-reason text (Phase 5 / M10)."""
    if value is None:
        return value
    # Imported here: the audit model imports this module.
    from app.models.payment_audit_event import normalize_audit_reason

    normalized, error = normalize_audit_reason(value)
    if error is not None or normalized != value:
        raise ValueError("A deletion reason must be normalized plain text or None")
    return value


# ---------------------------------------------------------------------------
# ORM guards shared by the invoice tables
# ---------------------------------------------------------------------------


def is_changing(target):
    """Whether this flush writes any column of `target`."""
    session = sa.inspect(target).session
    return session is not None and session.is_modified(target, include_collections=False)


def stored_row(connection, target, columns):
    """`columns` of `target`'s row as the database holds it before this flush
    writes it, read on the flush's own connection -- so the guard compares
    against stored truth even when the object's loaded state has expired."""
    table = type(target).__table__
    return (
        connection.execute(
            sa.select(*(table.c[name] for name in columns)).where(table.c.id == target.id)
        )
        .mappings()
        .first()
    )


def pending_value(target, key):
    """``(True, value)`` when this flush sets `key`, else ``(False, None)``."""
    added = sa.inspect(target).attrs[key].history.added
    return (True, added[0]) if added else (False, None)


_DELETION_WRITES = frozenset(DELETION_COLUMNS) | {"version", "updated_at"}


def refuse_or_allow_deletion(target, stored, noun):
    """Phase 5 / M10, shared by the invoice, payment and receipt guards.

    ``True`` when this flush is the one permitted deletion of `target` --
    all three tombstone columns set, the version moved by exactly one, and
    nothing else changed; ``False`` when it touches no tombstone column (the
    caller's own rules then apply). Raises :class:`FinancialHistoryError` for
    any change to a row that is already deleted, and for a partial deletion or
    one that changes anything else. `stored` must hold ``deleted_at`` and
    ``version``.
    """
    if stored["deleted_at"] is not None:
        raise FinancialHistoryError(f"A deleted {noun} never changes")
    changed = {attr.key for attr in sa.inspect(target).attrs if attr.history.added}
    if not changed & set(DELETION_COLUMNS):
        return False
    if not set(DELETION_COLUMNS) <= changed or not changed <= _DELETION_WRITES:
        raise FinancialHistoryError(
            f"A {noun} is deleted by setting its deletion columns and version only"
        )
    if any(getattr(target, key) is None for key in DELETION_COLUMNS):
        raise FinancialHistoryError(f"A {noun}'s deletion is recorded in full")
    setting, version = pending_value(target, "version")
    if not setting or version != stored["version"] + 1:
        raise FinancialHistoryError(f"Deleting a {noun} moves its version by exactly one")
    return True


_SET_ONCE_COLUMNS = ("student_fee_assignment_id", "invoice_number", "issued_at", "issued_by_id")


@event.listens_for(Invoice, "before_insert")
def _resolve_legacy_invoice_account(_mapper, connection, target):
    if target.student_id is None and target.student_fee_assignment_id:
        row = connection.execute(sa.text("SELECT e.student_id, e.id FROM student_fee_assignments a JOIN enrollments e ON e.id = a.enrollment_id WHERE a.id = :id"), {"id": target.student_fee_assignment_id}).first()
        if row:
            target.student_id, target.enrollment_id = row


@event.listens_for(Invoice, "before_update")
def _refuse_rewriting_invoice_history(_mapper, connection, target):
    if not is_changing(target):
        return
    from app.services.financial_history import revision_authorizes_update
    if revision_authorizes_update(connection, target):
        return
    stored = stored_row(
        connection, target, ("status", "version", "deleted_at") + _SET_ONCE_COLUMNS
    )
    if stored is None:
        return
    if stored["status"] == InvoiceStatus.CANCELLED.value:
        raise FinancialHistoryError("A cancelled invoice is read-only")
    if refuse_or_allow_deletion(target, stored, "invoice"):
        return
    for key in _SET_ONCE_COLUMNS:
        setting, value = pending_value(target, key)
        if setting and stored[key] is not None and value != stored[key]:
            raise FinancialHistoryError(f"An invoice's {key} never changes once set")


@event.listens_for(Invoice, "before_delete")
def _refuse_deleting_an_invoice(_mapper, _connection, _target):
    raise FinancialHistoryError("An invoice is never deleted")
