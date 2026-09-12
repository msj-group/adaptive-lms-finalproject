"""The one grade-calculation service (Phase 4 / M08).

Pure functions over ``Decimal`` and ``int``, so every case here needs no
application, no database and no request. These tests are the contract the
Teacher, Student and Administrator surfaces all depend on: if a number is
wrong anywhere in the gradebook, it is wrong here first.
"""

from decimal import Decimal

from app.services.grade_calculations import (
    BASIS_POINTS_TOTAL,
    CALC_PRECISION,
    OVERALL_CATEGORY_NOT_STARTED,
    OVERALL_MISSING_SCORES,
    OVERALL_NO_CATEGORIES,
    OVERALL_WEIGHTS_INCOMPLETE,
    build_category_result,
    category_percentage,
    configuration_blockers,
    format_percentage,
    format_points,
    format_weight,
    remaining_weight,
    summarize,
    total_weight,
    weighted_overall,
    weights_are_complete,
    weights_fit,
)


def cat(public_id, title, weight, released_items):
    return {
        "public_id": public_id,
        "title": title,
        "weight_basis_points": weight,
        "released_item_count": len(released_items),
        "released_items": [(pid, Decimal(points)) for pid, points in released_items],
    }


def scores(**pairs):
    return {key: Decimal(value) for key, value in pairs.items()}


# ===========================================================================
# Weights -- exact integer arithmetic, never a float tolerance
# ===========================================================================


def test_weights_are_summed_as_exact_integers():
    assert total_weight([6000, 4000]) == BASIS_POINTS_TOTAL
    assert total_weight([]) == 0
    assert total_weight([1]) == 1


def test_a_group_is_complete_only_at_exactly_one_hundred_percent():
    assert weights_are_complete([6000, 4000])
    assert weights_are_complete([10000])
    assert not weights_are_complete([6000, 3999])
    assert not weights_are_complete([6000, 4001])
    assert not weights_are_complete([])


def test_thirds_add_up_exactly_because_they_are_integers():
    """3333 + 3333 + 3334 is exactly 10000.

    The point of basis points: a Teacher can split a term three ways and
    the total is exact, where three float 0.3333s would not be.
    """
    assert weights_are_complete([3333, 3333, 3334])


def test_weights_fit_refuses_anything_over_one_hundred_percent():
    assert weights_fit([6000], 4000)
    assert not weights_fit([6000], 4001)
    assert weights_fit([], BASIS_POINTS_TOTAL)
    assert not weights_fit([], BASIS_POINTS_TOTAL + 1)


def test_remaining_weight_never_goes_negative():
    assert remaining_weight([6000]) == 4000
    assert remaining_weight([]) == BASIS_POINTS_TOTAL
    assert remaining_weight([10000]) == 0
    # An over-allocated group is impossible by construction; reporting a
    # negative remainder would only invite a caller to do arithmetic on it.
    assert remaining_weight([12000]) == 0


# ===========================================================================
# Category percentage
# ===========================================================================


def test_category_percentage_is_exact_for_terminating_divisions():
    assert category_percentage(Decimal("18.00"), Decimal("20.00")) == Decimal(90)
    assert category_percentage(Decimal("15.50"), Decimal("20.00")) == Decimal("77.5")


def test_category_percentage_carries_full_precision_for_repeating_divisions():
    value = category_percentage(Decimal("2"), Decimal("3"))
    # Carried at CALC_PRECISION significant digits, not rounded to two.
    assert str(value).startswith("66.6666666")
    assert len(str(value).replace(".", "").rstrip("0")) > 10
    # And rounded exactly once, at the presentation boundary.
    assert format_percentage(value) == "66.67"


def test_a_category_with_nothing_possible_is_none_and_never_zero():
    """``None`` and ``0`` are different statements.

    Zero claims the Student scored nothing; ``None`` says there is
    nothing released to score. Conflating them is how a gradebook shows a
    brand-new term as 0%.
    """
    assert category_percentage(Decimal(0), Decimal(0)) is None
    assert category_percentage(Decimal(0), None) is None
    assert category_percentage(None, Decimal("10")) is None
    assert category_percentage(Decimal("5"), Decimal("-1")) is None


def test_a_genuine_zero_score_is_zero_percent_not_unavailable():
    assert category_percentage(Decimal("0.00"), Decimal("20.00")) == Decimal(0)
    assert format_percentage(Decimal(0)) == "0.00"


# ===========================================================================
# build_category_result -- what a Student actually holds
# ===========================================================================


def test_a_category_result_sums_multiple_released_items():
    result = build_category_result(
        cat("c1", "Homework", 6000, [("i1", "20.00"), ("i2", "30.00")]),
        scores(i1="18.00", i2="27.00"),
    )
    assert result.earned == Decimal("45.00")
    assert result.possible == Decimal("50.00")
    assert result.percentage == Decimal(90)
    assert result.complete is True
    assert result.scored_item_count == 2
    assert result.released_item_count == 2


def test_a_missing_score_makes_the_category_incomplete_but_still_shows_progress():
    """A Student who joined after an item was created holds no record for
    it. Their percentage is computed over what was actually theirs -- an
    honest statement -- and ``complete`` records that it is not the whole
    category, which is what withholds the overall grade.
    """
    result = build_category_result(
        cat("c1", "Homework", 6000, [("i1", "20.00"), ("i2", "30.00")]),
        scores(i1="18.00"),
    )
    assert result.earned == Decimal("18.00")
    assert result.possible == Decimal("20.00")
    assert result.percentage == Decimal(90)
    assert result.complete is False
    assert result.scored_item_count == 1
    assert result.released_item_count == 2


def test_a_category_with_no_released_item_has_no_percentage():
    result = build_category_result(cat("c1", "Speaking", 4000, []), {})
    assert result.percentage is None
    assert result.earned is None
    assert result.possible is None
    assert result.complete is False


def test_another_students_scores_are_simply_not_in_the_mapping():
    """The service is handed one Student's scores, keyed by item public
    id. There is no way for another Student's number to be in it, which
    is why isolation is a property of the input rather than of a filter
    here."""
    result = build_category_result(
        cat("c1", "Homework", 6000, [("i1", "20.00")]),
        scores(i1="18.00", i_other="1.00"),
    )
    assert result.earned == Decimal("18.00")
    assert result.possible == Decimal("20.00")


def test_a_draft_item_cannot_be_counted_because_it_is_not_in_released_items():
    result = build_category_result(
        cat("c1", "Homework", 6000, [("released", "20.00")]),
        scores(released="20.00", draft="0.00"),
    )
    assert result.earned == Decimal("20.00")
    assert result.possible == Decimal("20.00")
    assert result.percentage == Decimal(100)


# ===========================================================================
# The weighted overall grade
# ===========================================================================


def test_two_weighted_categories_combine_exactly():
    summary = summarize(
        [
            cat("c1", "Homework", 6000, [("i1", "20.00")]),
            cat("c2", "Speaking", 4000, [("i2", "40.00")]),
        ],
        scores(i1="18.00", i2="30.00"),
    )
    # 90% * 0.60 + 75% * 0.40 = 54 + 30 = 84
    assert summary.unavailable_reason is None
    assert summary.overall == Decimal(84)
    assert format_percentage(summary.overall) == "84.00"


def test_three_categories_with_repeating_percentages_stay_exact_until_display():
    summary = summarize(
        [
            cat("c1", "A", 3333, [("i1", "3.00")]),
            cat("c2", "B", 3333, [("i2", "3.00")]),
            cat("c3", "C", 3334, [("i3", "3.00")]),
        ],
        scores(i1="2.00", i2="1.00", i3="3.00"),
    )
    assert summary.unavailable_reason is None
    # No float appears anywhere: the result is an exact Decimal carried at
    # full precision, rounded once for display.
    assert isinstance(summary.overall, Decimal)
    assert format_percentage(summary.overall) == "66.67"


def test_weighted_overall_uses_the_approved_formula_term_by_term():
    class R:
        def __init__(self, percentage, weight):
            self.percentage = percentage
            self.weight_basis_points = weight

    total = weighted_overall([R(Decimal(90), 6000), R(Decimal(75), 4000)])
    assert total == Decimal(84)


def test_a_perfect_term_is_exactly_one_hundred():
    summary = summarize(
        [
            cat("c1", "A", 5000, [("i1", "17.00")]),
            cat("c2", "B", 5000, [("i2", "13.00")]),
        ],
        scores(i1="17.00", i2="13.00"),
    )
    assert summary.overall == Decimal(100)
    assert format_percentage(summary.overall) == "100.00"


# ===========================================================================
# When the overall grade is unavailable, and why
# ===========================================================================


def test_no_categories_at_all():
    summary = summarize([], {})
    assert summary.overall is None
    assert summary.unavailable_reason == OVERALL_NO_CATEGORIES
    assert summary.categories == []


def test_weights_short_of_one_hundred_percent_withhold_the_overall_grade():
    summary = summarize(
        [cat("c1", "Homework", 6000, [("i1", "20.00")])], scores(i1="20.00")
    )
    assert summary.overall is None
    assert summary.unavailable_reason == OVERALL_WEIGHTS_INCOMPLETE
    # Progress is still shown -- only the misleading single number is withheld.
    assert summary.categories[0].percentage == Decimal(100)


def test_weights_over_one_hundred_percent_also_withhold_it():
    summary = summarize(
        [
            cat("c1", "A", 6000, [("i1", "10.00")]),
            cat("c2", "B", 6000, [("i2", "10.00")]),
        ],
        scores(i1="10.00", i2="10.00"),
    )
    assert summary.unavailable_reason == OVERALL_WEIGHTS_INCOMPLETE


def test_a_category_with_no_released_item_withholds_the_overall_grade():
    summary = summarize(
        [
            cat("c1", "Homework", 6000, [("i1", "20.00")]),
            cat("c2", "Speaking", 4000, []),
        ],
        scores(i1="18.00"),
    )
    assert summary.overall is None
    assert summary.unavailable_reason == OVERALL_CATEGORY_NOT_STARTED


def test_a_student_missing_one_score_withholds_only_their_own_overall_grade():
    categories = [
        cat("c1", "Homework", 6000, [("i1", "20.00"), ("i2", "20.00")]),
        cat("c2", "Speaking", 4000, [("i3", "40.00")]),
    ]
    complete = summarize(categories, scores(i1="18.00", i2="16.00", i3="30.00"))
    partial = summarize(categories, scores(i1="18.00", i3="30.00"))
    assert complete.unavailable_reason is None
    assert partial.overall is None
    assert partial.unavailable_reason == OVERALL_MISSING_SCORES
    # The same configuration, two different answers -- one per Student.
    assert complete.total_weight_basis_points == partial.total_weight_basis_points


def test_the_first_failing_rule_is_the_one_reported():
    """Rules are checked most-fundamental first, so the message always
    names the thing to fix first rather than the last thing checked."""
    summary = summarize([cat("c1", "Homework", 6000, [])], {})
    assert summary.unavailable_reason == OVERALL_WEIGHTS_INCOMPLETE


# ===========================================================================
# Group-level configuration blockers
# ===========================================================================


def test_configuration_blockers_are_the_group_level_subset_only():
    assert configuration_blockers([]) == [OVERALL_NO_CATEGORIES]
    assert configuration_blockers(
        [cat("c1", "A", 6000, [("i1", "10.00")])]
    ) == [OVERALL_WEIGHTS_INCOMPLETE]
    assert configuration_blockers(
        [cat("c1", "A", 10000, [])]
    ) == [OVERALL_CATEGORY_NOT_STARTED]
    assert configuration_blockers(
        [cat("c1", "A", 10000, [("i1", "10.00")])]
    ) == []


def test_configuration_blockers_never_report_a_per_student_reason():
    """``missing_scores`` is personal, so it can never appear on a page
    that describes the Group rather than a Student."""
    for categories in ([], [cat("c1", "A", 6000, [])], [cat("c1", "A", 10000, [])]):
        assert OVERALL_MISSING_SCORES not in configuration_blockers(categories)


def test_both_group_level_blockers_are_reported_together():
    codes = configuration_blockers(
        [cat("c1", "A", 6000, []), cat("c2", "B", 3000, [("i", "5.00")])]
    )
    assert codes == [OVERALL_WEIGHTS_INCOMPLETE, OVERALL_CATEGORY_NOT_STARTED]


# ===========================================================================
# The presentation boundary -- the ONLY place anything is rounded
# ===========================================================================


def test_display_rounding_is_half_up_to_two_places():
    assert format_percentage(Decimal("87.125")) == "87.13"
    assert format_percentage(Decimal("87.124")) == "87.12"
    # ROUND_HALF_UP, not Python's default ROUND_HALF_EVEN, which would
    # give 87.12 here and 87.14 below.
    assert format_percentage(Decimal("87.135")) == "87.14"
    assert format_percentage(Decimal("0.005")) == "0.01"


def test_formatters_return_strings_so_a_caller_cannot_keep_calculating():
    assert isinstance(format_percentage(Decimal("1")), str)
    assert isinstance(format_points(Decimal("1")), str)
    assert isinstance(format_weight(10000), str)


def test_formatters_pass_none_through_unchanged():
    assert format_percentage(None) is None
    assert format_points(None) is None


def test_points_are_displayed_with_exactly_two_places():
    assert format_points(Decimal("5")) == "5.00"
    assert format_points(Decimal("5.5")) == "5.50"
    assert format_points(Decimal("0.01")) == "0.01"
    assert format_points(Decimal("99999.99")) == "99999.99"


def test_weights_convert_from_basis_points_exactly():
    assert format_weight(10000) == "100.00"
    assert format_weight(2500) == "25.00"
    assert format_weight(1250) == "12.50"
    assert format_weight(1) == "0.01"
    assert format_weight(3333) == "33.33"


def test_no_float_is_produced_anywhere_in_a_summary():
    summary = summarize(
        [
            cat("c1", "A", 6000, [("i1", "7.00")]),
            cat("c2", "B", 4000, [("i2", "3.00")]),
        ],
        scores(i1="1.00", i2="1.00"),
    )
    assert isinstance(summary.overall, Decimal)
    for result in summary.categories:
        assert isinstance(result.percentage, Decimal)
        assert isinstance(result.earned, Decimal)
        assert isinstance(result.possible, Decimal)


def test_the_classic_float_trap_adds_up_exactly_here():
    """``0.1 + 0.2 != 0.3`` in binary floating point. In ``Decimal`` it
    does, which is the whole reason these columns are not floats."""
    result = build_category_result(
        cat("c1", "A", 10000, [("i1", "0.10"), ("i2", "0.20")]),
        scores(i1="0.10", i2="0.20"),
    )
    assert result.earned == Decimal("0.30")
    assert result.possible == Decimal("0.30")
    assert result.percentage == Decimal(100)


def test_calculation_precision_is_stated_explicitly_and_generous():
    assert CALC_PRECISION == 28
