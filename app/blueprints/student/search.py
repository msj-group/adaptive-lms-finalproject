"""Student global search across authorized learning content (M13).

``GET /student/search`` only -- a normal server-rendered form, never
live / AJAX. This page is **Student-only**: anonymous users get the
standard login redirect and every other authenticated role gets 403
(``roles_required``). There is deliberately no Teacher / Administrator /
Researcher global-search page, and the existing Administrator
resource-list searches are untouched.

Authorization for every result is SQL-scoped to ``current_user.id`` in
``app/services/search_queries.py`` -- this route only normalises the
query + filters, resolves the optional Group filter against the
Student's own authorized enrollments, picks the display state, and sets
private / non-cacheable response headers. An unknown or unauthorized
``group`` value yields an empty, non-disclosing result rather than
revealing whether that Group exists.

Phase 4 / M09 adds an ``announcement`` result type. Its authorization is
the M09 visibility rule rather than the M13 enrollment chain -- a Center
announcement is readable by any active Student, with or without an
enrollment -- and that rule is imported from the announcement query layer
rather than restated here, so this page cannot become one row more
generous than the announcement feed itself.
"""

from flask import make_response, render_template, request
from flask_login import current_user

from app.blueprints.student import student_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import search_queries
from app.services.search_queries import (
    authorized_search_groups,
    has_any_results,
    search_learning_content,
)
from app.services.search_terms import (
    MIN_QUERY_LENGTH,
    normalize_content_type,
    normalize_material_kind,
    normalize_query,
)

_CONTENT_TYPE_CHOICES = (
    ("all", "All content"),
    ("course", "Courses"),
    ("unit", "Units"),
    ("lesson", "Lessons"),
    ("material", "Materials"),
    ("announcement", "Announcements"),
)
_MATERIAL_KIND_CHOICES = (
    ("all", "All materials"),
    ("rich_text", "Rich text"),
    ("external_link", "External links"),
    ("file", "Files"),
)


def _empty_results(content_type):
    wanted = (
        search_queries.CONTENT_TYPE_ORDER
        if content_type == "all"
        else (content_type,)
    )
    return {
        name: {"items": [], "has_more": False}
        for name in search_queries.CONTENT_TYPE_ORDER
        if name in wanted
    }


@student_bp.get("/search")
@roles_required(UserRole.STUDENT.value)
def search():
    raw_q = request.args.get("q", "")
    content_type = normalize_content_type(request.args.get("type"))
    material_kind = normalize_material_kind(request.args.get("kind"))
    group_param = (request.args.get("group") or "").strip()

    query_norm = normalize_query(raw_q)
    groups = authorized_search_groups(current_user.id)
    selected_group = next((g for g in groups if g["public_id"] == group_param), None)
    group_unresolved = bool(group_param) and selected_group is None

    has_query = bool(raw_q.strip())
    too_short = has_query and query_norm.too_short

    results = None
    if query_norm.is_searchable:
        if group_unresolved:
            results = _empty_results(content_type)
        else:
            results = search_learning_content(
                current_user.id,
                query_norm,
                content_type=content_type,
                group_public_id=selected_group["public_id"] if selected_group else None,
                material_kind=material_kind,
            )

    response = make_response(
        render_template(
            "student/search.html",
            q=query_norm.text,
            raw_q=raw_q,
            content_type=content_type,
            material_kind=material_kind,
            group_param=group_param,
            selected_group=selected_group,
            groups=groups,
            results=results,
            searched=results is not None,
            any_results=results is not None and has_any_results(results),
            too_short=too_short,
            has_query=has_query,
            min_query_length=MIN_QUERY_LENGTH,
            content_type_choices=_CONTENT_TYPE_CHOICES,
            material_kind_choices=_MATERIAL_KIND_CHOICES,
        )
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response
