"""Keep the authenticated request identity stable across transaction resets."""
from flask import abort, g, has_request_context
from app.models import User


def require_current_actor(actor_id, role):
    actor = User.query.filter_by(id=actor_id, role=role, status="active").populate_existing().with_for_update().first()
    proof = getattr(g, "authenticated_actor", None) if has_request_context() else None
    if actor is None or (proof is not None and proof != (actor.id, actor.auth_version, actor.role)):
        if has_request_context():
            abort(403)
        raise ValueError("The authenticated operator is no longer authorized")
    return actor


def require_operator_authentication(actor):
    from app.extensions import db
    if db.session.info.get("research_operator_auth") != (actor.id, actor.auth_version):
        raise ValueError("Researcher authentication expired. Sign in to the operator tool again.")
