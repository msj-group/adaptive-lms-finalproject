from flask import Blueprint

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

from app.blueprints.admin import routes  # noqa: E402,F401
from app.blueprints.admin import academic_terms  # noqa: E402,F401
from app.blueprints.admin import levels  # noqa: E402,F401
