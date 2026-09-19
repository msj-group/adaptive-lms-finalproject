from flask import Blueprint

from app.blueprints.admin.navigation import ADMIN_NAV_SECTIONS

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.context_processor
def inject_admin_nav():
    return {"admin_nav_sections": ADMIN_NAV_SECTIONS}


from app.blueprints.admin import routes  # noqa: E402,F401
from app.blueprints.admin import academic_terms  # noqa: E402,F401
from app.blueprints.admin import levels  # noqa: E402,F401
from app.blueprints.admin import courses  # noqa: E402,F401
from app.blueprints.admin import groups  # noqa: E402,F401
from app.blueprints.admin import students  # noqa: E402,F401
from app.blueprints.admin import teachers  # noqa: E402,F401
from app.blueprints.admin import group_members  # noqa: E402,F401
from app.blueprints.admin import enrollments  # noqa: E402,F401
from app.blueprints.admin import schedules  # noqa: E402,F401
from app.blueprints.admin import attendance  # noqa: E402,F401
from app.blueprints.admin import grades  # noqa: E402,F401
from app.blueprints.admin import announcements  # noqa: E402,F401
from app.blueprints.admin import calendar  # noqa: E402,F401
from app.blueprints.admin import fee_plans  # noqa: E402,F401
from app.blueprints.admin import fee_assignments  # noqa: E402,F401
from app.blueprints.admin import invoices  # noqa: E402,F401
from app.blueprints.admin import payments  # noqa: E402,F401
from app.blueprints.admin import payment_intents  # noqa: E402,F401
from app.blueprints.admin import financial_reports  # noqa: E402,F401
from app.blueprints.admin import invoice_register  # noqa: E402,F401
from app.blueprints.admin import student_accounts  # noqa: E402,F401
from app.blueprints.admin import invoice_workspace  # noqa: E402,F401
from app.blueprints.admin import payment_workspace  # noqa: E402,F401
from app.blueprints.admin import deleted_records  # noqa: E402,F401
