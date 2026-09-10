import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import TranscriptVisibility
from app.models.submission_feedback import whole_second_utc

#: The finite input boundary for the unbounded ``transcript`` Text column
#: (Phase 4 / M05). Mirrored by
#: ``app.blueprints.teacher.listening_forms.ListeningContentForm.TRANSCRIPT_MAX``
#: -- the form is what actually rejects an oversized request; this constant
#: is declared beside the column so the two can be read together and cannot
#: drift silently. Same arrangement as M04A's
#: ``QUIZ_INSTRUCTIONS_MAX_LENGTH`` and M04B's
#: ``QUESTION_PROMPT_MAX_LENGTH``.
TRANSCRIPT_MAX_LENGTH = 20000

#: The same boundary for ``vocabulary_notes``. Vocabulary support is a
#: short list of words and glosses, not a second transcript, so its bound
#: is deliberately much smaller.
VOCABULARY_NOTES_MAX_LENGTH = 5000

#: The exact allowed ``transcript_visibility`` values, rendered once into
#: the database CHECK below so the application ``@validates`` guard and the
#: schema can never drift apart -- the same technique M01 uses for
#: ``assignments.status`` and M04D for ``quizzes.status``.
_VISIBILITY_VALUES = tuple(policy.value for policy in TranscriptVisibility)
_VISIBILITY_CHECK_SQL = "transcript_visibility IN (" + ", ".join(
    f"'{value}'" for value in _VISIBILITY_VALUES
) + ")"


class ListeningActivity(db.Model):
    """The Listening extension of exactly one Quiz (Phase 4 / M05).

    **A Listening activity *is* a Quiz.** M05 deliberately does not
    introduce a parallel question, option, attempt, answer, selection,
    grading, publication, deadline or locking system: a Listening activity
    is backed by one row in ``quizzes`` and reuses
    :class:`~app.models.quiz_question.QuizQuestion`,
    :class:`~app.models.question_option.QuestionOption`,
    :class:`~app.models.quiz_attempt.QuizAttempt`,
    :class:`~app.models.quiz_answer.QuizAnswer`,
    :class:`~app.models.quiz_answer.QuizAnswerSelection`, the M04D
    lifecycle columns, the exact-set grading rule, the attempt limit and
    the deadline arithmetic unchanged. What this table adds is exactly the
    four things a Listening activity has and an ordinary Quiz does not: a
    recording, a transcript, a transcript policy, and vocabulary support.

    **The presence of this row is what makes a Quiz a Listening
    activity.** There is no ``kind`` column on ``quizzes`` and no
    discriminator anywhere else, because a nullable flag and this row
    could disagree, and then two places would answer the same question
    differently. The ordinary Quiz lists and routes exclude Quizzes that
    have this extension; the Listening routes require it and can never
    reach an ordinary Quiz. Every Quiz created before M05 therefore stays
    an ordinary Quiz **by construction** -- no backfill, no default, no
    migration of existing rows.

    **Ownership is the Quiz, and nothing else.** Group, Course, Level and
    AcademicTerm are all reachable through ``activity.quiz.group`` and are
    not duplicated here -- the same single-source-of-truth reasoning
    applied to every row in this project. There is likewise no
    ``teacher_id`` / ``created_by``: every **active** assigned Teacher of
    the Quiz's Group is an equal collaborator, exactly as they already are
    on its Quizzes and Assignments.

    **One Quiz, one recording, both exclusive.** ``quiz_id`` and
    ``audio_file_id`` are each ``UNIQUE`` and NOT NULL:

    - a Quiz cannot carry two Listening extensions, so "is this a
      Listening activity?" has exactly one answer;
    - an :class:`~app.models.uploaded_file.UploadedFile` cannot back two
      activities, which mirrors ``materials.uploaded_file_id`` and keeps
      one physical file owned by exactly one object.

    Both are plain references with **no** ``ondelete`` behaviour, so no
    Quiz, Group or file lifecycle change can remove an activity, and
    nothing is ever hard-deleted.

    **The referenced upload must be an ``audio`` file.** That is a
    cross-table condition a CHECK cannot express, so it is enforced in the
    application -- against the *locked* rows on creation, and again on
    every publication check and every audio request. It is stated here
    rather than hidden: the foreign key proves the upload row exists,
    never that it is a recording, exactly as a foreign key into ``users``
    proves a row exists and never that it is still a Student.

    **The audio is immutable after creation.** M05 adds no replacement
    route and no hard delete: the bytes, the ``storage_key``, the
    ``sha256`` and this association are fixed from the moment the create
    transaction commits. A Teacher who attached the wrong recording
    creates another draft, which the Teacher UI says in as many words.
    Title, instructions, transcript, transcript policy, vocabulary notes
    and the Quiz settings all stay editable for exactly as long as the
    existing Quiz freezes permit -- publication makes the authored
    activity read-only, and the first attempt freezes it permanently.

    **``transcript`` and ``vocabulary_notes`` are plain authored text**,
    never HTML, never Markdown, and never rendered with ``|safe``. Both
    are unbounded ``Text`` columns that are NOT NULL with an empty-string
    default: "nothing authored" has exactly one spelling, so a NULL and an
    empty string can never come to mean two different things. The finite
    boundaries that actually protect a request are the form's
    :data:`TRANSCRIPT_MAX_LENGTH` / :data:`VOCABULARY_NOTES_MAX_LENGTH`
    length validators, applied to the **raw** value before trimming, so
    padding can never be used to slip a longer body past them.

    **``transcript_visibility`` governs Students only.** See
    :class:`~app.models.enums.TranscriptVisibility`. An empty transcript
    is legitimate under every policy, and the transcript is never placed
    in a URL, a signed token, a JavaScript value, a hidden form field or
    an audio response under any of them -- a policy decides whether a page
    *renders* it, and pages are the only place it ever appears.

    **Time.** Both timestamps are naive UTC truncated to whole seconds
    (:func:`~app.models.submission_feedback.whole_second_utc`, reused
    rather than re-implemented). On creation they are the **same**
    server-generated moment; on a meaningful edit only ``updated_at``
    moves. There is deliberately **no** ``onupdate``: the write path
    samples one authoritative moment *after* its locks and assigns it
    explicitly, and an implicit hook would both bypass that truncation and
    fire on writes this milestone defines as no-ops.

    **There is no ``version`` column here, on purpose.** ``Quiz.version``
    already represents the complete authored activity -- title,
    instructions, settings, questions, options and, since M05, the
    transcript, its policy and the vocabulary notes -- and every M05 edit
    increments it exactly once. A second counter could disagree with the
    first, and the signed stale-form tokens would then have to decide
    which one to believe.

    Database invariants (final defense only):

    - ``public_id`` unique and NOT NULL;
    - ``quiz_id`` unique and NOT NULL, no ``ondelete``;
    - ``audio_file_id`` unique and NOT NULL, no ``ondelete``;
    - ``creation_nonce`` unique and NOT NULL -- the final defense behind
      the duplicate-request protection, so a concurrent replay of one
      create request loses at the database rather than producing a second
      Quiz, extension row, UploadedFile, access-log entry and physical
      file;
    - ``transcript_visibility`` limited to the three members of
      :class:`~app.models.enums.TranscriptVisibility`
      (``ck_listening_activities_transcript_visibility_valid``).

    **No index beyond those uniqueness constraints is declared.** Both
    foreign keys already have a usable unique index of their own, and the
    only reads in M05 are "the activity for this Quiz", "is this Quiz a
    Listening activity?" and the Group-scoped list -- which resolves
    through ``ix_quizzes_group_created_id`` on the parent and a
    semi-join into this table. No speculative index is declared: there is
    no transcript search, no cross-Group listing and no counter.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier Part's indexes, this is a reasoned design pending an
    authorized real ``EXPLAIN``.

    **Relationships are for navigation, not for listing.**
    ``Quiz.listening_activity`` and ``UploadedFile.listening_activity``
    exist so the inverses are declared; both are one-to-one, carry **no**
    ``cascade`` / ``delete-orphan`` configuration, and are never iterated.
    Every list read goes through the bounded, column-projected queries in
    ``app/services/listening_queries.py``.
    """

    __tablename__ = "listening_activities"
    __table_args__ = (
        db.CheckConstraint(
            _VISIBILITY_CHECK_SQL,
            name="ck_listening_activities_transcript_visibility_valid",
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    #: The one Quiz this extension turns into a Listening activity.
    #: UNIQUE, so a Quiz can never carry two.
    quiz_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("quizzes.id"),
        nullable=False,
        unique=True,
    )
    #: The validated recording. UNIQUE, so one physical file backs exactly
    #: one activity. The `audio` category is an application invariant --
    #: see the class docstring.
    audio_file_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("uploaded_files.id"),
        nullable=False,
        unique=True,
    )
    #: Plain authored text. NOT NULL with an empty-string default: an
    #: unwritten transcript has exactly one spelling.
    transcript = db.Column(db.Text, nullable=False, default="")
    #: Who may read the transcript, and when. Never a Teacher restriction.
    transcript_visibility = db.Column(
        db.String(32), nullable=False, default=TranscriptVisibility.HIDDEN.value
    )
    #: Optional plain authored vocabulary support, shown to eligible
    #: Students before and during an attempt. Empty string means none.
    vocabulary_notes = db.Column(db.Text, nullable=False, default="")
    #: The random per-request value carried by the signed create token.
    #: UNIQUE, so an ordinary or concurrent replay of the create request
    #: resolves to the single already-created activity instead of
    #: inserting a duplicate -- the same mechanism, and the same column
    #: name, M12 uses for ``materials.creation_nonce``.
    creation_nonce = db.Column(db.String(64), nullable=False, unique=True)
    #: Both defaults are defense in depth only; the write path always
    #: supplies its own post-lock whole-second moment, and uses the SAME
    #: value for both columns on creation.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    quiz = db.relationship("Quiz", back_populates="listening_activity")
    audio_file = db.relationship("UploadedFile", back_populates="listening_activity")

    @validates("transcript_visibility")
    def validate_transcript_visibility(self, _key, value):
        if value not in set(_VISIBILITY_VALUES):
            raise ValueError(f"Invalid transcript visibility: {value}")
        return value
