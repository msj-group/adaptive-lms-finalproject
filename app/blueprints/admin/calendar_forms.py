"""Form and input parsing for the Administrator center-calendar surface
(Phase 4 / M10).

One WTForms form, for the one thing M10 stores: a center-wide
:class:`~app.models.calendar_event.CalendarEvent`.

**There is no audience, scope, target, recurrence, reminder, attachment
or link field, and no placeholder for one.** A center event is addressed
to the whole center by definition, happens on exactly one date, and is
plain text. Each of those absences is a decision, not an omission: a
Course- or Group-targeted event, a repeating event and a notification
are different objects with different rules, and none of them is
approved.

**There is likewise no ``status`` field.** A new event is ``scheduled``
the moment it is created -- there is no draft state to choose -- and
cancellation is a separate POST-only route with its own confirmation and
its own signed token. A status control here would let a slip of the
mouse cancel something the whole center is expecting.

**All-day or timed is expressed by the two time fields themselves.**
Leaving both empty means an all-day event; filling both in means a timed
one, and then the end must be after the start on the same civil day.
Exactly one of the two is the single illegal shape and is rejected here,
before any lock -- and ``ck_calendar_events_time_shape`` refuses the same
shape in the database as the final defense. There is deliberately no
separate "all day" checkbox: a checkbox and the two fields could
disagree, and then the form would have a state the database cannot
store.

Nothing here authorizes anything or touches a lock. Text normalisation
is not duplicated either: all three text fields go through
``app/services/calendar_text.py``. This module owns only the
Administrator's wording for each rule.
"""

from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms import DateField, StringField, SubmitField, TextAreaField, TimeField
from wtforms.validators import DataRequired, Optional, ValidationError

from app.models import (
    CALENDAR_EVENT_DETAILS_MAX_LENGTH,
    CALENDAR_EVENT_LOCATION_MAX_LENGTH,
    CALENDAR_EVENT_TITLE_MAX_LENGTH,
)
from app.services.calendar_text import (
    CONTROL,
    MISSING,
    TOO_LONG,
    normalize_details,
    normalize_location,
    normalize_title,
)

TITLE_MESSAGES = {
    MISSING: "Give this event a title.",
    CONTROL: "The title may only contain ordinary text.",
    TOO_LONG: f"The title must be at most {CALENDAR_EVENT_TITLE_MAX_LENGTH} characters.",
}
DETAILS_MESSAGES = {
    CONTROL: "The details may only contain ordinary text and line breaks.",
    TOO_LONG: (
        f"The details must be at most {CALENDAR_EVENT_DETAILS_MAX_LENGTH} characters."
    ),
}
LOCATION_MESSAGES = {
    CONTROL: "The location may only contain ordinary text.",
    TOO_LONG: (
        f"The location must be at most {CALENDAR_EVENT_LOCATION_MAX_LENGTH} characters."
    ),
}

DATE_REQUIRED_MESSAGE = "Choose the date this event happens on."
TIME_PAIR_MESSAGE = (
    "Give both a start time and an end time, or leave both empty for an all-day event."
)
TIME_ORDER_MESSAGE = (
    "The end time must be after the start time. An event that runs past midnight is not "
    "supported — create one event per day."
)


class CalendarEventForm(FlaskForm):
    """Title, details, date, optional times and optional location for one
    center calendar event.

    The validated, normalized results are stored on the form
    (:attr:`normalized_title`, :attr:`normalized_details`,
    :attr:`normalized_location`) so the route persists exactly what was
    validated rather than re-deriving it from the raw request a second
    time and risking a different answer.
    """

    title = StringField("Title")
    details = TextAreaField("Details")
    event_date = DateField(
        "Date",
        validators=[DataRequired(message=DATE_REQUIRED_MESSAGE)],
        render_kw={"type": "date"},
    )
    #: ``Optional`` so an **empty** field is "no time" rather than
    #: "not a valid time" -- leaving both empty is one of the two legal
    #: shapes. A non-empty but unparseable value still fails here, and
    #: the pair rule below runs afterwards in :meth:`validate`.
    start_time = TimeField(
        "Start time", validators=[Optional()], render_kw={"type": "time"}
    )
    end_time = TimeField(
        "End time", validators=[Optional()], render_kw={"type": "time"}
    )
    location = StringField("Location")
    submit = SubmitField("Save event")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.normalized_title = None
        self.normalized_details = None
        self.normalized_location = None

    def validate_title(self, field):
        value, error = normalize_title(field.data)
        if error is not None:
            raise ValidationError(TITLE_MESSAGES[error])
        self.normalized_title = value

    def validate_details(self, field):
        value, error = normalize_details(field.data)
        if error is not None:
            raise ValidationError(DETAILS_MESSAGES[error])
        self.normalized_details = value

    def validate_location(self, field):
        value, error = normalize_location(field.data)
        if error is not None:
            raise ValidationError(LOCATION_MESSAGES[error])
        self.normalized_location = value

    def validate(self, extra_validators=None):
        """Ordinary field validation, then the all-day / timed **pair**
        rule and the ordering inside a timed window.

        The pair rule lives here rather than in a ``validate_end_time``
        inline validator for a mechanical reason worth stating: both time
        fields carry ``Optional()``, which raises ``StopValidation`` on an
        empty field and therefore skips every validator after it --
        including an inline one. A rule about *two* fields, one of which
        may legitimately be empty, has to run after the per-field chain,
        which is exactly what this hook is. The same arrangement
        ``teacher/forms.py`` uses for its ``opens_at < due_at`` rule.

        Skipped entirely when either field failed to parse: that field has
        already said what is wrong with it, and adding "give both times"
        on top of "this is not a time" would explain the same typo twice.

        ``ck_calendar_events_time_shape`` refuses the same two illegal
        shapes in the database as the final defense.
        """
        ok = super().validate(extra_validators=extra_validators)
        if self.start_time.errors or self.end_time.errors:
            return False
        start, end = self.start_time.data, self.end_time.data
        if start is None and end is None:
            return ok  # an all-day event -- the legal shape with no times
        if start is None or end is None:
            self.end_time.errors.append(TIME_PAIR_MESSAGE)
            return False
        if end <= start:
            self.end_time.errors.append(TIME_ORDER_MESSAGE)
            return False
        return ok
