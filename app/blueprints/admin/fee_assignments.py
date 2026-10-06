"""Legacy fee assignments URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from app.blueprints.admin import admin_bp

from app.blueprints.admin.financial_http import _financial_response

from app.models import UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

_BASE = '/groups/<group_public_id>/enrollments/<enrollment_public_id>'

@admin_bp.get(_BASE + '/fee-assignments')
@roles_required(_ADMINISTRATOR)
@_financial_response
def enrollment_fee_assignments(group_public_id, enrollment_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, target='record')

@admin_bp.get(_BASE + '/fee-plans')
@roles_required(_ADMINISTRATOR)
@_financial_response
def enrollment_fee_plan_choices(group_public_id, enrollment_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, target='record')

@admin_bp.route(_BASE + '/fee-plans/<plan_public_id>/assign', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def enrollment_fee_assignment_create(group_public_id, enrollment_public_id, plan_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, target='record')

@admin_bp.post(_BASE + '/fee-assignments/<assignment_public_id>/cancel')
@roles_required(_ADMINISTRATOR)
@_financial_response
def enrollment_fee_assignment_cancel(group_public_id, enrollment_public_id, assignment_public_id):
    from app.services.finance_compatibility import legacy_finance_link
    return legacy_finance_link(group_public_id=group_public_id, enrollment_public_id=enrollment_public_id, assignment_public_id=assignment_public_id, target='correct')
