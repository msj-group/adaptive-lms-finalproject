"""Read queries and presentation for Administrator receipts (Phase 5 / M05).

Flask-independent. **Every read here is reachable only by an active
Administrator**, and a receipt is found only through the invoice in its URL:
a receipt whose collection belongs to another invoice is ``None``, which the
route turns into a 404.

**A receipt renders from its own document.** The number, invoice number,
names, method, amount and confirmation moment all come from the receipt's
server-built ``snapshot``, never from the current invoice, Student, Group or
accounts. Only what can change about a receipt -- its void status, attribution
and reason -- and the immutable transfer details of its collection are read
from their rows.
"""

from decimal import Decimal

from app.extensions import db
from app.models import PaymentTransaction, Receipt, ReceiptStatus
from app.models.receipt import parse_receipt_moment
from app.services.money import format_amount
from app.services.payment_queries import METHOD_LABELS, RECEIPT_STATUS_LABELS
from app.services.schedule_occurrences import to_app_local

_PUBLIC_ID_MAX_LENGTH = 36
_VOIDED = ReceiptStatus.VOIDED.value


def invoice_receipt(invoice_id, receipt_public_id):
    """``(receipt, payment)`` for one receipt by ``public_id`` **inside**
    `invoice_id`, or ``None``. One query."""
    if not receipt_public_id or len(receipt_public_id) > _PUBLIC_ID_MAX_LENGTH:
        return None
    return (
        db.session.query(Receipt, PaymentTransaction)
        .join(PaymentTransaction, PaymentTransaction.id == Receipt.payment_transaction_id)
        .filter(
            PaymentTransaction.invoice_id == invoice_id,
            Receipt.public_id == receipt_public_id,
        )
        .first()
    )


def build_receipt_view(receipt, payment, names, tz_name="UTC"):
    """One receipt as its page shows it. No internal id survives."""
    document = receipt.snapshot
    voided = receipt.status == _VOIDED
    return {
        "public_id": receipt.public_id,
        "receipt_number": document["receipt_number"],
        "status_label": RECEIPT_STATUS_LABELS.get(receipt.status, receipt.status),
        "is_voided": voided,
        "invoice_number": document["invoice_number"],
        "method": document["method"],
        "method_label": METHOD_LABELS.get(document["method"], document["method"]),
        "amount_text": format_amount(Decimal(document["amount"])),
        "currency_code": document["currency_code"],
        "confirmed_local": to_app_local(tz_name, parse_receipt_moment(document["confirmed_at"])),
        "confirmed_by_name": document["confirmed_by_name"],
        "student_name": document["student_name"],
        "group_name": document["group_name"],
        "course_title": document["course_title"],
        "academic_term_name": document["academic_term_name"],
        "bank_transfer_reference": payment.bank_transfer_reference,
        "bank_transfer_date": payment.bank_transfer_date,
        "voided_local": to_app_local(tz_name, receipt.voided_at) if voided else None,
        "voided_by_name": names.get(receipt.voided_by_id) if voided else None,
        "void_reason": receipt.void_reason if voided else None,
    }
