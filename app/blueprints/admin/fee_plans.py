"""Administrator fee plan catalogue (Phase 5 / M02).

Ten routes, every object addressed by ``public_id``::

    GET       /admin/fee-plans
    GET|POST  /admin/fee-plans/new
    GET       /admin/fee-plans/<pp>
    GET|POST  /admin/fee-plans/<pp>/edit
    GET|POST  /admin/fee-plans/<pp>/items/new
    GET|POST  /admin/fee-plans/<pp>/items/<ip>/edit
    POST      /admin/fee-plans/<pp>/items/<ip>/remove
    POST      /admin/fee-plans/<pp>/activate
    POST      /admin/fee-plans/<pp>/archive
    POST      /admin/fee-plans/<pp>/reactivate

**This is the only surface in the project that reads or writes a fee
plan.** There is no Student, Teacher or Researcher endpoint for one -- not
hidden in a template, but absent -- and every route here is gated by
``roles_required`` and then re-proves the acting account against its locked
row before any write.

**What a fee plan is not.** M02 creates no student assignment, invoice,
payment, receipt, refund, report, provider, webhook, discount, installment,
scholarship, exemption, tax, quantity or due date, and no endpoint, form
field or control for any of them. The Administrator navigation's
**Payments** entry stays disabled.

**Lifecycle.** A plan is created as a ``draft``. Only a draft may be edited,
and only a draft's items may be added, edited or removed. Activation needs
a current signed activation token and at least one active item, and freezes
the whole definition **permanently** -- from then on, archiving changes
availability only and reactivation restores ``active`` without allowing any
edit. A draft may be archived too; an archived plan is read-only, and only
one that was active before may be reactivated. Nothing is ever deleted: a
removed draft item stays as history.

**Every mutation** is POST-only and CSRF-protected, carries a
purpose-specific signed token (``app/services/fee_plan_tokens.py``), runs
the one lock chain in ``app/services/fee_plan_transactions.py`` -- the
academic-hierarchy reset point, the acting Administrator, the FeePlan, then
the affected items in ascending internal id -- and re-proves the actor, the
plan's lifecycle, the item's ownership, the token's exact state, label
uniqueness, the item limit and the version against the locked rows. A form
failure re-renders only when the submitted token still describes current
state; a stale one is rejected, so a fresh token is never paired with an
outdated form. An ``IntegrityError`` is rolled back first, the actor is
re-authorized from current state, and one generic sentence is shown.

Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: these pages state the center's fees, and a shared or
reused cache entry must never hand one to somebody else.
"""

from functools import wraps

from flask import (
    abort,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plan_forms import FeePlanForm, FeePlanItemForm
from app.extensions import db
from app.models import (
    FEE_PLAN_DESCRIPTION_MAX_LENGTH,
    FEE_PLAN_ITEM_LABEL_MAX_LENGTH,
    FEE_PLAN_NAME_MAX_LENGTH,
    MAX_ACTIVE_FEE_PLAN_ITEMS,
    FeePlan,
    FeePlanItem,
    FeePlanItemStatus,
    FeePlanStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services import fee_plan_tokens as tokens
from app.services.fee_plan_queries import (
    PAGE_SIZE,
    STATUS_LABELS,
    active_item_count,
    active_item_summaries,
    active_label_taken,
    admin_fee_plan,
    build_plan_detail_view,
    build_plan_header_view,
    build_plan_list_view,
    fee_plan_item,
    fee_plans_page,
    normalize_page,
    normalize_status_filter,
    plan_name_taken,
)
from app.services.fee_plan_transactions import (
    administrator_authz_broken,
    label_in_use,
    labels_are_unique,
    lock_fee_plan_chain,
    locked_active_items,
)
from app.services.money import (
    AMOUNT_SCALE,
    CURRENCY_CODE,
    MAX_AMOUNT,
    MIN_AMOUNT,
    amount_input_text,
    format_amount,
)
from app.services.schedule_occurrences import utc_reference_now

_DRAFT = FeePlanStatus.DRAFT.value
_ACTIVE = FeePlanStatus.ACTIVE.value
_ARCHIVED = FeePlanStatus.ARCHIVED.value
_ITEM_ACTIVE = FeePlanItemStatus.ACTIVE.value
_ITEM_REMOVED = FeePlanItemStatus.REMOVED.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: The hidden form field every mutating form carries its signed token in.
_STATE_FIELD = "state_token"


def _financial_response(view):
    """Send ``Cache-Control: private, no-store`` and ``Vary: Cookie`` on every
    response this view returns -- rendered pages and redirects alike."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        response = make_response(view(*args, **kwargs))
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
        return response

    return wrapped


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _write_moment():
    """The authoritative naive-UTC moment for one write, truncated to whole
    seconds (MySQL ``DATETIME`` rounds a fraction rather than truncating
    it). Read only **after** every lock that could have blocked."""
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# Administrator-facing sentences, declared once each
# ======================================================================

_STALE_MESSAGE = (
    "This fee plan was changed after this page was opened, or the page has expired. "
    "Nothing was saved. Please reload, review the plan as it is now, and try again."
)
_INTEGRITY_MESSAGE = (
    "That change could not be saved because the data changed at the same moment. "
    "Nothing was written. Please reload and try again."
)
_FROZEN_MESSAGE = (
    "This fee plan is not a draft, so its definition cannot be changed. Once a plan has "
    "been activated its name, items and amounts are permanent — create a new plan for a "
    "different fee structure."
)
_ITEM_REMOVED_MESSAGE = (
    "This item has already been removed. Removed items are kept as history and cannot be "
    "changed."
)
_NAME_TAKEN_MESSAGE = (
    "Another fee plan already uses this name. Plan names are unique across draft, active "
    "and archived plans."
)
_LABEL_TAKEN_MESSAGE = (
    "This plan already has an item with this label. Give each item its own label."
)
_ITEM_LIMIT_MESSAGE = (
    f"A fee plan can have at most {MAX_ACTIVE_FEE_PLAN_ITEMS} items. Remove one before "
    "adding another."
)
_NO_ITEMS_MESSAGE = "Add at least one item before activating this fee plan."
_DUPLICATE_LABELS_MESSAGE = (
    "Two items in this plan share a label. Rename or remove one before activating."
)
_NOT_ACTIVATABLE_MESSAGE = (
    "Only a draft fee plan can be activated. An activated plan stays frozen for good."
)
_NOT_ARCHIVABLE_MESSAGE = "This fee plan is already archived."
_NOT_REACTIVATABLE_MESSAGE = (
    "Only an archived plan that was active before can be reactivated. A draft that was "
    "archived before it was ever activated stays archived."
)
_ACTIVATE_CONFIRM_MESSAGE = (
    "Please tick the confirmation box before activating. Activation cannot be undone."
)
_ARCHIVE_CONFIRM_MESSAGE = "Please tick the confirmation box before archiving."
_REACTIVATE_CONFIRM_MESSAGE = "Please tick the confirmation box before reactivating."
_NO_CHANGES_MESSAGE = "Nothing was changed, so nothing was saved."
_CREATED_MESSAGE = (
    "Fee plan created as a draft. Add its items, then activate it when it is complete."
)
_SAVED_MESSAGE = "Fee plan saved."
_ITEM_ADDED_MESSAGE = "Item added."
_ITEM_SAVED_MESSAGE = "Item saved."
_ITEM_REMOVED_OK_MESSAGE = "Item removed. It is kept below as history."
_ACTIVATED_MESSAGE = "Fee plan activated. Its definition is now permanent."
_ARCHIVED_MESSAGE = (
    "Fee plan archived. It is no longer available, and its definition is unchanged."
)
_REACTIVATED_MESSAGE = (
    "Fee plan reactivated. Its definition is unchanged and still cannot be edited."
)


# ======================================================================
# URLs and token state
# ======================================================================


def _list_url():
    return url_for("admin.fee_plans_list")


def _new_url():
    return url_for("admin.fee_plan_create")


def _detail_url(plan_public_id):
    return url_for("admin.fee_plan_detail", plan_public_id=plan_public_id)


def _edit_url(plan_public_id):
    return url_for("admin.fee_plan_edit", plan_public_id=plan_public_id)


def _item_new_url(plan_public_id):
    return url_for("admin.fee_plan_item_create", plan_public_id=plan_public_id)


def _item_edit_url(plan_public_id, item_public_id):
    return url_for(
        "admin.fee_plan_item_edit",
        plan_public_id=plan_public_id,
        item_public_id=item_public_id,
    )


def _plan_state(plan):
    return {
        "plan_public_id": plan.public_id,
        "plan_version": plan.version,
        "plan_status": plan.status,
    }


def _item_state(plan, item):
    return dict(
        _plan_state(plan),
        item_public_id=item.public_id,
        item_version=item.version,
        item_status=item.status,
    )


# ======================================================================
# Shared rejection handling
# ======================================================================


def _fresh_admin_authorization(actor_id):
    """Prove from **current database state** that `actor_id` is still an
    active Administrator, for a path that has rolled back and released its
    locks. The actor is a scalar id captured before the reset, never
    ``current_user``."""
    actor = db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    if actor is None or actor.role != _ADMINISTRATOR or actor.status != _USER_ACTIVE:
        abort(404)


def _reject(message, url, actor_id, level="danger"):
    """Roll back first, re-authorize from current state, then flash and
    redirect to a plain GET."""
    db.session.rollback()
    _fresh_admin_authorization(actor_id)
    flash(message, level)
    return redirect(url)


def _plan_or_404(plan_public_id):
    plan = admin_fee_plan(plan_public_id)
    if plan is None:
        abort(404)
    return plan


def _item_or_404(plan, item_public_id):
    item = fee_plan_item(plan.id, item_public_id)
    if item is None:
        abort(404)
    return item


def _locked_plan_or_404(locks, plan_public_id):
    """The locked plan, after re-proving the locked actor. Any failure rolls
    back and 404s without saying which condition failed."""
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)
    plan = locks.plan
    if plan is None or plan.public_id != plan_public_id:
        db.session.rollback()
        abort(404)
    return plan


def _locked_item_or_404(locks, plan, item_id, item_public_id):
    item = locks.items.get(item_id)
    if item is None or item.fee_plan_id != plan.id or item.public_id != item_public_id:
        db.session.rollback()
        abort(404)
    return item


# ======================================================================
# The catalogue
# ======================================================================


@admin_bp.get("/fee-plans")
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plans_list():
    """One bounded page of fee plans, newest first, optionally filtered by
    lifecycle status. Two queries whatever the page holds; no ``COUNT``."""
    page = normalize_page(request.args.get("page"))
    status = normalize_status_filter(request.args.get("status"))

    rows, has_next = fee_plans_page(page, status=status)
    if not rows and page > 1:
        page = 1
        rows, has_next = fee_plans_page(page, status=status)
    summaries = active_item_summaries([row.id for row in rows])

    return render_template(
        "admin/fee_plans/list.html",
        plans=build_plan_list_view(rows, summaries, _tz_name()),
        status_labels=STATUS_LABELS,
        filter_status=status or "",
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        page_size=PAGE_SIZE,
        currency_code=CURRENCY_CODE,
        tz_name=_tz_name(),
    )


@admin_bp.get("/fee-plans/<plan_public_id>")
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_detail(plan_public_id):
    """One plan in full, with whichever controls its current state allows.

    A draft offers editing, item changes, activation and archiving; an
    active plan offers archiving; an archived plan that was active before
    offers reactivation; an archived draft offers nothing. A token is minted
    only for a control that is shown.
    """
    plan = _plan_or_404(plan_public_id)
    view = build_plan_detail_view(plan, _tz_name())
    actor_public_id = current_user.public_id
    state = _plan_state(plan)

    activate_token = archive_token = reactivate_token = None
    if plan.status == _DRAFT:
        activate_token = tokens.make_token(
            tokens.PURPOSE_ACTIVATE, actor_public_id=actor_public_id, **state
        )
        for item in view["active_items"]:
            item["edit_url"] = _item_edit_url(plan.public_id, item["public_id"])
            item["remove_token"] = tokens.make_token(
                tokens.PURPOSE_ITEM_REMOVE,
                actor_public_id=actor_public_id,
                item_public_id=item["public_id"],
                item_version=item["version"],
                item_status=item["status"],
                **state,
            )
    if plan.status in (_DRAFT, _ACTIVE):
        archive_token = tokens.make_token(
            tokens.PURPOSE_ARCHIVE, actor_public_id=actor_public_id, **state
        )
    if plan.status == _ARCHIVED and plan.first_activated_at is not None:
        reactivate_token = tokens.make_token(
            tokens.PURPOSE_REACTIVATE, actor_public_id=actor_public_id, **state
        )

    can_edit = plan.status == _DRAFT
    return render_template(
        "admin/fee_plans/detail.html",
        plan=view,
        activate_token=activate_token,
        archive_token=archive_token,
        reactivate_token=reactivate_token,
        edit_url=_edit_url(plan.public_id) if can_edit else None,
        item_new_url=(
            _item_new_url(plan.public_id)
            if can_edit and view["item_count"] < MAX_ACTIVE_FEE_PLAN_ITEMS
            else None
        ),
        list_url=_list_url(),
        state_field=_STATE_FIELD,
        tz_name=_tz_name(),
    )


# ======================================================================
# Plan create and edit
# ======================================================================


def _render_plan_form(form, plan=None):
    """Render the create / edit page with a token minted from **current**
    persisted state -- only ever reached when the submitted token, if any,
    still describes that state."""
    if plan is None:
        token = tokens.make_token(
            tokens.PURPOSE_CREATE, actor_public_id=current_user.public_id
        )
    else:
        token = tokens.make_token(
            tokens.PURPOSE_EDIT, actor_public_id=current_user.public_id, **_plan_state(plan)
        )
    return render_template(
        "admin/fee_plans/form.html",
        form=form,
        plan=None if plan is None else build_plan_header_view(plan),
        state_field=_STATE_FIELD,
        state_token=token,
        name_max=FEE_PLAN_NAME_MAX_LENGTH,
        description_max=FEE_PLAN_DESCRIPTION_MAX_LENGTH,
        currency_code=CURRENCY_CODE,
        cancel_url=_list_url() if plan is None else _detail_url(plan.public_id),
    )


@admin_bp.route("/fee-plans/new", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_create():
    """Create one draft fee plan with no items."""
    form = FeePlanForm(formdata=request.form if request.method == "POST" else None)
    if request.method == "GET":
        return _render_plan_form(form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    new_url = _new_url()

    if tokens.token_is_stale(token, tokens.PURPOSE_CREATE, actor_public_id=actor_public_id):
        return _reject(_STALE_MESSAGE, new_url, actor_id)
    if not form.validate_on_submit():
        return _render_plan_form(form)
    name, description = form.normalized_name, form.normalized_description
    if plan_name_taken(name):
        form.name.errors.append(_NAME_TAKEN_MESSAGE)
        return _render_plan_form(form)

    locks = lock_fee_plan_chain(actor_id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)
    if tokens.token_is_stale(token, tokens.PURPOSE_CREATE, actor_public_id=actor_public_id):
        return _reject(_STALE_MESSAGE, new_url, actor_id)
    if plan_name_taken(name):
        return _reject(_NAME_TAKEN_MESSAGE, new_url, actor_id, "warning")

    moment = _write_moment()
    plan = FeePlan(
        name=name,
        description=description,
        currency_code=CURRENCY_CODE,
        status=_DRAFT,
        created_by_id=actor_id,
        first_activated_at=None,
        first_activated_by_id=None,
        status_changed_at=None,
        status_changed_by_id=None,
        version=1,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(plan)
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, new_url, actor_id)

    flash(_CREATED_MESSAGE, "success")
    return redirect(_detail_url(plan.public_id))


@admin_bp.route("/fee-plans/<plan_public_id>/edit", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_edit(plan_public_id):
    """Change a **draft** plan's name or description. A save that changes
    neither is a no-op: no version and no timestamp moves."""
    plan = _plan_or_404(plan_public_id)
    detail_url = _detail_url(plan_public_id)
    if plan.status != _DRAFT:
        flash(_FROZEN_MESSAGE, "warning")
        return redirect(detail_url)

    form = FeePlanForm(
        formdata=request.form if request.method == "POST" else None,
        data={"name": plan.name, "description": plan.description or ""},
    )
    if request.method == "GET":
        return _render_plan_form(form, plan)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    edit_url = _edit_url(plan_public_id)

    if tokens.token_is_stale(
        token, tokens.PURPOSE_EDIT, actor_public_id=actor_public_id, **_plan_state(plan)
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if not form.validate_on_submit():
        return _render_plan_form(form, plan)
    name, description = form.normalized_name, form.normalized_description
    if name != plan.name and plan_name_taken(name, exclude_plan_id=plan.id):
        form.name.errors.append(_NAME_TAKEN_MESSAGE)
        return _render_plan_form(form, plan)
    plan_id = plan.id

    locks = lock_fee_plan_chain(actor_id, plan_id=plan_id)
    locked = _locked_plan_or_404(locks, plan_public_id)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_EDIT, actor_public_id=actor_public_id, **_plan_state(locked)
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if locked.status != _DRAFT or locked.first_activated_at is not None:
        return _reject(_FROZEN_MESSAGE, detail_url, actor_id, "warning")
    if locked.name == name and locked.description == description:
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(detail_url)
    if locked.name != name and plan_name_taken(name, exclude_plan_id=locked.id):
        return _reject(_NAME_TAKEN_MESSAGE, edit_url, actor_id, "warning")

    moment = _write_moment()
    locked.name = name
    locked.description = description
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, edit_url, actor_id)

    flash(_SAVED_MESSAGE, "success")
    return redirect(detail_url)


# ======================================================================
# Items of a draft plan
# ======================================================================


def _render_item_form(form, plan, item=None):
    if item is None:
        token = tokens.make_token(
            tokens.PURPOSE_ITEM_CREATE,
            actor_public_id=current_user.public_id,
            **_plan_state(plan),
        )
    else:
        token = tokens.make_token(
            tokens.PURPOSE_ITEM_EDIT,
            actor_public_id=current_user.public_id,
            **_item_state(plan, item),
        )
    return render_template(
        "admin/fee_plans/item_form.html",
        form=form,
        plan=build_plan_header_view(plan),
        editing=item is not None,
        state_field=_STATE_FIELD,
        state_token=token,
        label_max=FEE_PLAN_ITEM_LABEL_MAX_LENGTH,
        min_amount=format_amount(MIN_AMOUNT),
        max_amount=format_amount(MAX_AMOUNT),
        amount_scale=AMOUNT_SCALE,
        item_limit=MAX_ACTIVE_FEE_PLAN_ITEMS,
        currency_code=CURRENCY_CODE,
        cancel_url=_detail_url(plan.public_id),
    )


@admin_bp.route("/fee-plans/<plan_public_id>/items/new", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_item_create(plan_public_id):
    """Add one item to a **draft** plan, within the item limit and with a
    label no other active item of the plan uses."""
    plan = _plan_or_404(plan_public_id)
    detail_url = _detail_url(plan_public_id)
    if plan.status != _DRAFT:
        flash(_FROZEN_MESSAGE, "warning")
        return redirect(detail_url)

    form = FeePlanItemForm(formdata=request.form if request.method == "POST" else None)
    if request.method == "GET":
        if active_item_count(plan.id) >= MAX_ACTIVE_FEE_PLAN_ITEMS:
            flash(_ITEM_LIMIT_MESSAGE, "warning")
            return redirect(detail_url)
        return _render_item_form(form, plan)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    new_url = _item_new_url(plan_public_id)

    if tokens.token_is_stale(
        token, tokens.PURPOSE_ITEM_CREATE, actor_public_id=actor_public_id, **_plan_state(plan)
    ):
        return _reject(_STALE_MESSAGE, new_url, actor_id)
    if not form.validate_on_submit():
        return _render_item_form(form, plan)
    kind, label, amount = form.kind.data, form.normalized_label, form.parsed_amount
    if active_label_taken(plan.id, label):
        form.label.errors.append(_LABEL_TAKEN_MESSAGE)
        return _render_item_form(form, plan)
    if active_item_count(plan.id) >= MAX_ACTIVE_FEE_PLAN_ITEMS:
        return _reject(_ITEM_LIMIT_MESSAGE, detail_url, actor_id, "warning")
    plan_id = plan.id

    locks = lock_fee_plan_chain(actor_id, plan_id=plan_id, include_active_items=True)
    locked = _locked_plan_or_404(locks, plan_public_id)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_CREATE,
        actor_public_id=actor_public_id,
        **_plan_state(locked),
    ):
        return _reject(_STALE_MESSAGE, new_url, actor_id)
    if locked.status != _DRAFT or locked.first_activated_at is not None:
        return _reject(_FROZEN_MESSAGE, detail_url, actor_id, "warning")
    siblings = locked_active_items(locks)
    if len(siblings) >= MAX_ACTIVE_FEE_PLAN_ITEMS:
        return _reject(_ITEM_LIMIT_MESSAGE, detail_url, actor_id, "warning")
    if label_in_use(siblings, label):
        return _reject(_LABEL_TAKEN_MESSAGE, new_url, actor_id, "warning")

    moment = _write_moment()
    db.session.add(
        FeePlanItem(
            fee_plan_id=locked.id,
            kind=kind,
            label=label,
            amount=amount,
            status=_ITEM_ACTIVE,
            removed_at=None,
            removed_by_id=None,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
    )
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, new_url, actor_id)

    flash(_ITEM_ADDED_MESSAGE, "success")
    return redirect(detail_url)


@admin_bp.route(
    "/fee-plans/<plan_public_id>/items/<item_public_id>/edit", methods=["GET", "POST"]
)
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_item_edit(plan_public_id, item_public_id):
    """Change one active item of a **draft** plan. A save whose kind, label
    and exact amount all equal the stored ones is a no-op."""
    plan = _plan_or_404(plan_public_id)
    item = _item_or_404(plan, item_public_id)
    detail_url = _detail_url(plan_public_id)
    if plan.status != _DRAFT:
        flash(_FROZEN_MESSAGE, "warning")
        return redirect(detail_url)
    if item.status != _ITEM_ACTIVE:
        flash(_ITEM_REMOVED_MESSAGE, "warning")
        return redirect(detail_url)

    form = FeePlanItemForm(
        formdata=request.form if request.method == "POST" else None,
        data={
            "kind": item.kind,
            "label": item.label,
            "amount": amount_input_text(item.amount),
        },
    )
    if request.method == "GET":
        return _render_item_form(form, plan, item)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    edit_url = _item_edit_url(plan_public_id, item_public_id)

    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_EDIT,
        actor_public_id=actor_public_id,
        **_item_state(plan, item),
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if not form.validate_on_submit():
        return _render_item_form(form, plan, item)
    kind, label, amount = form.kind.data, form.normalized_label, form.parsed_amount
    if active_label_taken(plan.id, label, exclude_item_id=item.id):
        form.label.errors.append(_LABEL_TAKEN_MESSAGE)
        return _render_item_form(form, plan, item)
    plan_id, item_id = plan.id, item.id

    locks = lock_fee_plan_chain(
        actor_id, plan_id=plan_id, item_ids=(item_id,), include_active_items=True
    )
    locked = _locked_plan_or_404(locks, plan_public_id)
    locked_item = _locked_item_or_404(locks, locked, item_id, item_public_id)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_EDIT,
        actor_public_id=actor_public_id,
        **_item_state(locked, locked_item),
    ):
        return _reject(_STALE_MESSAGE, edit_url, actor_id)
    if locked.status != _DRAFT or locked.first_activated_at is not None:
        return _reject(_FROZEN_MESSAGE, detail_url, actor_id, "warning")
    if locked_item.status != _ITEM_ACTIVE:
        return _reject(_ITEM_REMOVED_MESSAGE, detail_url, actor_id, "warning")
    if label_in_use(locked_active_items(locks), label, exclude_item_id=item_id):
        return _reject(_LABEL_TAKEN_MESSAGE, edit_url, actor_id, "warning")
    if (
        locked_item.kind == kind
        and locked_item.label == label
        and locked_item.amount == amount
    ):
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(detail_url)

    moment = _write_moment()
    locked_item.kind = kind
    locked_item.label = label
    locked_item.amount = amount
    locked_item.version = locked_item.version + 1
    locked_item.updated_at = moment
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, edit_url, actor_id)

    flash(_ITEM_SAVED_MESSAGE, "success")
    return redirect(detail_url)


@admin_bp.post("/fee-plans/<plan_public_id>/items/<item_public_id>/remove")
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_item_remove(plan_public_id, item_public_id):
    """Mark one active item of a **draft** plan ``removed``. The row stays,
    with its removal attribution, as history; nothing is deleted."""
    plan = _plan_or_404(plan_public_id)
    item = _item_or_404(plan, item_public_id)
    detail_url = _detail_url(plan_public_id)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)

    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_REMOVE,
        actor_public_id=actor_public_id,
        **_item_state(plan, item),
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    plan_id, item_id = plan.id, item.id

    locks = lock_fee_plan_chain(actor_id, plan_id=plan_id, item_ids=(item_id,))
    locked = _locked_plan_or_404(locks, plan_public_id)
    locked_item = _locked_item_or_404(locks, locked, item_id, item_public_id)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ITEM_REMOVE,
        actor_public_id=actor_public_id,
        **_item_state(locked, locked_item),
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    if locked.status != _DRAFT or locked.first_activated_at is not None:
        return _reject(_FROZEN_MESSAGE, detail_url, actor_id, "warning")
    if locked_item.status != _ITEM_ACTIVE:
        return _reject(_ITEM_REMOVED_MESSAGE, detail_url, actor_id, "warning")

    moment = _write_moment()
    locked_item.status = _ITEM_REMOVED
    locked_item.removed_at = moment
    locked_item.removed_by_id = actor_id
    locked_item.version = locked_item.version + 1
    locked_item.updated_at = moment
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    flash(_ITEM_REMOVED_OK_MESSAGE, "success")
    return redirect(detail_url)


# ======================================================================
# Lifecycle
# ======================================================================


@admin_bp.post("/fee-plans/<plan_public_id>/activate")
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_activate(plan_public_id):
    """Activate a **draft** plan with at least one active item, freezing its
    whole definition permanently."""
    plan = _plan_or_404(plan_public_id)
    detail_url = _detail_url(plan_public_id)
    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)

    if request.form.get("confirm") != "yes":
        flash(_ACTIVATE_CONFIRM_MESSAGE, "warning")
        return redirect(detail_url)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_ACTIVATE, actor_public_id=actor_public_id, **_plan_state(plan)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    plan_id = plan.id

    locks = lock_fee_plan_chain(actor_id, plan_id=plan_id, include_active_items=True)
    locked = _locked_plan_or_404(locks, plan_public_id)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_ACTIVATE, actor_public_id=actor_public_id, **_plan_state(locked)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    if locked.status != _DRAFT or locked.first_activated_at is not None:
        return _reject(_NOT_ACTIVATABLE_MESSAGE, detail_url, actor_id, "warning")
    items = locked_active_items(locks)
    if not items:
        return _reject(_NO_ITEMS_MESSAGE, detail_url, actor_id, "warning")
    if len(items) > MAX_ACTIVE_FEE_PLAN_ITEMS:
        return _reject(_ITEM_LIMIT_MESSAGE, detail_url, actor_id, "warning")
    if not labels_are_unique(items):
        return _reject(_DUPLICATE_LABELS_MESSAGE, detail_url, actor_id, "warning")

    moment = _write_moment()
    locked.status = _ACTIVE
    locked.first_activated_at = moment
    locked.first_activated_by_id = actor_id
    locked.status_changed_at = moment
    locked.status_changed_by_id = actor_id
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    flash(_ACTIVATED_MESSAGE, "success")
    return redirect(detail_url)


@admin_bp.post("/fee-plans/<plan_public_id>/archive")
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_archive(plan_public_id):
    """Archive a draft or active plan. Availability changes; no item and no
    amount does."""
    plan = _plan_or_404(plan_public_id)
    detail_url = _detail_url(plan_public_id)
    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)

    if request.form.get("confirm") != "yes":
        flash(_ARCHIVE_CONFIRM_MESSAGE, "warning")
        return redirect(detail_url)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_ARCHIVE, actor_public_id=actor_public_id, **_plan_state(plan)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    plan_id = plan.id

    locks = lock_fee_plan_chain(actor_id, plan_id=plan_id)
    locked = _locked_plan_or_404(locks, plan_public_id)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_ARCHIVE, actor_public_id=actor_public_id, **_plan_state(locked)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    if locked.status not in (_DRAFT, _ACTIVE):
        return _reject(_NOT_ARCHIVABLE_MESSAGE, detail_url, actor_id, "warning")

    moment = _write_moment()
    locked.status = _ARCHIVED
    locked.status_changed_at = moment
    locked.status_changed_by_id = actor_id
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    flash(_ARCHIVED_MESSAGE, "success")
    return redirect(detail_url)


@admin_bp.post("/fee-plans/<plan_public_id>/reactivate")
@roles_required(_ADMINISTRATOR)
@_financial_response
def fee_plan_reactivate(plan_public_id):
    """Restore ``active`` to an archived plan that was active before. Its
    definition stays frozen; nothing becomes editable."""
    plan = _plan_or_404(plan_public_id)
    detail_url = _detail_url(plan_public_id)
    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)

    if request.form.get("confirm") != "yes":
        flash(_REACTIVATE_CONFIRM_MESSAGE, "warning")
        return redirect(detail_url)
    if tokens.token_is_stale(
        token, tokens.PURPOSE_REACTIVATE, actor_public_id=actor_public_id, **_plan_state(plan)
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    plan_id = plan.id

    locks = lock_fee_plan_chain(actor_id, plan_id=plan_id)
    locked = _locked_plan_or_404(locks, plan_public_id)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_REACTIVATE,
        actor_public_id=actor_public_id,
        **_plan_state(locked),
    ):
        return _reject(_STALE_MESSAGE, detail_url, actor_id)
    if locked.status != _ARCHIVED or locked.first_activated_at is None:
        return _reject(_NOT_REACTIVATABLE_MESSAGE, detail_url, actor_id, "warning")

    moment = _write_moment()
    locked.status = _ACTIVE
    locked.status_changed_at = moment
    locked.status_changed_by_id = actor_id
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, detail_url, actor_id)

    flash(_REACTIVATED_MESSAGE, "success")
    return redirect(detail_url)
