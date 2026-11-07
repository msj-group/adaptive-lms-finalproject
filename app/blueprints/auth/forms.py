from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_device = BooleanField("Keep me signed in on this device")
    submit = SubmitField("Log In")
