from datetime import datetime, timezone

from sqlalchemy import event, inspect
from sqlalchemy.orm import validates

from app.extensions import db

#: The only columns that may change after insertion: completion is set and
#: cleared by the Student completion transaction, and ``last_opened_at``
#: moves forward when the Student opens the Lesson page.
LESSON_PROGRESS_MUTABLE_COLUMNS = frozenset({"completed_at", "last_opened_at", "version"})


def progress_now():
    """The current moment as naive UTC truncated to the whole second -- the
    precision every lesson-progress timestamp column holds."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class LessonProgress(db.Model):
    """One Student's progress on one Lesson, through one Group
    (Phase 4 / M13).

    **Bound to the Group the Student is authorized through.** A Lesson
    already determines its Group (``lesson.unit.group``), so ``group_id`` is
    deliberately redundant: the Part requires progress to name the exact
    Group whose active Enrollment granted access. The database cannot prove
    ``lesson.unit.group_id == group_id`` -- ``lessons`` has no ``group_id``
    for a composite foreign key to reference -- so the completion
    transaction proves it against locked rows before every write, and every
    read joins ``Unit.group_id == LessonProgress.group_id``, so a row that
    ever disagreed would simply never be shown.

    **At most one row per Student, Group and Lesson.** The UNIQUE constraint
    is the final duplicate defense behind the application's own lookups.

    **Immutable identity, never deleted.** ``student_id``, ``group_id``,
    ``lesson_id`` and ``created_at`` never change after insertion and a row
    is never deleted; the mapper listeners below refuse both at flush time.
    A withdrawn Enrollment, a suspended account, an archived link or an
    unpublished Lesson removes *access* to the row, never the row itself.

    **``version`` counts completion changes, starting at 1.** It moves by
    exactly one each time ``completed_at`` is set or cleared -- never when
    ``last_opened_at`` moves -- so a completion form rendered against one
    state can be recognised as stale against another, while opening the same
    Lesson in a second tab never stales the first. A Student with no row is
    treated as version 1, not completed; a row first created by completion
    therefore starts at version 2.

    **No ORM relationship is declared in either direction**, deliberately --
    so no cascade, no delete-orphan ownership and no template lazy load of a
    Student's or a Group's progress history can exist. Every read goes
    through the bounded queries in
    ``app/services/lesson_progress_queries.py``.

    The three foreign keys are plain: they prove the rows exist, never that
    the Student is a Student, that the account is active or that the
    Enrollment is current. Those are proved in SQL on every read and against
    locked rows before every completion write.

    Indexes, one per real query path:

    - ``uq_lesson_progress_student_group_lesson`` (``student_id``,
      ``group_id``, ``lesson_id``) -- the uniqueness invariant, the exact
      shape of the Student Lesson-page lookup, and the leftmost prefix
      InnoDB requires for the ``student_id`` foreign key;
    - ``ix_lesson_progress_student_opened_id`` (``student_id``,
      ``last_opened_at``, ``id``) -- Recently Opened and Continue Learning:
      one Student's rows newest-opened first;
    - ``ix_lesson_progress_group_student`` (``group_id``, ``student_id``)
      -- the Teacher Group view's per-Student aggregates; also the
      ``group_id`` foreign key;
    - ``ix_lesson_progress_lesson_group`` (``lesson_id``, ``group_id``) --
      Lesson/Group ownership lookups; also the ``lesson_id`` foreign key.
    """

    __tablename__ = "lesson_progress"
    __table_args__ = (
        db.UniqueConstraint(
            "student_id", "group_id", "lesson_id",
            name="uq_lesson_progress_student_group_lesson",
        ),
        db.CheckConstraint("version > 0", name="ck_lesson_progress_version_positive"),
        db.Index("ix_lesson_progress_student_opened_id", "student_id", "last_opened_at", "id"),
        db.Index("ix_lesson_progress_group_student", "group_id", "student_id"),
        db.Index("ix_lesson_progress_lesson_group", "lesson_id", "group_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    student_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    group_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("groups.id"),
        nullable=False,
    )
    lesson_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("lessons.id"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=progress_now)
    completed_at = db.Column(db.DateTime, nullable=True)
    last_opened_at = db.Column(db.DateTime, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)

    @validates("version")
    def validate_version(self, _key, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("A lesson progress version must be a positive integer.")
        return value


@event.listens_for(LessonProgress, "before_update")
def _progress_identity_is_immutable(_mapper, _connection, target):
    """Refuse a flush that would change anything but completion, the last
    opening or the version."""
    state = inspect(target)
    changed = sorted(
        attr.key
        for attr in state.mapper.column_attrs
        if attr.key not in LESSON_PROGRESS_MUTABLE_COLUMNS
        and state.attrs[attr.key].history.has_changes()
    )
    if changed:
        raise ValueError(
            "A lesson progress record's " + ", ".join(changed)
            + " cannot change after it is created."
        )


@event.listens_for(LessonProgress, "before_delete")
def _progress_is_never_deleted(_mapper, _connection, _target):
    raise ValueError("A lesson progress record is never deleted.")
