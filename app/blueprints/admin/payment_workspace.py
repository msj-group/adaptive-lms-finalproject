"""Administrator Payments workspace actions (Phase 5 / M10).

Three URL rules beside the global payment list (``GET /admin/payments``)::

    GET       /admin/payments/new[?q=][&page=]      choose a payable invoice
    GET|POST  /admin/payments/<pp>/edit             correct a manual payment
    GET|POST  /admin/payments/<pp>/delete           delete it with its receipt

**New payment** lists the live issued invoices with an outstanding balance
and links each to the existing M05 cash and bank-transfer forms, which keep
every rule: cash is confirmed as it is recorded and receives its receipt, a
bank transfer is recorded pending and confirmed later, and no payment exceeds
the live outstanding balance. There is no standalone receipt anywhere.

**Edit** corrects a live manual collection (``cash`` or ``bank_transfer``,
``pending`` or ``confirmed``, not reversed) of an issued invoice with no
active payment intent. A recorded payment never changes in place: in one
transaction the collection and its receipt become deleted tombstones --
``payment_replaced`` and ``receipt_deleted``, which name the replacement --
and a corrected collection of the same method is recorded in their place with
M05's own events. A confirmed original is replaced by a confirmed collection
with a **new** receipt number (a bank transfer is recorded and confirmed in
the same transaction); a pending original by a pending one. The old receipt's
document is never overwritten; it stays in Deleted Records. The corrected
amount may not exceed the balance outstanding without the original.

**Delete** deletes the same kind of payment -- and its receipt -- as
tombstones, with a required reason and confirmation. An online collection, a
reversal and a rejected transfer are deleted only with their whole invoice
(the Invoices workspace).

Every write takes M05's lock chain with M07's intents and the payment's
receipt, re-proves the actor, nesting, token, lifecycle, intents and balance
against the locked rows, and commits once; a confirmed replacement also takes
the year's receipt sequence lock, last. POST-only, CSRF-protected, signed
stale-state tokens (``app/services/financial_workspace_tokens.py``). Only an
active Administrator reaches these pages; every response is
``private, no-store`` with ``Vary: Cookie``.
"""

import uuid

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from wtforms import StringField, TextAreaField
from wtforms.validators import ValidationError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plans import _financial_response, _reject, _tz_name, _write_moment
from app.blueprints.admin.invoice_workspace import DeletionReasonForm
from app.blueprints.admin.invoices import (
    _INTEGRITY_MESSAGE,
    _STATE_FIELD,
    _center_year,
    _context_or_404,
    _hierarchy_moved,
    _locked_invoice_or_404,
)
from app.blueprints.admin.payments import (
    _BALANCE_BROKEN_MESSAGE,
    _DATE_MESSAGES,
    _RECEIPTS_EXHAUSTED_MESSAGE,
    _REFERENCE_MESSAGES,
    _amount_error,
    _balance_of,
    _issue_receipt,
    _local_date,
    _lock,
    _locked_payment_or_404,
    _overpayment_message,
    _payable_block,
    _pre_lock_state,
)
from app.extensions import db
from app.models import (
    BANK_TRANSFER_REFERENCE_MAX_LENGTH,
    INVOICE_AUDIT_REASON_MAX_LENGTH,
    InvoiceStatus,
    PaymentMethod,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    UserRole,
)
from app.models.fee_plan import TEXT_CONTROL, TEXT_MISSING, TEXT_TOO_LONG
from app.models.payment_audit_event import normalize_audit_reason
from app.models.payment_transaction import (
    EARLIEST_BANK_TRANSFER_DATE,
    normalize_bank_transfer_reference,
    parse_bank_transfer_date,
)
from app.security.decorators import roles_required
from app.services import financial_workspace_tokens as tokens
from app.services import invoice_register_queries as register
from app.services import money
from app.services.fee_plan_queries import normalize_page
from app.services.financial_deletions import (
    BLOCK_DELETED,
    BLOCK_INTENT_ACTIVE,
    BLOCK_NOT_ISSUED,
    BLOCK_NOT_MANUAL,
    BLOCK_NOT_OPEN,
    BLOCK_REVERSED,
    delete_payments,
    payment_change_block,
    payment_locator,
)
from app.services.invoice_queries import active_lines, assignment_invoice
from app.services.invoice_transactions import invoice_rows_for_snapshot
from app.services.payment_audit import (
    BANK_CONFIRMED,
    BANK_RECORDED,
    CASH_RECORDED,
    build_payment_snapshot,
    record_payment_event,
)
from app.services.payment_intent_queries import invoice_intent_rows
from app.services.payment_queries import (
    METHOD_LABELS,
    STATUS_LABELS,
    invoice_payment,
    receipt_for_payment,
)
from app.services.payment_transactions import (
    allocate_receipt_number,
    lock_receipt_number_sequence,
    locked_invoice_payments,
    locked_payment_chain_intents,
    receipt_number_taken,
)
from app.services.schedule_occurrences import to_app_local

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_ISSUED = InvoiceStatus.ISSUED.value
_COLLECTION = PaymentTransactionKind.COLLECTION.value
_CASH = PaymentMethod.CASH.value
_BANK_TRANSFER = PaymentMethod.BANK_TRANSFER.value
_PENDING = PaymentTransactionStatus.PENDING.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value


# ======================================================================
# Administrator-facing sentences
# ======================================================================

_STALE_MESSAGE = (
    "This payment or its invoice changed after this page was opened, or the page has expired. "
    "Nothing was saved. Please review the current state and try again."
)
_BLOCK_MESSAGES = {
    BLOCK_DELETED: "This payment has already been deleted. Nothing was changed.",
    BLOCK_NOT_ISSUED: "This payment's invoice is not issued. Nothing was changed.",
    BLOCK_NOT_MANUAL: (
        "Only a cash or bank-transfer collection can be edited or deleted on its own. An online "
        "payment or a reversal is removed only by deleting its whole invoice. Nothing was changed."
    ),
    BLOCK_NOT_OPEN: (
        "A rejected bank transfer counts for nothing and is kept as history; it is removed only "
        "by deleting its whole invoice. Nothing was changed."
    ),
    BLOCK_REVERSED: (
        "This payment has been reversed, so it can no longer be edited or deleted on its own. "
        "Nothing was changed."
    ),
    BLOCK_INTENT_ACTIVE: (
        "This invoice has an active online payment intent, so its payments cannot be edited or "
        "deleted until the intent is cancelled, fails or is confirmed. Nothing was changed."
    ),
}
_CONFIRM_EDIT_MESSAGE = "Please tick the confirmation box before saving the corrected payment."
_CONFIRM_DELETE_MESSAGE = "Please tick the confirmation box before deleting this payment."
_NO_CHANGES_MESSAGE = "Nothing was changed, so nothing was saved."
_RECEIPT_MISSING_MESSAGE = (
    "This confirmed payment has no live receipt, so it cannot be corrected here. Nothing was "
    "changed."
)
_EDITED_MESSAGE = (
    "Payment corrected. The original payment{receipt} was deleted and is kept in Deleted Records; "
    "the corrected {method} payment of {amount} {currency} was recorded{new_receipt}."
)
_DELETED_MESSAGE = (
    "Payment deleted{receipt}. It is kept in Deleted Records and no longer counts in any balance "
    "or report."
)
_REASON_MESSAGES = {
    TEXT_MISSING: "Give the reason for this correction.",
    TEXT_CONTROL: "The reason may only contain ordinary text and line breaks.",
    TEXT_TOO_LONG: f"The reason must be at most {INVOICE_AUDIT_REASON_MAX_LENGTH} characters.",
}


class PaymentEditForm(FlaskForm):
    """The corrected amount -- and, for a bank transfer, reference and date --
    with the required reason. The method, status and currency are not
    submitted: they follow the original."""

    amount = StringField(f"Amount ({money.CURRENCY_CODE})")
    reference = StringField("Transfer reference")
    transfer_date = StringField("Transfer date")
    reason = TextAreaField("Reason for this correction")

    def __init__(self, *args, bank=False, latest_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.bank = bank
        self.latest_date = latest_date
        self.parsed_amount = None
        self.normalized_reference = None
        self.parsed_date = None
        self.normalized_reason = None

    def validate_amount(self, field):
        self.parsed_amount = _amount_error(field)

    def validate_reference(self, field):
        if not self.bank:
            return
        value, error = normalize_bank_transfer_reference(field.data)
        if error is not None:
            raise ValidationError(_REFERENCE_MESSAGES[error])
        self.normalized_reference = value

    def validate_transfer_date(self, field):
        if not self.bank:
            return
        value, error = parse_bank_transfer_date(field.data, self.latest_date)
        if error is not None:
            raise ValidationError(_DATE_MESSAGES[error])
        self.parsed_date = value

    def validate_reason(self, field):
        value, error = normalize_audit_reason(field.data)
        if error is not None:
            raise ValidationError(_REASON_MESSAGES[error])
        self.normalized_reason = value


# ======================================================================
# Shared handling
# ======================================================================


def _resolve(payment_public_id):
    """``(context, invoice, payment)`` for a live payment of a live invoice,
    through the existing nested lookups -- or a 404."""
    located = payment_locator(payment_public_id)
    if located is None:
        abort(404)
    context = _context_or_404(
        located.group_public_id, located.enrollment_public_id, located.assignment_public_id
    )
    invoice = assignment_invoice(context.assignment_id, located.invoice_public_id)
    if invoice is None:
        abort(404)
    payment = invoice_payment(invoice.id, payment_public_id)
    if payment is None:
        abort(404)
    return context, invoice, payment


def _payments_url(context, invoice):
    return url_for(
        "admin.invoice_payments",
        group_public_id=context.group_public_id,
        enrollment_public_id=context.enrollment_public_id,
        assignment_public_id=context.assignment_public_id,
        invoice_public_id=invoice.public_id,
    )


def _state(actor_public_id, invoice, payment, rows):
    return {
        "actor_public_id": actor_public_id,
        "invoice_public_id": invoice.public_id,
        "invoice_version": invoice.version,
        "payment_public_id": payment.public_id,
        "payment_version": payment.version,
        "payment_state": tokens.payment_state(rows),
    }


def _available(balance, payment):
    """The most a corrected collection may record: the balance outstanding
    without the original -- a confirmed original's amount is freed."""
    if balance is None:
        return None
    return balance.outstanding + (payment.amount if payment.status == _CONFIRMED else 0)


def _payment_view(payment, receipt, invoice, context):
    return {
        "public_id": payment.public_id,
        "method": payment.method,
        "method_label": METHOD_LABELS.get(payment.method, payment.method),
        "status_label": STATUS_LABELS.get(payment.status, payment.status),
        "is_bank": payment.method == _BANK_TRANSFER,
        "is_confirmed": payment.status == _CONFIRMED,
        "amount_text": money.format_amount(payment.amount),
        "recorded_local": to_app_local(_tz_name(), payment.recorded_at),
        "receipt_number": None if receipt is None else receipt.receipt_number,
        "invoice_number": invoice.invoice_number,
        "student_name": context.student_name,
        "group_name": context.group_name,
    }


def _live_receipt(payment):
    receipt = receipt_for_payment(payment.id)
    return receipt if receipt is not None and receipt.deleted_at is None else None


# ======================================================================
# New payment
# ======================================================================


@admin_bp.get("/payments/new")
@roles_required(_ADMINISTRATOR)
@_financial_response
def payment_workspace_new():
    """The live issued invoices with an outstanding balance, each linked to
    the existing cash and bank-transfer forms."""
    _status, _payment, search = register.normalize_filters(None, None, request.args.get("q"))
    rows, facts, total, page = register.register_page(
        _ISSUED, register.OUTSTANDING, search, normalize_page(request.args.get("page"))
    )
    active, reconciliation = register.attention(row.id for row in rows)
    invoices = register.build_register_view(rows, facts, active, reconciliation)
    for invoice, row in zip(invoices, rows):
        ids = {
            "group_public_id": invoice["group_public_id"],
            "enrollment_public_id": invoice["enrollment_public_id"],
            "assignment_public_id": invoice["assignment_public_id"],
            "invoice_public_id": invoice["public_id"],
        }
        payable = row.id not in active
        invoice["cash_url"] = url_for("admin.invoice_payment_cash", **ids) if payable else None
        invoice["bank_url"] = (
            url_for("admin.invoice_payment_bank_transfer", **ids) if payable else None
        )
        invoice["payments_url"] = url_for("admin.invoice_payments", **ids)
    filters = {"q": search} if search else {}
    first = (page - 1) * register.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * register.PAGE_SIZE + len(rows)
    return render_template(
        "admin/payments/new.html",
        invoices=invoices,
        search=search,
        allocation_note=register.ALLOCATION_NOTE,
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": url_for("admin.payment_workspace_new", page=page - 1, **filters)
            if page > 1
            else None,
            "next_url": url_for("admin.payment_workspace_new", page=page + 1, **filters)
            if last < total
            else None,
        },
        overview_url=url_for("admin.payments_overview"),
        currency_code=money.CURRENCY_CODE,
    )


# ======================================================================
# Edit payment
# ======================================================================


def _render_edit(context, invoice, payment, receipt, form, rows, available, confirm_error=None):
    return render_template(
        "admin/payments/edit.html",
        form=form,
        payment=_payment_view(payment, receipt, invoice, context),
        available_text=None if available is None else money.format_amount(available),
        confirm_error=confirm_error,
        action_url=url_for("admin.payment_workspace_edit", payment_public_id=payment.public_id),
        payments_url=_payments_url(context, invoice),
        overview_url=url_for("admin.payments_overview"),
        state_field=_STATE_FIELD,
        state_token=tokens.make_token(
            tokens.PURPOSE_PAYMENT_EDIT, **_state(current_user.public_id, invoice, payment, rows)
        ),
        min_amount=money.format_amount(money.MIN_AMOUNT),
        amount_scale=money.AMOUNT_SCALE,
        reference_max=BANK_TRANSFER_REFERENCE_MAX_LENGTH,
        reason_max=INVOICE_AUDIT_REASON_MAX_LENGTH,
        latest_date=form.latest_date.isoformat(),
        earliest_date=EARLIEST_BANK_TRANSFER_DATE.isoformat(),
        currency_code=money.CURRENCY_CODE,
    )


def _new_collection(invoice, original, amount, reference, transfer_date, moment, actor_id):
    """The corrected collection, recorded as the original's method records:
    cash confirmed at once, a bank transfer pending (confirmed by the caller
    when the original was confirmed)."""
    cash = original.method == _CASH
    return PaymentTransaction(
        public_id=str(uuid.uuid4()),
        invoice_id=invoice.id,
        kind=_COLLECTION,
        method=original.method,
        status=_CONFIRMED if cash else _PENDING,
        currency_code=money.CURRENCY_CODE,
        amount=amount,
        bank_transfer_reference=None if cash else reference,
        bank_transfer_date=None if cash else transfer_date,
        recorded_at=moment,
        recorded_by_id=actor_id,
        confirmed_at=moment if cash else None,
        confirmed_by_id=actor_id if cash else None,
        rejected_at=None,
        rejected_by_id=None,
        rejection_reason=None,
        reversal_of_payment_transaction_id=None,
        version=1,
        created_at=moment,
        updated_at=moment,
    )


@admin_bp.route("/payments/<payment_public_id>/edit", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def payment_workspace_edit(payment_public_id):
    """GET: the correction form. POST: replace the payment -- and its receipt
    -- with a corrected one, in one transaction."""
    context, invoice, payment = _resolve(payment_public_id)
    payments_url = _payments_url(context, invoice)
    lines, active, rows, balance = _pre_lock_state(invoice)
    block = payment_change_block(invoice, payment, rows, invoice_intent_rows(invoice.id))
    receipt = _live_receipt(payment)
    bank = payment.method == _BANK_TRANSFER
    form = PaymentEditForm(
        formdata=request.form if request.method == "POST" else None,
        data={
            "amount": money.amount_input_text(payment.amount),
            "reference": payment.bank_transfer_reference or "",
            "transfer_date": "" if payment.bank_transfer_date is None
            else payment.bank_transfer_date.isoformat(),
        },
        bank=bank,
        latest_date=_local_date(_write_moment()),
    )
    payable = _payable_block(invoice, lines, active, rows, balance)
    available = _available(balance, payment)
    if request.method == "GET":
        if block is not None or payable is not None:
            flash(_BLOCK_MESSAGES[block] if block else payable, "warning")
            return redirect(payments_url)
        return _render_edit(context, invoice, payment, receipt, form, rows, available)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_PAYMENT_EDIT, **_state(actor_public_id, invoice, payment, rows)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    if block is not None:
        return _reject(_BLOCK_MESSAGES[block], payments_url, actor_id, "warning")
    if payable is not None:
        return _reject(payable, payments_url, actor_id, "warning")
    confirmed = request.form.get("confirm") == "yes"
    if not form.validate_on_submit() or not confirmed:
        return _render_edit(
            context, invoice, payment, receipt, form, rows, available,
            None if confirmed else _CONFIRM_EDIT_MESSAGE,
        )
    amount, reference, transfer_date = (
        form.parsed_amount,
        form.normalized_reference,
        form.parsed_date,
    )
    if (amount, reference, transfer_date) == (
        payment.amount,
        payment.bank_transfer_reference,
        payment.bank_transfer_date,
    ):
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(payments_url)
    if amount > available:
        form.amount.errors.append(
            _overpayment_message(balance._replace(outstanding=available))
        )
        return _render_edit(context, invoice, payment, receipt, form, rows, available)
    reason = form.normalized_reason
    invoice_id, payment_id = invoice.id, payment.id
    form_url = url_for("admin.payment_workspace_edit", payment_public_id=payment_public_id)

    locks = _lock(
        context,
        actor_id,
        invoice_id,
        payment_id=payment_id,
        include_invoice_payments=True,
        include_invoice_intents=True,
        include_receipt=True,
    )
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice.public_id)
    target = _locked_payment_or_404(locks, payment_id, payment_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    payments = locked_invoice_payments(locks)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_PAYMENT_EDIT, **_state(actor_public_id, locked, target, payments)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    block = payment_change_block(locked, target, payments, locked_payment_chain_intents(locks))
    if block is not None:
        return _reject(_BLOCK_MESSAGES[block], payments_url, actor_id, "warning")
    item_rows = invoice_rows_for_snapshot(locked)
    active = active_lines(item_rows)
    balance = _balance_of(active, payments)
    payable = _payable_block(locked, item_rows, active, payments, balance)
    if payable is not None:
        return _reject(payable, payments_url, actor_id, "warning")
    available = _available(balance, target)
    if amount > available:
        return _reject(
            _overpayment_message(balance._replace(outstanding=available)),
            form_url,
            actor_id,
            "warning",
        )
    old_receipt = locks.receipt
    if old_receipt is not None and (
        old_receipt.deleted_at is not None or old_receipt.payment_transaction_id != target.id
    ):
        old_receipt = None
    confirmed_original = target.status == _CONFIRMED
    if confirmed_original and old_receipt is None:
        return _reject(_RECEIPT_MISSING_MESSAGE, payments_url, actor_id, "warning")

    number = None
    if confirmed_original or target.method == _CASH:
        provisional = _write_moment()
        year = _center_year(provisional)
        try:
            sequence = lock_receipt_number_sequence(year, provisional)
        except (IntegrityError, ValueError):
            return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)
        moment = _write_moment()
        if _center_year(moment) != year:
            return _reject(_STALE_MESSAGE, payments_url, actor_id)
        number = allocate_receipt_number(sequence, moment)
        if number is None:
            return _reject(_RECEIPTS_EXHAUSTED_MESSAGE, payments_url, actor_id, "warning")
    else:
        moment = _write_moment()
    if transfer_date is not None and transfer_date > _local_date(moment):
        return _reject(_STALE_MESSAGE, form_url, actor_id)

    actor = locks.chain.actor
    try:
        if number is not None and receipt_number_taken(number):
            return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)
        new = _new_collection(locked, target, amount, reference, transfer_date, moment, actor_id)
        db.session.add(new)
        db.session.flush()
        delete_payments(
            invoice=locked,
            actor=actor,
            active_items=active,
            payments=payments,
            targets=[target],
            receipts={} if old_receipt is None else {target.id: old_receipt},
            reason=reason,
            moment=moment,
            replacement=new,
        )
        rows_now = payments + [new]
        receipt_public_id = None
        if target.method == _CASH:
            receipt_public_id = _issue_receipt(
                context, locks, locked, active, rows_now, new, number, moment,
                build_payment_snapshot(locked, active, payments), CASH_RECORDED,
            )
        else:
            recorded_after = build_payment_snapshot(locked, active, rows_now, payment=new)
            record_payment_event(
                invoice=locked,
                actor=actor,
                kind=BANK_RECORDED,
                payment=new,
                receipt=None,
                before_snapshot=build_payment_snapshot(locked, active, payments),
                after_snapshot=recorded_after,
                reason=None,
                moment=moment,
            )
            if confirmed_original:
                new.status = _CONFIRMED
                new.confirmed_at = moment
                new.confirmed_by_id = actor_id
                new.version = new.version + 1
                new.updated_at = moment
                db.session.flush()
                receipt_public_id = _issue_receipt(
                    context, locks, locked, active, rows_now, new, number, moment,
                    recorded_after, BANK_CONFIRMED,
                )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)

    flash(
        _EDITED_MESSAGE.format(
            receipt="" if old_receipt is None else f" and its receipt {old_receipt.receipt_number}",
            method=METHOD_LABELS.get(target.method, target.method).lower(),
            amount=money.format_amount(amount),
            currency=money.CURRENCY_CODE,
            new_receipt=f" with receipt {number}" if receipt_public_id else " as pending",
        ),
        "success",
    )
    if receipt_public_id is not None:
        return redirect(
            url_for(
                "admin.invoice_receipt_detail",
                group_public_id=context.group_public_id,
                enrollment_public_id=context.enrollment_public_id,
                assignment_public_id=context.assignment_public_id,
                invoice_public_id=invoice.public_id,
                receipt_public_id=receipt_public_id,
            )
        )
    return redirect(payments_url)


# ======================================================================
# Delete payment
# ======================================================================


def _render_delete(context, invoice, payment, receipt, form, rows, confirm_error=None):
    return render_template(
        "admin/payments/delete.html",
        form=form,
        payment=_payment_view(payment, receipt, invoice, context),
        confirm_error=confirm_error,
        action_url=url_for("admin.payment_workspace_delete", payment_public_id=payment.public_id),
        payments_url=_payments_url(context, invoice),
        state_field=_STATE_FIELD,
        state_token=tokens.make_token(
            tokens.PURPOSE_PAYMENT_DELETE,
            **_state(current_user.public_id, invoice, payment, rows),
        ),
        reason_max=INVOICE_AUDIT_REASON_MAX_LENGTH,
        currency_code=money.CURRENCY_CODE,
    )


@admin_bp.route("/payments/<payment_public_id>/delete", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def payment_workspace_delete(payment_public_id):
    """GET: the payment and its receipt, and the reason form. POST: delete
    both as tombstones, in one transaction."""
    context, invoice, payment = _resolve(payment_public_id)
    payments_url = _payments_url(context, invoice)
    lines, active, rows, balance = _pre_lock_state(invoice)
    block = payment_change_block(invoice, payment, rows, invoice_intent_rows(invoice.id))
    receipt = _live_receipt(payment)
    form = DeletionReasonForm(formdata=request.form if request.method == "POST" else None)
    if request.method == "GET":
        if block is not None:
            flash(_BLOCK_MESSAGES[block], "warning")
            return redirect(payments_url)
        return _render_delete(context, invoice, payment, receipt, form, rows)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_PAYMENT_DELETE, **_state(actor_public_id, invoice, payment, rows)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    if block is not None:
        return _reject(_BLOCK_MESSAGES[block], payments_url, actor_id, "warning")
    confirmed = request.form.get("confirm") == "yes"
    if not form.validate_on_submit() or not confirmed:
        return _render_delete(
            context, invoice, payment, receipt, form, rows,
            None if confirmed else _CONFIRM_DELETE_MESSAGE,
        )
    reason = form.normalized_reason
    invoice_id, payment_id = invoice.id, payment.id

    locks = _lock(
        context,
        actor_id,
        invoice_id,
        payment_id=payment_id,
        include_invoice_payments=True,
        include_invoice_intents=True,
        include_receipt=True,
    )
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice.public_id)
    target = _locked_payment_or_404(locks, payment_id, payment_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    payments = locked_invoice_payments(locks)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_PAYMENT_DELETE, **_state(actor_public_id, locked, target, payments)
    ):
        return _reject(_STALE_MESSAGE, payments_url, actor_id)
    block = payment_change_block(locked, target, payments, locked_payment_chain_intents(locks))
    if block is not None:
        return _reject(_BLOCK_MESSAGES[block], payments_url, actor_id, "warning")
    item_rows = invoice_rows_for_snapshot(locked)
    active = active_lines(item_rows)
    if _balance_of(active, payments) is None:
        return _reject(_BALANCE_BROKEN_MESSAGE, payments_url, actor_id, "warning")
    old_receipt = locks.receipt
    if old_receipt is not None and (
        old_receipt.deleted_at is not None or old_receipt.payment_transaction_id != target.id
    ):
        old_receipt = None

    moment = _write_moment()
    try:
        delete_payments(
            invoice=locked,
            actor=locks.chain.actor,
            active_items=active,
            payments=payments,
            targets=[target],
            receipts={} if old_receipt is None else {target.id: old_receipt},
            reason=reason,
            moment=moment,
        )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, payments_url, actor_id)

    flash(
        _DELETED_MESSAGE.format(
            receipt="" if old_receipt is None else f" with its receipt {old_receipt.receipt_number}"
        ),
        "success",
    )
    return redirect(payments_url)
