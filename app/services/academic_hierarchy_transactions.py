"""Flask-independent deterministic ancestor locking for the guarded
academic lifecycle (Part M07C3).

The approved global lock order is:

    AcademicTerm -> Level -> Course -> Group
        -> User rows (ascending numeric id)
        -> relationship rows (ascending numeric id)

and within one entity type, unique numeric ids are locked in ascending
order. Several M07C3 mutation routes must lock a *set* of AcademicTerm,
Level and/or Course rows before touching the Group/Course they actually
mutate -- a Group retarget, for instance, has both a source and a target
AcademicTerm, Level and Course. This module owns that "lock this
ancestor set, in the one correct order" step so every route does it
identically. It delegates to the per-entity `*_in_open_transaction_by_id`
primitives, so the actual `SELECT ... FOR UPDATE` never drifts from them.

`lock_academic_hierarchy` performs the single deliberate transaction
reset (`db.session.rollback()`) and must be the *first* lock of the
request. Callers then add the Group / User / relationship locks in the
same still-open transaction and never reset again.
`lock_academic_hierarchy_in_open_transaction` is the no-reset variant for
the rare case where an earlier first-lock step already reset.

SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
REPEATABLE READ snapshot isolation, so this runs correctly in tests
without actually locking anything. That proves nothing about real
MySQL/InnoDB blocking -- tests can only assert the *requested* lock set
and its order (structural).
"""

from app.extensions import db
from app.services.academic_term_transactions import lock_academic_term_in_open_transaction_by_id
from app.services.course_transactions import lock_course_in_open_transaction_by_id
from app.services.level_transactions import lock_level_in_open_transaction_by_id


class HierarchyLocks:
    """The rows returned by an ancestor-set lock, keyed by numeric id.

    A value is the locked row, or ``None`` if that id no longer names a
    row -- callers must treat ``None`` as a business-rule rejection
    (the ancestor vanished), never as "keep going".
    """

    __slots__ = ("terms", "levels", "courses")

    def __init__(self, terms, levels, courses):
        self.terms = terms
        self.levels = levels
        self.courses = courses

    def term(self, term_id):
        return self.terms.get(term_id)

    def level(self, level_id):
        return self.levels.get(level_id)

    def course(self, course_id):
        return self.courses.get(course_id)


def lock_academic_hierarchy_in_open_transaction(term_ids=(), level_ids=(), course_ids=()):
    """Lock the given AcademicTerm/Level/Course rows `FOR UPDATE` in
    entity-type order (all Terms, then all Levels, then all Courses),
    ascending unique numeric id within each type. `None` ids are
    dropped. Does NOT reset the transaction.
    """
    terms = {
        term_id: lock_academic_term_in_open_transaction_by_id(term_id)
        for term_id in sorted({t for t in term_ids if t is not None})
    }
    levels = {
        level_id: lock_level_in_open_transaction_by_id(level_id)
        for level_id in sorted({lvl for lvl in level_ids if lvl is not None})
    }
    courses = {
        course_id: lock_course_in_open_transaction_by_id(course_id)
        for course_id in sorted({c for c in course_ids if c is not None})
    }
    return HierarchyLocks(terms, levels, courses)


def lock_academic_hierarchy(term_ids=(), level_ids=(), course_ids=()):
    """As `lock_academic_hierarchy_in_open_transaction`, preceded by
    exactly one deliberate `db.session.rollback()`. Use this for the
    FIRST lock of a request; see the module docstring.
    """
    db.session.rollback()
    return lock_academic_hierarchy_in_open_transaction(term_ids, level_ids, course_ids)
