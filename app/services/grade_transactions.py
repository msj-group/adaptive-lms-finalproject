"""Shared locking primitives and the atomic roster capture for the
Gradebook aggregate (Phase 4 / M08).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering, exactly like ``app/services/group_transactions.py``,
``app/services/quiz_transactions.py`` and
``app/services/attendance_transactions.py``. Route-level 404 / redirect /
flash handling belongs in the Blueprint modules that call this.

**One chain, one order, every M08 write.** There is a single lock
function here rather than one per route, deliberately: the order is the
thing that must not vary, and several near-identical functions would be
several chances for it to::

    AcademicTerm -> Level -> Course      (lock_academic_hierarchy, which
                                          owns the one deliberate reset)
    -> Group
    -> acting Teacher User
    -> GroupTeacherAssignment
    -> every GradeCategory of this Group, ascending internal id
    -> GradeItem rows, ascending internal id
    -> linked source row (Assignment / Quiz / SpeakingActivity)
    -> Student User rows, ascending internal id
    -> Enrollment rows, ascending internal id
    -> GradeRecord rows, ascending internal id

A caller passes only the id sets its operation needs, so a category edit
simply stops after the category rows while a release runs the whole
sequence -- but both request the rows they share in the same relative
order. That is what keeps a co-teacher editing weights, a co-teacher
entering scores and a co-teacher releasing an item from deadlocking
against each other.

Ascending internal id at every level, never display order and never the
order a form submitted things in: the project-wide rule, and it matters
especially here because the score sheet is ordered by Student *name*,
which an administrator can change between two requests.

The Group lock is the same one every Group-affecting mutation in this
project already takes, so a gradebook write serializes against a Group
retarget, an Enrollment change and a teacher-assignment change rather
than racing them. That is exactly what makes the roster snapshot
trustworthy: the membership cannot move while it is being captured.

**Why every category is locked on every write, and why their ids are
discovered *inside* the transaction.** "This Group's categories total at
most 10,000 basis points" and "they total exactly 10,000" are statements
about a *set* of rows, and no CHECK can express either. Two things are
therefore required, and the second is easy to get wrong:

1. the rows must be locked, so a concurrent edit of one of them cannot
   land between the sum and the write; and
2. **the set itself must be read after the Group lock**, not from a
   pre-lock preview. A preview taken before the Group lock cannot contain
   a category a competing request committed in the meantime -- so locking
   "the ids the preview found" would lock, sum, and approve a set that is
   already out of date, and two co-teachers each adding a 60% category
   would both succeed.

:func:`lock_gradebook_chain` therefore re-reads the Group's category ids
itself, after the Group row is locked, and locks each of them. The read
is a plain ``SELECT`` issued after a locking read in a transaction the
hierarchy lock has just reset, so under MySQL/InnoDB REPEATABLE READ it
establishes its consistent-read view at that moment and sees whatever the
transaction that held the Group lock before it committed -- which is the
whole reason the Group lock is taken first.

Locking every category on every write (rather than only the ones a
particular route reads) is a deliberate simplification: a Group's
categories are a small operational set, uniformity removes an entire
class of ordering mistake, and every M08 write already had to lock the
Group they hang off.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so this code runs correctly in tests
without actually locking anything. Tests can assert the *requested* lock
set and order (structural); they prove nothing about real InnoDB
blocking.
"""

from app.extensions import db
from app.models import (
    Assignment,
    Enrollment,
    EnrollmentStatus,
    GradeCategory,
    GradeItem,
    GradeRecord,
    GroupTeacherAssignment,
    Quiz,
    SpeakingActivity,
    User,
    UserRole,
    UserStatus,
)
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction

_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: Which table holds the source of each linked kind. Declared once so the
#: lock step and the ownership recheck can never disagree about what a
#: ``quiz`` source is.
_SOURCE_MODELS = {
    "assignment": Assignment,
    "quiz": Quiz,
    "speaking": SpeakingActivity,
}


class GradebookLocks:
    """The rows one M08 lock chain returned.

    Any attribute may be ``None`` (or hold a ``None`` value): the caller
    must treat that as a business / authorization rejection, roll back,
    and 404 or redirect. It must never "keep going past" one.
    """

    __slots__ = (
        "hierarchy",
        "group",
        "teacher",
        "teacher_assignment",
        "categories",
        "items",
        "source",
        "students",
        "enrollments",
        "records",
    )

    def __init__(
        self,
        hierarchy,
        group,
        teacher,
        teacher_assignment,
        categories=None,
        items=None,
        source=None,
        students=None,
        enrollments=None,
        records=None,
    ):
        self.hierarchy = hierarchy
        self.group = group
        self.teacher = teacher
        self.teacher_assignment = teacher_assignment
        self.categories = categories or {}
        self.items = items or {}
        self.source = source
        self.students = students or {}
        self.enrollments = enrollments or {}
        self.records = records or {}

    def category(self, category_id):
        return self.categories.get(category_id)

    def item(self, item_id):
        return self.items.get(item_id)

    def category_rows(self):
        """Every locked category row, ascending internal id, with any
        vanished one dropped."""
        return [
            row for _, row in sorted(self.categories.items()) if row is not None
        ]

    def weights(self, exclude_category_id=None):
        """The locked categories' weights, in basis points.

        This -- never a pre-lock preview -- is what the "at most 100%"
        and "exactly 100%" rules must be evaluated against.
        `exclude_category_id` drops the category being edited, so it
        cannot count against its own replacement weight.
        """
        return [
            row.weight_basis_points
            for category_id, row in sorted(self.categories.items())
            if row is not None and category_id != exclude_category_id
        ]

    def category_state(self):
        """``[[public_id, version], ...]`` for the locked categories,
        sorted by ``public_id`` -- the canonical bound state a signed
        gradebook-configuration token carries.

        Sorted by identifier rather than by the page's display order on
        purpose: the gradebook lists categories by *title*, and a
        co-teacher renaming one between the GET and the POST would
        otherwise reorder the list and make an untouched form look stale.
        Sorting by ``public_id`` binds the *set and its versions*, which
        is what actually matters, and nothing about presentation.

        Because the set is discovered inside the locked transaction, a
        category a competing request created in the meantime **is** in
        here -- which is exactly what makes the stale check able to catch
        it.
        """
        return sorted(
            [row.public_id, row.version] for row in self.category_rows()
        )


def _lock_prefix(group_public_id, term_id, level_id, course_id, teacher_id):
    """The shared prefix every M08 chain takes, in one open transaction,
    with the single deliberate reset owned by
    :func:`~app.services.academic_hierarchy_transactions.lock_academic_hierarchy`.

    Returns ``(hierarchy, group, teacher, teacher_assignment)``.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    teacher = User.query.filter_by(id=teacher_id).with_for_update().first()
    teacher_assignment = None
    if group is not None:
        teacher_assignment = (
            GroupTeacherAssignment.query.filter_by(
                group_id=group.id, teacher_id=teacher_id
            )
            .with_for_update()
            .first()
        )
    return hierarchy, group, teacher, teacher_assignment


def _lock_rows(model, row_ids):
    """Lock the given rows of one table ``FOR UPDATE``, ascending
    internal id, and return ``{id: row_or_None}``."""
    rows = {}
    for row_id in sorted({rid for rid in row_ids if rid is not None}):
        rows[row_id] = model.query.filter_by(id=row_id).with_for_update().first()
    return rows


def _lock_group_categories(group_id):
    """Discover this Group's category ids **inside the already-locked
    transaction**, then lock each row ascending.

    The two steps are both necessary and the order of them is the point:
    see the module docstring. The discovery read deliberately projects
    only ``id`` -- the values are read again from the locked rows, never
    carried over from here.
    """
    if group_id is None:
        return {}
    ids = [
        row.id
        for row in db.session.query(GradeCategory.id)
        .filter(GradeCategory.group_id == group_id)
        .order_by(GradeCategory.id.asc())
        .all()
    ]
    return _lock_rows(GradeCategory, ids)


def _lock_source(source_kind, source_id):
    """Lock the one linked source row, if this item has one.

    Returns ``None`` both for an unlinked (``activity`` / ``manual``)
    item and for a source that has vanished; the caller must distinguish
    the two from `source_kind`, never from this value alone.
    """
    model = _SOURCE_MODELS.get(source_kind)
    if model is None or source_id is None:
        return None
    return model.query.filter_by(id=source_id).with_for_update().first()


def lock_gradebook_chain(
    group_public_id,
    term_id,
    level_id,
    course_id,
    teacher_id,
    item_ids=(),
    source_kind=None,
    source_id=None,
    student_ids=(),
    enrollment_ids=(),
    record_ids=(),
):
    """Take the M08 lock order in one open transaction, stopping wherever
    the caller's arguments stop.

    Every call locks the academic prefix, the Group, the acting Teacher,
    the teacher assignment and **every one of the Group's categories**;
    everything after that is opt-in:

    * **category create / edit** passes nothing more -- the categories
      are always locked, which is the whole rule it needs;
    * **item creation** passes the linked source plus the previewed
      eligible Students and Enrollments (there is no item and no record
      yet);
    * **item edit** passes the item and the new linked source;
    * **score save** passes the item, its captured Students and its
      captured records;
    * **release** passes the item, the linked source, and the captured
      Students and records.

    `student_ids` / `enrollment_ids` for a creation come from the
    non-locking preview
    :func:`app.services.attendance_queries.eligible_roster_rows`; they
    only decide *which* rows to lock. The caller must re-apply every
    eligibility condition to the locked rows
    (:func:`eligible_locked_student_ids`) and must treat any ``None`` --
    a vanished User, Enrollment, category, item or source, or a missing
    Group, Teacher or assignment -- as a rejection.

    Returns a :class:`GradebookLocks`.
    """
    hierarchy, group, teacher, teacher_assignment = _lock_prefix(
        group_public_id, term_id, level_id, course_id, teacher_id
    )
    categories = _lock_group_categories(None if group is None else group.id)
    items = _lock_rows(GradeItem, item_ids)
    source = _lock_source(source_kind, source_id)
    students = _lock_rows(User, student_ids)
    enrollments = _lock_rows(Enrollment, enrollment_ids)
    records = _lock_rows(GradeRecord, record_ids)
    return GradebookLocks(
        hierarchy,
        group,
        teacher,
        teacher_assignment,
        categories=categories,
        items=items,
        source=source,
        students=students,
        enrollments=enrollments,
        records=records,
    )


def category_has_released_item(category_id):
    """True when this category holds at least one **released** item --
    the condition that freezes its title and weight permanently.

    A single bounded ``EXISTS``-shaped read, called against the locked
    category before any title or weight is written, and again by the
    page that renders the "frozen" badge, so the badge and the rule
    cannot disagree.
    """
    return (
        db.session.query(GradeItem.id)
        .filter(GradeItem.category_id == category_id, GradeItem.released_at.isnot(None))
        .first()
        is not None
    )


def source_belongs_to_group(source_kind, source_row, group_id):
    """True when a locked source row really belongs to `group_id`.

    A cross-table condition no foreign key can express, so it is proved
    here against the **locked** row rather than assumed from the fact
    that a reference exists. Ownership runs directly through
    ``group_id`` for an Assignment and a Quiz, and through the parent
    Assignment for a SpeakingActivity -- the extension row has no Group
    of its own.

    ``activity`` and ``manual`` have no source at all and are answered
    ``True`` here only when they really carry none; a row that somehow
    arrived with one is refused rather than quietly ignored.
    """
    if source_kind in ("activity", "manual"):
        return source_row is None
    if source_row is None:
        return False
    if source_kind == "assignment":
        return source_row.group_id == group_id
    if source_kind == "quiz":
        return source_row.group_id == group_id
    if source_kind == "speaking":
        parent = db.session.get(Assignment, source_row.assignment_id)
        return parent is not None and parent.group_id == group_id
    return False  # pragma: no cover -- the kind was validated before this


def source_is_published_row(source_kind, source_row):
    """``True`` / ``False`` for a locked source's publication state, or
    ``None`` when this kind has no source.

    ``None`` is not "no": an ``activity`` or ``manual`` item has nothing
    to publish and is releasable on its own, so the caller must
    distinguish "nothing to check" from "checked and it is a draft".

    A Speaking activity's publication lives on its parent Assignment --
    the extension row has no status of its own, exactly as M06 decided.
    """
    if source_kind in ("activity", "manual"):
        return None
    if source_row is None:
        return False
    if source_kind == "speaking":
        parent = db.session.get(Assignment, source_row.assignment_id)
        return parent is not None and parent.status == "published"
    return source_row.status == "published"


def eligible_locked_student_ids(locks, group_id):
    """The internal User ids that are **still** eligible according to the
    rows this chain actually locked, ascending.

    Re-applies, against locked current data, exactly the four conditions
    :func:`app.services.attendance_queries.eligible_roster_rows` applied
    to the preview: an ``active`` Enrollment, in this exact Group, whose
    User exists with role ``student`` and an ``active`` account. A row
    that stopped satisfying any of them between the preview and its own
    lock is simply dropped rather than silently captured -- and a row
    that *became* eligible in that window was never in the preview, so it
    is not captured either. Both directions are deliberate: the roster is
    whatever the locked rows say at the instant of capture, and the
    Teacher is shown the count that was actually written.

    Identical in shape and reasoning to M07's equivalent. It is
    re-implemented here rather than imported because the two milestones
    capture *different* aggregates and a future change to one roster must
    not silently change the other; the eligibility rule itself is still
    stated once, in ``eligible_roster_rows``, which both previews call.
    """
    eligible = []
    for enrollment in locks.enrollments.values():
        if enrollment is None:
            continue
        if enrollment.group_id != group_id or enrollment.status != _ENROLLMENT_ACTIVE:
            continue
        student = locks.students.get(enrollment.student_id)
        if student is None:
            continue
        if student.role != _STUDENT or student.status != _USER_ACTIVE:
            continue
        eligible.append(student.id)
    return sorted(set(eligible))


def create_item_with_roster(
    category_id,
    title,
    source_kind,
    max_points,
    source_columns,
    student_ids,
    moment,
):
    """Insert one GradeItem **and** its complete roster of GradeRecords
    in the caller's already-open, already-locked transaction, and return
    the item.

    `source_columns` is the ``{column_name: id}`` mapping the caller
    derived from the validated source kind -- empty for ``activity`` and
    ``manual``. It is applied as keyword arguments so an unlinked kind
    literally cannot set a link, rather than relying on the caller to
    remember to pass ``None`` three times.

    Every record is created **ungraded**: ``score`` NULL, ``comment``
    NULL, ``graded_by_id`` / ``graded_at`` NULL, ``version`` 1. A
    captured roster starts as a list of people to grade, not as a list of
    zeros -- a zero would be a claim that somebody scored nothing, which
    is a different statement and one nobody has made yet.

    Both timestamps on every row are the **same** authoritative post-lock
    whole-second `moment` the item itself carries, so a captured roster
    can never look as though its rows were written at different times.

    **Atomic by construction**: the item and every record are added to
    one transaction and the caller commits once. A failure leaves nothing
    behind -- never an item with a partial roster. Nothing here
    authorizes anything, validates the source, or commits; the caller has
    already proved all of that against the locked rows.
    """
    item = GradeItem(
        category_id=category_id,
        title=title,
        source_kind=source_kind,
        max_points=max_points,
        released_at=None,
        version=1,
        created_at=moment,
        updated_at=moment,
        **source_columns,
    )
    db.session.add(item)
    db.session.flush()
    for student_id in sorted(set(student_ids)):
        db.session.add(
            GradeRecord(
                grade_item_id=item.id,
                student_id=student_id,
                score=None,
                comment=None,
                graded_by_id=None,
                graded_at=None,
                version=1,
                created_at=moment,
                updated_at=moment,
            )
        )
    return item
