"""Forms and input parsing for the Teacher Gradebook surface
(Phase 4 / M08).

Two WTForms forms exist here -- the category form and the grade-item form
-- because those are the two M08 inputs with a fixed field set. The score
sheet has one score box and one comment box per *captured Student*, a set
decided by data rather than by a class, so it is parsed and validated
explicitly by :func:`parse_score_submission` rather than by a dynamically
built form class -- exactly the arrangement M07 uses for its marking
page. Both paths are CSRF-protected the same way: ``CSRFProtect`` is
initialised application-wide, so every POST in this project -- WTForms or
not -- must carry a valid ``csrf_token``.

Nothing here authorizes anything or touches a lock. These are ordinary
input checks that run **before** any lock is taken, so a mistyped score
costs no database work and the Teacher gets their own values back to
correct. Every authoritative condition -- the Group, the assignment, the
category's freeze, the item's draft state, the source's ownership and
publication, the weight total, ``score <= max_points`` -- is re-checked
against the **locked** rows afterwards.

**Numbers cross this boundary as text, and leave it as ``Decimal``.**
Every numeric field below is read as a raw string and matched against an
explicit pattern before anything is constructed from it. No
``FloatField``, no ``DecimalField`` with an implicit coercion, no
``float()`` call anywhere: a grade must never pass through a binary
value, and the surest way to guarantee that is for no code path to
create one.
"""

import re
from decimal import Decimal, InvalidOperation

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, ValidationError

from app.models import (
    BASIS_POINTS_TOTAL,
    GRADE_CATEGORY_TITLE_MAX_LENGTH,
    GRADE_COMMENT_MAX_LENGTH,
    GRADE_ITEM_TITLE_MAX_LENGTH,
    MAX_POINTS_CEILING,
    MIN_CATEGORY_BASIS_POINTS,
    MIN_POINTS,
    POINTS_SCALE,
)
from app.services.grade_calculations import format_weight, remaining_weight
from app.services.grade_queries import (
    SOURCE_KIND_LABELS,
    SOURCE_KIND_ORDER,
    UNLINKED_SOURCE_KINDS,
    duplicate_category_title_exists,
)

#: The request-boundary limit on one per-Student comment, mirroring the
#: constant declared beside the column (``app/models/grade_record.py``).
#: Applied to the **raw** submitted value, before stripping, so padding a
#: comment with whitespace cannot smuggle a longer one past it.
COMMENT_MAX = GRADE_COMMENT_MAX_LENGTH

#: Field-name prefixes on the score sheet. Each is suffixed with the
#: record's **public id** -- never an internal database id, and never a
#: row index, which a reordered page could silently re-point at another
#: Student.
SCORE_FIELD_PREFIX = "score__"
COMMENT_FIELD_PREFIX = "comment__"

#: A points value: up to five digits, optionally a decimal point and one
#: or two digits, with the integer part optional (``.5`` is a perfectly
#: ordinary thing to type). Exactly the shape ``DECIMAL(7, 2)`` can hold.
#:
#: Deliberately strict. It rejects a leading ``+`` or ``-`` (a negative
#: score is refused *here*, with a sentence about scores, rather than
#: reaching a CHECK constraint), scientific notation, thousands
#: separators, ``inf``, ``nan``, an empty fraction (``5.``) and any third
#: decimal place. A third decimal is **rejected, never rounded**:
#: silently turning a Teacher's ``7.005`` into ``7.01`` or ``7.00`` would
#: be the system deciding somebody's grade.
_POINTS_PATTERN = re.compile(r"^(?:\d{1,5}(?:\.\d{1,2})?|\.\d{1,2})$")

#: A weight, typed as a **percentage** because that is what a Teacher
#: thinks in, and converted to basis points the moment it is validated.
#: Up to three digits and up to two decimals, so 0.01 .. 100.00.
_WEIGHT_PATTERN = re.compile(r"^(?:\d{1,3}(?:\.\d{1,2})?|\.\d{1,2})$")

_HUNDRED = Decimal(100)


def parse_points(raw, allow_blank=False):
    """``(Decimal or None, error_code or None)`` for one points value.

    The single place a points string becomes a number, used by the item
    form's ``max_points`` and by every score on the score sheet, so the
    two can never accept different shapes. Returns a code rather than a
    sentence: the caller declares the wording, because "that is not a
    valid score" and "that is not a valid maximum" are different
    sentences about the same rule.

    `allow_blank` distinguishes the score sheet (where an empty box means
    *not graded yet*, a legitimate state) from the item form (where a
    missing maximum is simply missing).
    """
    text = (raw or "").strip()
    if not text:
        return (None, None) if allow_blank else (None, "missing")
    if not _POINTS_PATTERN.match(text):
        return None, "format"
    try:
        value = Decimal(text)
    except InvalidOperation:  # pragma: no cover -- the pattern already excludes it
        return None, "format"
    if value > MAX_POINTS_CEILING:
        return None, "too_large"
    # Normalise ".5" and "5" to the column's own two-decimal shape, so
    # what is stored and what is compared are the same value.
    return value.quantize(Decimal(1).scaleb(-POINTS_SCALE)), None


def parse_weight_percent(raw):
    """``(basis_points or None, error_code or None)`` for one weight.

    A Teacher types ``25`` or ``12.5``; the gradebook stores 2,500 or
    1,250 basis points. The conversion is exact: ``Decimal`` times an
    integer 100, then an integer check that nothing was lost -- which is
    why a third decimal place is rejected by the pattern rather than
    rounded here.
    """
    text = (raw or "").strip()
    if not text:
        return None, "missing"
    if not _WEIGHT_PATTERN.match(text):
        return None, "format"
    try:
        percent = Decimal(text)
    except InvalidOperation:  # pragma: no cover -- the pattern already excludes it
        return None, "format"
    basis_points = percent * _HUNDRED
    if basis_points != basis_points.to_integral_value():  # pragma: no cover
        return None, "format"
    basis_points = int(basis_points)
    if basis_points < MIN_CATEGORY_BASIS_POINTS or basis_points > BASIS_POINTS_TOTAL:
        return None, "range"
    return basis_points, None


class GradeCategoryForm(FlaskForm):
    """Create or edit one weighted gradebook category.

    Two controls: a title and a weight typed as a percentage. There is
    deliberately **no** delete control, no ordering control (categories
    are presented by title) and no "released" control -- release belongs
    to an item, and a category has no lifecycle of its own.

    Every check here is a **friendly pre-lock guard**. The duplicate
    title is re-checked against the locked rows and is finally defended
    by ``uq_grade_categories_group_title``; the weight total is
    re-checked against the locked category set, because two co-teachers
    each adding 60% would both pass a check made a moment earlier.
    """

    title = StringField(
        "Category name",
        validators=[DataRequired(), Length(max=GRADE_CATEGORY_TITLE_MAX_LENGTH)],
    )
    weight = StringField("Weight (%)", validators=[DataRequired()])
    submit = SubmitField("Save category")

    def __init__(
        self, *args, group_id=None, category_id=None, other_weights=(), **kwargs
    ):
        super().__init__(*args, **kwargs)
        self._group_id = group_id
        self._category_id = category_id
        #: The weights of every OTHER category of this Group, so the
        #: friendly guard can say how much room is left. The edited
        #: category's own weight is excluded by the caller -- it must not
        #: count against its own replacement.
        self._other_weights = list(other_weights)
        #: Set by :meth:`validate_weight` so the route can persist
        #: exactly what was validated rather than re-parsing the string.
        self.weight_basis_points = None

    def validate_title(self, field):
        if self._group_id is None:
            return
        if duplicate_category_title_exists(
            self._group_id, (field.data or "").strip(), self._category_id
        ):
            raise ValidationError(
                "This group already has a grade category with this name. Choose a "
                "different name."
            )

    def validate_weight(self, field):
        basis_points, error = parse_weight_percent(field.data)
        if error == "format":
            raise ValidationError(
                "Enter the weight as a percentage with at most two decimal places, for "
                "example 25 or 12.5."
            )
        if error == "range":
            raise ValidationError(
                "A category weight must be between 0.01% and 100%. A category worth "
                "nothing cannot affect any grade."
            )
        if error is not None:  # pragma: no cover -- DataRequired caught "missing"
            raise ValidationError("Enter a weight for this category.")
        room = remaining_weight(self._other_weights)
        if basis_points > room:
            raise ValidationError(
                "This group's categories may not add up to more than 100%. "
                f"{format_weight(room)}% is still unallocated."
            )
        self.weight_basis_points = basis_points


class GradeItemForm(FlaskForm):
    """Create or edit one draft grade item.

    Four controls: the category it belongs to, what it is a grade *for*,
    which same-Group object that is (when the kind needs one), and what it
    is out of. There is deliberately **no** release control -- release is
    a separate POST-only route behind its own confirmation and its own
    signed token, so a forged ``released_at`` field in this body has
    nowhere to land -- and **no** score control, because scores belong to
    the roster sheet.

    The three source selects are populated from same-Group rows only, and
    ``SelectField`` pre-validation refuses any value that is not one of
    them -- so another Group's Assignment, Quiz or Speaking activity is
    rejected here, before a lock, and then rejected again when the write
    path resolves the public id with a Group-scoped query that simply
    cannot find it.
    """

    category = SelectField("Category", validators=[DataRequired()], validate_choice=True)
    title = StringField(
        "Item name",
        validators=[DataRequired(), Length(max=GRADE_ITEM_TITLE_MAX_LENGTH)],
    )
    source_kind = SelectField(
        "What is this a grade for?", validators=[DataRequired()], validate_choice=True
    )
    #: One select per linked kind rather than one shared box, so a
    #: submitted Quiz public id can never be read as an Assignment: each
    #: field's choices are pre-validated against its own same-Group list.
    assignment_source = SelectField("Assignment", validate_choice=False)
    quiz_source = SelectField("Quiz or listening activity", validate_choice=False)
    speaking_source = SelectField("Speaking activity", validate_choice=False)
    max_points = StringField("Out of (points)", validators=[DataRequired()])
    submit = SubmitField("Save grade item")

    def __init__(self, *args, category_choices=(), source_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.category.choices = list(category_choices)
        self.source_kind.choices = [
            (kind, SOURCE_KIND_LABELS[kind]) for kind in SOURCE_KIND_ORDER
        ]
        choices = source_choices or {}
        self._source_fields = {
            "assignment": self.assignment_source,
            "quiz": self.quiz_source,
            "speaking": self.speaking_source,
        }
        for kind, field in self._source_fields.items():
            field.choices = [("", "— choose —")] + [
                (public_id, label if published else f"{label} (draft)")
                for public_id, label, published in choices.get(kind, [])
            ]
        #: Set by :meth:`validate` so the route persists exactly what was
        #: validated.
        self.max_points_value = None
        self.source_public_id = None

    def source_field(self, kind):
        """The select that belongs to one linked source kind, so the
        template renders the same field the validator reads."""
        return self._source_fields.get(kind)

    def validate_max_points(self, field):
        value, error = parse_points(field.data)
        if error == "format":
            raise ValidationError(
                "Enter the points as a number with at most two decimal places, for "
                "example 20 or 12.5."
            )
        if error == "too_large":
            raise ValidationError(
                f"A grade item cannot be worth more than {MAX_POINTS_CEILING} points."
            )
        if error is not None:  # pragma: no cover -- DataRequired caught "missing"
            raise ValidationError("Enter how many points this item is out of.")
        if value < MIN_POINTS:
            raise ValidationError(
                f"A grade item must be worth at least {MIN_POINTS} points. An item worth "
                "nothing cannot contribute to any grade."
            )
        self.max_points_value = value

    def validate(self, extra_validators=None):
        """Run the field validators, then the **source-kind rule** --
        which is a relationship between two fields and so cannot live on
        either one.

        The rule is exactly the one the database CHECK states: a linked
        kind requires its own select to name something, and an unlinked
        kind (``activity`` / ``manual``) requires every select to be
        empty. The unlinked half is enforced rather than ignored on
        purpose: a request that says ``manual`` *and* carries a Quiz id
        is not a request this form describes, and silently dropping the
        id would hide that.
        """
        if not super().validate(extra_validators=extra_validators):
            return False
        kind = self.source_kind.data
        submitted = {
            name: (field.data or "").strip()
            for name, field in self._source_fields.items()
        }
        if kind in UNLINKED_SOURCE_KINDS:
            if any(submitted.values()):
                self.source_kind.errors.append(
                    "A class activity or manual grade is not linked to anything. Choose "
                    "a different kind, or clear the linked item."
                )
                return False
            self.source_public_id = None
            return True
        chosen = submitted.get(kind, "")
        if not chosen:
            field = self._source_fields[kind]
            field.errors.append(
                "Choose which one of this group's items this grade is for."
            )
            return False
        if any(name != kind and value for name, value in submitted.items()):
            self.source_kind.errors.append(
                "A grade item is linked to exactly one thing. Clear the other linked "
                "items and try again."
            )
            return False
        self.source_public_id = chosen
        return True


class ScoreSubmission:
    """The parsed, ordinarily-valid result of one score-sheet POST.

    ``values`` maps a record ``public_id`` to
    ``(score_or_None, comment_or_None)``. ``errors`` is a list of
    Teacher-facing sentences; when it is non-empty the caller must
    re-render the form **without taking a lock and without writing
    anything**, so one bad number never costs a Teacher the rest of their
    marking -- and, just as importantly, never leaves half the roster
    written.
    """

    __slots__ = ("values", "raw", "errors")

    def __init__(self, values, raw, errors):
        self.values = values
        #: The raw strings, echoed back so a Teacher can correct the one
        #: box that was wrong instead of retyping the sheet.
        self.raw = raw
        self.errors = errors

    @property
    def ok(self):
        return not self.errors


def _normalize_comment(raw):
    """One optional per-Student comment, normalised exactly once.

    Leading and trailing whitespace is stripped and an empty result
    becomes ``None`` -- "no comment" and "a comment made of spaces" are
    the same thing, and storing the second would make a no-op save look
    like an edit. Internal whitespace and line breaks are kept exactly as
    typed.
    """
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def parse_score_submission(form_data, records, max_points, allow_blank_scores):
    """Validate a score-sheet POST against the **captured** roster.

    `records` is the list of presentation dicts for the item's records
    (each carrying ``public_id`` and ``student_name``), read from the
    database -- never from the request. The submitted field set is
    matched against it rather than the other way round, so a request that
    names an extra record, omits one, or renames one changes nothing:
    only the captured ``public_id``s are ever looked up, and a missing
    value is an ordinary validation error.

    Checks, all ordinary and all pre-lock:

    - every captured record has a ``score`` field present, even when it
      is empty. A text input always submits a value, so a **missing**
      score field means the request is not this form -- and treating it
      as "clear the score" would let a partial or hand-built POST erase
      what a colleague entered;
    - a blank score is accepted only when `allow_blank_scores` is true,
      which the caller sets from the item's **draft** state. Once an item
      is released, every captured record already carries a real score and
      the Student has been shown it; clearing one would silently withdraw
      a published result, so it is refused with its own sentence;
    - every non-blank score parses as a non-negative number with at most
      two decimal places, and is at most `max_points`. The upper bound is
      checked here as a friendly guard **and** re-checked against the
      locked GradeItem, because ``max_points`` is on a different row and
      a draft item's maximum can change;
    - every captured record has a ``comment`` field present, and every
      raw comment is at most :data:`COMMENT_MAX` characters.

    Returns a :class:`ScoreSubmission`. It never raises on bad input, and
    it never partially applies: the caller writes nothing unless
    ``ok`` is true.
    """
    values, raw_values, errors = {}, {}, []
    for record in records:
        public_id = record["public_id"]
        name = record.get("student_name", "this student")
        raw_score = form_data.get(SCORE_FIELD_PREFIX + public_id)
        raw_comment = form_data.get(COMMENT_FIELD_PREFIX + public_id)
        raw_values[public_id] = (
            raw_score if raw_score is not None else "",
            raw_comment if raw_comment is not None else "",
        )
        if raw_score is None:
            errors.append(f"The score field for {name} was missing from this form.")
            continue
        if raw_comment is None:
            errors.append(f"The comment field for {name} was missing from this form.")
            continue
        if len(raw_comment) > COMMENT_MAX:
            errors.append(
                f"The comment for {name} is too long. Comments are limited to "
                f"{COMMENT_MAX} characters."
            )
            continue
        if not (raw_score or "").strip() and not allow_blank_scores:
            errors.append(
                f"{name} already has a released score, so it cannot be left blank. "
                "Correct the number instead."
            )
            continue
        score, error = parse_points(raw_score, allow_blank=True)
        if error == "format":
            errors.append(
                f"“{raw_score.strip()}” is not a valid score for {name}. Enter a number "
                "that is zero or more, with at most two decimal places."
            )
            continue
        if error == "too_large" or (score is not None and score > max_points):
            errors.append(
                f"The score for {name} is higher than this item's maximum of "
                f"{max_points}."
            )
            continue
        values[public_id] = (score, _normalize_comment(raw_comment))
    return ScoreSubmission(values, raw_values, errors)
