"""Export selection. Collection has no scheduling form."""
from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms import SelectField, SelectMultipleField, SubmitField
from wtforms.fields import DateField
from wtforms.validators import DataRequired, Optional

class ExportForm(FlaskForm):
    groups = SelectMultipleField("Groups", choices=[], validators=[Optional()], validate_choice=True)
    configuration = SelectField("Collection", choices=[], default="", validators=[DataRequired()], validate_choice=True)
    period_from = DateField("From", validators=[Optional()])
    period_to = DateField("Through", validators=[Optional()])
    submit = SubmitField("Create export")
