"""Allocate invoice and receipt numbers under the caller's financial transaction.

Sequence rows are locked by primary key. A first-year insert race is handled
by the caller's rollback; neither allocator commits or reserves independently.
"""
from app.extensions import db
from app.models import (InvoiceNumberSequence, ReceiptNumberSequence,
    MAX_INVOICE_SEQUENCE_NUMBER, MAX_RECEIPT_SEQUENCE_NUMBER)
from app.models.invoice import format_invoice_number
from app.models.receipt_number_sequence import format_receipt_number

def _lock_by_id(model, row_id):
    if row_id is None:
        return None
    return model.query.filter_by(id=row_id).with_for_update().first()


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
    sequence_id = db.session.query(InvoiceNumberSequence.id).filter(InvoiceNumberSequence.calendar_year == calendar_year).scalar()
    if sequence_id is None:
        row = InvoiceNumberSequence(calendar_year=calendar_year, last_number=0, created_at=moment, updated_at=moment)
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


def lock_receipt_number_sequence(calendar_year, moment):
    """The locked sequence row of `calendar_year`, created (at 0) if the year
    has none -- the last lock of a confirmation.

    Found by a plain read and locked by primary key, never by a locking read
    that matches nothing (InnoDB gap locks would let two first receipts of a
    year deadlock). Two first confirmations that both insert the row meet the
    unique ``calendar_year``; the flush raises ``IntegrityError``, which the
    caller rolls back and reports generically. Nothing is consumed either way.
    """
    sequence_id = db.session.query(ReceiptNumberSequence.id).filter(ReceiptNumberSequence.calendar_year == calendar_year).scalar()
    if sequence_id is None:
        row = ReceiptNumberSequence(calendar_year=calendar_year, last_number=0, created_at=moment, updated_at=moment)
        db.session.add(row)
        db.session.flush()
        sequence_id = row.id
    return _lock_by_id(ReceiptNumberSequence, sequence_id)


def allocate_receipt_number(sequence, moment):
    """Increment the **locked** `sequence` exactly once and return the new
    ``RCT-YYYY-NNNNNN``, or ``None`` -- changing nothing -- when the year's
    six-digit capacity is exhausted."""
    if sequence is None or sequence.last_number >= MAX_RECEIPT_SEQUENCE_NUMBER:
        return None
    sequence.last_number = sequence.last_number + 1
    sequence.updated_at = moment
    return format_receipt_number(sequence.calendar_year, sequence.last_number)
