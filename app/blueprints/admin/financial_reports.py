"""Legacy financial reports URLs delegated to the approved current workflows.

Endpoint names, authorization, nested ownership checks and private headers are
retained for existing links. These routes do not implement the retired finance
or enrollment lifecycle.
"""
from functools import wraps

from flask import after_this_request

from app.blueprints.admin import admin_bp

from app.models import UserRole

from app.security.decorators import roles_required

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

def _report_response(view):
    """``Cache-Control: private, no-store``, ``Vary: Cookie`` and
    ``X-Content-Type-Options: nosniff`` on every response of `view` --
    including a 403, a login redirect or an error page, because the headers
    are attached before the role check runs."""

    @wraps(view)
    def wrapped(*args, **kwargs):

        @after_this_request
        def _headers(response):
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.vary.add('Cookie')
            return response
        return view(*args, **kwargs)
    return wrapped

@admin_bp.get('/financial-reports')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_reports_index():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('accounts', 'html')

@admin_bp.get('/financial-reports/collections')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_collections():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('history', 'html')

@admin_bp.get('/financial-reports/collections.csv')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_collections_csv():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('history', 'csv')

@admin_bp.get('/financial-reports/collections.pdf')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_collections_pdf():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('history', 'pdf')

@admin_bp.get('/financial-reports/outstanding-invoices')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_outstanding():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('accounts', 'html')

@admin_bp.get('/financial-reports/outstanding-invoices.csv')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_outstanding_csv():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('accounts', 'csv')

@admin_bp.get('/financial-reports/outstanding-invoices.pdf')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_outstanding_pdf():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('accounts', 'pdf')

@admin_bp.get('/financial-reports/exceptions')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_exceptions():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('exceptions', 'html')

@admin_bp.get('/financial-reports/exceptions.csv')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_exceptions_csv():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('exceptions', 'csv')

@admin_bp.get('/financial-reports/exceptions.pdf')
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_exceptions_pdf():
    from app.blueprints.admin.general_reports import reports_page
    return reports_page('exceptions', 'pdf')
