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
    """One immutable final payload for an Assignment/enrollment episode.

    A payload is plain text XOR one private UploadedFile, selected by the
    Teacher's Assignment type. The owning episode and Student are protected
    by a composite FK; assignment/episode uniqueness enforces finality.
    There are no draft, edit, replacement or resubmission write paths.
    Accepted timestamps are post-lock, whole-second naive UTC. Feedback and
    released grading retain their separate existing ownership/lifecycles.
    No cascading delete or ORM collection is introduced.
    """

    __tablename__ = "submissions"
    __table_args__ = (
        db.CheckConstraint("(answer_text IS NOT NULL AND uploaded_file_id IS NULL) OR (answer_text IS NULL AND uploaded_file_id IS NOT NULL)", name="ck_submissions_single_payload"),
        db.UniqueConstraint("uploaded_file_id", name="uq_submissions_uploaded_file"),
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
    answer_text = db.Column(db.Text, nullable=True)
    uploaded_file_id = db.Column(db.BigInteger, db.ForeignKey("uploaded_files.id", name="fk_submissions_uploaded_file"), nullable=True)
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
