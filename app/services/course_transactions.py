"""Flask-independent Course write-transaction primitives.

Deliberately has no `abort`, `flash`, `redirect`, or template rendering
here -- route-level 404/error handling belongs in the Blueprint modules
that call this, exactly like `app/services/group_transactions.py` stays
independent of Flask so it can be imported from any Blueprint without
pulling in request/response concerns.

On InnoDB foreign-key locking: `groups.course_id` references
`courses.id`, so a concurrent Group insert/update that references a
Course row *may* interact with locks already held on that Course's
primary-key index record, depending on the specific operation and the
active isolation level -- this is not "categorically irrelevant." It is
also not something this application relies on as its concurrency
contract: implicit FK-driven locking is undocumented behaviour of a
specific storage engine, gives no guarantee about *which* rows end up
locked or in what order, and provides nothing at all for the
Course-editing-Course case (two `course_edit` submissions, or an edit
racing a status toggle or a move), which never touches `groups` at all.
Every write path below takes an explicit `SELECT ... FOR UPDATE` lock
instead, exactly mirroring `group_transactions.py`'s own reasoning, so
the actual serialization guarantee is visible in this code rather than
implied by a side effect of the storage engine.

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


def lock_course_for_write(course_public_id):
    """End any transaction opened by earlier reads, then lock and return
    the *current* Course row by public_id -- or None if it does not
    exist. Entry point for routes that begin directly with a Course lock
    (Course edit, Course status toggle) -- see `lock_group_for_write`'s
    docstring for the full deliberate-reset rationale, which applies here
    identically.
    """
    db.session.rollback()
    return Course.query.filter_by(public_id=course_public_id).with_for_update().first()


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
    the Course -> Group lock sequence -- this module exposes only
    Course-locking primitives).
    """
    db.session.rollback()
    return Course.query.filter_by(id=course_id).with_for_update().first()
