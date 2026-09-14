"""Shared fixtures for the Phase 5 / M02 fee plan test modules.

Kept in one module -- like ``tests/calendar_fixtures.py`` -- so the model,
route, transaction and migration suites build the same accounts, plans and
items, and a change to what a fee plan *is* cannot make four files disagree.

Row helpers write straight into the tables, so a test about (say) an
archived plan refusing an edit is not also a test of the activation route.
The route helpers at the bottom drive the application's own write path and
read every signed token out of the page the server rendered -- a test that
minted its own token would stop proving that the page carries a usable one.

Every stored moment is a whole-second 2026 UTC instant earlier than the
real clock, so the lifecycle timestamp CHECKs hold when a route later writes
"now" on top of a fixture row.
"""

import re
from datetime import datetime

from app.extensions import db
from app.models import (
    FeePlan,
    FeePlanItem,
    FeePlanItemKind,
    FeePlanItemStatus,
    FeePlanStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password

PW = "Sup3rSecret!123"

CREATED = datetime(2026, 5, 1, 9, 0, 0)
ACTIVATED = datetime(2026, 5, 2, 9, 0, 0)
ARCHIVED = datetime(2026, 5, 3, 9, 0, 0)
REMOVED = datetime(2026, 5, 1, 12, 0, 0)

DRAFT = FeePlanStatus.DRAFT.value
ACTIVE = FeePlanStatus.ACTIVE.value
ARCHIVED_STATUS = FeePlanStatus.ARCHIVED.value
ITEM_ACTIVE = FeePlanItemStatus.ACTIVE.value
ITEM_REMOVED = FeePlanItemStatus.REMOVED.value
REGISTRATION = FeePlanItemKind.REGISTRATION.value
COURSE = FeePlanItemKind.COURSE.value

STATE_FIELD = "state_token"
STALE_TEXT = "Nothing was saved"
INTEGRITY_TEXT = "Nothing was written"

LIST_URL = "/admin/fee-plans"
NEW_URL = "/admin/fee-plans/new"

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def detail_url(pp):
    return f"/admin/fee-plans/{pp}"


def edit_url(pp):
    return f"/admin/fee-plans/{pp}/edit"


def item_new_url(pp):
    return f"/admin/fee-plans/{pp}/items/new"


def item_edit_url(pp, ip):
    return f"/admin/fee-plans/{pp}/items/{ip}/edit"


def item_remove_url(pp, ip):
    return f"/admin/fee-plans/{pp}/items/{ip}/remove"


def activate_url(pp):
    return f"/admin/fee-plans/{pp}/activate"


def archive_url(pp):
    return f"/admin/fee-plans/{pp}/archive"


def reactivate_url(pp):
    return f"/admin/fee-plans/{pp}/reactivate"


# ---------------------------------------------------------------------------
# Sessions and accounts
# ---------------------------------------------------------------------------


def fresh_identity():
    """Drop Flask-Login's per-request user cache so a second login in the
    same app context really switches accounts."""
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def login_as(client, email, password=PW):
    fresh_identity()
    client.post("/auth/logout", follow_redirects=True)
    fresh_identity()
    return client.post(
        "/auth/login", data={"email": email, "password": password}, follow_redirects=True
    )


def user(email, role, status=UserStatus.ACTIVE.value, name=None):
    row = User(
        email=email.strip().lower(),
        password_hash=hash_password(PW),
        full_name=name or email.split("@")[0],
        role=role,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def admin(email="admin@example.com", **kwargs):
    return user(email, UserRole.ADMINISTRATOR.value, **kwargs)


# ---------------------------------------------------------------------------
# Rows, written directly
# ---------------------------------------------------------------------------


def plan(
    creator,
    name=None,
    description=None,
    status=DRAFT,
    ever_activated=None,
    version=1,
    created_at=CREATED,
):
    """One fee plan in a consistent lifecycle state.

    ``ever_activated`` defaults to what the status implies -- ``False`` for a
    draft, ``True`` for an active plan and for an archived one -- and may be
    set ``False`` for an archived draft.
    """
    if ever_activated is None:
        ever_activated = status != DRAFT
    first_at = first_by = changed_at = changed_by = None
    updated_at = created_at
    if ever_activated:
        first_at, first_by = ACTIVATED, creator.id
        changed_at, changed_by = ACTIVATED, creator.id
        updated_at = ACTIVATED
    if status == ARCHIVED_STATUS:
        changed_at, changed_by = ARCHIVED, creator.id
        updated_at = ARCHIVED
    row = FeePlan(
        name=name or f"Plan {_next()}",
        description=description,
        currency_code="LYD",
        status=status,
        created_by_id=creator.id,
        first_activated_at=first_at,
        first_activated_by_id=first_by,
        status_changed_at=changed_at,
        status_changed_by_id=changed_by,
        version=version,
        created_at=created_at,
        updated_at=updated_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def item(
    owning_plan,
    label=None,
    amount="100.000",
    kind=COURSE,
    status=ITEM_ACTIVE,
    removed_by=None,
    version=1,
):
    removed_at = removed_by_id = None
    if status == ITEM_REMOVED:
        removed_at = REMOVED
        removed_by_id = removed_by.id if removed_by is not None else owning_plan.created_by_id
    row = FeePlanItem(
        fee_plan_id=owning_plan.id,
        kind=kind,
        label=label or f"Item {_next()}",
        amount=amount,
        status=status,
        removed_at=removed_at,
        removed_by_id=removed_by_id,
        version=version,
        created_at=CREATED,
        updated_at=REMOVED if status == ITEM_REMOVED else CREATED,
    )
    db.session.add(row)
    db.session.commit()
    return row


def stored_plan(pp):
    db.session.expire_all()
    return FeePlan.query.filter_by(public_id=pp).one()


def stored_item(ip):
    db.session.expire_all()
    return FeePlanItem.query.filter_by(public_id=ip).one()


# ---------------------------------------------------------------------------
# Tokens read out of rendered pages
# ---------------------------------------------------------------------------


def state_in(html, action=None):
    """The signed state token of the form posting to `action`, or of the
    page's one form when `action` is ``None``; ``""`` when absent."""
    if action is None:
        match = re.search(rf'name="{STATE_FIELD}" value="([^"]*)"', html)
    else:
        match = re.search(
            rf'action="{re.escape(action)}"[^>]*>\s*'
            r'<input type="hidden" name="csrf_token" value="[^"]*">\s*'
            rf'<input type="hidden" name="{STATE_FIELD}" value="([^"]*)"',
            html,
        )
    return match.group(1) if match else ""


def token_from(client, url, action=None):
    return state_in(client.get(url).get_data(as_text=True), action)


# ---------------------------------------------------------------------------
# Route-driven helpers
# ---------------------------------------------------------------------------


def plan_form(name="Standard plan", description="For new students.", token=""):
    return {"name": name, "description": description, STATE_FIELD: token}


def item_form(kind=COURSE, label="Course fee", amount="1250.500", token=""):
    return {"kind": kind, "label": label, "amount": amount, STATE_FIELD: token}


def public_id_from_redirect(response):
    return response.headers["Location"].rstrip("/").split("/")[-1]


def create_plan(client, **overrides):
    """Drive the create form; the new plan's public id, or ``None``."""
    token = token_from(client, NEW_URL)
    response = client.post(NEW_URL, data=plan_form(token=token, **overrides))
    if response.status_code != 302:
        return None
    public_id = public_id_from_redirect(response)
    return None if public_id == "new" else public_id


def edit_plan(client, pp, **overrides):
    token = token_from(client, edit_url(pp))
    return client.post(edit_url(pp), data=plan_form(token=token, **overrides))


def add_item(client, pp, **overrides):
    token = token_from(client, item_new_url(pp))
    return client.post(item_new_url(pp), data=item_form(token=token, **overrides))


def edit_item(client, pp, ip, **overrides):
    token = token_from(client, item_edit_url(pp, ip))
    return client.post(item_edit_url(pp, ip), data=item_form(token=token, **overrides))


def remove_item(client, pp, ip, token=None):
    if token is None:
        token = token_from(client, detail_url(pp), item_remove_url(pp, ip))
    return client.post(item_remove_url(pp, ip), data={STATE_FIELD: token})


def _lifecycle(client, url, pp, confirm, token):
    if token is None:
        token = token_from(client, detail_url(pp), url)
    data = {STATE_FIELD: token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(url, data=data)


def activate(client, pp, confirm=True, token=None):
    return _lifecycle(client, activate_url(pp), pp, confirm, token)


def archive(client, pp, confirm=True, token=None):
    return _lifecycle(client, archive_url(pp), pp, confirm, token)


def reactivate(client, pp, confirm=True, token=None):
    return _lifecycle(client, reactivate_url(pp), pp, confirm, token)


def page(client, url):
    """GET `url` and return its text (used to read flashed messages after a
    redirect)."""
    return client.get(url).get_data(as_text=True)
