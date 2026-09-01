from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.models import Lesson, Unit


class UnitForm(FlaskForm):
    """Teacher create/edit of one Group-owned Unit (M10).

    Carries only the editable fields: ``title`` and ``description``.
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


class LessonForm(FlaskForm):
    """Teacher create/edit of one Unit-owned Lesson (M11).

    Carries only the editable fields: ``title`` and plain-text
    ``description`` (5000-character form boundary). There is **no**
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
