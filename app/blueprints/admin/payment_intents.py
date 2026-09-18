"""Administrator Mock/Sandbox payment intents (Phase 5 / M06).

Eight URL rules, addressed only by public identifiers. With ``<invoice>`` =
``/admin/groups/<gp>/enrollments/<ep>/fee-assignments/<ap>/invoices/<ip>``
and ``<intent>`` = ``<invoice>/payment-intents/<xp>``::

    GET       <invoice>/payment-intents          one invoice's intents
    GET|POST  <invoice>/payment-intents/new      confirm, then create a Mock intent
    GET       <intent>                           one intent
    GET|POST  <intent>/checkout                  the Mock/Sandbox checkout (sandbox only)
    POST      <intent>/return                    the browser return: record the provider's result
    GET       <intent>/result                    the safe result page
    GET|POST  <intent>/cancel                    confirm, then cancel a pending intent
    GET       /admin/payment-intents             every intent, filtered and paged

**An intent is not a payment.** Creating one asks the provider to collect the
invoice's exact outstanding balance; nothing here creates a payment
transaction, a receipt or an audit event, or changes an invoice's lines,
amounts, balance, status or number. A provider's reported success is recorded
as ``provider_succeeded`` for sandbox testing only: **no payment is confirmed
until Phase 5 / M07 verifies a signed webhook.**

**Mock/Sandbox only.** The only provider is the in-process mock
(``app/services/mock_payment_provider.py``), enabled only when
``PAYMENT_PROVIDER_MODE=mock`` in development or testing. Otherwise the
creation, checkout, return and cancel routes are a plain 404, and the
read-only pages say online payments are disabled. The checkout collects no
card number, CVV/CVC, PIN, bank credential, account number, proof or other
credential: it offers exactly three simulated outcomes, and choosing one
changes only the mock provider's own ledger.

**The browser return never trusts the browser.** It is POST-only with CSRF and
the intent's signed, expiring checkout context; it reads the provider's result
only through ``get_payment_status()`` under the lock chain, ignores any status,
outcome or amount the request carries, and records only ``provider_succeeded``
or ``provider_failed`` for a still-pending intent whose provider reference,
amount and currency the provider's report matches. A GET, a forged or replayed
form, a context for another intent, a stale or expired context, or a provider
that has not decided changes nothing.

**Every mutation** is POST-only and CSRF-protected, carries a purpose-specific
signed token (``app/services/payment_intent_tokens.py``) minted by the GET
page that shows exactly what will change, runs the lock chain in
``app/services/payment_intent_transactions.py``, and re-proves the actor, the
nesting, the academic chain the chain locked, the provider mode, the token,
the invoice, its manual payments, its intents, the balance and every
applicable rule against the locked rows before the provider is called. An
``IntegrityError`` is rolled back first, the actor is re-authorized from
current state, and one generic sentence is shown.

A URL naming anything that does not nest inside the link before it is a plain
404. Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``.
"""

import uuid

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plans import _financial_response, _reject, _tz_name, _write_moment
from app.blueprints.admin.invoices import (
    _BASE,
    _INTEGRITY_MESSAGE,
    _STATE_FIELD,
    _context_or_404,
    _detail_url,
    _hierarchy_moved,
    _invoice_or_404,
    _locked_invoice_or_404,
    _page_context,
)
from app.extensions import db
from app.models import (
    MAX_INVOICE_ITEM_ROWS,
    MAX_INVOICE_PAYMENT_INTENTS,
    InvoiceStatus,
    PaymentIntent,
    PaymentIntentStatus,
    UserRole,
)
from app.models.payment_intent import PROVIDER_REFERENCE_MAX_LENGTH
from app.security.decorators import roles_required
from app.services import money
from app.services import payment_intent_tokens as tokens
from app.services.fee_plan_queries import normalize_page
from app.services.invoice_queries import STATUS_LABELS as INVOICE_STATUS_LABELS
from app.services.invoice_queries import active_lines, invoice_lines
from app.services.invoice_transactions import invoice_items_valid, invoice_rows_for_snapshot
from app.services.mock_payment_provider import (
    OUTCOME_CANCELLATION,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SANDBOX_OUTCOMES,
)
from app.services.payment_intent_queries import (
    PAGE_SIZE,
    STATUS_LABELS,
    build_intent_history_view,
    build_intent_view,
    build_overview_view,
    intent_account_ids,
    intent_history_page,
    intents_overview_page,
    invoice_intent,
    invoice_intent_rows,
    normalize_intent_status_filter,
)
from app.services.payment_intent_transactions import (
    active_intents,
    idempotency_key_taken,
    intent_nesting_broken,
    intent_rows_over_bound,
    invoice_intent_by_idempotency_key,
    lock_payment_intent_chain,
    locked_invoice_intents,
    locked_invoice_payments,
    manual_payment_freezes,
    provider_reference_taken,
)
from app.services.payment_providers import PaymentProviderError, ProviderPaymentStatus
from app.services.payment_queries import account_names, build_balance_view, invoice_payment_rows
from app.services.payment_transactions import payment_balance, payment_rows_over_bound

_ISSUED = InvoiceStatus.ISSUED.value
_PENDING = PaymentIntentStatus.PENDING.value
_SUCCEEDED = PaymentIntentStatus.PROVIDER_SUCCEEDED.value
_FAILED = PaymentIntentStatus.PROVIDER_FAILED.value
_CANCELLED = PaymentIntentStatus.CANCELLED.value
_REPORTED_PENDING = ProviderPaymentStatus.PENDING.value
_REPORTED_SUCCEEDED = ProviderPaymentStatus.SUCCEEDED.value
_REPORTED_FAILED = ProviderPaymentStatus.FAILED.value
_REPORTED_CANCELLED = ProviderPaymentStatus.CANCELLED.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

#: The hidden form field the signed checkout context travels in.
_CHECKOUT_FIELD = "checkout_context"

_INVOICE = _BASE + "/<invoice_public_id>"
_INTENTS = _INVOICE + "/payment-intents"
_ONE = _INTENTS + "/<intent_public_id>"

#: The two sentences the browser return's result page states prominently.
SANDBOX_RESULT_NOTICE = (
    "Provider result recorded for sandbox testing only.",
    "No payment is confirmed until M07 verifies a signed webhook.",
)

#: What the checkout page offers, in order: value, button text.
_OUTCOME_CHOICES = (
    (OUTCOME_SUCCESS, "Simulate success"),
    (OUTCOME_FAILURE, "Simulate failure"),
    (OUTCOME_CANCELLATION, "Simulate cancellation"),
)


# ======================================================================
# Administrator-facing sentences, declared once each
# ======================================================================

_STALE_MESSAGE = (
    "This invoice or its payment intents changed after this page was opened, or the page has "
    "expired. Nothing was saved. Please reload, review the current state, and try again."
)
_MOCK_DISABLED_MESSAGE = (
    "The Mock/Sandbox payment provider is not enabled in this environment. Nothing was changed."
)
_NOT_ISSUED_MESSAGE = (
    "A payment intent is created only for an issued invoice, and this invoice is not issued."
)
_ITEMS_INVALID_MESSAGE = (
    "This invoice's lines are not a valid charge, so no payment intent can be created for it."
)
_BALANCE_BROKEN_MESSAGE = (
    "This invoice's payment records do not describe a valid balance, so no payment intent can be "
    "created for it."
)
_MANUAL_PAYMENT_MESSAGE = (
    "This invoice has a pending or confirmed manual payment, so no online payment intent can be "
    "created for it."
)
_ACTIVE_INTENT_MESSAGE = (
    "This invoice already has an active payment intent, and only one may exist at a time. A "
    "pending intent must be cancelled before another is created."
)
_LIMIT_MESSAGE = (
    f"This invoice already holds {MAX_INVOICE_PAYMENT_INTENTS} payment intents, so no further "
    "intent can be created for it."
)
_SETTLED_MESSAGE = (
    "This invoice has no outstanding balance, so no payment intent can be created for it."
)
_BELOW_MINIMUM_MESSAGE = (
    "This invoice's outstanding balance is below the smallest amount a payment can record, so no "
    "payment intent can be created for it."
)
_ABOVE_MAXIMUM_MESSAGE = (
    "This invoice's outstanding balance is more than the largest single payment of "
    f"{money.format_amount(money.MAX_AMOUNT)} {money.CURRENCY_CODE}. A payment intent always "
    "covers the whole outstanding balance, so none can be created for it."
)
_PROVIDER_REFUSED_MESSAGE = (
    "The sandbox provider did not accept this request, or its answer did not match this payment "
    "intent. Nothing was changed."
)
_CREATE_CONFIRM_MESSAGE = (
    "Please tick the confirmation box before creating a sandbox payment intent."
)
_CANCEL_CONFIRM_MESSAGE = "Please tick the confirmation box before cancelling this payment intent."
_NOT_PENDING_MESSAGE = (
    "This payment intent is not pending, so it can no longer be cancelled, checked out or given a "
    "provider result. Nothing was changed."
)
_SUCCEEDED_NOT_CANCELLABLE_MESSAGE = (
    "The provider reported success for this payment intent, so it cannot be cancelled here. It "
    "stays active, and keeps the invoice frozen, until its payment is verified. Nothing was "
    "changed."
)
_CHECKOUT_INVALID_MESSAGE = (
    "This sandbox checkout is not valid for this payment intent, or it has expired. Nothing was "
    "changed. Open the checkout again from the payment intent."
)
_OUTCOME_INVALID_MESSAGE = (
    "Choose one of the simulated outcomes: success, failure or cancellation. Nothing was changed."
)
_SIMULATION_REFUSED_MESSAGE = (
    "The sandbox provider did not record that outcome: this sandbox payment already has a "
    "different result, or the sandbox no longer knows it (for example after a restart). Nothing "
    "was changed."
)
_SIMULATED_MESSAGE = (
    "The sandbox provider recorded a simulated {outcome}. No real payment was taken. Return to "
    "the LMS to record the provider's result."
)
_RESULT_PENDING_MESSAGE = (
    "The sandbox provider has not reported a result for this payment intent yet. Nothing was "
    "changed."
)
_RESULT_CANCELLED_MESSAGE = (
    "The sandbox provider reports that the checkout was cancelled. The payment intent is still "
    "pending; cancel it to release the invoice. Nothing was changed."
)
_CREATED_MESSAGE = (
    "Sandbox payment intent created for {amount} {currency}. No payment has been taken."
)
_ALREADY_CREATED_MESSAGE = (
    "This payment intent was already created by the same request. Nothing new was created."
)
_CANCELLED_OK_MESSAGE = "Payment intent cancelled. It is kept as history."

_OUTCOME_NAMES = {
    OUTCOME_SUCCESS: "success",
    OUTCOME_FAILURE: "failure",
    OUTCOME_CANCELLATION: "cancellation",
}


# ======================================================================
# Provider, URLs, state and shared handling
# ======================================================================


def _settings():
    """The provider settings ``create_app`` resolved at start-up."""
    return current_app.extensions["payment_provider"]


def _mock_or_404():
    """The sandbox-only routes do not exist unless the Mock/Sandbox provider
    is enabled in this environment."""
    if not _settings().mock_enabled:
        abort(404)


def _ids(context, invoice_public_id):
    return {
        "group_public_id": context.group_public_id,
        "enrollment_public_id": context.enrollment_public_id,
        "assignment_public_id": context.assignment_public_id,
        "invoice_public_id": invoice_public_id,
    }


def _intents_url(context, invoice_public_id):
    return url_for("admin.invoice_payment_intents", **_ids(context, invoice_public_id))


def _new_url(context, invoice_public_id):
    return url_for("admin.invoice_payment_intent_create", **_ids(context, invoice_public_id))


def _intent_url(endpoint, context, invoice_public_id, intent_public_id):
    return url_for(endpoint, intent_public_id=intent_public_id, **_ids(context, invoice_public_id))


def _detail(context, invoice_public_id, intent_public_id):
    return _intent_url(
        "admin.invoice_payment_intent_detail", context, invoice_public_id, intent_public_id
    )


def _payments_url(context, invoice_public_id):
    return url_for("admin.invoice_payments", **_ids(context, invoice_public_id))


def _create_state(actor_public_id, invoice, payments, intents):
    return {
        "actor_public_id": actor_public_id,
        "invoice_public_id": invoice.public_id,
        "invoice_version": invoice.version,
        "payment_state": tokens.payment_state(payments),
        "intent_state": tokens.intent_state(intents),
        "provider_mode": _settings().mode,
    }


def _cancel_state(actor_public_id, invoice, intent, intents):
    return {
        "actor_public_id": actor_public_id,
        "invoice_public_id": invoice.public_id,
        "invoice_version": invoice.version,
        "intent_public_id": intent.public_id,
        "intent_version": intent.version,
        "intent_status": intent.status,
        "intent_state": tokens.intent_state(intents),
        "provider_mode": _settings().mode,
    }


def _checkout_state(intent):
    return {
        "intent_public_id": intent.public_id,
        "intent_version": intent.version,
        "intent_status": intent.status,
        "provider_reference": intent.provider_reference,
    }


def _invoice_view(invoice):
    return {
        "public_id": invoice.public_id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status,
        "status_label": INVOICE_STATUS_LABELS.get(invoice.status, invoice.status),
        "is_issued": invoice.status == _ISSUED,
    }


def _intent_or_404(invoice, intent_public_id):
    intent = invoice_intent(invoice.id, intent_public_id)
    if intent is None:
        abort(404)
    return intent


def _balance_of(active, payments):
    """The balance of pre- or post-lock rows, or ``None`` when they cannot
    describe one (more rows than any balance may read, or a negative amount)."""
    return None if payment_rows_over_bound(payments) else payment_balance(active, payments)


def _pre_lock_state(invoice):
    """``(lines, active, payments, intents, balance)`` read before any lock --
    what a page shows and a friendly preview decides, never what a write
    decides."""
    lines = invoice_lines(invoice.id)
    active = active_lines(lines)
    payments = invoice_payment_rows(invoice.id)
    intents = invoice_intent_rows(invoice.id)
    return lines, active, payments, intents, _balance_of(active, payments)


def _create_block(invoice, lines, active, payments, intents, balance):
    """Why no payment intent may be created for `invoice` now, as a sentence,
    or ``None``. Used with pre-lock rows for a page and with locked rows for
    the decision."""
    if invoice.status != _ISSUED:
        return _NOT_ISSUED_MESSAGE
    if len(lines) > MAX_INVOICE_ITEM_ROWS or not invoice_items_valid(active):
        return _ITEMS_INVALID_MESSAGE
    if balance is None:
        return _BALANCE_BROKEN_MESSAGE
    if manual_payment_freezes(payments):
        return _MANUAL_PAYMENT_MESSAGE
    if active_intents(intents):
        return _ACTIVE_INTENT_MESSAGE
    if intent_rows_over_bound(intents) or len(intents) >= MAX_INVOICE_PAYMENT_INTENTS:
        return _LIMIT_MESSAGE
    if balance.outstanding == 0:
        return _SETTLED_MESSAGE
    if balance.outstanding < money.MIN_AMOUNT:
        return _BELOW_MINIMUM_MESSAGE
    if balance.outstanding > money.MAX_AMOUNT:
        return _ABOVE_MAXIMUM_MESSAGE
    return None


def _lock(context, actor_id, invoice_id, intent_id=None):
    """The M06 chain for `context`'s invoice. `context` is a plain row
    captured before the chain's reset, so it stays readable afterwards."""
    return lock_payment_intent_chain(
        context.group_public_id,
        context.academic_term_id,
        context.level_id,
        context.course_id,
        context.student_id,
        context.enrollment_id,
        actor_id,
        context.assignment_id,
        invoice_id,
        intent_id=intent_id,
    )


def _locked_intent_or_404(locks, intent_id, intent_public_id):
    if intent_nesting_broken(locks, intent_id, intent_public_id):
        db.session.rollback()
        abort(404)
    return locks.intent


def _is_reference(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= PROVIDER_REFERENCE_MAX_LENGTH
        and all("\x21" <= character <= "\x7e" for character in value)
    )


def _created_as_asked(reported, amount):
    """Whether the provider's answer to a creation is a pending payment of
    exactly `amount` in the application's currency, under a usable reference."""
    return (
        _is_reference(reported.reference)
        and reported.status == _REPORTED_PENDING
        and reported.amount == amount
        and reported.currency_code == money.CURRENCY_CODE
    )


def _replayed_intent(payload, actor_public_id, invoice):
    """The intent a verified create payload of this actor and invoice already
    created, or ``None``. A replayed or interrupted creation resolves to it."""
    if (
        payload is None
        or payload["actor_public_id"] != actor_public_id
        or payload["invoice_public_id"] != invoice.public_id
    ):
        return None
    return invoice_intent_by_idempotency_key(invoice.id, tokens.idempotency_key_for(payload))


def _names_of(rows):
    return account_names(intent_account_ids(rows))


def _common(context, invoice, balance):
    return {
        "invoice": _invoice_view(invoice),
        "balance": build_balance_view(balance),
        "detail_url": _detail_url(context, invoice.public_id),
        "payments_url": _payments_url(context, invoice.public_id),
        "intents_url": _intents_url(context, invoice.public_id),
        "overview_url": url_for("admin.payment_intents_overview"),
        "mock_enabled": _settings().mock_enabled,
        "tz_name": _tz_name(),
        **_page_context(context),
    }


def _invoice_balance(invoice):
    """The invoice's balance from a fresh pre-lock read, for display only."""
    active = active_lines(invoice_lines(invoice.id))
    return _balance_of(active, invoice_payment_rows(invoice.id))


# ======================================================================
# One invoice's intents
# ======================================================================


@admin_bp.get(_INTENTS)
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intents(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id
):
    """The invoice's payment intents, newest first, 20 per page with no
    ``COUNT``, and -- when the sandbox is enabled -- whether a new intent may
    be created. A fixed number of queries whatever the invoice holds."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    page = normalize_page(request.args.get("page"))
    rows, has_next = intent_history_page(invoice.id, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = intent_history_page(invoice.id, page)
    entries = build_intent_history_view(rows, _names_of(rows), _tz_name())
    for entry in entries:
        entry["url"] = _detail(context, invoice.public_id, entry["public_id"])
    lines, active, payments, intents, balance = _pre_lock_state(invoice)
    create_block = create_url = None
    if _settings().mock_enabled:
        create_block = _create_block(invoice, lines, active, payments, intents, balance)
        if create_block is None:
            create_url = _new_url(context, invoice.public_id)
    return render_template(
        "admin/payment_intents/history.html",
        intents=entries,
        create_block=create_block,
        create_url=create_url,
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        page_size=PAGE_SIZE,
        **_common(context, invoice, balance),
    )


def _render_create(context, invoice, payments, intents, balance, confirm_error=None):
    return render_template(
        "admin/payment_intents/create.html",
        confirm_error=confirm_error,
        action_url=_new_url(context, invoice.public_id),
        state_token=tokens.make_token(
            tokens.PURPOSE_CREATE,
            **_create_state(current_user.public_id, invoice, payments, intents),
        ),
        amount_text=money.format_amount(balance.outstanding),
        **_common(context, invoice, balance),
    )


@admin_bp.route(_INTENTS + "/new", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_create(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id
):
    """GET: the confirmation page. POST: ask the Mock/Sandbox provider to
    collect the invoice's exact outstanding balance under a server-derived
    idempotency key, and record one ``pending`` intent. A replay of the same
    logical request resolves to the intent it already created."""
    _mock_or_404()
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    intents_url = _intents_url(context, invoice.public_id)
    lines, active, payments, intents, balance = _pre_lock_state(invoice)
    block = _create_block(invoice, lines, active, payments, intents, balance)
    if request.method == "GET":
        if block is not None:
            flash(block, "warning")
            return redirect(intents_url)
        return _render_create(context, invoice, payments, intents, balance)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    payload = tokens.load_token(token, tokens.PURPOSE_CREATE)
    replayed = _replayed_intent(payload, actor_public_id, invoice)
    if replayed is not None:
        return _reject(
            _ALREADY_CREATED_MESSAGE,
            _detail(context, invoice.public_id, replayed.public_id),
            actor_id,
            "info",
        )
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_CREATE,
        **_create_state(actor_public_id, invoice, payments, intents),
    ):
        return _reject(_STALE_MESSAGE, intents_url, actor_id)
    if block is not None:
        return _reject(block, intents_url, actor_id, "warning")
    if request.form.get("confirm") != "yes":
        return _render_create(context, invoice, payments, intents, balance, _CREATE_CONFIRM_MESSAGE)
    idempotency_key = tokens.idempotency_key_for(payload)
    invoice_id = invoice.id

    locks = _lock(context, actor_id, invoice_id)
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, intents_url, actor_id)
    settings = _settings()
    if not settings.mock_enabled:
        return _reject(_MOCK_DISABLED_MESSAGE, intents_url, actor_id, "warning")
    existing = invoice_intent_by_idempotency_key(locked.id, idempotency_key)
    if existing is not None:
        return _reject(
            _ALREADY_CREATED_MESSAGE,
            _detail(context, invoice_public_id, existing.public_id),
            actor_id,
            "info",
        )
    payments = locked_invoice_payments(locks)
    intents = locked_invoice_intents(locks)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_CREATE,
        **_create_state(actor_public_id, locked, payments, intents),
    ):
        return _reject(_STALE_MESSAGE, intents_url, actor_id)
    item_rows = invoice_rows_for_snapshot(locked)
    active = active_lines(item_rows)
    balance = _balance_of(active, payments)
    block = _create_block(locked, item_rows, active, payments, intents, balance)
    if block is not None:
        return _reject(block, intents_url, actor_id, "warning")
    amount = balance.outstanding
    if idempotency_key_taken(idempotency_key):
        return _reject(_INTEGRITY_MESSAGE, intents_url, actor_id)

    try:
        reported = settings.provider.create_payment_intent(
            idempotency_key=idempotency_key, amount=amount, currency_code=money.CURRENCY_CODE
        )
    except PaymentProviderError:
        return _reject(_PROVIDER_REFUSED_MESSAGE, intents_url, actor_id, "warning")
    if not _created_as_asked(reported, amount):
        return _reject(_PROVIDER_REFUSED_MESSAGE, intents_url, actor_id, "warning")
    if provider_reference_taken(reported.reference):
        return _reject(_INTEGRITY_MESSAGE, intents_url, actor_id)
    moment = _write_moment()
    intent_public_id = str(uuid.uuid4())
    try:
        db.session.add(
            PaymentIntent(
                public_id=intent_public_id,
                invoice_id=locked.id,
                provider=settings.provider.name,
                provider_reference=reported.reference,
                idempotency_key=idempotency_key,
                status=_PENDING,
                currency_code=money.CURRENCY_CODE,
                amount=amount,
                created_by_id=actor_id,
                provider_result_at=None,
                provider_result_by_id=None,
                terminal_at=None,
                cancelled_by_id=None,
                version=1,
                created_at=moment,
                updated_at=moment,
            )
        )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, intents_url, actor_id)

    flash(
        _CREATED_MESSAGE.format(amount=money.format_amount(amount), currency=money.CURRENCY_CODE),
        "success",
    )
    return redirect(_detail(context, invoice_public_id, intent_public_id))


# ======================================================================
# One intent
# ======================================================================


@admin_bp.get(_ONE)
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_detail(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    intent_public_id,
):
    """One intent, what the provider has reported, and -- when the sandbox is
    enabled and the intent is pending -- its checkout and cancel actions."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    intent = _intent_or_404(invoice, intent_public_id)
    sandbox_actions = _settings().mock_enabled and intent.status == _PENDING
    return render_template(
        "admin/payment_intents/detail.html",
        intent=build_intent_view(intent, _names_of([intent]), _tz_name()),
        checkout_url=_intent_url(
            "admin.invoice_payment_intent_checkout", context, invoice.public_id, intent.public_id
        )
        if sandbox_actions
        else None,
        cancel_url=_intent_url(
            "admin.invoice_payment_intent_cancel", context, invoice.public_id, intent.public_id
        )
        if sandbox_actions
        else None,
        result_notice=SANDBOX_RESULT_NOTICE,
        **_common(context, invoice, _invoice_balance(invoice)),
    )


def _checkout_urls(context, invoice_public_id, intent_public_id):
    return {
        "checkout_url": _intent_url(
            "admin.invoice_payment_intent_checkout", context, invoice_public_id, intent_public_id
        ),
        "return_url": _intent_url(
            "admin.invoice_payment_intent_return", context, invoice_public_id, intent_public_id
        ),
    }


@admin_bp.route(_ONE + "/checkout", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_checkout(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    intent_public_id,
):
    """The Mock/Sandbox checkout. GET: the simulation page, with a fresh
    signed checkout context. POST: record one simulated outcome in the mock
    provider's own ledger -- and nothing else; the local intent changes only
    through the browser return, from the provider's reported status."""
    _mock_or_404()
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    intent = _intent_or_404(invoice, intent_public_id)
    detail_url = _detail(context, invoice.public_id, intent.public_id)
    urls = _checkout_urls(context, invoice.public_id, intent.public_id)
    if intent.status != _PENDING:
        flash(_NOT_PENDING_MESSAGE, "info")
        return redirect(detail_url)
    if request.method == "GET":
        return render_template(
            "admin/payment_intents/checkout.html",
            intent=build_intent_view(intent, {}, _tz_name()),
            checkout_field=_CHECKOUT_FIELD,
            checkout_context=tokens.make_checkout_context(intent),
            outcomes=_OUTCOME_CHOICES,
            intent_url=detail_url,
            currency_code=money.CURRENCY_CODE,
            **urls,
        )

    actor_id = current_user.id
    if tokens.token_is_stale(
        request.form.get(_CHECKOUT_FIELD), tokens.PURPOSE_CHECKOUT, **_checkout_state(intent)
    ):
        return _reject(_CHECKOUT_INVALID_MESSAGE, urls["checkout_url"], actor_id)
    outcome = request.form.get("outcome")
    if outcome not in SANDBOX_OUTCOMES:
        return _reject(_OUTCOME_INVALID_MESSAGE, urls["checkout_url"], actor_id, "warning")
    try:
        _settings().provider.simulate_checkout_outcome(intent.provider_reference, outcome)
    except PaymentProviderError:
        return _reject(_SIMULATION_REFUSED_MESSAGE, urls["checkout_url"], actor_id, "warning")
    flash(_SIMULATED_MESSAGE.format(outcome=_OUTCOME_NAMES[outcome]), "info")
    return redirect(urls["checkout_url"])


@admin_bp.post(_ONE + "/return")
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_return(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    intent_public_id,
):
    """The browser return from the sandbox checkout. Reads the provider's
    result **only** through ``get_payment_status()`` under the lock chain and
    records ``provider_succeeded`` or ``provider_failed`` for a still-pending
    intent -- never a payment, a receipt or a balance change. Anything the
    request itself claims about the result is ignored."""
    _mock_or_404()
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    intent = _intent_or_404(invoice, intent_public_id)
    detail_url = _detail(context, invoice.public_id, intent.public_id)
    result_url = _intent_url(
        "admin.invoice_payment_intent_result", context, invoice.public_id, intent.public_id
    )
    actor_id = current_user.id
    checkout_context = request.form.get(_CHECKOUT_FIELD)
    if tokens.token_is_stale(checkout_context, tokens.PURPOSE_CHECKOUT, **_checkout_state(intent)):
        return _reject(_CHECKOUT_INVALID_MESSAGE, detail_url, actor_id)
    if intent.status != _PENDING:
        return _reject(_NOT_PENDING_MESSAGE, detail_url, actor_id, "info")
    invoice_id, intent_id = invoice.id, intent.id

    locks = _lock(context, actor_id, invoice_id, intent_id=intent_id)
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    target = _locked_intent_or_404(locks, intent_id, intent_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    settings = _settings()
    if not settings.mock_enabled or target.provider != settings.provider.name:
        return _reject(_MOCK_DISABLED_MESSAGE, detail_url, actor_id, "warning")
    if tokens.token_is_stale(checkout_context, tokens.PURPOSE_CHECKOUT, **_checkout_state(target)):
        return _reject(_CHECKOUT_INVALID_MESSAGE, detail_url, actor_id)
    if target.status != _PENDING:
        return _reject(_NOT_PENDING_MESSAGE, detail_url, actor_id, "info")
    if locked.status != _ISSUED:
        return _reject(_NOT_ISSUED_MESSAGE, detail_url, actor_id, "warning")
    if [row.id for row in active_intents(locked_invoice_intents(locks))] != [target.id]:
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    try:
        reported = settings.provider.get_payment_status(target.provider_reference)
    except PaymentProviderError:
        return _reject(_PROVIDER_REFUSED_MESSAGE, detail_url, actor_id, "warning")
    if reported.reference != target.provider_reference:
        return _reject(_PROVIDER_REFUSED_MESSAGE, detail_url, actor_id, "warning")
    if reported.status == _REPORTED_PENDING:
        return _reject(_RESULT_PENDING_MESSAGE, result_url, actor_id, "info")
    if reported.status == _REPORTED_CANCELLED:
        return _reject(_RESULT_CANCELLED_MESSAGE, result_url, actor_id, "info")
    if (
        reported.status not in (_REPORTED_SUCCEEDED, _REPORTED_FAILED)
        or reported.amount != target.amount
        or reported.currency_code != target.currency_code
    ):
        return _reject(_PROVIDER_REFUSED_MESSAGE, detail_url, actor_id, "warning")

    moment = _write_moment()
    decided = _SUCCEEDED if reported.status == _REPORTED_SUCCEEDED else _FAILED
    try:
        target.version = target.version + 1
        target.status = decided
        target.provider_result_at = moment
        target.provider_result_by_id = actor_id
        if decided == _FAILED:
            target.terminal_at = moment
        target.updated_at = moment
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    flash(SANDBOX_RESULT_NOTICE[0], "success")
    return redirect(result_url)


@admin_bp.get(_ONE + "/result")
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_result(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    intent_public_id,
):
    """The safe result page of the browser return: the intent's recorded
    provider result, the invoice's unchanged balance, and the statement that
    no payment is confirmed until Phase 5 / M07 verifies a signed webhook.
    Read-only: visiting it changes nothing."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    intent = _intent_or_404(invoice, intent_public_id)
    return render_template(
        "admin/payment_intents/result.html",
        intent=build_intent_view(intent, _names_of([intent]), _tz_name()),
        intent_url=_detail(context, invoice.public_id, intent.public_id),
        result_notice=SANDBOX_RESULT_NOTICE,
        **_common(context, invoice, _invoice_balance(invoice)),
    )


@admin_bp.route(_ONE + "/cancel", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_cancel(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id,
    intent_public_id,
):
    """Cancel a ``pending`` intent: the provider is asked first, and only its
    confirmed cancellation moves the intent to ``cancelled`` -- once, with who
    and when -- which releases the invoice when nothing else freezes it. A
    ``provider_succeeded`` intent cannot be cancelled in M06."""
    _mock_or_404()
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    intent = _intent_or_404(invoice, intent_public_id)
    detail_url = _detail(context, invoice.public_id, intent.public_id)
    action_url = _intent_url(
        "admin.invoice_payment_intent_cancel", context, invoice.public_id, intent.public_id
    )
    if intent.status == _SUCCEEDED:
        flash(_SUCCEEDED_NOT_CANCELLABLE_MESSAGE, "info")
        return redirect(detail_url)
    if intent.status != _PENDING:
        flash(_NOT_PENDING_MESSAGE, "info")
        return redirect(detail_url)
    intents = invoice_intent_rows(invoice.id)

    def render(confirm_error=None):
        return render_template(
            "admin/payment_intents/cancel.html",
            intent=build_intent_view(intent, _names_of([intent]), _tz_name()),
            intent_url=detail_url,
            action_url=action_url,
            confirm_error=confirm_error,
            state_token=tokens.make_token(
                tokens.PURPOSE_CANCEL,
                **_cancel_state(current_user.public_id, invoice, intent, intents),
            ),
            **_common(context, invoice, _invoice_balance(invoice)),
        )

    if request.method == "GET":
        return render()

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_CANCEL, **_cancel_state(actor_public_id, invoice, intent, intents)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    if request.form.get("confirm") != "yes":
        return render(_CANCEL_CONFIRM_MESSAGE)
    invoice_id, intent_id = invoice.id, intent.id

    locks = _lock(context, actor_id, invoice_id, intent_id=intent_id)
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    target = _locked_intent_or_404(locks, intent_id, intent_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    settings = _settings()
    if not settings.mock_enabled or target.provider != settings.provider.name:
        return _reject(_MOCK_DISABLED_MESSAGE, detail_url, actor_id, "warning")
    intents = locked_invoice_intents(locks)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_CANCEL, **_cancel_state(actor_public_id, locked, target, intents)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    if target.status == _SUCCEEDED:
        return _reject(_SUCCEEDED_NOT_CANCELLABLE_MESSAGE, detail_url, actor_id, "info")
    if target.status != _PENDING:
        return _reject(_NOT_PENDING_MESSAGE, detail_url, actor_id, "info")
    if locked.status != _ISSUED:
        return _reject(_NOT_ISSUED_MESSAGE, detail_url, actor_id, "warning")

    try:
        reported = settings.provider.cancel_payment(target.provider_reference)
    except PaymentProviderError:
        return _reject(_PROVIDER_REFUSED_MESSAGE, detail_url, actor_id, "warning")
    if (
        reported.reference != target.provider_reference
        or reported.status != _REPORTED_CANCELLED
    ):
        return _reject(_PROVIDER_REFUSED_MESSAGE, detail_url, actor_id, "warning")

    moment = _write_moment()
    try:
        target.version = target.version + 1
        target.status = _CANCELLED
        target.terminal_at = moment
        target.cancelled_by_id = actor_id
        target.updated_at = moment
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    flash(_CANCELLED_OK_MESSAGE, "success")
    return redirect(detail_url)


# ======================================================================
# The sandbox intent overview
# ======================================================================


@admin_bp.get("/payment-intents")
@roles_required(_ADMINISTRATOR)
@_financial_response
def payment_intents_overview():
    """Every payment intent, newest first, 20 per page with no ``COUNT``,
    filtered only by a known status; anything else is dropped. One query per
    page whatever it holds."""
    page = normalize_page(request.args.get("page"))
    status = normalize_intent_status_filter(request.args.get("status"))
    rows, has_next = intents_overview_page(page, status)
    if not rows and page > 1:
        page = 1
        rows, has_next = intents_overview_page(page, status)
    entries = build_overview_view(rows, _tz_name())
    for entry in entries:
        entry["url"] = url_for(
            "admin.invoice_payment_intent_detail",
            group_public_id=entry["group_public_id"],
            enrollment_public_id=entry["enrollment_public_id"],
            assignment_public_id=entry["assignment_public_id"],
            invoice_public_id=entry["invoice_public_id"],
            intent_public_id=entry["public_id"],
        )
    return render_template(
        "admin/payment_intents/overview.html",
        intents=entries,
        status_labels=STATUS_LABELS,
        filter_status=status,
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        page_size=PAGE_SIZE,
        mock_enabled=_settings().mock_enabled,
        tz_name=_tz_name(),
        currency_code=money.CURRENCY_CODE,
    )
