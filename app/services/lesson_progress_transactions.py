"""The authoritative completion transaction for Student lesson progress,
and the Lesson page's opening record (Phase 4 / M13).

Flask-independent: no ``abort``, ``flash``, ``redirect``, template or
logger. Each function returns a status constant and the route decides the
response.

**One lock chain, for complete and undo**::

    AcademicTerm -> Level -> Course -> Group
      -> acting Student User -> Enrollment
      -> Unit -> Lesson
      -> the Student's LessonProgress row for this Group and Lesson, if any

It is the shared hierarchy/Group prefix every Group-affecting write takes
(``lock_academic_hierarchy`` owns the one deliberate transaction reset),
then the acting user and their own relationship row exactly as the
discussion reply chain takes them, then the M11 ``Unit -> Lesson`` tail,
and last the one table no other writer locks. So a completion serializes
against membership changes and Group lifecycle or identity operations (the
Group row), an account suspension or role change (the User row), Unit
archive and Lesson publication (the Group, Unit and Lesson rows those
routes lock), and a second completion from the same Student in another tab
(the Group row first, then the progress row). No writer locks a Lesson
before an Enrollment or a progress row before anything, so the lock graph
gains no reverse path.

**Preview, then lock, then prove.** A non-locking preview
(:func:`~app.services.lesson_progress_queries.lock_target`) only names the
rows. After the locks, every condition -- the unmoved academic chain, every
link active, the acting account's role and status, the current Enrollment,
the Unit's Group, the Unit's status, the Lesson's Unit, its publication and
the progress row's binding -- is proved again from the locked rows, and a
failure rolls back without a write. A Group whose Course moved between the
preview and the locks is previewed once more (bounded by
:data:`MAX_ATTEMPTS`, and only when the new preview names different rows).

**Idempotent by state, stale by version.** Completing a completed Lesson
and undoing an incomplete one are authorized no-ops (:data:`ALREADY`)
whatever version the form carried: no row is created, ``completed_at`` is
not touched, and the version does not move -- so a double click or a
replayed form is harmless. Otherwise the locked version must equal the one
the form was rendered against, or nothing is written (:data:`STALE`).

**Opening a Lesson is recorded without the lock chain**
(:func:`record_open`). The Lesson page has already proved access in SQL for
this request, and the fact recorded is exactly that: the Student opened the
page while authorized. Taking nine row locks -- including the Group row
every classmate's page view would then queue behind -- for a timestamp that
authorizes nothing would be disproportionate, and it is unnecessary for
integrity: every reader re-applies current visibility, so a row can never
surface content that has since become unavailable, and the row's identity
cannot be invalidated by a race because no route moves a Lesson to another
Unit or a Unit to another Group. An opening never changes completion or the
version, and it is refreshed at most once per :data:`OPEN_REFRESH_SECONDS`.

**Nothing is written until everything is proved**, and every function here
commits or rolls back before returning, so no lock outlives it.

SQLite (the test backend) honours neither ``FOR UPDATE`` nor REPEATABLE
READ. Tests can assert the *requested* lock set and order; they prove
nothing about real InnoDB blocking.
"""

from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AcademicStatus,
    Enrollment,
    EnrollmentStatus,
    Lesson,
    LessonProgress,
    LessonStatus,
    Unit,
    User,
    UserRole,
    UserStatus,
)
from app.models.lesson_progress import progress_now
from app.services import lesson_progress_queries
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.lesson_progress_tokens import ACTION_COMPLETE, ACTION_UNDO

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_PUBLISHED = LessonStatus.PUBLISHED.value

#: Previews tried before a write is rejected. A retry happens only after
#: the locked chain proved the Group's academic parents moved, and only for
#: different rows.
MAX_ATTEMPTS = 2

#: An opening within this many seconds of the last recorded one changes
#: nothing: refreshing a Lesson page is a no-op rather than a write.
OPEN_REFRESH_SECONDS = 60
_OPEN_REFRESH = timedelta(seconds=OPEN_REFRESH_SECONDS)

CHANGED = "changed"
ALREADY = "already"
STALE = "stale"
UNAVAILABLE = "unavailable"
CONFLICT = "conflict"

RECORDED = "recorded"
SKIPPED = "skipped"


class ProgressLocks:
    """The rows one completion lock chain returned. Any value may be
    ``None``; every such case except ``progress`` is a rejection.
    ``__slots__`` so a mistyped attribute raises instead of reading
    ``None``."""

    __slots__ = ("hierarchy", "group", "actor", "enrollment", "unit", "lesson", "progress")

    def __init__(self, hierarchy, group, actor, enrollment, unit, lesson, progress):
        self.hierarchy = hierarchy
        self.group = group
        self.actor = actor
        self.enrollment = enrollment
        self.unit = unit
        self.lesson = lesson
        self.progress = progress


def lock_progress_chain(target, student_id):
    """Take the M13 lock order in one fresh transaction and return a
    :class:`ProgressLocks`.

    `target` is a :class:`~app.services.lesson_progress_queries.LockTarget`
    from a non-locking preview; it only names the rows to lock.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[target.term_id],
        level_ids=[target.level_id],
        course_ids=[target.course_id],
    )
    group = lock_group_in_open_transaction(target.group_public_id)
    actor = User.query.filter_by(id=student_id).with_for_update().first()
    enrollment = None
    if group is not None:
        enrollment = (
            Enrollment.query.filter_by(group_id=group.id, student_id=student_id, status="active")
            .with_for_update()
            .first()
        )
    unit = Unit.query.filter_by(id=target.unit_id).with_for_update().first()
    lesson = Lesson.query.filter_by(id=target.lesson_id).with_for_update().first()
    progress = None
    if group is not None:
        progress = (
            LessonProgress.query.filter_by(
                student_id=student_id, group_id=group.id, lesson_id=target.lesson_id, enrollment_id=enrollment.id if enrollment else None
            )
            .with_for_update()
            .first()
        )
    return ProgressLocks(hierarchy, group, actor, enrollment, unit, lesson, progress)


def access_proof_error(locks, target, student_id):
    """``None`` when the **locked** rows still prove that `student_id` may
    record progress on `target`'s Lesson through its Group, else a short
    internal reason (logged nowhere and never shown)."""
    group = locks.group
    term = locks.hierarchy.term(target.term_id)
    level = locks.hierarchy.level(target.level_id)
    course = locks.hierarchy.course(target.course_id)
    if group is None or term is None or level is None or course is None:
        return "missing"
    if (
        group.id != target.group_id
        or group.course_id != course.id
        or group.academic_term_id != term.id
        or course.level_id != level.id
    ):
        return "moved"
    if any(row.status != _ACTIVE for row in (term, level, course, group)):
        return "not_operational"
    actor = locks.actor
    if actor is None or actor.id != student_id or actor.role != _STUDENT:
        return "account"
    if actor.status != _USER_ACTIVE:
        return "account"
    enrollment = locks.enrollment
    if (
        enrollment is None
        or enrollment.group_id != group.id
        or enrollment.student_id != student_id
        or enrollment.status != _ENROLLMENT_ACTIVE
    ):
        return "enrollment"
    unit = locks.unit
    if unit is None or unit.id != target.unit_id or unit.group_id != group.id:
        return "unit"
    if unit.status != _ACTIVE:
        return "unit"
    lesson = locks.lesson
    if lesson is None or lesson.id != target.lesson_id or lesson.unit_id != unit.id:
        return "lesson"
    if lesson.status != _PUBLISHED:
        return "lesson"
    progress = locks.progress
    if progress is not None and (
        progress.student_id != student_id
        or progress.group_id != group.id
        or progress.lesson_id != lesson.id
    ):
        return "progress"
    return None


def _locked_access(student_id, group_public_id, unit_public_id, lesson_public_id):
    """Preview, lock and prove; return the :class:`ProgressLocks` with the
    transaction still open and every lock held, or ``None`` after rolling
    back."""
    previous = None
    for _ in range(MAX_ATTEMPTS):
        target = lesson_progress_queries.lock_target(
            group_public_id, unit_public_id, lesson_public_id
        )
        if target is None or target == previous:
            break
        previous = target
        locks = lock_progress_chain(target, student_id)
        error = access_proof_error(locks, target, student_id)
        if error is None:
            return locks
        db.session.rollback()
        if error != "moved":
            break
    db.session.rollback()
    return None


def set_completion(student_id, group_public_id, unit_public_id, lesson_public_id, action,
                   expected_version):
    """Mark the Lesson complete (``complete``) or clear that mark
    (``undo``) for `student_id`, only while the locked rows still grant
    access and the locked version is still `expected_version`.

    `expected_version` came from a verified completion token. Returns
    :data:`CHANGED`, :data:`ALREADY`, :data:`STALE`, :data:`UNAVAILABLE`
    or :data:`CONFLICT`; only :data:`CHANGED` wrote anything.
    """
    if action not in (ACTION_COMPLETE, ACTION_UNDO):
        return UNAVAILABLE
    locks = _locked_access(student_id, group_public_id, unit_public_id, lesson_public_id)
    if locks is None:
        return UNAVAILABLE

    progress = locks.progress
    completed = progress is not None and progress.completed_at is not None
    if (action == ACTION_COMPLETE) == completed:
        db.session.rollback()
        return ALREADY
    current_version = (
        progress.version if progress is not None else lesson_progress_queries.NO_PROGRESS_VERSION
    )
    if current_version != expected_version:
        db.session.rollback()
        return STALE

    now = progress_now()
    if progress is None:
        # Only ``complete`` reaches here: ``undo`` with no row is ALREADY.
        db.session.add(
            LessonProgress(
                student_id=student_id,
                group_id=locks.group.id,
                lesson_id=locks.lesson.id,
                created_at=now,
                completed_at=now,
                last_opened_at=None,
                version=lesson_progress_queries.NO_PROGRESS_VERSION + 1,
            )
        )
    else:
        progress.completed_at = now if action == ACTION_COMPLETE else None
        progress.version = progress.version + 1
    try:
        db.session.flush()
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT
    return CHANGED


def record_open(student_id, progress, now=None):
    """Record that `student_id` opened the Lesson `progress` describes.

    `progress` is the :class:`~app.services.lesson_progress_queries.LessonProgressRef`
    the Lesson page's own authorized query returned **in this request**;
    calling this for anything else is a programming error. See the module
    docstring for why no lock chain is taken.

    - An opening within :data:`OPEN_REFRESH_SECONDS` of the recorded one is
      :data:`SKIPPED` without issuing any statement.
    - A first opening inserts the row at version 1 with no completion.
    - A later opening locks only its progress row, then rechecks the refresh
      window. The normal mapper update preserves the immutable episode and
      identity guards; no bulk-history exception is needed.

    ``completed_at`` and ``version`` are never touched. A concurrent first
    opening that wins the UNIQUE constraint makes this one
    :data:`SKIPPED`. Any other database error propagates for the caller to
    contain; the session is left for the caller to roll back.
    """
    now = now or progress_now()
    if progress.last_opened_at is not None and now - progress.last_opened_at < _OPEN_REFRESH:
        return SKIPPED
    try:
        if progress.progress_id is None:
            db.session.add(
                LessonProgress(
                    student_id=student_id,
                    group_id=progress.group_id,
                    lesson_id=progress.lesson_id,
                    created_at=now,
                    completed_at=None,
                    last_opened_at=now,
                    version=lesson_progress_queries.NO_PROGRESS_VERSION,
                )
            )
            db.session.flush()
        else:
            row = LessonProgress.query.filter_by(
                id=progress.progress_id, student_id=student_id,
                group_id=progress.group_id, lesson_id=progress.lesson_id,
            ).populate_existing().with_for_update().first()
            if row is None or (row.last_opened_at is not None
                               and now - row.last_opened_at < _OPEN_REFRESH):
                db.session.rollback()
                return SKIPPED
            row.last_opened_at = now
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return SKIPPED
    return RECORDED
