"""Legacy fee plans URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from app.blueprints.admin.financial_http import _financial_response

from flask import redirect, url_for

from app.blueprints.admin import admin_bp

from app.models import UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

@admin_bp.get('/fee-plans')
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plans_list():
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.get('/fee-plans/<plan_public_id>')
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_detail(plan_public_id):
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.route('/fee-plans/new', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_create():
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.route('/fee-plans/<plan_public_id>/edit', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_edit(plan_public_id):
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.route('/fee-plans/<plan_public_id>/items/new', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_item_create(plan_public_id):
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.route('/fee-plans/<plan_public_id>/items/<item_public_id>/edit', methods=['GET', 'POST'])
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_item_edit(plan_public_id, item_public_id):
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.post('/fee-plans/<plan_public_id>/items/<item_public_id>/remove')
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_item_remove(plan_public_id, item_public_id):
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.post('/fee-plans/<plan_public_id>/activate')
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_activate(plan_public_id):
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.post('/fee-plans/<plan_public_id>/archive')
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_archive(plan_public_id):
    return redirect(url_for('admin.courses_list'), code=303)

@admin_bp.post('/fee-plans/<plan_public_id>/reactivate')
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_reactivate(plan_public_id):
    return redirect(url_for('admin.courses_list'), code=303)
