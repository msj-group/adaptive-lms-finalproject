"""Flask-independent Group write-transaction primitive.

Deliberately has no `abort`, `flash`, `redirect`, or template rendering
here -- route-level 404/error handling belongs in the Blueprint modules
that call this, exactly like `app/services/group_memberships.py` stays
independent of Flask so it can be imported from any Blueprint (or, later,
from Attendance) without pulling in request/response concerns.
"""

from app.extensions import db
from app.models import Group


def lock_group_for_write(group_public_id):
    """End any transaction opened by earlier reads, then lock and return
    the *current* Group row by public_id -- or None if it does not exist.

    The `db.session.rollback()` below is a deliberate transaction-
    boundary reset, not error recovery. By the time any Group-affecting
    mutation route reaches this call, Flask-Login has already read
    `current_user`, `@roles_required` has checked it, and (for Group
    edit) an earlier friendly pre-lock read/form-validation pass may have
    run -- under MySQL/InnoDB REPEATABLE READ, any of those may have
    already established this session's consistent-read snapshot.
    `SELECT ... FOR UPDATE` always fetches the *current* committed row
    for whatever it locks regardless of that snapshot, but every *plain*
    SELECT issued afterwards in the same still-open transaction would
    otherwise keep reading the earlier snapshot instead of anything
    committed since. Rolling back here -- nothing has been mutated yet,
    so there is nothing to lose -- ends that snapshot before the lock is
    taken, so the fresh transaction it starts is what every subsequent
    check in the caller's request reads from.

    Every route that mutates a Group or a Group's membership (Enrollment
    create/withdraw/reactivate, GroupTeacherAssignment assign/remove/
    reactivate, Group edit, Group status toggle) calls this same
    function, in the same order, so concurrent requests touching the same
    Group serialize on this lock instead of racing independently: the
    second request blocks until the first commits or rolls back, at
    which point its own re-check of whatever conditions it cares about
    sees the first request's already-committed result rather than stale
    data.

    Callers that need the Group's relationships (course, academic_term,
    ...) for display must query for a *separate*, non-locking read after
    releasing this lock (`db.session.rollback()` or `db.session.commit()`)
    -- holding the write lock for anything beyond the minimum needed to
    decide and apply the mutation serializes other requests against this
    one for longer than necessary.

    SQLite (used by the test suite) has no SELECT ... FOR UPDATE syntax
    and no REPEATABLE READ snapshot isolation to begin with, so this code
    path runs correctly in tests without actually locking or resetting
    any snapshot. This proves nothing about real blocking: SQLite does
    not prove actual MySQL/InnoDB row blocking. Tests can only assert
    that this function *requests* the lock and the rollback (structural),
    not that SQLite honours either.

    Callers that must lock a Course (see
    `app/services/course_transactions.py`) *before* this Group lock, in
    the same transaction, should call `lock_group_in_open_transaction`
    instead -- see that function's docstring for why a second reset here
    would release the Course lock already held.
    """
    db.session.rollback()
    return lock_group_in_open_transaction(group_public_id)


def lock_group_in_open_transaction(group_public_id):
    """Lock and return the current Group row by public_id -- or None --
    WITHOUT first resetting the transaction.

    Only safe to call when the caller's transaction is already
    known-fresh -- in practice, immediately after
    `app.services.course_transactions.lock_course_for_write_by_id` has
    just reset it as the first query of a Course -> Group lock sequence
    (see `group_edit` in `app/blueprints/admin/groups.py`, which locks
    the submitted target Course first, precisely so a Group create or
    retarget serializes against a concurrent Course-level change on that
    same Course). Calling this directly against a transaction that might
    still be holding an earlier, stale REPEATABLE READ snapshot
    reintroduces exactly the staleness problem the reset in
    `lock_group_for_write` exists to avoid.

    Every route that begins directly with a Group lock -- Group status
    toggle, and every Enrollment/GroupTeacherAssignment mutation -- must
    keep calling `lock_group_for_write`, never this function, so that
    lock is still the first query of a fresh transaction.
    """
    return Group.query.filter_by(public_id=group_public_id).with_for_update().first()
