from flask import abort, current_app, request, url_for
from flask_login import current_user
from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.security.decorators import roles_required
from app.services.activity_queries import TYPES, STATES, activities_page, activity_view, activity_courses, activity_groups
from app.services.assignment_queries import normalize_page
from app.services.schedule_occurrences import utc_reference_now
from app.services.student_lessons import student_outline_group


@student_bp.get("/activities")
@roles_required("student")
def activities():
    page = normalize_page(request.args.get("page"))
    kind = request.args.get("type", "")
    state = request.args.get("state", "")
    course = request.args.get("course", "")[:36]
    selected_group = request.args.get("group", "")[:36]
    group = student_outline_group(current_user.id, selected_group) if selected_group else None
    if selected_group and group is None:
        abort(404)
    moment = utc_reference_now()
    rows, has_next = activities_page(current_user.id, moment, kind=kind, state=state, course=course, group=selected_group, page=page)
    tz_name = current_app.config["APP_TIMEZONE"]
    return private_no_store("student/activities.html", items=activity_view(rows, tz_name, url_for), types=TYPES, states=STATES,
        selected_type=kind if kind in TYPES else "", selected_state=state if state in STATES else "", selected_course=course,
        page=page, has_next=has_next, tz_name=tz_name, active_nav="activities", courses=activity_courses(current_user.id, moment),
        groups=activity_groups(current_user.id, moment, course), selected_group=selected_group, hub_group=group, teacher=False)
