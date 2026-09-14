"""Read queries and presentation for the Administrator fee plan catalogue
(Phase 5 / M02).

Flask-independent: explicit, column-projected queries returning ORM rows or
plain presentation dicts, and no ``request``, ``abort`` or template.
Route-level 404 / redirect handling belongs in
``app/blueprints/admin/fee_plans.py``.

**Every read here is unscoped, and reachable only by an active
Administrator.** The catalogue is center-wide configuration managed by role;
there is no Student, Teacher or Researcher read of a fee plan anywhere in
M02, and nothing here is called from one.

**Internal ids stay inside the service layer.** A list row carries its
plan's internal id only so the page's item totals can be fetched in one
keyed query; :func:`build_plan_list_view` and :func:`build_plan_detail_view`
drop every internal id before anything reaches a template.

**Bounded, and free of N+1.** The list is a fixed page of :data:`PAGE_SIZE`
ordered by ``id DESC``, with ``LIMIT PAGE_SIZE + 1`` for the has-next flag
and no ``COUNT`` -- an exact total would be both an unbounded scan and a
figure nobody needs. The page's item counts and totals come from **one**
query keyed by that page's plan ids. A detail page is three queries whatever
the plan holds, and its removed-item history is capped at
:data:`REMOVED_HISTORY_CAP` rows.

**Money is added in Python, never in SQL.** No ``SUM()`` is issued over
``fee_plan_items.amount``; every total is :func:`~app.services.money.sum_amounts`
over exact ``Decimal`` values, so the backend's arithmetic -- floating point
on SQLite -- is never part of a total.
"""

from decimal import Decimal

from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    MAX_ACTIVE_FEE_PLAN_ITEMS,
    FeePlan,
    FeePlanItem,
    FeePlanItemKind,
    FeePlanItemStatus,
    FeePlanStatus,
    User,
)
from app.models.fee_plan_item import fee_plan_item_label_key
from app.services.money import format_amount, sum_amounts
from app.services.schedule_occurrences import to_app_local

_DRAFT = FeePlanStatus.DRAFT.value
_ACTIVE = FeePlanStatus.ACTIVE.value
_ARCHIVED = FeePlanStatus.ARCHIVED.value
_ITEM_ACTIVE = FeePlanItemStatus.ACTIVE.value
_ITEM_REMOVED = FeePlanItemStatus.REMOVED.value

#: The fixed list page size. Declared here rather than imported: each
#: feature owns its own bounds.
PAGE_SIZE = 20

#: How many removed items a detail page shows, newest removal first.
REMOVED_HISTORY_CAP = 50

_MAX_PAGE = 10000
_PUBLIC_ID_MAX_LENGTH = 36

#: Administrator-facing wording per status and per kind, declared once so
#: the list, the filter, the badges and the forms cannot disagree.
STATUS_LABELS = {
    _DRAFT: "Draft",
    _ACTIVE: "Active",
    _ARCHIVED: "Archived",
}
STATUS_FILTERS = tuple(STATUS_LABELS)
KIND_LABELS = {
    FeePlanItemKind.REGISTRATION.value: "Registration",
    FeePlanItemKind.COURSE.value: "Course",
}
KIND_CHOICES = tuple(KIND_LABELS.items())


def normalize_page(value):
    """A positive page number. A missing, non-numeric, zero, negative or
    absurdly large value becomes page 1 rather than reaching SQL as an
    offset."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > _MAX_PAGE:
        return 1
    return page


def normalize_status_filter(value):
    """One of :data:`STATUS_FILTERS`, or ``None`` for "any status". Anything
    else is dropped rather than guessed at or reported."""
    value = (value or "").strip()
    return value if value in STATUS_FILTERS else None


def _local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)


def _page(query, page):
    rows = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1).all()
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


def fee_plans_page(page, status=None):
    """``(rows, has_next)`` for one page of the catalogue, newest first.

    `status` must already be normalised by :func:`normalize_status_filter`.
    """
    creator = aliased(User)
    query = db.session.query(
        FeePlan.id,
        FeePlan.public_id,
        FeePlan.name,
        FeePlan.status,
        FeePlan.currency_code,
        FeePlan.first_activated_at,
        FeePlan.created_at,
        FeePlan.updated_at,
        creator.full_name.label("created_by_name"),
    ).join(creator, creator.id == FeePlan.created_by_id)
    if status is not None:
        query = query.filter(FeePlan.status == status)
    return _page(query.order_by(FeePlan.id.desc()), page)


def active_item_summaries(plan_ids):
    """``{plan_id: (active_item_count, exact_total)}`` for `plan_ids`, from
    one query, added in Python ``Decimal``."""
    ids = list(plan_ids)
    if not ids:
        return {}
    rows = (
        db.session.query(FeePlanItem.fee_plan_id, FeePlanItem.amount)
        .filter(FeePlanItem.fee_plan_id.in_(ids), FeePlanItem.status == _ITEM_ACTIVE)
        .order_by(FeePlanItem.fee_plan_id.asc(), FeePlanItem.id.asc())
        .all()
    )
    amounts = {plan_id: [] for plan_id in ids}
    for plan_id, amount in rows:
        amounts[plan_id].append(amount)
    return {plan_id: (len(values), sum_amounts(values)) for plan_id, values in amounts.items()}


def build_plan_list_view(rows, summaries, tz_name="UTC"):
    """Presentation dicts for one list page. No internal id survives."""
    view = []
    for row in rows:
        count, total = summaries.get(row.id, (0, Decimal(0)))
        view.append(
            {
                "public_id": row.public_id,
                "name": row.name,
                "status": row.status,
                "status_label": STATUS_LABELS.get(row.status, row.status),
                "currency_code": row.currency_code,
                "item_count": count,
                "total_text": format_amount(total),
                "frozen": row.first_activated_at is not None,
                "created_by_name": row.created_by_name,
                "created_local": _local(tz_name, row.created_at),
                "updated_local": _local(tz_name, row.updated_at),
            }
        )
    return view


# ---------------------------------------------------------------------------
# One plan
# ---------------------------------------------------------------------------


def admin_fee_plan(public_id):
    """One plan by ``public_id``, or ``None``. A pre-lock read: it decides
    which row a write locks and what a page shows, never whether a write is
    allowed."""
    if not public_id or len(public_id) > _PUBLIC_ID_MAX_LENGTH:
        return None
    return FeePlan.query.filter(FeePlan.public_id == public_id).first()


def fee_plan_item(plan_id, item_public_id):
    """One item by ``public_id`` **inside** `plan_id`, or ``None``. An item of
    another plan is not found, which is what makes a mismatched URL a 404."""
    if not item_public_id or len(item_public_id) > _PUBLIC_ID_MAX_LENGTH:
        return None
    return FeePlanItem.query.filter(
        FeePlanItem.fee_plan_id == plan_id, FeePlanItem.public_id == item_public_id
    ).first()


def plan_name_taken(name, exclude_plan_id=None):
    """Whether another plan -- in any status -- already has `name`.

    `name` must be the normalized value the write would persist. Comparison
    is left to the column's collation, as for every existing unique title in
    this project; ``uq_fee_plans_name`` is the final defense.
    """
    query = FeePlan.query.filter(FeePlan.name == name)
    if exclude_plan_id is not None:
        query = query.filter(FeePlan.id != exclude_plan_id)
    return bool(db.session.query(query.exists()).scalar())


def active_item_count(plan_id):
    """How many active items `plan_id` has, read no further than one past the
    limit. A friendly preview only; the write re-counts the locked rows."""
    return len(
        db.session.query(FeePlanItem.id)
        .filter(FeePlanItem.fee_plan_id == plan_id, FeePlanItem.status == _ITEM_ACTIVE)
        .limit(MAX_ACTIVE_FEE_PLAN_ITEMS + 1)
        .all()
    )


def active_label_taken(plan_id, label, exclude_item_id=None):
    """Whether an active item of `plan_id` other than `exclude_item_id`
    already carries `label`. A friendly preview only; the write re-proves it
    against the locked rows."""
    key = fee_plan_item_label_key(label)
    rows = (
        db.session.query(FeePlanItem.id, FeePlanItem.label)
        .filter(FeePlanItem.fee_plan_id == plan_id, FeePlanItem.status == _ITEM_ACTIVE)
        .all()
    )
    return any(
        row.id != exclude_item_id and fee_plan_item_label_key(row.label) == key for row in rows
    )


def build_plan_header_view(plan):
    """The few facts a form page shows about the plan it belongs to."""
    return {
        "public_id": plan.public_id,
        "name": plan.name,
        "status": plan.status,
        "status_label": STATUS_LABELS.get(plan.status, plan.status),
        "currency_code": plan.currency_code,
    }


def build_plan_detail_view(plan, tz_name="UTC"):
    """One plan in full: its attribution, its active items with their exact
    total, and its capped removed-item history. Three queries, whatever the
    plan holds. No internal id survives."""
    items = (
        FeePlanItem.query.filter(
            FeePlanItem.fee_plan_id == plan.id, FeePlanItem.status == _ITEM_ACTIVE
        )
        .order_by(FeePlanItem.id.asc())
        .all()
    )
    remover = aliased(User)
    removed = (
        db.session.query(
            FeePlanItem.kind,
            FeePlanItem.label,
            FeePlanItem.amount,
            FeePlanItem.removed_at,
            remover.full_name.label("removed_by_name"),
        )
        .join(remover, remover.id == FeePlanItem.removed_by_id)
        .filter(FeePlanItem.fee_plan_id == plan.id, FeePlanItem.status == _ITEM_REMOVED)
        .order_by(FeePlanItem.removed_at.desc(), FeePlanItem.id.desc())
        .limit(REMOVED_HISTORY_CAP + 1)
        .all()
    )
    actor_ids = {
        user_id
        for user_id in (plan.created_by_id, plan.first_activated_by_id, plan.status_changed_by_id)
        if user_id is not None
    }
    names = dict(
        db.session.query(User.id, User.full_name).filter(User.id.in_(actor_ids)).all()
    )

    view = build_plan_header_view(plan)
    view.update(
        {
            "description": plan.description,
            "version": plan.version,
            "is_draft": plan.is_draft,
            "is_active": plan.is_active,
            "is_archived": plan.is_archived,
            "frozen": plan.has_been_activated,
            "created_by_name": names.get(plan.created_by_id),
            "created_local": _local(tz_name, plan.created_at),
            "updated_local": _local(tz_name, plan.updated_at),
            "first_activated_by_name": names.get(plan.first_activated_by_id),
            "first_activated_local": _local(tz_name, plan.first_activated_at),
            "status_changed_by_name": names.get(plan.status_changed_by_id),
            "status_changed_local": _local(tz_name, plan.status_changed_at),
            "active_items": [
                {
                    "public_id": item.public_id,
                    "kind": item.kind,
                    "kind_label": KIND_LABELS.get(item.kind, item.kind),
                    "label": item.label,
                    "amount_text": format_amount(item.amount),
                    "version": item.version,
                    "status": item.status,
                }
                for item in items
            ],
            "item_count": len(items),
            "item_limit": MAX_ACTIVE_FEE_PLAN_ITEMS,
            "total_text": format_amount(sum_amounts([item.amount for item in items])),
            "removed_items": [
                {
                    "kind_label": KIND_LABELS.get(row.kind, row.kind),
                    "label": row.label,
                    "amount_text": format_amount(row.amount),
                    "removed_by_name": row.removed_by_name,
                    "removed_local": _local(tz_name, row.removed_at),
                }
                for row in removed[:REMOVED_HISTORY_CAP]
            ],
            "removed_truncated": len(removed) > REMOVED_HISTORY_CAP,
            "removed_cap": REMOVED_HISTORY_CAP,
        }
    )
    return view
