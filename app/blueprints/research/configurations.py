"""Collection configuration pages (Phase 6 replacement)::

    GET       /research/configurations[?page=]
    GET|POST  /research/configurations/new
    GET       /research/configurations/<configuration_public_id>
    GET|POST  /research/configurations/<configuration_public_id>/edit
    POST      /research/configurations/<configuration_public_id>/activate
    POST      /research/configurations/<configuration_public_id>/collecting

Active Researcher only. Every write is a POST with CSRF and a signed token
bound to the configuration's version (``research_tokens``), re-proved inside
the transaction against the locked row (``research_configuration_transactions``).
Activation also needs the confirmation box, and it **does not start
collection**: collecting is a separate, audited resume action. Nothing on
these pages is an ethics approval or a consent document, and the pages say
so.
"""

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.research import research_bp
from app.blueprints.research.forms import (
    POLICY_LABELS,
    ActivateForm,
    CollectingForm,
    ConfigurationForm,
)
from app.models import POLICY_BOUNDS, UserRole
from app.security.decorators import roles_required
from app.services import research_configuration_transactions as tx
from app.services import research_tokens as tokens
from app.services import research_workspace_queries as queries
from app.services.schedule_occurrences import to_app_local

_RESEARCHER = UserRole.RESEARCHER.value

_MESSAGES = {
    tx.UNAUTHORIZED: ("Your account can no longer make this change.", "danger"),
    tx.NOT_FOUND: ("That configuration no longer exists.", "danger"),
    tx.STALE: ("This configuration changed after the page was opened. Review it and try "
               "again.", "warning"),
    tx.CONFLICT: ("The change could not be saved because of a concurrent change. Nothing was "
                  "written.", "danger"),
    tx.NOT_DRAFT: ("Only a draft can be changed or activated.", "warning"),
    tx.NOT_ACTIVE: ("Only the active configuration can be paused or resumed.", "warning"),
    tx.NO_RETENTION: ("The deployment has not supplied RESEARCH_RETENTION_DAYS, so no "
                      "configuration can be activated.", "danger"),
    tx.PERIOD_ENDED: ("The collection period of this configuration has already ended.",
                      "warning"),
    tx.UNCHANGED: ("Nothing changed.", "info"),
    tx.ALREADY: ("Collection is already in that state.", "info"),
    tx.UPDATED: ("Draft saved.", "success"),
    tx.ACTIVATED: ("Configuration activated. Collection has not started: use Start "
                   "collecting when you are ready.", "success"),
    tx.COLLECTING: ("Collection started.", "success"),
    tx.PAUSED: ("Collection paused. Open sessions were closed.", "success"),
}


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _local(moment):
    return None if moment is None else to_app_local(_tz_name(), moment)


def _or_404(public_id):
    configuration = queries.configuration_by_public_id(public_id)
    if configuration is None:
        abort(404)
    return configuration


def _defaults(form):
    for column, (_low, _high, default) in POLICY_BOUNDS.items():
        getattr(form, column).data = default


def _fill(form, configuration):
    form.label.data = configuration.label
    form.collection_starts_at.data = _local(configuration.collection_starts_at)
    form.collection_ends_at.data = _local(configuration.collection_ends_at)
    for column in POLICY_BOUNDS:
        getattr(form, column).data = getattr(configuration, column)


@research_bp.get("/configurations")
@roles_required(_RESEARCHER)
def configurations():
    rows, total, page = queries.configurations_page(
        queries.normalize_page(request.args.get("page")))
    first = (page - 1) * queries.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * queries.PAGE_SIZE + len(rows)
    return render_template(
        "research/configurations/list.html",
        active_nav="configurations",
        rows=rows,
        local=_local,
        tz_name=_tz_name(),
        pagination={
            "first": first, "last": last, "total": total,
            "prev_url": url_for("research.configurations", page=page - 1) if page > 1 else None,
            "next_url": url_for("research.configurations", page=page + 1)
            if last < total else None,
        },
    )


@research_bp.route("/configurations/new", methods=["GET", "POST"])
@roles_required(_RESEARCHER)
def configuration_new():
    form = ConfigurationForm(tz_name=_tz_name())
    if request.method == "GET":
        _defaults(form)
    if form.validate_on_submit():
        status, public_id = tx.create_draft(current_user.id, form.values())
        if status == tx.CREATED:
            flash("Draft created.", "success")
            return redirect(url_for("research.configuration_detail",
                                    configuration_public_id=public_id))
        message, category = _MESSAGES.get(status, _MESSAGES[tx.CONFLICT])
        flash(message, category)
    return render_template(
        "research/configurations/form.html", active_nav="configurations", form=form,
        configuration=None, baseline=queries.active_configuration(), policy_labels=POLICY_LABELS,
        policy_bounds=POLICY_BOUNDS, tz_name=_tz_name(),
    )


@research_bp.get("/configurations/<configuration_public_id>")
@roles_required(_RESEARCHER)
def configuration_detail(configuration_public_id):
    configuration = _or_404(configuration_public_id)
    activate_form = collecting_form = None
    if configuration.is_draft:
        activate_form = ActivateForm()
        activate_form.state.data = tokens.make_token(
            tokens.PURPOSE_ACTIVATE, current_user.public_id, configuration.public_id,
            configuration.version)
    if configuration.is_active:
        collecting_form = CollectingForm()
        collecting_form.state.data = tokens.make_token(
            tokens.PURPOSE_COLLECTING, current_user.public_id, configuration.public_id,
            configuration.version, collecting=not configuration.is_collecting)
    return render_template(
        "research/configurations/detail.html",
        active_nav="configurations",
        configuration=configuration,
        activate_form=activate_form,
        collecting_form=collecting_form,
        retention_days=current_app.config.get("RESEARCH_RETENTION_DAYS"),
        policy_labels=POLICY_LABELS,
        local=_local,
        tz_name=_tz_name(),
    )


@research_bp.route("/configurations/<configuration_public_id>/edit", methods=["GET", "POST"])
@roles_required(_RESEARCHER)
def configuration_edit(configuration_public_id):
    configuration = _or_404(configuration_public_id)
    detail_url = url_for("research.configuration_detail",
                         configuration_public_id=configuration.public_id)
    if not configuration.is_draft:
        flash(*_MESSAGES[tx.NOT_DRAFT])
        return redirect(detail_url)
    form = ConfigurationForm(tz_name=_tz_name())
    if request.method == "GET":
        _fill(form, configuration)
        form.state.data = tokens.make_token(
            tokens.PURPOSE_EDIT, current_user.public_id, configuration.public_id,
            configuration.version)
    elif form.validate_on_submit():
        payload = tokens.load_token(tokens.PURPOSE_EDIT, form.state.data,
                                    current_user.public_id, configuration.public_id)
        if payload is None or payload["version"] != configuration.version:
            flash(*_MESSAGES[tx.STALE])
            return redirect(detail_url)
        status = tx.update_draft(current_user.id, configuration.public_id,
                                 payload["version"], form.values())
        flash(*_MESSAGES.get(status, _MESSAGES[tx.CONFLICT]))
        return redirect(detail_url)
    return render_template(
        "research/configurations/form.html", active_nav="configurations", form=form,
        configuration=configuration, baseline=queries.active_configuration(), policy_labels=POLICY_LABELS,
        policy_bounds=POLICY_BOUNDS, tz_name=_tz_name(),
    )


@research_bp.post("/configurations/<configuration_public_id>/activate")
@roles_required(_RESEARCHER)
def configuration_activate(configuration_public_id):
    configuration = _or_404(configuration_public_id)
    detail_url = url_for("research.configuration_detail",
                         configuration_public_id=configuration.public_id)
    form = ActivateForm()
    if not form.validate_on_submit():
        flash("Tick the confirmation box to activate this version.", "warning")
        return redirect(detail_url)
    payload = tokens.load_token(tokens.PURPOSE_ACTIVATE, form.state.data,
                                current_user.public_id, configuration.public_id)
    if payload is None:
        flash(*_MESSAGES[tx.STALE])
        return redirect(detail_url)
    status = tx.activate(current_user.id, configuration.public_id, payload["version"],
                         current_app.config.get("RESEARCH_RETENTION_DAYS"))
    flash(*_MESSAGES.get(status, _MESSAGES[tx.CONFLICT]))
    return redirect(detail_url)


@research_bp.post("/configurations/<configuration_public_id>/collecting")
@roles_required(_RESEARCHER)
def configuration_collecting(configuration_public_id):
    configuration = _or_404(configuration_public_id)
    detail_url = url_for("research.configuration_detail",
                         configuration_public_id=configuration.public_id)
    form = CollectingForm()
    payload = tokens.load_token(tokens.PURPOSE_COLLECTING, form.state.data,
                                current_user.public_id, configuration.public_id) \
        if form.validate_on_submit() else None
    if payload is None:
        flash(*_MESSAGES[tx.STALE])
        return redirect(detail_url)
    status = tx.set_collecting(current_user.id, configuration.public_id, payload["version"],
                               payload["collecting"])
    flash(*_MESSAGES.get(status, _MESSAGES[tx.CONFLICT]))
    return redirect(detail_url)
