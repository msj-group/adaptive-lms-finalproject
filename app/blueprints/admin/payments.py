"""Legacy payments URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from app.blueprints.admin import admin_bp

from app.blueprints.admin.financial_http import _financial_response

from app.blueprints.admin.invoices import _BASE

from app.models import UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

_INVOICE = _BASE + '/<invoice_public_id>'

_PAYMENTS = _INVOICE + '/payments'

_ONE = _PAYMENTS + '/<payment_public_id>'

@admin_bp.get(_PAYMENTS)
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payments(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='record')

@admin_bp.route(_PAYMENTS + '/cash', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_cash(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='money')

@admin_bp.route(_PAYMENTS + '/bank-transfer', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_bank_transfer(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='money')

@admin_bp.route(_ONE + '/confirm', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_confirm(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, payment_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, payment_public_id=payment_public_id, target='correct')

@admin_bp.route(_ONE + '/reject', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_reject(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, payment_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, payment_public_id=payment_public_id, target='correct')

@admin_bp.route(_ONE + '/reverse', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_payment_reverse(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, payment_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, payment_public_id=payment_public_id, target='correct')

@admin_bp.get(_INVOICE + '/receipts/<receipt_public_id>')
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_receipt_detail(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, receipt_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, receipt_public_id=receipt_public_id, target='record')

@admin_bp.get('/payments')
@roles_required(_ADMINISTRATOR)
@_financial_response
def payments_overview():
    from app.blueprints.admin.finance_registers import register_page
    return register_page('payment')
