"""Administrator manual payments, reversals and receipts (Phase 5 / M05).

Eight URL rules, addressed only by public identifiers. With ``<invoice>`` =
``/admin/groups/<gp>/enrollments/<ep>/fee-assignments/<ap>/invoices/<ip>``::

    GET       <invoice>/payments                       payment history and balance
    GET|POST  <invoice>/payments/cash                  confirm, then record cash
    GET|POST  <invoice>/payments/bank-transfer         record a pending bank transfer
    GET|POST  <invoice>/payments/<pp>/confirm          confirm a pending transfer
    GET|POST  <invoice>/payments/<pp>/reject           reject a pending transfer
    GET|POST  <invoice>/payments/<pp>/reverse          reverse a collection in full
    GET       <invoice>/receipts/<rp>                  one receipt
    GET       /admin/payments                          every payment, filtered and paged

**Payments are recorded only against an issued invoice**, by ``cash`` --
confirmed as it is recorded -- or ``bank_transfer`` -- recorded ``pending``
and then confirmed or rejected by an Administrator. A collection may be
partial and never exceeds the invoice's current outstanding balance; a pending
transfer reserves nothing. Every confirmed collection receives one permanent
``RCT-YYYY-NNNNNN`` receipt in the same transaction.

**A recorded payment is never edited or deleted.** A correction is a full
reversing entry for one confirmed collection, with a reason: a new row that
reopens the amount as outstanding and voids the collection's receipt, which is
kept. It is not a refund.

**Every movement writes its audit events** in the same transaction
(``app/services/payment_audit.py``). Recording a pending or confirmed payment
freezes the invoice's lines and cancellation (``app/blueprints/admin/invoices.py``);
nothing here changes an invoice's lines, amounts, status or number.

**This is the only surface that reads or writes a payment or a receipt.**
Every route is gated by ``roles_required``; every write re-proves the acting
account against its locked row.

**Every mutation** is POST-only and CSRF-protected, carries a purpose-specific
signed token (``app/services/payment_tokens.py``) minted by the GET page that
shows exactly what will change, runs the lock chain in
``app/services/payment_transactions.py``, and re-proves the actor, the
nesting, the academic chain the chain locked, the token, the invoice and
payment state, the balance and every applicable rule against the locked rows.
A form that fails validation is re-rendered only while its token still
describes current state. An ``IntegrityError`` or a refused audit event is
rolled back first, the actor is re-authorized from current state, and one
generic sentence is shown.

**Eligibility.** Recording or confirming a collection needs an issued invoice
with a valid set of lines and enough outstanding balance -- not an active
Student, Enrollment, Group, academic chain, plan or assignment, whose later
changes do not stop the collection of an issued charge. Rejecting a pending
transfer and reversing a confirmed collection likewise stay available whatever
later happened to that context: they correct financial history.

A URL naming anything that does not nest inside the link before it is a plain
404. Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``.
"""

import uuid

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from wtforms import StringField, TextAreaField
from wtforms.validators import ValidationError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plan_forms import AMOUNT_MESSAGES
from app.blueprints.admin.fee_plans import _financial_response, _reject, _tz_name, _write_moment
from app.blueprints.admin.invoices import (
    _BASE,
    _INTEGRITY_MESSAGE,
    _STATE_FIELD,
    _center_year,
    _context_or_404,
    _detail_url,
    _hierarchy_moved,
    _invoice_or_404,
    _locked_invoice_or_404,
    _page_context,
)
from app.extensions import db
from app.models import (
    BANK_TRANSFER_REFERENCE_MAX_LENGTH,
    MAX_INVOICE_COLLECTIONS,
    MAX_INVOICE_ITEM_ROWS,
    MAX_INVOICE_PAYMENT_ROWS,
    PAYMENT_REASON_MAX_LENGTH,
    InvoiceStatus,
    PaymentMethod,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    Receipt,
    ReceiptStatus,
    UserRole,
)
from app.models.fee_plan import TEXT_CONTROL, TEXT_MISSING, TEXT_TOO_LONG
from app.models.payment_audit_event import normalize_audit_reason
from app.models.payment_transaction import (
    DATE_FORMAT,
    DATE_IN_FUTURE,
    DATE_TOO_EARLY,
    EARLIEST_BANK_TRANSFER_DATE,
    REFERENCE_CARD_LIKE,
    normalize_bank_transfer_reference,
    parse_bank_transfer_date,
)
from app.security.decorators import roles_required
from app.services import money
from app.services import payment_tokens as tokens
from app.services.fee_plan_queries import normalize_page
from app.services.invoice_queries import STATUS_LABELS as INVOICE_STATUS_LABELS
from app.services.invoice_queries import active_lines, invoice_lines
from app.services.invoice_transactions import invoice_items_valid, invoice_rows_for_snapshot
from app.services.payment_audit import (
    BANK_CONFIRMED,
    BANK_RECORDED,
    BANK_REJECTED,
    CASH_RECORDED,
    RECEIPT_ISSUED,
    RECEIPT_VOIDED,
    REVERSED,
    build_payment_snapshot,
    build_receipt_document,
    record_payment_event,
)
from app.services.payment_queries import (
    METHOD_LABELS,
    PAGE_SIZE,
    STATUS_LABELS,
    account_names,
    build_balance_view,
    build_overview_view,
    build_payment_detail_view,
    build_payment_history_view,
    history_account_ids,
    invoice_payment,
    invoice_payment_rows,
    normalize_payment_method_filter,
    normalize_payment_status_filter,
    payments_overview_page,
    receipt_for_payment,
    receipts_by_payment,
)
from app.services.payment_transactions import (
    allocate_receipt_number,
    collection_rows,
    current_invoice_payments,
    lock_payment_chain,
    lock_receipt_number_sequence,
    locked_invoice_payments,
    payment_balance,
    payment_has_receipt,
    payment_nesting_broken,
    payment_rows_over_bound,
    receipt_number_taken,
    reversal_of,
)
from app.services.receipt_queries import build_receipt_view, invoice_receipt
from app.services.schedule_occurrences import to_app_local

_ISSUED = InvoiceStatus.ISSUED.value
_COLLECTION = PaymentTransactionKind.COLLECTION.value
_REVERSAL = PaymentTransactionKind.REVERSAL.value
_CASH = PaymentMethod.CASH.value
_BANK_TRANSFER = PaymentMethod.BANK_TRANSFER.value
_PENDING = PaymentTransactionStatus.PENDING.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value
_REJECTED = PaymentTransactionStatus.REJECTED.value
_RECEIPT_ISSUED = ReceiptStatus.ISSUED.value
_RECEIPT_VOIDED = ReceiptStatus.VOIDED.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

_INVOICE = _BASE + "/<invoice_public_id>"
_PAYMENTS = _INVOICE + "/payments"
_ONE = _PAYMENTS + "/<payment_public_id>"


# ======================================================================
# Administrator-facing sentences, declared once each
# ======================================================================

_STALE_MESSAGE = (
    "This invoice or its payments changed after this page was opened, or the page has expired. "
    "Nothing was saved. Please reload, review the current state, and try again."
)
_NOT_ISSUED_MESSAGE = (
    "Payments are recorded only against an issued invoice, and this invoice is not issued."
)
_ITEMS_INVALID_MESSAGE = (
    "This invoice's lines are not a valid charge, so no payment can be recorded against it."
)
_LIMIT_MESSAGE = (
    f"This invoice already holds {MAX_INVOICE_COLLECTIONS} payments, rejected ones included, "
    "so no further payment can be recorded against it."
)
_BALANCE_BROKEN_MESSAGE = (
    "This invoice's payment records do not describe a valid balance, so no payment can be "
    "recorded or decided here."
)
_SETTLED_MESSAGE = "This invoice has no outstanding balance, so no further payment can be recorded."
_BELOW_MINIMUM_MESSAGE = (
    "This invoice's outstanding balance is below the smallest amount a payment can record."
)
_OVERPAYMENT_MESSAGE = (
    "The amount is more than this invoice's outstanding balance of {outstanding} "
    f"{money.CURRENCY_CODE}. Overpayments and credit balances are not supported."
)
_NOT_PENDING_MESSAGE = (
    "This bank transfer is not pending: it has already been confirmed or rejected. Nothing was "
    "changed."
)
_NOT_REVERSIBLE_MESSAGE = (
    "Only a confirmed cash or bank-transfer payment can be reversed. Nothing was changed."
)
_ALREADY_REVERSED_MESSAGE = (
    "This payment has already been reversed. A payment is reversed at most once. Nothing was "
    "changed."
)
_RECEIPT_MISSING_MESSAGE = (
    "This payment has no issued receipt to void, so it cannot be reversed. Nothing was changed."
)
_RECEIPTS_EXHAUSTED_MESSAGE = (
    "No receipt number is left for this year. Nothing was recorded and no number was used."
)
_CASH_CONFIRM_MESSAGE = (
    "Please tick the confirmation box before recording cash. A recorded payment is permanent."
)
_BANK_CONFIRM_MESSAGE = "Please tick the confirmation box before confirming this bank transfer."
_REJECT_CONFIRM_MESSAGE = "Please tick the confirmation box before rejecting this bank transfer."
_REVERSE_CONFIRM_MESSAGE = "Please tick the confirmation box before reversing this payment."
_REJECT_REASON_MISSING = "Give the reason for rejecting this bank transfer."
_REVERSE_REASON_MISSING = "Give the reason for reversing this payment."
_CASH_OK_MESSAGE = "Cash payment of {amount} {currency} recorded. Receipt {number} issued."
_BANK_RECORDED_OK_MESSAGE = (
    "Bank transfer of {amount} {currency} recorded as pending. It does not change the balance "
    "until it is confirmed."
)
_BANK_CONFIRMED_OK_MESSAGE = "Bank transfer confirmed. Receipt {number} issued."
_BANK_REJECTED_OK_MESSAGE = (
    "Bank transfer rejected. It is kept as history and does not change the balance."
)
_REVERSED_OK_MESSAGE = (
    "Payment reversed in full. Its amount is outstanding again, and receipt {number} is void "
    "and kept as history."
)

_REFERENCE_MESSAGES = {
    TEXT_MISSING: "Enter the bank's reference for this transfer.",
    TEXT_CONTROL: "The reference may only contain ordinary text.",
    TEXT_TOO_LONG: (
        f"The reference must be at most {BANK_TRANSFER_REFERENCE_MAX_LENGTH} characters."
    ),
    REFERENCE_CARD_LIKE: (
        "This looks like a payment card number. Never enter card numbers, account numbers or "
        "credentials; enter only the bank's transfer reference."
    ),
}
_DATE_MESSAGES = {
    TEXT_MISSING: "Enter the date of the transfer.",
    DATE_FORMAT: "Enter the transfer date as a real date, for example 2026-09-15.",
    DATE_TOO_EARLY: (
        f"The transfer date must be on or after {EARLIEST_BANK_TRANSFER_DATE.isoformat()}."
    ),
    DATE_IN_FUTURE: "The transfer date cannot be later than today.",
}
_REASON_MESSAGES = {
    TEXT_CONTROL: "The reason may only contain ordinary text and line breaks.",
    TEXT_TOO_LONG: f"The reason must be at most {PAYMENT_REASON_MAX_LENGTH} characters.",
}


# ======================================================================
# Forms
# ======================================================================


def _amount_error(field):
    value, error = money.parse_amount(field.data)
    if error is not None:
        raise ValidationError(AMOUNT_MESSAGES[error])
    return value


class CashPaymentForm(FlaskForm):
    """The exact amount of cash received. The amount is text until
    :func:`money.parse_amount` accepts it; there is no card, account, provider,
    currency or status field."""

    amount = StringField(f"Amount received ({money.CURRENCY_CODE})")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parsed_amount = None

    def validate_amount(self, field):
        self.parsed_amount = _amount_error(field)


class BankTransferForm(FlaskForm):
    """The exact amount, the bank's reference and the civil date of one
    transfer. No account number, card, credential or proof upload."""

    amount = StringField(f"Amount transferred ({money.CURRENCY_CODE})")
    reference = StringField("Transfer reference")
    transfer_date = StringField("Transfer date")

    def __init__(self, *args, latest_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.latest_date = latest_date
        self.parsed_amount = None
        self.normalized_reference = None
        self.parsed_date = None

    def validate_amount(self, field):
        self.parsed_amount = _amount_error(field)

    def validate_reference(self, field):
        value, error = normalize_bank_transfer_reference(field.data)
        if error is not None:
            raise ValidationError(_REFERENCE_MESSAGES[error])
        self.normalized_reference = value

    def validate_transfer_date(self, field):
        value, error = parse_bank_transfer_date(field.data, self.latest_date)
        if error is not None:
            raise ValidationError(_DATE_MESSAGES[error])
        self.parsed_date = value


class PaymentReasonForm(FlaskForm):
    """The reason for rejecting a transfer or reversing a payment."""

    reason = TextAreaField("Reason")

    def __init__(self, *args, missing_message=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.missing_message = missing_message
        self.normalized_reason = None

    def validate_reason(self, field):
        value, error = normalize_audit_reason(field.data)
        if error is not None:
            raise ValidationError(
                self.missing_message if error == TEXT_MISSING else _REASON_MESSAGES[error]
            )
        self.normalized_reason = value


# ======================================================================
# URLs, state and shared handling
# ======================================================================


def _ids(context, invoice_public_id):
    return {
        "group_public_id": context.group_public_id,
        "enrollment_public_id": context.enrollment_public_id,
        "assignment_public_id": context.assignment_public_id,
        "invoice_public_id": invoice_public_id,
    }


def _payments_url(context, invoice_public_id):
    return url_for("admin.invoice_payments", **_ids(context, invoice_public_id))


def _cash_url(context, invoice_public_id):
    return url_for("admin.invoice_payment_cash", **_ids(context, invoice_public_id))


def _bank_url(context, invoice_public_id):
    return url_for("admin.invoice_payment_bank_transfer", **_ids(context, invoice_public_id))


def _payment_url(endpoint, context, invoice_public_id, payment_public_id):
    return url_for(
        endpoint, payment_public_id=payment_public_id, **_ids(context, invoice_public_id)
    )


def _receipt_url(context, invoice_public_id, receipt_public_id):
    return url_for(
        "admin.invoice_receipt_detail",
        receipt_public_id=receipt_public_id,
        **_ids(context, invoice_public_id),
    )


def _record_state(actor_public_id, invoice, rows):
    return {
        "actor_public_id": actor_public_id,
        "invoice_public_id": invoice.public_id,
        "invoice_version": invoice.version,
        "payment_state": tokens.payment_state(rows),
    }


def _decision_state(actor_public_id, invoice, payment, rows):
    return {
        "actor_public_id": actor_public_id,
        "invoice_public_id": invoice.public_id,
        "payment_public_id": payment.public_id,
        "payment_version": payment.version,
        "payment_state": tokens.payment_state(rows),
    }


def _reverse_state(actor_public_id, invoice, payment, receipt):
    return {
        "actor_public_id": actor_public_id,
        "invoice_public_id": invoice.public_id,
        "invoice_version": invoice.version,
        "payment_public_id": payment.public_id,
        "payment_version": payment.version,
        "receipt_state": tokens.receipt_state(receipt),
    }


def _invoice_view(invoice):
    return {
        "public_id": invoice.public_id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status,
        "status_label": INVOICE_STATUS_LABELS.get(invoice.status, invoice.status),
        "is_issued": invoice.status == _ISSUED,
    }


def _payment_or_404(invoice, payment_public_id):
    payment = invoice_payment(invoice.id, payment_public_id)
    if payment is None:
        abort(404)
    return payment


def _balance_of(active, rows):
    """The balance of pre- or post-lock rows, or ``None`` when they cannot
    describe one (more rows than any balance may read, or a negative amount)."""
    return None if payment_rows_over_bound(rows) else payment_balance(active, rows)


def _pre_lock_state(invoice):
    """``(lines, active, rows, balance)`` read before any lock -- what a page
    shows and a friendly preview decides, never what a write decides."""
    lines = invoice_lines(invoice.id)
    active = active_lines(lines)
    rows = invoice_payment_rows(invoice.id)
    return lines, active, rows, _balance_of(active, rows)


def _payable_block(invoice, lines, active, rows, balance):
    """Why no collection may be recorded or confirmed against `invoice` now,
    as a sentence, or ``None``. Used with pre-lock rows for a page and with
    locked rows for the decision."""
    if invoice.status != _ISSUED:
        return _NOT_ISSUED_MESSAGE
    if len(lines) > MAX_INVOICE_ITEM_ROWS or not invoice_items_valid(active):
        return _ITEMS_INVALID_MESSAGE
    if balance is None:
        return _BALANCE_BROKEN_MESSAGE
    return None


def _record_block(invoice, lines, active, rows, balance):
    """:func:`_payable_block`, plus the collection bound and a balance a new
    collection could still settle."""
    block = _payable_block(invoice, lines, active, rows, balance)
    if block is not None:
        return block
    if len(collection_rows(rows)) >= MAX_INVOICE_COLLECTIONS:
        return _LIMIT_MESSAGE
    if balance.outstanding == 0:
        return _SETTLED_MESSAGE
    if balance.outstanding < money.MIN_AMOUNT:
        return _BELOW_MINIMUM_MESSAGE
    return None


def _overpayment_message(balance):
    return _OVERPAYMENT_MESSAGE.format(outstanding=money.format_amount(balance.outstanding))


def _lock(context, actor_id, invoice_id, **links):
    """The M05 chain for `context`'s invoice. `context` is a plain row
    captured before the chain's reset, so it stays readable afterwards."""
    return lock_payment_chain(
        context.group_public_id,
        context.academic_term_id,
        context.level_id,
        context.course_id,
        context.student_id,
        context.enrollment_id,
        actor_id,
        context.assignment_id,
        invoice_id,
        **links,
    )


def _locked_payment_or_404(locks, payment_id, payment_public_id):
    if payment_nesting_broken(locks, payment_id, payment_public_id):
        db.session.rollback()
        abort(404)
    return locks.payment


def _local_date(moment):
    return to_app_local(_tz_name(), moment).date()


def _issue_receipt(context, locks, invoice, active, payments, payment, number, moment, before, kind):
    """Write the collection's own event, its receipt and the receipt's event,
    for a `payment` already confirmed in this transaction. `payments` are all
    of the invoice's transactions, `payment` included. Returns the receipt's
    public id; the caller commits and catches ``IntegrityError`` /
    ``ValueError``."""
    actor = locks.chain.actor
    after_payment = build_payment_snapshot(invoice, active, payments, payment=payment)
    record_payment_event(
        invoice=invoice,
        actor=actor,
        kind=kind,
        payment=payment,
        receipt=None,
        before_snapshot=before,
        after_snapshot=after_payment,
        reason=None,
        moment=moment,
    )
    hierarchy = locks.chain.hierarchy
    receipt_public_id = str(uuid.uuid4())
    receipt = Receipt(
        public_id=receipt_public_id,
        payment_transaction_id=payment.id,
        receipt_number=number,
        status=_RECEIPT_ISSUED,
        issued_at=moment,
        issued_by_id=actor.id,
        voided_at=None,
        voided_by_id=None,
        void_reason=None,
        snapshot=build_receipt_document(
            receipt_public_id=receipt_public_id,
            receipt_number=number,
            payment=payment,
            invoice=invoice,
            assignment_public_id=context.assignment_public_id,
            confirmed_by_name=actor.full_name,
            student_name=locks.chain.student.full_name,
            group_name=locks.chain.group.name,
            course_title=hierarchy.course(context.course_id).title,
            academic_term_name=hierarchy.term(context.academic_term_id).name,
        ),
        version=1,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(receipt)
    db.session.flush()
    record_payment_event(
        invoice=invoice,
        actor=actor,
        kind=RECEIPT_ISSUED,
        payment=payment,
        receipt=receipt,
        before_snapshot=after_payment,
        after_snapshot=build_payment_snapshot(
            invoice, active, payments, payment=payment, receipt=receipt
        ),
        reason=None,
        moment=moment,
    )
    return receipt_public_id


def _allocate_receipt_number(locked, active, payments, before_payment, reject_url, actor_id):
    """Build the "before" snapshot, then take the year's sequence lock -- the
    last lock -- and allocate one number. ``(before, moment, number, None)``
    or ``(None, None, None, response)``."""
    provisional = _write_moment()
    year = _center_year(provisional)
    try:
        before = build_payment_snapshot(locked, active, payments, payment=before_payment)
        sequence = lock_receipt_number_sequence(year, provisional)
    except (IntegrityError, ValueError):
        return None, None, None, _reject(_INTEGRITY_MESSAGE, reject_url, actor_id)
    moment = _write_moment()
    if _center_year(moment) != year:
        return None, None, None, _reject(_STALE_MESSAGE, reject_url, actor_id)
    number = allocate_receipt_number(sequence, moment)
    if number is None:
        return None, None, None, _reject(_RECEIPTS_EXHAUSTED_MESSAGE, reject_url, actor_id, "warning")
    return before, moment, number, None


def _history_entries(context, invoice, rows):
    shown = sorted(rows, key=lambda row: row.id)[:MAX_INVOICE_PAYMENT_ROWS]
    receipts = receipts_by_payment([row.id for row in shown])
    names = account_names(history_account_ids(shown, receipts))
    entries = build_payment_history_view(shown, receipts, names, _tz_name())
    for entry in entries:
        pp = entry["public_id"]
        pending = entry["is_pending"] and entry["kind"] == _COLLECTION
        entry["confirm_url"] = (
            _payment_url("admin.invoice_payment_confirm", context, invoice.public_id, pp)
            if pending
            else None
        )
        entry["reject_url"] = (
            _payment_url("admin.invoice_payment_reject", context, invoice.public_id, pp)
            if pending
            else None
        )
        receipt = entry["receipt"]
        entry["reverse_url"] = (
            _payment_url("admin.invoice_payment_reverse", context, invoice.public_id, pp)
            if entry["is_confirmed_collection"]
            and not entry["is_reversed"]
            and receipt is not None
            and receipt["is_issued"]
            else None
        )
        if receipt is not None:
            receipt["url"] = _receipt_url(context, invoice.public_id, receipt["public_id"])
    return entries


def _common(context, invoice, balance):
    return {
        "invoice": _invoice_view(invoice),
        "balance": build_balance_view(balance),
        "detail_url": _detail_url(context, invoice.public_id),
        "payments_url": _payments_url(context, invoice.public_id),
        "tz_name": _tz_name(),
        **_page_context(context),
    }


# ======================================================================
# One invoice's payments
# ======================================================================


@admin_bp.get(_PAYMENTS)
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payments(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    """The invoice's exact total, paid and outstanding amounts and every one of
    its transactions, newest first, with the controls each allows. A fixed
    number of queries whatever the invoice holds; no ``COUNT``."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    lines, active, rows, balance = _pre_lock_state(invoice)
    block = _record_block(invoice, lines, active, rows, balance)
    return render_template(
        "admin/payments/history.html",
        payments=_history_entries(context, invoice, rows),
        record_block=block,
        cash_url=None if block else _cash_url(context, invoice.public_id),
        bank_url=None if block else _bank_url(context, invoice.public_id),
        rows_truncated=payment_rows_over_bound(rows),
        **_common(context, invoice, balance),
    )


def _render_cash(context, invoice, form, rows, balance, confirm_error=None):
    return render_template(
        "admin/payments/cash.html",
        form=form,
        confirm_error=confirm_error,
        action_url=_cash_url(context, invoice.public_id),
        state_token=tokens.make_token(
            tokens.PURPOSE_CASH_RECORD, **_record_state(current_user.public_id, invoice, rows)
        ),
        min_amount=money.format_amount(money.MIN_AMOUNT),
        amount_scale=money.AMOUNT_SCALE,
        **_common(context, invoice, balance),
    )


@admin_bp.route(_PAYMENTS + "/cash", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_cash(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id
):
    """GET: the cash confirmation form. POST: record a confirmed cash
    collection within the outstanding balance, issue its receipt and write
    both events -- all in one transaction."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    payments_url = _payments_url(context, invoice.public_id)
    cash_url = _cash_url(context, invoice.public_id)
    lines, active, rows, balance = _pre_lock_state(invoice)
    block = _record_block(invoice, lines, active, rows, balance)
    form = CashPaymentForm(formdata=request.form if request.method == "POST" else None)
    if request.method == "GET":
        if block is not None:
            flash(block, "warning")
            return redirect(payments_url)
        return _render_cash(context, invoice, form, rows, balance)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_CASH_RECORD, **_record_state(actor_public_id, invoice, rows)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    if block is not None:
        return _reject(block, payments_url, actor_id, "warning")
    confirmed = request.form.get("confirm") == "yes"
    if not form.validate_on_submit() or not confirmed:
        return _render_cash(
            context, invoice, form, rows, balance, None if confirmed else _CASH_CONFIRM_MESSAGE
        )
    amount = form.parsed_amount
    if amount > balance.outstanding:
        form.amount.errors.append(_overpayment_message(balance))
        return _render_cash(context, invoice, form, rows, balance)
    invoice_id = invoice.id

    locks = _lock(context, actor_id, invoice_id, include_invoice_payments=True)
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    payments = locked_invoice_payments(locks)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_CASH_RECORD, **_record_state(actor_public_id, locked, payments)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    item_rows = invoice_rows_for_snapshot(locked)
    active = active_lines(item_rows)
    balance = _balance_of(active, payments)
    block = _record_block(locked, item_rows, active, payments, balance)
    if block is not None:
        return _reject(block, payments_url, actor_id, "warning")
    if amount > balance.outstanding:
        return _reject(_overpayment_message(balance), cash_url, actor_id, "warning")

    before, moment, number, refused = _allocate_receipt_number(
        locked, active, payments, None, payments_url, actor_id
    )
    if refused is not None:
        return refused
    try:
        if receipt_number_taken(number):
            return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)
        payment = PaymentTransaction(
            public_id=str(uuid.uuid4()),
            invoice_id=locked.id,
            kind=_COLLECTION,
            method=_CASH,
            status=_CONFIRMED,
            currency_code=money.CURRENCY_CODE,
            amount=amount,
            bank_transfer_reference=None,
            bank_transfer_date=None,
            recorded_at=moment,
            recorded_by_id=actor_id,
            confirmed_at=moment,
            confirmed_by_id=actor_id,
            rejected_at=None,
            rejected_by_id=None,
            rejection_reason=None,
            reversal_of_payment_transaction_id=None,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
        db.session.add(payment)
        db.session.flush()
        receipt_public_id = _issue_receipt(
            context, locks, locked, active, payments + [payment], payment, number, moment, before,
            CASH_RECORDED,
        )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)

    flash(
        _CASH_OK_MESSAGE.format(
            amount=money.format_amount(amount), currency=money.CURRENCY_CODE, number=number
        ),
        "success",
    )
    return redirect(_receipt_url(context, invoice_public_id, receipt_public_id))


def _render_bank(context, invoice, form, rows, balance):
    return render_template(
        "admin/payments/bank_transfer.html",
        form=form,
        action_url=_bank_url(context, invoice.public_id),
        state_token=tokens.make_token(
            tokens.PURPOSE_BANK_RECORD, **_record_state(current_user.public_id, invoice, rows)
        ),
        min_amount=money.format_amount(money.MIN_AMOUNT),
        amount_scale=money.AMOUNT_SCALE,
        reference_max=BANK_TRANSFER_REFERENCE_MAX_LENGTH,
        latest_date=form.latest_date.isoformat(),
        earliest_date=EARLIEST_BANK_TRANSFER_DATE.isoformat(),
        **_common(context, invoice, balance),
    )


@admin_bp.route(_PAYMENTS + "/bank-transfer", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_bank_transfer(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id
):
    """GET: the bank transfer form. POST: record a pending transfer within the
    outstanding balance, with its reference and date, and write its event. It
    changes no balance and reserves nothing until it is confirmed."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    payments_url = _payments_url(context, invoice.public_id)
    bank_url = _bank_url(context, invoice.public_id)
    lines, active, rows, balance = _pre_lock_state(invoice)
    block = _record_block(invoice, lines, active, rows, balance)
    form = BankTransferForm(
        formdata=request.form if request.method == "POST" else None,
        latest_date=_local_date(_write_moment()),
    )
    if request.method == "GET":
        if block is not None:
            flash(block, "warning")
            return redirect(payments_url)
        return _render_bank(context, invoice, form, rows, balance)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_BANK_RECORD, **_record_state(actor_public_id, invoice, rows)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    if block is not None:
        return _reject(block, payments_url, actor_id, "warning")
    if not form.validate_on_submit():
        return _render_bank(context, invoice, form, rows, balance)
    amount, reference, transfer_date = (
        form.parsed_amount,
        form.normalized_reference,
        form.parsed_date,
    )
    if amount > balance.outstanding:
        form.amount.errors.append(_overpayment_message(balance))
        return _render_bank(context, invoice, form, rows, balance)
    invoice_id = invoice.id

    locks = _lock(context, actor_id, invoice_id, include_invoice_payments=True)
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    payments = locked_invoice_payments(locks)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_BANK_RECORD, **_record_state(actor_public_id, locked, payments)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    item_rows = invoice_rows_for_snapshot(locked)
    active = active_lines(item_rows)
    balance = _balance_of(active, payments)
    block = _record_block(locked, item_rows, active, payments, balance)
    if block is not None:
        return _reject(block, payments_url, actor_id, "warning")
    if amount > balance.outstanding:
        return _reject(_overpayment_message(balance), bank_url, actor_id, "warning")

    moment = _write_moment()
    if transfer_date > _local_date(moment):
        return _reject(_STALE_MESSAGE, bank_url, actor_id)
    try:
        before = build_payment_snapshot(locked, active, payments)
        payment = PaymentTransaction(
            public_id=str(uuid.uuid4()),
            invoice_id=locked.id,
            kind=_COLLECTION,
            method=_BANK_TRANSFER,
            status=_PENDING,
            currency_code=money.CURRENCY_CODE,
            amount=amount,
            bank_transfer_reference=reference,
            bank_transfer_date=transfer_date,
            recorded_at=moment,
            recorded_by_id=actor_id,
            confirmed_at=None,
            confirmed_by_id=None,
            rejected_at=None,
            rejected_by_id=None,
            rejection_reason=None,
            reversal_of_payment_transaction_id=None,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
        db.session.add(payment)
        db.session.flush()
        record_payment_event(
            invoice=locked,
            actor=locks.chain.actor,
            kind=BANK_RECORDED,
            payment=payment,
            receipt=None,
            before_snapshot=before,
            after_snapshot=build_payment_snapshot(
                locked, active, payments + [payment], payment=payment
            ),
            reason=None,
            moment=moment,
        )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)

    flash(
        _BANK_RECORDED_OK_MESSAGE.format(
            amount=money.format_amount(amount), currency=money.CURRENCY_CODE
        ),
        "success",
    )
    return redirect(payments_url)


# ======================================================================
# Deciding a pending bank transfer
# ======================================================================


def _is_pending_transfer(payment):
    return (
        payment.kind == _COLLECTION
        and payment.method == _BANK_TRANSFER
        and payment.status == _PENDING
    )


def _confirm_block(invoice, lines, active, rows, balance, payment):
    block = _payable_block(invoice, lines, active, rows, balance)
    if block is not None:
        return block
    if payment.amount > balance.outstanding:
        return _overpayment_message(balance)
    return None


def _detail_view(payment):
    return build_payment_detail_view(
        payment,
        None,
        account_names([payment.recorded_by_id, payment.confirmed_by_id, payment.rejected_by_id]),
        _tz_name(),
    )


def _render_decision(template, purpose, context, invoice, payment, rows, balance, **extra):
    return render_template(
        template,
        payment=_detail_view(payment),
        state_token=tokens.make_token(
            purpose, **_decision_state(current_user.public_id, invoice, payment, rows)
        ),
        **extra,
        **_common(context, invoice, balance),
    )


@admin_bp.route(_ONE + "/confirm", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_confirm(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    payment_public_id,
):
    """Confirm a pending bank transfer that still fits the outstanding balance:
    record who and when, move its version once, issue its receipt and write
    both events. Of two pending transfers, only a confirmation that still fits
    the balance it finds under the invoice lock succeeds."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    payment = _payment_or_404(invoice, payment_public_id)
    payments_url = _payments_url(context, invoice.public_id)
    action_url = _payment_url(
        "admin.invoice_payment_confirm", context, invoice.public_id, payment.public_id
    )
    if not _is_pending_transfer(payment):
        flash(_NOT_PENDING_MESSAGE, "info")
        return redirect(payments_url)
    lines, active, rows, balance = _pre_lock_state(invoice)
    block = _confirm_block(invoice, lines, active, rows, balance, payment)

    def render(confirm_error=None):
        return _render_decision(
            "admin/payments/confirm.html", tokens.PURPOSE_BANK_CONFIRM, context, invoice,
            payment, rows, balance, action_url=action_url, confirm_error=confirm_error,
        )

    if request.method == "GET":
        if block is not None:
            flash(block, "warning")
            return redirect(payments_url)
        return render()

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_BANK_CONFIRM,
        **_decision_state(actor_public_id, invoice, payment, rows),
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    if block is not None:
        return _reject(block, payments_url, actor_id, "warning")
    if request.form.get("confirm") != "yes":
        return render(_BANK_CONFIRM_MESSAGE)
    invoice_id, payment_id = invoice.id, payment.id

    locks = _lock(
        context, actor_id, invoice_id, payment_id=payment_id, include_invoice_payments=True
    )
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    target = _locked_payment_or_404(locks, payment_id, payment_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    payments = locked_invoice_payments(locks)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_BANK_CONFIRM,
        **_decision_state(actor_public_id, locked, target, payments),
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    if not _is_pending_transfer(target):
        return _reject(_NOT_PENDING_MESSAGE, payments_url, actor_id, "info")
    item_rows = invoice_rows_for_snapshot(locked)
    active = active_lines(item_rows)
    balance = _balance_of(active, payments)
    block = _confirm_block(locked, item_rows, active, payments, balance, target)
    if block is not None:
        return _reject(block, payments_url, actor_id, "warning")

    before, moment, number, refused = _allocate_receipt_number(
        locked, active, payments, target, payments_url, actor_id
    )
    if refused is not None:
        return refused
    try:
        if receipt_number_taken(number) or payment_has_receipt(target.id):
            return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)
        target.status = _CONFIRMED
        target.confirmed_at = moment
        target.confirmed_by_id = actor_id
        target.version = target.version + 1
        target.updated_at = moment
        db.session.flush()
        receipt_public_id = _issue_receipt(
            context, locks, locked, active, payments, target, number, moment, before,
            BANK_CONFIRMED,
        )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)

    flash(_BANK_CONFIRMED_OK_MESSAGE.format(number=number), "success")
    return redirect(_receipt_url(context, invoice_public_id, receipt_public_id))


@admin_bp.route(_ONE + "/reject", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_reject(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    payment_public_id,
):
    """Reject a pending bank transfer with a reason: record who, when and why,
    move its version once and write its event. It never had, and never gets,
    a financial effect or a receipt. Available whatever later happened to the
    Student, Enrollment, Group, academic chain, plan or assignment."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    payment = _payment_or_404(invoice, payment_public_id)
    payments_url = _payments_url(context, invoice.public_id)
    action_url = _payment_url(
        "admin.invoice_payment_reject", context, invoice.public_id, payment.public_id
    )
    if not _is_pending_transfer(payment):
        flash(_NOT_PENDING_MESSAGE, "info")
        return redirect(payments_url)
    _lines, _active, rows, balance = _pre_lock_state(invoice)
    form = PaymentReasonForm(
        formdata=request.form if request.method == "POST" else None,
        missing_message=_REJECT_REASON_MISSING,
    )

    def render(confirm_error=None):
        return _render_decision(
            "admin/payments/reject.html", tokens.PURPOSE_BANK_REJECT, context, invoice, payment,
            rows, balance, form=form, action_url=action_url, confirm_error=confirm_error,
            reason_max=PAYMENT_REASON_MAX_LENGTH,
        )

    if request.method == "GET":
        return render()

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_BANK_REJECT,
        **_decision_state(actor_public_id, invoice, payment, rows),
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    confirmed = request.form.get("confirm") == "yes"
    if not form.validate_on_submit() or not confirmed:
        return render(None if confirmed else _REJECT_CONFIRM_MESSAGE)
    reason = form.normalized_reason
    invoice_id, payment_id = invoice.id, payment.id

    locks = _lock(context, actor_id, invoice_id, payment_id=payment_id)
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    target = _locked_payment_or_404(locks, payment_id, payment_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    payments = current_invoice_payments(locked)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_BANK_REJECT,
        **_decision_state(actor_public_id, locked, target, payments),
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    if not _is_pending_transfer(target):
        return _reject(_NOT_PENDING_MESSAGE, payments_url, actor_id, "info")
    if locked.status != _ISSUED:
        return _reject(_NOT_ISSUED_MESSAGE, payments_url, actor_id, "warning")
    active = active_lines(invoice_rows_for_snapshot(locked))
    if _balance_of(active, payments) is None:
        return _reject(_BALANCE_BROKEN_MESSAGE, payments_url, actor_id, "warning")

    moment = _write_moment()
    try:
        before = build_payment_snapshot(locked, active, payments, payment=target)
        target.status = _REJECTED
        target.rejected_at = moment
        target.rejected_by_id = actor_id
        target.rejection_reason = reason
        target.version = target.version + 1
        target.updated_at = moment
        record_payment_event(
            invoice=locked,
            actor=locks.chain.actor,
            kind=BANK_REJECTED,
            payment=target,
            receipt=None,
            before_snapshot=before,
            after_snapshot=build_payment_snapshot(locked, active, payments, payment=target),
            reason=reason,
            moment=moment,
        )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)

    flash(_BANK_REJECTED_OK_MESSAGE, "success")
    return redirect(payments_url)


# ======================================================================
# Full reversal
# ======================================================================


def _is_confirmed_collection(payment):
    return payment.kind == _COLLECTION and payment.status == _CONFIRMED


@admin_bp.route(_ONE + "/reverse", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_reverse(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    payment_public_id,
):
    """Reverse one confirmed collection in full, with a reason: insert a
    confirmed reversal of the same amount and method, void the collection's
    receipt, and write both events. The collection itself never changes; its
    amount becomes outstanding again. Available whatever later happened to the
    operational context."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    payment = _payment_or_404(invoice, payment_public_id)
    payments_url = _payments_url(context, invoice.public_id)
    action_url = _payment_url(
        "admin.invoice_payment_reverse", context, invoice.public_id, payment.public_id
    )
    if not _is_confirmed_collection(payment):
        flash(_NOT_REVERSIBLE_MESSAGE, "info")
        return redirect(payments_url)
    _lines, _active, rows, balance = _pre_lock_state(invoice)
    if reversal_of(rows, payment.id) is not None:
        flash(_ALREADY_REVERSED_MESSAGE, "info")
        return redirect(payments_url)
    receipt = receipt_for_payment(payment.id)
    if receipt is None or receipt.status != _RECEIPT_ISSUED:
        flash(_RECEIPT_MISSING_MESSAGE, "warning")
        return redirect(payments_url)
    form = PaymentReasonForm(
        formdata=request.form if request.method == "POST" else None,
        missing_message=_REVERSE_REASON_MISSING,
    )

    def render(confirm_error=None):
        return render_template(
            "admin/payments/reverse.html",
            form=form,
            payment=_detail_view(payment),
            receipt_number=receipt.receipt_number,
            action_url=action_url,
            confirm_error=confirm_error,
            reason_max=PAYMENT_REASON_MAX_LENGTH,
            state_token=tokens.make_token(
                tokens.PURPOSE_REVERSE,
                **_reverse_state(current_user.public_id, invoice, payment, receipt),
            ),
            **_common(context, invoice, balance),
        )

    if request.method == "GET":
        return render()

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_REVERSE, **_reverse_state(actor_public_id, invoice, payment, receipt)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    confirmed = request.form.get("confirm") == "yes"
    if not form.validate_on_submit() or not confirmed:
        return render(None if confirmed else _REVERSE_CONFIRM_MESSAGE)
    reason = form.normalized_reason
    invoice_id, payment_id = invoice.id, payment.id

    locks = _lock(
        context,
        actor_id,
        invoice_id,
        payment_id=payment_id,
        include_invoice_payments=True,
        include_receipt=True,
    )
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    original = _locked_payment_or_404(locks, payment_id, payment_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    receipt = locks.receipt
    if receipt is not None and receipt.payment_transaction_id != original.id:
        receipt = None
    if tokens.token_is_stale(
        token, tokens.PURPOSE_REVERSE, **_reverse_state(actor_public_id, locked, original, receipt)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    if not _is_confirmed_collection(original):
        return _reject(_NOT_REVERSIBLE_MESSAGE, payments_url, actor_id, "info")
    payments = locked_invoice_payments(locks)
    if reversal_of(payments, original.id) is not None:
        return _reject(_ALREADY_REVERSED_MESSAGE, payments_url, actor_id, "info")
    if receipt is None or receipt.status != _RECEIPT_ISSUED:
        return _reject(_RECEIPT_MISSING_MESSAGE, payments_url, actor_id, "warning")
    if locked.status != _ISSUED:
        return _reject(_NOT_ISSUED_MESSAGE, payments_url, actor_id, "warning")
    active = active_lines(invoice_rows_for_snapshot(locked))
    if _balance_of(active, payments) is None:
        return _reject(_BALANCE_BROKEN_MESSAGE, payments_url, actor_id, "warning")

    moment = _write_moment()
    number = receipt.receipt_number
    try:
        before = build_payment_snapshot(locked, active, payments)
        reversal = PaymentTransaction(
            public_id=str(uuid.uuid4()),
            invoice_id=locked.id,
            kind=_REVERSAL,
            method=original.method,
            status=_CONFIRMED,
            currency_code=original.currency_code,
            amount=original.amount,
            bank_transfer_reference=None,
            bank_transfer_date=None,
            recorded_at=moment,
            recorded_by_id=actor_id,
            confirmed_at=moment,
            confirmed_by_id=actor_id,
            rejected_at=None,
            rejected_by_id=None,
            rejection_reason=None,
            reversal_of_payment_transaction_id=original.id,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
        db.session.add(reversal)
        db.session.flush()
        everything = payments + [reversal]
        after_reversal = build_payment_snapshot(locked, active, everything, payment=reversal)
        record_payment_event(
            invoice=locked,
            actor=locks.chain.actor,
            kind=REVERSED,
            payment=reversal,
            receipt=None,
            before_snapshot=before,
            after_snapshot=after_reversal,
            reason=reason,
            moment=moment,
        )
        receipt_before = build_payment_snapshot(
            locked, active, everything, payment=original, receipt=receipt
        )
        receipt.status = _RECEIPT_VOIDED
        receipt.voided_at = moment
        receipt.voided_by_id = actor_id
        receipt.void_reason = reason
        receipt.version = receipt.version + 1
        receipt.updated_at = moment
        record_payment_event(
            invoice=locked,
            actor=locks.chain.actor,
            kind=RECEIPT_VOIDED,
            payment=original,
            receipt=receipt,
            before_snapshot=receipt_before,
            after_snapshot=build_payment_snapshot(
                locked, active, everything, payment=original, receipt=receipt
            ),
            reason=reason,
            moment=moment,
        )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)

    flash(_REVERSED_OK_MESSAGE.format(number=number), "success")
    return redirect(payments_url)


# ======================================================================
# Receipts and the overview
# ======================================================================


@admin_bp.get(_INVOICE + "/receipts/<receipt_public_id>")
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_receipt_detail(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    receipt_public_id,
):
    """One receipt, rendered from its permanent document, with its void
    record when its collection was reversed."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    found = invoice_receipt(invoice.id, receipt_public_id)
    if found is None:
        abort(404)
    receipt, payment = found
    return render_template(
        "admin/receipts/detail.html",
        receipt=build_receipt_view(
            receipt, payment, account_names([receipt.voided_by_id]), _tz_name()
        ),
        detail_url=_detail_url(context, invoice.public_id),
        payments_url=_payments_url(context, invoice.public_id),
        tz_name=_tz_name(),
        **_page_context(context),
    )


@admin_bp.get("/payments")
@roles_required(_ADMINISTRATOR)
@_financial_response
def payments_overview():
    """Every transaction, newest first, 20 per page with no ``COUNT``, filtered
    only by a known status and a known method; anything else is dropped. One
    query per page whatever it holds."""
    page = normalize_page(request.args.get("page"))
    status = normalize_payment_status_filter(request.args.get("status"))
    method = normalize_payment_method_filter(request.args.get("method"))
    rows, has_next = payments_overview_page(page, status, method)
    if not rows and page > 1:
        page = 1
        rows, has_next = payments_overview_page(page, status, method)
    entries = build_overview_view(rows, _tz_name())
    for entry in entries:
        ids = {
            "group_public_id": entry["group_public_id"],
            "enrollment_public_id": entry["enrollment_public_id"],
            "assignment_public_id": entry["assignment_public_id"],
            "invoice_public_id": entry["invoice_public_id"],
        }
        entry["payments_url"] = url_for("admin.invoice_payments", **ids)
        entry["receipt_url"] = (
            url_for(
                "admin.invoice_receipt_detail",
                receipt_public_id=entry["receipt_public_id"],
                **ids,
            )
            if entry["receipt_public_id"]
            else None
        )
    return render_template(
        "admin/payments/overview.html",
        payments=entries,
        status_labels=STATUS_LABELS,
        method_labels=METHOD_LABELS,
        filter_status=status,
        filter_method=method,
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        page_size=PAGE_SIZE,
        tz_name=_tz_name(),
        currency_code=money.CURRENCY_CODE,
    )
