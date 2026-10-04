"""The separate Researcher workspace (Phase 6 replacement).

Its own login entry, its own layout and navigation, and no link from or to
any Administrator, Teacher or Student page. Every rule except the login page
requires an active Researcher; see ``access.py``.

**Headers.** Every response Flask finalizes for a path under ``/research`` --
pages, redirects, the login redirect, 403, 404 and 405 alike -- carries
``Cache-Control: private, no-store`` and ``Vary: Cookie`` through the
application-wide, path-scoped hook below (a blueprint hook would miss
Flask's own 404/405, which have no blueprint).
"""

from flask import Blueprint, request

research_bp = Blueprint("research", __name__, url_prefix="/research")


def _is_research_path(path):
    prefix = research_bp.url_prefix
    return path == prefix or path.startswith(prefix + "/")


@research_bp.after_app_request
def _no_store_under_research(response):
    if _is_research_path(request.path):
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
    return response


from app.blueprints.research import access  # noqa: E402,F401
from app.blueprints.research import workspace  # noqa: E402,F401
from app.blueprints.research import configurations  # noqa: E402,F401
from app.blueprints.research import exports  # noqa: E402,F401
