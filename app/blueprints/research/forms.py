"""Forms of the Researcher workspace (Phase 6 replacement).

Configuration periods cross this boundary as local wall-clock values in
``APP_TIMEZONE`` and are converted to canonical naive UTC with the project's
shared ``from_app_local`` policy (a nonexistent or ambiguous DST time is
refused, never guessed) -- the same rule as the Assignment form.

Every server-owned field (status, version number, marker, collecting state,
retention, actors, moments) is absent from every form: a forged field has
nowhere to land.
"""

from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms import BooleanField, HiddenField, IntegerField, SelectField, StringField, SubmitField
from wtforms.fields import DateField, DateTimeLocalField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models import CONFIGURATION_LABEL_MAX_LENGTH, POLICY_BOUNDS
from app.services.schedule_occurrences import LocalTimeError, from_app_local

_DATETIME_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"]

POLICY_LABELS = {
    "session_inactivity_minutes": "Session inactivity timeout (minutes)",
    "max_prompts_per_session": "Automatic prompts per session (maximum)",
    "max_prompts_per_day": "Automatic prompts per student per day (maximum)",
    "lookback_seconds": "Lookback window ending at the prompt (seconds)",
    "min_observed_seconds": "Minimum continuously observed time in the window (seconds)",
    "random_prompt_permille": "Random-moment probability per eligible check (per mille)",
    "activity_end_prompt_permille": "Activity-end probability per natural ending (per mille)",
    "offer_ttl_seconds": "Offer lifetime before display (seconds)",
    "response_window_seconds": "Response window after display (seconds)",
}

def _policy_field(column):
    low, high, _default = POLICY_BOUNDS[column]
    return IntegerField(
        POLICY_LABELS[column], validators=[DataRequired() if low > 0 else Optional(),
                                           NumberRange(min=low, max=high)]
    )


class ConfigurationForm(FlaskForm):
    label = StringField(
        "Label", validators=[DataRequired(), Length(max=CONFIGURATION_LABEL_MAX_LENGTH)]
    )
    collection_starts_at = DateTimeLocalField(
        "Collection starts", format=_DATETIME_FORMATS, validators=[DataRequired()]
    )
    collection_ends_at = DateTimeLocalField(
        "Collection ends", format=_DATETIME_FORMATS, validators=[DataRequired()]
    )
    session_inactivity_minutes = _policy_field("session_inactivity_minutes")
    max_prompts_per_session = _policy_field("max_prompts_per_session")
    max_prompts_per_day = _policy_field("max_prompts_per_day")
    lookback_seconds = _policy_field("lookback_seconds")
    min_observed_seconds = _policy_field("min_observed_seconds")
    random_prompt_permille = _policy_field("random_prompt_permille")
    activity_end_prompt_permille = _policy_field("activity_end_prompt_permille")
    offer_ttl_seconds = _policy_field("offer_ttl_seconds")
    response_window_seconds = _policy_field("response_window_seconds")
    state = HiddenField()
    submit = SubmitField("Save draft")

    def __init__(self, *args, tz_name=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tz_name = tz_name
        self.starts_utc = None
        self.ends_utc = None

    def _to_utc(self, field):
        try:
            return from_app_local(self._tz_name, field.data)
        except LocalTimeError as exc:
            field.errors.append(str(exc))
            return None

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        for column in POLICY_BOUNDS:
            if getattr(self, column).data is None:
                getattr(self, column).errors.append("A whole number is required.")
                return False
        self.starts_utc = self._to_utc(self.collection_starts_at)
        self.ends_utc = self._to_utc(self.collection_ends_at)
        if self.starts_utc is None or self.ends_utc is None:
            return False
        if self.ends_utc <= self.starts_utc:
            self.collection_ends_at.errors.append("The end must be after the start.")
            return False
        if self.min_observed_seconds.data > self.lookback_seconds.data:
            self.min_observed_seconds.errors.append(
                "The observed minimum cannot exceed the lookback window."
            )
            return False
        if " ".join(self.label.data.split()) == "":
            self.label.errors.append("A label is required.")
            return False
        return True

    def values(self):
        """The editable values, normalised, for the transaction."""
        result = {
            "label": " ".join(self.label.data.split()),
            "collection_starts_at": self.starts_utc.replace(microsecond=0),
            "collection_ends_at": self.ends_utc.replace(microsecond=0),
        }
        for column in POLICY_BOUNDS:
            result[column] = int(getattr(self, column).data)
        return result


class ActivateForm(FlaskForm):
    confirm = BooleanField(
        "I understand that activation freezes this version and retires the active one. "
        "It does not start collection.",
        validators=[DataRequired(message="Tick the box to confirm.")],
    )
    state = HiddenField()
    submit = SubmitField("Activate version")


class CollectingForm(FlaskForm):
    state = HiddenField()
    submit = SubmitField()


class ExportForm(FlaskForm):
    configuration = SelectField("Configuration version", choices=[], default="",
                                validate_choice=True)
    period_from = DateField("Sessions starting from (local date)", validators=[Optional()])
    period_to = DateField("Sessions starting until (local date)", validators=[Optional()])
    submit = SubmitField("Create export")
