"""Teacher forms for Group-owned quiz drafts and their multiple-choice
questions (Phase 4 / M04A and M04B).

Kept in its own module rather than appended to
``app/blueprints/teacher/forms.py`` so the M04 quiz surface stays
readable on its own and does not enlarge the shared Unit / Lesson /
Material / Assignment form module. Nothing here is imported by any
existing form.

:class:`QuizForm` owns the Quiz's own title and instructions (M04A).
:class:`QuizQuestionForm` owns one question's prompt, answer mode and
ordered answer options (M04B). They are deliberately separate forms on
separate pages: conflating them would let one save silently touch the
other's rows.
"""

import re

from flask_wtf import FlaskForm
from wtforms import (
    IntegerField,
    RadioField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.fields import DateTimeLocalField
from wtforms.validators import (
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from app.models import (
    MAX_ACTIVE_OPTIONS,
    MAX_ATTEMPT_LIMIT,
    MAX_TIME_LIMIT_MINUTES,
    MIN_ACTIVE_OPTIONS,
    MIN_ATTEMPT_LIMIT,
    MIN_TIME_LIMIT_MINUTES,
    OPTION_TEXT_MAX_LENGTH,
    QUESTION_PROMPT_MAX_LENGTH,
    QUIZ_INSTRUCTIONS_MAX_LENGTH,
    QUIZ_TITLE_MAX_LENGTH,
    QuestionAnswerMode,
)
from app.services.quiz_queries import duplicate_title_exists
from app.services.schedule_occurrences import LocalTimeError, from_app_local


class QuizForm(FlaskForm):
    """Create or edit one Group-owned quiz draft.

    The form carries **only** ``title`` and ``instructions``. There is
    deliberately no ``status`` / ``published_at`` / ``opens_at`` /
    ``due_at`` / ``time_limit`` control, no ``group_id`` and no
    ``version`` field: a Quiz in M04A has no lifecycle to change, its
    Group is the authorized public identifier in the URL, ``version``
    comes from the locked row, and both timestamps are generated on the
    server after the locks. A forged ``group_id`` / ``version`` /
    ``created_at`` / ``status`` field in the POST body therefore has
    **nowhere to land**. The signed stale-form token travels as its own
    hidden input rather than as a form field, exactly like the M01 Teacher
    edit snapshot and the M03 feedback state token.

    There is likewise no question, option, answer-key, score, attempt or
    publish control **on this form**. Since Phase 4 / M04B questions and
    options do exist, but they are authored on their own page through
    :class:`QuizQuestionForm`; this form still cannot create, change,
    reorder or retire any of them, so a forged option field in a Quiz
    metadata save has nowhere to land. Score, attempt and publish
    controls exist nowhere at all.

    **Validation contract**

    - ``DataRequired`` rejects empty *and* whitespace-only input on both
      fields: WTForms treats a string that is falsy after ``.strip()`` as
      missing, so ``"   "`` and ``"\\n\\n"`` never reach the database.
    - ``Length(max=...)`` is applied by WTForms to the **raw** submitted
      value, before any trimming this class performs, so padding a body
      with whitespace can never be used to slip a longer one past the
      limit. ``title`` is bounded at the column's own width
      (:data:`~app.models.quiz.QUIZ_TITLE_MAX_LENGTH`);
      ``quizzes.instructions`` is an unbounded ``Text`` column, so
      :data:`~app.models.quiz.QUIZ_INSTRUCTIONS_MAX_LENGTH` is the finite
      boundary that actually protects the request.

    **Normalization contract** -- :meth:`normalized_title` /
    :meth:`normalized_instructions`

    A plain ``.strip()``, applied **after** the length validators have
    already run against the raw value. Nothing else is touched: internal
    line breaks, blank lines, indentation and a browser's ``\\r\\n`` pairs
    are stored exactly as typed. Both values are plain text throughout --
    never HTML, never Markdown, never rendered with ``|safe``.

    The **normalized** values are also what decide the edit no-op: a save
    whose normalized title and instructions both equal the stored ones
    changes nothing at all, so re-saving unchanged values cannot bump the
    version or move a timestamp.

    **The title check here is a friendly pre-lock guard only.** It shares
    its single definition (``quiz_queries.duplicate_title_exists``) with
    the authoritative post-lock recheck, so the two cannot drift; the DB
    ``UniqueConstraint(group_id, title)`` remains the final defense and
    the route catches the resulting ``IntegrityError``.
    """

    #: Mirrors the column width so the two cannot drift.
    TITLE_MAX = QUIZ_TITLE_MAX_LENGTH
    #: The finite input boundary for the unbounded ``instructions`` Text
    #: column. Imported from the model so the two cannot drift.
    INSTRUCTIONS_MAX = QUIZ_INSTRUCTIONS_MAX_LENGTH

    title = StringField("Title", validators=[DataRequired(), Length(max=TITLE_MAX)])
    instructions = TextAreaField(
        "Instructions", validators=[DataRequired(), Length(max=INSTRUCTIONS_MAX)]
    )
    submit = SubmitField("Save Quiz")

    def __init__(self, *args, group_id=None, quiz_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group_id = group_id
        self._quiz_id = quiz_id

    def normalized_title(self):
        """The exact title that will be persisted -- see the class
        docstring's normalization contract."""
        return (self.title.data or "").strip()

    def normalized_instructions(self):
        """The exact instructions that will be persisted -- see the class
        docstring's normalization contract."""
        return (self.instructions.data or "").strip()

    def validate_title(self, field):
        title = (field.data or "").strip()
        if self._group_id is None or not title:
            # No Group context (nothing to compare within), or a value
            # `DataRequired` has already rejected -- adding a second error
            # for the same field would only be noise.
            return
        if duplicate_title_exists(self._group_id, title, exclude_quiz_id=self._quiz_id):
            raise ValidationError("A quiz with this title already exists in this group.")


# ===========================================================================
# Phase 4 / M04B -- multiple-choice question authoring
# ===========================================================================

#: A brand-new option row carries a client-generated key of this shape
#: instead of a public_id, because it has none yet. Bounded on purpose: a
#: key is never stored, never rendered as identity, and never trusted --
#: it exists only to tie one submitted row's text to its correct-answer
#: checkbox within a single request.
_NEW_KEY_RE = re.compile(r"\Anew:[0-9]{1,9}\Z")

#: A persisted option row is claimed by its own ``public_id``. The shape
#: check here is a cheap early filter; **ownership is proved post-lock**
#: against the locked Question, so a well-formed UUID belonging to another
#: question is still rejected.
_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)

#: The three repeated form fields that carry the option rows. Named once
#: so the template, the JavaScript and this parser cannot drift.
OPTION_KEY_FIELD = "option_key"
OPTION_TEXT_FIELD = "option_text"
OPTION_CORRECT_FIELD = "option_correct"


class SubmittedOption:
    """One option row as the Teacher submitted it, after normalization.

    ``key`` is the row's identity **within this request only**: either a
    persisted option's ``public_id`` or a ``new:N`` marker. ``public_id``
    is the former or ``None``. Nothing here is trusted until the route has
    proved, against the locked Question, that every non-``None``
    ``public_id`` really is one of that Question's currently active
    options.
    """

    __slots__ = ("key", "public_id", "text", "is_correct")

    def __init__(self, key, public_id, text, is_correct):
        self.key = key
        self.public_id = public_id
        self.text = text
        self.is_correct = is_correct

    @property
    def is_new(self):
        return self.public_id is None


class QuizQuestionForm(FlaskForm):
    """Create or edit one ordered multiple-choice question (Phase 4 / M04B).

    **What the form carries, and what it deliberately cannot.** Only
    ``prompt``, ``answer_mode``, and the repeated option rows described
    below. There is no ``quiz_id``, no ``question_id``, no
    ``display_order``, no ``version``, no ``is_active`` / ``retired_at``,
    no score, points, weight, time limit, publish or attempt control: the
    Quiz and Question are authorized public identifiers in the URL,
    ordering and versions are server-owned, retirement is derived from
    which rows were submitted, and none of the rest exists anywhere in
    M04B. A forged field of any of those names therefore has **nowhere to
    land**. The signed stale-form token travels as its own hidden input,
    exactly like the M01 assignment snapshot, the M03 feedback state and
    the M04A quiz edit state.

    **Option rows are submitted as three parallel repeated fields**, not
    as indexed subforms:

    - ``option_key`` -- one per row, in the order the rows appear;
    - ``option_text`` -- one per row, positionally parallel to the keys;
    - ``option_correct`` -- the **keys** of the rows the Teacher marked
      correct.

    That shape is chosen for a specific reason: an unchecked checkbox
    submits nothing at all, so an index-based encoding silently
    misaligns the answer key the moment a row is removed or reordered in
    the browser. Sending the *key* as the checkbox value makes "which rows
    are correct" unambiguous regardless of how the rows were rearranged,
    and it means the browser never has to renumber anything. Row **order**
    is simply the order of ``option_key`` in the request body.

    A key is either a persisted option's ``public_id`` or a
    client-generated ``new:N`` marker for a row that has never been saved.
    Keys are request-scoped only: none is stored, and a persisted key is
    proved to belong to the locked Question before anything is written.

    **Answer modes are cardinality rules, and switching never guesses.**
    ``single`` requires exactly one correct active option; ``multiple``
    requires at least two, and every active option being correct is
    accepted -- no distractor is required, because no such rule was
    approved. Changing multiple -> single with several options still
    checked is **rejected**, with the Teacher's attempted values kept, so
    they choose the one explicitly; nothing is cleared or truncated for
    them. Changing single -> multiple with one option checked is likewise
    rejected until they select another; nothing is auto-checked. Both
    modes use checkboxes for exactly this reason: a radio group would
    discard the extra selections in the browser before the server ever saw
    them, which is the silent data loss this Part forbids.

    **Validation contract**

    - ``DataRequired`` rejects an empty *and* a whitespace-only prompt.
    - ``Length(max=...)`` is applied by WTForms to the **raw** submitted
      value, before any trimming, so padding can never slip a longer body
      past the limit. The same is done by hand for each option's raw text.
    - Between :data:`MIN_ACTIVE_OPTIONS` and :data:`MAX_ACTIVE_OPTIONS`
      active rows.
    - No empty, whitespace-only, or duplicate normalized option text.
    - No repeated or malformed row keys.

    **Normalization contract** -- a plain ``.strip()`` on the prompt and
    on each option's text, applied *after* the length checks. Internal
    whitespace and line breaks are stored exactly as typed. Everything is
    plain text, autoescaped, never HTML, never Markdown, never ``|safe``.

    **This form is not the authority.** Row count, text, uniqueness and
    cardinality are all re-validated against the locked aggregate in
    ``app/blueprints/teacher/quizzes.py``, together with the ownership
    checks this class cannot make (does this ``public_id`` belong to this
    Question?) -- because a co-teacher may change the question between the
    GET and the POST.
    """

    PROMPT_MAX = QUESTION_PROMPT_MAX_LENGTH
    OPTION_TEXT_MAX = OPTION_TEXT_MAX_LENGTH
    MIN_OPTIONS = MIN_ACTIVE_OPTIONS
    MAX_OPTIONS = MAX_ACTIVE_OPTIONS
    SINGLE = QuestionAnswerMode.SINGLE.value
    MULTIPLE = QuestionAnswerMode.MULTIPLE.value

    prompt = TextAreaField(
        "Question", validators=[DataRequired(), Length(max=PROMPT_MAX)]
    )
    answer_mode = RadioField(
        "Answer mode",
        choices=[
            (QuestionAnswerMode.SINGLE.value, "Single answer"),
            (QuestionAnswerMode.MULTIPLE.value, "Multiple answers"),
        ],
        validators=[DataRequired()],
    )
    submit = SubmitField("Save Question")

    def __init__(
        self,
        *args,
        option_keys=None,
        option_texts=None,
        correct_keys=None,
        seed_rows=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        #: Raw repeated fields exactly as submitted, or empty on a GET.
        self._option_keys = list(option_keys or [])
        self._option_texts = list(option_texts or [])
        self._correct_keys = list(correct_keys or [])
        #: Errors that belong to the option block as a whole rather than to
        #: one WTForms field. Rendered above the rows.
        self.option_errors = []
        #: The parsed, normalized rows -- only meaningful once
        #: :meth:`validate` has returned True.
        self.options = []
        #: What the template renders. On a GET this is the persisted state
        #: (or two blank rows for a new question); after a failed POST it
        #: is the Teacher's attempted values, so nothing they typed is
        #: lost.
        self.option_rows = seed_rows if seed_rows is not None else self._attempted_rows()

    # -- rendering -------------------------------------------------------

    def _attempted_rows(self):
        """The submitted rows, echoed back verbatim for a re-render.

        Deliberately **not** normalized and not filtered: a Teacher whose
        save was rejected must see exactly what they typed, including the
        row that was too long or blank.
        """
        correct = set(self._correct_keys)
        rows = []
        for index, key in enumerate(self._option_keys):
            text = self._option_texts[index] if index < len(self._option_texts) else ""
            rows.append(
                {"key": key, "text": text, "is_correct": key in correct}
            )
        return rows

    # -- normalization ---------------------------------------------------

    def normalized_prompt(self):
        """The exact prompt that will be persisted -- see the class
        docstring's normalization contract."""
        return (self.prompt.data or "").strip()

    # -- validation ------------------------------------------------------

    def validate(self, extra_validators=None):
        """Ordinary WTForms validation, then the option-block rules.

        Both halves always run, so a Teacher who submitted a blank prompt
        *and* a duplicate option sees both problems at once instead of
        discovering them one reload at a time.
        """
        fields_ok = super().validate(extra_validators=extra_validators)
        options_ok = self._validate_option_rows()
        return fields_ok and options_ok

    def _validate_option_rows(self):
        """Parse and check the repeated option fields. Populates
        :attr:`options` on success and :attr:`option_errors` otherwise."""
        keys, texts = self._option_keys, self._option_texts

        if len(keys) != len(texts):
            # Only reachable through a tampered or truncated body: the
            # browser always submits one text per key.
            self.option_errors.append(
                "The answer options could not be read. Please reload the page and try again."
            )
            return False

        if not self.MIN_OPTIONS <= len(keys) <= self.MAX_OPTIONS:
            self.option_errors.append(
                f"A question needs between {self.MIN_OPTIONS} and {self.MAX_OPTIONS} answer "
                f"options. This one has {len(keys)}."
            )
            return False

        seen_keys = set()
        for key in keys:
            if not (_NEW_KEY_RE.match(key) or _UUID_RE.match(key)):
                self.option_errors.append(
                    "The answer options could not be read. Please reload the page and try again."
                )
                return False
            if key in seen_keys:
                # A repeated identifier would make "which row is correct"
                # ambiguous, so it is refused rather than resolved.
                self.option_errors.append(
                    "The same answer option was submitted twice. Please reload the page and "
                    "try again."
                )
                return False
            seen_keys.add(key)

        unknown_correct = [key for key in self._correct_keys if key not in seen_keys]
        if unknown_correct:
            self.option_errors.append(
                "A correct-answer selection did not match any answer option. Please reload the "
                "page and try again."
            )
            return False

        ok = True
        normalized = []
        seen_text = set()
        for index, key in enumerate(keys):
            raw = texts[index] or ""
            text = raw.strip()
            if len(raw) > self.OPTION_TEXT_MAX:
                self.option_errors.append(
                    f"Answer option {index + 1} is longer than {self.OPTION_TEXT_MAX} "
                    "characters."
                )
                ok = False
                continue
            if not text:
                self.option_errors.append(
                    f"Answer option {index + 1} is empty. Write it or remove the row."
                )
                ok = False
                continue
            if text in seen_text:
                self.option_errors.append(
                    f"Answer option {index + 1} repeats the wording of an earlier option. "
                    "Every option must be different."
                )
                ok = False
                continue
            seen_text.add(text)
            normalized.append(
                SubmittedOption(
                    key=key,
                    public_id=None if _NEW_KEY_RE.match(key) else key,
                    text=text,
                    is_correct=key in set(self._correct_keys),
                )
            )

        if not ok:
            return False

        if not self._validate_answer_cardinality(normalized):
            return False

        self.options = normalized
        return True

    def _validate_answer_cardinality(self, options):
        """The two approved answer modes, and nothing else.

        Never silently adds, clears or truncates a correct answer: a
        mismatch is an explicit rejection that keeps the Teacher's
        attempted values, so the choice stays theirs.
        """
        mode = self.answer_mode.data
        correct = [option for option in options if option.is_correct]

        if mode == self.SINGLE:
            if len(correct) != 1:
                self.option_errors.append(
                    "Single answer mode needs exactly one correct option, and "
                    f"{len(correct)} {'is' if len(correct) == 1 else 'are'} selected. "
                    "Choose the one correct answer yourself -- nothing is selected or "
                    "cleared for you."
                )
                return False
            return True

        if mode == self.MULTIPLE:
            if len(correct) < self.MIN_OPTIONS:
                self.option_errors.append(
                    "Multiple answers mode needs at least two correct options, and "
                    f"{len(correct)} {'is' if len(correct) == 1 else 'are'} selected. "
                    "Select the others yourself -- nothing is selected for you. Marking "
                    "every option correct is allowed."
                )
                return False
            return True

        # An unsupported mode. `RadioField` already rejects it, so this is
        # the belt-and-braces path for a tampered body; it never guesses a
        # default.
        self.option_errors.append("Choose whether this question has one answer or several.")
        return False


def submitted_option_fields(formdata):
    """The three repeated option lists, read from one request body.

    Kept here beside the parser so the field names live in exactly one
    module, and so the route never spells them itself.
    """
    return (
        formdata.getlist(OPTION_KEY_FIELD),
        formdata.getlist(OPTION_TEXT_FIELD),
        formdata.getlist(OPTION_CORRECT_FIELD),
    )


# ===========================================================================
# Phase 4 / M04D -- availability, timing and attempt settings
# ===========================================================================


class QuizSettingsForm(FlaskForm):
    """The Quiz's availability window, optional time limit and attempt
    limit (Phase 4 / M04D).

    Deliberately a **separate** form from :class:`QuizForm`. Title and
    instructions are what the Quiz *says*; these fields are when and how
    often Students may take it. Keeping them apart means a metadata save
    can never silently move an availability window, and this save can
    never touch authored wording.

    There is no ``status``, ``published_at``, question, option or score
    field here: publication is owned solely by its own POST route, so a
    forged ``status`` or ``published_at`` in the body has nowhere to land,
    and grading policy does not exist anywhere to be configured.

    **Times cross this boundary as local wall clocks.** Both datetime
    fields are entered and rendered in ``APP_TIMEZONE`` (the template
    shows the label) and :meth:`validate` converts them to the canonical
    naive-UTC values the route persists, through the shared M09 timezone
    policy. A nonexistent or ambiguous DST wall-clock value is rejected
    with the helper's own safe message rather than resolved by guessing a
    fold.

    **Availability is a pair.** Either both moments are cleared -- a Quiz
    still being written -- or both are present and ordered. A
    half-configured window is exactly the state that would let publication
    proceed with "until when?" undecided, so the form refuses it and
    ``ck_quizzes_availability_window`` refuses it again.

    ``attempt_limit`` is required and bounded 1..10; there is deliberately
    no "unlimited" choice, because nobody decided what unlimited would
    mean for a graded attempt. ``time_limit_minutes`` is genuinely
    optional -- absent means the attempt simply ends when the Quiz closes
    -- and bounded 1..300 when present.
    """

    MIN_TIME_LIMIT = MIN_TIME_LIMIT_MINUTES
    MAX_TIME_LIMIT = MAX_TIME_LIMIT_MINUTES
    MIN_ATTEMPTS = MIN_ATTEMPT_LIMIT
    MAX_ATTEMPTS = MAX_ATTEMPT_LIMIT

    #: The first format is what WTForms renders back into the control's
    #: value and must be a valid HTML5 ``datetime-local`` value (the ``T``
    #: separator); the rest are accepted on input only. Same reasoning as
    #: ``AssignmentForm``.
    _DATETIME_FORMATS = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]

    opens_at = DateTimeLocalField(
        "Opens at", format=_DATETIME_FORMATS, validators=[Optional()]
    )
    closes_at = DateTimeLocalField(
        "Closes at", format=_DATETIME_FORMATS, validators=[Optional()]
    )
    time_limit_minutes = IntegerField(
        "Time limit in minutes (optional)",
        validators=[
            Optional(),
            NumberRange(min=MIN_TIME_LIMIT_MINUTES, max=MAX_TIME_LIMIT_MINUTES),
        ],
    )
    attempt_limit = IntegerField(
        "Attempts allowed",
        validators=[
            InputRequired(),
            NumberRange(min=MIN_ATTEMPT_LIMIT, max=MAX_ATTEMPT_LIMIT),
        ],
    )
    submit = SubmitField("Save Settings")

    def __init__(self, *args, tz_name=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tz_name = tz_name
        #: Canonical naive-UTC values, set by `validate` once both fields
        #: parsed and converted. The route persists these -- never
        #: `opens_at.data` / `closes_at.data`, which are local wall clocks.
        self.opens_at_utc = None
        self.closes_at_utc = None

    def _to_utc(self, field):
        try:
            return from_app_local(self._tz_name, field.data)
        except LocalTimeError as exc:
            field.errors.append(str(exc))
            return None

    def validate(self, extra_validators=None):
        """Ordinary field validation, then the availability-pair rule.

        The ordering rule is checked against the **converted UTC** values,
        not the local ones: that is the pair the database CHECK
        constrains, and across a DST transition the two comparisons can
        genuinely disagree.
        """
        if not super().validate(extra_validators=extra_validators):
            return False

        opens, closes = self.opens_at.data, self.closes_at.data
        if opens is None and closes is None:
            # A Quiz still being written. Publication will refuse it, and
            # says so on its own page rather than here.
            return True
        if opens is None or closes is None:
            missing = self.opens_at if opens is None else self.closes_at
            missing.errors.append(
                "Set both an opening time and a closing time, or leave both empty."
            )
            return False

        self.opens_at_utc = self._to_utc(self.opens_at)
        self.closes_at_utc = self._to_utc(self.closes_at)
        if self.opens_at_utc is None or self.closes_at_utc is None:
            return False
        if self.opens_at_utc >= self.closes_at_utc:
            self.closes_at.errors.append(
                "The closing time must be after the opening time."
            )
            return False
        return True
