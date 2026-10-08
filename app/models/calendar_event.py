from app.models.code_types import CODE_COLLATION
import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import CalendarEventStatus
from app.models.submission_feedback import whole_second_utc

#: The ``title`` column's own width, declared once so the column, the
#: form's length validator and the plain-text normaliser cannot disagree
#: -- the same arrangement M04A uses for ``QUIZ_TITLE_MAX_LENGTH``, M08
#: for ``GRADE_CATEGORY_TITLE_MAX_LENGTH`` and M09 for
#: ``ANNOUNCEMENT_TITLE_MAX_LENGTH``.
CALENDAR_EVENT_TITLE_MAX_LENGTH = 150

#: The ``details`` column's own width. A center event is a line in a
#: calendar, not a document: M10 adds no attachment, no upload and no
#: rich text, so an event that needs more than this is describing
#: something that already has its own module.
CALENDAR_EVENT_DETAILS_MAX_LENGTH = 5000

#: The ``location`` column's own width, matching ``schedules.location``
#: and ``attendance_sessions.location`` -- the same kind of value, so the
#: same bound.
CALENDAR_EVENT_LOCATION_MAX_LENGTH = 255

#: The exact allowed ``status`` values, rendered once into the database
#: CHECK constraint below so the application-level ``@validates`` guard
#: and the schema can never drift apart -- the technique every other
#: closed-set column in this project uses.
_STATUS_VALUES = tuple(status.value for status in CalendarEventStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"

#: An event is **either** all-day (neither time) **or** timed (both, in
#: order). A half-configured window -- a start with no end -- is the one
#: shape that would leave "until when?" undecided while still rendering,
#: so the database refuses it outright.
_TIME_SHAPE_CHECK_SQL = (
    "(start_time IS NULL AND end_time IS NULL)"
    " OR (start_time IS NOT NULL AND end_time IS NOT NULL AND start_time < end_time)"
)

#: The complete state / timestamp truth table, as something the database
#: proves rather than something the application promises.
_CANCELLED_STATE_CHECK_SQL = (
    "(status = 'scheduled' AND cancelled_at IS NULL)"
    " OR (status = 'cancelled' AND cancelled_at IS NOT NULL)"
)


class CalendarEvent(db.Model):
    """One Administrator-owned, center-wide calendar event
    (Phase 4 / M10).

    **This is the only thing M10 stores.** The calendar itself is a *read
    model*: recurring class occurrences are still computed from
    ``schedules`` by ``app/services/schedule_occurrences.py``, and
    Assignment, Quiz and Listening dates are still read from those
    objects' own columns. Nothing is materialized, duplicated or cached
    into a table anywhere in M10 -- a second copy of a deadline and the
    deadline itself could disagree, and then two places would answer the
    same question differently. This table exists for the one kind of
    calendar entry that has no existing source object at all: something
    the center itself is doing on a particular day.

    **Scope is the whole center, and there is no audience field.** An
    event is visible to every active Student and every active Teacher,
    and is managed by Administrators. There is deliberately no
    ``course_id``, ``group_id``, ``level_id``, ``academic_term_id``,
    recipient list or audience selector, and no placeholder for one:
    targeting an event at a Course or a Group is a different object with
    different visibility rules, and nobody has approved one. A
    Researcher, and an unauthenticated visitor, reach no surface that
    returns one of these rows.

    **Time is local civil time, and only that.** ``event_date`` is a
    ``DATE`` and ``start_time`` / ``end_time`` are ``TIME`` values, all
    interpreted through ``APP_TIMEZONE`` exactly as ``schedules``'
    weekly wall-clock values are (the M08 decision). They are never
    converted to UTC, there is no timezone column, and only the audit
    timestamps (``created_at`` / ``updated_at`` / ``cancelled_at``) are
    UTC. An event that runs from 09:00 to 11:00 on a given day runs from
    09:00 to 11:00 in the center, whatever the server's clock thinks.

    **All-day or timed, never half of each.** Both times NULL means an
    all-day event; both set means a timed one, and then
    ``start_time < end_time`` on the same civil day -- overnight events
    are out of scope, exactly as overnight Schedule slots are.
    ``ck_calendar_events_time_shape`` is the final defense.

    **The lifecycle is one-way and has two states.** See
    :class:`~app.models.enums.CalendarEventStatus`. A ``scheduled`` event
    may be edited by an Administrator under stale-form protection; a
    ``cancelled`` event is **permanently immutable** -- no edit, no
    restore, no re-schedule, no duplicate and no hard delete, and no
    endpoint exists server-side for any of them. A cancelled event
    vanishes from every Student and Teacher calendar immediately and
    remains visible, clearly marked, only to Administrators. Correcting a
    cancelled event means creating a new one, which is what keeps the
    record of what was announced and then called off intact.

    **Plain text only.** ``title``, ``details`` and ``location`` are
    stored exactly as the Administrator typed them after
    whitespace/control-character normalisation, and every surface renders
    them through Jinja's autoescaping. No HTML is stored, none is
    rendered, and nothing in M10 is ever marked safe. There is no
    external link field, no attachment, no reply, no comment, no
    acknowledgement, no RSVP and no notification behaviour.

    **``created_by_id`` is immutable and is not an authorization fact.**
    It records which account created the event, for the management
    surfaces. It does **not** decide who may manage it: every active
    Administrator manages the center's calendar equally. A foreign key
    into ``users`` proves the row exists, never that it is still an
    Administrator's or still active, so every write path re-checks the
    *acting* account's role and status against the locked row.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1 and increases by exactly one per *meaningful* change -- a
    real edit, the cancellation -- so a signed form token can detect that
    the row moved under it, including an A -> B -> A round trip and two
    edits inside one whole second, neither of which a timestamp
    comparison could catch. An edit whose normalized title, details,
    date, times and location all equal the stored ones is a **no-op**:
    neither this column nor ``updated_at`` moves. No row is kept per
    version.

    Database invariants (final defense only):

    - ``public_id`` unique and NOT NULL -- the only identifier that ever
      appears in a URL or in rendered HTML;
    - ``ck_calendar_events_status_valid`` -- the closed set as a literal
      ``IN`` list rather than a MySQL ``ENUM`` column, so the SQLite test
      backend enforces it identically and adding a member stays a visible
      schema change;
    - ``ck_calendar_events_time_shape`` -- all-day or timed and ordered
      (see :data:`_TIME_SHAPE_CHECK_SQL`);
    - ``ck_calendar_events_cancelled_state`` -- ``cancelled_at`` is NULL
      exactly while the event is ``scheduled`` and set exactly while it
      is ``cancelled``;
    - ``ck_calendar_events_version_positive`` (``version > 0``).

    Conditions a CHECK cannot express are stated here rather than hidden:
    that ``created_by_id`` names an Administrator with an active account,
    that a cancelled event is never written again, and that a reader is
    an active Student or Teacher. All of them are enforced by the
    application against the **locked** rows.

    Indexes -- one per real query path, and no more:

    - ``ix_calendar_events_status_date_id``
      (``status``, ``event_date``, ``id``) -- the Student and Teacher
      read, which is an equality on ``status`` followed by the
      ``event_date BETWEEN`` range and the deterministic tie-break;
    - ``ix_calendar_events_date_id`` (``event_date``, ``id``) -- the
      Administrator calendar, which spans the same range but deliberately
      includes cancelled rows, so ``status`` must not lead;
    - ``ix_calendar_events_created_by_date_id``
      (``created_by_id``, ``event_date``, ``id``) -- the leftmost prefix
      InnoDB requires for the ``created_by_id`` foreign key. No route
      filters on the creator, and none is planned; the index is declared
      rather than left to InnoDB's implicit one so the schema states what
      exists.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier index in this project, this is a reasoned design
    pending an authorized real ``EXPLAIN``.

    **No ORM relationship is declared in either direction**, the same
    choice M02 / M03 / M06 / M07 / M08 / M09 made: every read goes
    through explicit, column-projected queries in
    ``app/services/calendar_queries.py`` and returns plain presentation
    dicts, so rendering a calendar can never trigger a lazy load or an
    ORM-driven authorization decision, and there is no ``cascade`` /
    ``delete-orphan`` configuration anywhere that could remove an event
    when an account is touched. The one foreign key is a plain reference
    with **no** ``ondelete`` and **no** ``onupdate``.
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_calendar_events_status_valid"),
        db.CheckConstraint(_TIME_SHAPE_CHECK_SQL, name="ck_calendar_events_time_shape"),
        db.CheckConstraint(
            _CANCELLED_STATE_CHECK_SQL, name="ck_calendar_events_cancelled_state"
        ),
        db.CheckConstraint("version > 0", name="ck_calendar_events_version_positive"),
        db.Index("ix_calendar_events_status_date_id", "status", "event_date", "id"),
        db.Index("ix_calendar_events_date_id", "event_date", "id"),
        db.Index(
            "ix_calendar_events_created_by_date_id",
            "created_by_id",
            "event_date",
            "id",
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    #: Which account created it. Set once, at creation, and never written
    #: again by any code path -- see the class docstring.
    created_by_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    title = db.Column(db.String(CALENDAR_EVENT_TITLE_MAX_LENGTH), nullable=False)
    #: Optional plain authored text. NULL means "nothing written"; the
    #: normaliser never stores an empty string, so "no details" has
    #: exactly one spelling.
    details = db.Column(db.String(CALENDAR_EVENT_DETAILS_MAX_LENGTH), nullable=True)
    #: The local civil date in ``APP_TIMEZONE``. Never a UTC instant.
    event_date = db.Column(db.Date, nullable=False)
    #: Both NULL for an all-day event; both set, ordered, for a timed one.
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    location = db.Column(db.String(CALENDAR_EVENT_LOCATION_MAX_LENGTH), nullable=True)
    status = db.Column(
        db.String(32, collation=CODE_COLLATION), nullable=False, default=CalendarEventStatus.SCHEDULED.value
    )
    #: NULL exactly while ``status`` is ``scheduled``; one authoritative
    #: whole-second naive-UTC moment once cancelled, and never changed
    #: again.
    cancelled_at = db.Column(db.DateTime, nullable=True)
    #: 1 on creation, +1 per meaningful change. See the class docstring --
    #: the stale-form signal, not a revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Both defaults are defense in depth only; the write path always
    #: supplies its own post-lock whole-second moment and uses the SAME
    #: value for both columns on creation. There is deliberately no
    #: ``onupdate`` hook: an implicit one would bypass that truncation and
    #: would fire on a no-op edit, which must leave every timestamp alone.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in set(_STATUS_VALUES):
            raise ValueError(f"Invalid calendar event status: {value}")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("CalendarEvent version must be a positive integer")
        return value

    @property
    def is_scheduled(self):
        return self.status == CalendarEventStatus.SCHEDULED.value

    @property
    def is_cancelled(self):
        return self.status == CalendarEventStatus.CANCELLED.value

    @property
    def is_all_day(self):
        """``True`` when this event carries neither time.

        Derived, never stored: a boolean column beside the two times could
        disagree with them, and ``ck_calendar_events_time_shape`` already
        makes "neither time" the one and only spelling of all-day.
        """
        return self.start_time is None and self.end_time is None
