"""Legacy payment intents URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from flask import redirect, url_for

from app.blueprints.admin import admin_bp

from app.blueprints.admin.financial_http import _financial_response

from app.blueprints.admin.invoices import _BASE

from app.models import Invoice, PaymentIntent, UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

_INVOICE = _BASE + '/<invoice_public_id>'

_INTENTS = _INVOICE + '/payment-intents'

_ONE = _INTENTS + '/<intent_public_id>'

@admin_bp.get(_INTENTS)
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intents(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import enrollment_account
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, enrollment_id=episode.id, student_id=episode.student_id).first_or_404()
    return redirect(url_for('admin.student_financial_record', student_public_id=episode.student.public_id), code=303)

@admin_bp.route(_INTENTS + '/new', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_create(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import enrollment_account
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, enrollment_id=episode.id, student_id=episode.student_id).first_or_404()
    return redirect(url_for('admin.student_financial_record', student_public_id=episode.student.public_id), code=303)

@admin_bp.get(_ONE)
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_detail(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, intent_public_id):
    from app.services.finance_compatibility import enrollment_account
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, enrollment_id=episode.id, student_id=episode.student_id).first_or_404()
    intent = PaymentIntent.query.filter_by(public_id=intent_public_id, invoice_id=invoice.id).first_or_404()
    return redirect(url_for('admin.student_financial_record', student_public_id=episode.student.public_id), code=303)

@admin_bp.route(_ONE + '/checkout', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_checkout(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, intent_public_id):
    from app.services.finance_compatibility import enrollment_account
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, enrollment_id=episode.id, student_id=episode.student_id).first_or_404()
    intent = PaymentIntent.query.filter_by(public_id=intent_public_id, invoice_id=invoice.id).first_or_404()
    return redirect(url_for('admin.student_financial_record', student_public_id=episode.student.public_id), code=303)

@admin_bp.post(_ONE + '/checkout/webhook')
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_deliver(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, intent_public_id):
    from app.services.finance_compatibility import enrollment_account
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, enrollment_id=episode.id, student_id=episode.student_id).first_or_404()
    intent = PaymentIntent.query.filter_by(public_id=intent_public_id, invoice_id=invoice.id).first_or_404()
    return redirect(url_for('admin.student_financial_record', student_public_id=episode.student.public_id), code=303)

@admin_bp.post(_ONE + '/return')
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_return(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, intent_public_id):
    from app.services.finance_compatibility import enrollment_account
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, enrollment_id=episode.id, student_id=episode.student_id).first_or_404()
    intent = PaymentIntent.query.filter_by(public_id=intent_public_id, invoice_id=invoice.id).first_or_404()
    return redirect(url_for('admin.student_financial_record', student_public_id=episode.student.public_id), code=303)

@admin_bp.get(_ONE + '/result')
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_result(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, intent_public_id):
    from app.services.finance_compatibility import enrollment_account
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, enrollment_id=episode.id, student_id=episode.student_id).first_or_404()
    intent = PaymentIntent.query.filter_by(public_id=intent_public_id, invoice_id=invoice.id).first_or_404()
    return redirect(url_for('admin.student_financial_record', student_public_id=episode.student.public_id), code=303)

@admin_bp.route(_ONE + '/cancel', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_intent_cancel(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, intent_public_id):
    from app.services.finance_compatibility import enrollment_account
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id)
    invoice = Invoice.query.filter_by(public_id=invoice_public_id, enrollment_id=episode.id, student_id=episode.student_id).first_or_404()
    intent = PaymentIntent.query.filter_by(public_id=intent_public_id, invoice_id=invoice.id).first_or_404()
    return redirect(url_for('admin.student_financial_record', student_public_id=episode.student.public_id), code=303)

@admin_bp.get('/payment-intents')
@roles_required(_ADMINISTRATOR)
@_financial_response
def payment_intents_overview():
    return redirect(url_for('admin.student_accounts'), code=303)
