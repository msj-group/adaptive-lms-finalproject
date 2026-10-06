import uuid

from app.extensions import db
from app.models.submission_feedback import whole_second_utc

#: The finite input boundary for the unbounded ``feedback_text`` Text
#: column (Phase 4 / M06). Mirrored by
#: ``app.blueprints.teacher.speaking_forms.SpeakingFeedbackForm.FEEDBACK_MAX``
#: -- the form is what actually rejects an oversized request; this
#: constant is declared beside the column so the two can be read together
#: and cannot drift silently. Same arrangement, and the same boundary, as
#: M03's ``FEEDBACK_MAX_LENGTH``.
SPEAKING_FEEDBACK_MAX_LENGTH = 10000


class SpeakingFeedback(db.Model):
    """The **one** shared Teacher feedback record on one immutable
    Speaking submission (Phase 4 / M06).

    **Deliberately a separate table from ``submission_feedback``.** M03's
    row is keyed by ``submission_id`` -- a *text* Submission -- and its
    unique constraint, its queries and its signed token shape are all
    written against that. A Speaking recording is not a text Submission
    and has no row in ``submissions``, so reusing that table would have
    meant either a nullable double foreign key (two columns that can
    disagree about what a row is about) or a fabricated text Submission
    to hang it from. Neither is acceptable, and both would have loosened
    an accepted M03 invariant. What **is** reused is the design: the same
    one-row-per-object rule, the same latest-text-only policy, the same
    ``version`` concurrency signal, the same no-op rule, the same
    reviewer-integrity fail-closed rule and the same whole-second
    timestamps.

    **Feedback is text, not a grade.** There is deliberately no
    ``score`` / ``grade`` / ``max_points`` / ``passed`` column, no review
    ``status`` enum, no publication state, no pronunciation or fluency
    rubric, no ``attempt``, and no historical-version table. Nothing here
    means "graded", "passed", "completed" or "officially approved" -- it
    means a Teacher listened and wrote a comment. Grades are an undecided
    module and no placeholder is left for them.

    **Ownership is the Speaking submission, and nothing else.** The
    activity, Assignment, Student, Group, Course, Level and AcademicTerm
    are all reachable through
    ``SpeakingSubmission -> SpeakingActivity -> Assignment -> Group ->
    ...``, so none of them is duplicated here.

    **One row per submission, and only the latest text.**
    ``uq_speaking_feedback_submission`` is the final defense behind the
    write path's post-lock check. A revision overwrites ``feedback_text``
    in place; earlier wordings are **not** retained anywhere, which the
    Teacher UI states plainly before a save.

    **``reviewer_id`` is the last editor, not the author.** Every actively
    assigned co-teacher of the Group is an equal collaborator on the same
    record, exactly as they are on the activity itself, so the column is
    reassigned on every meaningful edit. A foreign key into ``users``
    proves the row exists, never that it is a Teacher's -- every
    presentation read re-checks ``role`` and fails closed on a
    role-inconsistent row rather than silently treating it as absent (see
    ``app/services/speaking_queries.py``), and the write path refuses to
    overwrite such a row.

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

    **Time.** Both timestamps are naive UTC truncated to whole seconds. On
    creation they are the **same** server-generated moment; on an edit
    only ``updated_at`` moves, and ``created_at``, ``id``, ``public_id``
    and ``speaking_submission_id`` are preserved. There is deliberately
    **no** ``onupdate``: the write path samples one authoritative moment
    *after* its locks and assigns it explicitly.

    **No ORM relationship is declared in either direction**, the same
    choice M02/M03 made: every read goes through explicit joins in
    ``app/services/speaking_queries.py`` and returns plain presentation
    dicts, so rendering feedback can never trigger a lazy load, and there
    is no ``cascade`` / ``delete-orphan`` configuration anywhere. Both
    foreign keys are plain references with **no** ``ondelete``
    behaviour, and there is no delete endpoint at all.

    Database invariants (final defense only):

    - one feedback row per Speaking submission
      (``uq_speaking_feedback_submission``);
    - ``version > 0`` (``ck_speaking_feedback_version_positive``) -- a
      plain comparison CHECK, supported by both MySQL 8 and the SQLite
      test backend;
    - ``public_id`` unique and NOT NULL.

    Indexes -- two objects, each with a distinct justification:

    - ``uq_speaking_feedback_submission`` (``speaking_submission_id``) --
      the uniqueness invariant, and also the exact shape of every lookup
      in this milestone, all of which resolve feedback *for a known
      submission*. Because it is a single-column unique index it
      additionally gives that foreign key its required index.
    - ``ix_speaking_feedback_reviewer_id`` -- declared for the
      ``reviewer_id`` **foreign key**, not for a query shape. Nothing
      above leads with ``reviewer_id`` and InnoDB requires an index on a
      referencing column.

    **No MySQL execution plan has been measured for this table.**
    """

    __tablename__ = "speaking_feedback"
    __table_args__ = (
        db.UniqueConstraint(
            "speaking_submission_id", name="uq_speaking_feedback_submission"
        ),
        db.CheckConstraint("version > 0", name="ck_speaking_feedback_version_positive"),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    speaking_submission_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("speaking_submissions.id"),
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
