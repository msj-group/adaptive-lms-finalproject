"""Legacy invoice register URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from app.blueprints.admin import admin_bp

from app.blueprints.admin.financial_http import _financial_response

from app.models import UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

@admin_bp.get('/invoices')
@roles_required(_ADMINISTRATOR)
@_financial_response
def invoice_register():
    from app.blueprints.admin.finance_registers import register_page
    return register_page('invoice')
