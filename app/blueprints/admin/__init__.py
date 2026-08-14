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
