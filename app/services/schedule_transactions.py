"""Flask-independent Schedule write-transaction primitive.

Deliberately has no ``abort``, ``flash``, ``redirect``, or template
rendering here -- route-level 404/error handling belongs in the
Blueprint modules that call this, exactly like
``app/services/group_transactions.py`` and
``app/services/course_transactions.py`` stay independent of Flask.

Schedule is the LAST entity in the approved global lock order:

    AcademicTerm -> Level -> Course -> Group -> Schedule

Every Schedule mutation locks its whole ancestor chain first -- via
``lock_academic_hierarchy`` (which owns the single deliberate
``db.session.rollback()``) then ``lock_group_in_open_transaction`` -- so
the Schedule lock is only ever taken inside an already-open,
already-reset transaction. There is therefore no first-lock (resetting)
entry point here, only the no-reset primitive: a second reset at this
point would release the AcademicTerm/Level/Course/Group locks already
held.

``lock_schedule_in_open_transaction_by_id`` performs no
transaction-ending call of its own -- no ``rollback`` / ``commit`` /
``close`` / ``remove`` -- so it never ends a transaction or releases a
lock an earlier step acquired.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so this runs correctly in tests
without actually locking anything. That proves nothing about real
MySQL/InnoDB blocking -- tests can only assert the *requested* lock and
its order (structural).
"""

from app.models import Schedule


def lock_schedule_in_open_transaction_by_id(schedule_id):
    """Lock and return the current Schedule row by internal numeric id --
    or ``None`` -- WITHOUT resetting the transaction.

    Only safe to call after ``lock_academic_hierarchy`` +
    ``lock_group_in_open_transaction`` have already reset the transaction
    and taken the ancestor locks in the same request.
    """
    return Schedule.query.filter_by(id=schedule_id).with_for_update().first()
