"""Legacy invoice workspace URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from flask import redirect, url_for

from app.blueprints.admin import admin_bp

from app.blueprints.admin.financial_http import _financial_response

from app.models import UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

@admin_bp.route('/invoices/new', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_workspace_new():
    return redirect(url_for('admin.student_accounts'), code=303)

@admin_bp.route('/invoices/<invoice_public_id>/delete', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_workspace_delete(invoice_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(invoice_public_id=invoice_public_id, target='correct')
