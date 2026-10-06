import re

from sqlalchemy import event
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.invoice import (
    MAX_INVOICE_YEAR,
    MIN_INVOICE_YEAR,
    FinancialHistoryError,
    is_changing,
    pending_value,
    stored_row,
)
from app.models.submission_feedback import whole_second_utc

#: ``RCT-YYYY-NNNNNN``: fifteen ASCII characters.
RECEIPT_NUMBER_LENGTH = 15

#: The six-digit annual capacity. Issuing past it is refused, never wrapped.
MAX_RECEIPT_SEQUENCE_NUMBER = 999999

_NUMBER_SHAPE = re.compile(r"RCT-(?P<year>[0-9]{4})-(?P<sequence>[0-9]{6})")


def _bounded_int(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def format_receipt_number(calendar_year, sequence_number):
    """``RCT-YYYY-NNNNNN`` for one allocated number, zero-padded."""
    if not _bounded_int(calendar_year, MIN_INVOICE_YEAR, MAX_INVOICE_YEAR) or not _bounded_int(
        sequence_number, 1, MAX_RECEIPT_SEQUENCE_NUMBER
    ):
        raise ValueError(
            "A receipt number needs a four-digit year and a sequence number from 1 to "
            f"{MAX_RECEIPT_SEQUENCE_NUMBER}"
        )
    return f"RCT-{calendar_year:04d}-{sequence_number:06d}"


def receipt_number_is_valid(value):
    """Whether `value` is exactly a system receipt number: ASCII ``RCT-``, a
    four-digit year from 1000, ``-`` and a six-digit sequence number from 1."""
    if not isinstance(value, str):
        return False
    match = _NUMBER_SHAPE.fullmatch(value)
    return (
        match is not None
        and int(match.group("year")) >= MIN_INVOICE_YEAR
        and int(match.group("sequence")) >= 1
    )


class ReceiptNumberSequence(db.Model):
    """The internal, lockable annual counter behind receipt numbers
    (Phase 5 / M05).

    **Technical, and never shown**, exactly like
    :class:`~app.models.invoice_number_sequence.InvoiceNumberSequence`: one row
    per center-local calendar year; no public id, route, form, template,
    token, report or API ever carries one.

    ``last_number`` is the most recently allocated number of the year (0
    before the first). Each confirmed collection increments it exactly once,
    under ``SELECT ... FOR UPDATE``, in the same transaction that confirms the
    payment and writes its receipt, so a rolled-back confirmation consumes
    nothing. At :data:`MAX_RECEIPT_SEQUENCE_NUMBER` the year is full and
    confirmation is refused.

    Database invariants: ``uq_receipt_number_sequences_calendar_year``,
    ``ck_receipt_number_sequences_year_range``,
    ``ck_receipt_number_sequences_last_number_range`` and
    ``ck_receipt_number_sequences_timestamps_ordered``. The receipt number's
    own unique constraint is the final defense against a reused number.

    The ORM guards refuse a delete, a year change and a decrease.
    """

    __tablename__ = "receipt_number_sequences"
    __table_args__ = (
        db.UniqueConstraint("calendar_year", name="uq_receipt_number_sequences_calendar_year"),
        db.CheckConstraint(
            f"calendar_year >= {MIN_INVOICE_YEAR} AND calendar_year <= {MAX_INVOICE_YEAR}",
            name="ck_receipt_number_sequences_year_range",
        ),
        db.CheckConstraint(
            f"last_number >= 0 AND last_number <= {MAX_RECEIPT_SEQUENCE_NUMBER}",
            name="ck_receipt_number_sequences_last_number_range",
        ),
        db.CheckConstraint(
            "updated_at >= created_at", name="ck_receipt_number_sequences_timestamps_ordered"
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    calendar_year = db.Column(db.Integer, nullable=False)
    last_number = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("calendar_year")
    def validate_calendar_year(self, _key, value):
        if not _bounded_int(value, MIN_INVOICE_YEAR, MAX_INVOICE_YEAR):
            raise ValueError("A receipt number year must have four digits")
        return value

    @validates("last_number")
    def validate_last_number(self, _key, value):
        if not _bounded_int(value, 0, MAX_RECEIPT_SEQUENCE_NUMBER):
            raise ValueError("A receipt sequence number is out of range")
        return value


@event.listens_for(ReceiptNumberSequence, "before_update")
def _refuse_rewinding_a_receipt_sequence(_mapper, connection, target):
    if not is_changing(target):
        return
    stored = stored_row(connection, target, ("calendar_year", "last_number"))
    if stored is None:
        return
    setting, year = pending_value(target, "calendar_year")
    if setting and year != stored["calendar_year"]:
        raise FinancialHistoryError("A receipt number sequence never changes its year")
    setting, number = pending_value(target, "last_number")
    if setting and number < stored["last_number"]:
        raise FinancialHistoryError("A receipt number sequence never goes backwards")


@event.listens_for(ReceiptNumberSequence, "before_delete")
def _refuse_deleting_a_receipt_sequence(_mapper, _connection, _target):
    raise FinancialHistoryError("A receipt number sequence is never deleted")
