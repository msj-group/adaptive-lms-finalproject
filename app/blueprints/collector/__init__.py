from flask import Blueprint

#: The Student-side research collector: authenticated, CSRF-protected JSON
#: endpoints for interaction batches and the optional feedback prompt.
collector_bp = Blueprint("collector", __name__, url_prefix="/collect")

from app.blueprints.collector import routes  # noqa: E402,F401
