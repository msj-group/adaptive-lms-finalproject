"""Administrator Student Accounts (Phase 5 / M10).

Three read-only GET rules::

    GET  /admin/student-accounts[?q=][&page=]                     every Student
    GET  /admin/student-accounts/<sp>/financial-record            one Student
    GET  /admin/billing-desk                                      compatibility

Student Accounts replaces the M09 Billing Desk as the per-Student finance
workspace; ``/admin/billing-desk`` survives only as a redirect to it, so an
old bookmark still lands somewhere useful.

**The financial status is computed, never stored or propagated.** See
``app/services/student_account_queries.py``: the five labels are derived on
every request from live invoices, lines and payments, and nothing here writes
anything -- no User, Enrollment, Group or academic status ever changes. A
POST, PUT, PATCH or DELETE is a 405.

**The Financial Record** shows the Student's live invoices across every
Enrollment and Group, their totals and -- where valid -- paid and outstanding
amounts, and every live payment and receipt in chronological order, each
linked to its existing invoice and receipt page built from the row's own
public ids. Deleted documents count for nothing; a small link opens Deleted
Records filtered to the Student.

Only an active Administrator reaches these pages. Every response carries
``Cache-Control: private, no-store`` and ``Vary: Cookie``.
"""

from flask import abort, redirect, render_template, request, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plans import _financial_response, _tz_name
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import money
from app.services import student_account_queries as accounts
from app.services.fee_plan_queries import normalize_page
from app.services.invoice_register_queries import ALLOCATION_NOTE

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value


def _invoice_ids(entry):
    return {
        "group_public_id": entry["group_public_id"],
        "enrollment_public_id": entry["enrollment_public_id"],
        "assignment_public_id": entry["assignment_public_id"],
        "invoice_public_id": entry.get("invoice_public_id") or entry["public_id"],
    }


@admin_bp.get("/billing-desk")
@roles_required(_ADMINISTRATOR)
@_financial_response
def billing_desk():
    """The M09 Billing Desk is now Student Accounts."""
    return redirect(url_for("admin.student_accounts"))


@admin_bp.get("/student-accounts")
@roles_required(_ADMINISTRATOR)
@_financial_response
def student_accounts():
    """One page of Students with their computed financial status."""
    search = accounts.normalize_search(request.args.get("q"))
    rows, total, page = accounts.students_page(search, normalize_page(request.args.get("page")))
    by_student, facts = accounts.open_invoice_facts([row.id for row in rows])
    students = accounts.build_accounts_view(rows, by_student, facts, money.CURRENCY_CODE)
    for student in students:
        student["record_url"] = url_for(
            "admin.student_financial_record", student_public_id=student["public_id"]
        )
    filters = {"q": search} if search else {}
    first = (page - 1) * accounts.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * accounts.PAGE_SIZE + len(rows)
    return render_template(
        "admin/student_accounts/index.html",
        students=students,
        search=search,
        filtered=bool(search),
        status_labels=accounts.FINANCIAL_STATUS_LABELS,
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": url_for("admin.student_accounts", page=page - 1, **filters)
            if page > 1
            else None,
            "next_url": url_for("admin.student_accounts", page=page + 1, **filters)
            if last < total
            else None,
        },
    )


@admin_bp.get("/student-accounts/<student_public_id>/financial-record")
@roles_required(_ADMINISTRATOR)
@_financial_response
def student_financial_record(student_public_id):
    """Every live financial document of one Student, across all of their
    Enrollments and Groups."""
    student = accounts.account_student(student_public_id)
    if student is None:
        abort(404)
    tz_name = _tz_name()
    status_rows, status_facts = accounts.student_status_facts(student.id)
    status = accounts.record_status(status_rows, status_facts, money.CURRENCY_CODE)
    invoice_rows, invoices_truncated = accounts.record_invoices(student.id)
    invoices = accounts.build_record_invoices_view(
        invoice_rows, accounts.record_invoice_facts(invoice_rows), tz_name
    )
    for invoice in invoices:
        ids = _invoice_ids(invoice)
        invoice["detail_url"] = url_for("admin.invoice_detail", **ids)
        invoice["payments_url"] = (
            url_for("admin.invoice_payments", **ids) if invoice["status"] == "issued" else None
        )
    payment_rows, payments_truncated = accounts.record_payments(student.id)
    payments = accounts.build_record_payments_view(payment_rows, tz_name)
    for payment in payments:
        ids = _invoice_ids(payment)
        payment["invoice_url"] = url_for("admin.invoice_detail", **ids)
        payment["receipt_url"] = (
            url_for(
                "admin.invoice_receipt_detail",
                receipt_public_id=payment["receipt_public_id"],
                **ids,
            )
            if payment["receipt_public_id"]
            else None
        )
    return render_template(
        "admin/student_accounts/record.html",
        student={
            "public_id": student.public_id,
            "full_name": student.full_name,
            "email": student.email,
            "account_status_label": accounts.ACCOUNT_STATUS_LABELS.get(
                student.status, student.status
            ),
        },
        status=status,
        invoices=invoices,
        invoices_truncated=invoices_truncated,
        payments=payments,
        payments_truncated=payments_truncated,
        allocation_note=ALLOCATION_NOTE,
        deleted_url=url_for("admin.deleted_financial_records", q=student.email),
        new_invoice_url=url_for("admin.invoice_workspace_new", student=student.public_id)
        if student.status == "active"
        else None,
        accounts_url=url_for("admin.student_accounts"),
        currency_code=money.CURRENCY_CODE,
        tz_name=tz_name,
    )
