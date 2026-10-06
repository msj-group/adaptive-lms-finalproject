"""Bounded center-wide registers using direct Student account ownership."""
from flask import abort, render_template, request
from app.extensions import db
from app.models import Invoice, PaymentTransaction, Receipt, User
from app.services.finance_compatibility import legacy_finance_link
from app.services.money import format_amount
from app.services.schedule_occurrences import to_app_local
from flask import current_app


def local_moment(value):
    return to_app_local(current_app.config["APP_TIMEZONE"], value).strftime("%Y-%m-%d %H:%M:%S") if value else "—"


def register_page(kind):
    doc_kind = request.args.get("kind", "invoice") if kind == "deleted" else kind
    model = {"invoice": Invoice, "payment": PaymentTransaction}.get(doc_kind)
    if model is None:
        abort(404)
    query = db.session.query(model, User).join(User, User.id == model.student_id).filter(User.role == "student")
    if kind == "deleted":
        query = query.filter(model.deleted_at.is_not(None))
    state = request.args.get("state", "")
    if state in {"issued", "cancelled", "confirmed", "pending", "rejected"}:
        query = query.filter(model.status == state, model.deleted_at.is_(None))
    search = request.args.get("q", "").strip()[:100]
    if search:
        query = query.filter(User.full_name.ilike(f"%{search}%"))
    page = query.order_by(model.id.desc()).paginate(page=max(1, request.args.get("page", 1, type=int)), per_page=50, error_out=False)
    receipts = {row.payment_transaction_id: row for row in Receipt.query.filter(Receipt.payment_transaction_id.in_(
        [doc.id for doc, _student in page.items])).all()} if doc_kind == "payment" else {}
    return render_template("admin/general_finance/register.html", page=page, kind=doc_kind,
        deleted=kind == "deleted", search=search, state=state, money=format_amount, local=local_moment, receipts=receipts)


def deleted_record_link(doc_type, public_id):
    if doc_type == "invoice":
        return legacy_finance_link(invoice_public_id=public_id)
    if doc_type == "payment":
        return legacy_finance_link(payment_public_id=public_id)
    if doc_type == "receipt":
        return legacy_finance_link(receipt_public_id=public_id)
    abort(404)
