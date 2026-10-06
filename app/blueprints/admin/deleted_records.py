"""Legacy deleted records URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from app.blueprints.admin import admin_bp

from app.blueprints.admin.financial_http import _financial_response

from app.models import UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

@admin_bp.get('/deleted-financial-records')
@roles_required(_ADMINISTRATOR)
@_financial_response
def deleted_financial_records():
    from app.blueprints.admin.finance_registers import register_page
    return register_page('deleted')

@admin_bp.get('/deleted-financial-records/<doc_type>/<public_id>')
@roles_required(_ADMINISTRATOR)
@_financial_response
def deleted_financial_record(doc_type, public_id):
    from app.blueprints.admin.finance_registers import deleted_record_link
    return deleted_record_link(doc_type, public_id)
