"""Research export pages (Phase 6 replacement)::

    GET   /research/exports[?page=]
    POST  /research/exports
    GET   /research/exports/<export_public_id>
    GET   /research/exports/<export_public_id>/download

Active Researcher only. Creating an export builds its ZIP once and stores it
with its description and an audit event. Every download serves exactly
those stored bytes -- never regenerated -- after re-checking their SHA-256,
and is audited too. An archive the retention rule removed is answered as
gone (410); a corrupted one is refused. The server selects the deployment's
export scope, with operational review kept separate from study data.
No file carries a name, email address, account id, mapping or
content (``research_exports``).
"""

from flask import (
    abort, current_app, flash, make_response, redirect, render_template, request, url_for,
)
from flask_login import current_user

from app.blueprints.research import research_bp
from app.blueprints.research.forms import ExportForm
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import research_exports as exporter
from app.services import research_workspace_queries as queries

_RESEARCHER = UserRole.RESEARCHER.value


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _provenance():
    return current_app.config["RESEARCH_DATA_PROVENANCE"]


def _form():
    form = ExportForm()
    form.configuration.choices = [("", "Every configuration version")] + [
        (row.public_id, f"Version {row.version_number} — {row.label}")
        for row in queries.configuration_choices()
    ]
    return form


@research_bp.route("/exports", methods=["GET", "POST"])
@research_bp.route("/exports/new", methods=["GET", "POST"], endpoint="export_create")
@roles_required(_RESEARCHER)
def exports():
    form = _form()
    selection = None
    if request.method == "POST":
        if form.validate_on_submit():
            if request.form.get("preview"):
                try:
                    selection = exporter.selection_summary(form.configuration.data or None,
                        form.period_from.data, form.period_to.data, _tz_name())
                except (ValueError, OverflowError):
                    flash("Check the date range and configuration.", "warning")
            else:
                status, public_id = exporter.create_export(
                current_user.id, form.configuration.data or None, form.period_from.data,
                form.period_to.data, _tz_name())
                if status == exporter.CREATED:
                    flash("Export created.", "success")
                    return redirect(url_for("research.export_detail", export_public_id=public_id))
                flash({
                exporter.BAD_PERIOD: "Check the period: the end date is before the start date.",
                exporter.TOO_LARGE: "This export is larger than the supported bound. Narrow "
                                    "the period or the configuration version.",
                exporter.UNAUTHORIZED: "Your account can no longer create exports.",
                exporter.CLOCK_AHEAD: "Some saved research data carries a time later than "
                                      "this server's clock, so nothing was exported. Try "
                                      "again in a minute, and check the server clocks if "
                                      "this repeats.",
                }.get(status, "The export could not be created. Nothing was written."), "danger")
        else:
            flash("Check the export filters.", "warning")
    provenance = _provenance()
    rows, total, page = queries.exports_page(
        queries.normalize_page(request.args.get("page")), provenance)
    first = (page - 1) * queries.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * queries.PAGE_SIZE + len(rows)
    return render_template(
        "research/exports/create.html" if request.endpoint == "research.export_create" or request.method == "POST" else "research/exports/list.html",
        active_nav="exports",
        form=form,
        selection=selection,
        rows=rows,
        tz_name=_tz_name(),
        provenance=provenance,
        provenance_labels=queries.PROVENANCE_LABELS,
        to_local=lambda ms: queries.ms_to_local(_tz_name(), ms),
        pagination={
            "first": first, "last": last, "total": total,
            "prev_url": url_for("research.exports", page=page - 1) if page > 1 else None,
            "next_url": url_for("research.exports", page=page + 1) if last < total else None,
        },
    )


@research_bp.get("/exports/<export_public_id>")
@roles_required(_RESEARCHER)
def export_detail(export_public_id):
    export = queries.export_by_public_id(export_public_id, _provenance())
    if export is None:
        abort(404)
    return render_template(
        "research/exports/detail.html",
        active_nav="exports",
        export=export,
        version=queries.configuration_version_number(export.configuration_id),
        available=exporter.archive_available(export.id),
        tz_name=_tz_name(),
        to_local=lambda ms: queries.ms_to_local(_tz_name(), ms),
        provenance=_provenance(),
        provenance_labels=queries.PROVENANCE_LABELS,
    )


@research_bp.get("/exports/<export_public_id>/download")
@roles_required(_RESEARCHER)
def export_download(export_public_id):
    export = queries.export_by_public_id(export_public_id, _provenance())
    if export is None:
        abort(404)
    status, data = exporter.download_export(current_user.id, export_public_id)
    if status == exporter.EXPIRED:
        return render_template("research/exports/unavailable.html", active_nav="exports",
                               reason="expired"), 410
    if status == exporter.CORRUPT:
        return render_template("research/exports/unavailable.html", active_nav="exports",
                               reason="corrupt"), 409
    if status != exporter.SERVED:
        abort(404)
    response = make_response(data)
    response.headers["Content-Type"] = "application/zip"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="research-export-{_provenance()}-{export_public_id}.zip"'
    )
    return response
