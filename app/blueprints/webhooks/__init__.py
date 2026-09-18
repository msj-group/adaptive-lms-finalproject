from flask import Blueprint

webhooks_bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")

from app.blueprints.webhooks import routes  # noqa: E402,F401
