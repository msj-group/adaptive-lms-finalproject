"""Shared fixtures for the Phase 5 / M05 payment test modules.

Kept in one module, like ``tests/invoice_fixtures.py`` (whose academic chain,
assignment, invoice and token helpers it reuses), so the model, audit, route,
transaction and migration suites build the same payments and receipts.

Row helpers write straight into the tables, so a test about (say) a voided
receipt refusing a change is not also a test of the reversal route. The route
helpers at the bottom drive the application's own write path and read every
signed token out of the page the server rendered.

Every stored moment is a whole-second 2026 UTC instant earlier than the real
clock, so the timestamp CHECKs hold when a route later writes "now" on top of
a fixture row.
"""

import re
import uuid
from datetime import date, datetime

import tests.invoice_fixtures as fx
from app.extensions import db
from app.models import (
    Invoice,
    PaymentAuditEvent,
    PaymentTransaction,
    Receipt,
    ReceiptNumberSequence,
    StudentFeeAssignment,
    User,
)
from app.services.payment_audit import build_receipt_document

PW = fx.PW
STATE_FIELD = fx.STATE_FIELD
STALE_TEXT = fx.STALE_TEXT
INTEGRITY_TEXT = fx.INTEGRITY_TEXT
MISSING = fx.MISSING

RECORDED_AT = datetime(2026, 7, 1, 9, 0, 0)
DECIDED_AT = datetime(2026, 7, 2, 9, 0, 0)
REVERSED_AT = datetime(2026, 7, 3, 9, 0, 0)
TRANSFER_DATE = date(2026, 6, 30)
#: A submitted transfer date no later than the real clock's date.
TRANSFER_DATE_TEXT = "2026-06-15"

CASH_OK_TEXT = "Cash payment of"
BANK_RECORDED_OK_TEXT = "recorded as pending"
BANK_CONFIRMED_OK_TEXT = "Bank transfer confirmed."
BANK_REJECTED_OK_TEXT = "Bank transfer rejected."
REVERSED_OK_TEXT = "Payment reversed in full."
NOT_ISSUED_TEXT = "Payments are recorded only against an issued invoice"
ITEMS_INVALID_TEXT = "lines are not a valid charge"
LIMIT_TEXT = "already holds 25 payments"
BALANCE_BROKEN_TEXT = "do not describe a valid balance"
SETTLED_TEXT = "has no outstanding balance"
OVERPAYMENT_TEXT = "Overpayments and credit balances are not supported"
NOT_PENDING_TEXT = "This bank transfer is not pending"
NOT_REVERSIBLE_TEXT = "Only a confirmed cash or bank-transfer payment can be reversed"
ALREADY_REVERSED_TEXT = "This payment has already been reversed"
RECEIPT_MISSING_TEXT = "has no issued receipt to void"
EXHAUSTED_TEXT = "No receipt number is left for this year"
CASH_CONFIRM_TEXT = "tick the confirmation box before recording cash"
BANK_CONFIRM_TEXT = "tick the confirmation box before confirming this bank transfer"
REJECT_CONFIRM_TEXT = "tick the confirmation box before rejecting this bank transfer"
REVERSE_CONFIRM_TEXT = "tick the confirmation box before reversing this payment"
REJECT_REASON_TEXT = "Give the reason for rejecting this bank transfer."
REVERSE_REASON_TEXT = "Give the reason for reversing this payment."
#: Phase 5 / M10: a payment freezes the cancellation only; the lines keep a
#: payment floor instead.
FROZEN_TEXT = "has a pending or confirmed payment, so it can no longer be cancelled"
CARD_LIKE_TEXT = "looks like a payment card number"
FUTURE_DATE_TEXT = "cannot be later than today"
REFERENCE_MISSING_TEXT = "Enter the bank"

INVOICE_TOTAL = "1250.5000"

login_as = fx.login_as
fresh_identity = fx.fresh_identity
admin = fx.admin
user = fx.user
state_in = fx.state_in
page = fx.page
followed = fx.followed

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

OVERVIEW_URL = "/admin/payments"


def invoice_url(w, ip=None):
    return fx.detail_url(w, ip or w["ip"])


def payments_url(w, ip=None):
    return invoice_url(w, ip) + "/payments"


def cash_url(w, ip=None):
    return payments_url(w, ip) + "/cash"


def bank_url(w, ip=None):
    return payments_url(w, ip) + "/bank-transfer"


def confirm_url(w, pp, ip=None):
    return f"{payments_url(w, ip)}/{pp}/confirm"


def reject_url(w, pp, ip=None):
    return f"{payments_url(w, ip)}/{pp}/reject"


def reverse_url(w, pp, ip=None):
    return f"{payments_url(w, ip)}/{pp}/reverse"


def receipt_url(w, rp, ip=None):
    return f"{invoice_url(w, ip)}/receipts/{rp}"


# ---------------------------------------------------------------------------
# Rows, written directly
# ---------------------------------------------------------------------------


def issued_invoice(assignment, actor, lines=fx.DEFAULT_LINES, number=None):
    """An issued invoice of 1,250.500 LYD by default."""
    return fx.invoice(assignment, actor, status=fx.ISSUED, lines=lines,
                      number=number or f"INV-2026-{_next():06d}")


def payment(owner, actor, amount="100.000", method="cash", status="confirmed", kind="collection",
            reference=None, transfer_date=None, reversal_of=None, reason=None):
    """One transaction in a consistent lifecycle state, written directly."""
    bank = kind == "collection" and method == "bank_transfer"
    decided = bank and status != "pending"
    recorded_at = REVERSED_AT if kind == "reversal" else RECORDED_AT
    confirmed = status == "confirmed"
    rejected = status == "rejected"
    row = PaymentTransaction(
        invoice_id=owner.id,
        kind=kind,
        method=method,
        status=status,
        currency_code="LYD",
        amount=amount,
        bank_transfer_reference=(reference or f"REF-{_next()}") if bank else None,
        bank_transfer_date=(transfer_date or TRANSFER_DATE) if bank else None,
        recorded_at=recorded_at,
        recorded_by_id=actor.id,
        confirmed_at=(DECIDED_AT if bank else recorded_at) if confirmed else None,
        confirmed_by_id=actor.id if confirmed else None,
        rejected_at=DECIDED_AT if rejected else None,
        rejected_by_id=actor.id if rejected else None,
        rejection_reason=(reason or "Not received") if rejected else None,
        reversal_of_payment_transaction_id=None if reversal_of is None else reversal_of.id,
        version=2 if decided else 1,
        created_at=recorded_at,
        updated_at=DECIDED_AT if decided else recorded_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def receipt(pay, actor, number=None, status="issued", reason="Wrong amount"):
    """The receipt of a confirmed collection, written directly."""
    owner = db.session.get(Invoice, pay.invoice_id)
    assignment = db.session.get(StudentFeeAssignment, owner.student_fee_assignment_id)
    number = number or f"RCT-2026-{_next():06d}"
    public_id = str(uuid.uuid4())
    voided = status == "voided"
    row = Receipt(
        public_id=public_id,
        payment_transaction_id=pay.id,
        receipt_number=number,
        status=status,
        issued_at=pay.confirmed_at,
        issued_by_id=actor.id,
        voided_at=REVERSED_AT if voided else None,
        voided_by_id=actor.id if voided else None,
        void_reason=reason if voided else None,
        snapshot=build_receipt_document(
            receipt_public_id=public_id, receipt_number=number, payment=pay, invoice=owner,
            assignment_public_id=assignment.public_id, confirmed_by_name=actor.full_name,
            student_name="Student One", group_name="Group A", course_title="Course A",
            academic_term_name="Term A",
        ),
        version=2 if voided else 1,
        created_at=pay.confirmed_at,
        updated_at=REVERSED_AT if voided else pay.confirmed_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def cash_with_receipt(owner, actor, amount="100.000", number=None):
    pay = payment(owner, actor, amount=amount)
    return pay, receipt(pay, actor, number=number)


def reversed_collection(owner, actor, amount="100.000", method="cash"):
    """A confirmed collection, its voided receipt and its reversal."""
    status = "confirmed"
    original = payment(owner, actor, amount=amount, method=method, status=status)
    voided = receipt(original, actor, status="voided")
    reversal = payment(owner, actor, amount=amount, method=method, kind="reversal",
                       reversal_of=original)
    return original, voided, reversal


def sequence(calendar_year, last_number, moment=fx.CREATED_AT):
    row = ReceiptNumberSequence(calendar_year=calendar_year, last_number=last_number,
                                created_at=moment, updated_at=moment)
    db.session.add(row)
    db.session.commit()
    return row


def world(app, admin_email="admin@example.com"):
    """M04's world plus one issued invoice of 1,250.500 LYD for the
    assignment. Plain scalars only."""
    w = fx.world(app, admin_email)
    with app.app_context():
        owner = issued_invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]),
                               db.session.get(User, w["admin_id"]))
        w.update(ip=owner.public_id, invoice_id=owner.id)
    return w


def login_world(app, client):
    w = world(app)
    login_as(client, "admin@example.com")
    return w


def rows_of(app, w):
    """``(invoice, actor)`` of the world, in the current app context."""
    return db.session.get(Invoice, w["invoice_id"]), db.session.get(User, w["admin_id"])


def stored_payments(w):
    db.session.expire_all()
    return (PaymentTransaction.query.filter_by(invoice_id=w["invoice_id"])
            .order_by(PaymentTransaction.id).all())


def stored_payment(pp):
    db.session.expire_all()
    return PaymentTransaction.query.filter_by(public_id=pp).one()


def stored_receipts(w):
    db.session.expire_all()
    return (Receipt.query.join(PaymentTransaction, PaymentTransaction.id == Receipt.payment_transaction_id)
            .filter(PaymentTransaction.invoice_id == w["invoice_id"]).order_by(Receipt.id).all())


def stored_events(w):
    db.session.expire_all()
    return (PaymentAuditEvent.query.filter_by(invoice_id=w["invoice_id"])
            .order_by(PaymentAuditEvent.id).all())


def payment_record():
    """Every transaction, receipt, receipt sequence and M04 financial row --
    what "nothing was written" is compared against."""
    db.session.expire_all()
    return (
        [(r.id, r.public_id, r.invoice_id, r.kind, r.method, r.status, r.amount,
          r.bank_transfer_reference, r.bank_transfer_date, r.recorded_at, r.recorded_by_id,
          r.confirmed_at, r.confirmed_by_id, r.rejected_at, r.rejected_by_id, r.rejection_reason,
          r.reversal_of_payment_transaction_id, r.version, r.updated_at)
         for r in PaymentTransaction.query.order_by(PaymentTransaction.id)],
        [(r.id, r.public_id, r.payment_transaction_id, r.receipt_number, r.status, r.voided_at,
          r.voided_by_id, r.void_reason, r.version, r.updated_at, r.snapshot)
         for r in Receipt.query.order_by(Receipt.id)],
        [(r.calendar_year, r.last_number, r.updated_at)
         for r in ReceiptNumberSequence.query.order_by(ReceiptNumberSequence.id)],
        [(r.id, r.kind, r.payment_transaction_id, r.receipt_id, r.reason)
         for r in PaymentAuditEvent.query.order_by(PaymentAuditEvent.id)],
        fx.financial_record(),
    )


def record(app):
    with app.app_context():
        return payment_record()


# ---------------------------------------------------------------------------
# Route-driven helpers
# ---------------------------------------------------------------------------


def id_from(response, segment):
    location = response.headers["Location"]
    match = re.search(rf"/{segment}/([^/?]+)$", location)
    assert match, location
    return match.group(1)


def rp_from(response):
    return id_from(response, "receipts")


def cash_token(client, w, ip=None):
    return state_in(page(client, cash_url(w, ip)), cash_url(w, ip))


def record_cash(client, w, amount="100", confirm=True, token=None, ip=None):
    if token is None:
        token = cash_token(client, w, ip)
    data = {STATE_FIELD: token, "amount": amount}
    if confirm:
        data["confirm"] = "yes"
    return client.post(cash_url(w, ip), data=data)


def bank_token(client, w, ip=None):
    return state_in(page(client, bank_url(w, ip)), bank_url(w, ip))


def record_bank(client, w, amount="200", reference="TRX-0001", transfer_date=TRANSFER_DATE_TEXT,
                token=None, ip=None):
    if token is None:
        token = bank_token(client, w, ip)
    return client.post(bank_url(w, ip), data={
        STATE_FIELD: token, "amount": amount, "reference": reference,
        "transfer_date": transfer_date})


def confirm_token(client, w, pp):
    return state_in(page(client, confirm_url(w, pp)), confirm_url(w, pp))


def confirm(client, w, pp, confirm=True, token=None):
    if token is None:
        token = confirm_token(client, w, pp)
    data = {STATE_FIELD: token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(confirm_url(w, pp), data=data)


def reject_token(client, w, pp):
    return state_in(page(client, reject_url(w, pp)), reject_url(w, pp))


def reject(client, w, pp, reason="Not received", confirm=True, token=None):
    if token is None:
        token = reject_token(client, w, pp)
    data = {STATE_FIELD: token}
    if reason is not None:
        data["reason"] = reason
    if confirm:
        data["confirm"] = "yes"
    return client.post(reject_url(w, pp), data=data)


def reverse_token(client, w, pp):
    return state_in(page(client, reverse_url(w, pp)), reverse_url(w, pp))


def reverse(client, w, pp, reason="Wrong amount", confirm=True, token=None):
    if token is None:
        token = reverse_token(client, w, pp)
    data = {STATE_FIELD: token}
    if reason is not None:
        data["reason"] = reason
    if confirm:
        data["confirm"] = "yes"
    return client.post(reverse_url(w, pp), data=data)


def pending_transfer(client, w, amount="200"):
    """Drive a bank transfer recording; the new pending transaction's public id."""
    response = record_bank(client, w, amount=amount)
    assert response.status_code == 302, response.status_code
    with_pending = [row for row in stored_payments(w) if row.status == "pending"]
    return with_pending[-1].public_id
