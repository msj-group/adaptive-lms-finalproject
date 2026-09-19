"""Administrator Invoice Register (Phase 5 / M09R).

One read-only GET rule::

    GET  /admin/invoices[?status=][&payment=][&q=][&page=]

Every invoice, newest first, 25 per page with the exact range, filtered by
lifecycle status (``all``, ``draft``, ``issued``, ``cancelled``), by payment
state for issued invoices (``all``, ``outstanding``, ``paid``) and by a
literal, case-insensitive search of the invoice number, the Student's name and
email, and the Group's name and code. An unknown filter value is ``all`` and a
malformed page is page 1, as on the other Administrator lists; the filters are
kept across pages.

**A lookup, never a writer.** Each row links to its existing invoice page
and, for an issued invoice, to its payments and receipts page, built from the
row's own verified public ids. The register creates, issues, edits, cancels
and records nothing, has no form that posts and offers no collection
shortcut; the linked pages stay authoritative. A POST, PUT, PATCH or DELETE is
a 405. The Billing Desk (M09) remains the guided page for one Student.

Only an active Administrator reaches it. Every response carries
``Cache-Control: private, no-store`` and ``Vary: Cookie``.
"""

from flask import render_template, request, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plans import _financial_response
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import invoice_register_queries as register
from app.services.fee_plan_queries import normalize_page

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value


@admin_bp.get("/invoices")
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_register():
    """One page of every invoice matching the filters, each linked to its
    existing pages."""
    status, payment, search = register.normalize_filters(
        request.args.get("status"), request.args.get("payment"), request.args.get("q")
    )
    rows, facts, total, page = register.register_page(
        status, payment, search, normalize_page(request.args.get("page"))
    )
    active, reconciliation = register.attention(row.id for row in rows)
    invoices = register.build_register_view(rows, facts, active, reconciliation)
    for invoice in invoices:
        ids = {
            "group_public_id": invoice["group_public_id"],
            "enrollment_public_id": invoice["enrollment_public_id"],
            "assignment_public_id": invoice["assignment_public_id"],
            "invoice_public_id": invoice["public_id"],
        }
        invoice["detail_url"] = url_for("admin.invoice_detail", **ids)
        invoice["payments_url"] = (
            url_for("admin.invoice_payments", **ids) if invoice["is_issued"] else None
        )
    filters = {
        key: value
        for key, value in (("status", status), ("payment", payment), ("q", search))
        if value and value != register.ALL
    }
    first = (page - 1) * register.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * register.PAGE_SIZE + len(rows)
    return render_template(
        "admin/invoices/register.html",
        invoices=invoices,
        status=status,
        payment=payment,
        search=search,
        status_filters=register.STATUS_FILTERS,
        payment_filters=register.PAYMENT_FILTERS,
        filtered=bool(filters),
        allocation_note=register.ALLOCATION_NOTE,
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": url_for("admin.invoice_register", page=page - 1, **filters)
            if page > 1
            else None,
            "next_url": url_for("admin.invoice_register", page=page + 1, **filters)
            if last < total
            else None,
        },
    )
