import uuid

from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class SpeakingActivity(db.Model):
    """The Speaking extension of exactly one Assignment (Phase 4 / M06).

    **A Speaking activity *is* an Assignment.** M06 deliberately does not
    introduce a parallel title, instructions, availability, publication,
    deadline or visibility system: a Speaking activity is backed by one
    row in ``assignments`` and reuses
    :class:`~app.models.assignment.Assignment` for Group ownership, the
    title, the instructions, ``opens_at`` / ``due_at``, the
    ``draft`` / ``published`` lifecycle, ``published_at``, the
    ``opens_at < due_at`` rule and the whole derived-state and visibility
    formula unchanged. What this table adds is exactly what an ordinary
    Assignment has no concept of: that the answer is a **recording** a
    Student makes in the browser rather than typed text.

    **The presence of this row is what makes an Assignment a Speaking
    activity.** There is no ``kind`` column on ``assignments`` and no
    discriminator anywhere else, because a nullable flag and this row
    could disagree, and then two places would answer the same question
    differently. The ordinary Assignment lists and routes exclude
    Assignments that have this extension; the Speaking routes require it
    and can never reach an ordinary Assignment. Every Assignment created
    before M06 therefore stays an ordinary Assignment **by construction**
    -- no backfill, no default, no migration of existing rows. This is
    the same arrangement M05 established between ``quizzes`` and
    ``listening_activities``.

    **Ownership is the Assignment, and nothing else.** Group, Course,
    Level and AcademicTerm are all reachable through
    ``activity.assignment.group`` and are not duplicated here -- the same
    single-source-of-truth reasoning applied to every row in this
    project. There is likewise no ``teacher_id`` / ``created_by``: every
    **active** assigned Teacher of the Assignment's Group is an equal
    collaborator, exactly as they already are on its Assignments,
    Quizzes and Listening activities.

    **One Assignment, one extension.** ``assignment_id`` is NOT NULL and
    ``UNIQUE``, so an Assignment can never carry two Speaking extensions
    and "is this a Speaking activity?" has exactly one answer. It is a
    plain reference with **no** ``ondelete`` behaviour, so no Assignment
    or Group lifecycle change can remove an activity, and nothing is ever
    hard-deleted -- M06 adds no delete endpoint at all.

    **The recording lives on the Student's submission, not here.** A
    Speaking *activity* holds no audio: it is the task. Each Student's
    single final recording is one
    :class:`~app.models.speaking_submission.SpeakingSubmission` row, and
    the Teacher never uploads audio on this surface. That is the whole
    difference from M05's Listening activity, where the Teacher supplies
    the recording and the Students answer questions about it.

    **There is deliberately no ``version`` column.** The authored content
    is the Assignment's -- title, instructions and the time window -- and
    the M01 signed edit snapshot already protects exactly those fields
    against a co-teacher overwrite. A second counter here could disagree
    with that snapshot, and the stale-form rejection would then have to
    decide which one to believe. There is likewise no ``status`` here:
    ``Assignment.status`` / ``Assignment.published_at`` are the single
    publication state.

    **Time.** Both timestamps are naive UTC truncated to whole seconds
    (:func:`~app.models.submission_feedback.whole_second_utc`, reused
    rather than re-implemented, because ``DATETIME`` on MySQL carries
    fractional precision 0 and *rounds* an excess fraction). On creation
    they are the **same** server-generated moment. There is deliberately
    **no** ``onupdate``: the write path samples one authoritative moment
    *after* its locks and assigns it explicitly, and an implicit hook
    would both bypass that truncation and fire on writes this milestone
    defines as no-ops.

    Database invariants (final defense only):

    - ``public_id`` unique and NOT NULL;
    - ``assignment_id`` unique and NOT NULL, no ``ondelete``;
    - ``creation_nonce`` unique and NOT NULL -- the final defense behind
      the duplicate-request protection, so a concurrent replay of one
      create request loses at the database rather than producing a second
      Assignment and extension row.

    **No index beyond those uniqueness constraints is declared.**
    ``assignment_id`` already has a usable unique index of its own, and
    the only reads in M06 are "the activity for this Assignment", "is
    this Assignment a Speaking activity?" and the Group-scoped list --
    which resolves through the existing ``assignments`` indexes on the
    parent and a join into this table. No speculative index is declared:
    there is no cross-Group listing and no counter.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier Part's indexes, this is a reasoned design pending an
    authorized real ``EXPLAIN``.

    **Relationships are for navigation, not for listing.**
    ``Assignment.speaking_activity`` exists so the inverse is declared;
    it is one-to-one, carries **no** ``cascade`` / ``delete-orphan``
    configuration, and is never iterated. Every list read goes through
    the bounded, column-projected queries in
    ``app/services/speaking_queries.py``.
    """

    __tablename__ = "speaking_activities"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    #: The one Assignment this extension turns into a Speaking activity.
    #: UNIQUE, so an Assignment can never carry two.
    assignment_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("assignments.id"),
        nullable=False,
        unique=True,
    )
    #: The random per-request value carried by the signed create token.
    #: UNIQUE, so an ordinary or concurrent replay of the create request
    #: resolves to the single already-created activity instead of
    #: inserting a duplicate -- the same mechanism, and the same column
    #: name, M12 uses for ``materials.creation_nonce`` and M05 for
    #: ``listening_activities.creation_nonce``.
    creation_nonce = db.Column(db.String(64), nullable=False, unique=True)
    #: Both defaults are defense in depth only; the write path always
    #: supplies its own post-lock whole-second moment, and uses the SAME
    #: value for both columns on creation.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    assignment = db.relationship("Assignment", back_populates="speaking_activity")
