import uuid
from datetime import datetime, timezone

from app.extensions import db

#: The finite input boundary for the unbounded ``feedback_text`` Text
#: column (Phase 4 / M03). Mirrored by
#: ``app.blueprints.teacher.forms.SubmissionFeedbackForm.FEEDBACK_MAX`` --
#: the form is what actually rejects an oversized request; this constant
#: is declared beside the column so the two can be read together and
#: cannot drift silently. Same arrangement as M02's ``ANSWER_MAX_LENGTH``.
FEEDBACK_MAX_LENGTH = 10000


def whole_second_utc():
    """The canonical write moment for this table: naive UTC truncated to
    a whole second.

    ``created_at`` / ``updated_at`` are plain ``DateTime`` columns, which
    on MySQL are ``DATETIME`` with fractional precision **0**, and MySQL
    *rounds* an excess fraction rather than truncating it. Truncating in
    Python makes the value the route decided with and the value the
    database stores the same instant on every backend -- the same
    reasoning ``submissions.submitted_at`` already applies.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class SubmissionFeedback(db.Model):
    """The **one** shared Teacher feedback record on one immutable
    Submission (Phase 4 / M03).

    **Feedback is text, not a grade.** There is deliberately no
    ``score`` / ``grade`` / ``max_points`` / ``passed`` column, no review
    ``status`` enum, no publication state, no ``attempt``, and no
    historical-version table. Nothing here means "graded", "passed",
    "completed" or "officially approved" -- it means a Teacher wrote a
    comment. Grades are an undecided module and no placeholder is left
    for them.

    **Ownership is the Submission, and nothing else.** Assignment,
    Student, Group, Course, Level and AcademicTerm are all reachable
    through ``Submission -> Assignment -> Group -> ...``, so none of them
    is duplicated here -- the same single-source-of-truth reasoning
    already applied to Enrollment, GroupTeacherAssignment, Schedule,
    Unit, Lesson, Assignment and Submission.

    **One row per Submission, and only the latest text.**
    ``uq_submission_feedback_submission`` is the final defense behind the
    write path's post-lock check. A revision overwrites ``feedback_text``
    in place; earlier wordings are **not** retained anywhere, which is
    stated plainly in the Teacher UI before a save.

    **``reviewer_id`` is the last editor, not the author.** Every
    actively assigned co-teacher of the Group is an equal collaborator on
    the same record, exactly as they are on the Assignment itself, so the
    column is reassigned on every meaningful edit. It is what the Student
    receipt and the Teacher pages attribute the current text to. A
    foreign key into ``users`` proves the row exists, never that it is a
    Teacher's -- every presentation read re-checks ``role`` and fails
    closed on a role-inconsistent row rather than silently treating it as
    absent (see ``app/services/submission_feedback_queries.py``).

    **``version`` is the concurrency signal, not a history counter.** It
    starts at 1 and increases by exactly one per *meaningful* edit. It
    exists so a signed co-teacher form token can detect that the row
    changed under it -- including an A -> B -> A round trip that leaves
    the text identical to what a third form was opened against, and
    including two edits landing inside the same whole second, neither of
    which a timestamp comparison could catch. It is never displayed as a
    revision number and no row is kept per version.

    **A save with identical normalized text is a no-op.** ``version``,
    ``updated_at`` and ``reviewer_id`` are all left alone: re-saving
    unchanged text is not an edit, so it must not steal attribution from
    the Teacher who actually wrote it.

    **Time.** Both timestamps are naive UTC. On creation they are the
    **same** server-generated whole-second moment; on an edit only
    ``updated_at`` moves, and ``created_at``, ``id``, ``public_id`` and
    ``submission_id`` are preserved. There is deliberately **no**
    ``onupdate`` on ``updated_at``: the write path samples one
    authoritative moment *after* its locks and assigns it explicitly, and
    an implicit hook would both bypass that whole-second truncation and
    fire on writes this milestone does not want timestamped.

    **No ORM relationship is declared in either direction.** Not an
    oversight, and the same choice M02 made for ``Submission``: every
    read goes through explicit joins in
    ``app/services/submission_feedback_queries.py`` and returns plain
    presentation dicts, so rendering feedback can never trigger a lazy
    load, and there is no ``cascade`` / ``delete-orphan`` configuration
    anywhere that could remove -- or be removed with -- a Submission, an
    Assignment or a User. Both foreign keys are plain references with
    **no** ``ondelete`` behaviour. There is no delete endpoint at all in
    this milestone.

    **Feedback text.** Required plain text, never HTML and never
    Markdown, rendered with line breaks preserved by CSS and never with
    ``|safe``. Leading and trailing whitespace is stripped by the form;
    internal whitespace and line breaks are stored exactly as typed. The
    column is unbounded ``Text`` (65,535 bytes on MySQL, comfortably
    above 10,000 utf8mb4 characters); the finite boundary that actually
    protects the request is the form's ``Length(max=FEEDBACK_MAX_LENGTH)``,
    applied to the **raw** value before stripping.

    Database invariants (final defense only):

    - one feedback row per Submission
      (``uq_submission_feedback_submission``);
    - ``version > 0`` (``ck_submission_feedback_version_positive``) -- a
      plain comparison CHECK, supported by both MySQL 8 and the SQLite
      test backend.

    Indexes -- two objects, each with a distinct justification and no
    redundancy between them:

    - ``uq_submission_feedback_submission`` (``submission_id``) -- the
      required uniqueness invariant, and also the exact shape of every
      lookup in this milestone, all of which resolve feedback *for a
      known Submission*. Because it is a single-column unique index it
      additionally gives the ``submission_id`` foreign key its required
      index, so no separate one is declared.
    - ``ix_submission_feedback_reviewer_id`` -- declared for the
      ``reviewer_id`` **foreign key**, not for a query shape. Nothing
      above leads with ``reviewer_id`` and InnoDB requires an index on a
      referencing column; declaring it keeps the model, the migration and
      the real schema in agreement instead of letting MySQL create an
      auto-named one behind their backs.

    No speculative reporting index is declared: there is no
    feedback-by-Teacher page, no review queue and no aggregate counter in
    this milestone, so there is no read shape for one to serve.

    **No MySQL execution plan has been measured for this table.** As with
    M01's and M02's indexes, this is a reasoned design pending an
    authorized real ``EXPLAIN``.
    """

    __tablename__ = "submission_feedback"
    __table_args__ = (
        db.UniqueConstraint("submission_id", name="uq_submission_feedback_submission"),
        db.CheckConstraint("version > 0", name="ck_submission_feedback_version_positive"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    submission_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("submissions.id"),
        nullable=False,
    )
    reviewer_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    feedback_text = db.Column(db.Text, nullable=False)
    #: 1 on creation, +1 per meaningful edit. See the class docstring --
    #: this is the stale-form signal, not a revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Both defaults are defense in depth only; the write path always
    #: supplies its own post-lock whole-second moment, and uses the SAME
    #: value for both columns on creation.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
