import uuid

from sqlalchemy import event
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import InvoiceItemKind, InvoiceItemStatus
from app.models.fee_plan import normalize_single_line_text
from app.models.fee_plan_item import FEE_PLAN_ITEM_LABEL_MAX_LENGTH, MAX_ACTIVE_FEE_PLAN_ITEMS
from app.models.invoice import FinancialHistoryError, is_changing, pending_value, stored_row
from app.models.submission_feedback import whole_second_utc
from app.services.money import (
    AMOUNT_PRECISION,
    AMOUNT_SCALE,
    MAX_AMOUNT,
    MIN_AMOUNT,
    validate_amount,
)

#: The fee plan item's own width, so a copied label always fits.
INVOICE_ITEM_LABEL_MAX_LENGTH = FEE_PLAN_ITEM_LABEL_MAX_LENGTH

#: The most **active** lines one invoice may carry: the same bound as the
#: fee plan it was copied from.
MAX_ACTIVE_INVOICE_ITEMS = MAX_ACTIVE_FEE_PLAN_ITEMS

#: The most item rows one invoice may hold, removed history included. It
#: bounds the invoice page and every audit snapshot, which records the
#: removed lines too.
MAX_INVOICE_ITEM_ROWS = 100


def normalize_invoice_item_label(raw):
    """``(label, error_code)`` for one invoice line label. Required, single
    line, normalized exactly like a fee plan item label."""
    return normalize_single_line_text(raw, INVOICE_ITEM_LABEL_MAX_LENGTH)


_KIND_VALUES = tuple(kind.value for kind in InvoiceItemKind)
_STATUS_VALUES = tuple(status.value for status in InvoiceItemStatus)
_KIND_CHECK_SQL = "kind IN (" + ", ".join(f"'{v}'" for v in _KIND_VALUES) + ")"
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"
_AMOUNT_RANGE_SQL = f"amount >= {MIN_AMOUNT} AND amount <= {MAX_AMOUNT}"
_REMOVAL_STATE_SQL = (
    "(status = 'active' AND removed_at IS NULL AND removed_by_id IS NULL)"
    " OR (status = 'removed' AND removed_at IS NOT NULL AND removed_by_id IS NOT NULL)"
)
_TIMESTAMPS_ORDERED_SQL = (
    "updated_at >= created_at"
    " AND (removed_at IS NULL OR (removed_at >= created_at AND updated_at >= removed_at))"
)


class InvoiceItem(db.Model):
    """One financial snapshot line of one
    :class:`~app.models.invoice.Invoice` (Phase 5 / M04).

    **A snapshot, not a reference.** A draft copies each active fee plan
    item's kind, label and amount into a new row; there is deliberately **no**
    foreign key to ``fee_plan_items``, so nothing that happens to the plan can
    move an invoice, and editing a line never touches the plan.

    **Ownership is the invoice, and only the invoice.** ``invoice_id`` never
    changes; every nested route resolves a line through the invoice in its
    URL.

    **An amount is exact**: ``DECIMAL(19, 4)`` within the M02 bounds,
    enforced by ``ck_invoice_items_amount_range`` and by
    :func:`~app.services.money.validate_amount`, which refuses a ``float``.

    **Removal is history, not deletion.** A removed line keeps its row with
    ``removed_at`` / ``removed_by_id`` and never changes again. While its
    invoice is a draft or issued, an active line may be edited in place,
    moving its ``version`` and the invoice's.

    **Rules no CHECK can read**: at least one active line after every change,
    at most :data:`MAX_ACTIVE_INVOICE_ITEMS` active lines with distinct
    labels, and at most :data:`MAX_INVOICE_ITEM_ROWS` rows. Each is proved by
    the application under the invoice's lock.

    Indexes: ``ix_invoice_items_invoice_status_id`` (``invoice_id``,
    ``status``, ``id``) -- an invoice's lines in id order, the page totals and
    the foreign key; ``ix_invoice_items_removed_by_id``.

    **No MySQL execution plan has been measured for this table.** No ORM
    relationship is declared, and no foreign key carries ``ondelete``. The
    ORM guards refuse a delete, a change to a removed line and a move to
    another invoice.
    """

    __tablename__ = "invoice_items"
    __table_args__ = (
        db.CheckConstraint(_KIND_CHECK_SQL, name="ck_invoice_items_kind_valid"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_invoice_items_status_valid"),
        db.CheckConstraint(_AMOUNT_RANGE_SQL, name="ck_invoice_items_amount_range"),
        db.CheckConstraint("version > 0", name="ck_invoice_items_version_positive"),
        db.CheckConstraint(_REMOVAL_STATE_SQL, name="ck_invoice_items_removal_state"),
        db.CheckConstraint(_TIMESTAMPS_ORDERED_SQL, name="ck_invoice_items_timestamps_ordered"),
        db.Index("ix_invoice_items_invoice_status_id", "invoice_id", "status", "id"),
        db.Index("ix_invoice_items_removed_by_id", "removed_by_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    invoice_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("invoices.id"),
        nullable=False,
    )
    kind = db.Column(db.String(32), nullable=False)
    label = db.Column(db.String(INVOICE_ITEM_LABEL_MAX_LENGTH), nullable=False)
    #: Exact fixed point, never a float -- see :mod:`app.services.money`.
    amount = db.Column(
        db.DECIMAL(precision=AMOUNT_PRECISION, scale=AMOUNT_SCALE, asdecimal=True),
        nullable=False,
    )
    status = db.Column(db.String(32), nullable=False, default=InvoiceItemStatus.ACTIVE.value)
    removed_at = db.Column(db.DateTime, nullable=True)
    removed_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("kind")
    def validate_kind(self, _key, value):
        if value not in _KIND_VALUES:
            raise ValueError(f"Invalid invoice item kind: {value}")
        return value

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid invoice item status: {value}")
        return value

    @validates("label")
    def validate_label(self, _key, value):
        normalized, error = normalize_invoice_item_label(value)
        if error is not None or normalized != value:
            raise ValueError("Invoice item label must be non-empty normalized plain text")
        return value

    @validates("amount")
    def validate_amount(self, _key, value):
        return validate_amount(value)

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("InvoiceItem version must be a positive integer")
        return value

    @property
    def is_active(self):
        return self.status == InvoiceItemStatus.ACTIVE.value


@event.listens_for(InvoiceItem, "before_update")
def _refuse_rewriting_item_history(_mapper, connection, target):
    if not is_changing(target):
        return
    stored = stored_row(connection, target, ("status", "invoice_id"))
    if stored is None:
        return
    if stored["status"] == InvoiceItemStatus.REMOVED.value:
        raise FinancialHistoryError("A removed invoice item never changes")
    setting, value = pending_value(target, "invoice_id")
    if setting and value != stored["invoice_id"]:
        raise FinancialHistoryError("An invoice item never moves to another invoice")


@event.listens_for(InvoiceItem, "before_delete")
def _refuse_deleting_an_item(_mapper, _connection, _target):
    raise FinancialHistoryError("An invoice item is never deleted")
