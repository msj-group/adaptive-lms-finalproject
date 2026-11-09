"""One-click controls; the signed intended state makes retries non-toggling."""
from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadData, URLSafeTimedSerializer
from wtforms import HiddenField
from wtforms.validators import DataRequired, Length
from app.i18n import LocalizedFlaskForm
from app.blueprints.research import research_bp
from app.security.decorators import roles_required
from app.services import research_configuration_transactions as tx
from app.services.research_collection_controls import control
from app.services.research_workspace_queries import active_configuration
from app.services.research_scope import within_period, utcnow_naive


class ControlForm(LocalizedFlaskForm):
    state = HiddenField(validators=[DataRequired(), Length(max=2000)])


def signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="research.immediate-control")


@research_bp.app_context_processor
def collection_control_context():
    if (not request.endpoint or not request.endpoint.startswith("research.")
            or not current_user.is_authenticated or current_user.role != "researcher"):
        return {}
    config = active_configuration()
    intended = not bool(config and config.is_collecting)
    form = ControlForm()
    form.state.data = signer().dumps(dict(actor=current_user.public_id,
        configuration=config.public_id if config else None, version=config.version if config else 0,
        collecting=intended))
    enabled = current_app.config.get("RESEARCH_COLLECTION_ENABLED", False)
    running = bool(enabled and config and config.is_collecting
                   and within_period(config.collection_starts_at, config.collection_ends_at, utcnow_naive()))
    return dict(collection_control=dict(form=form, intended=intended, enabled=enabled,
        running=running, status="Collecting" if running else "Collection stopped",
        end=config.collection_ends_at if config else None))


@research_bp.post("/collection/control")
@roles_required("researcher")
def collection_control():
    form = ControlForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        state = signer().loads(form.state.data, max_age=12 * 3600)
    except BadData:
        abort(400)
    if (not isinstance(state, dict) or set(state) != {"actor", "configuration", "version", "collecting"}
            or state["actor"] != current_user.public_id or type(state["version"]) is not int
            or type(state["collecting"]) is not bool
            or (state["configuration"] is not None and not isinstance(state["configuration"], str))):
        abort(400)
    result = control(current_user.id, state["configuration"], state["version"], state["collecting"])
    if result not in (tx.COLLECTING, tx.PAUSED, tx.ALREADY):
        flash({tx.STALE: "Collection changed in another tab. Refresh and try again.",
            tx.PERIOD_ENDED: "The approved study period has ended.",
            tx.NO_RETENTION: "The study end has not been supplied by the server administrator.",
            tx.COLLECTION_NOT_READY: "Collection is unavailable. Contact the server administrator.",
        }.get(result, "Collection could not be changed. Refresh and try again."), "warning")
    target = request.form.get("return_to")
    allowed = {"research.dashboard", "research.sessions", "research.exports", "research.manage"}
    target = target if target in allowed else "research.dashboard"
    values = {}
    if target == "research.manage" and request.form.get("return_section") in {"groups","quality","exclusions","audit","storage"}:
        values["section"] = request.form["return_section"]
    return redirect(url_for(target, **values), code=303)
