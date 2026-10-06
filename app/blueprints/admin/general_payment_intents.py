"""Sandbox intents with general-account amounts and unchanged verified delivery."""
import secrets
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from wtforms import HiddenField, SelectField, StringField, SubmitField
from wtforms.validators import InputRequired, Length
from app.blueprints.admin import admin_bp
from app.blueprints.admin.financial_http import _financial_response
from app.extensions import db
from app.models import Invoice, PaymentIntent, PaymentProviderEvent, User
from app.models.submission_feedback import whole_second_utc
from app.security.decorators import roles_required
from app.services.financial_history import document_snapshot
from app.services.actor_authorization import require_current_actor
from app.services.general_finance import lock_financial_student, account_totals
from app.services.money import validate_amount, format_amount
from app.services.payment_providers import PaymentProviderError
from app.services.payment_webhooks import process_provider_webhook, WebhookRejected, WebhookRetry
from app.blueprints.admin.finance_registers import local_moment


class IntentForm(FlaskForm):
    state = HiddenField(validators=[InputRequired(), Length(max=24000)])
    amount = StringField("Amount (LYD)", validators=[InputRequired(), Length(max=32)])
    submit = SubmitField("Create sandbox intent")


class SandboxActionForm(FlaskForm):
    state = HiddenField(validators=[InputRequired(), Length(max=24000)])
    action = SelectField("Action", choices=[("simulate", "Simulate provider outcome"),
        ("observe", "Read provider result"), ("deliver", "Deliver signed webhook"), ("cancel", "Cancel pending intent")])
    outcome = SelectField("Simulated outcome", choices=[("success", "Succeeded"), ("failure", "Failed")])
    submit = SubmitField("Apply sandbox action")


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="repair.general-intent.v1")


def _provider():
    settings = current_app.extensions["payment_provider"]
    if not settings.mock_enabled:
        abort(404)
    return settings.provider


def _url(student, invoice, intent=None):
    values = dict(student_public_id=student.public_id, invoice_public_id=invoice.public_id)
    if intent:
        values["intent_public_id"] = intent.public_id
    return url_for("admin.student_invoice_intent" if intent else "admin.student_invoice_intents", **values)


def _context(student_public_id, invoice_public_id):
    student = User.query.filter_by(public_id=student_public_id, role="student").first_or_404()
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, student_id=student.id).first_or_404()
    return student, invoice


@admin_bp.route("/student-accounts/<student_public_id>/invoices/<invoice_public_id>/sandbox", methods=["GET", "POST"])
@_financial_response
@roles_required("administrator")
def student_invoice_intents(student_public_id, invoice_public_id):
    provider = _provider()
    student, invoice = _context(student_public_id, invoice_public_id)
    form = IntentForm()
    actor_id, actor_auth_version = current_user.id, current_user.auth_version
    if request.method == "GET":
        form.state.data = _serializer().dumps({"invoice":document_snapshot(invoice), "actor":current_user.public_id, "key":secrets.token_hex(32)})
        balance = account_totals(student.id)["balance"]
        form.amount.data = format_amount(balance) if balance > 0 else ""
    if form.validate_on_submit():
        try:
            payload = _serializer().loads(form.state.data)
            amount = validate_amount(form.amount.data)
            if not isinstance(payload, dict) or payload.get("actor") != current_user.public_id or len(payload.get("key", "")) != 64:
                raise ValueError("Reload the intent form.")
            student = lock_financial_student(student_public_id)
            actor = require_current_actor(actor_id, "administrator")
            if actor.auth_version != actor_auth_version:
                abort(403)
            invoice = Invoice.query.filter_by(public_id=invoice_public_id, student_id=student.id).populate_existing().with_for_update().first()
            if invoice is None:
                abort(404)
            existing = PaymentIntent.query.filter_by(idempotency_key=payload["key"]).first()
            if existing:
                if existing.invoice_id != invoice.id or existing.amount != amount or existing.created_by_id != actor_id:
                    raise ValueError("This form was already used for different payment details.")
                db.session.rollback()
                return redirect(_url(student, invoice, existing))
            if invoice is None or document_snapshot(invoice) != payload.get("invoice") or invoice.deleted_at or invoice.status != "issued":
                raise ValueError("The invoice changed. Review a fresh form.")
            if PaymentIntent.query.filter(PaymentIntent.invoice_id == invoice.id, PaymentIntent.status.in_(["pending","provider_succeeded"])).first():
                raise ValueError("Review the existing active intent before creating another for this invoice context.")
            reported = provider.create_payment_intent(idempotency_key=payload["key"], amount=amount, currency_code="LYD")
            if reported.status != "pending" or reported.amount != amount or reported.currency_code != "LYD":
                raise ValueError("The provider did not return the requested pending payment.")
            moment = whole_second_utc()
            intent = PaymentIntent(invoice_id=invoice.id, provider=provider.name, provider_reference=reported.reference,
                idempotency_key=payload["key"], amount=amount, currency_code="LYD", status="pending", version=1,
                created_by_id=actor_id, created_at=moment, updated_at=moment)
            db.session.add(intent)
            db.session.commit()
            return redirect(_url(student, invoice, intent))
        except (BadSignature, ValueError, IntegrityError, PaymentProviderError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else "The sandbox intent could not be created. Reload and review its details.", "danger")
            return redirect(_url(student, invoice))
    intents = PaymentIntent.query.filter_by(invoice_id=invoice.id).order_by(PaymentIntent.id.desc()).paginate(
        page=max(1, request.args.get("page",1,type=int)), per_page=30, error_out=False)
    return render_template("admin/general_finance/intents.html", student=student, invoice=invoice, form=form,
        intents=intents, money=format_amount, local=local_moment)


@admin_bp.route("/student-accounts/<student_public_id>/invoices/<invoice_public_id>/sandbox/<intent_public_id>", methods=["GET", "POST"])
@_financial_response
@roles_required("administrator")
def student_invoice_intent(student_public_id, invoice_public_id, intent_public_id):
    provider = _provider()
    student, invoice = _context(student_public_id, invoice_public_id)
    intent = PaymentIntent.query.filter_by(public_id=intent_public_id, invoice_id=invoice.id).first_or_404()
    form = SandboxActionForm()
    actor_id, auth_version = current_user.id, current_user.auth_version
    if request.method == "GET":
        form.state.data = _serializer().dumps({"intent":document_snapshot(intent), "actor":current_user.public_id})
    if form.validate_on_submit():
        try:
            payload = _serializer().loads(form.state.data)
            student = lock_financial_student(student_public_id)
            actor = require_current_actor(actor_id, "administrator")
            if actor.auth_version != auth_version:
                abort(403)
            invoice = Invoice.query.filter_by(public_id=invoice_public_id, student_id=student.id).populate_existing().with_for_update().first()
            if invoice is None:
                abort(404)
            intent = PaymentIntent.query.filter_by(public_id=intent_public_id, invoice_id=invoice.id).populate_existing().with_for_update().first()
            if intent is None:
                abort(404)
            if (not isinstance(payload,dict) or payload.get("actor") != actor.public_id
                    or payload.get("intent") != document_snapshot(intent)):
                raise ValueError("The intent changed. Reload its details before taking action.")
            if intent.status not in {"pending","provider_succeeded"}:
                raise ValueError("This intent has already reached its final state.")
            reference, action = intent.provider_reference, form.action.data
            moment = whole_second_utc()
            if action == "simulate":
                if intent.status != "pending":
                    raise ValueError("Only a pending intent can be simulated.")
                provider.simulate_checkout_outcome(reference, form.outcome.data)
                db.session.rollback()
            elif action == "deliver":
                body, headers = provider.emit_webhook(reference)
                db.session.rollback()
                result = process_provider_webhook(provider, body, headers, tz_name=current_app.config["APP_TIMEZONE"])
                flash("Verified provider delivery: " + result.outcome.replace("_"," ") + ".", "info")
            else:
                reported = provider.cancel_payment(reference) if action == "cancel" else provider.get_payment_status(reference)
                if reported.reference != reference or reported.amount != intent.amount or reported.currency_code != intent.currency_code:
                    raise ValueError("The provider report does not match this intent.")
                if action == "cancel":
                    if intent.status != "pending" or reported.status != "cancelled":
                        raise ValueError("The provider did not cancel this pending payment.")
                    intent.status, intent.cancelled_by_id, intent.terminal_at = "cancelled", actor_id, moment
                else:
                    if intent.status != "pending" or reported.status not in {"succeeded","failed"}:
                        raise ValueError("The provider has no new final browser result.")
                    intent.status = "provider_succeeded" if reported.status == "succeeded" else "provider_failed"
                    intent.provider_result_at, intent.provider_result_by_id = moment, actor_id
                    if reported.status == "failed":
                        intent.terminal_at = moment
                intent.version, intent.updated_at = intent.version + 1, moment
                db.session.commit()
                flash("Provider observation saved. Only verified webhook delivery records received money.", "info")
        except (BadSignature, ValueError, IntegrityError, PaymentProviderError, WebhookRejected, WebhookRetry) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else "The sandbox action could not be completed. Reload and try again.", "danger")
        return redirect(_url(student, invoice, intent))
    events = PaymentProviderEvent.query.filter_by(payment_intent_id=intent.id).order_by(PaymentProviderEvent.id.desc()).paginate(
        page=max(1,request.args.get("page",1,type=int)), per_page=30, error_out=False)
    return render_template("admin/general_finance/intent.html", student=student, invoice=invoice, intent=intent,
        events=events, form=form, money=format_amount, local=local_moment)
