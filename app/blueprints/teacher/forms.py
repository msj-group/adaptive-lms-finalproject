from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.models import Lesson, Material, Unit
from app.services.material_content import (
    ExternalUrlError,
    sanitize_rich_text_html,
    validate_external_url,
)
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
