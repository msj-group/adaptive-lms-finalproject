"""Legacy invoices URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from app.blueprints.admin import admin_bp

from app.blueprints.admin.financial_http import _financial_response

from app.models import UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

_BASE = '/groups/<group_public_id>/enrollments/<enrollment_public_id>/fee-assignments/<assignment_public_id>/invoices'

@admin_bp.get(_BASE)
@roles_required(_ADMINISTRATOR)
@_financial_response
def assignment_invoices(group_public_id, enrollment_public_id, assignment_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, target='record')

@admin_bp.route(_BASE + '/new', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def assignment_invoice_create(group_public_id, enrollment_public_id, assignment_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, target='record')

@admin_bp.get(_BASE + '/<invoice_public_id>')
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_detail(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='record')

@admin_bp.get(_BASE + '/<invoice_public_id>/edit')
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_edit(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='correct')

@admin_bp.route(_BASE + '/<invoice_public_id>/items/new', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_line_create(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='record')

@admin_bp.route(_BASE + '/<invoice_public_id>/items/<item_public_id>/edit', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_line_edit(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, item_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='correct')

@admin_bp.route(_BASE + '/<invoice_public_id>/items/<item_public_id>/remove', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_line_remove(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id, item_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='record')

@admin_bp.post(_BASE + '/<invoice_public_id>/issue')
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_issue(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='record')

@admin_bp.route(_BASE + '/<invoice_public_id>/cancel', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_cancel(group_public_id, enrollment_public_id, assignment_public_id, invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, invoice_public_id=invoice_public_id, target='correct')
