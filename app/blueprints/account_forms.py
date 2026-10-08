"""Separate native, CSRF-protected forms for the current account only."""
from app.i18n import LocalizedFlaskForm as FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import HiddenField, PasswordField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length

from app.security.passwords import ACCOUNT_PASSWORD_MAX_LENGTH, ACCOUNT_PASSWORD_MIN_LENGTH


class ChangePasswordForm(FlaskForm):
    state = HiddenField(validators=[DataRequired(), Length(max=2048)])
    current_password = PasswordField("Current password", validators=[DataRequired(), Length(max=1024)])
    new_password = PasswordField("New password", validators=[
        DataRequired(), Length(min=ACCOUNT_PASSWORD_MIN_LENGTH, max=ACCOUNT_PASSWORD_MAX_LENGTH),
    ])
    confirm_password = PasswordField("Confirm new password", validators=[
        DataRequired(), Length(max=ACCOUNT_PASSWORD_MAX_LENGTH),
        EqualTo("new_password", message="Passwords do not match."),
    ])
    submit = SubmitField("Change password")


class ProfilePhotoForm(FlaskForm):
    state = HiddenField(validators=[DataRequired(), Length(max=2048)])
    photo = FileField("Choose a photo", validators=[FileRequired("Choose a photo to upload.")])
    submit = SubmitField("Save photo")
