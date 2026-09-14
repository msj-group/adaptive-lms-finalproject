"""Forms and input parsing for the Administrator fee plan catalogue
(Phase 5 / M02).

Two WTForms forms: one for a plan's name and description, one for an item's
kind, label and amount.

**There is no status, currency, version, creator, quantity, discount, tax
or due-date field, and no placeholder for one.** A new plan is a ``draft``;
activation, archiving and reactivation are separate POST-only routes with
their own confirmation and signed token; the currency is always ``LYD``.

**The amount crosses this boundary as text and leaves it as ``Decimal``.**
It is a ``StringField`` -- never a ``FloatField`` or a coercing
``DecimalField`` -- parsed by :func:`app.services.money.parse_amount`, the
one place submitted money becomes a number. Nothing is rounded: a value the
parser refuses is shown back to the Administrator with a sentence saying
why.

Nothing here authorizes anything or touches a lock. Every check is a
friendly pre-lock guard; the route re-proves each rule that depends on
other rows (a unique name, a unique label, the item limit) against locked
rows before writing. This module owns only the Administrator's wording.
"""

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import ValidationError

from app.models import (
    FEE_PLAN_DESCRIPTION_MAX_LENGTH,
    FEE_PLAN_ITEM_LABEL_MAX_LENGTH,
    FEE_PLAN_NAME_MAX_LENGTH,
)
from app.models.fee_plan import (
    TEXT_CONTROL,
    TEXT_MISSING,
    TEXT_TOO_LONG,
    normalize_fee_plan_description,
    normalize_fee_plan_name,
)
from app.models.fee_plan_item import normalize_fee_plan_item_label
from app.services import money
from app.services.fee_plan_queries import KIND_CHOICES

NAME_MESSAGES = {
    TEXT_MISSING: "Give this fee plan a name.",
    TEXT_CONTROL: "The name may only contain ordinary text.",
    TEXT_TOO_LONG: f"The name must be at most {FEE_PLAN_NAME_MAX_LENGTH} characters.",
}
DESCRIPTION_MESSAGES = {
    TEXT_CONTROL: "The description may only contain ordinary text and line breaks.",
    TEXT_TOO_LONG: (
        f"The description must be at most {FEE_PLAN_DESCRIPTION_MAX_LENGTH} characters."
    ),
}
LABEL_MESSAGES = {
    TEXT_MISSING: "Give this item a label.",
    TEXT_CONTROL: "The label may only contain ordinary text.",
    TEXT_TOO_LONG: f"The label must be at most {FEE_PLAN_ITEM_LABEL_MAX_LENGTH} characters.",
}
AMOUNT_MESSAGES = {
    money.MISSING: f"Enter the amount in {money.CURRENCY_CODE}.",
    money.FORMAT: (
        "Enter the amount using digits and at most one decimal point, for example 1250 "
        "or 1250.500. Do not use commas, spaces, signs, letters or exponents."
    ),
    money.PRECISION: (
        f"The amount may have at most {money.AMOUNT_SCALE} digits after the decimal point. "
        "It is never rounded for you."
    ),
    money.NOT_POSITIVE: "The amount must be greater than zero.",
    money.TOO_SMALL: (
        f"The amount must be at least {money.format_amount(money.MIN_AMOUNT)} "
        f"{money.CURRENCY_CODE}."
    ),
    money.TOO_LARGE: (
        f"The amount must be at most {money.format_amount(money.MAX_AMOUNT)} "
        f"{money.CURRENCY_CODE}."
    ),
}
KIND_MESSAGE = "Choose whether this item is a registration fee or a course fee."

_KIND_VALUES = frozenset(value for value, _label in KIND_CHOICES)


class FeePlanForm(FlaskForm):
    """Name and optional description of one draft fee plan.

    The normalized results are kept on the form so the route persists
    exactly what was validated.
    """

    name = StringField("Plan name")
    description = TextAreaField("Description")
    submit = SubmitField("Save plan")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.normalized_name = None
        self.normalized_description = None

    def validate_name(self, field):
        value, error = normalize_fee_plan_name(field.data)
        if error is not None:
            raise ValidationError(NAME_MESSAGES[error])
        self.normalized_name = value

    def validate_description(self, field):
        value, error = normalize_fee_plan_description(field.data)
        if error is not None:
            raise ValidationError(DESCRIPTION_MESSAGES[error])
        self.normalized_description = value


class FeePlanItemForm(FlaskForm):
    """Kind, label and exact amount of one item of a draft fee plan.

    ``kind`` is validated against the closed set here, with the
    Administrator's own wording, rather than by WTForms' generic choice
    check. ``amount`` is plain text until :func:`money.parse_amount`
    accepts it.
    """

    kind = SelectField("Kind", choices=list(KIND_CHOICES), validate_choice=False)
    label = StringField("Label")
    amount = StringField(f"Amount ({money.CURRENCY_CODE})")
    submit = SubmitField("Save item")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.normalized_label = None
        self.parsed_amount = None

    def validate_kind(self, field):
        if field.data not in _KIND_VALUES:
            raise ValidationError(KIND_MESSAGE)

    def validate_label(self, field):
        value, error = normalize_fee_plan_item_label(field.data)
        if error is not None:
            raise ValidationError(LABEL_MESSAGES[error])
        self.normalized_label = value

    def validate_amount(self, field):
        value, error = money.parse_amount(field.data)
        if error is not None:
            raise ValidationError(AMOUNT_MESSAGES[error])
        self.parsed_amount = value
