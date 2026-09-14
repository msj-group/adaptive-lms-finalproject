import unicodedata
import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import FeePlanStatus
from app.models.submission_feedback import whole_second_utc
from app.services.money import CURRENCY_CODE

#: The ``name`` column's own width, declared once so the column, the
#: normaliser and the form wording cannot disagree.
FEE_PLAN_NAME_MAX_LENGTH = 150

#: The ``description`` column's own width. A description explains what a
#: plan is for; it is not a contract document.
FEE_PLAN_DESCRIPTION_MAX_LENGTH = 1000

#: Normalisation error codes, returned instead of a sentence. The form owns
#: the wording. Shared with :mod:`app.models.fee_plan_item`.
TEXT_MISSING = "missing"
TEXT_CONTROL = "control"
TEXT_TOO_LONG = "too_long"

#: Bidirectional embedding, override and isolate controls. Invisible, and
#: able to make a plan name or an item label *display* as something other
#: than what is stored -- which is exactly what financial text must never
#: do. Ordinary Arabic text needs none of them.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
_LINE_SEPARATORS = frozenset("  ")


def _forbidden(ch, allowed=frozenset()):
    """``True`` for a character plain fee-plan text must not contain.

    Every Unicode ``Cc`` control (C0, DEL and C1), every bidi control and
    the two Unicode line separators -- except those in `allowed`.
    """
    if ch in allowed:
        return False
    return (
        unicodedata.category(ch) == "Cc"
        or ch in _BIDI_CONTROLS
        or ch in _LINE_SEPARATORS
    )


def normalize_single_line_text(raw, max_length, required=True):
    """``(text_or_None, error_code)`` for one single-line plain-text value.

    Rejects any forbidden character outright -- never drops it, which would
    store something nobody typed or saw -- then collapses every run of
    whitespace to one space and strips the ends, so a pasted value is stored
    the way it reads. Length is measured *after* that collapse. An absent
    optional value is ``None``, never ``""``.
    """
    text = raw if isinstance(raw, str) else ""
    if any(_forbidden(ch) for ch in text):
        return None, TEXT_CONTROL
    text = " ".join(text.split())
    if not text:
        return (None, TEXT_MISSING) if required else (None, None)
    if len(text) > max_length:
        return None, TEXT_TOO_LONG
    return text, None


def normalize_fee_plan_name(raw):
    """``(name, error_code)`` for one fee plan name. Required."""
    return normalize_single_line_text(raw, FEE_PLAN_NAME_MAX_LENGTH)


def normalize_fee_plan_description(raw):
    """``(description_or_None, error_code)`` for one optional description.

    Line endings become ``\\n``; interior newlines and tabs are kept as
    typed; every other forbidden character is rejected; the outer whitespace
    is stripped. Blank means absent, which is ``None``.
    """
    text = (raw if isinstance(raw, str) else "").replace("\r\n", "\n").replace("\r", "\n")
    if any(_forbidden(ch, allowed=frozenset("\n\t")) for ch in text):
        return None, TEXT_CONTROL
    text = text.strip()
    if not text:
        return None, None
    if len(text) > FEE_PLAN_DESCRIPTION_MAX_LENGTH:
        return None, TEXT_TOO_LONG
    return text, None


_STATUS_VALUES = tuple(status.value for status in FeePlanStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"
_CURRENCY_CHECK_SQL = f"currency_code = '{CURRENCY_CODE}'"

#: Who first activated a plan and when are recorded together or not at all.
_FIRST_ACTIVATION_PAIR_SQL = (
    "(first_activated_at IS NULL AND first_activated_by_id IS NULL)"
    " OR (first_activated_at IS NOT NULL AND first_activated_by_id IS NOT NULL)"
)

#: Likewise the most recent lifecycle transition.
_STATUS_CHANGE_PAIR_SQL = (
    "(status_changed_at IS NULL AND status_changed_by_id IS NULL)"
    " OR (status_changed_at IS NOT NULL AND status_changed_by_id IS NOT NULL)"
)

#: The lifecycle truth table, proved by the database rather than promised
#: by the application. A draft has never been activated and never changed
#: state; an active plan has been activated, and its latest transition is
#: no earlier than that; an archived plan has a transition, and was either
#: never activated or archived no earlier than its first activation.
_LIFECYCLE_STATE_SQL = (
    "(status = 'draft' AND first_activated_at IS NULL AND status_changed_at IS NULL)"
    " OR (status = 'active' AND first_activated_at IS NOT NULL"
    " AND status_changed_at IS NOT NULL AND status_changed_at >= first_activated_at)"
    " OR (status = 'archived' AND status_changed_at IS NOT NULL"
    " AND (first_activated_at IS NULL OR status_changed_at >= first_activated_at))"
)

_TIMESTAMPS_ORDERED_SQL = (
    "updated_at >= created_at"
    " AND (first_activated_at IS NULL OR first_activated_at >= created_at)"
    " AND (status_changed_at IS NULL"
    " OR (status_changed_at >= created_at AND updated_at >= status_changed_at))"
)


class FeePlan(db.Model):
    """One reusable, Administrator-managed fee plan (Phase 5 / M02).

    **A catalogue entry, and nothing more.** A plan names a fee structure
    -- its items and their amounts in ``LYD`` -- so that a later Part can
    use it. M02 stores no student assignment, invoice, payment, receipt,
    refund, discount, installment, scholarship, exemption, tax, quantity
    or due date, and there is no column or placeholder for any of them.

    **The lifecycle freezes the financial definition.** See
    :class:`~app.models.enums.FeePlanStatus`. Only a ``draft`` may be
    edited, and only a draft's items may be added, edited or removed. The
    first activation sets ``first_activated_at`` / ``first_activated_by_id``
    once, and from then on the plan and its items are frozen **forever**:
    archiving changes availability only, and reactivating restores
    ``active`` without permitting any edit. A different fee structure is a
    new plan. A draft may also be archived, and an archived plan is
    read-only whatever its history.

    **Nothing is ever physically deleted.** There is no delete route, no
    ``cascade`` and no ``ondelete``; every foreign key is a plain reference.

    **``name`` is unique across every status**, so an archived plan keeps
    its name. ``uq_fee_plans_name`` is the final defense; the effective
    case- and accent-sensitivity is the column's collation (the MySQL
    server's default, binary on the SQLite test backend), the same
    arrangement every existing unique title in this project uses.

    **``currency_code`` is always ``LYD``**, stored rather than implied
    and checked by ``ck_fee_plans_currency_code``.

    **Attribution.** ``created_by_id`` is set once. ``first_activated_*``
    records the freeze. ``status_changed_*`` records the most recent
    lifecycle transition (activate, archive or reactivate). None of them
    is an authorization fact: a foreign key into ``users`` proves the row
    exists, never that it is still an active Administrator, so every write
    re-reads the *acting* account under its lock.

    **``version`` is the aggregate's optimistic-concurrency signal.** It
    starts at 1 and increases by exactly one per meaningful change to the
    plan **or any of its items**, so a signed form token detects that the
    aggregate moved under it -- including a change to an item the form did
    not show. A no-op edit moves neither ``version`` nor ``updated_at``.

    Database invariants (final defense only):

    - ``public_id`` and ``uq_fee_plans_name`` unique;
    - ``ck_fee_plans_status_valid`` -- the closed set as a literal ``IN``
      list, the project's convention for closed sets;
    - ``ck_fee_plans_currency_code`` -- ``LYD`` only;
    - ``ck_fee_plans_version_positive``;
    - ``ck_fee_plans_first_activation_pair`` and
      ``ck_fee_plans_status_change_pair`` -- attribution moments and actors
      are set together;
    - ``ck_fee_plans_lifecycle_state`` -- see :data:`_LIFECYCLE_STATE_SQL`;
    - ``ck_fee_plans_timestamps_ordered``.

    Conditions a CHECK cannot express are stated here rather than hidden:
    that an attributed account is an active Administrator, that a frozen
    plan is never written except by a lifecycle transition, and that an
    active plan has between one and
    :data:`~app.models.fee_plan_item.MAX_ACTIVE_FEE_PLAN_ITEMS` active items
    with distinct labels. The application proves each against locked rows.

    Indexes -- one per real query path:

    - ``ix_fee_plans_status_id`` (``status``, ``id``) -- the filtered list,
      an equality on ``status`` ordered by ``id DESC``; the unfiltered list
      walks the primary key;
    - ``ix_fee_plans_created_by_id``, ``ix_fee_plans_first_activated_by_id``
      and ``ix_fee_plans_status_changed_by_id`` -- declared for the three
      foreign keys InnoDB requires an index for, rather than left implicit.

    **No MySQL execution plan has been measured for this table.**

    **No ORM relationship is declared in either direction**: every read is
    an explicit query in ``app/services/fee_plan_queries.py``, so rendering
    a plan can never trigger a lazy load, and no ``delete-orphan``
    configuration exists that could remove an item.
    """

    __tablename__ = "fee_plans"
    __table_args__ = (
        db.UniqueConstraint("name", name="uq_fee_plans_name"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_fee_plans_status_valid"),
        db.CheckConstraint(_CURRENCY_CHECK_SQL, name="ck_fee_plans_currency_code"),
        db.CheckConstraint("version > 0", name="ck_fee_plans_version_positive"),
        db.CheckConstraint(
            _FIRST_ACTIVATION_PAIR_SQL, name="ck_fee_plans_first_activation_pair"
        ),
        db.CheckConstraint(_STATUS_CHANGE_PAIR_SQL, name="ck_fee_plans_status_change_pair"),
        db.CheckConstraint(_LIFECYCLE_STATE_SQL, name="ck_fee_plans_lifecycle_state"),
        db.CheckConstraint(_TIMESTAMPS_ORDERED_SQL, name="ck_fee_plans_timestamps_ordered"),
        db.Index("ix_fee_plans_status_id", "status", "id"),
        db.Index("ix_fee_plans_created_by_id", "created_by_id"),
        db.Index("ix_fee_plans_first_activated_by_id", "first_activated_by_id"),
        db.Index("ix_fee_plans_status_changed_by_id", "status_changed_by_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    name = db.Column(db.String(FEE_PLAN_NAME_MAX_LENGTH), nullable=False)
    #: Optional plain text. NULL means "nothing written"; never ``""``.
    description = db.Column(db.String(FEE_PLAN_DESCRIPTION_MAX_LENGTH), nullable=True)
    currency_code = db.Column(db.String(3), nullable=False, default=CURRENCY_CODE)
    status = db.Column(db.String(32), nullable=False, default=FeePlanStatus.DRAFT.value)
    created_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    #: Set once, at the first activation, and never changed again. Its
    #: presence is what freezes the plan.
    first_activated_at = db.Column(db.DateTime, nullable=True)
    first_activated_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    #: The most recent lifecycle transition. NULL exactly while a draft.
    status_changed_at = db.Column(db.DateTime, nullable=True)
    status_changed_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defaults are defense in depth only; every write supplies its own
    #: post-lock whole-second moment. Deliberately no ``onupdate`` hook: a
    #: no-op edit must leave both timestamps alone.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("name")
    def validate_name(self, _key, value):
        normalized, error = normalize_fee_plan_name(value)
        if error is not None or normalized != value:
            raise ValueError("Fee plan name must be non-empty normalized plain text")
        return value

    @validates("description")
    def validate_description(self, _key, value):
        if value is None:
            return value
        normalized, error = normalize_fee_plan_description(value)
        if error is not None or normalized != value:
            raise ValueError("Fee plan description must be normalized plain text or None")
        return value

    @validates("currency_code")
    def validate_currency_code(self, _key, value):
        if value != CURRENCY_CODE:
            raise ValueError(f"Fee plan currency must be {CURRENCY_CODE}")
        return value

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid fee plan status: {value}")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("FeePlan version must be a positive integer")
        return value

    @property
    def is_draft(self):
        return self.status == FeePlanStatus.DRAFT.value

    @property
    def is_active(self):
        return self.status == FeePlanStatus.ACTIVE.value

    @property
    def is_archived(self):
        return self.status == FeePlanStatus.ARCHIVED.value

    @property
    def has_been_activated(self):
        """Whether the plan's financial definition is frozen forever."""
        return self.first_activated_at is not None

    @property
    def can_be_reactivated(self):
        """Only an archived plan that was active before may be reactivated."""
        return self.is_archived and self.has_been_activated
