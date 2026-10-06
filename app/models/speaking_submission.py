import uuid

from app.models.submission_feedback import whole_second_utc

from app.extensions import db


class SpeakingSubmission(db.Model):
    """One Student's single, final recording for one Speaking activity
    (Phase 4 / M06).

    **Ownership is the Speaking activity + the Student, and nothing
    else.** The Assignment, Group, Course, Level, AcademicTerm, the
    Teacher and the Enrollment are all reachable through
    ``SpeakingActivity -> Assignment -> Group -> ...`` and through the
    Student, so none of them is duplicated here -- the same
    single-source-of-truth reasoning M02 already applied to
    ``submissions``.

    **The answer is audio, and it is deliberately not squeezed into the
    text Submission.** ``submissions.answer_text`` is a NOT NULL text
    column for a typed answer; a recording is a validated file with a
    storage key, a byte size, a digest and an audit trail. Forcing one
    into the other would have meant either a fake answer string or a
    nullable column that means two different things. A Speaking answer is
    therefore its own row referencing its own
    :class:`~app.models.uploaded_file.UploadedFile`, and the M02 text
    submission path is left exactly as it was.

    **Row existence is the whole state machine.** A row means
    "Submitted"; its absence means "Not submitted". There is deliberately
    no ``status`` / ``state`` / ``draft`` column, no ``attempt`` or
    ``version``, no ``grade`` / ``score`` / ``passed`` / ``reviewed_at``,
    no ``duration_seconds`` and no late-policy column. Re-recording
    happens **entirely in the browser**, before anything is uploaded, so
    it leaves no history here at all: nothing reaches the server until
    the Student presses the final submit button.

    **Immutable by construction.** No route updates or deletes a row: the
    Student blueprint exposes exactly one POST endpoint, which only ever
    INSERTs, and a second POST for the same pair is an authorized no-op
    that redirects to the existing receipt without writing another file,
    ``UploadedFile``, access-log entry or submission.
    ``uq_speaking_submissions_activity_student`` is the final defense, not
    the only one. Immutability is enforced by the absence of write paths
    -- deliberately **not** by a database trigger or a general
    audit/history system.

    **The audio association is as immutable as the row.**
    ``audio_file_id`` is NOT NULL and **UNIQUE**: one physical recording
    backs exactly one submission, mirroring ``materials.uploaded_file_id``
    and ``listening_activities.audio_file_id``. There is no replacement
    route, so the bytes, the ``storage_key``, the ``sha256`` and this
    association are fixed from the moment the insert commits.

    **The referenced upload must be an ``audio`` file.** That is a
    cross-table condition a CHECK cannot express, so it is enforced in
    the application -- against the *locked* rows on creation, and again on
    **every** audio request. It is stated here rather than hidden: the
    foreign key proves the upload row exists, never that it is a
    recording, exactly as a foreign key into ``users`` proves a row exists
    and never that it is still a Student.

    **``creation_nonce`` is the duplicate-request defense.** It carries
    the random value of the signed submission token the recording page
    minted, so a double-pressed submit button, a reloaded POST or a
    genuinely concurrent replay resolves to the one existing receipt
    instead of storing a second recording. It is UNIQUE, which is what
    makes the losing request lose at the database rather than in a race.

    **Time.** ``submitted_at`` is naive UTC, generated on the server from
    the request's *post-lock* authoritative moment -- never from a browser
    field, and never from a timestamp read before the locks that decide
    whether the submission is still in time. Like every other timestamp
    in this project the column is a plain ``DateTime``, which on MySQL is
    ``DATETIME`` with fractional precision **0**, and MySQL *rounds* an
    excess fraction rather than truncating it; both the route's
    authoritative moment and the default below therefore truncate to a
    whole second **before** the comparison and the write, so the instant
    that decided acceptance is byte-for-byte the instant persisted.
    Second precision is also exactly why every ordering over this column
    carries the internal ``id`` as a deterministic tie-break.

    **No ORM relationship is declared in either direction.** Not an
    oversight, and the same choice M02 made for ``Submission``: every read
    in ``app/services/speaking_queries.py`` joins explicitly and returns
    plain presentation dicts, so rendering a submission can never trigger
    a lazy load, and there is no ``cascade`` / ``delete-orphan``
    configuration anywhere that could remove submission history when an
    activity, an upload or a User row is touched. All three foreign keys
    are plain references with **no** ``ondelete`` behaviour.

    Indexes -- three objects, each with a distinct justification and no
    redundancy between them:

    - ``uq_speaking_submissions_activity_student``
      (``speaking_activity_id``, ``student_id``) -- the required
      uniqueness invariant, and also the exact shape of the Student
      receipt lookup, which constrains **both** columns. Because it leads
      with ``speaking_activity_id`` it additionally gives that foreign key
      a usable leftmost prefix, so no separate single-column index is
      declared for it.
    - ``ix_speaking_submissions_activity_submitted_id``
      (``speaking_activity_id``, ``submitted_at``, ``id``) -- the Teacher
      list: a single-activity equality followed directly by the two
      ordering columns (``submitted_at DESC, id DESC``), the same shape
      M01 established for ``assignments`` and M02 for ``submissions``.
    - ``ix_speaking_submissions_student_id`` -- declared for the
      ``student_id`` **foreign key**, not for a query shape. No index
      above leads with ``student_id``, and InnoDB requires an index on a
      referencing column; declaring it keeps the model, the migration and
      the real schema in agreement instead of letting MySQL create an
      auto-named one behind their backs. ``audio_file_id`` and
      ``creation_nonce`` need none of their own: each is UNIQUE and
      therefore already indexed.

    **No MySQL execution plan has been measured for this table.** As with
    M01's and M02's indexes, this is a reasoned design pending an
    authorized real ``EXPLAIN``.
    """

    __tablename__ = "speaking_submissions"
    __table_args__ = (
        db.ForeignKeyConstraint(['enrollment_id', 'student_id'], ['enrollments.id', 'enrollments.student_id'], name='fk_speaking_submission_episode_student'),
        db.UniqueConstraint(
            "speaking_activity_id",
            "enrollment_id",
            name="uq_speaking_submissions_activity_episode",
        ),
        db.Index(
            "ix_speaking_submissions_activity_submitted_id",
            "speaking_activity_id",
            "submitted_at",
            "id",
        ),
    )

    enrollment_id = db.Column(db.BigInteger, db.ForeignKey('enrollments.id', name='fk_speaking_submission_episode'), nullable=False, index=True)
    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    speaking_activity_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("speaking_activities.id"),
        nullable=False,
    )
    student_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    #: The validated recording. UNIQUE, so one physical file backs exactly
    #: one submission. The ``audio`` category is an application invariant
    #: -- see the class docstring.
    audio_file_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("uploaded_files.id"),
        nullable=False,
        unique=True,
    )
    creation_nonce = db.Column(db.String(64), nullable=False, unique=True)
    submitted_at = db.Column(
        db.DateTime,
        nullable=False,
        #: Naive UTC truncated to a whole second -- the same canonical
        #: precision the route's authoritative moment uses. Defense in
        #: depth only; the route always supplies the value it also used
        #: for the acceptance decision.
        default=whole_second_utc,
    )
