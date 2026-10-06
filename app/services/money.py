"""The single exact-money boundary (Phase 5 / M02).

Flask-independent pure functions over ``str`` and ``decimal.Decimal``: no
``request``, no ORM, no template and no I/O. Every place that turns
submitted text into an amount, checks an amount before it is stored, adds
amounts up or renders one goes through exactly these functions, so no two
callers can disagree about what an amount *is*.

**One currency.** The center charges in Libyan dinars and nothing else:
:data:`CURRENCY_CODE` is ``LYD``, and there is no exchange rate, no
currency selector and no second code anywhere. A fee plan still stores the
code explicitly (``fee_plans.currency_code``, checked by the database), so
a stored amount is never read without the unit it is in.

**Money never passes through a binary float.** ``FLOAT`` / ``DOUBLE``
cannot represent 0.1 exactly, so a sum of fees would silently stop adding
up. Amounts are stored as ``DECIMAL(19, 4)``, parsed from the submitted
*text* straight into :class:`~decimal.Decimal`, added in Python ``Decimal``
(never in SQL and never through ``float``) and rendered from ``Decimal``.
A ``float`` handed to :func:`validate_amount` is refused outright rather
than converted: ``Decimal(0.1)`` is
``0.1000000000000000055511151231257827``, not ``0.1``.

**Submitted money is never rounded, repaired or guessed at.** The one
accepted shape is ASCII digits, optionally followed by one ``.`` and one
to four ASCII digits -- ``1250``, ``1250.5``, ``0.001``, ``99999.999``.
Everything else is rejected with a code, never silently fixed:

- a sign (``+5``, ``-5``), an exponent (``1e3``), ``NaN`` or ``Infinity``;
- a comma, whether a thousands separator (``1,250``) or a decimal comma
  (``12,5``), and any inner space;
- a non-ASCII digit (Arabic-Indic ``١٢``, fullwidth ``１２``) -- ``int()``
  and ``Decimal()`` would both accept those, which is exactly why the text
  is matched against an explicit ``[0-9]`` class before anything is built
  from it;
- a bare or trailing point (``.5``, ``5.``);
- more than four digits after the point, **trailing zeros included**
  (``1.00000``): the column holds four, and a fifth is either a rounding
  nobody asked for or a typo;
- zero, anything below :data:`MIN_AMOUNT` and anything above
  :data:`MAX_AMOUNT` -- both bounds inclusive.

Only surrounding spaces and tabs are stripped. An error is returned as a
*code*, never as a sentence: the wording belongs beside its audience.

The display helper groups thousands with commas **for reading only**. A
form is never pre-filled with it -- it receives :func:`amount_input_text`,
which the parser accepts back unchanged.
"""

import re
from decimal import Decimal, Inexact, ROUND_HALF_UP, localcontext

#: The only currency the system supports.
CURRENCY_CODE = "LYD"

#: The exact shape of every stored amount: ``DECIMAL(19, 4)``.
AMOUNT_PRECISION = 19
AMOUNT_SCALE = 4

#: Inclusive bounds of one fee item's amount, in LYD. Declared once so the
#: parser, the model validator, the database CHECK and the form wording
#: cannot drift apart.
MIN_AMOUNT = Decimal("0.001")
MAX_AMOUNT = Decimal("99999.999")

#: Anything longer than this is not an amount anybody typed.
MAX_AMOUNT_TEXT_LENGTH = 32

#: Returned instead of a sentence. The caller owns the wording.
MISSING = "missing"
FORMAT = "format"
PRECISION = "precision"
NOT_POSITIVE = "not_positive"
TOO_SMALL = "too_small"
TOO_LARGE = "too_large"

#: ``[0-9]`` rather than ``\\d``: in a ``str`` pattern ``\\d`` matches every
#: Unicode decimal digit, and only ASCII digits are an amount here.
_SHAPE = re.compile(r"(?P<whole>[0-9]+)(?:\.(?P<fraction>[0-9]+))?")
_OUTER_WHITESPACE = " \t"
_STORAGE_QUANTUM = Decimal(1).scaleb(-AMOUNT_SCALE)
_DINAR_QUANTUM = Decimal("0.001")


def _exact_quantize(value, quantum):
    """`value` at `quantum`'s exponent, raising :class:`decimal.Inexact`
    instead of rounding a non-zero digit away."""
    with localcontext() as context:
        context.traps[Inexact] = True
        return value.quantize(quantum)


def _amount_error(value):
    """The error code for one finite ``Decimal``, or ``None``.

    The range is checked before the precision so an absurd magnitude is
    reported as out of range rather than failing to quantize.
    """
    if value <= 0:
        return NOT_POSITIVE
    if value < MIN_AMOUNT:
        return TOO_SMALL
    if value > MAX_AMOUNT:
        return TOO_LARGE
    try:
        _exact_quantize(value, _DINAR_QUANTUM)
    except Inexact:
        return PRECISION
    return None


def parse_amount(raw):
    """``(Decimal, None)`` for acceptable submitted text, else
    ``(None, error_code)``.

    The returned value is canonical -- exactly :data:`AMOUNT_SCALE` places,
    obtained by appending zeros, never by rounding -- so what is compared
    and what is stored are the same value.
    """
    if raw is None:
        return None, MISSING
    if not isinstance(raw, str):
        return None, FORMAT
    text = raw.strip(_OUTER_WHITESPACE)
    if not text:
        return None, MISSING
    if len(text) > MAX_AMOUNT_TEXT_LENGTH:
        return None, FORMAT
    match = _SHAPE.fullmatch(text)
    if match is None:
        return None, FORMAT
    if len(match.group("fraction") or "") > AMOUNT_SCALE:
        return None, PRECISION
    value = Decimal(text)
    error = _amount_error(value)
    if error is not None:
        return None, error
    return _exact_quantize(value, _STORAGE_QUANTUM), None


def validate_amount(value):
    """The canonical ``Decimal`` for a value about to be stored, or raise
    ``ValueError``.

    Accepts only a ``Decimal`` or the text form :func:`parse_amount`
    accepts. A ``float`` is refused rather than converted, and so is an
    ``int`` or a ``bool``: the one way a number enters the catalogue is as
    exact decimal text or an exact ``Decimal``.
    """
    if isinstance(value, str):
        amount, error = parse_amount(value)
        if error is not None:
            raise ValueError(f"Invalid money amount: {error}")
        return amount
    if isinstance(value, float):
        raise ValueError("A money amount must never be a binary float")
    if not isinstance(value, Decimal):
        raise ValueError("A money amount must be an exact Decimal or its text")
    if not value.is_finite():
        raise ValueError("A money amount must be finite")
    error = _amount_error(value)
    if error is not None:
        raise ValueError(f"Invalid money amount: {error}")
    return _exact_quantize(value, _STORAGE_QUANTUM)


def sum_amounts(amounts):
    """The exact ``Decimal`` sum of `amounts` (``Decimal(0)`` for none).

    Refuses anything but a finite ``Decimal``, and traps
    :class:`decimal.Inexact`, so a total is either exact or not produced.
    """
    total = Decimal(0)
    with localcontext() as context:
        context.traps[Inexact] = True
        for amount in amounts:
            if not isinstance(amount, Decimal) or not amount.is_finite():
                raise ValueError("Only exact, finite Decimal amounts can be added")
            total += amount
    return total


def _display_shape(value):
    """`value` with three places when its fourth is zero, else four.

    Three is the dinar's own precision, so that is how a plain amount reads;
    a genuine fourth digit is shown rather than hidden. Neither branch can
    round: both quantizations trap :class:`decimal.Inexact`.
    """
    canonical = _exact_quantize(value, _STORAGE_QUANTUM)
    try:
        return _exact_quantize(canonical, _DINAR_QUANTUM), 3
    except Inexact:
        return canonical, 4


def format_amount(value):
    """``'1,250.500'`` -- grouped for reading, never rounded. Display only."""
    if type(value) is int:
        value = Decimal(value)
    shown, places = _display_shape(value)
    return format(shown, f",.{places}f").rstrip("0").rstrip(".")


def amount_input_text(value):
    """``'1250.500'`` -- the ungrouped text a form is pre-filled with, which
    :func:`parse_amount` accepts back as the same value."""
    shown, places = _display_shape(value)
    return format(shown, f".{places}f").rstrip("0").rstrip(".")


def validate_course_price(value):
    """Nonnegative exact LYD charge; zero requires an explicit input."""
    if isinstance(value, str):
        text = value.strip(_OUTER_WHITESPACE)
        if len(text) > MAX_AMOUNT_TEXT_LENGTH or _SHAPE.fullmatch(text) is None:
            raise ValueError("Enter an exact nonnegative LYD amount.")
        value = Decimal(text)
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or value > MAX_AMOUNT:
        raise ValueError("Enter an exact nonnegative LYD amount within the supported range.")
    try:
        return _exact_quantize(value, _DINAR_QUANTUM)
    except Inexact:
        raise ValueError("LYD amounts support three decimal places.") from None


def calculate_discount(price, kind="none", value="0"):
    """Return snapshotted discount and net charge, rounding a percentage once."""
    price = validate_course_price(price)
    if kind == "none":
        return Decimal("0.000"), price
    if kind == "amount":
        discount = validate_course_price(value)
    elif kind == "percentage":
        if not isinstance(value, (str, Decimal)):
            raise ValueError("Enter an exact percentage.")
        text = str(value).strip()
        if len(text) > MAX_AMOUNT_TEXT_LENGTH or _SHAPE.fullmatch(text) is None:
            raise ValueError("Enter a percentage from 0 to 100.")
        percentage = Decimal(text)
        if not percentage.is_finite() or not 0 <= percentage <= 100:
            raise ValueError("Enter a percentage from 0 to 100.")
        with localcontext() as context:
            context.prec = 64
            discount = (price * percentage / Decimal(100)).quantize(_DINAR_QUANTUM, rounding=ROUND_HALF_UP)
    else:
        raise ValueError("Select an amount or percentage discount.")
    if discount > price:
        raise ValueError("The discount cannot exceed the course charge.")
    return discount, price - discount
