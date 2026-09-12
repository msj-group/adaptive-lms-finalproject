"""The **one** place a grade is calculated (Phase 4 / M08).

Pure functions over ``decimal.Decimal`` and ``int``. No database access,
no Flask, no ``request``, no template, no clock -- so every result here is
a deterministic function of its arguments and can be tested exhaustively
without an application at all.

**Nothing else in this project may compute a grade.** Not a route
handler, not a template filter, not a Jinja expression, not JavaScript,
not a SQL ``SUM`` over a score column. Every percentage, every weighted
total, every "unavailable" decision and every displayed number on the
Teacher, Student and Administrator surfaces comes from this module, so
three surfaces can never quietly disagree about what a Student's grade
is. The query layer (``app/services/grade_queries.py``) fetches rows; it
hands them here; this module answers.

Why the arithmetic is here and not in SQL
-----------------------------------------
``SUM(score)`` would be evaluated by the database, and the two backends
this project runs on do not agree about what that means: MySQL sums
``DECIMAL`` exactly, while the SQLite test backend stores the same column
through a C double. A test that passed on SQLite would then be saying
nothing about the number a real Student is shown. Sums are therefore
taken in Python over exact ``Decimal`` values, on bounded row sets, and
the backend never performs a single grade arithmetic operation.

Exactness, and the one place rounding happens
---------------------------------------------
* **Weights are integers.** A category's weight is an integer count of
  basis points (10,000 = 100.00%), so "does this Group's configuration
  add up to exactly 100%?" is an exact integer equality, never a
  tolerance against a float.
* **Points are exact decimals.** ``max_points`` and ``score`` are
  ``DECIMAL(7, 2)``; they are added with ``Decimal`` addition, which is
  exact.
* **Division is the only inexact step**, and it is carried at
  :data:`CALC_PRECISION` significant digits -- 28, the Python default,
  stated explicitly here rather than inherited from whatever context a
  caller happens to be in, so the result cannot depend on ambient state.
* **Rounding happens exactly once, at the presentation boundary.**
  :func:`format_percentage` and :func:`format_points` are the only
  functions that round, they round ``ROUND_HALF_UP`` to two decimal
  places, and they return **strings**. An internal value is never
  rounded, re-rounded, or stored rounded, so a category percentage shown
  as ``83.33`` still contributes its full precision to the weighted
  overall grade.

``ROUND_HALF_UP`` is chosen deliberately over Python's default
``ROUND_HALF_EVEN``: a Student reading 87.125 expects 87.13, not 87.12,
and "round half away from zero" is what a human means by rounding a
grade. Scores are never negative, so the away-from-zero direction is
always up.

The rules, stated once
----------------------
For one Student and one category::

    earned   = sum of that Student's scores on the category's RELEASED items
    possible = sum of max_points of those same items
    percentage = earned / possible * 100

Only **released** items count, ever. A draft item, a draft score and a
draft comment are Teacher-only working material; including one would
publish it.

The weighted overall grade is produced **only** when all three of these
hold, and otherwise is reported as unavailable with a reason rather than
as a misleading partial total::

    1. the Group's category weights total exactly 10,000 basis points;
    2. every category has at least one released item;
    3. the Student has a complete score on every released item.

    overall = sum over categories of (percentage * weight / 10,000)

Condition 3 is per Student, and is what protects a Student who joined the
Group after some items had already been created: they hold no record for
those items, so no number is invented for them and no partial total is
shown. Their per-category progress is still displayed, computed over the
items they actually hold a record for -- an honest statement of what they
have been graded on, rather than a percentage silently deflated by work
that was never theirs.
"""

from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.models import BASIS_POINTS_TOTAL

#: Significant digits carried through every internal division. Stated
#: explicitly (rather than relying on the ambient ``decimal`` context) so
#: a result never depends on what some other part of the process did to
#: the context. 28 is Python's default and is far more than a
#: ``DECIMAL(7, 2)`` grade needs.
CALC_PRECISION = 28

#: The display rounding rule, applied exactly once, at the presentation
#: boundary. Two decimal places, half away from zero.
DISPLAY_EXPONENT = Decimal("0.01")
DISPLAY_ROUNDING = ROUND_HALF_UP

_HUNDRED = Decimal(100)
_ZERO = Decimal(0)

# ---------------------------------------------------------------------------
# Why an overall grade is unavailable -- short codes, never sentences
# ---------------------------------------------------------------------------
#
# Codes rather than wording, for the reason M07 states about its
# occurrence rules: the Teacher, the Student and the Administrator need
# *different* sentences for the same fact -- a Teacher is told which part
# of the configuration to fix, a Student is told that their term is not
# finished being graded, and an Administrator is shown a yes/no column --
# and each surface declares its own wording exactly once. A shared
# sentence here would either leak configuration detail to Students or
# tell Teachers nothing useful.

#: The Group has no grade categories at all.
OVERALL_NO_CATEGORIES = "no_categories"
#: The categories exist but their weights do not total exactly 100.00%.
OVERALL_WEIGHTS_INCOMPLETE = "weights_incomplete"
#: At least one category holds no released item, so part of the weight
#: describes nothing that has been graded yet.
OVERALL_CATEGORY_NOT_STARTED = "category_not_started"
#: This Student has no score on at least one released item -- either it
#: has not been graded, or they were not on that item's captured roster.
OVERALL_MISSING_SCORES = "missing_scores"


class CategoryResult:
    """One category's outcome for one Student.

    ``percentage`` is an exact-as-possible ``Decimal`` (never rounded)
    or ``None`` when the category has no released item this Student holds
    a record for. ``complete`` says whether this Student has a real score
    on **every** released item in the category, which is what the overall
    grade requires.
    """

    __slots__ = (
        "category_public_id",
        "title",
        "weight_basis_points",
        "earned",
        "possible",
        "percentage",
        "released_item_count",
        "scored_item_count",
        "complete",
    )

    def __init__(
        self,
        category_public_id,
        title,
        weight_basis_points,
        earned,
        possible,
        percentage,
        released_item_count,
        scored_item_count,
        complete,
    ):
        self.category_public_id = category_public_id
        self.title = title
        self.weight_basis_points = weight_basis_points
        self.earned = earned
        self.possible = possible
        self.percentage = percentage
        self.released_item_count = released_item_count
        self.scored_item_count = scored_item_count
        self.complete = complete


class StudentGradeSummary:
    """One Student's whole gradebook outcome in one Group.

    ``overall`` is an unrounded ``Decimal`` percentage, or ``None`` --
    and when it is ``None``, ``unavailable_reason`` says which of the
    four rules failed. The two are always consistent: exactly one of them
    is set.
    """

    __slots__ = ("categories", "overall", "unavailable_reason", "total_weight_basis_points")

    def __init__(self, categories, overall, unavailable_reason, total_weight_basis_points):
        self.categories = categories
        self.overall = overall
        self.unavailable_reason = unavailable_reason
        self.total_weight_basis_points = total_weight_basis_points


# ---------------------------------------------------------------------------
# Weight configuration -- exact integer arithmetic only
# ---------------------------------------------------------------------------


def total_weight(weights):
    """The exact integer sum of a Group's category weights, in basis
    points.

    Integers in, integer out: no float and no ``Decimal`` is involved, so
    comparing the result against :data:`~app.models.BASIS_POINTS_TOTAL`
    is an exact equality rather than a tolerance.
    """
    return sum(int(weight) for weight in weights)


def weights_are_complete(weights):
    """True when this Group's categories total **exactly** 100.00%.

    Not "close to", not "at least": the one condition under which a
    weighted overall grade is meaningful at all.
    """
    return total_weight(weights) == BASIS_POINTS_TOTAL


def weights_fit(weights, addition):
    """True when adding `addition` basis points to `weights` still leaves
    the Group at or below 100.00%.

    The rule behind "a Group's active category total may not exceed
    10,000 basis points", used both by the friendly pre-lock form guard
    and by the authoritative post-lock recheck, so the two can never
    disagree about what fits. `weights` must already exclude the category
    being edited.
    """
    return total_weight(weights) + int(addition) <= BASIS_POINTS_TOTAL


def remaining_weight(weights):
    """How many basis points a Group still has left to allocate. Never
    negative -- an over-allocated Group is impossible by construction,
    and reporting a negative remainder would only invite a caller to do
    arithmetic on it."""
    return max(0, BASIS_POINTS_TOTAL - total_weight(weights))


# ---------------------------------------------------------------------------
# Percentages
# ---------------------------------------------------------------------------


def category_percentage(earned, possible):
    """``earned / possible * 100`` as an unrounded ``Decimal``, or
    ``None`` when the category cannot be scored.

    ``None`` -- not zero -- is returned when `possible` is ``None``, zero
    or negative. Zero would be a *claim* (this Student scored nothing),
    and "there is nothing released to score yet" is a different
    statement; conflating them is how a gradebook ends up showing a
    brand-new term as 0%.
    """
    if possible is None or earned is None:
        return None
    possible = Decimal(possible)
    if possible <= _ZERO:
        return None
    with localcontext() as ctx:
        ctx.prec = CALC_PRECISION
        ctx.rounding = DISPLAY_ROUNDING
        return (Decimal(earned) / possible) * _HUNDRED


def weighted_overall(results):
    """``sum(percentage * weight / 10,000)`` over `results`, as an
    unrounded ``Decimal``.

    The caller must already have established that the overall grade is
    available (see :func:`summarize`); this function does the arithmetic
    and nothing else. Each term is computed exactly as the approved
    formula is written -- percentage times weight divided by 10,000 --
    rather than being algebraically rearranged, so the code and the rule
    can be read side by side.
    """
    with localcontext() as ctx:
        ctx.prec = CALC_PRECISION
        ctx.rounding = DISPLAY_ROUNDING
        total = _ZERO
        for result in results:
            total += (
                result.percentage
                * Decimal(result.weight_basis_points)
                / Decimal(BASIS_POINTS_TOTAL)
            )
        return total


# ---------------------------------------------------------------------------
# The whole summary, for one Student
# ---------------------------------------------------------------------------


def build_category_result(category, scores):
    """One :class:`CategoryResult` from one category's released items and
    one Student's scores on them.

    `category` is a plain dict carrying ``public_id``, ``title``,
    ``weight_basis_points`` and ``released_items`` -- the released items
    being ``(item_public_id, max_points)`` pairs. `scores` maps an item
    public id to that Student's exact ``Decimal`` score, and simply omits
    every item the Student has no record for or has not been graded on.

    ``earned`` and ``possible`` are summed over exactly the items the
    Student **holds a scored record for**, so the percentage is an honest
    statement about the work that was actually theirs. ``complete`` is
    what records the difference: it is true only when that set is the
    whole set of released items in the category, which is the condition
    the weighted overall grade requires.
    """
    released_items = list(category["released_items"])
    earned, possible, scored = _ZERO, _ZERO, 0
    for item_public_id, max_points in released_items:
        score = scores.get(item_public_id)
        if score is None:
            continue
        earned += Decimal(score)
        possible += Decimal(max_points)
        scored += 1
    return CategoryResult(
        category_public_id=category["public_id"],
        title=category["title"],
        weight_basis_points=category["weight_basis_points"],
        earned=earned if scored else None,
        possible=possible if scored else None,
        percentage=category_percentage(earned, possible) if scored else None,
        released_item_count=len(released_items),
        scored_item_count=scored,
        complete=bool(released_items) and scored == len(released_items),
    )


def summarize(categories, scores):
    """One Student's complete :class:`StudentGradeSummary` for one Group.

    `categories` is the Group's **whole** category list (each a dict as
    described in :func:`build_category_result`), in display order;
    `scores` maps item public id to that Student's ``Decimal`` score.

    The three availability rules are applied in a fixed order and the
    **first** failure is the reported reason, so the message a Student or
    Teacher sees always names the thing to fix first rather than the last
    thing checked:

    1. there is at least one category;
    2. the weights total exactly 100.00%;
    3. every category holds at least one released item;
    4. this Student has a score on every one of those released items.

    Rules 1-3 are properties of the Group's configuration and are the
    same for everybody in it; rule 4 is personal. When any of them fails,
    ``overall`` is ``None`` and the per-category results are still
    returned in full -- progress is always shown, only the misleading
    single number is withheld.
    """
    results = [build_category_result(category, scores) for category in categories]
    weights = [result.weight_basis_points for result in results]
    summary_total = total_weight(weights)

    reason = None
    if not results:
        reason = OVERALL_NO_CATEGORIES
    elif summary_total != BASIS_POINTS_TOTAL:
        reason = OVERALL_WEIGHTS_INCOMPLETE
    elif any(result.released_item_count == 0 for result in results):
        reason = OVERALL_CATEGORY_NOT_STARTED
    elif not all(result.complete for result in results):
        reason = OVERALL_MISSING_SCORES

    overall = None if reason is not None else weighted_overall(results)
    return StudentGradeSummary(results, overall, reason, summary_total)


def configuration_blockers(categories):
    """Every reason this Group's configuration cannot yet produce a
    weighted overall grade **for anybody**, as codes, most fundamental
    first. An empty list means the configuration is complete.

    Deliberately the Group-level subset of :func:`summarize`'s rules --
    rules 1 to 3, never rule 4 -- so the Teacher's gradebook page can
    state what is wrong with the *setup* without naming a Student, and so
    that page and the per-Student calculation can never disagree about
    what "configured" means. `categories` carries ``weight_basis_points``
    and ``released_item_count``.
    """
    blockers = []
    if not categories:
        return [OVERALL_NO_CATEGORIES]
    if total_weight(c["weight_basis_points"] for c in categories) != BASIS_POINTS_TOTAL:
        blockers.append(OVERALL_WEIGHTS_INCOMPLETE)
    if any(c["released_item_count"] == 0 for c in categories):
        blockers.append(OVERALL_CATEGORY_NOT_STARTED)
    return blockers


# ---------------------------------------------------------------------------
# The presentation boundary -- the ONLY place anything is rounded
# ---------------------------------------------------------------------------


def format_percentage(value):
    """A percentage as a two-decimal string, or ``None``.

    The single rounding step in the whole milestone: ``ROUND_HALF_UP`` to
    two decimal places. Returns a **string**, deliberately, so a caller
    cannot accidentally keep calculating with a rounded number -- the
    type itself makes "round once, at the end" impossible to get wrong.
    """
    if value is None:
        return None
    return str(Decimal(value).quantize(DISPLAY_EXPONENT, rounding=DISPLAY_ROUNDING))


def format_points(value):
    """Points as a two-decimal string, or ``None``.

    ``max_points`` and ``score`` are already exact two-decimal values, so
    this normally only fixes the *presentation* (``5`` -> ``5.00``);
    quantizing anyway keeps one rule for every number this milestone
    displays.
    """
    if value is None:
        return None
    return str(Decimal(value).quantize(DISPLAY_EXPONENT, rounding=DISPLAY_ROUNDING))


def format_weight(basis_points):
    """An integer basis-point weight as a percentage string.

    Exact by construction: 10,000 basis points is exactly 100.00% and the
    division by 100 of an integer is exact in ``Decimal``. Nothing is
    rounded here -- there is nothing to round.
    """
    return str(
        (Decimal(int(basis_points)) / Decimal(100)).quantize(
            DISPLAY_EXPONENT, rounding=DISPLAY_ROUNDING
        )
    )
