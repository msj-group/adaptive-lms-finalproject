"""A Student's own released grades (Phase 4 / M08).

Two routes, and both of them are ``GET``::

    GET  /student/grades
    GET  /student/grades/<group_public_id>

There is deliberately **no** POST, PUT, PATCH or DELETE anywhere on the
Student grade surface -- no self-grading, no correction request, no
appeal, no dispute, no acknowledgement and no re-calculation. A Student
never writes a grade record, so no write path exists for one to reach.

**What a Student sees is their own released grades, and nothing else.**
The visibility formula lives in the SQL ``WHERE`` clause of
``app/services/grade_queries.py`` and is applied once, keyed off
``current_user.id``:

- the record's ``student_id`` is this Student;
- the acting account still has the ``student`` role and an ``active``
  status -- a foreign key proves a row exists, never that it is still a
  Student's, so the join re-proves it rather than trusting the session;
- the record's GradeItem is **released**.

**What is deliberately excluded**, and excluded by never being fetched
rather than by being hidden in a template: any other Student's record,
any other Student's name, the Group roster, any **draft** item, any draft
score or comment, the Teacher's identity, the grader's name, every
version, and every internal numeric id. A draft is a Teacher's work in
progress -- a roster of empty boxes -- and showing one would tell a
Student they have no mark for work nobody has finished grading.

**History survives.** The Enrollment, the Group and the academic
ancestors are deliberately *not* required to still be active: a Student
who has withdrawn keeps reading exactly the grades that were released to
them, which is the whole reason the roster is captured at item creation
instead of re-derived later. What a withdrawal never does is give
anybody access to somebody else's record -- the ``student_id`` equality
is on every query, on every route, in every direction.

**Every number on this page comes from the one calculation service.**
The per-item percentage, the per-category percentage and the weighted
overall grade are all produced by
``app/services/grade_calculations.py`` in ``Decimal``, rounded exactly
once for display. Nothing is calculated in this module, in a template, or
in JavaScript.

**An unavailable overall grade says so plainly.** When the Group's
weights do not total 100%, a category has nothing released yet, or this
Student does not hold a score on every released item, the page shows
their per-category progress and states that the overall grade is not
available yet -- rather than a partial weighted total that would read as
a real result. The Student's wording is deliberately its own: it explains
that their term is still being graded, never which part of the Teacher's
configuration is unfinished.
"""

from flask import abort, request
from flask_login import current_user

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.grade_calculations import (
    OVERALL_CATEGORY_NOT_STARTED,
    OVERALL_MISSING_SCORES,
    OVERALL_NO_CATEGORIES,
    OVERALL_WEIGHTS_INCOMPLETE,
    format_percentage,
    format_weight,
    summarize,
)
from app.services.grade_queries import (
    PAGE_SIZE,
    build_student_grade_view,
    group_by_public_id,
    normalize_page,
    student_category_rows,
    student_group_rows,
    student_released_rows,
)

#: The Student's wording for each "no overall grade yet" reason.
#: Deliberately **not** the Teacher's wording for the same codes: a
#: Teacher is told which part of the setup to fix, a Student is told that
#: their term is still being graded. Neither sentence names a category
#: weight a Student has no business auditing, and none of them names
#: another Student.
_OVERALL_MESSAGES = {
    OVERALL_NO_CATEGORIES: (
        "Your teacher has not set up this group's gradebook yet, so there is no overall "
        "grade to show."
    ),
    OVERALL_WEIGHTS_INCOMPLETE: (
        "Your teacher is still setting up how this group's grade is calculated, so the "
        "overall grade is not available yet. The results below are final."
    ),
    OVERALL_CATEGORY_NOT_STARTED: (
        "Some parts of this group's grade have not been released yet, so the overall grade "
        "is not available yet. The results below are final."
    ),
    OVERALL_MISSING_SCORES: (
        "You do not have a result for every released item in this group yet, so an overall "
        "grade cannot be calculated for you. The results below are final."
    ),
}


@student_bp.get("/grades")
@roles_required(UserRole.STUDENT.value)
def grades_list():
    """One bounded page of the Groups this Student has released grades
    in, newest Group first.

    A Group appears exactly when this Student holds at least one record
    on a released item of it -- the same condition the detail page
    applies -- so the list can never offer a link to a page that would
    then turn out to be empty.

    Fixed page size, deterministic SQL ordering and
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag with **no** ``COUNT``:
    an exact total would be both an unbounded scan and a disclosure of
    how much history exists beyond the page.

    The response carries ``Cache-Control: private, no-store`` and
    ``Vary: Cookie``: this is one person's grades, and a shared or reused
    cache entry must never be able to hand it to anybody else.
    """
    page = normalize_page(request.args.get("page"))
    rows, has_next = student_group_rows(current_user.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = student_group_rows(current_user.id, page)

    return private_no_store(
        "student/grades/list.html",
        groups=[
            {
                "group_public_id": row.group_public_id,
                "group_name": row.group_name,
                "course_title": row.course_title,
                "term_name": row.term_name,
                "released_count": row.released_count,
            }
            for row in rows
        ],
        active_nav="grades",
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@student_bp.get("/grades/<group_public_id>")
@roles_required(UserRole.STUDENT.value)
def group_grades(group_public_id):
    """This Student's own released grades in one Group, with their
    category summaries and -- when it is available -- their weighted
    overall grade.

    **The 404 is non-disclosing.** A Group this Student has nothing
    released in fails here exactly like a Group that does not exist:
    the released rows are fetched first, by ``student_id`` *and* Group
    public id in one ``WHERE`` clause, and an empty result aborts before
    the Group is ever named. Another Student's Group, a Group whose items
    are all still drafts, and an invented public id are indistinguishable
    from the outside.

    Three bounded reads: this Student's released records, the Group's
    category configuration, and the Group's display name. The
    configuration is needed because "the weights total 100% and every
    category has a released item" is a fact about the *Group*, not about
    this Student -- but nothing a Student may not see is fetched with it:
    no draft item, no other Student, no score, no comment and no roster.
    """
    rows = student_released_rows(current_user.id, group_public_id)
    if not rows:
        abort(404)

    group = group_by_public_id(group_public_id)
    if group is None:  # pragma: no cover -- the rows above joined this Group
        abort(404)

    categories = student_category_rows(group.id)
    scores = {
        row.item_public_id: row.score for row in rows if row.score is not None
    }
    summary = summarize(categories, scores)

    by_category = {}
    for item in build_student_grade_view(rows):
        by_category.setdefault(item["category_title"], []).append(item)

    return private_no_store(
        "student/grades/detail.html",
        group=group,
        categories=[
            {
                "title": result.title,
                "weight_display": format_weight(result.weight_basis_points),
                "percentage_display": format_percentage(result.percentage),
                "complete": result.complete,
                "scored_item_count": result.scored_item_count,
                "released_item_count": result.released_item_count,
                "grade_items": by_category.get(result.title, []),
            }
            for result in summary.categories
        ],
        overall_display=format_percentage(summary.overall),
        overall_message=(
            None
            if summary.unavailable_reason is None
            else _OVERALL_MESSAGES[summary.unavailable_reason]
        ),
        active_nav="grades",
    )
