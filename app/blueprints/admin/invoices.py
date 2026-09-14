"""Administrator invoices for Student Fee Assignments (Phase 5 / M04).

Nine URL rules, nested below the fee assignment routes and addressed only by
public identifiers. With ``<base>`` =
``/admin/groups/<gp>/enrollments/<ep>/fee-assignments/<ap>/invoices``::

    GET       <base>                                one assignment's invoices
    GET|POST  <base>/new                            confirm, then create a draft
    GET       <base>/<ip>                           detail and audit timeline
    GET       <base>/<ip>/edit                      the line edit surface
    GET|POST  <base>/<ip>/items/new                 add a line
    GET|POST  <base>/<ip>/items/<lp>/edit           edit a line
    GET|POST  <base>/<ip>/items/<lp>/remove         confirm, then remove a line
    POST      <base>/<ip>/issue                     issue and number a draft
    GET|POST  <base>/<ip>/cancel                    confirm, then cancel

**An invoice belongs to one Student Fee Assignment**, which holds at most one
``draft`` or ``issued`` invoice at a time. A draft copies the assigned fee
plan's active items as its own lines. An Administrator issues it manually,
which allocates its permanent ``INV-YYYY-NNNNNN`` number. No payment exists in
M04, so a draft or issued invoice's lines may still be added, edited and
removed; after issue every change needs a reason. Cancellation needs a reason,
keeps every row and number, and leaves the invoice read-only forever; a new
draft may then be created for an assignment that is still ``assigned``.

**Every movement writes exactly one audit event** in the same transaction
(``app/services/invoice_audit.py``), with server-built before / after
snapshots. Nothing here edits or deletes an event, and no route exists that
could.

**This is the only surface that reads or writes an invoice.** Every route is
gated by ``roles_required``; every write re-proves the acting account against
its locked row.

**Every mutation** is POST-only and CSRF-protected, carries a purpose-specific
signed token (``app/services/invoice_tokens.py``) minted by the GET page that
shows exactly what will change, runs the lock chain in
``app/services/invoice_transactions.py``, and re-proves the actor, the
nesting, the academic chain the chain locked, the token, the lifecycle, the
lines and every applicable rule against the locked rows. A form that fails
validation is re-rendered only while its token still describes current state.
An ``IntegrityError`` is rolled back first, the actor is re-authorized from
current state, and one generic sentence is shown.

**Eligibility.** Creating a draft needs an active Enrollment, Student and
academic chain, an ``assigned`` assignment and a plan that was activated at
least once. Once an invoice exists its correction -- line changes, issue and
cancellation -- stays available whatever later happens to the Student, the
Enrollment, the Group, the academic chain, the plan or the assignment.

A URL naming anything that does not nest inside the link before it is a plain
404. Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``.
"""

import uuid

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import ValidationError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plan_forms import AMOUNT_MESSAGES, KIND_MESSAGE, LABEL_MESSAGES
from app.blueprints.admin.fee_plans import _financial_response, _reject, _tz_name, _write_moment
from app.extensions import db
from app.models import (
    INVOICE_AUDIT_REASON_MAX_LENGTH,
    INVOICE_ITEM_LABEL_MAX_LENGTH,
    MAX_ACTIVE_INVOICE_ITEMS,
    MAX_INVOICE_ITEM_ROWS,
    FeePlan,
    Invoice,
    InvoiceItem,
    InvoiceItemStatus,
    InvoiceStatus,
    UserRole,
)
from app.models.fee_plan import TEXT_CONTROL, TEXT_MISSING, TEXT_TOO_LONG
from app.models.invoice_item import normalize_invoice_item_label
from app.models.payment_audit_event import normalize_audit_reason
from app.security.decorators import roles_required
from app.services import invoice_tokens as tokens
from app.services import money
from app.services.fee_plan_queries import KIND_CHOICES, normalize_page
from app.services.fee_plan_transactions import administrator_authz_broken, label_in_use
from app.services.invoice_audit import (
    CANCELLED as EVENT_CANCELLED,
)
from app.services.invoice_audit import (
    CREATED as EVENT_CREATED,
)
from app.services.invoice_audit import (
    ISSUED as EVENT_ISSUED,
)
from app.services.invoice_audit import (
    build_invoice_snapshot,
    edit_event_kind,
    record_invoice_event,
)
from app.services.invoice_queries import (
    BLOCK_ACADEMIC_INACTIVE,
    BLOCK_ASSIGNMENT_CANCELLED,
    BLOCK_ENROLLMENT_INACTIVE,
    BLOCK_OPEN_INVOICE,
    BLOCK_PLAN_UNAVAILABLE,
    BLOCK_STUDENT_INACTIVE,
    PAGE_SIZE,
    STATUS_LABELS,
    active_lines,
    assignment_invoice,
    assignment_invoice_context,
    audit_event_page,
    build_assignment_view,
    build_invoice_history_view,
    build_invoice_view,
    build_timeline_view,
    context_draft_block_reason,
    draft_block_reason,
    invoice_history_page,
    invoice_line,
    invoice_line_summaries,
    invoice_lines,
    line_form_data,
)
from app.services.invoice_transactions import (
    allocate_invoice_number,
    assignment_nesting_broken,
    fee_plan_invoiceable,
    invoice_items_valid,
    invoice_nesting_broken,
    invoice_number_taken,
    invoice_rows_for_snapshot,
    latest_change,
    lock_invoice_chain,
    lock_invoice_number_sequence,
    locked_active_items,
    locked_assignment_invoices,
    locked_item,
    locked_plan_items,
    open_invoices,
)
from app.services.schedule_occurrences import to_app_local
from app.services.student_fee_assignment_queries import (
    build_enrollment_context_view,
    build_plan_confirmation_view,
    fee_plan_active_items,
)
from app.services.student_fee_assignment_transactions import (
    enrollment_nesting_broken,
    fee_plan_items_valid,
    hierarchy_moved,
    locked_academic_statuses,
)

_DRAFT = InvoiceStatus.DRAFT.value
_ISSUED = InvoiceStatus.ISSUED.value
_CANCELLED = InvoiceStatus.CANCELLED.value
_OPEN = (_DRAFT, _ISSUED)
_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value
_ITEM_REMOVED = InvoiceItemStatus.REMOVED.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

#: The hidden form field every mutating form carries its signed token in.
_STATE_FIELD = "state_token"

_BASE = (
    "/groups/<group_public_id>/enrollments/<enrollment_public_id>"
    "/fee-assignments/<assignment_public_id>/invoices"
)


# ======================================================================
# Administrator-facing sentences, declared once each
# ======================================================================

_STALE_MESSAGE = (
    "This invoice or its fee assignment changed after this page was opened, or the page has "
    "expired. Nothing was saved. Please reload, review the current state, and try again."
)
_INTEGRITY_MESSAGE = (
    "That change could not be saved because the data changed at the same moment. "
    "Nothing was written. Please reload and try again."
)
_BLOCK_MESSAGES = {
    BLOCK_ASSIGNMENT_CANCELLED: (
        "This fee assignment is cancelled, so no new invoice can be created for it. Its invoices "
        "are kept here as history."
    ),
    BLOCK_OPEN_INVOICE: (
        "This fee assignment already has a draft or issued invoice. Cancel that invoice "
        "explicitly before creating another."
    ),
    BLOCK_ENROLLMENT_INACTIVE: (
        "This enrollment is withdrawn, so a new invoice cannot be created."
    ),
    BLOCK_STUDENT_INACTIVE: (
        "This student's account is not active, so a new invoice cannot be created."
    ),
    BLOCK_ACADEMIC_INACTIVE: (
        "This enrollment's group, course, level or academic term is archived, so a new invoice "
        "cannot be created."
    ),
    BLOCK_PLAN_UNAVAILABLE: (
        "The assigned fee plan has no recorded activation, so an invoice cannot be copied from it."
    ),
}
_PLAN_ITEMS_INVALID_MESSAGE = (
    "The assigned fee plan does not have a valid set of items, so an invoice cannot be copied "
    "from it."
)
_READ_ONLY_MESSAGE = "This invoice is cancelled. A cancelled invoice and its lines are read-only."
_LINE_REMOVED_MESSAGE = (
    "This line has already been removed. Removed lines are kept as history and cannot be changed."
)
_LABEL_TAKEN_MESSAGE = (
    "This invoice already has a line with this label. Give each line its own label."
)
_LINE_LIMIT_MESSAGE = (
    f"An invoice can have at most {MAX_ACTIVE_INVOICE_ITEMS} active lines. Remove one before "
    "adding another."
)
_ROW_LIMIT_MESSAGE = (
    f"This invoice already holds {MAX_INVOICE_ITEM_ROWS} lines, removed lines included. Cancel it "
    "and create a new draft to make further changes."
)
_LAST_LINE_MESSAGE = (
    "An invoice must keep at least one line. Add another line before removing this one, or "
    "cancel the invoice."
)
_NOT_ISSUABLE_MESSAGE = (
    "Only a draft invoice can be issued. This invoice has already been issued or cancelled, so "
    "nothing was changed."
)
_LINES_INVALID_MESSAGE = (
    f"This draft cannot be issued: it needs one to {MAX_ACTIVE_INVOICE_ITEMS} lines with distinct "
    "labels and valid amounts."
)
_NUMBERS_EXHAUSTED_MESSAGE = (
    "No invoice number is left for this year. The invoice was not issued and no number was used."
)
_ISSUE_CONFIRM_MESSAGE = (
    "Please tick the confirmation box before issuing. An invoice number is permanent."
)
_CANCEL_CONFIRM_MESSAGE = "Please tick the confirmation box before cancelling this invoice."
_ALREADY_CANCELLED_MESSAGE = "This invoice is already cancelled. Nothing was changed."
_NO_CHANGES_MESSAGE = "Nothing was changed, so nothing was saved."
_CREATED_MESSAGE = (
    "Draft invoice created from the assigned fee plan. Review its lines, then issue it when it "
    "is correct."
)
_LINE_ADDED_MESSAGE = "Line added."
_LINE_SAVED_MESSAGE = "Line saved."
_LINE_REMOVED_OK_MESSAGE = "Line removed. It is kept as history."
_ISSUED_MESSAGE = "Invoice issued as {number}. Its number is permanent."
_CANCELLED_OK_MESSAGE = "Invoice cancelled. It is kept as read-only history."

_REASON_MESSAGES = {
    TEXT_MISSING: (
        "Give the reason for this change. An issued invoice is only changed with a recorded "
        "reason."
    ),
    TEXT_CONTROL: "The reason may only contain ordinary text and line breaks.",
    TEXT_TOO_LONG: f"The reason must be at most {INVOICE_AUDIT_REASON_MAX_LENGTH} characters.",
}
_CANCEL_REASON_MISSING = "Give the reason for cancelling this invoice."

_KIND_VALUES = frozenset(value for value, _label in KIND_CHOICES)


# ======================================================================
# Forms
# ======================================================================


class InvoiceLineForm(FlaskForm):
    """Kind, label and exact amount of one invoice line, plus the reason an
    issued invoice's change needs.

    The amount is text until :func:`money.parse_amount` accepts it, exactly as
    for a fee plan item. There is no quantity, discount, tax, due-date,
    status, currency or plan field.
    """

    kind = SelectField("Kind", choices=list(KIND_CHOICES), validate_choice=False)
    label = StringField("Label")
    amount = StringField(f"Amount ({money.CURRENCY_CODE})")
    reason = TextAreaField("Reason for this change")
    submit = SubmitField("Save line")

    def __init__(self, *args, reason_required=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.reason_required = reason_required
        self.normalized_label = None
        self.parsed_amount = None
        self.normalized_reason = None

    def validate_kind(self, field):
        if field.data not in _KIND_VALUES:
            raise ValidationError(KIND_MESSAGE)

    def validate_label(self, field):
        value, error = normalize_invoice_item_label(field.data)
        if error is not None:
            raise ValidationError(LABEL_MESSAGES[error])
        self.normalized_label = value

    def validate_amount(self, field):
        value, error = money.parse_amount(field.data)
        if error is not None:
            raise ValidationError(AMOUNT_MESSAGES[error])
        self.parsed_amount = value

    def validate_reason(self, field):
        if not self.reason_required:
            return
        value, error = normalize_audit_reason(field.data)
        if error is not None:
            raise ValidationError(_REASON_MESSAGES[error])
        self.normalized_reason = value


class InvoiceReasonForm(FlaskForm):
    """The reason for removing an issued invoice's line or for cancelling an
    invoice. Nothing else is submitted."""

    reason = TextAreaField("Reason")

    def __init__(self, *args, reason_required=False, missing_message=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reason_required = reason_required
        self.missing_message = missing_message
        self.normalized_reason = None

    def validate_reason(self, field):
        if not self.reason_required:
            return
        value, error = normalize_audit_reason(field.data)
        if error is not None:
            if error == TEXT_MISSING and self.missing_message:
                raise ValidationError(self.missing_message)
            raise ValidationError(_REASON_MESSAGES[error])
        self.normalized_reason = value


# ======================================================================
# URLs, state and shared handling
# ======================================================================


def _ids(context):
    return {
        "group_public_id": context.group_public_id,
        "enrollment_public_id": context.enrollment_public_id,
        "assignment_public_id": context.assignment_public_id,
    }


def _history_url(context):
    return url_for("admin.assignment_invoices", **_ids(context))


def _new_url(context):
    return url_for("admin.assignment_invoice_create", **_ids(context))


def _detail_url(context, invoice_public_id):
    return url_for("admin.invoice_detail", invoice_public_id=invoice_public_id, **_ids(context))


def _edit_url(context, invoice_public_id):
    return url_for("admin.invoice_edit", invoice_public_id=invoice_public_id, **_ids(context))


def _line_new_url(context, invoice_public_id):
    return url_for(
        "admin.invoice_line_create", invoice_public_id=invoice_public_id, **_ids(context)
    )


def _line_edit_url(context, invoice_public_id, item_public_id):
    return url_for(
        "admin.invoice_line_edit",
        invoice_public_id=invoice_public_id,
        item_public_id=item_public_id,
        **_ids(context),
    )


def _line_remove_url(context, invoice_public_id, item_public_id):
    return url_for(
        "admin.invoice_line_remove",
        invoice_public_id=invoice_public_id,
        item_public_id=item_public_id,
        **_ids(context),
    )


def _issue_url(context, invoice_public_id):
    return url_for("admin.invoice_issue", invoice_public_id=invoice_public_id, **_ids(context))


def _cancel_url(context, invoice_public_id):
    return url_for("admin.invoice_cancel", invoice_public_id=invoice_public_id, **_ids(context))


def _create_state(context, actor_public_id):
    """Every create-token field except the assignment version."""
    return {
        "actor_public_id": actor_public_id,
        "group_public_id": context.group_public_id,
        "enrollment_public_id": context.enrollment_public_id,
        "assignment_public_id": context.assignment_public_id,
        "plan_public_id": context.plan_public_id,
    }


def _invoice_state(actor_public_id, invoice):
    return {
        "actor_public_id": actor_public_id,
        "invoice_public_id": invoice.public_id,
        "invoice_version": invoice.version,
    }


def _page_context(context):
    """What every M04 page shows about where the invoice sits."""
    return {
        "context": build_enrollment_context_view(context),
        "assignment": build_assignment_view(context),
        "history_url": _history_url(context),
        "fee_assignments_url": url_for(
            "admin.enrollment_fee_assignments",
            group_public_id=context.group_public_id,
            enrollment_public_id=context.enrollment_public_id,
        ),
        "plan_url": url_for("admin.fee_plan_detail", plan_public_id=context.plan_public_id),
        "currency_code": money.CURRENCY_CODE,
        "state_field": _STATE_FIELD,
    }


def _invoice_header(invoice):
    return {
        "public_id": invoice.public_id,
        "status": invoice.status,
        "status_label": STATUS_LABELS.get(invoice.status, invoice.status),
        "invoice_number": invoice.invoice_number,
        "version": invoice.version,
        "is_draft": invoice.status == _DRAFT,
        "is_issued": invoice.status == _ISSUED,
    }


def _context_or_404(group_public_id, enrollment_public_id, assignment_public_id):
    context = assignment_invoice_context(
        group_public_id, enrollment_public_id, assignment_public_id
    )
    if context is None:
        abort(404)
    return context


def _invoice_or_404(context, invoice_public_id):
    invoice = assignment_invoice(context.assignment_id, invoice_public_id)
    if invoice is None:
        abort(404)
    return invoice


def _line_or_404(invoice, item_public_id):
    line = invoice_line(invoice.id, item_public_id)
    if line is None:
        abort(404)
    return line


def _lock(context, actor_id, **links):
    """The M04 chain for `context`'s assignment. `context` is a plain row
    captured before the chain's reset, so it stays readable afterwards."""
    return lock_invoice_chain(
        context.group_public_id,
        context.academic_term_id,
        context.level_id,
        context.course_id,
        context.student_id,
        context.enrollment_id,
        actor_id,
        context.assignment_id,
        **links,
    )


def _prove_chain(locks, context):
    """Re-prove the locked actor and the Group -> Enrollment -> Student ->
    assignment nesting. Any failure rolls back and 404s without saying
    which."""
    if (
        administrator_authz_broken(locks)
        or enrollment_nesting_broken(locks, context.group_public_id, context.enrollment_id)
        or assignment_nesting_broken(locks, context.assignment_id, context.assignment_public_id)
    ):
        db.session.rollback()
        abort(404)


def _locked_invoice_or_404(locks, context, invoice_id, invoice_public_id):
    _prove_chain(locks, context)
    if invoice_nesting_broken(locks, invoice_id, invoice_public_id):
        db.session.rollback()
        abort(404)
    return locks.invoice


def _hierarchy_moved(locks, context):
    return hierarchy_moved(locks, context.academic_term_id, context.level_id, context.course_id)


def _line_limit_message(active_count, row_count):
    if row_count >= MAX_INVOICE_ITEM_ROWS:
        return _ROW_LIMIT_MESSAGE
    if active_count >= MAX_ACTIVE_INVOICE_ITEMS:
        return _LINE_LIMIT_MESSAGE
    return None


def _center_year(moment):
    """The center-local calendar year of a naive-UTC `moment`."""
    return to_app_local(_tz_name(), moment).year


def _record_and_commit(context, locks, invoice, kind, version_before, before, lines, reason, moment):
    """Write the one audit event of a change already applied to `invoice` and
    commit both. The caller catches ``IntegrityError`` / ``ValueError``."""
    record_invoice_event(
        invoice=invoice,
        actor=locks.actor,
        kind=kind,
        version_before=version_before,
        before_snapshot=before,
        after_snapshot=build_invoice_snapshot(invoice, context.assignment_public_id, lines),
        reason=reason,
        moment=moment,
    )
    db.session.commit()


# ======================================================================
# One assignment's invoices
# ======================================================================


@admin_bp.get(_BASE)
@roles_required(_ADMINISTRATOR)
@_financial_response
def assignment_invoices(group_public_id, enrollment_public_id, assignment_public_id):
    """One assignment's invoices, newest first. A fixed number of queries
    whatever the history holds; no ``COUNT``."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    page = normalize_page(request.args.get("page"))
    rows, has_next = invoice_history_page(context.assignment_id, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = invoice_history_page(context.assignment_id, page)
    invoices = build_invoice_history_view(
        rows, invoice_line_summaries([row.id for row in rows]), _tz_name()
    )
    for entry in invoices:
        entry["detail_url"] = _detail_url(context, entry["public_id"])

    block = context_draft_block_reason(context)
    return render_template(
        "admin/invoices/history.html",
        invoices=invoices,
        block_message=_BLOCK_MESSAGES.get(block),
        create_url=None if block else _new_url(context),
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        page_size=PAGE_SIZE,
        tz_name=_tz_name(),
        **_page_context(context),
    )


@admin_bp.route(_BASE + "/new", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def assignment_invoice_create(group_public_id, enrollment_public_id, assignment_public_id):
    """GET: the confirmation page -- the lines the draft will copy from the
    assigned plan. POST: create the draft, its lines and its audit event."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    history_url = _history_url(context)

    if request.method == "GET":
        block = context_draft_block_reason(context)
        if block is not None:
            flash(_BLOCK_MESSAGES[block], "warning")
            return redirect(history_url)
        items = fee_plan_active_items(context.fee_plan_id)
        if not fee_plan_items_valid(items):
            flash(_PLAN_ITEMS_INVALID_MESSAGE, "warning")
            return redirect(history_url)
        state_token = tokens.make_token(
            tokens.PURPOSE_CREATE,
            assignment_version=context.assignment_version,
            **_create_state(context, current_user.public_id),
        )
        return render_template(
            "admin/invoices/create.html",
            plan=build_plan_confirmation_view(db.session.get(FeePlan, context.fee_plan_id), items),
            action_url=_new_url(context),
            state_token=state_token,
            **_page_context(context),
        )

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    bound = _create_state(context, actor_public_id)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_CREATE, assignment_version=context.assignment_version, **bound
    ):
        return _reject(_STALE_MESSAGE, history_url, actor_id)

    locks = _lock(
        context,
        actor_id,
        plan_id=context.fee_plan_id,
        include_plan_items=True,
        include_assignment_invoices=True,
    )
    _prove_chain(locks, context)
    plan = locks.plan
    if (
        plan is None
        or plan.id != locks.assignment.fee_plan_id
        or plan.public_id != context.plan_public_id
    ):
        db.session.rollback()
        abort(404)
    if _hierarchy_moved(locks, context):
        return _reject(_STALE_MESSAGE, history_url, actor_id)
    existing = locked_assignment_invoices(locks)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_CREATE,
        changed_at=latest_change(existing),
        assignment_version=locks.assignment.version,
        **bound,
    ):
        return _reject(_STALE_MESSAGE, history_url, actor_id)

    block = draft_block_reason(
        locks.assignment.status,
        bool(open_invoices(existing)),
        locks.enrollment.status,
        locks.student.status,
        locked_academic_statuses(
            locks, context.academic_term_id, context.level_id, context.course_id
        ),
        fee_plan_invoiceable(plan),
    )
    if block is not None:
        return _reject(_BLOCK_MESSAGES[block], history_url, actor_id, "warning")
    plan_items = locked_plan_items(locks)
    if not fee_plan_items_valid(plan_items):
        return _reject(_PLAN_ITEMS_INVALID_MESSAGE, history_url, actor_id, "warning")

    moment = _write_moment()
    invoice_public_id = str(uuid.uuid4())
    try:
        invoice = Invoice(
            public_id=invoice_public_id,
            student_fee_assignment_id=locks.assignment.id,
            currency_code=money.CURRENCY_CODE,
            status=_DRAFT,
            invoice_number=None,
            issued_at=None,
            issued_by_id=None,
            cancelled_at=None,
            cancelled_by_id=None,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
        db.session.add(invoice)
        db.session.flush()
        lines = [
            InvoiceItem(
                public_id=str(uuid.uuid4()),
                invoice_id=invoice.id,
                kind=item.kind,
                label=item.label,
                amount=item.amount,
                status=_ITEM_ACTIVE,
                removed_at=None,
                removed_by_id=None,
                version=1,
                created_at=moment,
                updated_at=moment,
            )
            for item in plan_items
        ]
        db.session.add_all(lines)
        db.session.flush()
        _record_and_commit(context, locks, invoice, EVENT_CREATED, None, None, lines, None, moment)
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, history_url, actor_id)

    flash(_CREATED_MESSAGE, "success")
    return redirect(_detail_url(context, invoice_public_id))


# ======================================================================
# One invoice
# ======================================================================


@admin_bp.get(_BASE + "/<invoice_public_id>")
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_detail(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    """The invoice, its lines and exact total, and one page of its audit
    timeline, newest first. An issue token is minted only for a draft whose
    lines can be issued."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    lines = invoice_lines(invoice.id)
    view = build_invoice_view(invoice, lines, _tz_name())

    page = normalize_page(request.args.get("page"))
    events, has_next = audit_event_page(invoice.id, page)
    if not events and page > 1:
        page = 1
        events, has_next = audit_event_page(invoice.id, page)

    issue_token = issue_block = None
    if invoice.status == _DRAFT:
        active = active_lines(lines)
        if len(lines) <= MAX_INVOICE_ITEM_ROWS and invoice_items_valid(active):
            issue_token = tokens.make_token(
                tokens.PURPOSE_ISSUE,
                active_items=tokens.line_state(active),
                **_invoice_state(current_user.public_id, invoice),
            )
        else:
            issue_block = _LINES_INVALID_MESSAGE

    is_open = invoice.status in _OPEN
    return render_template(
        "admin/invoices/detail.html",
        invoice=view,
        timeline=build_timeline_view(events, _tz_name()),
        issue_token=issue_token,
        issue_block=issue_block,
        issue_url=_issue_url(context, invoice.public_id),
        edit_url=_edit_url(context, invoice.public_id) if is_open else None,
        cancel_url=_cancel_url(context, invoice.public_id) if is_open else None,
        detail_url=_detail_url(context, invoice.public_id),
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        page_size=PAGE_SIZE,
        tz_name=_tz_name(),
        **_page_context(context),
    )


@admin_bp.get(_BASE + "/<invoice_public_id>/edit")
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_edit(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    """The line edit surface of a draft or issued invoice."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    detail_url = _detail_url(context, invoice.public_id)
    if invoice.status not in _OPEN:
        flash(_READ_ONLY_MESSAGE, "warning")
        return redirect(detail_url)

    lines = invoice_lines(invoice.id)
    view = build_invoice_view(invoice, lines, _tz_name())
    for line in view["active_lines"]:
        line["edit_url"] = _line_edit_url(context, invoice.public_id, line["public_id"])
        line["remove_url"] = _line_remove_url(context, invoice.public_id, line["public_id"])
    limit = _line_limit_message(view["active_count"], len(lines))
    return render_template(
        "admin/invoices/edit.html",
        invoice=view,
        line_actions=True,
        line_new_url=None if limit else _line_new_url(context, invoice.public_id),
        limit_message=limit,
        detail_url=detail_url,
        **_page_context(context),
    )


# ======================================================================
# Lines
# ======================================================================


def _render_line_form(context, invoice, form, line=None):
    """The add / edit page with a token minted from **current** persisted
    state -- only reached when the submitted token, if any, still describes
    that state."""
    state = _invoice_state(current_user.public_id, invoice)
    if line is None:
        token = tokens.make_token(tokens.PURPOSE_ITEM_CREATE, **state)
        action_url = _line_new_url(context, invoice.public_id)
    else:
        token = tokens.make_token(
            tokens.PURPOSE_ITEM_EDIT, item_public_id=line.public_id, **state
        )
        action_url = _line_edit_url(context, invoice.public_id, line.public_id)
    return render_template(
        "admin/invoices/line_form.html",
        form=form,
        invoice=_invoice_header(invoice),
        editing=line is not None,
        action_url=action_url,
        cancel_url=_edit_url(context, invoice.public_id),
        state_token=token,
        label_max=INVOICE_ITEM_LABEL_MAX_LENGTH,
        reason_max=INVOICE_AUDIT_REASON_MAX_LENGTH,
        min_amount=money.format_amount(money.MIN_AMOUNT),
        max_amount=money.format_amount(money.MAX_AMOUNT),
        amount_scale=money.AMOUNT_SCALE,
        **_page_context(context),
    )


@admin_bp.route(_BASE + "/<invoice_public_id>/items/new", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_line_create(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id
):
    """Add one line to a draft or issued invoice, within the line limits and
    with a label no other active line uses."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    detail_url = _detail_url(context, invoice.public_id)
    edit_url = _edit_url(context, invoice.public_id)
    if invoice.status not in _OPEN:
        flash(_READ_ONLY_MESSAGE, "warning")
        return redirect(detail_url)

    status_seen = invoice.status
    form = InvoiceLineForm(
        formdata=request.form if request.method == "POST" else None,
        reason_required=status_seen == _ISSUED,
    )
    lines = invoice_lines(invoice.id)
    limit = _line_limit_message(len(active_lines(lines)), len(lines))
    if request.method == "GET":
        if limit is not None:
            flash(limit, "warning")
            return redirect(edit_url)
        return _render_line_form(context, invoice, form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    new_url = _line_new_url(context, invoice.public_id)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_ITEM_CREATE, **_invoice_state(actor_public_id, invoice)
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if not form.validate_on_submit():
        return _render_line_form(context, invoice, form)
    kind, label, amount = form.kind.data, form.normalized_label, form.parsed_amount
    if label_in_use(active_lines(lines), label):
        form.label.errors.append(_LABEL_TAKEN_MESSAGE)
        return _render_line_form(context, invoice, form)
    if limit is not None:
        return _reject(limit, edit_url, actor_id, "warning")
    invoice_id = invoice.id

    locks = _lock(context, actor_id, invoice_id=invoice_id, include_active_items=True)
    locked = _locked_invoice_or_404(locks, context, invoice_id, invoice_public_id)
    if _hierarchy_moved(locks, context):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_ITEM_CREATE, **_invoice_state(actor_public_id, locked)
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if locked.status not in _OPEN:
        return _reject(_READ_ONLY_MESSAGE, detail_url, actor_id, "warning")
    if locked.status != status_seen:
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    siblings = locked_active_items(locks)
    rows = invoice_rows_for_snapshot(locked)
    limit = _line_limit_message(len(siblings), len(rows))
    if limit is not None:
        return _reject(limit, edit_url, actor_id, "warning")
    if label_in_use(siblings, label):
        return _reject(_LABEL_TAKEN_MESSAGE, new_url, actor_id, "warning")

    moment = _write_moment()
    try:
        before = build_invoice_snapshot(locked, context.assignment_public_id, rows)
        version_before = locked.version
        line = InvoiceItem(
            public_id=str(uuid.uuid4()),
            invoice_id=locked.id,
            kind=kind,
            label=label,
            amount=amount,
            status=_ITEM_ACTIVE,
            removed_at=None,
            removed_by_id=None,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
        db.session.add(line)
        locked.version = version_before + 1
        locked.updated_at = moment
        db.session.flush()
        _record_and_commit(
            context,
            locks,
            locked,
            edit_event_kind(locked.status),
            version_before,
            before,
            rows + [line],
            form.normalized_reason,
            moment,
        )
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, new_url, actor_id)

    flash(_LINE_ADDED_MESSAGE, "success")
    return redirect(edit_url)


@admin_bp.route(
    _BASE + "/<invoice_public_id>/items/<item_public_id>/edit", methods=["GET", "POST"]
)
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_line_edit(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, item_public_id
):
    """Change one active line. A save whose kind, label and exact amount all
    equal the stored ones is a no-op: no version, timestamp or event moves."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    line = _line_or_404(invoice, item_public_id)
    detail_url = _detail_url(context, invoice.public_id)
    edit_url = _edit_url(context, invoice.public_id)
    if invoice.status not in _OPEN:
        flash(_READ_ONLY_MESSAGE, "warning")
        return redirect(detail_url)
    if line.status != _ITEM_ACTIVE:
        flash(_LINE_REMOVED_MESSAGE, "warning")
        return redirect(edit_url)

    status_seen = invoice.status
    form = InvoiceLineForm(
        formdata=request.form if request.method == "POST" else None,
        data=line_form_data(line),
        reason_required=status_seen == _ISSUED,
    )
    if request.method == "GET":
        return _render_line_form(context, invoice, form, line)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    form_url = _line_edit_url(context, invoice.public_id, line.public_id)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_EDIT,
        item_public_id=line.public_id,
        **_invoice_state(actor_public_id, invoice),
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if not form.validate_on_submit():
        return _render_line_form(context, invoice, form, line)
    kind, label, amount = form.kind.data, form.normalized_label, form.parsed_amount
    if label_in_use(active_lines(invoice_lines(invoice.id)), label, exclude_item_id=line.id):
        form.label.errors.append(_LABEL_TAKEN_MESSAGE)
        return _render_line_form(context, invoice, form, line)
    invoice_id, line_id = invoice.id, line.id

    locks = _lock(
        context, actor_id, invoice_id=invoice_id, item_ids=(line_id,), include_active_items=True
    )
    locked = _locked_invoice_or_404(locks, context, invoice_id, invoice_public_id)
    locked_line = locked_item(locks, line_id, item_public_id)
    if locked_line is None:
        db.session.rollback()
        abort(404)
    if _hierarchy_moved(locks, context):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_EDIT,
        item_public_id=locked_line.public_id,
        **_invoice_state(actor_public_id, locked),
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if locked.status not in _OPEN:
        return _reject(_READ_ONLY_MESSAGE, detail_url, actor_id, "warning")
    if locked.status != status_seen:
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if locked_line.status != _ITEM_ACTIVE:
        return _reject(_LINE_REMOVED_MESSAGE, edit_url, actor_id, "warning")
    if label_in_use(locked_active_items(locks), label, exclude_item_id=line_id):
        return _reject(_LABEL_TAKEN_MESSAGE, form_url, actor_id, "warning")
    if locked_line.kind == kind and locked_line.label == label and locked_line.amount == amount:
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(edit_url)

    rows = invoice_rows_for_snapshot(locked)
    moment = _write_moment()
    try:
        before = build_invoice_snapshot(locked, context.assignment_public_id, rows)
        version_before = locked.version
        locked_line.kind = kind
        locked_line.label = label
        locked_line.amount = amount
        locked_line.version = locked_line.version + 1
        locked_line.updated_at = moment
        locked.version = version_before + 1
        locked.updated_at = moment
        _record_and_commit(
            context,
            locks,
            locked,
            edit_event_kind(locked.status),
            version_before,
            before,
            rows,
            form.normalized_reason,
            moment,
        )
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, form_url, actor_id)

    flash(_LINE_SAVED_MESSAGE, "success")
    return redirect(edit_url)


def _render_line_remove(context, invoice, line, form):
    token = tokens.make_token(
        tokens.PURPOSE_ITEM_REMOVE,
        item_public_id=line.public_id,
        **_invoice_state(current_user.public_id, invoice),
    )
    return render_template(
        "admin/invoices/line_remove.html",
        form=form,
        invoice=_invoice_header(invoice),
        line={
            "kind_label": dict(KIND_CHOICES).get(line.kind, line.kind),
            "label": line.label,
            "amount_text": money.format_amount(line.amount),
        },
        action_url=_line_remove_url(context, invoice.public_id, line.public_id),
        cancel_url=_edit_url(context, invoice.public_id),
        state_token=token,
        reason_max=INVOICE_AUDIT_REASON_MAX_LENGTH,
        **_page_context(context),
    )


@admin_bp.route(
    _BASE + "/<invoice_public_id>/items/<item_public_id>/remove", methods=["GET", "POST"]
)
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_line_remove(
    group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, item_public_id
):
    """Mark one active line ``removed``, keeping at least one active line. The
    row stays, with its removal attribution, as history."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    line = _line_or_404(invoice, item_public_id)
    detail_url = _detail_url(context, invoice.public_id)
    edit_url = _edit_url(context, invoice.public_id)
    if invoice.status not in _OPEN:
        flash(_READ_ONLY_MESSAGE, "warning")
        return redirect(detail_url)
    if line.status != _ITEM_ACTIVE:
        flash(_LINE_REMOVED_MESSAGE, "warning")
        return redirect(edit_url)

    status_seen = invoice.status
    form = InvoiceReasonForm(
        formdata=request.form if request.method == "POST" else None,
        reason_required=status_seen == _ISSUED,
    )
    if request.method == "GET":
        if len(active_lines(invoice_lines(invoice.id))) <= 1:
            flash(_LAST_LINE_MESSAGE, "warning")
            return redirect(edit_url)
        return _render_line_remove(context, invoice, line, form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_REMOVE,
        item_public_id=line.public_id,
        **_invoice_state(actor_public_id, invoice),
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if not form.validate_on_submit():
        return _render_line_remove(context, invoice, line, form)
    invoice_id, line_id = invoice.id, line.id

    locks = _lock(
        context, actor_id, invoice_id=invoice_id, item_ids=(line_id,), include_active_items=True
    )
    locked = _locked_invoice_or_404(locks, context, invoice_id, invoice_public_id)
    locked_line = locked_item(locks, line_id, item_public_id)
    if locked_line is None:
        db.session.rollback()
        abort(404)
    if _hierarchy_moved(locks, context):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_REMOVE,
        item_public_id=locked_line.public_id,
        **_invoice_state(actor_public_id, locked),
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if locked.status not in _OPEN:
        return _reject(_READ_ONLY_MESSAGE, detail_url, actor_id, "warning")
    if locked.status != status_seen:
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if locked_line.status != _ITEM_ACTIVE:
        return _reject(_LINE_REMOVED_MESSAGE, edit_url, actor_id, "warning")
    if len(locked_active_items(locks)) < 2:
        return _reject(_LAST_LINE_MESSAGE, edit_url, actor_id, "warning")

    rows = invoice_rows_for_snapshot(locked)
    moment = _write_moment()
    try:
        before = build_invoice_snapshot(locked, context.assignment_public_id, rows)
        version_before = locked.version
        locked_line.status = _ITEM_REMOVED
        locked_line.removed_at = moment
        locked_line.removed_by_id = actor_id
        locked_line.version = locked_line.version + 1
        locked_line.updated_at = moment
        locked.version = version_before + 1
        locked.updated_at = moment
        _record_and_commit(
            context,
            locks,
            locked,
            edit_event_kind(locked.status),
            version_before,
            before,
            rows,
            form.normalized_reason,
            moment,
        )
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, edit_url, actor_id)

    flash(_LINE_REMOVED_OK_MESSAGE, "success")
    return redirect(edit_url)


# ======================================================================
# Issue and cancellation
# ======================================================================


@admin_bp.post(_BASE + "/<invoice_public_id>/issue")
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_issue(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    """Issue a draft whose lines are a valid charge: allocate its number under
    the annual sequence's lock, record who and when, move its version once and
    write its audit event -- all in one transaction."""
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    detail_url = _detail_url(context, invoice.public_id)
    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)

    if request.form.get("confirm") != "yes":
        flash(_ISSUE_CONFIRM_MESSAGE, "warning")
        return redirect(detail_url)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ISSUE,
        active_items=tokens.line_state(active_lines(invoice_lines(invoice.id))),
        **_invoice_state(actor_public_id, invoice),
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    invoice_id = invoice.id

    locks = _lock(context, actor_id, invoice_id=invoice_id, include_active_items=True)
    locked = _locked_invoice_or_404(locks, context, invoice_id, invoice_public_id)
    if _hierarchy_moved(locks, context):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    active = locked_active_items(locks)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ISSUE,
        active_items=tokens.line_state(active),
        **_invoice_state(actor_public_id, locked),
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    if locked.status != _DRAFT:
        return _reject(_NOT_ISSUABLE_MESSAGE, detail_url, actor_id, "warning")
    rows = invoice_rows_for_snapshot(locked)
    if len(rows) > MAX_INVOICE_ITEM_ROWS or not invoice_items_valid(active):
        return _reject(_LINES_INVALID_MESSAGE, detail_url, actor_id, "warning")

    provisional = _write_moment()
    year = _center_year(provisional)
    try:
        before = build_invoice_snapshot(locked, context.assignment_public_id, rows)
        sequence = lock_invoice_number_sequence(year, provisional)
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)
    moment = _write_moment()
    if _center_year(moment) != year:
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    number = allocate_invoice_number(sequence, moment)
    if number is None:
        return _reject(_NUMBERS_EXHAUSTED_MESSAGE, detail_url, actor_id, "warning")

    try:
        if invoice_number_taken(number):
            return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)
        version_before = locked.version
        locked.status = _ISSUED
        locked.invoice_number = number
        locked.issued_at = moment
        locked.issued_by_id = actor_id
        locked.version = version_before + 1
        locked.updated_at = moment
        _record_and_commit(
            context, locks, locked, EVENT_ISSUED, version_before, before, rows, None, moment
        )
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    flash(_ISSUED_MESSAGE.format(number=number), "success")
    return redirect(detail_url)


def _render_cancel(context, invoice, form, confirm_error=None):
    token = tokens.make_token(
        tokens.PURPOSE_CANCEL, **_invoice_state(current_user.public_id, invoice)
    )
    return render_template(
        "admin/invoices/cancel.html",
        form=form,
        invoice=build_invoice_view(invoice, invoice_lines(invoice.id), _tz_name()),
        confirm_error=confirm_error,
        action_url=_cancel_url(context, invoice.public_id),
        detail_url=_detail_url(context, invoice.public_id),
        state_token=token,
        reason_max=INVOICE_AUDIT_REASON_MAX_LENGTH,
        **_page_context(context),
    )


@admin_bp.route(_BASE + "/<invoice_public_id>/cancel", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_cancel(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    """Cancel a draft or issued invoice with a reason: record who and when,
    move its version once and write its audit event. Every line and a number,
    if any, are kept; the invoice is read-only from then on.

    Safe on replay: an invoice that is already cancelled is left exactly as it
    is. Cancellation needs no active Enrollment, Student, academic chain, plan
    or assignment -- it is how an Administrator corrects a charge.
    """
    context = _context_or_404(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = _invoice_or_404(context, invoice_public_id)
    detail_url = _detail_url(context, invoice.public_id)
    if invoice.status == _CANCELLED:
        flash(_ALREADY_CANCELLED_MESSAGE, "info")
        return redirect(detail_url)

    form = InvoiceReasonForm(
        formdata=request.form if request.method == "POST" else None,
        reason_required=True,
        missing_message=_CANCEL_REASON_MISSING,
    )
    if request.method == "GET":
        return _render_cancel(context, invoice, form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_CANCEL, **_invoice_state(actor_public_id, invoice)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    confirmed = request.form.get("confirm") == "yes"
    if not form.validate_on_submit() or not confirmed:
        return _render_cancel(
            context, invoice, form, None if confirmed else _CANCEL_CONFIRM_MESSAGE
        )
    invoice_id = invoice.id

    locks = _lock(context, actor_id, invoice_id=invoice_id)
    locked = _locked_invoice_or_404(locks, context, invoice_id, invoice_public_id)
    if _hierarchy_moved(locks, context):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    if locked.status == _CANCELLED:
        return _reject(_ALREADY_CANCELLED_MESSAGE, detail_url, actor_id, "info")
    if tokens.token_is_stale(
        token, tokens.PURPOSE_CANCEL, **_invoice_state(actor_public_id, locked)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)

    rows = invoice_rows_for_snapshot(locked)
    moment = _write_moment()
    try:
        before = build_invoice_snapshot(locked, context.assignment_public_id, rows)
        version_before = locked.version
        locked.status = _CANCELLED
        locked.cancelled_at = moment
        locked.cancelled_by_id = actor_id
        locked.version = version_before + 1
        locked.updated_at = moment
        _record_and_commit(
            context,
            locks,
            locked,
            EVENT_CANCELLED,
            version_before,
            before,
            rows,
            form.normalized_reason,
            moment,
        )
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    flash(_CANCELLED_OK_MESSAGE, "success")
    return redirect(detail_url)
