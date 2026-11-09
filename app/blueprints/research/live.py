"""Private, read-only snapshots; every poll repeats Researcher authorization."""
from flask import jsonify
from app.blueprints.research import research_bp
from app.models import now_ms
from app.security.decorators import roles_required
from app.services.research_live import snapshot


@research_bp.get('/live')
@roles_required('researcher')
def live():
    return jsonify(snapshot(now_ms()))
