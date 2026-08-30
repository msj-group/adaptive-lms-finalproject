"""Query-only academic-lifecycle helpers: does an academic entity
currently have an *active* descendant that a future archive guard must
care about?

Deliberately independent of Flask: no `request`, `flash`, `redirect`, or
template rendering here, and no route decorators -- plain functions over
the ORM, mirroring `app/services/course_integrity.py` and
`app/services/group_memberships.py`, so any Blueprint can import them
without pulling in route-level concerns.

**These helpers take no locks.** A read here reflects whatever snapshot
the caller's transaction already holds. An authoritative mutation caller
(a future archive/reactivation guard) MUST first hold the appropriate
parent lock -- `SELECT ... FOR UPDATE` on the entity it is about to
change, via `app/services/academic_term_transactions.py`,
`app/services/level_transactions.py`, or
`app/services/course_transactions.py` -- and then re-run the relevant
helper against that locked, current data before deciding. Calling a
helper without that lock is only acceptable for a non-authoritative,
friendly pre-lock preview whose result is re-verified after the lock.

Every helper is a scalar SQL `EXISTS` -- `SELECT EXISTS (SELECT ...
WHERE ...)` -- never a `COUNT`, never a Python loop over loaded rows,
never a descendant ORM row materialized, since only existence matters. A
missing or nonexistent id simply matches no descendant row and returns
False.
"""

from app.extensions import db
from app.models import AcademicStatus, Course, Group


def academic_term_has_active_group(academic_term_id):
    """True if any *active* Group directly references this AcademicTerm.

    For the future AcademicTerm archive guard: a Term may not be archived
    while an active Group still runs under it. Archived Groups do not
    count -- they are already closed. The authoritative caller must hold
    the AcademicTerm write lock before trusting this result (see the
    module docstring).
    """
    exists_clause = (
        db.session.query(Group.id)
        .filter(
            Group.academic_term_id == academic_term_id,
            Group.status == AcademicStatus.ACTIVE.value,
        )
        .exists()
    )
    return bool(db.session.query(exists_clause).scalar())


def course_has_active_group(course_id):
    """True if any *active* Group directly references this Course.

    For the future Course archive guard. Narrower than
    `course_integrity.course_has_group_reference`, which answers the
    Part 7B1 level-freeze question ("does *any* Group -- active or
    archived -- reference this Course"): here only active Groups block
    archival. The authoritative caller must hold the Course write lock
    before trusting this result (see the module docstring).
    """
    exists_clause = (
        db.session.query(Group.id)
        .filter(
            Group.course_id == course_id,
            Group.status == AcademicStatus.ACTIVE.value,
        )
        .exists()
    )
    return bool(db.session.query(exists_clause).scalar())


def level_has_active_course(level_id):
    """True if any *active* Course directly references this Level.

    One of the two conditions the future Level archive guard checks (see
    `level_has_active_group` for the other). The authoritative caller
    must hold the Level write lock before trusting this result (see the
    module docstring).
    """
    exists_clause = (
        db.session.query(Course.id)
        .filter(
            Course.level_id == level_id,
            Course.status == AcademicStatus.ACTIVE.value,
        )
        .exists()
    )
    return bool(db.session.query(exists_clause).scalar())


def level_has_active_group(level_id):
    """True if any *active* Group belongs to this Level through its
    Course (`Group -> Course -> Level`), **regardless of that Course's
    own status**.

    An active Group under an *archived* Course whose Level is this one is
    a legacy inconsistency -- it was possible before the guarded-lifecycle
    policy. It must still block archiving this Level: otherwise the
    Group's derived Level (`group.course.level`) would become archived
    while the Group is active, exactly the contradiction the guard exists
    to prevent. So this join filters only on `Course.level_id` and
    `Group.status`, never on `Course.status`.

    The second of the two conditions the future Level archive guard
    checks (with `level_has_active_course`). The authoritative caller
    must hold the Level write lock before trusting this result (see the
    module docstring).
    """
    exists_clause = (
        db.session.query(Group.id)
        .join(Course, Group.course_id == Course.id)
        .filter(
            Course.level_id == level_id,
            Group.status == AcademicStatus.ACTIVE.value,
        )
        .exists()
    )
    return bool(db.session.query(exists_clause).scalar())
