import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class AttendanceSession(db.Model):
    """One Group meeting whose attendance a Teacher recorded
    (Phase 4 / M07).

    **A session is schedule-backed, and only schedule-backed.** It exists
    exactly when an actively assigned Teacher opened attendance for a real
    occurrence of one of the Group's own
    :class:`~app.models.schedule.Schedule` rows -- same weekday, inside
    that Schedule's inclusive effective date range, and not in the future
    in the center timezone. There is deliberately **no** unscheduled,
    make-up, ad hoc, bulk-generated or recurring auto-created session, and
    no second calendar system: the occurrence arithmetic is the M08/M09
    one in ``app/services/schedule_occurrences.py``, reused rather than
    reinvented.

    **Ownership is the Group, and the Schedule is the occurrence it came
    from.** Course, Level, AcademicTerm, the Teachers and the Students are
    all reachable through ``AttendanceSession -> Group -> ...``, so none of
    them is duplicated here -- the same single-source-of-truth reasoning
    already applied to Enrollment, GroupTeacherAssignment, Schedule, Unit,
    Assignment and Quiz. ``group_id`` is nonetheless stored **as well as**
    ``schedule_id`` because it is what every Group-scoped list and every
    nested ownership check reads. The two must always agree, and
    "this Schedule belongs to exactly this Group" is a cross-table
    condition a plain foreign key cannot express: it is therefore an
    application invariant, proved against the **locked** rows before the
    insert and re-proved on every nested read, never assumed.

    **``session_date``, ``start_time``, ``end_time`` and ``location`` are
    deliberate history, not a cache.** They are the local civil date and
    the Schedule's wall-clock values copied **once**, at creation, and no
    route ever rewrites them. A Schedule that is later edited, moved,
    relocated or archived must not retroactively change what a recorded
    meeting was: the attendance of 12 May must keep saying 12 May,
    18:00-20:00, Room 3 forever. This mirrors the project-wide rule that
    history is preserved rather than recomputed, and it is exactly why
    there is no ``ON DELETE`` behaviour on either foreign key.

    Timezone: ``session_date`` / ``start_time`` / ``end_time`` are **local
    civil** values interpreted through ``APP_TIMEZONE``, exactly like the
    Schedule they were copied from -- they are never converted to UTC, and
    there is no timezone column. Only ``finalized_at`` / ``created_at`` /
    ``updated_at`` are UTC, following the project convention.

    **Draft versus finalized is ``finalized_at``, and nothing else.**
    ``NULL`` means draft -- a Teacher may keep saving it; a value means
    finalized, and the session and every one of its records are frozen
    permanently. There is deliberately no ``status`` enum, no ``is_final``
    flag and no second state column that could disagree with the
    timestamp, and there is **no reopen, unlock, delete, archive, restore
    or duplicate route anywhere** -- immutability is enforced by the
    absence of write paths, not by a trigger or an audit system.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1 and increases by exactly one per *meaningful* draft save
    and once more at finalization, so a signed co-teacher form token can
    detect that the session changed under it -- including two saves
    landing inside the same whole second, which a timestamp comparison
    could not catch. A save that changes nothing is a no-op: neither this
    column nor ``updated_at`` moves. No row is kept per version.

    Database invariants (final defense only):

    - ``uq_attendance_sessions_schedule_date`` -- one attendance session
      per scheduled occurrence, so a double-submitted create, a reloaded
      confirmation or a genuinely concurrent second Teacher can never
      produce two sessions for the same meeting. The losing request loses
      at the database and is resolved to the existing session.
    - ``ck_attendance_sessions_version_positive`` (``version > 0``) and
      ``ck_attendance_sessions_time_order`` (``start_time < end_time``) --
      plain comparison CHECKs, supported by MySQL 8 and by the SQLite test
      backend alike. The time order is the same rule
      ``ck_schedules_time_order`` already enforces on the row these values
      were copied from; overnight slots are out of scope there and here.

    Indexes -- three objects, each with a distinct read shape and no
    redundancy between them:

    - ``uq_attendance_sessions_schedule_date`` (``schedule_id``,
      ``session_date``) -- the uniqueness invariant, the exact shape of
      the "does this occurrence already have a session?" lookup, and the
      leftmost prefix the ``schedule_id`` foreign key needs, so no
      separate single-column index is declared for it.
    - ``ix_attendance_sessions_group_date_id`` (``group_id``,
      ``session_date``, ``id``) -- the Teacher's Group-scoped list: a
      single-Group equality followed directly by the two ordering columns
      (``session_date DESC, id DESC``). It leads with ``group_id``, so it
      is also the index the ``group_id`` foreign key requires.
    - ``ix_attendance_sessions_date_id`` (``session_date``, ``id``) -- the
      Administrator's center-wide review list, which is ordered by exactly
      those two columns across every Group.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier index in this project, this is a reasoned design pending
    an authorized real ``EXPLAIN``.

    **No ORM relationship is declared in either direction.** Not an
    oversight, and the same choice M02 / M03 / M06 made: every read goes
    through explicit joins in ``app/services/attendance_queries.py`` and
    returns plain presentation dicts, so rendering a session can never
    trigger a lazy load or an ORM-driven authorization decision, and there
    is no ``cascade`` / ``delete-orphan`` configuration anywhere that
    could remove attendance history when a Schedule, a Group, an account
    or an academic ancestor is touched. Both foreign keys are plain
    references with **no** ``ondelete`` and **no** ``onupdate``.
    """

    __tablename__ = "attendance_sessions"
    __table_args__ = (
        db.UniqueConstraint(
            "schedule_id", "session_date", name="uq_attendance_sessions_schedule_date"
        ),
        db.CheckConstraint("version > 0", name="ck_attendance_sessions_version_positive"),
        db.CheckConstraint(
            "start_time < end_time", name="ck_attendance_sessions_time_order"
        ),
        db.Index(
            "ix_attendance_sessions_group_date_id", "group_id", "session_date", "id"
        ),
        db.Index("ix_attendance_sessions_date_id", "session_date", "id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    group_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("groups.id"),
        nullable=False,
    )
    schedule_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("schedules.id"),
        nullable=False,
    )
    #: The local civil date of the scheduled occurrence -- never derived
    #: again from the Schedule after creation.
    session_date = db.Column(db.Date, nullable=False)
    #: Immutable wall-clock snapshots copied from the Schedule at creation.
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    location = db.Column(db.String(255), nullable=True)
    #: 1 on creation, +1 per meaningful draft save, +1 once at
    #: finalization. See the class docstring -- the stale-form signal, not
    #: a revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: NULL while the session is a draft; one authoritative whole-second
    #: naive-UTC moment once finalized, and never changed again -- a
    #: finalization replay returns the stored value untouched.
    finalized_at = db.Column(db.DateTime, nullable=True)
    #: Defense in depth only; the write path always supplies its own
    #: post-lock whole-second moment and uses the SAME value for both
    #: columns on creation. There is deliberately no ``onupdate`` hook:
    #: an implicit one would bypass that truncation and would fire on a
    #: no-op save, which must leave every timestamp alone.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Attendance session version must be a positive integer")
        return value

    def is_finalized(self):
        """Whether this session is frozen.

        The one place the draft / finalized question is answered, so no
        caller can invent a second rule for it.
        """
        return self.finalized_at is not None
