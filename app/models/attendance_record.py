from app.models.code_types import CODE_COLLATION
import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AttendanceStatus
from app.models.submission_feedback import whole_second_utc

#: The finite input boundary for the unbounded ``note`` Text column
#: (Phase 4 / M07). Mirrored by
#: ``app.blueprints.teacher.attendance_forms.NOTE_MAX`` -- the request
#: boundary is what actually rejects an oversized note; this constant is
#: declared beside the column so the two can be read together and cannot
#: drift silently. Same arrangement as M02's ``ANSWER_MAX_LENGTH`` and
#: M03's ``FEEDBACK_MAX_LENGTH``.
ATTENDANCE_NOTE_MAX_LENGTH = 1000

#: The value every captured record starts at. Declared here, once, so the
#: column default, the roster-capture insert and the CHECK can never
#: disagree about what "not yet marked" means -- and so that it is
#: visibly ``absent`` rather than a silent "present until proven
#: otherwise".
DEFAULT_ATTENDANCE_STATUS = AttendanceStatus.ABSENT.value

_STATUS_VALUES = tuple(status.value for status in AttendanceStatus)


class AttendanceRecord(db.Model):
    """One captured Student's mark for one
    :class:`~app.models.attendance_session.AttendanceSession`
    (Phase 4 / M07).

    **The roster is captured once, at session creation, and then frozen.**
    Every Student who was an eligible active member of the Group at that
    moment -- active Enrollment, ``student`` role, active account, exactly
    this Group -- gets one row, inserted in the same transaction as the
    session itself, with status ``absent``. After that the *set* of rows
    never changes: a Student who enrolls later gets **no** row on an
    existing session, and a Student who withdraws, is suspended, is moved,
    or whose Group or Schedule is archived **keeps** theirs. That is the
    whole point -- an attendance record is a statement about who was
    expected at one meeting on one date, and re-deriving it later from
    today's membership would rewrite history. A Group with no eligible
    active Student therefore cannot open a session at all, rather than
    producing an empty one.

    **Ownership is the session + the Student, and nothing else.** Group,
    Schedule, Course, Level, AcademicTerm and the Teacher are all
    reachable through ``AttendanceSession -> Group -> ...``, so none of
    them is duplicated here.

    ``student_id`` is a plain foreign key into the shared ``users`` table.
    As everywhere else in this project, that proves the row **exists** --
    never that it is still a Student, still active, or still enrolled. The
    eligibility rules above are enforced by the application at capture
    time against the **locked** rows, and every presentation read
    re-checks ``role`` rather than trusting the reference.

    **Immutable after finalization.** While the session is a draft an
    actively assigned Teacher may change ``status`` and ``note`` as often
    as they like; once ``AttendanceSession.finalized_at`` is set, no route
    writes either column again. There is **no hard delete, no Student
    self-edit, no automatic status, no GPS or biometric check-in and no
    late-minutes calculation** anywhere -- and no column for one.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1 and increases by exactly one per *meaningful* draft
    update -- a change to ``status``, to ``note``, or to both counts once.
    A save that leaves both identical is a no-op: neither this column nor
    ``updated_at`` moves, because re-saving the same mark is not a
    change. The signed draft token carries every captured record's
    ``public_id`` and ``version``, which is what turns a losing
    co-teacher race into an explicit "reload and review" rejection rather
    than a silent overwrite -- including an A -> B -> A round trip and two
    saves inside the same whole second, neither of which a timestamp
    comparison could catch.

    **``note`` is private to staff.** It is optional plain text, never
    HTML and never Markdown, rendered with line breaks preserved by CSS
    and never with ``|safe``. Teachers assigned to the Group and
    Administrators may read it; the Student it is about never does, on any
    route, in any page, token or URL. The column is unbounded ``Text``
    (65,535 bytes on MySQL, comfortably above 1,000 utf8mb4 characters);
    the finite boundary that actually protects the request is the
    1,000-character check applied at the request boundary
    (:data:`ATTENDANCE_NOTE_MAX_LENGTH`) to the **raw** submitted value,
    before stripping.

    Database invariants (final defense only):

    - ``uq_attendance_records_session_student`` -- exactly one record per
      session and Student. It is also the exact shape of every per-Student
      lookup within a session, and the leftmost prefix the
      ``attendance_session_id`` foreign key needs, so no separate
      single-column index is declared for it.
    - ``ck_attendance_records_status`` -- ``status`` must be one of the
      four :class:`~app.models.enums.AttendanceStatus` members. A CHECK
      over a literal ``IN`` list rather than a MySQL ``ENUM`` column, the
      same convention every other status column in this project uses, so
      the SQLite test backend enforces it identically.
    - ``ck_attendance_records_version_positive`` (``version > 0``).

    Indexes:

    - ``uq_attendance_records_session_student`` -- see above.
    - ``ix_attendance_records_student_id`` -- declared for the
      ``student_id`` **foreign key**, which InnoDB requires and nothing
      above leads with. It is also what the Student's own attendance
      summary leads with, that query being an equality on exactly this
      column joined to the session for ordering. Declaring it keeps the
      model, the migration and the real schema in agreement instead of
      letting MySQL create an auto-named one behind their backs.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier index in this project, this is a reasoned design pending
    an authorized real ``EXPLAIN``.

    **No ORM relationship is declared in either direction**, for the same
    reason as on ``AttendanceSession``: every read is an explicit join
    returning plain presentation dicts, and no ``cascade`` /
    ``delete-orphan`` configuration exists that could remove attendance
    history. Both foreign keys are plain references with **no**
    ``ondelete`` and **no** ``onupdate``.
    """

    __tablename__ = "attendance_records"
    __table_args__ = (
        db.ForeignKeyConstraint(['enrollment_id', 'student_id'], ['enrollments.id', 'enrollments.student_id'], name='fk_attendance_record_episode_student'),
        db.UniqueConstraint(
            "attendance_session_id",
            "student_id",
            name="uq_attendance_records_session_student",
        ),
        db.CheckConstraint(
            "status IN ('present', 'absent', 'late', 'excused')",
            name="ck_attendance_records_status",
        ),
        db.CheckConstraint("version > 0", name="ck_attendance_records_version_positive"),
    )

    enrollment_id = db.Column(db.BigInteger, db.ForeignKey('enrollments.id', name='fk_attendance_record_episode'), nullable=False, index=True)
    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    attendance_session_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("attendance_sessions.id"),
        nullable=False,
    )
    student_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    status = db.Column(
        db.String(32, collation=CODE_COLLATION), nullable=False, default=DEFAULT_ATTENDANCE_STATUS
    )
    note = db.Column(db.Text, nullable=True)
    #: 1 on creation, +1 per meaningful draft update. See the class
    #: docstring -- the stale-form signal, not a revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defense in depth only; the write path always supplies its own
    #: post-lock whole-second moment, and uses the SAME value for both
    #: columns on creation. Deliberately no ``onupdate`` hook -- a no-op
    #: save must leave both alone.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid attendance status: {value}")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Attendance record version must be a positive integer")
        return value
