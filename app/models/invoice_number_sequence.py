from sqlalchemy import event
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.invoice import (
    MAX_INVOICE_SEQUENCE_NUMBER,
    MAX_INVOICE_YEAR,
    MIN_INVOICE_YEAR,
    FinancialHistoryError,
    is_changing,
    pending_value,
    stored_row,
)
from app.models.submission_feedback import whole_second_utc


class InvoiceNumberSequence(db.Model):
    """The internal, lockable annual counter behind invoice numbers
    (Phase 5 / M04).

    **Technical, and never shown.** One row per center-local calendar year;
    no public id, route, form, template, token, report or API ever carries
    one. It exists only so a first issue can allocate ``INV-YYYY-NNNNNN``
    under a row lock without two Administrators receiving the same number.

    ``last_number`` is the most recently allocated number of the year (0
    before the first). Each first issue increments it exactly once, under
    ``SELECT ... FOR UPDATE``, in the same transaction that writes the
    invoice. A rolled-back issue therefore consumes nothing. At
    :data:`~app.models.invoice.MAX_INVOICE_SEQUENCE_NUMBER` the year is full
    and issuing is refused.

    Database invariants: ``uq_invoice_number_sequences_calendar_year``,
    ``ck_invoice_number_sequences_year_range``,
    ``ck_invoice_number_sequences_last_number_range`` and
    ``ck_invoice_number_sequences_timestamps_ordered``. The invoice number's
    own unique constraint is the final defense against a reused number.

    The ORM guards refuse a delete, a year change and a decrease.
    """

    __tablename__ = "invoice_number_sequences"
    __table_args__ = (
        db.UniqueConstraint("calendar_year", name="uq_invoice_number_sequences_calendar_year"),
        db.CheckConstraint(
            f"calendar_year >= {MIN_INVOICE_YEAR} AND calendar_year <= {MAX_INVOICE_YEAR}",
            name="ck_invoice_number_sequences_year_range",
        ),
        db.CheckConstraint(
            f"last_number >= 0 AND last_number <= {MAX_INVOICE_SEQUENCE_NUMBER}",
            name="ck_invoice_number_sequences_last_number_range",
        ),
        db.CheckConstraint(
            "updated_at >= created_at", name="ck_invoice_number_sequences_timestamps_ordered"
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    calendar_year = db.Column(db.Integer, nullable=False)
    last_number = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("calendar_year")
    def validate_calendar_year(self, _key, value):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not MIN_INVOICE_YEAR <= value <= MAX_INVOICE_YEAR
        ):
            raise ValueError("An invoice number year must have four digits")
        return value

    @validates("last_number")
    def validate_last_number(self, _key, value):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= MAX_INVOICE_SEQUENCE_NUMBER
        ):
            raise ValueError("An invoice sequence number is out of range")
        return value


@event.listens_for(InvoiceNumberSequence, "before_update")
def _refuse_rewinding_a_sequence(_mapper, connection, target):
    if not is_changing(target):
        return
    stored = stored_row(connection, target, ("calendar_year", "last_number"))
    if stored is None:
        return
    setting, year = pending_value(target, "calendar_year")
    if setting and year != stored["calendar_year"]:
        raise FinancialHistoryError("An invoice number sequence never changes its year")
    setting, number = pending_value(target, "last_number")
    if setting and number < stored["last_number"]:
        raise FinancialHistoryError("An invoice number sequence never goes backwards")


@event.listens_for(InvoiceNumberSequence, "before_delete")
def _refuse_deleting_a_sequence(_mapper, _connection, _target):
    raise FinancialHistoryError("An invoice number sequence is never deleted")
