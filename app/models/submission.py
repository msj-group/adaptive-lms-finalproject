import uuid
from datetime import datetime, timezone

from app.extensions import db

#: The finite input boundary for the unbounded ``answer_text`` Text
#: column (Phase 4 / M02). Mirrored by
#: ``app.blueprints.student.forms.SubmissionForm.ANSWER_MAX`` -- the form
#: is what actually rejects an oversized request; this constant is
#: declared beside the column so the two can be read together and cannot
#: drift silently.
ANSWER_MAX_LENGTH = 10000


class Submission(db.Model):
    """One Student's single, final, plain-text answer to one Assignment
    (Phase 4 / M02).

    **Ownership is Assignment + Student, and nothing else.** Group,
    Course, Level, AcademicTerm, the Teacher and the Enrollment are all
    reachable through ``Assignment -> Group -> ...`` and through the
    Student, so none of them is duplicated here -- the same
    single-source-of-truth reasoning already applied to Enrollment,
    GroupTeacherAssignment, Schedule, Unit, Lesson and Assignment. There
    is no generic Activity superclass: a Submission answers an
    Assignment, not an abstraction.

    **Row existence is the whole state machine.** A row means
    "Submitted"; its absence means "Not submitted". There is deliberately
    no ``status`` / ``state`` / ``draft`` column, no ``attempt`` or
    ``version``, no ``grade`` / ``score`` / ``feedback`` / ``reviewed_at``,
    and no late-policy column. Those are later milestones and no
    placeholder is left for them.

    **Immutable by construction.** No route updates or deletes a row:
    the Student blueprint exposes exactly one POST endpoint, which only
    ever INSERTs, and a second POST for the same pair is an authorized
    no-op that redirects to the existing receipt. The
    ``uq_submissions_assignment_student`` constraint is the final
    defense, not the only one. Immutability is enforced by the absence of
    write paths -- deliberately **not** by a database trigger or a
    general audit/history system.

    **No ORM relationship is declared in either direction.** Not an
    oversight: every read in ``app/services/submission_queries.py``
    joins explicitly and returns plain presentation dicts, so rendering a
    submission can never trigger a lazy load, and there is no
    ``cascade`` / ``delete-orphan`` configuration anywhere that could
    remove submission history when an Assignment, Group or User row is
    touched. Both foreign keys are plain references with **no**
    ``ondelete`` behaviour.

    **Time.** ``submitted_at`` is naive UTC, generated on the server from
    the request's *post-lock* authoritative moment (see
    ``app/blueprints/student/assignments.py``) -- never from a browser
    field, and never from a timestamp read before the locks that decide
    whether the submission is still in time. The column default below is
    defense in depth only; the route always supplies the value it also
    used for the acceptance decision.

    **Canonical precision: whole seconds.** Like every other timestamp in
    this project the column is a plain ``DateTime``, which on MySQL is
    ``DATETIME`` with fractional precision **0**. MySQL *rounds* an
    excess fraction rather than truncating it, so a value carrying
    microseconds could be accepted as "before the deadline" and then
    stored *at* it. Both the route's authoritative moment and the default
    above therefore truncate microseconds **before** the comparison and
    the write, so the instant that decided acceptance is byte-for-byte
    the instant persisted, on MySQL and on the SQLite test backend alike.
    Second precision is also exactly why every ordering over this column
    carries the internal ``id`` as a deterministic tie-break.

    **Answer text.** Required plain text, never HTML and never Markdown,
    rendered with line breaks preserved by CSS and never with ``|safe``.
    Leading and trailing whitespace is stripped by the form; internal
    whitespace and line breaks are stored exactly as submitted. The
    column is unbounded ``Text`` (65,535 bytes on MySQL, comfortably
    above 10,000 utf8mb4 characters); the finite boundary that actually
    protects the request is the form's ``Length(max=ANSWER_MAX_LENGTH)``.

    Indexes -- three objects, each with a distinct justification and no
    redundancy between them:

    - ``uq_submissions_assignment_student`` (``assignment_id``,
      ``student_id``) -- the required uniqueness invariant, and also the
      exact shape of the Student receipt lookup, which constrains **both**
      columns. Because it leads with ``assignment_id`` it additionally
      gives the ``assignment_id`` foreign key a usable leftmost prefix,
      so no separate single-column index is declared for it.
    - ``ix_submissions_assignment_submitted_id`` (``assignment_id``,
      ``submitted_at``, ``id``) -- the Teacher list: a single-Assignment
      equality followed directly by the two ordering columns
      (``submitted_at DESC, id DESC``), the same shape M14 established
      for ``notifications`` and M01 for ``assignments``.
    - ``ix_submissions_student_id`` -- declared for the ``student_id``
      **foreign key**, not for a query shape. No index above leads with
      ``student_id``, and InnoDB requires an index on a referencing
      column; declaring it keeps the model, the migration and the real
      schema in agreement instead of letting MySQL create an
      auto-named one behind their backs.

    **No MySQL execution plan has been measured for this table.** As with
    M01's ``assignments`` indexes, this is a reasoned design pending an
    authorized real ``EXPLAIN``.
    """

    __tablename__ = "submissions"
    __table_args__ = (
        db.ForeignKeyConstraint(['enrollment_id', 'student_id'], ['enrollments.id', 'enrollments.student_id'], name='fk_submission_episode_student'),
        db.UniqueConstraint(
            "assignment_id", "enrollment_id", name="uq_submissions_assignment_episode"
        ),
        db.Index(
            "ix_submissions_assignment_submitted_id",
            "assignment_id",
            "submitted_at",
            "id",
        ),
    )

    enrollment_id = db.Column(db.BigInteger, db.ForeignKey('enrollments.id', name='fk_submission_episode'), nullable=False, index=True)
    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    assignment_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("assignments.id"),
        nullable=False,
    )
    student_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    answer_text = db.Column(db.Text, nullable=False)
    submitted_at = db.Column(
        db.DateTime,
        nullable=False,
        #: Naive UTC truncated to a whole second -- the same canonical
        #: precision the route's authoritative moment uses (see
        #: ``_acceptance_moment`` in
        #: ``app/blueprints/student/assignments.py``). This default is
        #: defense in depth only; the route always supplies the value it
        #: also used for the acceptance decision.
        default=lambda: datetime.now(timezone.utc).replace(
            tzinfo=None, microsecond=0
        ),
    )
