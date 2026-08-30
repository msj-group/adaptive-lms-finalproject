"""Flask-independent AcademicTerm write-transaction primitives.

Deliberately has no `abort`, `flash`, `redirect`, or template rendering
here -- route-level 404/error handling belongs in the Blueprint modules
that call this, exactly like `app/services/course_transactions.py` and
`app/services/group_transactions.py` stay independent of Flask.

AcademicTerm is the FIRST entity in the approved global lock order
(`AcademicTerm -> Level -> Course -> Group -> User(s, ascending id) ->
relationship row`). Every chain that locks an AcademicTerm at all locks
it first, so both entry points here perform the deliberate
transaction-boundary reset (`db.session.rollback()`) before their
locking query. There is no `in_open_transaction` variant, because
nothing in an approved chain is ever locked before the AcademicTerm.

The reset is not error recovery. By the time a mutation route reaches
this call, Flask-Login has read `current_user`, `@roles_required` has
checked it, and a friendly pre-lock preview read may have run -- under
MySQL/InnoDB REPEATABLE READ any of those may have opened this session's
consistent-read snapshot. `SELECT ... FOR UPDATE` always fetches the
current committed row, but every *plain* SELECT issued afterwards in the
same still-open transaction would keep reading the earlier snapshot.
Rolling back here -- nothing has been mutated yet, so there is nothing to
lose -- ends that snapshot so the fresh transaction is what every
subsequent check in the caller's request reads from. No ordinary query
runs between the reset and the locking query.

SQLite (used by the test suite) has no `SELECT ... FOR UPDATE` syntax and
no REPEATABLE READ snapshot isolation, so these functions run correctly
in tests without actually locking or resetting anything. This proves
nothing about real blocking: SQLite does not prove actual MySQL/InnoDB
row blocking. Tests can only assert that this code *requests* the lock
and the rollback, and in the right order (structural).
"""

from app.extensions import db
from app.models import AcademicTerm


def _lock_academic_term_row(**criteria):
    """Issue the `SELECT ... FOR UPDATE` for a single AcademicTerm row
    matching `criteria` and return it (or None). Shared by both public
    entry points so their locking query cannot drift apart. Does not
    reset the transaction -- the callers do that first.
    """
    return AcademicTerm.query.filter_by(**criteria).with_for_update().first()


def lock_academic_term_for_write(academic_term_public_id):
    """End any transaction opened by earlier reads, then lock and return
    the *current* AcademicTerm row by public_id -- or None if it does not
    exist. First-lock entry point; see this module's docstring for the
    deliberate-reset rationale.
    """
    db.session.rollback()
    return _lock_academic_term_row(public_id=academic_term_public_id)


def lock_academic_term_for_write_by_id(academic_term_id):
    """Same as `lock_academic_term_for_write`, but by internal numeric id.

    A Group create/edit form submits an AcademicTerm's internal id
    (`GroupForm.academic_term_id` is a `SelectField` of `AcademicTerm.id`,
    and `Group.academic_term_id` stores that same id), so a future
    `Term -> ... -> Group` chain that starts from a submitted Group form
    needs the by-id entry point. Performs the same single deliberate
    reset as the public_id variant.
    """
    db.session.rollback()
    return _lock_academic_term_row(id=academic_term_id)
