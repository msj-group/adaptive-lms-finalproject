"""Legacy student accounts URLs delegated to the approved current workflows.

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

@admin_bp.get('/billing-desk')
@roles_required(_ADMINISTRATOR)
@_financial_response
def billing_desk():
    """The M09 Billing Desk is now Student Accounts."""
    return redirect(url_for('admin.student_accounts'))

@admin_bp.get('/student-accounts')
@roles_required(_ADMINISTRATOR)
@_financial_response
def student_accounts():
    from app.blueprints.admin.general_finance import accounts_page
    return accounts_page()

@admin_bp.get('/student-accounts/<student_public_id>/financial-record')
@roles_required(_ADMINISTRATOR)
@_financial_response
def student_financial_record(student_public_id):
    from app.blueprints.admin.general_finance import record_page
    return record_page(student_public_id)
