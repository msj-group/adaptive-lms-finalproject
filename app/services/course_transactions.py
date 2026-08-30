"""Flask-independent Course write-transaction primitives.

Deliberately has no `abort`, `flash`, `redirect`, or template rendering
here -- route-level 404/error handling belongs in the Blueprint modules
that call this, exactly like `app/services/group_transactions.py` stays
independent of Flask so it can be imported from any Blueprint without
pulling in request/response concerns.

On InnoDB foreign-key locking: `groups.course_id` references
`courses.id`, so a Group insert or update that references a Course row
may interact with locks on that referenced Course's index record while
InnoDB checks the foreign-key constraint, depending on the operation and
the active isolation behaviour. This engine-level behaviour is not the
application's concurrency contract and is not relied upon here. The
contract is the explicit Course `SELECT ... FOR UPDATE` locking that the
protected routes take (`lock_course_for_write` /
`lock_course_for_write_by_id` below), exactly mirroring
`group_transactions.py`'s own reasoning, so the serialization guarantee
is visible in this code.

Course-to-Course operations (two `course_edit` submissions, or an edit
racing a status toggle or a reorder) do not involve the
`groups.course_id` foreign key at all; their coordination depends on the
explicit application locks where implemented (`course_edit` and
`course_toggle_status` share `lock_course_for_write`) and on ordinary
database write locking otherwise -- not on any foreign-key side effect.

Course is the THIRD entity in the approved global lock order
(`AcademicTerm -> Level -> Course -> Group -> User(s, ascending id) ->
relationship row`). A chain that locks a Course may start here (a
Course-only edit or status toggle) or may already hold an AcademicTerm
and/or Level lock (a future `Term -> Level -> Course -> ...` chain):

- `lock_course_for_write` / `lock_course_for_write_by_id` are the
  first-lock entry points: they perform exactly one deliberate
  transaction-boundary reset (`db.session.rollback()`), then delegate to
  the matching no-reset primitive. No ordinary query runs between the
  reset and the locking query.
- `lock_course_in_open_transaction` / `lock_course_in_open_transaction_by_id`
  never reset. Use them only when an earlier first-lock entry point in
  the same request already reset the transaction -- the AcademicTerm lock
  in a `Term -> Level -> Course` chain, or the Level lock when the chain
  has no Term. A second `db.session.rollback()` here would release the
  lock that earlier entry point acquired. These helpers perform no
  transaction-ending operation of their own -- no `rollback`, `commit`,
  `close`, or `remove` -- so they never end a transaction; they make no
  claim about what the caller does afterwards.

The reset is not error recovery: by the time a mutation route reaches
this call, Flask-Login has read `current_user`, `@roles_required` has
checked it, and a friendly pre-lock preview read may have run -- under
MySQL/InnoDB REPEATABLE READ any of those may have opened this session's
consistent-read snapshot. `SELECT ... FOR UPDATE` always fetches the
current committed row, but every *plain* SELECT issued afterwards in the
same still-open transaction would keep reading the earlier snapshot.
Rolling back once here -- nothing has been mutated yet -- ends that
snapshot so the fresh transaction is what every subsequent check reads
from.

SQLite (used by the test suite) has no SELECT ... FOR UPDATE syntax and
no REPEATABLE READ snapshot isolation to begin with, so every function
here runs correctly in tests without actually locking or resetting any
snapshot. This proves nothing about real blocking: SQLite does not prove
actual MySQL/InnoDB row blocking. Tests can only assert that this code
*requests* the lock and the rollback, and in the right order
(structural), never that SQLite honours either.
"""

from app.extensions import db
from app.models import Course


def lock_course_in_open_transaction(course_public_id):
    """Lock and return the current Course row by public_id -- or None --
    WITHOUT resetting the transaction first.

    Only safe when an earlier first-lock entry point in the same request
    already reset the transaction -- the AcademicTerm lock in a future
    `Term -> Level -> Course -> ...` chain, or the Level lock when the
    chain has no Term. In a chain that does include a Term, the
    intervening Level lock is itself a no-reset primitive
    (`lock_level_in_open_transaction_by_id`), so it is the *first-lock*
    entry point -- not the step immediately before this one -- that
    performed the reset. Calling this against a transaction that might
    still hold a stale REPEATABLE READ snapshot reintroduces the
    staleness problem that reset exists to avoid.

    It performs no transaction-ending operation of its own -- no
    `rollback`, `commit`, `close`, or `remove` -- so it never ends a
    transaction or releases a lock an earlier step acquired. It makes no
    claim about a later `rollback`, `commit`, `close`, `remove`, or
    exception unwinding in the caller.
    """
    return Course.query.filter_by(public_id=course_public_id).with_for_update().first()


def lock_course_in_open_transaction_by_id(course_id):
    """Same as `lock_course_in_open_transaction`, but by internal numeric
    id rather than public_id -- for a future chain that identifies the
    Course by id (e.g. a submitted Group form's `course_id`). Also never
    resets.
    """
    return Course.query.filter_by(id=course_id).with_for_update().first()


def lock_course_for_write(course_public_id):
    """End any transaction opened by earlier reads, then lock and return
    the *current* Course row by public_id -- or None if it does not
    exist. First-lock entry point for routes that begin directly with a
    Course lock (Course edit, Course status toggle).

    Performs exactly one deliberate reset (see this module's docstring
    and `lock_group_for_write`'s for the full REPEATABLE READ rationale),
    then delegates to `lock_course_in_open_transaction` so the locking
    query cannot drift from the no-reset path. External behaviour is
    unchanged from before that primitive was split out.
    """
    db.session.rollback()
    return lock_course_in_open_transaction(course_public_id)


def lock_course_for_write_by_id(course_id):
    """Same as `lock_course_for_write`, but by internal numeric id rather
    than public_id.

    Group create/edit only ever have a Course's internal id available --
    `GroupForm.course_id` is a `SelectField` whose values are `Course.id`,
    and `Group.course_id` stores that same internal id, never a
    cross-reference to a Course's public_id. This is the entry point
    those two routes use to lock the *submitted target* Course before
    creating or retargeting a Group, so that operation serializes against
    a concurrent Course-level change on the same row (see `group_edit` and
    `group_create` in `app/blueprints/admin/groups.py`, which import this
    function directly and, for `group_edit`, also import
    `lock_group_in_open_transaction` directly from
    `app.services.group_transactions` for the second, no-reset half of
    the Course -> Group lock sequence).

    Performs exactly one deliberate reset, then delegates to
    `lock_course_in_open_transaction_by_id`; external behaviour is
    unchanged from before that primitive was split out.
    """
    db.session.rollback()
    return lock_course_in_open_transaction_by_id(course_id)
