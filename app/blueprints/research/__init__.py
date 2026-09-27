from flask import Blueprint

research_bp = Blueprint("research", __name__, url_prefix="/research")

from app.blueprints.research import routes  # noqa: E402,F401
from app.blueprints.research import protocols  # noqa: E402,F401
