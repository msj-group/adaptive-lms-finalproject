from flask_wtf import FlaskForm
from wtforms import DateField, IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, Course, Group, Level


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


class CourseForm(FlaskForm):
    level_id = SelectField("Level", coerce=int, validators=[DataRequired()])
    title = StringField("Title", validators=[DataRequired(), Length(max=150)])
    code = StringField("Code", validators=[Optional(), Length(max=20)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=5000)])
    submit = SubmitField("Save")

    def __init__(self, *args, course_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._course_id = course_id
        self.level_id.choices = [
            (
                level.id,
                level.name if level.status == AcademicStatus.ACTIVE.value else f"{level.name} (Archived)",
            )
            for level in Level.query.order_by(Level.display_order, Level.id).all()
        ]

    def validate_level_id(self, field):
        if db.session.get(Level, field.data) is None:
            raise ValidationError("Selected level does not exist.")

    def validate_title(self, field):
        query = Course.query.filter(
            Course.level_id == self.level_id.data, Course.title == field.data.strip()
        )
        if self._course_id is not None:
            query = query.filter(Course.id != self._course_id)
        if query.first() is not None:
            raise ValidationError("A course with this title already exists in the selected level.")

    def validate_code(self, field):
        if not field.data or not field.data.strip():
            return
        query = Course.query.filter(
            Course.level_id == self.level_id.data, Course.code == field.data.strip()
        )
        if self._course_id is not None:
            query = query.filter(Course.id != self._course_id)
        if query.first() is not None:
            raise ValidationError("A course with this code already exists in the selected level.")


class GroupForm(FlaskForm):
    academic_term_id = SelectField("Academic Term", coerce=int, validators=[DataRequired()])
    course_id = SelectField("Course", coerce=int, validators=[DataRequired()])
    name = StringField("Group Name", validators=[DataRequired(), Length(max=100)])
    code = StringField("Group Code", validators=[Optional(), Length(max=20)])
    capacity = IntegerField("Capacity", validators=[DataRequired(), NumberRange(min=1)])
    status = SelectField(
        "Status",
        choices=[(s.value, s.value.capitalize()) for s in AcademicStatus],
        default=AcademicStatus.ACTIVE.value,
        validators=[DataRequired()],
    )
    submit = SubmitField("Save")

    def __init__(self, *args, group_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group_id = group_id
        self.academic_term_id.choices = [
            (
                term.id,
                term.name if term.status == AcademicStatus.ACTIVE.value else f"{term.name} (Archived)",
            )
            for term in AcademicTerm.query.order_by(AcademicTerm.start_date.desc()).all()
        ]
        self.course_id.choices = [
            (
                course.id,
                f"{course.title} ({course.level.name})"
                if course.status == AcademicStatus.ACTIVE.value
                else f"{course.title} ({course.level.name}) (Archived)",
            )
            for course in Course.query.join(Level).order_by(Level.display_order, Course.display_order).all()
        ]

    def validate_academic_term_id(self, field):
        if db.session.get(AcademicTerm, field.data) is None:
            raise ValidationError("Selected academic term does not exist.")

    def validate_course_id(self, field):
        if db.session.get(Course, field.data) is None:
            raise ValidationError("Selected course does not exist.")

    def validate_name(self, field):
        query = Group.query.filter(
            Group.academic_term_id == self.academic_term_id.data,
            Group.course_id == self.course_id.data,
            Group.name == field.data.strip(),
        )
        if self._group_id is not None:
            query = query.filter(Group.id != self._group_id)
        if query.first() is not None:
            raise ValidationError(
                "A group with this name already exists for the selected term and course."
            )

    def validate_code(self, field):
        if not field.data or not field.data.strip():
            return
        query = Group.query.filter(
            Group.academic_term_id == self.academic_term_id.data,
            Group.course_id == self.course_id.data,
            Group.code == field.data.strip(),
        )
        if self._group_id is not None:
            query = query.filter(Group.id != self._group_id)
        if query.first() is not None:
            raise ValidationError(
                "A group with this code already exists for the selected term and course."
            )
