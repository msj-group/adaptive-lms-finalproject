"""Teacher activity hub; native GET filters over the existing group-owned activities."""
from flask import current_app, request, url_for
from flask_login import current_user

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.units import _teacher_group_or_404
from app.blueprints.teacher.assignments import _group_is_operational
from app.blueprints.workspace import private_page
from app.security.decorators import roles_required
from app.services.activity_queries import TYPES
from app.services.assignment_queries import normalize_page
from app.services import teacher_activity_queries as library


@teacher_bp.get("/activities")
@teacher_bp.get("/groups/<group_public_id>/activities", endpoint="group_activities")
@roles_required("teacher")
def activities(group_public_id=None):
    selected_group = group_public_id or request.args.get("group", "")[:36]
    group = _teacher_group_or_404(selected_group) if selected_group else None
    kind = request.args.get("type", "")
    state = request.args.get("state", "")
    course = request.args.get("course", "")[:36]
    page = normalize_page(request.args.get("page"))
    rows, has_next = library.activities_page(current_user.id, kind=kind, publication=state,
        course=course, group=selected_group, page=page)
    tz_name = current_app.config["APP_TIMEZONE"]
    return private_page("teacher/activities.html", items=library.activity_view(rows, tz_name, url_for),
        types=TYPES, states=library.PUBLICATIONS, courses=library.activity_courses(current_user.id),
        groups=library.activity_groups(current_user.id, course), hub_group=group,
        hub_operational=_group_is_operational(group) if group else False,
        selected_type=kind if kind in TYPES else "", selected_state=state if state in library.PUBLICATIONS else "",
        selected_course=course, selected_group=selected_group, page=page, has_next=has_next,
        tz_name=tz_name, teacher=True, active_nav="activities")
