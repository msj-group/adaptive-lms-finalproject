"""Read-only, role-scoped navigation workspaces; existing writes stay authoritative."""
from datetime import timedelta

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import AcademicTerm, Course, Enrollment, Group, Unit, User
from app.security.decorators import roles_required
from app.services.dashboard_queries import student_dashboard, teacher_dashboard
from app.services.schedule_occurrences import app_now, to_app_local

workspace_bp = Blueprint("workspace", __name__)


def private_page(template, **context):
    response = current_app.make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@workspace_bp.get("/workspace/search")
@roles_required("student", "teacher", "administrator", "researcher")
def search():
    """Live escaped HTML, or its native GET fallback; queries never write.

    Preview requests render no shell or collector and emit no page view or
    new research outcome. Never accept a role, account or provenance from args.
    """
    from app.i18n import gettext
    from app.services.search_terms import normalize_query
    from app.services.workspace_search import destinations, search_records
    norm = normalize_query(request.args.get("q", ""))
    links = []
    for label, endpoint, icon in destinations(current_user.role):
        text = (label + " " + gettext(label)).casefold()
        if not norm.text or all(token.casefold() in text for token in norm.tokens):
            links.append({"title": gettext(label), "url": url_for(endpoint),
                          "icon": icon, "context": gettext("Workspace page")})
    sections = search_records(current_user.role, current_user.id, norm,
        current_app.config.get("RESEARCH_DATA_PROVENANCE", "development"), url_for)
    template = "workspace/_search_results.html" if request.args.get("preview") == "1" \
        else "workspace/search.html"
    return private_page(template, search_query=norm.text, search_sections=sections,
        search_destinations=links, searchable=norm.is_searchable,
        result_count=sum(len(section["items"]) for section in sections) + len(links))


def _tz():
    return current_app.config.get("APP_TIMEZONE", "UTC")


@workspace_bp.get("/student/courses")
@roles_required("student")
def student_courses():
    data = student_dashboard(current_user.id, app_now(_tz()))
    return private_page("workspace/groups.html", cards=data["cards"], teacher=False,
                        tz_name=_tz(), today=app_now(_tz()).date(), active_nav="courses")


@workspace_bp.get("/student/courses/<group_public_id>")
@roles_required("student")
def student_course(group_public_id):
    from app.services.student_lessons import student_outline_group, outline_units
    group = student_outline_group(current_user.id, group_public_id)
    if group is None:
        abort(404)
    return private_page("workspace/course.html", group=group,
                        units=outline_units(group.id, current_user.id), active_nav="courses")


@workspace_bp.get("/teacher/groups")
@roles_required("teacher")
def teacher_groups():
    data = teacher_dashboard(current_user.id, app_now(_tz()))
    return private_page("workspace/groups.html", cards=data["cards"], teacher=True,
                        tz_name=_tz(), today=app_now(_tz()).date(), active_nav="groups")


@workspace_bp.get("/teacher/groups/<group_public_id>/workspace")
@roles_required("teacher")
def teacher_group(group_public_id):
    from app.blueprints.teacher.units import _teacher_group_or_404
    from app.models import Lesson
    group = _teacher_group_or_404(group_public_id)
    units = Unit.query.filter_by(group_id=group.id).order_by(Unit.display_order, Unit.id).all()
    lessons = Lesson.query.join(Unit, Lesson.unit_id == Unit.id).filter(
        Unit.group_id == group.id).order_by(Unit.display_order, Lesson.display_order, Lesson.id).all()
    by_unit = {}
    for lesson in lessons:
        by_unit.setdefault(lesson.unit_id, []).append(lesson)
    return private_page("workspace/teacher_group.html", group=group, units=units,
                        lessons_by_unit=by_unit, active_nav="groups")


@workspace_bp.get("/teacher/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/preview")
@roles_required("teacher")
def lesson_preview(group_public_id, unit_public_id, lesson_public_id):
    from app.blueprints.teacher.units import _teacher_group_or_404, _unit_for_group_or_404
    from app.blueprints.teacher.lessons import _lesson_for_unit_or_404
    from app.services.material_queries import student_visible_materials
    group = _teacher_group_or_404(group_public_id)
    unit = _unit_for_group_or_404(group, unit_public_id)
    lesson = _lesson_for_unit_or_404(unit, lesson_public_id)
    return private_page("workspace/lesson_preview.html", group=group, unit=unit,
                        lesson=lesson, materials=student_visible_materials(lesson.id))


@workspace_bp.post("/teacher/material-preview")
@roles_required("teacher")
def material_preview():
    from app.services.material_content import sanitize_rich_text_html
    content = request.form.get("content_html", "")
    if len(content) > 100000:
        abort(413)
    response = jsonify(content_html=sanitize_rich_text_html(content) or "")
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@workspace_bp.get("/admin/students/<student_public_id>/enrollments")
@roles_required("administrator")
def student_enrollments(student_public_id):
    student = User.query.filter_by(public_id=student_public_id, role="student").first_or_404()
    page = _page()
    rows = Enrollment.query.options(joinedload(Enrollment.group).joinedload(Group.course),
        joinedload(Enrollment.group).joinedload(Group.academic_term)).filter_by(
        student_id=student.id).order_by(Enrollment.created_at.desc(), Enrollment.id.desc()).paginate(
        page=page, per_page=25, error_out=False)
    return private_page("workspace/enrollments.html", student=student, rows=rows)


def _page():
    try:
        return max(1, min(100000, int(request.args.get("page", "1"))))
    except ValueError:
        return 1


@workspace_bp.get("/teacher/review")
@roles_required("teacher")
def review_queue():
    """Feedback is a comment, never an inferred grade or mastery decision."""
    from app.services.teacher_review_queries import review_page, review_view
    page = _page()
    kind = request.args.get("type", "")
    state = request.args.get("state", "pending")
    group_id = request.args.get("group", "")[:36]
    since = request.args.get("from", "")[:10]
    until = request.args.get("to", "")[:10]
    try:
        result, has_next = review_page(current_user.id, _tz(), page=page, kind=kind,
            state=state, group_id=group_id, since=since, until=until)
    except (ValueError, OverflowError):
        abort(400)
    rows = review_view(result, _tz(), url_for)
    groups = teacher_dashboard(current_user.id, app_now(_tz()))["cards"]
    return private_page("workspace/review_queue.html", rows=rows, groups=groups, page=page,
        has_next=has_next, selected_type=kind, selected_state=state, selected_group=group_id,
        since=since, until=until, tz_name=_tz(), active_nav="review")


@workspace_bp.get("/workspace/help")
@roles_required("student", "teacher", "administrator", "researcher")
def help_page():
    return _utility_redirect("help")


@workspace_bp.get("/workspace/preferences")
@roles_required("student", "teacher", "administrator", "researcher")
def preferences():
    return _utility_redirect("display")


def _utility_redirect(panel):
    home = {"student": "student.dashboard", "teacher": "teacher.dashboard",
            "administrator": "admin.dashboard", "researcher": "research.dashboard"}
    response = redirect(url_for(home[current_user.role], _utility=panel))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@workspace_bp.app_context_processor
def workspace_context():
    return {"workspace_context": navigation_context, "workspace_local": lambda moment:
            to_app_local(_tz(), moment) if moment else None, "workspace_group_choices": group_choices,
            "workspace_nav_active": nav_active, "workspace_size": human_size,
            "workspace_calendar_weeks": calendar_weeks, "workspace_back": filtered_back_link,
            "workspace_admin_navigation": admin_navigation}


def admin_navigation():
    """Static own-role navigation for shared Help/Display/Account pages."""
    if not current_user.is_authenticated or current_user.role != "administrator":
        return ()
    from app.blueprints.admin.navigation import ADMIN_NAV_SECTIONS
    return ADMIN_NAV_SECTIONS


def filtered_back_link():
    """Same-origin, role-scoped list return; never stores private content or URLs."""
    from urllib.parse import urlsplit
    from werkzeug.exceptions import HTTPException
    if not current_user.is_authenticated or not request.referrer:
        return None
    previous = urlsplit(request.referrer)
    current = urlsplit(request.url)
    if previous.netloc != current.netloc or previous.scheme != current.scheme or not previous.query or previous.path == current.path:
        return None
    try:
        endpoint, _args = current_app.url_map.bind_to_environ(request.environ).match(previous.path, method="GET")
    except HTTPException:
        return None
    allowed = {
        "student": {"student.activities", "student.search", "student.records"},
        "teacher": {"workspace.review_queue", "teacher.activities", "teacher.group_activities", "teacher.group_assignments", "teacher.group_quizzes",
            "teacher.group_listening", "teacher.group_speaking", "teacher.group_progress"},
        "administrator": {"admin.students_list", "admin.teachers_list", "admin.groups_list", "admin.invoice_register",
            "admin.payments_overview", "admin.deleted_financial_records", "admin.student_accounts"},
        "researcher": {"research.sessions", "research.exports", "research.activity_log"},
    }
    if endpoint not in allowed.get(current_user.role, set()) or len(previous.query) > 2048:
        return None
    return previous.path + "?" + previous.query


def calendar_weeks(days, start, end):
    """Presentation dates only; all entries arrive from authorized calendar queries."""
    from app.services.calendar_queries import MAX_RANGE_DAYS
    if not start or not end or (end - start).days not in range(MAX_RANGE_DAYS):
        return []
    by_date = {day["date"] if isinstance(day, dict) else day.date:
               day["rows"] if isinstance(day, dict) else day.rows for day in days}
    weeks = []
    cursor = start - timedelta(days=start.weekday())
    while cursor <= end:
        week = []
        for offset in range(7):
            day = cursor + timedelta(days=offset)
            week.append({"date": day, "rows": by_date.get(day, []), "in_range": start <= day <= end})
        weeks.append(week)
        cursor += timedelta(days=7)
    return weeks


def human_size(value):
    from decimal import Decimal
    amount = Decimal(value or 0)
    unit = "B"
    for candidate in ("B", "KiB", "MiB", "GiB", "TiB"):
        unit = candidate
        if abs(amount) < 1024 or candidate == "TiB":
            break
        amount /= 1024
    return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"


def nav_active(target):
    endpoint = request.endpoint or ""
    if endpoint == target:
        return True
    if target == "admin.student_accounts":
        return endpoint in {"admin.student_financial_record", "admin.student_money_new", "admin.student_money_preview",
            "admin.student_document_correct", "admin.student_document_detail", "admin.student_financial_history", "admin.student_account_receipt"}
    if target == "admin.students_list":
        return endpoint.startswith("admin.student_") and not nav_active("admin.student_accounts") or endpoint == "workspace.student_enrollments"
    families = {"admin.academic_terms_list":"admin.academic_term", "admin.levels_list":"admin.level",
        "admin.courses_list":"admin.course", "admin.teachers_list":"admin.teacher", "admin.groups_list":"admin.group",
        "admin.rooms_list":"admin.room", "admin.schedules_overview":"admin.schedule", "admin.attendance_overview":"admin.attendance",
        "admin.gradebook_overview":"admin.grade", "admin.announcements_overview":"admin.announcement",
        "admin.financial_reports_index":"admin.financial_report"}
    return endpoint.startswith(families[target]) if target in families else False


def group_choices():
    if not current_user.is_authenticated or current_user.role != "administrator":
        return []
    if not hasattr(g, "workspace_group_choices"):
        g.workspace_group_choices = db.session.query(Group.public_id, Group.name,
            Course.title, AcademicTerm.name.label("term_name")).join(Course, Course.id == Group.course_id).join(
            AcademicTerm, AcademicTerm.id == Group.academic_term_id).order_by(Group.name, Course.title, Group.id).limit(500).all()
    return g.workspace_group_choices


def navigation_context():
    """No private queries for login, errors, Researcher pages or unrelated objects."""
    if hasattr(g, "workspace_context"):
        return g.workspace_context
    context = None
    args = request.view_args or {}
    endpoint = request.endpoint or ""
    if current_user.is_authenticated and current_user.role in ("student", "teacher") and args.get("group_public_id"):
        group_id = args["group_public_id"]
        if current_user.role == "student":
            from app.services.student_lessons import student_outline_group
            group = student_outline_group(current_user.id, group_id)
            tabs = [("Overview", "workspace.student_course", {}), ("Lessons", "student.group_units", {}),
                    ("Activities", "student.activities", {"course": group.course.public_id, "group": group_id} if group else {}),
                    ("Discussions", "student.discussions_group", {})]
        else:
            from app.blueprints.teacher.units import _teacher_group_or_404
            group = _teacher_group_or_404(group_id)
            section = ("Content" if any(token in endpoint for token in ("unit", "lesson", "material")) else
                "Activities" if any(token in endpoint for token in ("assignment", "quiz", "question", "listening", "speaking", "submission", "activities")) else
                "Class records" if any(token in endpoint for token in ("attendance", "grade", "progress")) else
                "Communication" if any(token in endpoint for token in ("announcement", "discussion")) else "Overview")
            sections = [("Overview", "workspace.teacher_group"), ("Content", "teacher.group_units"),
                ("Activities", "teacher.group_activities"), ("Class records", "teacher.group_attendance"),
                ("Communication", "teacher.group_announcements")]
            tabs = {
                "Overview": [],
                "Content": [("Content outline", "workspace.teacher_group", {"_anchor": "group-content"}),
                            ("Units & lessons", "teacher.group_units", {})],
                "Activities": [("Assignments", "teacher.group_assignments", {}), ("Quizzes", "teacher.group_quizzes", {}),
                               ("Listening", "teacher.group_listening", {}), ("Speaking", "teacher.group_speaking", {})],
                "Class records": [("Attendance", "teacher.group_attendance", {}), ("Gradebook", "teacher.group_gradebook", {}),
                                  ("Progress", "teacher.group_progress", {})],
                "Communication": [("Announcements", "teacher.group_announcements", {}), ("Discussions", "teacher.discussions_group", {})],
            }[section]
        if group:
            links = []
            for label, target, extra in tabs:
                params = extra if target == "student.activities" else {"group_public_id": group_id, **extra}
                families = {"Content": ("unit", "lesson", "material"), "Lessons": ("unit", "lesson", "material"),
                    "Assignments": ("assignment", "submission"), "Quizzes": ("quiz", "question"),
                    "Listening": ("listening",), "Speaking": ("speaking",), "Attendance": ("attendance",),
                    "Gradebook": ("grade",), "Progress": ("progress",), "Announcements": ("announcement",),
                    "Discussions": ("discussion",)}
                active = endpoint == target or any(token in endpoint for token in families.get(label, ()))
                if label == "Quizzes" and "listening" in endpoint:
                    active = False
                if label == "Assignments" and "speaking" in endpoint:
                    active = False
                links.append(dict(label=label, url=url_for(target, **params), active=active))
            context = dict(title=group.name, subtitle=group.course.title + " · " + group.academic_term.name,
                course_title=group.course.title, course_public_id=group.course.public_id, group_public_id=group_id,
                parent=url_for("workspace.student_courses" if current_user.role == "student" else "workspace.teacher_groups"),
                parent_label="My courses" if current_user.role == "student" else "My groups", tabs=links)
            if current_user.role == "teacher":
                context["sections"] = [dict(label=label, url=url_for(target, group_public_id=group_id),
                    active=label == section) for label, target in sections]
    elif current_user.is_authenticated and current_user.role == "administrator":
        student_id = args.get("student_public_id") or (args.get("public_id") if endpoint.startswith("admin.student_") else None)
        if student_id:
            student = User.query.filter_by(public_id=student_id, role="student").first()
            if student:
                tabs = [("Overview", "admin.student_detail", {"public_id": student_id}),
                    ("Enrollments", "workspace.student_enrollments", {"student_public_id": student_id}),
                    ("Finance", "admin.student_financial_record", {"student_public_id": student_id}),
                    ("Financial history", "admin.student_financial_history", {"student_public_id": student_id}),
                    ("Account history", "admin.operational_history", {"kind": "student", "public_id": student_id})]
                context = dict(title=student.full_name, subtitle="Student account", parent=url_for("admin.students_list"),
                    parent_label="Students", tabs=[dict(label=label, url=url_for(target, **params), active=endpoint == target or label == "Finance" and nav_active("admin.student_accounts") and endpoint != "admin.student_financial_history")
                    for label, target, params in tabs])
        group_id = args.get("group_public_id") or (args.get("public_id") if endpoint.startswith("admin.group_") else None)
        if not context and group_id:
            group = Group.query.options(joinedload(Group.course), joinedload(Group.academic_term)).filter_by(public_id=group_id).first()
            if group:
                tabs = [("Overview", "admin.group_detail", {"public_id": group_id}),
                    ("Members & staff", "admin.group_members", {"group_public_id": group_id}),
                    ("Schedule", "admin.group_schedules", {"group_public_id": group_id}),
                    ("Attendance", "admin.attendance_overview", {"group": group_id}),
                    ("Grades", "admin.gradebook_overview", {"group": group_id}),
                    ("History", "admin.operational_history", {"kind": "group", "public_id": group_id})]
                context = dict(title=group.name, subtitle=group.course.title + " · " + group.academic_term.name,
                    parent=url_for("admin.groups_list"), parent_label="Groups", tabs=[dict(label=label,
                    url=url_for(target, **params), active=endpoint == target) for label, target, params in tabs])
    g.workspace_context = context
    return context
