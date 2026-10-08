"""General Student account forms, corrections and immutable revision views."""
import uuid
from functools import wraps
from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from app.i18n import LocalizedFlaskForm as FlaskForm
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from wtforms import BooleanField, DateField, HiddenField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import InputRequired, Length, Optional
from app.blueprints.admin import admin_bp
from app.blueprints.admin.financial_http import _financial_response
from app.extensions import db
from app.models import Enrollment, FinancialRevision, Group, Invoice, PaymentTransaction, Receipt, User
from app.security.decorators import roles_required
from app.services.financial_history import document_snapshot
from app.services.general_finance import account_totals, correct_invoice, correct_payment, lock_financial_student, record_money
from app.services.general_finance_queries import balances_for_students
from app.services.money import calculate_discount, format_amount, validate_amount, validate_course_price


class MoneyForm(FlaskForm):
    operation_key = HiddenField(validators=[InputRequired(), Length(min=36, max=36)])
    amount = StringField("Amount (LYD)", validators=[InputRequired(), Length(max=32)])
    direction = SelectField("Movement", choices=[("in", "Collection"), ("out", "Return credit to student")])
    method = SelectField("Method", choices=[("cash", "Cash"), ("bank_transfer", "Bank transfer")])
    invoice_public_id = SelectField("Invoice context (optional)", choices=[], validators=[Optional()])
    bank_reference = StringField("Bank reference", validators=[Optional(), Length(max=64)])
    bank_date = DateField("Bank transfer date", validators=[Optional()])
    confirmed = BooleanField("Bank transfer has been verified")
    submit = SubmitField("Record movement")


class CorrectionForm(FlaskForm):
    snapshot = HiddenField(validators=[InputRequired(), Length(max=24000)])
    action = SelectField("Action", choices=[("edit", "Correct record"), ("delete", "Delete record"),
        ("reverse", "Cancel obligation"), ("confirmed", "Confirm pending bank transfer"), ("rejected", "Reject pending bank transfer")])
    amount = StringField("Corrected amount (LYD)", validators=[Optional(), Length(max=32)])
    discount_kind = SelectField("Discount", choices=[("none", "No discount"), ("amount", "Amount"), ("percentage", "Percentage")])
    discount_value = StringField("Discount value", default="0", validators=[InputRequired(), Length(max=32)])
    reason = TextAreaField("Reason (optional)", validators=[Optional(), Length(max=500)])
    submit = SubmitField("Save correction")


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="repair.finance.snapshot.v1")


def _token(document):
    return _serializer().dumps(document_snapshot(document))


def _matches(token, document):
    try:
        return _serializer().loads(token) == document_snapshot(document)
    except (BadSignature, TypeError, ValueError):
        return False


def _record_url(student):
    return url_for("admin.student_financial_record", student_public_id=student.public_id)


def _student(public_id):
    return User.query.filter_by(public_id=public_id, role="student").first_or_404()


def accounts_page():
    search = request.args.get("q", "").strip()[:100]
    query = User.query.filter_by(role="student")
    if search:
        query = query.filter(or_(User.full_name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%")))
    page = query.order_by(User.full_name, User.id).paginate(page=max(1, request.args.get("page", 1, type=int)), per_page=50, error_out=False)
    return render_template("admin/general_finance/accounts.html", page=page,
        balances=balances_for_students(row.id for row in page.items), search=search, money=format_amount)


def record_page(student_public_id):
    student = _student(student_public_id)
    invoices = Invoice.query.filter_by(student_id=student.id).order_by(Invoice.id.desc()).paginate(
        page=max(1, request.args.get("invoice_page", 1, type=int)), per_page=30, error_out=False)
    payments = PaymentTransaction.query.filter_by(student_id=student.id).order_by(PaymentTransaction.recorded_at.desc(), PaymentTransaction.id.desc()).paginate(
        page=max(1, request.args.get("payment_page", 1, type=int)), per_page=30, error_out=False)
    receipts = {row.payment_transaction_id: row for row in Receipt.query.filter(
        Receipt.payment_transaction_id.in_([row.id for row in payments.items])).all()}
    episodes = {episode.id: (episode, group) for episode, group in db.session.query(Enrollment, Group).join(Group,
        Group.id == Enrollment.group_id).filter(Enrollment.id.in_([row.enrollment_id for row in invoices.items]), Enrollment.student_id == student.id).all()}
    from app.blueprints.admin.finance_registers import local_moment
    return render_template("admin/general_finance/record.html", student=student, facts=account_totals(student.id),
        invoices=invoices, payments=payments, money=format_amount, receipts=receipts, episodes=episodes,
        local=local_moment)


@admin_bp.route("/student-accounts/<student_public_id>/money/new", methods=["GET", "POST"])
@_financial_response
@roles_required("administrator")
def student_money_new(student_public_id):
    student = _student(student_public_id)
    form = MoneyForm()
    invoices = Invoice.query.filter_by(student_id=student.id, deleted_at=None).order_by(Invoice.id.desc()).limit(500).all()
    selected_invoice = request.args.get("invoice", "")[:36] if request.method == "GET" else form.invoice_public_id.data
    if selected_invoice and all(row.public_id != selected_invoice for row in invoices):
        selected = Invoice.query.filter_by(student_id=student.id, deleted_at=None, public_id=selected_invoice).first()
        if selected is not None:
            invoices.append(selected)
    form.invoice_public_id.choices = [("", "General student account")] + [(row.public_id,
        f"{row.invoice_number} · {(row.course_snapshot or {}).get('title', 'Course')} · {row.status}") for row in invoices]
    if request.method == "GET":
        form.operation_key.data = str(uuid.uuid4())
        form.invoice_public_id.data = request.args.get("invoice", "")[:36]
    if form.validate_on_submit():
        try:
            student = lock_financial_student(student_public_id)
            invoice = None
            if form.invoice_public_id.data:
                invoice = Invoice.query.filter_by(public_id=form.invoice_public_id.data.strip(), student_id=student.id).populate_existing().with_for_update().first()
                if invoice is None or invoice.deleted_at is not None:
                    raise ValueError("Select a current invoice belonging to this student, or leave its context empty.")
            record_money(student, current_user.id, form.amount.data, form.method.data, form.direction.data,
                form.operation_key.data, invoice=invoice, confirmed=form.confirmed.data,
                bank_reference=form.bank_reference.data, bank_date=form.bank_date.data)
            db.session.commit()
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash("The movement could not be saved. Reload and review its details." if isinstance(exc, IntegrityError) else str(exc), "danger")
        else:
            flash("Money movement recorded.", "success")
            return redirect(_record_url(student))
    return render_template("admin/general_finance/form.html", form=form, student=student, title="Record money movement", facts=account_totals(student.id),
        description="Invoice context is optional. A payout records money actually returned to the student and cannot exceed current credit.")


@admin_bp.route("/student-accounts/<student_public_id>/documents/<kind>/<document_public_id>/correct", methods=["GET", "POST"])
@_financial_response
@roles_required("administrator")
def student_document_correct(student_public_id, kind, document_public_id):
    model = {"invoice": Invoice, "payment": PaymentTransaction}.get(kind)
    if model is None:
        abort(404)
    student = _student(student_public_id)
    document = model.query.filter_by(public_id=document_public_id, student_id=student.id).first_or_404()
    form = CorrectionForm()
    if kind == "payment":
        form.discount_value.validators = [Optional()]
        form.discount_kind.default = "none"
        if request.method == "POST" and not form.discount_kind.data:
            form.discount_kind.data = "none"
    form.action.choices = [(key, label) for key, label in form.action.choices if key in (
        {"edit", "delete", "reverse"} if kind == "invoice" else {"edit", "delete", "confirmed", "rejected"})]
    if document.deleted_at is not None or kind == "payment" and document.kind != "collection":
        return redirect(url_for("admin.student_document_detail", student_public_id=student.public_id, kind=kind, document_public_id=document.public_id))
    if kind == "invoice" and document.status != "issued":
        form.action.choices = [("delete", "Delete record")]
    if kind == "payment":
        reversal = PaymentTransaction.query.filter_by(reversal_of_payment_transaction_id=document.id).first()
        allowed = {"delete"} if reversal else {"edit", "delete"} | ({"confirmed", "rejected"} if document.status == "pending" else set())
        form.action.choices = [(key, label) for key, label in form.action.choices if key in allowed]
    if request.method == "GET":
        form.snapshot.data = _token(document)
        form.amount.data = format(document.charge_amount if kind == "invoice" else document.amount, "f")
        if kind == "invoice":
            form.discount_kind.data, form.discount_value.data = document.discount_kind, document.discount_value
    if form.validate_on_submit():
        try:
            student = lock_financial_student(student_public_id)
            document = model.query.filter_by(public_id=document_public_id, student_id=student.id).populate_existing().with_for_update().first()
            if document is None or not _matches(form.snapshot.data, document):
                raise ValueError("This document changed. Reload the form and review current details.")
            if kind == "invoice":
                correct_invoice(document, student, current_user.id, document.version, form.amount.data,
                    form.discount_kind.data, form.discount_value.data, form.reason.data or None,
                    delete=form.action.data == "delete", reverse=form.action.data == "reverse")
            else:
                correct_payment(document, student, current_user.id, document.version, amount=form.amount.data if form.action.data == "edit" else None,
                    decision=form.action.data if form.action.data in {"confirmed", "rejected"} else None,
                    delete=form.action.data == "delete", reason=form.reason.data or None)
            db.session.commit()
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash("The correction could not be saved. Reload and review its details." if isinstance(exc, IntegrityError) else str(exc), "danger")
            return redirect(url_for("admin.student_document_correct", student_public_id=student_public_id, kind=kind, document_public_id=document_public_id))
        flash("Correction saved with its previous values and operator.", "success")
        return redirect(_record_url(student))
    return render_template("admin/general_finance/form.html", form=form, student=student, title="Correct financial record", kind=kind, document=document, facts=account_totals(student.id),
        description="Corrections preserve the previous record. Deleting a recorded collection does not record a real payout; use Return credit to student when money actually changes hands.")


@admin_bp.post("/student-accounts/<student_public_id>/money/preview")
@_financial_response
@roles_required("administrator")
def student_money_preview(student_public_id):
    """Read-only exact-decimal impact; no locks, writes or signed tokens consumed."""
    from decimal import Decimal
    student = _student(student_public_id)
    balance = account_totals(student.id)["balance"]
    try:
        kind = request.form.get("document_kind", "")
        action = request.form.get("action", "edit")
        if kind:
            model = {"invoice": Invoice, "payment": PaymentTransaction}.get(kind)
            if model is None:
                abort(404)
            document = model.query.filter_by(student_id=student.id,
                public_id=request.form.get("document_public_id", "")[:36], deleted_at=None).first_or_404()
            if kind == "invoice":
                before = document.charge_amount - document.discount_amount if document.status == "issued" else Decimal(0)
                if action != "delete" and document.status != "issued":
                    raise ValueError("Only a current issued invoice can be corrected or cancelled.")
                if action in {"delete", "reverse"}:
                    after = Decimal(0)
                elif action == "edit":
                    price = validate_course_price(request.form.get("amount", ""))
                    _discount, after = calculate_discount(price, request.form.get("discount_kind", "none"), request.form.get("discount_value", "0"))
                    if document.status != "issued":
                        after = Decimal(0)
                else:
                    raise ValueError("Choose an available invoice action.")
                delta = after - before
            else:
                if document.kind != "collection":
                    raise ValueError("Review the original movement and its reversal together.")
                reversal = PaymentTransaction.query.filter_by(reversal_of_payment_transaction_id=document.id).first()
                if reversal is not None and action != "delete":
                    raise ValueError("Review this reversed movement in its history before correcting it.")
                before = document.amount if document.status == "confirmed" else Decimal(0)
                if action == "delete":
                    if reversal is not None and reversal.deleted_at is None and reversal.status == "confirmed":
                        before -= reversal.amount
                    after = Decimal(0)
                elif action in {"confirmed", "rejected"}:
                    if document.status != "pending":
                        raise ValueError("Only pending bank transfers can be confirmed or rejected.")
                    after = document.amount if action == "confirmed" else Decimal(0)
                elif action == "edit":
                    amount = validate_amount(request.form.get("amount", ""))
                    if document.movement_direction == "out" and amount - document.amount > max(-balance, Decimal(0)):
                        raise ValueError("The corrected payout would exceed this student's credit.")
                    after = amount if document.status == "confirmed" else Decimal(0)
                else:
                    raise ValueError("Choose an available movement action.")
                delta = (after - before) * (1 if document.movement_direction == "out" else -1)
        else:
            amount = validate_amount(request.form.get("amount", ""))
            direction = request.form.get("direction", "")
            method = request.form.get("method", "")
            if direction not in {"in", "out"} or method not in {"cash", "bank_transfer"}:
                raise ValueError("Choose the movement direction and method.")
            confirmed = method == "cash" or direction == "out" or request.form.get("confirmed") in {"y", "true", "on"}
            delta = amount * (1 if direction == "out" else -1) if confirmed else Decimal(0)
            if direction == "out" and amount > max(-balance, Decimal(0)):
                raise ValueError("This payout exceeds the student's current credit.")
        return jsonify(before=format_amount(balance), change=format_amount(delta), after=format_amount(balance + delta),
            note="Preview only. Current records, permissions, credit and document versions are checked again when saving.")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@admin_bp.get("/student-accounts/<student_public_id>/documents/<kind>/<document_public_id>")
@_financial_response
@roles_required("administrator")
def student_document_detail(student_public_id, kind, document_public_id):
    model = {"invoice": Invoice, "payment": PaymentTransaction}.get(kind)
    if model is None:
        abort(404)
    student = _student(student_public_id)
    document = model.query.filter_by(public_id=document_public_id, student_id=student.id).first_or_404()
    receipt = Receipt.query.filter_by(payment_transaction_id=document.id).first() if kind == "payment" else None
    from app.blueprints.admin.finance_registers import local_moment
    return render_template("admin/general_finance/document.html", student=student, document=document,
        kind=kind, receipt=receipt, money=format_amount, local=local_moment)


@admin_bp.get("/student-accounts/<student_public_id>/history")
@_financial_response
@roles_required("administrator")
def student_financial_history(student_public_id):
    student = _student(student_public_id)
    revisions = db.session.query(FinancialRevision, User.full_name).outerjoin(User, User.id == FinancialRevision.actor_id).filter(
        FinancialRevision.student_id == student.id).order_by(FinancialRevision.created_at.desc(), FinancialRevision.id.desc()).paginate(
        page=max(1, request.args.get("page", 1, type=int)), per_page=50, error_out=False)
    from app.services.general_financial_reports import revision_view
    return render_template("admin/general_finance/history.html", student=student, revisions=revisions,
        entries=[revision_view(row, actor) for row, actor in revisions.items], money=format_amount)


@admin_bp.get("/student-accounts/<student_public_id>/receipts/<receipt_public_id>")
@_financial_response
@roles_required("administrator")
def student_account_receipt(student_public_id, receipt_public_id):
    student = _student(student_public_id)
    receipt = Receipt.query.join(PaymentTransaction, Receipt.payment_transaction_id == PaymentTransaction.id).filter(
        Receipt.public_id == receipt_public_id, PaymentTransaction.student_id == student.id).first_or_404()
    from decimal import Decimal
    from app.models.receipt import parse_receipt_moment
    from app.blueprints.admin.finance_registers import local_moment
    return render_template("admin/general_finance/receipt.html", student=student, receipt=receipt, money=format_amount,
        amount=format_amount(Decimal(receipt.snapshot["amount"])), confirmed_at=local_moment(parse_receipt_moment(receipt.snapshot["confirmed_at"])))
