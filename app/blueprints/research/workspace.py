"""The Researcher workspace read pages (Phase 6 replacement)::

    GET  /research/                      -> redirect to the dashboard
    GET  /research/dashboard[?provenance=]
    GET  /research/sessions[?provenance=][&page=]
    GET  /research/sessions/<session_public_id>
    GET  /research/exclusions[?page=]
    GET  /research/activity-log[?page=]

Every rule requires an active Researcher (``roles_required``): an anonymous
visitor is sent to the research login, any other role gets 403. The pages
read only pseudonymous research rows (``research_workspace_queries``):
subject codes, never a name, email address, account id or mapping. Numbers
are real counts with explicit empty states; nothing is predicted, and no
page presents a rating distribution or a chart as measured emotion.

Development and demonstration data are shown only when explicitly selected,
under a label that says they are not research data.
"""

from flask import abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.research import research_bp
from app.models import UserRole, now_ms
from app.security.decorators import roles_required
from app.services import research_workspace_queries as queries
from app.services.research_event_dictionary import REPORTING_AREAS
from app.services.research_scope import within_period, utcnow_naive

_RESEARCHER = UserRole.RESEARCHER.value


def tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


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
            "detail": ("study" if config.get("RESEARCH_DATA_PROVENANCE") == "study"
                       else "development: nothing collected here can be exported."),
        },
        {
            "label": "An active configuration is collecting within its period",
            "ok": bool(active and active.is_collecting and within_period(
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
    provenance = queries.normalize_provenance(request.args.get("provenance"))
    active = queries.active_configuration()
    moment = now_ms()
    period_state = None
    if active is not None:
        now = utcnow_naive()
        if now < active.collection_starts_at:
            period_state = "not_started"
        elif now >= active.collection_ends_at:
            period_state = "ended"
        else:
            period_state = "open"
    return render_template(
        "research/dashboard.html",
        active_nav="dashboard",
        provenance=provenance,
        provenance_labels=queries.PROVENANCE_LABELS,
        active=active,
        period_state=period_state,
        readiness=readiness(active),
        subjects=queries.subject_totals(),
        sessions=queries.session_totals(provenance),
        prompts=queries.prompt_summary(provenance, moment),
        prompt_state_labels=queries.PROMPT_STATE_LABELS,
        event_types=queries.event_type_counts(provenance),
        devices=queries.device_coverage(provenance),
        areas=queries.area_coverage(provenance),
        coverage_areas=("core",) + REPORTING_AREAS,
        tz_name=tz_name(),
    )


@research_bp.get("/sessions")
@roles_required(_RESEARCHER)
def sessions():
    provenance = queries.normalize_provenance(request.args.get("provenance"))
    rows, total, page = queries.sessions_page(
        provenance, queries.normalize_page(request.args.get("page")))
    first = (page - 1) * queries.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * queries.PAGE_SIZE + len(rows)
    return render_template(
        "research/sessions/list.html",
        active_nav="sessions",
        provenance=provenance,
        provenance_labels=queries.PROVENANCE_LABELS,
        rows=rows,
        end_reason_labels=queries.END_REASON_LABELS,
        to_local=lambda ms: queries.ms_to_local(tz_name(), ms),
        tz_name=tz_name(),
        pagination={
            "first": first, "last": last, "total": total,
            "prev_url": url_for("research.sessions", provenance=provenance, page=page - 1)
            if page > 1 else None,
            "next_url": url_for("research.sessions", provenance=provenance, page=page + 1)
            if last < total else None,
        },
    )


@research_bp.get("/sessions/<session_public_id>")
@roles_required(_RESEARCHER)
def session_detail(session_public_id):
    row = queries.session_detail(session_public_id)
    if row is None:
        abort(404)
    moment = now_ms()
    prompts = [
        {
            "row": prompt,
            "state": queries.derived_prompt_state(prompt[0], prompt[2], prompt[5], prompt[11],
                                                  prompt[12], moment, prompt[8]),
        }
        for prompt in queries.session_prompts(row.id)
    ]
    return render_template(
        "research/sessions/detail.html",
        active_nav="sessions",
        session=row,
        timeline=queries.session_timeline(row.id),
        timeline_cap=queries.TIMELINE_CAP,
        prompts=prompts,
        prompt_state_labels=queries.PROMPT_STATE_LABELS,
        end_reason_labels=queries.END_REASON_LABELS,
        provenance_labels=queries.PROVENANCE_LABELS,
        to_local=lambda ms: queries.ms_to_local(tz_name(), ms),
        tz_name=tz_name(),
    )


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
    rows, total, page = queries.audit_page(queries.normalize_page(request.args.get("page")))
    first = (page - 1) * queries.PAGE_SIZE + 1 if rows else 0
    last = (page - 1) * queries.PAGE_SIZE + len(rows)
    return render_template(
        "research/audit.html",
        active_nav="audit",
        rows=rows,
        me=current_user.id,
        tz_name=tz_name(),
        pagination={
            "first": first, "last": last, "total": total,
            "prev_url": url_for("research.activity_log", page=page - 1) if page > 1 else None,
            "next_url": url_for("research.activity_log", page=page + 1) if last < total else None,
        },
    )
