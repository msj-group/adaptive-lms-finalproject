import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import StudentFeeAssignmentStatus
from app.models.submission_feedback import whole_second_utc

_STATUS_VALUES = tuple(status.value for status in StudentFeeAssignmentStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"

#: Who assigned the plan and when are always recorded together. Both columns
#: are also NOT NULL; the CHECK states the pairing explicitly so it survives
#: any later change to either column.
_ASSIGNMENT_PAIR_SQL = "assigned_at IS NOT NULL AND assigned_by_id IS NOT NULL"

#: Who cancelled the assignment and when are recorded together or not at all.
_CANCELLATION_PAIR_SQL = (
    "(cancelled_at IS NULL AND cancelled_by_id IS NULL)"
    " OR (cancelled_at IS NOT NULL AND cancelled_by_id IS NOT NULL)"
)

#: An assigned row carries no cancellation; a cancelled row always does.
_LIFECYCLE_STATE_SQL = (
    "(status = 'assigned' AND cancelled_at IS NULL)"
    " OR (status = 'cancelled' AND cancelled_at IS NOT NULL)"
)

_TIMESTAMPS_ORDERED_SQL = (
    "assigned_at >= created_at"
    " AND updated_at >= assigned_at"
    " AND (cancelled_at IS NULL"
    " OR (cancelled_at >= assigned_at AND updated_at >= cancelled_at))"
)


class StudentFeeAssignment(db.Model):
    """One fee plan assigned to one Student Enrollment (Phase 5 / M03).

    **The assignment belongs to the Enrollment, and only the Enrollment.**
    ``enrollment_id`` names the exact registration the fees apply to; the
    Student, Group, Course, Level and Academic Term are read through it and
    are deliberately **not** duplicated here, so the two can never disagree.
    A move to another Group is another Enrollment and needs its own
    assignment.

    **The fee plan supplies the financial definition.** ``fee_plan_id`` points
    at a plan that was active when it was assigned, and an activated plan's
    name, items and amounts are frozen forever (Phase 5 / M02), so the row
    stores no name, currency, item, amount, total or payment state. Invoice
    snapshots are Phase 5 / M04's; nothing here is an invoice.

    **Lifecycle.** See :class:`~app.models.enums.StudentFeeAssignmentStatus`.
    A row is created ``assigned`` and is immutable except for its single
    explicit ``assigned -> cancelled`` transition, which records
    ``cancelled_at`` / ``cancelled_by_id`` and increments ``version`` exactly
    once. Assigning a plan again -- the same plan included -- inserts a new
    row; a cancelled row is never reused. Withdrawing, reactivating or
    transferring an Enrollment, archiving a plan, or suspending an account
    changes no assignment.

    **At most one ``assigned`` row per Enrollment** is an application
    invariant, proved against locked rows while the Enrollment lock is held.
    MySQL has no portable partial unique index for "assigned rows only".

    **Nothing is ever physically deleted.** There is no delete route, no
    ``cascade`` and no ``ondelete``; every foreign key is a plain reference.

    **Attribution is not authorization.** A foreign key into ``users`` proves
    the account exists, never that it is still an active Administrator, so
    every write re-reads the *acting* account under its lock.

    Database invariants (final defense only):

    - ``public_id`` unique;
    - ``ck_student_fee_assignments_status_valid`` -- the closed set;
    - ``ck_student_fee_assignments_version_positive``;
    - ``ck_student_fee_assignments_assignment_pair`` and
      ``ck_student_fee_assignments_cancellation_pair``;
    - ``ck_student_fee_assignments_lifecycle_state``;
    - ``ck_student_fee_assignments_timestamps_ordered``.

    Indexes -- one per real lookup path:

    - ``ix_student_fee_assignments_enrollment_status_id`` (``enrollment_id``,
      ``status``, ``id``) -- the "is a plan assigned?" check at assignment,
      at withdrawal and on the pages; the id-only read that picks the rows to
      lock; the history page, which reads one Enrollment's prefix and orders
      its few rows by ``id``; and the ``enrollment_id`` foreign key;
    - ``ix_student_fee_assignments_fee_plan_id``,
      ``ix_student_fee_assignments_assigned_by_id`` and
      ``ix_student_fee_assignments_cancelled_by_id`` -- declared for the
      three foreign keys InnoDB requires an index for.

    **No MySQL execution plan has been measured for this table.** No ORM
    relationship is declared in either direction; every read is an explicit
    query in ``app/services/student_fee_assignment_queries.py``.
    """

    __tablename__ = "student_fee_assignments"
    __table_args__ = (
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_student_fee_assignments_status_valid"),
        db.CheckConstraint("version > 0", name="ck_student_fee_assignments_version_positive"),
        db.CheckConstraint(
            _ASSIGNMENT_PAIR_SQL, name="ck_student_fee_assignments_assignment_pair"
        ),
        db.CheckConstraint(
            _CANCELLATION_PAIR_SQL, name="ck_student_fee_assignments_cancellation_pair"
        ),
        db.CheckConstraint(
            _LIFECYCLE_STATE_SQL, name="ck_student_fee_assignments_lifecycle_state"
        ),
        db.CheckConstraint(
            _TIMESTAMPS_ORDERED_SQL, name="ck_student_fee_assignments_timestamps_ordered"
        ),
        db.Index(
            "ix_student_fee_assignments_enrollment_status_id", "enrollment_id", "status", "id"
        ),
        db.Index("ix_student_fee_assignments_fee_plan_id", "fee_plan_id"),
        db.Index("ix_student_fee_assignments_assigned_by_id", "assigned_by_id"),
        db.Index("ix_student_fee_assignments_cancelled_by_id", "cancelled_by_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    enrollment_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("enrollments.id"),
        nullable=False,
    )
    fee_plan_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("fee_plans.id"),
        nullable=False,
    )
    status = db.Column(
        db.String(32), nullable=False, default=StudentFeeAssignmentStatus.ASSIGNED.value
    )
    #: Set once, when the row is inserted.
    assigned_at = db.Column(db.DateTime, nullable=False)
    assigned_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    #: NULL exactly while ``assigned``; set once, at cancellation.
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancelled_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defaults are defense in depth only; every write supplies its own
    #: post-lock whole-second moment. Deliberately no ``onupdate`` hook.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid student fee assignment status: {value}")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("StudentFeeAssignment version must be a positive integer")
        return value

    @property
    def is_assigned(self):
        return self.status == StudentFeeAssignmentStatus.ASSIGNED.value

    @property
    def is_cancelled(self):
        return self.status == StudentFeeAssignmentStatus.CANCELLED.value
