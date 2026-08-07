from flask_wtf import FlaskForm
from wtforms import DateField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from app.models import AcademicTerm, Level


class AcademicTermForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    start_date = DateField("Start Date", validators=[DataRequired()], render_kw={"type": "date"})
    end_date = DateField("End Date", validators=[DataRequired()], render_kw={"type": "date"})
    submit = SubmitField("Save")

    def __init__(self, *args, term_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._term_id = term_id

    def validate_name(self, field):
        query = AcademicTerm.query.filter(AcademicTerm.name == field.data.strip())
        if self._term_id is not None:
            query = query.filter(AcademicTerm.id != self._term_id)
        if query.first() is not None:
            raise ValidationError("An academic term with this name already exists.")

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data <= self.start_date.data:
            raise ValidationError("End date must be after the start date.")


class LevelForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    code = StringField("Code", validators=[Optional(), Length(max=20)])
    submit = SubmitField("Save")

    def __init__(self, *args, level_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._level_id = level_id

    def validate_name(self, field):
        query = Level.query.filter(Level.name == field.data.strip())
        if self._level_id is not None:
            query = query.filter(Level.id != self._level_id)
        if query.first() is not None:
            raise ValidationError("A level with this name already exists.")

    def validate_code(self, field):
        if not field.data or not field.data.strip():
            return
        query = Level.query.filter(Level.code == field.data.strip())
        if self._level_id is not None:
            query = query.filter(Level.id != self._level_id)
        if query.first() is not None:
            raise ValidationError("A level with this code already exists.")
