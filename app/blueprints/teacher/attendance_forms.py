"""Forms for the Teacher Attendance surface (Phase 4 / M07).

Only **one** WTForms form exists here -- the create-session chooser --
because it is the only M07 input with a fixed field set. The marking page
has one status control and one optional note per *captured Student*, a
set that is decided by data rather than by a class, so it is parsed and
validated explicitly by :func:`parse_marking_submission` below rather than
by a dynamically built form class. Both paths are CSRF-protected the same
way: ``CSRFProtect`` is initialised application-wide, so every POST in
this project -- WTForms or not -- must carry a valid ``csrf_token``.

Nothing here authorizes anything or touches a lock. These are ordinary
input checks that run **before** any lock is taken, so a mistyped note or
a missing status costs no database work and the Teacher gets their own
values back to correct. Every authoritative condition is re-checked
against the locked rows afterwards.
"""

from app.i18n import LocalizedFlaskForm as FlaskForm
from wtforms import SelectField, SubmitField
from wtforms.fields import DateField
from wtforms.validators import DataRequired, InputRequired

from app.models import ATTENDANCE_NOTE_MAX_LENGTH
from app.services.attendance_queries import STATUS_VALUES

#: The request-boundary limit on one private note, mirroring the constant
#: declared beside the column (``app/models/attendance_record.py``). It is
#: applied to the **raw** submitted value, before stripping, so padding a
#: note with whitespace cannot smuggle a longer one past it.
NOTE_MAX = ATTENDANCE_NOTE_MAX_LENGTH

#: Field-name prefixes on the marking form. Each is suffixed with the
#: record's **public id** -- never an internal database id, and never a
#: row index, which a reordered page could silently re-point at another
#: Student.
STATUS_FIELD_PREFIX = "status__"
NOTE_FIELD_PREFIX = "note__"


class AttendanceSessionCreateForm(FlaskForm):
    """Choose which scheduled occurrence to open attendance for.

    Two controls and nothing else: one of the Group's **active**
    Schedules, and a local civil date. The Teacher never types a weekday,
    a start time, an end time or a location -- the server copies all four
    from the locked Schedule, which is what makes the stored session a
    snapshot of a real meeting rather than of whatever a form said.

    ``schedule`` holds a Schedule ``public_id``. The choices are supplied
    by the route from ``active_schedules_for_group``, and ``SelectField``
    pre-validation refuses any value that is not one of them -- so an
    archived slot, another Group's slot and an invented id are all
    rejected here, before a lock, and then rejected again against the
    locked rows.

    There is deliberately **no** status, no roster, no bulk control, no
    "repeat weekly" and no make-up option: none of those exists
    server-side either.
    """

    schedule = SelectField(
        "Scheduled class", validators=[DataRequired()], validate_choice=True
    )
    session_date = DateField(
        "Class date",
        validators=[InputRequired()],
        render_kw={"type": "date"},
    )
    submit = SubmitField("Continue")

    def __init__(self, *args, schedule_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.schedule.choices = list(schedule_choices)


class MarkingSubmission:
    """The parsed, ordinarily-valid result of one marking POST.

    ``values`` maps a record ``public_id`` to ``(status, note_or_None)``.
    ``errors`` is a list of Teacher-facing sentences; when it is non-empty
    the caller must re-render the form **without taking a lock and
    without writing anything**, so a single bad note never costs a Teacher
    the rest of their marking.
    """

    __slots__ = ("values", "errors")

    def __init__(self, values, errors):
        self.values = values
        self.errors = errors

    @property
    def ok(self):
        return not self.errors


def _normalize_note(raw):
    """One optional private note, normalised exactly once.

    Leading and trailing whitespace is stripped and an empty result
    becomes ``None`` -- "no note" and "a note made of spaces" are the same
    thing, and storing the second would make a no-op save look like an
    edit. Internal whitespace and line breaks are kept exactly as typed.
    """
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def parse_marking_submission(form_data, records):
    """Validate a marking POST against the **captured** roster.

    `records` is the list of presentation dicts for the session's records
    (each carrying ``public_id`` and ``student_name``), read from the
    database -- never from the request. The submitted field set is matched
    against it rather than the other way round, so a request that names an
    extra record, omits one, or renames one changes nothing: only the
    captured ``public_id``s are ever looked up, and a missing value is an
    ordinary validation error.

    Checks, all ordinary and all pre-lock:

    - every captured record has a ``status`` value present;
    - every ``status`` is one of the four known members -- an unknown one
      is refused here and would be refused again by the model validator
      and by ``ck_attendance_records_status``;
    - every captured record has a ``note`` field present, even when it is
      empty. A ``<textarea>`` always submits a value, so a **missing**
      note field means the request is not this form -- and treating it as
      "clear the note" would let a partial or hand-built POST silently
      erase what a colleague wrote. An **empty** note field is a perfectly
      ordinary "no note" and clears it deliberately;
    - every raw note is at most :data:`NOTE_MAX` characters.

    Returns a :class:`MarkingSubmission`. It never raises on bad input.
    """
    values, errors = {}, []
    for record in records:
        public_id = record["public_id"]
        name = record.get("student_name", "this student")
        status = form_data.get(STATUS_FIELD_PREFIX + public_id)
        raw_note = form_data.get(NOTE_FIELD_PREFIX + public_id)
        if status is None:
            errors.append(f"Choose an attendance status for {name}.")
            continue
        if status not in STATUS_VALUES:
            errors.append(f"“{name}” was given an attendance status that does not exist.")
            continue
        if raw_note is None:
            errors.append(f"The note field for {name} was missing from this form.")
            continue
        if len(raw_note) > NOTE_MAX:
            errors.append(
                f"The note for {name} is too long. Notes are limited to "
                f"{NOTE_MAX} characters."
            )
            continue
        values[public_id] = (status, _normalize_note(raw_note))
    return MarkingSubmission(values, errors)
