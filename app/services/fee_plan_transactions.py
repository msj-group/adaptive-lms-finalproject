"""The single deterministic lock chain for every fee plan write
(Phase 5 / M02).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering, exactly like ``app/services/calendar_transactions.py``. Route-level
404 / redirect / flash handling belongs in
``app/blueprints/admin/fee_plans.py``.

**One chain, one order, every M02 write**::

    lock_academic_hierarchy() reset point
    -> acting Administrator User
    -> FeePlan
    -> affected FeePlanItems (ascending internal id)

There is one function rather than one per route: the order is the thing that
must not vary. A caller passes only the links its write has, and the chain
stops where the arguments stop -- a plan **create** locks the acting
Administrator and nothing else, because there is no plan row yet.

**A fee plan has no academic hierarchy, and none is invented.** The chain
still *calls* :func:`lock_academic_hierarchy` with no ids, because that
function owns the project's single deliberate ``db.session.rollback()``
before a request's first lock. The call locks no row.

**The FeePlan lock is the aggregate's serialization point.** Every write to
a plan or to any of its items takes it first, so two Administrators editing
the same draft -- one adding an item, one activating -- run one after the
other, and the second re-proves everything against what the first committed.
Items are locked after the plan in ascending internal id, never in display
or submission order, which is the project-wide rule that keeps concurrent
writers from deadlocking.

Which items a write locks:

- item **create** and **activation** -- every currently active item of the
  plan, because the label-uniqueness rule, the item limit and the "at least
  one item" rule are decided over exactly that set;
- item **edit** -- the edited item plus every active sibling, for the label
  rule;
- item **remove** -- the removed item only;
- plan **edit**, **archive** and **reactivate** -- none: they change no item.

The id-only read that decides which active items to lock runs **after** the
plan lock is held. Every other item insert or status change must take that
same lock first, so the set cannot change between that read and the item
locks.

**Nothing here authorizes anything.** Every value may come back ``None`` --
a suspended actor, a vanished row -- and the caller must treat each as a
rejection, roll back, and 404 or redirect.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no REPEATABLE
READ snapshot isolation, so this runs correctly in tests without locking
anything. Tests assert the *requested* lock set and order (structural);
they prove nothing about real InnoDB blocking.
"""

from app.extensions import db
from app.models import FeePlan, FeePlanItem, FeePlanItemStatus, User, UserRole, UserStatus
from app.models.fee_plan_item import fee_plan_item_label_key
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_ITEM_ACTIVE = FeePlanItemStatus.ACTIVE.value


class FeePlanLocks:
    """The rows one M02 lock chain returned.

    ``items`` maps internal item id to the locked row (or ``None`` when the
    id no longer names a row). Deliberately ``__slots__``-ed, so a typo in a
    caller raises instead of silently reading ``None``.
    """

    __slots__ = ("hierarchy", "actor", "plan", "items")

    def __init__(self, hierarchy, actor=None, plan=None, items=None):
        self.hierarchy = hierarchy
        self.actor = actor
        self.plan = plan
        self.items = {} if items is None else items


def lock_fee_plan_chain(actor_id, plan_id=None, item_ids=(), include_active_items=False):
    """Take the M02 lock order in one open transaction, stopping wherever
    the caller's arguments stop. Returns a :class:`FeePlanLocks`.

    `plan_id` and `item_ids` are **internal** ids discovered by non-locking
    reads of public ids before this call. Those reads only decide *which*
    rows to lock: ownership, lifecycle, versions and every business rule are
    re-proved against the locked rows afterwards.

    `include_active_items` adds every active item of the locked plan to the
    item set. Items are locked only once the plan lock is held.
    """
    hierarchy = lock_academic_hierarchy()
    actor = None
    if actor_id is not None:
        actor = User.query.filter_by(id=actor_id).with_for_update().first()
    plan = None
    if plan_id is not None:
        plan = FeePlan.query.filter_by(id=plan_id).with_for_update().first()
    items = {}
    if plan is not None:
        wanted = {item_id for item_id in item_ids if item_id is not None}
        if include_active_items:
            wanted.update(
                row.id
                for row in db.session.query(FeePlanItem.id)
                .filter(
                    FeePlanItem.fee_plan_id == plan.id,
                    FeePlanItem.status == _ITEM_ACTIVE,
                )
                .all()
            )
        for item_id in sorted(wanted):
            items[item_id] = (
                FeePlanItem.query.filter_by(id=item_id).with_for_update().first()
            )
    return FeePlanLocks(hierarchy, actor=actor, plan=plan, items=items)


def administrator_authz_broken(locks):
    """``True`` when the **locked** acting account is no longer an active
    Administrator. ``roles_required`` ran once, before the view; a request
    that waited behind somebody else's lock needs the answer as it is now.
    """
    if locks.actor is None:
        return True
    return locks.actor.role != _ADMINISTRATOR or locks.actor.status != _USER_ACTIVE


def locked_active_items(locks):
    """The locked plan's **active** items, ascending internal id, re-proved
    from the locked rows themselves -- a row that changed plan or status
    between the id read and its lock is not counted."""
    plan = locks.plan
    if plan is None:
        return []
    return [
        item
        for _item_id, item in sorted(locks.items.items())
        if item is not None and item.fee_plan_id == plan.id and item.status == _ITEM_ACTIVE
    ]


def label_in_use(items, label, exclude_item_id=None):
    """Whether any of `items` other than `exclude_item_id` already carries
    `label`, compared by :func:`fee_plan_item_label_key`."""
    key = fee_plan_item_label_key(label)
    return any(
        item.id != exclude_item_id and fee_plan_item_label_key(item.label) == key
        for item in items
    )


def labels_are_unique(items):
    """Whether no two of `items` share a label key."""
    keys = [fee_plan_item_label_key(item.label) for item in items]
    return len(keys) == len(set(keys))
