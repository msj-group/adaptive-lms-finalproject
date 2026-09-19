"""Administrator Billing Desk (Phase 5 / M09).

One read-only GET rule::

    GET  /admin/billing-desk                        choose a Group
    GET  /admin/billing-desk?group=<gp>[&q=][&page=] its enrolled Students
    GET  /admin/billing-desk?group=<gp>&student=<sp> one Student's billing

**A guide to the existing pages, never a writer.** The desk finds the selected
Enrollment's fee assignment and invoice, states them, and links the one next
step for the current state to its existing route -- fee plan choice, draft
invoice creation, the invoice, cash, bank transfer, payments and receipts, or
payment intents. It creates, issues, edits, cancels, confirms, rejects,
reverses or deletes nothing; it has no form that posts, and every linked route
still re-proves everything after its own locks. A POST, PUT, PATCH or DELETE is
a 405.

**Selection is by public id**, validated here: the Group must exist, and the
Student must be a Student-role account enrolled in that Group. Anything else --
unknown, malformed, numeric, foreign, mismatched or repeated -- is a plain 404.
A withdrawn Enrollment shows its history only.

**The rules are the existing ones.** Assigning uses the fee assignment page's
own preview (``context_block_reason``) and creating a draft the invoice page's
(``context_draft_block_reason``); the payment shortcuts use the payments page's
own ``_pre_lock_state``, ``_payable_block`` and ``_record_block``, imported from
it as that page imports its neighbours' helpers. Payments are invoice-wide: the
registration and course subtotals only explain the invoice.

Only an active Administrator reaches the desk. Every response carries
``Cache-Control: private, no-store`` and ``Vary: Cookie``.
"""

from flask import abort, render_template, request, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_assignments import _BLOCK_MESSAGES as _ASSIGN_BLOCK_MESSAGES
from app.blueprints.admin.fee_plans import _financial_response
from app.blueprints.admin.invoices import _BLOCK_MESSAGES as _DRAFT_BLOCK_MESSAGES
from app.blueprints.admin.payments import _payable_block, _pre_lock_state, _record_block
from app.models import EnrollmentStatus, InvoiceStatus, UserRole
from app.security.decorators import roles_required
from app.services import billing_desk_queries as desk
from app.services.fee_plan_queries import normalize_page
from app.services.financial_reports import group_choices
from app.services.invoice_queries import STATUS_LABELS as INVOICE_STATUS_LABELS
from app.services.invoice_queries import assignment_invoice_context, context_draft_block_reason
from app.services.payment_intent_queries import (
    invoice_intent_rows,
    invoice_reconciliation_required,
)
from app.services.student_fee_assignment_queries import (
    context_block_reason,
    enrollment_fee_context,
)

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_DRAFT = InvoiceStatus.DRAFT.value

#: Each action key's label and existing route.
_ACTIONS = {
    desk.ASSIGN_PLAN: ("Assign fee plan", "admin.enrollment_fee_plan_choices"),
    desk.CREATE_DRAFT: ("Create draft invoice", "admin.assignment_invoice_create"),
    desk.OPEN_DRAFT: ("Open draft invoice", "admin.invoice_detail"),
    desk.RECORD_CASH: ("Record cash payment", "admin.invoice_payment_cash"),
    desk.RECORD_BANK: ("Record bank transfer", "admin.invoice_payment_bank_transfer"),
    desk.OPEN_PAYMENTS: ("Open payments and receipts", "admin.invoice_payments"),
    desk.OPEN_INVOICE: ("Open invoice", "admin.invoice_detail"),
    desk.OPEN_INTENTS: ("Open payment intents", "admin.invoice_payment_intents"),
    desk.FEE_HISTORY: ("Fee assignment history", "admin.enrollment_fee_assignments"),
    desk.INVOICE_HISTORY: ("Invoice history", "admin.assignment_invoices"),
}


def _single(name):
    """The one submitted value of `name` (``""`` when absent); a repeated
    identifier is a 404, never resolved by picking one."""
    values = request.args.getlist(name)
    if len(values) > 1:
        abort(404)
    return values[0] if values else ""


def _links(keys, **ids):
    """``[{label, url}]`` for action `keys`, each built from server-derived
    public ids for its existing route."""
    links = []
    for key in keys:
        label, endpoint = _ACTIONS[key]
        links.append({"key": key, "label": label, "url": url_for(endpoint, **ids)})
    return links


def _summary(context):
    """The selected Enrollment's billing stage, its facts and its links."""
    ids = {
        "group_public_id": context.group_public_id,
        "enrollment_public_id": context.enrollment_public_id,
    }
    history = _links([desk.FEE_HISTORY], **ids)

    def summary(stage, notes=(), actions=(), action_ids=None, **extra):
        return desk.build_summary(
            context, stage, notes, _links(actions, **(action_ids or ids)), history, **extra
        )

    if context.enrollment_status != _ENROLLMENT_ACTIVE:
        return summary(desk.WITHDRAWN)
    assigned, latest = desk.enrollment_assignments(context.enrollment_id)
    if len(assigned) > 1:
        return summary(desk.INCONSISTENT)
    if not assigned:
        if latest is not None:
            history += _links([desk.INVOICE_HISTORY], assignment_public_id=latest.public_id, **ids)
            return summary(desk.ASSIGNMENT_CANCELLED)
        block = context_block_reason(context)
        return summary(
            desk.NO_ASSIGNMENT,
            actions=[desk.ASSIGN_PLAN] if block is None else [],
            block_text=None if block is None else _ASSIGN_BLOCK_MESSAGES[block],
        )

    assignment_ids = dict(ids, assignment_public_id=assigned[0].public_id)
    history += _links([desk.INVOICE_HISTORY], **assignment_ids)
    assignment = assignment_invoice_context(**assignment_ids)
    if assignment is None:
        return summary(desk.INCONSISTENT)
    plan = {"name": assignment.plan_name}
    open_invoices, latest_invoice = desk.assignment_invoices(assignment.assignment_id)
    if len(open_invoices) > 1:
        return summary(desk.INCONSISTENT, plan=plan)
    if not open_invoices:
        if latest_invoice is not None:
            invoice = {
                "number": latest_invoice.invoice_number,
                "status_label": INVOICE_STATUS_LABELS[latest_invoice.status],
            }
            return summary(desk.INVOICE_CANCELLED, plan=plan, invoice=invoice)
        block = context_draft_block_reason(assignment)
        return summary(
            desk.NO_INVOICE,
            actions=[desk.CREATE_DRAFT] if block is None else [],
            action_ids=assignment_ids,
            plan=plan,
            block_text=None if block is None else _DRAFT_BLOCK_MESSAGES[block],
        )

    invoice = open_invoices[0]
    invoice_ids = dict(assignment_ids, invoice_public_id=invoice.public_id)
    invoice_view = {
        "number": invoice.invoice_number,
        "status_label": INVOICE_STATUS_LABELS[invoice.status],
    }
    if invoice.status == _DRAFT:
        return summary(
            desk.DRAFT, actions=[desk.OPEN_DRAFT], action_ids=invoice_ids, plan=plan,
            invoice=invoice_view,
        )
    lines, active, rows, balance = _pre_lock_state(invoice)
    intents = invoice_intent_rows(invoice.id)
    reconciliation = invoice_reconciliation_required(invoice.id)
    stage, notes = desk.issued_stage(
        balance,
        _payable_block(invoice, lines, active, rows, balance),
        _record_block(invoice, lines, active, rows, balance, intents),
        desk.pending_transfer_count(rows),
        reconciliation,
    )
    return summary(
        stage,
        notes=notes,
        actions=desk.stage_actions(stage, has_intents=bool(intents) or reconciliation),
        action_ids=invoice_ids,
        plan=plan,
        invoice=invoice_view,
        money=desk.build_money_view(balance, desk.kind_subtotals(active), invoice.currency_code),
    )


@admin_bp.get("/billing-desk")
@roles_required(_ADMINISTRATOR)
@_financial_response
def billing_desk():
    """Choose a Group, then one of its enrolled Students, then follow the one
    next step for that Student's fee assignment, invoice and payments."""
    raw_group, raw_student = _single("group"), _single("student")
    context = {"group_choices": group_choices(), "group": None, "students": None,
               "summary": None, "search": ""}
    if not raw_group:
        if raw_student:
            abort(404)
        return render_template("admin/billing_desk/index.html", **context)
    group = desk.desk_group(raw_group)
    if group is None:
        abort(404)
    context["group"] = desk.build_group_view(group)
    if raw_student:
        enrollment = desk.student_enrollment(group.id, raw_student)
        if enrollment is None:
            abort(404)
        selected = enrollment_fee_context(group.public_id, enrollment[0])
        if selected is None:
            abort(404)
        context["summary"] = _summary(selected)
        return render_template("admin/billing_desk/index.html", **context)

    search = desk.normalize_search(request.args.get("q"))
    rows, total, page = desk.enrolled_students_page(
        group.id, search, normalize_page(request.args.get("page"))
    )
    students = desk.build_student_list_view(
        rows, desk.list_states([row.enrollment_id for row in rows])
    )
    for student in students:
        student["url"] = url_for(
            "admin.billing_desk", group=group.public_id, student=student["student_public_id"]
        )
    first = (page - 1) * desk.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * desk.PAGE_SIZE + len(rows)
    query = {"group": group.public_id, **({"q": search} if search else {})}
    context.update(
        search=search,
        students=students,
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": url_for("admin.billing_desk", page=page - 1, **query) if page > 1 else None,
            "next_url": url_for("admin.billing_desk", page=page + 1, **query)
            if last < total
            else None,
        },
    )
    return render_template("admin/billing_desk/index.html", **context)
