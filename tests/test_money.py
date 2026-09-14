"""Phase 5 / M02 -- the single exact-money boundary.

Pure-function tests for ``app/services/money.py``: accepted shapes and their
canonical values, both inclusive bounds, precision without rounding, every
hostile spelling the parser must refuse, the model-facing validator's refusal
of binary floats, exact Decimal addition and non-rounding display.
"""

import pathlib
from decimal import Decimal

import pytest

from app.services import money

_SOURCE = pathlib.Path(money.__file__).read_text(encoding="utf-8")


# ===========================================================================
# Accepted text
# ===========================================================================


@pytest.mark.parametrize(
    "raw, canonical",
    [
        ("0.001", "0.0010"),
        ("99999.999", "99999.9990"),
        ("1250", "1250.0000"),
        ("1250.5", "1250.5000"),
        ("12.3456", "12.3456"),
        ("0.0015", "0.0015"),
        ("1", "1.0000"),
        (" 12.5\t", "12.5000"),
        ("0012.5", "12.5000"),
        ("99999.9990", "99999.9990"),
    ],
)
def test_accepted_amounts_become_canonical_exact_decimals(raw, canonical):
    value, error = money.parse_amount(raw)
    assert error is None
    assert isinstance(value, Decimal)
    assert str(value) == canonical


def test_both_bounds_are_inclusive():
    assert money.parse_amount("0.001") == (Decimal("0.0010"), None)
    assert money.parse_amount("99999.999") == (Decimal("99999.9990"), None)
    assert money.parse_amount("0.0009") == (None, money.TOO_SMALL)
    assert money.parse_amount("99999.9991") == (None, money.TOO_LARGE)


# ===========================================================================
# Refused text -- every code
# ===========================================================================


@pytest.mark.parametrize("raw", [None, "", "   ", "\t \t"])
def test_missing(raw):
    assert money.parse_amount(raw) == (None, money.MISSING)


@pytest.mark.parametrize("raw", ["0", "00", "0.0", "0.000", "0.0000"])
def test_zero_is_not_an_amount(raw):
    assert money.parse_amount(raw) == (None, money.NOT_POSITIVE)


@pytest.mark.parametrize("raw", ["0.0001", "0.0009"])
def test_below_the_minimum(raw):
    assert money.parse_amount(raw) == (None, money.TOO_SMALL)


@pytest.mark.parametrize("raw", ["100000", "99999.9999", "123456789"])
def test_above_the_maximum(raw):
    assert money.parse_amount(raw) == (None, money.TOO_LARGE)


@pytest.mark.parametrize("raw", ["1.00000", "12.34567", "0.00001", "5.123456789"])
def test_more_than_four_decimal_places_is_refused_never_rounded(raw):
    assert money.parse_amount(raw) == (None, money.PRECISION)


@pytest.mark.parametrize(
    "raw",
    [
        "+5",
        "-5",
        "-0",
        "-0.001",
        "1e3",
        "1E3",
        "1.5e-2",
        "NaN",
        "nan",
        "sNaN",
        "Infinity",
        "inf",
        "-inf",
        "1,250",
        "1,250.500",
        "12,5",
        "1 250",
        "1_000",
        "0x10",
        ".5",
        "5.",
        "5..0",
        "5.0.0",
        "abc",
        "1/2",
        "١٢",  # Arabic-Indic digits
        "١.٥",
        "۱۲",  # Extended Arabic-Indic digits
        "１２",  # fullwidth digits
        "12 ",  # a no-break space is not stripped
        " 12",
        "\n5",
        "5\n",
        "5 0",
        "‏5",  # right-to-left mark
        "1" * (money.MAX_AMOUNT_TEXT_LENGTH + 1),
        "$5",
        "5 LYD",
        "LYD 5",
    ],
)
def test_hostile_spellings_are_refused(raw):
    value, error = money.parse_amount(raw)
    assert value is None
    assert error in (money.FORMAT, money.MISSING)
    if raw.strip(" \t"):
        assert error == money.FORMAT


@pytest.mark.parametrize("raw", [1.5, 5, True, Decimal("5"), b"5", ["5"]])
def test_the_parser_accepts_text_only(raw):
    assert money.parse_amount(raw) == (None, money.FORMAT)


# ===========================================================================
# The model-facing validator
# ===========================================================================


def test_validate_amount_accepts_a_decimal_or_its_text():
    assert str(money.validate_amount(Decimal("12.5"))) == "12.5000"
    assert str(money.validate_amount("12.5")) == "12.5000"
    # Trailing zeros past the scale are not a fifth significant place on a
    # Decimal that is already a number.
    assert str(money.validate_amount(Decimal("12.50000"))) == "12.5000"


@pytest.mark.parametrize(
    "value",
    [
        0.1,
        12.5,
        float("nan"),
        float("inf"),
        5,
        True,
        None,
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0"),
        Decimal("-0"),
        Decimal("-5"),
        Decimal("0.0009"),
        Decimal("100000"),
        Decimal("1E+30"),
        Decimal("1E-30"),
        Decimal("12.00001"),
        "1,250",
        "12.34567",
    ],
)
def test_validate_amount_refuses_everything_else(value):
    with pytest.raises(ValueError):
        money.validate_amount(value)


def test_a_binary_float_is_named_as_the_reason():
    with pytest.raises(ValueError, match="float"):
        money.validate_amount(0.1)


# ===========================================================================
# Exact arithmetic and display
# ===========================================================================


def test_sum_is_exact_where_binary_floats_are_not():
    tenth = money.validate_amount("0.1")
    assert 0.1 + 0.2 != 0.3
    assert money.sum_amounts([tenth, money.validate_amount("0.2")]) == Decimal("0.3")
    twenty = [money.validate_amount("99999.999")] * 20
    assert money.sum_amounts(twenty) == Decimal("1999999.98")
    assert money.sum_amounts([]) == Decimal(0)


@pytest.mark.parametrize("bad", [[0.1], [1], [Decimal("NaN")], ["1.5"]])
def test_sum_refuses_anything_but_finite_decimals(bad):
    with pytest.raises(ValueError):
        money.sum_amounts(bad)


@pytest.mark.parametrize(
    "value, shown, input_text",
    [
        (Decimal("1250.5"), "1,250.500", "1250.500"),
        (Decimal("12.3456"), "12.3456", "12.3456"),
        (Decimal("0.001"), "0.001", "0.001"),
        (Decimal("99999.999"), "99,999.999", "99999.999"),
        (Decimal("1999999.9800"), "1,999,999.980", "1999999.980"),
        (Decimal("0"), "0.000", "0.000"),
    ],
)
def test_display_never_rounds_and_the_input_text_round_trips(value, shown, input_text):
    assert money.format_amount(value) == shown
    assert money.amount_input_text(value) == input_text
    if value > 0 and value <= money.MAX_AMOUNT:
        assert money.parse_amount(input_text) == (money.validate_amount(value), None)


def test_display_refuses_to_round_a_fifth_place():
    from decimal import Inexact

    with pytest.raises(Inexact):
        money.format_amount(Decimal("1.00001"))


def test_the_module_never_constructs_a_float():
    code = _SOURCE.split('"""', 2)[2]
    assert "float(" not in code
    assert "round(" not in code
    assert money.CURRENCY_CODE == "LYD"
    assert (money.AMOUNT_PRECISION, money.AMOUNT_SCALE) == (19, 4)
    assert (money.MIN_AMOUNT, money.MAX_AMOUNT) == (Decimal("0.001"), Decimal("99999.999"))
