from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.fields import DateTimeLocalField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.models import Assignment, Lesson, Material, Unit
from app.services.material_content import (
    ExternalUrlError,
    sanitize_rich_text_html,
    validate_external_url,
)
from app.services.schedule_occurrences import LocalTimeError, from_app_local
from app.services.search_terms import SearchKeywordsError, normalize_search_keywords


class _SearchKeywordsMixin:
    """Shared optional ``search_keywords`` field + validator (M13) for the
    Teacher Unit / Lesson / Material create/edit forms.

    Raw input is comma- or newline-separated. ``validate_search_keywords``
    runs the shared pure normaliser and, on success, replaces
    ``field.data`` with the canonical ``", "``-joined string (or
    ``None``) so the route always persists exactly what was validated.
    There is deliberately no ``Optional()`` validator -- the normaliser
    itself turns an empty submission into ``None``. Templates place the
    field explicitly, so its declaration order here does not matter.
    """

    search_keywords = TextAreaField(
        "Search keywords (optional)", validators=[Length(max=2000)]
    )

    def validate_search_keywords(self, field):
        try:
            field.data = normalize_search_keywords(field.data)
        except SearchKeywordsError as exc:
            raise ValidationError(str(exc)) from exc


class UnitForm(_SearchKeywordsMixin, FlaskForm):
    """Teacher create/edit of one Group-owned Unit (M10, + M13 keywords).

    Carries only the editable fields: ``title``, ``description``, and the
    optional M13 ``search_keywords``.
    There is **no** ``status`` control (owned by the dedicated toggle
    route) and **no** ``display_order`` control (server-owned; new /
    reactivated Units append after the Group's highest order, move-up /
    move-down handle the rest). The title-uniqueness check here is a
    friendly pre-lock guard; the DB ``UniqueConstraint(group_id, title)``
    is the final defense and the route catches the resulting
    ``IntegrityError``.
    """

    title = StringField("Title", validators=[DataRequired(), Length(max=150)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=5000)])
    submit = SubmitField("Save Unit")

    def __init__(self, *args, group_id=None, unit_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group_id = group_id
        self._unit_id = unit_id

    def validate_title(self, field):
        if self._group_id is None:
            return
        query = Unit.query.filter(
            Unit.group_id == self._group_id, Unit.title == field.data.strip()
        )
        if self._unit_id is not None:
            query = query.filter(Unit.id != self._unit_id)
        if query.first() is not None:
            raise ValidationError("A unit with this title already exists in this group.")


class LessonForm(_SearchKeywordsMixin, FlaskForm):
    """Teacher create/edit of one Unit-owned Lesson (M11, + M13 keywords).

    Carries only the editable fields: ``title``, plain-text
    ``description`` (5000-character form boundary), and the optional M13
    ``search_keywords``. There is **no**
    ``status`` control (publication is owned by the dedicated
    toggle-publication route) and **no** ``display_order`` control
    (server-owned; new Lessons append after the Unit's highest order,
    move-up / move-down handle the rest). The title-uniqueness check here
    is a friendly pre-lock guard; the DB
    ``UniqueConstraint(unit_id, title)`` is the final defense and the
    route catches the resulting ``IntegrityError``.
    """

    title = StringField("Title", validators=[DataRequired(), Length(max=150)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=5000)])
    submit = SubmitField("Save Lesson")

    def __init__(self, *args, unit_id=None, lesson_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._unit_id = unit_id
        self._lesson_id = lesson_id

    def validate_title(self, field):
        if self._unit_id is None:
            return
        query = Lesson.query.filter(
            Lesson.unit_id == self._unit_id, Lesson.title == field.data.strip()
        )
        if self._lesson_id is not None:
            query = query.filter(Lesson.id != self._lesson_id)
        if query.first() is not None:
            raise ValidationError("A lesson with this title already exists in this unit.")


class AssignmentForm(FlaskForm):
    """Teacher create/edit of one Group-owned Assignment (Phase 4 / M01).

    Carries only the four editable fields: ``title``, plain-text
    ``instructions``, ``opens_at`` and ``due_at``. There is deliberately
    **no** ``status`` / ``published_at`` control -- publication is owned
    solely by the dedicated toggle-publication route, so a forged
    ``status`` or ``published_at`` field in the POST body has nowhere to
    land -- and **no** ordering control, because Assignment presentation
    is time-driven.

    **Times cross this boundary in local wall-clock form.** The two
    datetime fields are entered and rendered in ``APP_TIMEZONE`` (the
    template shows the label), and :meth:`validate` converts them to the
    canonical naive-UTC values the route persists, using the shared M09
    timezone policy through
    :func:`app.services.schedule_occurrences.from_app_local`. A
    nonexistent or ambiguous DST wall-clock value is rejected with the
    helper's own safe message rather than being resolved by guessing a
    fold; an unresolvable ``APP_TIMEZONE`` stays a fail-closed
    configuration error and is *not* caught here.

    ``instructions`` is stored in an unbounded ``Text`` column, so this
    ``Length(max=10000)`` is the finite form boundary that actually
    protects the request. The title-uniqueness check is a friendly
    pre-lock guard only; the DB ``UniqueConstraint(group_id, title)`` is
    the final defense and the route catches the resulting
    ``IntegrityError``.
    """

    #: The finite input boundary for the unbounded ``instructions`` Text
    #: column -- long enough for real task instructions, short enough that
    #: a single request can never carry an unbounded body.
    INSTRUCTIONS_MAX = 10000

    title = StringField("Title", validators=[DataRequired(), Length(max=150)])
    instructions = TextAreaField(
        "Instructions", validators=[DataRequired(), Length(max=INSTRUCTIONS_MAX)]
    )
    #: The FIRST format is what WTForms renders back into the input's
    #: ``value``, and the others are only accepted on input.
    #:
    #: It must be a valid HTML5 ``datetime-local`` value, which requires
    #: the ``T`` separator. WTForms' own default starts with a
    #: *space*-separated format, which no browser accepts and which would
    #: silently blank the field on every edit -- the space, not the
    #: seconds, is what makes it invalid. Seconds with a ``T`` separator
    #: are perfectly valid, so rendering them is what makes the edit round
    #: trip lossless at the column's real precision: a stored
    #: ``08:00:37`` re-renders as ``08:00:37`` instead of being truncated
    #: to ``08:00`` by a Teacher who only meant to fix a typo in the
    #: title. The paired ``step="1"`` on both controls (see
    #: ``teacher/assignments/form.html``) is what makes a second-bearing
    #: value a *valid* control value rather than one the browser rounds.
    #:
    #: Ordinary minute-only input stays accepted, so a browser that omits
    #: seconds still parses.
    _DATETIME_FORMATS = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]

    opens_at = DateTimeLocalField(
        "Opens at", format=_DATETIME_FORMATS, validators=[DataRequired()]
    )
    due_at = DateTimeLocalField(
        "Due at", format=_DATETIME_FORMATS, validators=[DataRequired()]
    )
    submit = SubmitField("Save Assignment")

    def __init__(self, *args, tz_name=None, group_id=None, assignment_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tz_name = tz_name
        self._group_id = group_id
        self._assignment_id = assignment_id
        #: Canonical naive-UTC values, set by `validate` once both fields
        #: parsed and converted. The route persists these -- never
        #: `opens_at.data` / `due_at.data`, which are local wall clocks.
        self.opens_at_utc = None
        self.due_at_utc = None

    def validate_title(self, field):
        if self._group_id is None:
            return
        query = Assignment.query.filter(
            Assignment.group_id == self._group_id, Assignment.title == field.data.strip()
        )
        if self._assignment_id is not None:
            query = query.filter(Assignment.id != self._assignment_id)
        if query.first() is not None:
            raise ValidationError("An assignment with this title already exists in this group.")

    def _to_utc(self, field):
        """Convert one parsed local wall-clock field to naive UTC, or
        attach a safe validation error and return ``None``."""
        try:
            return from_app_local(self._tz_name, field.data)
        except LocalTimeError as exc:
            field.errors.append(str(exc))
            return None

    def validate(self, extra_validators=None):
        """Ordinary field validation, then the timezone conversion and
        the ``opens_at < due_at`` rule.

        The ordering rule is checked against the **converted UTC** values,
        not the local ones: that is the pair the database CHECK constrains,
        and across a DST transition the two comparisons can genuinely
        disagree. The database CHECK remains the final defense.
        """
        if not super().validate(extra_validators=extra_validators):
            return False

        self.opens_at_utc = self._to_utc(self.opens_at)
        self.due_at_utc = self._to_utc(self.due_at)
        if self.opens_at_utc is None or self.due_at_utc is None:
            return False

        if self.opens_at_utc >= self.due_at_utc:
            self.due_at.errors.append("The due time must be after the opening time.")
            return False
        return True


class _MaterialTitleForm(_SearchKeywordsMixin, FlaskForm):
    """Shared title field + optional M13 ``search_keywords`` + uniqueness
    check for every Material kind (M12). ``kind`` itself is never a form
    field -- it is immutable after creation and decided entirely by which
    concrete form/route handles the request. ``search_keywords`` is
    editable for every kind, **including** ``file`` (whose bytes and
    ``kind`` stay immutable)."""

    title = StringField("Title", validators=[DataRequired(), Length(max=150)])

    def __init__(self, *args, lesson_id=None, material_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._lesson_id = lesson_id
        self._material_id = material_id

    def validate_title(self, field):
        if self._lesson_id is None:
            return
        query = Material.query.filter(
            Material.lesson_id == self._lesson_id, Material.title == field.data.strip()
        )
        if self._material_id is not None:
            query = query.filter(Material.id != self._material_id)
        if query.first() is not None:
            raise ValidationError("A material with this title already exists in this lesson.")


class RichTextMaterialForm(_MaterialTitleForm):
    """Create/edit a ``rich_text`` Material. ``content_html`` is the raw
    HTML the small local editor produced; ``validate_content_html``
    sanitises it with `nh3` and rejects a submission that becomes
    effectively empty, and -- on success -- replaces ``field.data`` with
    the sanitised value so the route always persists exactly what was
    validated, never the raw input."""

    content_html = TextAreaField("Content", validators=[DataRequired()])
    submit = SubmitField("Save Material")

    def validate_content_html(self, field):
        sanitized = sanitize_rich_text_html(field.data)
        if sanitized is None:
            raise ValidationError(
                "This content is empty once unsupported formatting is removed. Add some text."
            )
        field.data = sanitized


class ExternalLinkMaterialForm(_MaterialTitleForm):
    """Create/edit an ``external_link`` Material. ``validate_external_url``
    applies the full HTTPS/host/IP-literal policy (never a network fetch)
    and, on success, replaces ``field.data`` with the cleaned URL."""

    external_url = StringField("URL", validators=[DataRequired(), Length(max=2048)])
    submit = SubmitField("Save Material")

    def validate_external_url(self, field):
        try:
            field.data = validate_external_url(field.data)
        except ExternalUrlError as exc:
            raise ValidationError(str(exc)) from exc


class FileMaterialForm(_MaterialTitleForm):
    """Create a ``file`` Material. The file itself is validated by
    ``app.services.file_validation`` / ``file_storage`` in the route, not
    here -- WTForms' `FileRequired` only confirms a file part was sent."""

    file = FileField("File", validators=[FileRequired(message="Choose a file to upload.")])
    submit = SubmitField("Save Material")


class FileMaterialEditForm(_MaterialTitleForm):
    """Edit a ``file`` Material: the editable fields are ``title`` and the
    optional M13 ``search_keywords`` (both inherited via
    ``_MaterialTitleForm`` / ``_SearchKeywordsMixin``). The uploaded file
    bytes and ``kind`` stay immutable -- replacing a wrong file means
    archiving this Material and creating a new one."""

    submit = SubmitField("Save Material")
