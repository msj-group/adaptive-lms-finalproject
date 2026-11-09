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
switch it. Only Study data is accepted.
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
                       else "Study provenance is required before collection can run."),
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
    from app.services.research_charts import activity_chart
    analytics = dashboard_summary(provenance, filters)
    return render_template("research/dashboard.html", active_nav="dashboard", provenance=provenance,
        provenance_labels=queries.PROVENANCE_LABELS, active=active, period_state=period_state,
        collection_enabled=current_app.config.get("RESEARCH_COLLECTION_ENABLED", False),
        readiness=readiness(active), analytics=analytics, chart=activity_chart(analytics['daily']),
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
    from app.extensions import db
    from app.models import ResearchSession
    from app.models.research_observations import ResearchEvaluationGroup as Partition
    part = db.session.query(Partition.partition_code, ResearchSession.subject_id).select_from(ResearchSession).outerjoin(
        Partition, ResearchSession.subject_id == Partition.subject_id).filter(ResearchSession.id == row.id).first()
    reviewable = bool(part and part.partition_code in observations.REVIEW_GROUPS)
    operational = bool(part and part.partition_code in ('unassigned', None))
    from app.services.research_live import session_activity
    return render_template("research/sessions/detail.html", active_nav="sessions", session=row,
        reviewable=reviewable, timeline=observations.session_timeline(row.id) if reviewable else [], timeline_cap=observations.TIMELINE_CAP,
        operational=operational, recent_actions=session_activity(row.id, part.subject_id, row.subject_code) if operational else [],
        questions=observations.session_questions(row.id, now_ms()) if reviewable else [],
        end_reason_labels=queries.END_REASON_LABELS, provenance_labels=queries.PROVENANCE_LABELS,
        to_local=lambda ms: queries.ms_to_local(tz_name(), ms), tz_name=tz_name())


def management_redirect(section):
    values = request.args.to_dict()
    values["section"] = section
    return redirect(url_for("research.manage", **values), code=303)


@research_bp.route("/manage", methods=["GET", "POST"])
@roles_required(_RESEARCHER)
def manage():
    section = request.args.get("section", "")
    if section not in {"", "groups", "quality", "exclusions", "audit", "storage"}:
        abort(404)
    if request.method == "POST" and section != "storage":
        abort(405)
    if section in {"groups", "quality"}:
        from app.blueprints.research.collection_review import collection_quality
        return collection_quality()
    if section == "exclusions":
        return redirect(url_for('research.manage'), code=303)
    if section == "audit":
        return activity_log()
    if section == "storage":
        from app.blueprints.research.storage import storage
        return storage()
    from app.services.schedule_occurrences import to_app_local
    active = queries.active_configuration()
    return render_template("research/manage.html", active_nav="manage",
        retention_days=current_app.config["RESEARCH_RETENTION_DAYS"],
        study_end=to_app_local(tz_name(), active.collection_ends_at).strftime("%d %b %Y %H:%M") if active else None)


@research_bp.get("/exclusions")
@roles_required(_RESEARCHER)
def exclusions():
    return redirect(url_for('research.manage'), code=303)


@research_bp.get("/activity-log")
@roles_required(_RESEARCHER)
def activity_log():
    if request.endpoint == "research.activity_log":
        return management_redirect("audit")
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
