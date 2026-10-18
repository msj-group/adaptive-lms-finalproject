"""Opt-in GET result fragments; existing views retain all query/auth decisions."""
from flask import current_app, render_template, request

# Explicit presentation allowlist. No POST, endpoint, query or permission shortcut.
FRAGMENTS = {
    "admin/students/list.html": "admin/students/_results.html",
    "admin/teachers/list.html": "admin/teachers/_results.html",
    "admin/groups/enrollment_operation.html": "admin/groups/_selection_results.html",
    "student/activities.html": "student/template/_activity_results.html",
    "teacher/activities.html": "teacher/activities/_results.html",
    "student/search.html": "student/_search_results.html",
    "student/announcements/detail.html": "student/announcements/_reader.html",
    "admin/groups/list.html": "admin/groups/_query_results.html",
    "admin/schedules/overview.html": "admin/schedules/_query_results.html",
    "admin/courses/list.html": "admin/courses/_query_results.html",
    "admin/announcements/overview.html": "admin/announcements/_query_results.html",
    "admin/grades/overview.html": "admin/grades/_query_results.html",
    "admin/general_finance/accounts.html": "admin/general_finance/_account_results.html",
    "admin/general_finance/register.html": "admin/general_finance/_register_results.html",
    "teacher/announcements/feed.html": "teacher/announcements/_feed_results.html",
    "notifications/inbox.html": "notifications/_inbox_results.html",
    "workspace/search.html": "workspace/_search_results.html",
}


def render_query_page(template, **context):
    fragment = request.method == "GET" and request.headers.get("X-AELMS-Query") == "results"
    selected = FRAGMENTS.get(template) if fragment else None
    response = current_app.make_response(render_template(selected or template, **context))
    if selected:
        response.headers["X-AELMS-Query"] = "results"
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    response.vary.add("X-AELMS-Query")
    return response
