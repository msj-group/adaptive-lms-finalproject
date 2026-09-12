"""The single deterministic lock chain for every CalendarEvent write
(Phase 4 / M10).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering, exactly like ``app/services/group_transactions.py``,
``app/services/quiz_transactions.py``,
``app/services/attendance_transactions.py``,
``app/services/grade_transactions.py`` and
``app/services/announcement_transactions.py``. Route-level 404 /
redirect / flash handling belongs in the Blueprint module that calls
this.

**One chain, one order, every M10 write** -- creation, edit and
cancellation alike::

    (academic hierarchy, only where a future scope needs one)
    -> acting User
    -> CalendarEvent

There is deliberately one function rather than one per route: the order
is the thing that must not vary, and three near-identical functions
would be three chances for it to. A caller passes only the links its
write actually has, and the chain simply stops where the arguments stop
-- a **create** locks the acting Administrator and nothing else, because
there is no event row yet.

**A center event has no academic hierarchy, and none is invented.** A
``calendar_events`` row carries no AcademicTerm, Level, Course or Group
(see the model docstring), so there is nothing academic to lock and this
chain locks nothing academic. It still *calls*
:func:`~app.services.academic_hierarchy_transactions.lock_academic_hierarchy`
with no ids, for one reason: that function owns the project's single
deliberate ``db.session.rollback()`` before a request's first lock, and
every milestone's chain takes it from the same place rather than each
one resetting the transaction its own way. The call locks no row --
``None`` / empty id sets are dropped -- and the returned
``HierarchyLocks`` is empty. It is also the place a future scoped
calendar event would add its ancestors, in the one order the whole
project already uses, instead of a new order being invented.

Because every write requests its subset **in the same relative order**,
two Administrators editing and cancelling the same event, or editing two
different events at once, cannot deadlock against each other. They also
serialize against the academic-lifecycle and Group-membership writes
that pass through the acting-``users`` lock.

**Nothing here authorizes anything.** Every value may come back ``None``
-- a suspended actor, an event somebody else is mid-write on, an event
that never existed -- and the caller must treat each of those as a
rejection, roll back, and 404 or redirect. It must never "keep going
past" one.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so this code runs correctly in tests
without actually locking anything. Tests can assert the *requested* lock
set and order (structural); they prove nothing about real InnoDB
blocking.
"""

from app.models import CalendarEvent, User, UserRole, UserStatus
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_USER_ACTIVE = UserStatus.ACTIVE.value


class CalendarLocks:
    """The rows one M10 lock chain returned.

    Any attribute may be ``None``; see the module docstring. Deliberately
    ``__slots__``-ed, so a typo in a caller raises instead of silently
    reading ``None``.
    """

    __slots__ = ("hierarchy", "actor", "event")

    def __init__(self, hierarchy, actor=None, event=None):
        self.hierarchy = hierarchy
        self.actor = actor
        self.event = event


def lock_calendar_event_chain(actor_id, event_id=None):
    """Take the M10 lock order in one open transaction, stopping wherever
    the caller's arguments stop. Returns a :class:`CalendarLocks`.

    `event_id` is the **internal** id, discovered by a non-locking read
    of the public id before this call. That pre-lock read only decides
    *which* row to lock: every condition -- the lifecycle, the version,
    the public id itself -- is re-proved against the locked row
    afterwards, and a row that vanished in between locks to ``None``.
    """
    hierarchy = lock_academic_hierarchy()
    actor = None
    if actor_id is not None:
        actor = User.query.filter_by(id=actor_id).with_for_update().first()
    event = None
    if event_id is not None:
        event = (
            CalendarEvent.query.filter_by(id=event_id).with_for_update().first()
        )
    return CalendarLocks(hierarchy, actor=actor, event=event)


def administrator_authz_broken(locks):
    """``True`` when the **locked** acting account is no longer an active
    Administrator -- the caller rolls back and 404s, with no disclosure
    of which condition failed.

    ``roles_required`` ran once, before the view; a request that has
    waited behind somebody else's lock needs the answer as it is *now*.
    There is deliberately no assignment or membership to check: the
    center's calendar is managed by role, and role is exactly what this
    re-reads.
    """
    if locks.actor is None:
        return True
    return locks.actor.role != _ADMINISTRATOR or locks.actor.status != _USER_ACTIVE
