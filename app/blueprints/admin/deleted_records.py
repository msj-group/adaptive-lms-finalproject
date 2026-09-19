"""Administrator Deleted Records (Phase 5 / M10).

Two read-only GET rules::

    GET  /admin/deleted-financial-records[?type=][&q=][&page=]
    GET  /admin/deleted-financial-records/<type>/<public_id>

Every visibly deleted invoice, payment transaction and receipt, newest
deletion first, 25 per page with the exact range, filtered by document type
and by a literal, case-insensitive search over the Student's name and email,
the invoice number and the receipt number. Each row shows only the document
type, its number or label, the Student, the related invoice, when it was
deleted, by whom and why, and links to its read-only tombstone.

**Read-only, no restore.** Nothing here writes, locks or commits; a POST, PUT,
PATCH or DELETE is a 405. Deleted documents count for nothing in any live
list, balance or report -- this is the only place they are listed. No
bank-transfer reference, provider or webhook value, idempotency key, audit
snapshot or internal id is shown (``app/services/deleted_record_queries.py``).

An unknown type, a malformed public id or a live document is a plain 404.
Only an active Administrator reaches these pages; every response is
``private, no-store`` with ``Vary: Cookie``.
"""

from flask import abort, render_template, request, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plans import _financial_response, _tz_name
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import deleted_record_queries as deleted
from app.services.fee_plan_queries import normalize_page

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

_DETAILS = {
    deleted.INVOICE: deleted.deleted_invoice_detail,
    deleted.PAYMENT: deleted.deleted_payment_detail,
    deleted.RECEIPT: deleted.deleted_receipt_detail,
}


def _detail_url(doc_type, public_id):
    return url_for("admin.deleted_financial_record", doc_type=doc_type, public_id=public_id)


@admin_bp.get("/deleted-financial-records")
@roles_required(_ADMINISTRATOR)
@_financial_response
def deleted_financial_records():
    """One page of deleted financial documents."""
    kind, search = deleted.normalize_filters(request.args.get("type"), request.args.get("q"))
    entries, total, page = deleted.deleted_page(
        kind, search, normalize_page(request.args.get("page"))
    )
    records = deleted.build_deleted_view(entries, _tz_name())
    for record in records:
        record["detail_url"] = _detail_url(record["doc_type"], record["public_id"])
    filters = {
        key: value for key, value in (("type", kind), ("q", search)) if value and value != "all"
    }
    first = (page - 1) * deleted.PAGE_SIZE + 1 if entries else 0
    last = (page - 1) * deleted.PAGE_SIZE + len(entries)
    return render_template(
        "admin/deleted_records/index.html",
        records=records,
        kind=kind,
        search=search,
        type_filters=deleted.TYPE_FILTERS,
        filtered=bool(filters),
        list_url=url_for("admin.deleted_financial_records"),
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": url_for("admin.deleted_financial_records", page=page - 1, **filters)
            if page > 1
            else None,
            "next_url": url_for("admin.deleted_financial_records", page=page + 1, **filters)
            if last < total
            else None,
        },
        tz_name=_tz_name(),
    )


@admin_bp.get("/deleted-financial-records/<doc_type>/<public_id>")
@roles_required(_ADMINISTRATOR)
@_financial_response
def deleted_financial_record(doc_type, public_id):
    """One deleted document's read-only tombstone."""
    detail = _DETAILS.get(doc_type)
    if detail is None or not public_id or len(public_id) > 36:
        abort(404)
    record = detail(public_id, _tz_name())
    if record is None:
        abort(404)
    if doc_type == deleted.INVOICE:
        for payment in record["payments"]:
            payment["detail_url"] = _detail_url(deleted.PAYMENT, payment["public_id"])
            payment["receipt_url"] = (
                _detail_url(deleted.RECEIPT, payment["receipt_public_id"])
                if payment["receipt_public_id"]
                else None
            )
    if doc_type == deleted.PAYMENT:
        record["invoice_url"] = (
            _detail_url(deleted.INVOICE, record["invoice_public_id"])
            if record["invoice_deleted"]
            else None
        )
        record["receipt_url"] = (
            _detail_url(deleted.RECEIPT, record["receipt_public_id"])
            if record["receipt_public_id"]
            else None
        )
    if doc_type == deleted.RECEIPT:
        record["payment_url"] = _detail_url(deleted.PAYMENT, record["payment_public_id"])
    return render_template(
        f"admin/deleted_records/{doc_type}.html",
        record=record,
        list_url=url_for("admin.deleted_financial_records"),
        tz_name=_tz_name(),
    )
