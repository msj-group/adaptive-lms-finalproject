"""The Researcher workspace read pages (Phase 6 replacement)::

    GET  /research/                      -> redirect to the dashboard
    GET  /research/dashboard
    GET  /research/sessions[?page=]
    GET  /research/sessions/<session_public_id>
    GET  /research/exclusions[?page=]
    GET  /research/activity-log[?page=]

Every rule requires an active Researcher (``roles_required``): an anonymous
visitor is sent to the shared login, any other role gets 403. The pages
read only pseudonymous research rows (``research_workspace_queries``):
subject codes, never a name, email address, account id or mapping. Numbers
are real counts with explicit empty states; nothing is predicted, and no
page presents a rating distribution or a chart as measured emotion.

The deployment controls the displayed provenance. A browser parameter cannot
switch it, and operational review sessions remain outside study exports.
"""

from flask import abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.research import research_bp
from app.models import UserRole, now_ms
from app.models.enums import ResearchAuditAction
from app.security.decorators import roles_required
from app.services import research_workspace_queries as queries
from app.services.research_event_dictionary import REPORTING_AREAS
from app.services.research_scope import within_period, utcnow_naive

_RESEARCHER = UserRole.RESEARCHER.value


def tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def deployment_provenance():
    """Use the validated server setting, never a browser selection."""
    return current_app.config["RESEARCH_DATA_PROVENANCE"]


def readiness(active):
    """The production-readiness checklist: stated, never assumed."""
    config = current_app.config
    return [
        {
            "label": "Retention supplied by the deployment (RESEARCH_RETENTION_DAYS)",
            "ok": config.get("RESEARCH_RETENTION_DAYS") is not None,
            "detail": (f"{config.get('RESEARCH_RETENTION_DAYS')} days"
                       if config.get("RESEARCH_RETENTION_DAYS") else
                       "Not configured: no configuration can be activated."),
        },
        {
            "label": "Deployment provenance is study data",
            "ok": config.get("RESEARCH_DATA_PROVENANCE") == "study",
            "detail": ("Study exports are enabled for eligible study sessions."
                       if config.get("RESEARCH_DATA_PROVENANCE") == "study"
                       else "Operational review exports are available separately; "
                            "their data is excluded from study exports."),
        },
        {
            "label": "An active configuration is collecting within its period",
            "ok": bool(config.get("RESEARCH_COLLECTION_ENABLED", False) and active and active.is_collecting and within_period(
                active.collection_starts_at, active.collection_ends_at, utcnow_naive())),
            "detail": "See Configurations.",
        },
        {
            "label": "External participation arrangements and approvals",
            "ok": None,
            "detail": "Handled outside the application; not verified here.",
        },
    ]


@research_bp.get("/")
@roles_required(_RESEARCHER)
def index():
    return redirect(url_for("research.dashboard"))


@research_bp.get("/dashboard")
@roles_required(_RESEARCHER)
def dashboard():
    from app.services.research_analytics import dashboard_filters
    from app.services.research_observation_queries import dashboard_summary
    provenance = deployment_provenance()
    active = queries.active_configuration()
    moment = now_ms()
    try: filters = dashboard_filters(request.args, moment)
    except (ValueError, OverflowError): abort(400)
    period_state = None
    if active is not None:
        now = utcnow_naive()
        period_state = "not_started" if now < active.collection_starts_at else "ended" if now >= active.collection_ends_at else "open"
    return render_template("research/dashboard.html", active_nav="dashboard", provenance=provenance,
        provenance_labels=queries.PROVENANCE_LABELS, active=active, period_state=period_state,
        collection_enabled=current_app.config.get("RESEARCH_COLLECTION_ENABLED", False),
        readiness=readiness(active), analytics=dashboard_summary(provenance, filters),
        analytics_filters=filters, configurations=queries.configuration_choices(), tz_name=tz_name())


@research_bp.get("/sessions")
@roles_required(_RESEARCHER)
def sessions():
    provenance = deployment_provenance()
    from app.services.workspace_filters import research_filters
    try:
        filters, filter_values = research_filters(request.args, tz_name())
    except (ValueError, OverflowError):
        abort(400)
    from app.services.research_observation_queries import sessions_page
    rows, total, page = sessions_page(
        provenance, queries.normalize_page(request.args.get("page")), filters)
    first = (page - 1) * queries.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * queries.PAGE_SIZE + len(rows)
    return render_template(
        "research/sessions/list.html",
        active_nav="sessions",
        provenance=provenance,
        provenance_labels=queries.PROVENANCE_LABELS,
        rows=rows,
        filters=filter_values,
        configurations=queries.configuration_choices(),
        end_reason_labels=queries.END_REASON_LABELS,
        to_local=lambda ms: queries.ms_to_local(tz_name(), ms),
        tz_name=tz_name(),
        pagination={
            "first": first, "last": last, "total": total,
            "prev_url": url_for("research.sessions", page=page - 1, **filter_values)
            if page > 1 else None,
            "next_url": url_for("research.sessions", page=page + 1, **filter_values)
            if last < total else None,
        },
    )


@research_bp.get("/sessions/<session_public_id>")
@roles_required(_RESEARCHER)
def session_detail(session_public_id):
    from app.services import research_observation_queries as observations
    row = observations.session_detail(session_public_id, deployment_provenance())
    if row is None: abort(404)
    return render_template("research/sessions/detail.html", active_nav="sessions", session=row,
        timeline=observations.session_timeline(row.id), timeline_cap=observations.TIMELINE_CAP,
        questions=observations.session_questions(row.id, now_ms()),
        end_reason_labels=queries.END_REASON_LABELS, provenance_labels=queries.PROVENANCE_LABELS,
        to_local=lambda ms: queries.ms_to_local(tz_name(), ms), tz_name=tz_name())


@research_bp.get("/exclusions")
@roles_required(_RESEARCHER)
def exclusions():
    rows, total, page = queries.excluded_subjects_page(
        queries.normalize_page(request.args.get("page")))
    first = (page - 1) * queries.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * queries.PAGE_SIZE + len(rows)
    return render_template(
        "research/exclusions.html",
        active_nav="exclusions",
        rows=rows,
        basis_labels=queries.EXCLUSION_BASIS_LABELS,
        pagination={
            "first": first, "last": last, "total": total,
            "prev_url": url_for("research.exclusions", page=page - 1) if page > 1 else None,
            "next_url": url_for("research.exclusions", page=page + 1) if last < total else None,
        },
    )


@research_bp.get("/activity-log")
@roles_required(_RESEARCHER)
def activity_log():
    from app.services.workspace_filters import research_filters
    try:
        filters, filter_values = research_filters(request.args, tz_name())
    except (ValueError, OverflowError):
        abort(400)
    rows, total, page = queries.audit_page(queries.normalize_page(request.args.get("page")), filters)
    first = (page - 1) * queries.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * queries.PAGE_SIZE + len(rows)
    return render_template(
        "research/audit.html",
        active_nav="audit",
        rows=rows,
        filters=filter_values,
        me=current_user.id,
        actions=tuple(action.value for action in ResearchAuditAction),
        tz_name=tz_name(),
        pagination={
            "first": first, "last": last, "total": total,
            "prev_url": url_for("research.activity_log", page=page - 1, **filter_values) if page > 1 else None,
            "next_url": url_for("research.activity_log", page=page + 1, **filter_values) if last < total else None,
        },
    )
