from app.models.code_types import CODE_COLLATION
import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import FeePlanItemKind, FeePlanItemStatus
from app.models.fee_plan import normalize_single_line_text
from app.models.submission_feedback import whole_second_utc
from app.services.money import (
    AMOUNT_PRECISION,
    AMOUNT_SCALE,
    MAX_AMOUNT,
    MIN_AMOUNT,
    validate_amount,
)

#: The ``label`` column's own width.
FEE_PLAN_ITEM_LABEL_MAX_LENGTH = 150

#: The most **active** items one plan may carry. Removed items are history
#: and do not count.
MAX_ACTIVE_FEE_PLAN_ITEMS = 20


def normalize_fee_plan_item_label(raw):
    """``(label, error_code)`` for one item label. Required, single line."""
    return normalize_single_line_text(raw, FEE_PLAN_ITEM_LABEL_MAX_LENGTH)


def fee_plan_item_label_key(label):
    """What "the same label" means inside one plan: the normalized label,
    case-folded.

    There is no database constraint behind this rule -- MySQL has no partial
    unique index for "active rows only" -- so the comparison is the
    application's alone, made in Python against locked rows. Case-folding
    keeps ``Course fee`` and ``course fee`` from appearing as two lines of
    one plan.
    """
    return label.casefold()


_KIND_VALUES = tuple(kind.value for kind in FeePlanItemKind)
_STATUS_VALUES = tuple(status.value for status in FeePlanItemStatus)
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


class FeePlanItem(db.Model):
    """One line of one :class:`~app.models.fee_plan.FeePlan`
    (Phase 5 / M02).

    **Ownership is the plan, and only the plan.** ``fee_plan_id`` is a plain
    NOT NULL foreign key; every nested route resolves an item *through* the
    plan in its URL, so an item public id under another plan's URL is a
    404.

    **An amount is exact.** ``DECIMAL(19, 4)``, from
    :data:`~app.services.money.MIN_AMOUNT` through
    :data:`~app.services.money.MAX_AMOUNT` LYD inclusive, enforced by
    ``ck_fee_plan_items_amount_range`` and by :func:`validate_amount`, which
    refuses a ``float`` outright. There is no quantity, discount, tax or due
    date: an item is a kind, a label and an amount.

    **Edits happen only while the plan is a draft.** Once the plan has ever
    been activated its items are frozen with it, forever.

    **Removal is history, not deletion.** Removing an item from a draft sets
    ``status='removed'`` with ``removed_at`` / ``removed_by_id``; the row is
    never deleted, restored or edited again. ``ck_fee_plan_items_removal_state``
    ties the removal attribution to the status exactly.

    **Labels are distinct among a plan's active items**, compared by
    :func:`fee_plan_item_label_key`. That rule, and the
    :data:`MAX_ACTIVE_FEE_PLAN_ITEMS` limit, are application invariants
    proved under the plan's lock; no CHECK can read sibling rows.

    **``version``** starts at 1 and increases by one per meaningful change
    (an edit, the removal); every such change also increments the owning
    plan's version, which is the aggregate's concurrency signal.

    Indexes:

    - ``ix_fee_plan_items_plan_status_id`` (``fee_plan_id``, ``status``,
      ``id``) -- a plan's active items in id order, the page totals' ``IN``
      lookup and the removed-history read; it also serves the
      ``fee_plan_id`` foreign key;
    - ``ix_fee_plan_items_removed_by_id`` -- the ``removed_by_id`` foreign
      key.

    **No MySQL execution plan has been measured for this table.** No ORM
    relationship is declared, and no foreign key carries ``ondelete`` or
    ``onupdate``.
    """

    __tablename__ = "fee_plan_items"
    __table_args__ = (
        db.CheckConstraint(_KIND_CHECK_SQL, name="ck_fee_plan_items_kind_valid"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_fee_plan_items_status_valid"),
        db.CheckConstraint(_AMOUNT_RANGE_SQL, name="ck_fee_plan_items_amount_range"),
        db.CheckConstraint("version > 0", name="ck_fee_plan_items_version_positive"),
        db.CheckConstraint(_REMOVAL_STATE_SQL, name="ck_fee_plan_items_removal_state"),
        db.CheckConstraint(
            _TIMESTAMPS_ORDERED_SQL, name="ck_fee_plan_items_timestamps_ordered"
        ),
        db.Index("ix_fee_plan_items_plan_status_id", "fee_plan_id", "status", "id"),
        db.Index("ix_fee_plan_items_removed_by_id", "removed_by_id"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    fee_plan_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("fee_plans.id"),
        nullable=False,
    )
    kind = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    label = db.Column(db.String(FEE_PLAN_ITEM_LABEL_MAX_LENGTH), nullable=False)
    #: Exact fixed point, never a float -- see :mod:`app.services.money`.
    amount = db.Column(
        db.DECIMAL(precision=AMOUNT_PRECISION, scale=AMOUNT_SCALE, asdecimal=True),
        nullable=False,
    )
    status = db.Column(
        db.String(32, collation=CODE_COLLATION), nullable=False, default=FeePlanItemStatus.ACTIVE.value
    )
    #: NULL exactly while ``active``; set once, at removal.
    removed_at = db.Column(db.DateTime, nullable=True)
    removed_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("kind")
    def validate_kind(self, _key, value):
        if value not in _KIND_VALUES:
            raise ValueError(f"Invalid fee plan item kind: {value}")
        return value

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid fee plan item status: {value}")
        return value

    @validates("label")
    def validate_label(self, _key, value):
        normalized, error = normalize_fee_plan_item_label(value)
        if error is not None or normalized != value:
            raise ValueError("Fee plan item label must be non-empty normalized plain text")
        return value

    @validates("amount")
    def validate_amount(self, _key, value):
        return validate_amount(value)

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("FeePlanItem version must be a positive integer")
        return value

    @property
    def is_active(self):
        return self.status == FeePlanItemStatus.ACTIVE.value
