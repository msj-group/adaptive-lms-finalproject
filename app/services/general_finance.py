"""Student account operations: invoices, collections and real payouts.

Account totals are derived, never stored. Invoice references on money are context
only. Every write holds the Student row before documents and annual sequences.
"""
from decimal import Decimal
import uuid
from flask import current_app
from sqlalchemy import case, func
from app.extensions import db
from app.models import FinancialRevision, Invoice, PaymentTransaction, Receipt, User
from app.models.receipt import receipt_moment_text
from app.models.submission_feedback import whole_second_utc
from app.services.financial_history import document_snapshot, record_document_revision
from app.services.financial_numbering import allocate_invoice_number, lock_invoice_number_sequence
from app.services.financial_numbering import allocate_receipt_number, lock_receipt_number_sequence
from app.services.money import calculate_discount, validate_amount, validate_course_price
from app.services.schedule_occurrences import to_app_local
from app.models.payment_transaction import normalize_bank_transfer_reference, parse_bank_transfer_date


class FinanceConflict(ValueError):
    pass


def account_totals(student_id):
    obligations = db.session.query(func.coalesce(func.sum(Invoice.charge_amount - Invoice.discount_amount), 0)).filter(
        Invoice.student_id == student_id, Invoice.status == "issued", Invoice.deleted_at.is_(None)).scalar()
    effective = case((PaymentTransaction.kind == "reversal", -PaymentTransaction.amount), else_=PaymentTransaction.amount)
    incoming = db.session.query(func.coalesce(func.sum(effective), 0)).filter(
        PaymentTransaction.student_id == student_id, PaymentTransaction.status == "confirmed",
        PaymentTransaction.movement_direction == "in", PaymentTransaction.deleted_at.is_(None)).scalar()
    outgoing = db.session.query(func.coalesce(func.sum(effective), 0)).filter(
        PaymentTransaction.student_id == student_id, PaymentTransaction.status == "confirmed",
        PaymentTransaction.movement_direction == "out", PaymentTransaction.deleted_at.is_(None)).scalar()
    obligations, incoming, outgoing = (Decimal(value) for value in (obligations, incoming, outgoing))
    return {"obligations": obligations, "collections": incoming, "payouts": outgoing,
            "balance": obligations - incoming + outgoing}


def lock_financial_student(public_id):
    db.session.rollback()
    student = User.query.filter_by(public_id=public_id, role="student").populate_existing().with_for_update().first()
    if student is None:
        raise FinanceConflict("This student account is unavailable.")
    return student


def _actor(actor_id):
    from app.services.actor_authorization import require_current_actor
    return require_current_actor(actor_id, "administrator")


def _operation_key(value):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise FinanceConflict("Reload this form before submitting.")
    return value


def issue_enrollment_invoice(student, episode, course, actor_id, kind="none", value="0", reason=None):
    """No commit/reset: enrollment, invoice and initial money share one transaction."""
    if course.price is None:
        raise FinanceConflict("Set the course price before enrolling a student.")
    price = validate_course_price(course.price)
    discount, _net = calculate_discount(price, kind, value)
    moment = whole_second_utc()
    year = to_app_local(current_app.config["APP_TIMEZONE"], moment).year
    _actor(actor_id)
    number = allocate_invoice_number(lock_invoice_number_sequence(year, moment), moment)
    if number is None:
        raise FinanceConflict("The annual invoice number capacity has been reached.")
    invoice = Invoice(student_id=student.id, enrollment_id=episode.id, status="issued", invoice_number=number,
        charge_amount=price, discount_amount=discount, discount_kind=kind, discount_value=str(value),
        discount_actor_id=actor_id if kind != "none" else None, discount_reason=reason,
        course_snapshot={"public_id": course.public_id, "title": course.title, "version": course.version,
                         "price": format(price, "f"), "currency": "LYD"}, currency_code="LYD",
        issued_at=moment, issued_by_id=actor_id, created_at=moment, updated_at=moment, version=1)
    db.session.add(invoice)
    db.session.flush()
    record_document_revision(invoice, student.id, actor_id, "create", None, reason)
    return invoice


def _receipt_snapshot(receipt, payment, student, actor, invoice):
    return {"schema": "repair.student-receipt.v1", "receipt_public_id": receipt.public_id,
        "receipt_number": receipt.receipt_number, "payment_public_id": payment.public_id,
        "student_name": student.full_name, "invoice_public_id": invoice.public_id if invoice else None,
        "invoice_number": invoice.invoice_number if invoice else None, "method": payment.method,
        "amount": format(payment.amount, ".4f"), "currency_code": "LYD",
        "confirmed_at": receipt_moment_text(payment.confirmed_at), "confirmed_by_name": actor.full_name,
        "movement_direction": payment.movement_direction}


def issue_general_receipt(payment, student, actor_id):
    actor = _actor(actor_id)
    moment = whole_second_utc()
    year = to_app_local(current_app.config["APP_TIMEZONE"], moment).year
    number = allocate_receipt_number(lock_receipt_number_sequence(year, moment), moment)
    if number is None:
        raise FinanceConflict("The annual receipt number capacity has been reached.")
    receipt = Receipt(public_id=str(uuid.uuid4()), payment_transaction_id=payment.id, receipt_number=number,
        status="issued", issued_at=moment, issued_by_id=actor.id, version=1, created_at=moment, updated_at=moment)
    invoice = db.session.get(Invoice, payment.invoice_id) if payment.invoice_id else None
    receipt.snapshot = _receipt_snapshot(receipt, payment, student, actor, invoice)
    db.session.add(receipt)
    db.session.flush()
    record_document_revision(receipt, student.id, actor_id, "create", None)
    return receipt


def record_money(student, actor_id, amount, method, direction, operation_key, *, invoice=None,
                 confirmed=False, bank_reference=None, bank_date=None):
    """Call after Student lock; no lifetime row cap and no invoice allocation."""
    key = _operation_key(operation_key)
    amount = validate_amount(amount)
    if method not in {"cash", "bank_transfer"} or direction not in {"in", "out"}:
        raise FinanceConflict("Select a supported money movement and method.")
    moment = whole_second_utc()
    if method == "cash":
        confirmed, bank_reference, bank_date = True, None, None
    else:
        bank_reference, reference_error = normalize_bank_transfer_reference(bank_reference)
        latest = to_app_local(current_app.config["APP_TIMEZONE"], moment).date()
        bank_date, date_error = parse_bank_transfer_date(str(bank_date or ""), latest)
        if reference_error or date_error:
            raise FinanceConflict("Enter a valid bank reference and transfer date (no later than today).")
    confirmed = bool(confirmed or direction == "out")
    actor = _actor(actor_id)
    existing = PaymentTransaction.query.filter_by(operation_key=key).first()
    if existing is not None:
        original = FinancialRevision.query.filter_by(payment_id=existing.id, action="create", version=1).first()
        snapshot = original.after_snapshot if original else document_snapshot(existing)
        supplied = {"student_id": student.id, "amount": format(amount, ".4f"), "method": method,
            "movement_direction": direction, "invoice_id": invoice.id if invoice else None,
            "bank_transfer_reference": bank_reference, "bank_transfer_date": bank_date.isoformat() if bank_date else None,
            "status": "confirmed" if confirmed else "pending", "recorded_by_id": actor.id}
        if any(snapshot.get(field) != value for field, value in supplied.items()):
            raise FinanceConflict("This submission was already used for different money details.")
        return existing
    if invoice is not None and invoice.student_id != student.id:
        raise FinanceConflict("Invoice context belongs to another student.")
    if direction == "out":
        confirmed = True
        credit = -account_totals(student.id)["balance"]
        if credit <= 0 or amount > credit:
            raise FinanceConflict("The payout cannot exceed money currently owed to this student.")
    if method == "cash":
        confirmed = True
        bank_reference, bank_date = None, None
    elif not bank_reference or bank_date is None:
        raise FinanceConflict("A bank reference and transfer date are required.")
    payment = PaymentTransaction(student_id=student.id, invoice_id=invoice.id if invoice else None,
        movement_direction=direction, operation_key=key, kind="collection", method=method,
        amount=amount, currency_code="LYD", status="confirmed" if confirmed else "pending",
        bank_transfer_reference=bank_reference, bank_transfer_date=bank_date,
        recorded_at=moment, recorded_by_id=actor.id, confirmed_at=moment if confirmed else None,
        confirmed_by_id=actor.id if confirmed else None, created_at=moment, updated_at=moment, version=1)
    db.session.add(payment)
    db.session.flush()
    record_document_revision(payment, student.id, actor.id, "create", None)
    if confirmed:
        issue_general_receipt(payment, student, actor.id)
    return payment


def correct_invoice(invoice, student, actor_id, expected_version, charge, kind, value, reason=None, delete=False, reverse=False):
    _actor(actor_id)
    if invoice.student_id != student.id or invoice.version != expected_version or invoice.deleted_at is not None:
        raise FinanceConflict("This invoice changed or is unavailable. Reload before changing it.")
    before = document_snapshot(invoice)
    moment = whole_second_utc()
    if delete:
        invoice.deleted_at, invoice.deleted_by_id, invoice.deletion_reason = moment, actor_id, reason or "Invoice deleted"
    elif reverse:
        if invoice.status != "issued":
            raise FinanceConflict("This obligation is no longer effective.")
        invoice.status, invoice.cancelled_at, invoice.cancelled_by_id = "cancelled", moment, actor_id
    else:
        if invoice.status != "issued":
            raise FinanceConflict("Only a current issued invoice can be corrected.")
        invoice.charge_amount = validate_course_price(charge)
        invoice.discount_amount, _net = calculate_discount(invoice.charge_amount, kind, value)
        invoice.discount_kind, invoice.discount_value = kind, str(value)
        invoice.discount_actor_id = actor_id if kind != "none" else None
        invoice.discount_reason = reason
    invoice.version += 1
    invoice.updated_at = moment
    record_document_revision(invoice, student.id, actor_id, "delete" if delete else "obligation_reversed" if reverse else "edit", before, reason)


def correct_payment(payment, student, actor_id, expected_version, *, amount=None, decision=None, delete=False, reason=None):
    if payment.student_id != student.id or payment.version != expected_version or payment.deleted_at is not None:
        raise FinanceConflict("This money movement changed or is unavailable. Reload before changing it.")
    actor = _actor(actor_id)
    if payment.kind != "collection":
        raise FinanceConflict("Correct the original collection together with its recorded reversal.")
    reversal = PaymentTransaction.query.filter_by(reversal_of_payment_transaction_id=payment.id).populate_existing().with_for_update().first()
    if reversal is not None and not delete:
        raise FinanceConflict("This collection has already been reversed. Its history remains available.")
    before = document_snapshot(payment)
    moment = whole_second_utc()
    receipt = Receipt.query.filter_by(payment_transaction_id=payment.id).populate_existing().with_for_update().first()
    receipt_before = document_snapshot(receipt) if receipt else None
    if delete:
        payment.deleted_at, payment.deleted_by_id, payment.deletion_reason = moment, actor_id, reason or "Recorded movement deleted"
        if reversal is not None and reversal.deleted_at is None:
            reversal_before = document_snapshot(reversal)
            reversal.deleted_at, reversal.deleted_by_id, reversal.deletion_reason = moment, actor_id, reason or "Original movement and reversal deleted"
            reversal.version += 1
            reversal.updated_at = moment
            record_document_revision(reversal, student.id, actor_id, "delete", reversal_before, reason)
    elif decision:
        if payment.status != "pending" or decision not in {"confirmed", "rejected"}:
            raise FinanceConflict("Only a pending movement can be confirmed or rejected.")
        payment.status = decision
        if decision == "confirmed":
            payment.confirmed_at, payment.confirmed_by_id = moment, actor.id
        else:
            payment.rejected_at, payment.rejected_by_id, payment.rejection_reason = moment, actor.id, reason or "Bank transfer rejected"
    else:
        new_amount = validate_amount(amount)
        if payment.movement_direction == "out" and new_amount > payment.amount:
            credit = -account_totals(student.id)["balance"]
            if new_amount - payment.amount > max(credit, Decimal(0)):
                raise FinanceConflict("The corrected payout would exceed this student's credit.")
        payment.amount = new_amount
    payment.version += 1
    payment.updated_at = moment
    record_document_revision(payment, student.id, actor_id, "delete" if delete else decision or "edit", before, reason)
    if receipt is not None:
        if receipt.status != "issued" or receipt.deleted_at is not None:
            return
        if delete:
            receipt.deleted_at, receipt.deleted_by_id, receipt.deletion_reason = moment, actor_id, reason or "Receipt deleted with its recorded movement"
        elif amount is not None:
            snapshot = dict(receipt.snapshot)
            snapshot["amount"] = format(payment.amount, ".4f")
            receipt.snapshot = snapshot
        else:
            return
        receipt.version += 1
        receipt.updated_at = moment
        record_document_revision(receipt, student.id, actor_id, "delete" if delete else "edit", receipt_before, reason)
    elif decision == "confirmed":
        # Persist the revision and confirmation before issuing the matching receipt.
        db.session.flush()
        issue_general_receipt(payment, student, actor_id)
