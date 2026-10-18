"""Teacher activity library and a read-only entry to existing draft workflows."""
from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.units import _teacher_group_or_404
from app.blueprints.teacher.assignments import _group_is_operational
from app.blueprints.workspace import private_page
from app.security.decorators import roles_required
from app.services.activity_queries import TYPES
from app.services.assignment_queries import normalize_page
from app.services import teacher_activity_queries as library

CREATE_ENDPOINTS = {
    "assignment": "teacher.assignment_create",
    "quiz": "teacher.quiz_create",
    "listening": "teacher.listening_create",
    "speaking": "teacher.speaking_create",
}


def legacy_activity_redirect(group_public_id, kind):
    """Retire a category page while retaining its assigned-Group URL safely."""
    if kind not in CREATE_ENDPOINTS:
        abort(400)
    group = _teacher_group_or_404(group_public_id)
    response = redirect(url_for("teacher.activities", group=group.public_id, type=kind))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@teacher_bp.get("/activities")
@teacher_bp.get("/groups/<group_public_id>/activities", endpoint="group_activities")
@roles_required("teacher")
def activities(group_public_id=None):
    selected_group = group_public_id or request.args.get("group", "")[:36]
    group = _teacher_group_or_404(selected_group) if selected_group else None
    kind = request.args.get("type", "")
    state = request.args.get("state", "")
    course = request.args.get("course", "")[:36]
    # Native GETs can carry a previous Group after Course is changed.
    if group and course and group.course.public_id != course:
        selected_group, group = "", None
    page = normalize_page(request.args.get("page"))
    rows, has_next = library.activities_page(current_user.id, kind=kind, publication=state,
        course=course, group=selected_group, page=page)
    tz_name = current_app.config["APP_TIMEZONE"]
    groups = list(library.activity_groups(current_user.id))
    if group and not any(row["group_public_id"] == group.public_id for row in groups):
        # Keep a valid direct Group link usable beyond the bounded option list.
        groups.insert(0, dict(group_public_id=group.public_id, group_name=group.name,
            course_public_id=group.course.public_id, course_title=group.course.title,
            term_name=group.academic_term.name, operational=_group_is_operational(group)))
    return private_page("teacher/activities.html", items=library.activity_view(rows, tz_name, url_for),
        types=TYPES, states=library.PUBLICATIONS, courses=library.activity_courses(current_user.id),
        groups=groups, creation_groups=[row for row in groups if row["operational"]], hub_group=group,
        hub_operational=_group_is_operational(group) if group else False,
        selected_type=kind if kind in TYPES else "", selected_state=state if state in library.PUBLICATIONS else "",
        selected_course=course, selected_group=selected_group, page=page, has_next=has_next,
        tz_name=tz_name, teacher=True, active_nav="activities")


@teacher_bp.get("/activities/new")
@roles_required("teacher")
def activity_start():
    """Choose context only; the destination owns CSRF, locks and draft creation."""
    kind = request.args.get("type", "")
    group_public_id = request.args.get("group", "")
    if kind not in CREATE_ENDPOINTS or not group_public_id or len(group_public_id) > 36:
        abort(400)
    group = _teacher_group_or_404(group_public_id)
    if not _group_is_operational(group):
        flash("This group is read only. Choose an active group to create an activity.", "warning")
        return redirect(url_for("teacher.activities", group=group.public_id))
    return redirect(url_for(CREATE_ENDPOINTS[kind], group_public_id=group.public_id))
