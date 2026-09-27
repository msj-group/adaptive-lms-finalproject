"""Forms for the Researcher's experiment protocol catalogue (Phase 6 / M02A).

Four WTForms forms: a protocol header, a derived version's header, a task
set, and a task. **There is no status, digest, activation, order, actor,
participant or target field, and no placeholder for one.** The order of sets
and tasks is server-owned; activation, discarding and moving are separate
POST-only controls with their own signed tokens; the digest is computed on
the server.

Every text field is normalised by ``app/services/experiment_protocol_text.py``
so the value redisplayed after an error is the value that would be stored,
and every choice is one of the closed sets the database CHECKs name.
Nothing here authorizes anything or touches a lock: the transactions
re-prove everything that depends on other rows against locked rows.
"""

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import ValidationError

from app.models import (
    ALLOWED_CRITERIA_BY_TYPE,
    EQUIVALENCE_RATIONALE_MAX_LENGTH,
    MAX_RECOMMENDED_DURATION_SECONDS,
    MIN_RECOMMENDED_DURATION_SECONDS,
    PROTOCOL_TITLE_MAX_LENGTH,
    PROTOCOL_VERSION_MAX_LENGTH,
    TASK_GOAL_MAX_LENGTH,
    TASK_INSTRUCTIONS_MAX_LENGTH,
    TASK_SET_CODE_MAX_LENGTH,
    TASK_SET_TITLE_MAX_LENGTH,
    TASK_TITLE_MAX_LENGTH,
)
from app.services import experiment_protocol_text as text
from app.services.experiment_protocol_queries import (
    CRITERION_LABELS,
    DIFFICULTY_LABELS,
    TASK_TYPE_LABELS,
)

_PROHIBITED = (
    "Remove participant codes and email addresses. Protocol text describes tasks and "
    "must not identify anybody."
)
_CONTROL = "Use ordinary text only."


def _messages(missing, too_long, fmt=None):
    messages = {
        text.MISSING: missing,
        text.CONTROL: _CONTROL,
        text.TOO_LONG: too_long,
        text.PROHIBITED: _PROHIBITED,
    }
    if fmt:
        messages[text.FORMAT] = fmt
    return messages


VERSION_MESSAGES = _messages(
    "Give this protocol version an identifier, for example VA-2026-01.",
    f"The version identifier must be at most {PROTOCOL_VERSION_MAX_LENGTH} characters.",
    "Use letters, digits, dots, hyphens and underscores only, starting with a letter or digit.",
)
TITLE_MESSAGES = _messages(
    "Give this protocol version a title.",
    f"The title must be at most {PROTOCOL_TITLE_MAX_LENGTH} characters.",
)
RATIONALE_MESSAGES = _messages(
    "",
    f"The equivalence rationale must be at most {EQUIVALENCE_RATIONALE_MAX_LENGTH} characters.",
)
SET_CODE_MESSAGES = _messages(
    "Give this task set a code, for example SET-A.",
    f"The set code must be at most {TASK_SET_CODE_MAX_LENGTH} characters.",
    "Use letters, digits and hyphens only, starting with a letter or digit.",
)
SET_TITLE_MESSAGES = _messages(
    "Give this task set a title.",
    f"The title must be at most {TASK_SET_TITLE_MAX_LENGTH} characters.",
)
TASK_TITLE_MESSAGES = _messages(
    "Give this task a title.",
    f"The title must be at most {TASK_TITLE_MAX_LENGTH} characters.",
)
INSTRUCTIONS_MESSAGES = _messages(
    "Write the instructions a participant would read.",
    f"The instructions must be at most {TASK_INSTRUCTIONS_MAX_LENGTH} characters.",
)
GOAL_MESSAGES = _messages(
    "Describe the expected goal of this task.",
    f"The expected goal must be at most {TASK_GOAL_MAX_LENGTH} characters.",
)
DURATION_MESSAGE = (
    f"Enter a whole number of seconds from {MIN_RECOMMENDED_DURATION_SECONDS} to "
    f"{MAX_RECOMMENDED_DURATION_SECONDS}."
)
PAIR_MESSAGE = "That completion criterion cannot be used with this task type."


def _normalized(normalizer, messages):
    """A validator that replaces the field's data with its normalised value,
    or raises the matching sentence."""

    def _validate(_form, field):
        value, error = normalizer(field.data)
        if error is not None:
            raise ValidationError(messages[error])
        field.data = value

    return _validate


def _duration(_form, field):
    raw = (field.data or "").strip() if isinstance(field.data, str) else ""
    if not raw.isascii() or not raw.isdigit():
        raise ValidationError(DURATION_MESSAGE)
    value = int(raw)
    if not MIN_RECOMMENDED_DURATION_SECONDS <= value <= MAX_RECOMMENDED_DURATION_SECONDS:
        raise ValidationError(DURATION_MESSAGE)
    field.data = value


class ProtocolForm(FlaskForm):
    """A draft protocol version's header."""

    version_identifier = StringField(
        "Version identifier",
        validators=[_normalized(text.normalize_protocol_version, VERSION_MESSAGES)],
    )
    title = StringField(
        "Title", validators=[_normalized(text.normalize_protocol_title, TITLE_MESSAGES)]
    )
    equivalence_rationale = TextAreaField(
        "Equivalence rationale",
        validators=[_normalized(text.normalize_equivalence_rationale, RATIONALE_MESSAGES)],
    )
    submit = SubmitField("Save draft")


class DeriveForm(FlaskForm):
    """The header of a new draft copied from a frozen version."""

    version_identifier = StringField(
        "New version identifier",
        validators=[_normalized(text.normalize_protocol_version, VERSION_MESSAGES)],
    )
    title = StringField(
        "Title", validators=[_normalized(text.normalize_protocol_title, TITLE_MESSAGES)]
    )
    submit = SubmitField("Create draft copy")


class TaskSetForm(FlaskForm):
    """One task set of a draft."""

    set_code = StringField(
        "Set code", validators=[_normalized(text.normalize_set_code, SET_CODE_MESSAGES)]
    )
    title = StringField(
        "Title", validators=[_normalized(text.normalize_set_title, SET_TITLE_MESSAGES)]
    )
    submit = SubmitField("Save task set")


class TaskForm(FlaskForm):
    """One task of a draft task set."""

    task_type = SelectField("Task type", choices=list(TASK_TYPE_LABELS.items()))
    title = StringField(
        "Title", validators=[_normalized(text.normalize_task_title, TASK_TITLE_MESSAGES)]
    )
    participant_instructions = TextAreaField(
        "Participant instructions",
        validators=[_normalized(text.normalize_participant_instructions,
                                INSTRUCTIONS_MESSAGES)],
    )
    expected_goal = TextAreaField(
        "Expected goal",
        validators=[_normalized(text.normalize_expected_goal, GOAL_MESSAGES)],
    )
    difficulty = SelectField("Intended difficulty", choices=list(DIFFICULTY_LABELS.items()))
    recommended_duration_seconds = StringField(
        "Recommended duration (seconds)", validators=[_duration]
    )
    completion_criterion = SelectField(
        "Intended completion criterion", choices=list(CRITERION_LABELS.items())
    )
    submit = SubmitField("Save task")

    def validate_completion_criterion(self, field):
        allowed = ALLOWED_CRITERIA_BY_TYPE.get(self.task_type.data, ())
        if field.data not in allowed:
            raise ValidationError(PAIR_MESSAGE)

    def values(self):
        """The validated task fields, keyed as the model names them."""
        return {
            "task_type": self.task_type.data,
            "title": self.title.data,
            "participant_instructions": self.participant_instructions.data,
            "expected_goal": self.expected_goal.data,
            "difficulty": self.difficulty.data,
            "recommended_duration_seconds": self.recommended_duration_seconds.data,
            "completion_criterion": self.completion_criterion.data,
        }
