"""Administrator **review** of the center's gradebooks (Phase 4 / M08).

Two routes, and both of them are ``GET``::

    GET  /admin/grades
    GET  /admin/groups/<group_public_id>/gradebook

**There is no Administrator mutation route in M08, at all.** No create,
edit, score, release, unrelease, delete, archive, restore, recalculate,
override, import or export exists here -- not hidden behind a permission
check, not disabled in a template: the endpoints do not exist, so a POST
to any gradebook URL under ``/admin`` returns 405 or 404 rather than
being refused by a check somebody could later relax. Grades are entered
and released by the Teacher who taught the group; an Administrator
reviews how the gradebooks are configured and how far along they are.

**What an Administrator may read, and what they deliberately may not.**
They see every Group's gradebook *configuration* and *progress*: the
categories, their weights, whether those weights add up to exactly 100%,
how many items exist, how many are released, how many students are
enrolled, and whether a weighted overall grade is possible at all. That
is what a report about gradebooks is for.

They do **not** see any individual Student's score, percentage, overall
grade or -- above all -- the Teacher's private comment to that Student.
Those are not reporting fields: a comment is a message between one
Teacher and one Student, and an Administrator's legitimate need to check
that a term is being graded on time does not extend to reading it. This
is enforced by the queries this module calls, which never fetch a score
or a comment in the first place, rather than by a template that omits
them.

**Filters are validated into known shapes before they reach SQL.** The
Group filter is a *public* id resolved to an internal one by a lookup
(an unknown value simply matches nothing); the Term filter must parse as
a positive integer that names a real term; the configuration filter must
be exactly ``complete`` or ``incomplete``. Anything else is dropped
rather than guessed at, and no raw query-string value is ever
interpolated into a query.

**Bounded and free of N+1.** One page of Groups is one paged query plus
three keyed aggregates, whatever the page size and whatever the size of
the gradebooks on it; the per-Group report page is five bounded reads
regardless of how many categories, items or students the Group holds.

Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: a gradebook page names a center's Groups and their
academic progress, and a shared or reused cache entry must never be able
to hand it to somebody else.
"""

from flask import abort, current_app, make_response, render_template, request

from app.blueprints.admin import admin_bp
from app.models import BASIS_POINTS_TOTAL, UserRole
from app.security.decorators import roles_required
from app.services.grade_calculations import (
    OVERALL_CATEGORY_NOT_STARTED,
    OVERALL_NO_CATEGORIES,
    OVERALL_WEIGHTS_INCOMPLETE,
    configuration_blockers,
    format_weight,
    total_weight,
)
from app.services.grade_queries import (
    PAGE_SIZE,
    active_student_counts_for_groups,
    admin_groups_page,
    build_admin_report_view,
    build_gradebook_view,
    calculation_categories,
    category_rows,
    group_by_public_id,
    item_counts_for_groups,
    item_rows,
    normalize_page,
    record_counts_for_items,
    term_choice_rows,
)

#: The two accepted values of the ``configured`` filter, mapped to what
#: the query layer takes. Anything else -- including an empty string and
#: a cleverly cased variant -- is dropped, which is what "filters limited
#: to known, validated values" means.
_CONFIGURED_FILTER = {"complete": True, "incomplete": False}

_MAX_BIGINT = 9223372036854775807

#: The Administrator's wording for each configuration gap. Deliberately
#: its own, again: this surface reports on *setup*, so it names the
#: configuration directly -- and it never mentions an individual Student,
#: which is why the per-Student ``missing_scores`` reason has no entry
#: here and can never appear on this page.
_CONFIG_MESSAGES = {
    OVERALL_NO_CATEGORIES: "This group has no grade categories yet.",
    OVERALL_WEIGHTS_INCOMPLETE: (
        "The category weights do not add up to exactly 100%, so no overall grade can be "
        "calculated in this group yet."
    ),
    OVERALL_CATEGORY_NOT_STARTED: (
        "At least one category has no released grade item yet, so no overall grade can be "
        "calculated in this group yet."
    ),
}


def _private_no_store(template, **context):
    """Render a **personalized** Administrator page with the two headers
    every content-bearing gradebook response must carry.

    Same contract as the Teacher and Student surfaces
    (``teacher/assignments._private_no_store``,
    ``student/routes.private_no_store``), applied here rather than
    imported across blueprints, matching how every other module in this
    project already sets it.
    """
    response = make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _group_filter():
    """``(raw_public_id, resolved_group_or_None)`` for the Group filter.

    The raw value is kept only so the form can render what was typed; it
    is never used in a query. An unknown public id resolves to ``None``
    and the page says so, rather than silently listing the whole center.
    """
    raw = (request.args.get("group") or "").strip()
    if not raw:
        return "", None
    return raw, group_by_public_id(raw)


def _term_filter(terms):
    """The internal AcademicTerm id the ``term`` filter names, or
    ``None``.

    Validated against the ids this page actually offers, so a value that
    is not a real term -- missing, non-numeric, zero, negative, absurdly
    large, or simply nonexistent -- is dropped rather than reaching the
    database as a filter.
    """
    value = request.args.get("term", type=int)
    if value is None or value < 1 or value > _MAX_BIGINT:
        return None
    known = {row.id for row in terms}
    return value if value in known else None


def _configured_filter():
    """``True`` for fully configured only, ``False`` for incomplete only,
    ``None`` for either."""
    return _CONFIGURED_FILTER.get((request.args.get("configured") or "").strip())


@admin_bp.get("/grades")
@roles_required(UserRole.ADMINISTRATOR.value)
def gradebook_overview():
    """One bounded page of the center's gradebooks, newest Group first.

    Fixed page size, deterministic SQL ordering, ``LIMIT PAGE_SIZE + 1``
    for the has-next flag and **no** ``COUNT``. Three extra bounded
    aggregates resolve the item counts, the released-item counts and the
    active-student counts for the at-most-``PAGE_SIZE`` Groups on the
    page, so the cost does not grow with how large any gradebook is or
    with how much history exists.

    Only Groups that actually own a grade category are listed: a report
    about gradebooks that included every Group in the center would be the
    Groups page, not this one.
    """
    page = normalize_page(request.args.get("page"))
    raw_group, group = _group_filter()
    terms = term_choice_rows()
    term_id = _term_filter(terms)
    configured = _configured_filter()

    if raw_group and group is None:
        # A filter that names nothing must narrow to nothing, never widen.
        rows, has_next = [], False
    else:
        rows, has_next = admin_groups_page(
            page,
            group_id=None if group is None else group.id,
            term_id=term_id,
            configured=configured,
        )
        if not rows and page > 1:
            page = 1
            rows, has_next = admin_groups_page(
                page,
                group_id=None if group is None else group.id,
                term_id=term_id,
                configured=configured,
            )

    group_ids = [row.id for row in rows]
    return _private_no_store(
        "admin/grades/overview.html",
        groups=build_admin_report_view(
            rows,
            item_counts_for_groups(group_ids),
            active_student_counts_for_groups(group_ids),
        ),
        terms=terms,
        filter_group=raw_group,
        filter_group_row=group,
        filter_group_unknown=bool(raw_group) and group is None,
        filter_term=term_id,
        filter_configured=(
            (request.args.get("configured") or "").strip()
            if (request.args.get("configured") or "").strip() in _CONFIGURED_FILTER
            else ""
        ),
        tz_name=_tz_name(),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@admin_bp.get("/groups/<group_public_id>/gradebook")
@roles_required(UserRole.ADMINISTRATOR.value)
def group_gradebook(group_public_id):
    """One Group's gradebook configuration in full -- read only.

    Every category with its weight, every item with its source, its
    maximum points, its release state and how many of its captured
    students have been scored. Renders no form, no token and no control
    of any kind, because no endpoint exists to aim one at.

    **No individual result appears on this page.** The per-item figures
    are integer ``COUNT``s of how many records carry a score -- never a
    score, never a percentage, never a Student name, and never a
    comment. The Group-level "can an overall grade be calculated?"
    verdict comes from the same
    :func:`~app.services.grade_calculations.configuration_blockers` the
    Teacher page uses, so the two surfaces cannot disagree about whether
    a gradebook is finished.
    """
    group = group_by_public_id(group_public_id)
    if group is None:
        abort(404)

    categories = category_rows(group.id)
    items = item_rows(group.id)
    counts = record_counts_for_items([row.id for row in items])
    view = build_gradebook_view(categories, items, counts)
    weights = [category["weight_basis_points"] for category in view]
    weight_total = total_weight(weights)

    return _private_no_store(
        "admin/grades/detail.html",
        group=group,
        categories=view,
        weight_total_display=format_weight(weight_total),
        weights_complete=weight_total == BASIS_POINTS_TOTAL,
        config_messages=[
            _CONFIG_MESSAGES[code]
            for code in configuration_blockers(calculation_categories(view))
            if code in _CONFIG_MESSAGES
        ],
        student_count=active_student_counts_for_groups([group.id]).get(group.id, 0),
        tz_name=_tz_name(),
    )
