"""Administrator Invoices workspace actions (Phase 5 / M10).

Two URL rules beside the Invoice Register (``GET /admin/invoices``)::

    GET|POST  /admin/invoices/new[?student=][&enrollment=][&plan=]
    GET|POST  /admin/invoices/<ip>/delete

**New invoice** is a guided flow -- Student, then one of their active
Enrollments (and so the Group), then a Fee Plan, then a confirmation page --
that ends in **one** validated transaction:

- it reuses the Enrollment's ``assigned`` fee assignment when the chosen plan
  is the one it already uses, or creates the assignment -- cancelling a
  different, current assignment only when that one holds no live open invoice;
- it creates a draft invoice copying the plan's active items as its own lines;
- it writes the draft's ``invoice_draft_created`` event (a fee assignment has
  no event trail of its own: its row records who assigned and who cancelled
  it, exactly as M03 writes it);
- and it redirects to the existing invoice page, where lines stay edited on
  their own pages.

Every step's page is a preview. The POST re-proves everything against the
rows it locked, in the M03 order extended by M04's last step::

    AcademicTerm -> Level -> Course -> Group -> Student -> Enrollment
    -> acting Administrator -> FeePlan -> active FeePlanItems
    -> the Enrollment's StudentFeeAssignments -> the current assignment's Invoices

The Enrollment lock serializes the flow against fee assignment, assignment
cancellation, draft creation and Enrollment withdrawal of the same
Enrollment. The existing rules are kept: an inactive Student, Enrollment or
academic chain, an unavailable plan, a plan without a valid item set and a
live open invoice all refuse, with nothing written. The client's ids are never
trusted: the Enrollment must belong to the chosen Student, and the plan must
be assignable (or be the current assignment's, still invoiceable).

**Delete invoice** is a visible, tombstone deletion with a required reason and
confirmation (``app/services/financial_deletions.py``). A draft or an issued
invoice without live payments is deleted alone; an issued invoice with live
payments is deleted with its **whole document family** -- every live
transaction and receipt, each with its own event -- in one transaction, after
the page has listed exactly what will go. A cancelled invoice is never
deleted, and nothing is deleted while a payment intent is active. The lock
order is M05's with M07's intents, then the receipts::

    ... -> Invoice -> its live PaymentTransactions -> its PaymentIntents
    -> the live Receipts of those transactions

Every mutation is POST-only, CSRF-protected and bound to a signed stale-state
token (``app/services/financial_workspace_tokens.py``). An
``IntegrityError`` or a refused event is rolled back, the actor re-authorized,
and one generic sentence shown. Only an active Administrator reaches these
pages; every response is ``private, no-store`` with ``Vary: Cookie``.
"""

import uuid

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from wtforms import TextAreaField
from wtforms.validators import ValidationError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_assignments import _BLOCK_MESSAGES as _ASSIGN_BLOCK_MESSAGES
from app.blueprints.admin.fee_plans import _financial_response, _reject, _tz_name, _write_moment
from app.blueprints.admin.invoices import _BLOCK_MESSAGES as _DRAFT_BLOCK_MESSAGES
from app.blueprints.admin.invoices import (
    _INTEGRITY_MESSAGE,
    _STATE_FIELD,
    _context_or_404,
    _hierarchy_moved,
    _locked_invoice_or_404,
)
from app.blueprints.admin.payments import _lock as _payment_lock
from app.extensions import db
from app.models import (
    INVOICE_AUDIT_REASON_MAX_LENGTH,
    FeePlan,
    Invoice,
    InvoiceItem,
    InvoiceItemStatus,
    InvoiceStatus,
    StudentFeeAssignment,
    StudentFeeAssignmentStatus,
    UserRole,
)
from app.models.fee_plan import TEXT_CONTROL, TEXT_MISSING, TEXT_TOO_LONG
from app.models.payment_audit_event import normalize_audit_reason
from app.security.decorators import roles_required
from app.services import financial_workspace_queries as flow
from app.services import financial_workspace_tokens as tokens
from app.services import money
from app.services.fee_plan_queries import normalize_page
from app.services.fee_plan_transactions import administrator_authz_broken, locked_active_items
from app.services.financial_deletions import (
    BLOCK_CANCELLED,
    BLOCK_DELETED,
    BLOCK_INTENT_ACTIVE,
    delete_invoice,
    delete_payments,
    family_targets,
    invoice_delete_block,
    invoice_locator,
    lock_live_receipts,
)
from app.services.invoice_audit import CREATED as EVENT_CREATED
from app.services.invoice_audit import build_invoice_snapshot, record_invoice_event
from app.services.invoice_queries import (
    BLOCK_OPEN_INVOICE,
    BLOCK_PLAN_UNAVAILABLE,
    STATUS_LABELS,
    active_lines,
    assignment_invoice,
    invoice_lines,
)
from app.services.invoice_transactions import (
    fee_plan_invoiceable,
    invoice_rows_for_snapshot,
    lock_assignment_invoices,
    open_invoices,
)
from app.services.payment_intent_queries import invoice_intent_rows
from app.services.payment_queries import KIND_LABELS, METHOD_LABELS
from app.services.payment_queries import STATUS_LABELS as PAYMENT_STATUS_LABELS
from app.services.payment_queries import invoice_payment_rows, receipts_by_payment
from app.services.payment_transactions import (
    locked_invoice_payments,
    locked_payment_chain_intents,
    payment_balance,
    payment_rows_over_bound,
)
from app.services.schedule_occurrences import to_app_local
from app.services.student_fee_assignment_queries import (
    assignment_block_reason,
    build_plan_confirmation_view,
    fee_plan_active_items,
)
from app.services.student_fee_assignment_transactions import (
    assigned_rows,
    enrollment_nesting_broken,
    fee_plan_assignable,
    fee_plan_items_valid,
    hierarchy_moved,
    latest_change,
    lock_fee_assignment_chain,
    locked_academic_statuses,
    locked_enrollment_assignments,
)

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_DRAFT = InvoiceStatus.DRAFT.value
_ISSUED = InvoiceStatus.ISSUED.value
_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value
_ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
_CANCELLED_ASSIGNMENT = StudentFeeAssignmentStatus.CANCELLED.value


# ======================================================================
# Administrator-facing sentences
# ======================================================================

_STALE_MESSAGE = (
    "This student's enrollment, fee assignment or invoices changed after this page was opened, "
    "or the page has expired. Nothing was saved. Please review the current state and try again."
)
_DELETE_STALE_MESSAGE = (
    "This invoice or its payments changed after this page was opened, or the page has expired. "
    "Nothing was deleted. Please review the current state and try again."
)
_CONFIRM_CREATE_MESSAGE = "Please tick the confirmation box before creating the invoice."
_CONFIRM_DELETE_MESSAGE = "Please tick the confirmation box before deleting."
_ENROLLMENT_NOT_INVOICEABLE = (
    "This enrollment, or its group, course, level or academic term, is not active, so a new "
    "invoice cannot be created for it."
)
_PLAN_ITEMS_INVALID_MESSAGE = (
    "This fee plan does not have a valid set of items, so an invoice cannot be copied from it."
)
_CREATED_MESSAGE = (
    "Draft invoice created from the fee plan “{plan}”. Review its lines, then issue it when it "
    "is correct."
)
_DELETE_BLOCK_MESSAGES = {
    BLOCK_DELETED: "This invoice has already been deleted. Nothing was changed.",
    BLOCK_CANCELLED: (
        "This invoice is cancelled. A cancelled invoice is kept as read-only history and is "
        "never deleted."
    ),
    BLOCK_INTENT_ACTIVE: (
        "This invoice has an active online payment intent, so it cannot be deleted until the "
        "intent is cancelled, fails or is confirmed by its signed webhook. Nothing was changed."
    ),
}
_DELETE_BALANCE_MESSAGE = (
    "This invoice's payment records do not describe a valid balance, so it cannot be deleted "
    "here. Nothing was changed."
)
_DELETED_MESSAGE = "Invoice deleted. It is kept as a read-only record in Deleted Records."
_FAMILY_DELETED_MESSAGE = (
    "Invoice deleted with {payments} payment(s) and {receipts} receipt(s). They are kept as "
    "read-only records in Deleted Records and no longer count in any balance or report."
)
_REASON_MESSAGES = {
    TEXT_MISSING: "Give the reason for deleting.",
    TEXT_CONTROL: "The reason may only contain ordinary text and line breaks.",
    TEXT_TOO_LONG: f"The reason must be at most {INVOICE_AUDIT_REASON_MAX_LENGTH} characters.",
}


class DeletionReasonForm(FlaskForm):
    """The required reason for a visible deletion. Nothing else is
    submitted; the confirmation box is read separately."""

    reason = TextAreaField("Reason for deleting")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.normalized_reason = None

    def validate_reason(self, field):
        value, error = normalize_audit_reason(field.data)
        if error is not None:
            raise ValidationError(_REASON_MESSAGES[error])
        self.normalized_reason = value


def _local(moment):
    return None if moment is None else to_app_local(_tz_name(), moment)


# ======================================================================
# New invoice
# ======================================================================


def _step_url(student=None, enrollment=None, plan=None, **extra):
    args = {key: value for key, value in (("student", student), ("enrollment", enrollment),
                                          ("plan", plan)) if value}
    return url_for("admin.invoice_workspace_new", **args, **extra)


def _render_students():
    search = flow.normalize_search(request.args.get("q"))
    rows, total, page = flow.active_students_page(search, normalize_page(request.args.get("page")))
    filters = {"q": search} if search else {}
    first = (page - 1) * flow.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * flow.PAGE_SIZE + len(rows)
    return render_template(
        "admin/invoices/new_student.html",
        students=[
            {"full_name": row.full_name, "email": row.email, "select_url": _step_url(row.public_id)}
            for row in rows
        ],
        search=search,
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": _step_url(page=page - 1, **filters) if page > 1 else None,
            "next_url": _step_url(page=page + 1, **filters) if last < total else None,
        },
        register_url=url_for("admin.invoice_register"),
    )


def _render_enrollments(student):
    rows, truncated = flow.student_enrollments(student.id)
    open_invoices_by_assignment = flow.assignments_with_live_open_invoice(
        [row.assignment_id for row in rows]
    )
    enrollments = []
    for row in rows:
        open_invoice = open_invoices_by_assignment.get(row.assignment_id)
        invoiceable = flow.enrollment_is_invoiceable(row)
        entry = {
            "group_name": row.group_name,
            "group_code": row.group_code,
            "course_title": row.course_title,
            "term_name": row.term_name,
            "plan_name": row.plan_name,
            "invoiceable": invoiceable and open_invoice is None,
            "blocked_reason": None if invoiceable else _ENROLLMENT_NOT_INVOICEABLE,
            "open_invoice_url": None,
            "select_url": _step_url(student.public_id, row.enrollment_public_id),
        }
        if invoiceable and open_invoice is not None:
            entry["blocked_reason"] = (
                "This enrollment's fee assignment already has a draft or issued invoice."
            )
            entry["open_invoice_url"] = url_for(
                "admin.invoice_detail",
                group_public_id=row.group_public_id,
                enrollment_public_id=row.enrollment_public_id,
                assignment_public_id=row.assignment_public_id,
                invoice_public_id=open_invoice,
            )
        enrollments.append(entry)
    return render_template(
        "admin/invoices/new_enrollment.html",
        student={"full_name": student.full_name, "email": student.email},
        enrollments=enrollments,
        truncated=truncated,
        back_url=_step_url(),
    )


def _preview_block(context, current):
    """What refuses a new invoice for `context` on the flow's pages, as a
    sentence, or ``None``. The POST decides again against locked rows."""
    block = assignment_block_reason(
        context.enrollment_status,
        context.student_status,
        (context.group_status, context.course_status, context.level_status, context.term_status),
        has_assigned_plan=False,
    )
    if block is not None:
        return _ASSIGN_BLOCK_MESSAGES[block]
    if current is not None and flow.assignments_with_live_open_invoice([current.id]):
        return _DRAFT_BLOCK_MESSAGES[BLOCK_OPEN_INVOICE]
    return None


def _render_plans(student, context, current):
    page = normalize_page(request.args.get("page"))
    rows, has_next = flow.assignable_plans(page)
    if not rows and page > 1:
        page = 1
        rows, has_next = flow.assignable_plans(page)
    plans = flow.plan_choice_view(rows)
    reused = None
    if current is not None:
        plan = db.session.get(FeePlan, current.fee_plan_id)
        if plan is not None and fee_plan_invoiceable(plan):
            reused = {
                "name": plan.name,
                "select_url": _step_url(
                    student.public_id, context.enrollment_public_id, plan.public_id
                ),
            }
    for plan in plans:
        plan["select_url"] = (
            _step_url(student.public_id, context.enrollment_public_id, plan["public_id"])
            if plan["selectable"]
            else None
        )
    return render_template(
        "admin/invoices/new_plan.html",
        student={"full_name": student.full_name, "email": student.email},
        context=context,
        plans=plans,
        reused=reused,
        replaces_assignment=current is not None,
        page=page,
        prev_url=_step_url(student.public_id, context.enrollment_public_id, page=page - 1)
        if page > 1
        else None,
        next_url=_step_url(student.public_id, context.enrollment_public_id, page=page + 1)
        if has_next
        else None,
        back_url=_step_url(student.public_id),
        currency_code=money.CURRENCY_CODE,
    )


def _create_state(actor_public_id, student_public_id, context, plan):
    return {
        "actor_public_id": actor_public_id,
        "student_public_id": student_public_id,
        "enrollment_public_id": context.enrollment_public_id,
        "plan_public_id": plan.public_id,
        "plan_version": plan.version,
    }


def _render_confirm(student, context, current, plan, items, confirm_error=None):
    reuse = current is not None and current.fee_plan_id == plan.id
    return render_template(
        "admin/invoices/new_confirm.html",
        student={"full_name": student.full_name, "email": student.email},
        context=context,
        plan=build_plan_confirmation_view(plan, items),
        assignment_action="reuse" if reuse else ("replace" if current is not None else "create"),
        confirm_error=confirm_error,
        action_url=url_for("admin.invoice_workspace_new"),
        state_field=_STATE_FIELD,
        state_token=tokens.make_token(
            tokens.PURPOSE_INVOICE_CREATE,
            **_create_state(current_user.public_id, student.public_id, context, plan),
        ),
        hidden={
            "student": student.public_id,
            "enrollment": context.enrollment_public_id,
            "plan": plan.public_id,
        },
        back_url=_step_url(student.public_id, context.enrollment_public_id),
        currency_code=money.CURRENCY_CODE,
    )


def _resolve(student_public_id, enrollment_public_id):
    """``(student, context, current assignment)`` for the flow's ids, or a
    404 -- the Enrollment must belong to the chosen active Student."""
    student = flow.active_student(student_public_id)
    if student is None:
        abort(404)
    context = flow.workspace_context(student.public_id, enrollment_public_id)
    if context is None:
        abort(404)
    return student, context, flow.current_assignment(context.enrollment_id)


@admin_bp.route("/invoices/new", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_workspace_new():
    """GET: the flow's current step. POST: create the draft -- and, where
    needed, the fee assignment -- in one transaction."""
    if request.method == "POST":
        return _create_invoice()
    student_public_id = request.args.get("student")
    if not student_public_id:
        return _render_students()
    student = flow.active_student(student_public_id)
    if student is None:
        abort(404)
    enrollment_public_id = request.args.get("enrollment")
    if not enrollment_public_id:
        return _render_enrollments(student)
    student, context, current = _resolve(student_public_id, enrollment_public_id)
    block = _preview_block(context, current)
    if block is not None:
        flash(block, "warning")
        return redirect(_step_url(student.public_id))
    plan_public_id = request.args.get("plan")
    if not plan_public_id:
        return _render_plans(student, context, current)
    plan = flow.chosen_plan(plan_public_id, None if current is None else current.fee_plan_id)
    if plan is None:
        abort(404)
    items = fee_plan_active_items(plan.id)
    if not fee_plan_items_valid(items):
        flash(_PLAN_ITEMS_INVALID_MESSAGE, "warning")
        return redirect(_step_url(student.public_id, context.enrollment_public_id))
    return _render_confirm(student, context, current, plan, items)


def _create_invoice():
    student, context, current = _resolve(request.form.get("student"), request.form.get("enrollment"))
    plan = flow.chosen_plan(
        request.form.get("plan"), None if current is None else current.fee_plan_id
    )
    if plan is None:
        abort(404)
    back_url = _step_url(student.public_id, context.enrollment_public_id)
    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    bound = _create_state(actor_public_id, student.public_id, context, plan)
    if tokens.token_is_stale(token, tokens.PURPOSE_INVOICE_CREATE, **bound):
        return _reject(_STALE_MESSAGE, back_url, actor_id)
    if request.form.get("confirm") != "yes":
        return _render_confirm(
            student, context, current, plan, fee_plan_active_items(plan.id),
            _CONFIRM_CREATE_MESSAGE,
        )
    plan_id, student_public_id = plan.id, student.public_id

    locks = lock_fee_assignment_chain(
        context.group_public_id,
        context.academic_term_id,
        context.level_id,
        context.course_id,
        context.student_id,
        context.enrollment_id,
        actor_id,
        plan_id=plan_id,
        include_plan_items=True,
        include_enrollment_assignments=True,
    )
    if (
        administrator_authz_broken(locks)
        or enrollment_nesting_broken(locks, context.group_public_id, context.enrollment_id)
        or locks.student.public_id != student_public_id
        or locks.plan is None
        or locks.plan.id != plan_id
    ):
        db.session.rollback()
        abort(404)
    if hierarchy_moved(locks, context.academic_term_id, context.level_id, context.course_id):
        return _reject(_STALE_MESSAGE, back_url, actor_id)
    existing = locked_enrollment_assignments(locks)
    assigned = assigned_rows(existing)
    current_locked = assigned[0] if assigned else None
    invoices = []
    if current_locked is not None:
        invoices = [
            row
            for row in lock_assignment_invoices(current_locked.id).values()
            if row is not None and row.student_fee_assignment_id == current_locked.id
        ]
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_INVOICE_CREATE,
        changed_at=latest_change(list(existing) + invoices),
        **dict(bound, plan_version=locks.plan.version),
    ):
        return _reject(_STALE_MESSAGE, back_url, actor_id)

    block = assignment_block_reason(
        locks.enrollment.status,
        locks.student.status,
        locked_academic_statuses(
            locks, context.academic_term_id, context.level_id, context.course_id
        ),
        has_assigned_plan=False,
    )
    if block is not None:
        return _reject(_ASSIGN_BLOCK_MESSAGES[block], back_url, actor_id, "warning")
    if len(assigned) > 1:
        return _reject(_INTEGRITY_MESSAGE, back_url, actor_id)
    reuse = current_locked is not None and current_locked.fee_plan_id == locks.plan.id
    if not (fee_plan_invoiceable(locks.plan) if reuse else fee_plan_assignable(locks.plan)):
        return _reject(_DRAFT_BLOCK_MESSAGES[BLOCK_PLAN_UNAVAILABLE], back_url, actor_id, "warning")
    if open_invoices(invoices):
        return _reject(_DRAFT_BLOCK_MESSAGES[BLOCK_OPEN_INVOICE], back_url, actor_id, "warning")
    plan_items = locked_active_items(locks)
    if not fee_plan_items_valid(plan_items):
        return _reject(_PLAN_ITEMS_INVALID_MESSAGE, back_url, actor_id, "warning")

    moment = _write_moment()
    try:
        if reuse:
            assignment = current_locked
        else:
            if current_locked is not None:
                # The one assigned plan per Enrollment: the current, different
                # assignment -- which holds no live open invoice -- is
                # cancelled and kept as history, exactly as its own page would.
                current_locked.status = _CANCELLED_ASSIGNMENT
                current_locked.cancelled_at = moment
                current_locked.cancelled_by_id = actor_id
                current_locked.version = current_locked.version + 1
                current_locked.updated_at = moment
                db.session.flush()
            assignment = StudentFeeAssignment(
                public_id=str(uuid.uuid4()),
                enrollment_id=locks.enrollment.id,
                fee_plan_id=locks.plan.id,
                status=_ASSIGNED,
                assigned_at=moment,
                assigned_by_id=actor_id,
                cancelled_at=None,
                cancelled_by_id=None,
                version=1,
                created_at=moment,
                updated_at=moment,
            )
            db.session.add(assignment)
            db.session.flush()
        invoice = Invoice(
            public_id=str(uuid.uuid4()),
            student_fee_assignment_id=assignment.id,
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
        record_invoice_event(
            invoice=invoice,
            actor=locks.actor,
            kind=EVENT_CREATED,
            version_before=None,
            before_snapshot=None,
            after_snapshot=build_invoice_snapshot(invoice, assignment.public_id, lines),
            reason=None,
            moment=moment,
        )
        detail_url = url_for(
            "admin.invoice_detail",
            group_public_id=context.group_public_id,
            enrollment_public_id=context.enrollment_public_id,
            assignment_public_id=assignment.public_id,
            invoice_public_id=invoice.public_id,
        )
        plan_name = locks.plan.name
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, back_url, actor_id)

    flash(_CREATED_MESSAGE.format(plan=plan_name), "success")
    return redirect(detail_url)


# ======================================================================
# Delete invoice
# ======================================================================


def _family_view(rows):
    receipts = receipts_by_payment([row.id for row in rows])
    view = []
    for row in sorted(rows, key=lambda item: item.id):
        receipt = receipts.get(row.id)
        live_receipt = receipt if receipt is not None and receipt.deleted_at is None else None
        view.append(
            {
                "recorded_local": _local(row.recorded_at),
                "kind_label": KIND_LABELS.get(row.kind, row.kind),
                "method_label": METHOD_LABELS.get(row.method, row.method),
                "status_label": PAYMENT_STATUS_LABELS.get(row.status, row.status),
                "amount_text": money.format_amount(row.amount),
                "receipt_number": None if live_receipt is None else live_receipt.receipt_number,
            }
        )
    return view


def _delete_state(actor_public_id, invoice, rows):
    return {
        "actor_public_id": actor_public_id,
        "invoice_public_id": invoice.public_id,
        "invoice_version": invoice.version,
        "payment_state": tokens.payment_state(rows),
    }


def _render_delete(context, invoice, rows, form, confirm_error=None):
    lines = invoice_lines(invoice.id)
    active = active_lines(lines)
    family = _family_view(rows)
    return render_template(
        "admin/invoices/delete.html",
        form=form,
        invoice={
            "public_id": invoice.public_id,
            "number": invoice.invoice_number,
            "status_label": STATUS_LABELS.get(invoice.status, invoice.status),
            "is_issued": invoice.status == _ISSUED,
            "total_text": money.format_amount(money.sum_amounts([line.amount for line in active])),
            "currency_code": invoice.currency_code,
            "student_name": context.student_name,
            "group_name": context.group_name,
            "plan_name": context.plan_name,
        },
        family=family,
        receipt_count=sum(1 for entry in family if entry["receipt_number"]),
        confirm_error=confirm_error,
        action_url=url_for("admin.invoice_workspace_delete", invoice_public_id=invoice.public_id),
        detail_url=url_for(
            "admin.invoice_detail",
            group_public_id=context.group_public_id,
            enrollment_public_id=context.enrollment_public_id,
            assignment_public_id=context.assignment_public_id,
            invoice_public_id=invoice.public_id,
        ),
        state_field=_STATE_FIELD,
        state_token=tokens.make_token(
            tokens.PURPOSE_INVOICE_DELETE,
            **_delete_state(current_user.public_id, invoice, rows),
        ),
        reason_max=INVOICE_AUDIT_REASON_MAX_LENGTH,
    )


@admin_bp.route("/invoices/<invoice_public_id>/delete", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_workspace_delete(invoice_public_id):
    """GET: exactly what the deletion removes -- the invoice and, for an issued
    invoice, every live payment and receipt. POST: delete them all, with the
    reason, in one transaction."""
    located = invoice_locator(invoice_public_id)
    if located is None:
        abort(404)
    context = _context_or_404(
        located.group_public_id, located.enrollment_public_id, located.assignment_public_id
    )
    invoice = assignment_invoice(context.assignment_id, invoice_public_id)
    if invoice is None:
        abort(404)
    register_url = url_for("admin.invoice_register")
    rows = invoice_payment_rows(invoice.id)
    block = invoice_delete_block(invoice, invoice_intent_rows(invoice.id))
    form = DeletionReasonForm(formdata=request.form if request.method == "POST" else None)
    if request.method == "GET":
        if block is not None:
            flash(_DELETE_BLOCK_MESSAGES[block], "warning")
            return redirect(register_url)
        return _render_delete(context, invoice, rows, form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_INVOICE_DELETE, **_delete_state(actor_public_id, invoice, rows)
    ):
        return _reject(_DELETE_STALE_MESSAGE, register_url, actor_id)
    if block is not None:
        return _reject(_DELETE_BLOCK_MESSAGES[block], register_url, actor_id, "warning")
    confirmed = request.form.get("confirm") == "yes"
    if not form.validate_on_submit() or not confirmed:
        return _render_delete(
            context, invoice, rows, form, None if confirmed else _CONFIRM_DELETE_MESSAGE
        )
    reason = form.normalized_reason
    invoice_id = invoice.id

    locks = _payment_lock(
        context, actor_id, invoice_id, include_invoice_payments=True, include_invoice_intents=True
    )
    locked = _locked_invoice_or_404(locks.chain, context, invoice_id, invoice_public_id)
    if _hierarchy_moved(locks.chain, context):
        return _reject(_DELETE_STALE_MESSAGE, register_url, actor_id)
    payments = locked_invoice_payments(locks)
    receipts = lock_live_receipts([row.id for row in payments])
    if tokens.token_is_stale(
        token, tokens.PURPOSE_INVOICE_DELETE, **_delete_state(actor_public_id, locked, payments)
    ):
        return _reject(_DELETE_STALE_MESSAGE, register_url, actor_id)
    block = invoice_delete_block(locked, locked_payment_chain_intents(locks))
    if block is not None:
        return _reject(_DELETE_BLOCK_MESSAGES[block], register_url, actor_id, "warning")
    item_rows = invoice_rows_for_snapshot(locked)
    active = active_lines(item_rows)
    if payments and (
        payment_rows_over_bound(payments) or payment_balance(active, payments) is None
    ):
        return _reject(_DELETE_BALANCE_MESSAGE, register_url, actor_id, "warning")

    moment = _write_moment()
    try:
        if payments:
            delete_payments(
                invoice=locked,
                actor=locks.chain.actor,
                active_items=active,
                payments=payments,
                targets=family_targets(payments),
                receipts=receipts,
                reason=reason,
                moment=moment,
            )
        delete_invoice(
            invoice=locked,
            actor=locks.chain.actor,
            assignment_public_id=context.assignment_public_id,
            items=item_rows,
            reason=reason,
            moment=moment,
        )
        db.session.commit()
    except (IntegrityError, ValueError):
        return _reject(_INTEGRITY_MESSAGE, register_url, actor_id)

    if payments:
        flash(
            _FAMILY_DELETED_MESSAGE.format(payments=len(payments), receipts=len(receipts)),
            "success",
        )
    else:
        flash(_DELETED_MESSAGE, "success")
    return redirect(register_url)
