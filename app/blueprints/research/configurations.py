"""Retired manual-configuration URLs redirect to the management area."""
from flask import redirect, url_for
from app.blueprints.research import research_bp
from app.security.decorators import roles_required

@research_bp.get("/configurations", endpoint="configurations")
@research_bp.get("/configurations/new", endpoint="configuration_new")
@research_bp.get("/configurations/<configuration_public_id>", endpoint="configuration_detail")
@research_bp.get("/configurations/<configuration_public_id>/edit", endpoint="configuration_edit")
@roles_required("researcher")
def manual_configuration_redirect(configuration_public_id=None):
    return redirect(url_for("research.manage"), code=303)
