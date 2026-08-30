"""Flask-independent Level write-transaction primitives.

Deliberately has no `abort`, `flash`, `redirect`, or template rendering
here -- route-level 404/error handling belongs in the Blueprint modules
that call this, exactly like `app/services/course_transactions.py` and
`app/services/group_transactions.py` stay independent of Flask.

**No route imports this module in M07C1.** These primitives are
infrastructure for later academic-lifecycle wiring -- a future Level
archive/reactivation guard, and future Course operations that must lock
the Level identified by a submitted `level_id`. Nothing here changes any
current behaviour.

Level is the SECOND entity in the approved global lock order
(`AcademicTerm -> Level -> Course -> Group -> User(s, ascending id) ->
relationship row`). A future chain that locks a Level either starts from
the Level or already holds an AcademicTerm lock:

- `lock_level_for_write` / `lock_level_for_write_by_id` are first-lock
  entry points: they perform the deliberate transaction-boundary reset
  (`db.session.rollback()`) before the locking query, with no ordinary
  query in between. Use them when the Level lock is the first lock of the
  request -- a future Level lifecycle operation, or a future Course
  operation that identifies the Level by a submitted `level_id`. See
  `app/services/academic_term_transactions.py`'s docstring for the full
  REPEATABLE READ rationale, which applies here identically.
- `lock_level_in_open_transaction_by_id` performs no reset -- a second
  `db.session.rollback()` after an AcademicTerm lock would release that
  lock. Use it only when an earlier first-lock entry point in the same
  request already reset the transaction.

The first-lock entry points route their locking query through the same
private helper as the no-reset primitive, so the actual
`SELECT ... FOR UPDATE` cannot drift between them.

A Level *reorder* (`move-up` / `move-down`) rewrites the `display_order`
of several sibling Level rows at once. This service locks a single Level
row by identity; it does not, on its own, serialize a whole multi-row
reorder, and this Part neither claims that nor wires reorder to use it.
Any future reorder-concurrency work is a separate decision.

SQLite (used by the test suite) has no `SELECT ... FOR UPDATE` syntax and
no REPEATABLE READ snapshot isolation, so these functions run correctly
in tests without actually locking or resetting anything. This proves
nothing about real blocking: SQLite does not prove actual MySQL/InnoDB
row blocking. Tests can only assert that this code *requests* the lock
and the rollback, and in the right order (structural).
"""

from app.extensions import db
from app.models import Level


def _lock_level_row(**criteria):
    """Issue the `SELECT ... FOR UPDATE` for a single Level row matching
    `criteria` and return it (or None). Shared by every entry point so
    the locking query cannot drift. Does not reset the transaction.
    """
    return Level.query.filter_by(**criteria).with_for_update().first()


def lock_level_in_open_transaction_by_id(level_id):
    """Lock and return the current Level row by internal numeric id -- or
    None -- WITHOUT resetting the transaction first.

    For a future `Term -> Level -> ...` chain: an AcademicTerm first-lock
    entry point has already reset the transaction, so this step must not
    reset again (that would release the AcademicTerm lock). Calling it
    against a transaction that might still hold a stale REPEATABLE READ
    snapshot reintroduces the staleness problem that reset exists to
    avoid.

    It performs no transaction-ending operation of its own -- no
    `rollback`, `commit`, `close`, or `remove` -- so it never ends a
    transaction or releases a lock an earlier step acquired. It makes no
    claim about a later `rollback`, `commit`, `close`, `remove`, or
    exception unwinding in the caller.
    """
    return _lock_level_row(id=level_id)


def lock_level_for_write(level_public_id):
    """End any transaction opened by earlier reads, then lock and return
    the *current* Level row by public_id -- or None. First-lock entry
    point for a future request whose Level lock is its first lock (e.g. a
    Level lifecycle operation identified by public_id).
    """
    db.session.rollback()
    return _lock_level_row(public_id=level_public_id)


def lock_level_for_write_by_id(level_id):
    """Same as `lock_level_for_write`, but by internal numeric id --
    first-lock entry point for a future request that identifies the Level
    by id (e.g. a future Course operation locking the Level named by a
    submitted `level_id`). Performs the same single deliberate reset, then
    delegates to `lock_level_in_open_transaction_by_id` so the locking
    query cannot drift from the no-reset path.
    """
    db.session.rollback()
    return lock_level_in_open_transaction_by_id(level_id)
