"""Administrator fee assignments by Enrollment (Phase 5 / M03).

Four URL rules, nested under the Group membership workflow and addressed only
by public identifiers::

    GET       /admin/groups/<gp>/enrollments/<ep>/fee-assignments
    GET       /admin/groups/<gp>/enrollments/<ep>/fee-plans
    GET|POST  /admin/groups/<gp>/enrollments/<ep>/fee-plans/<pp>/assign
    POST      /admin/groups/<gp>/enrollments/<ep>/fee-assignments/<ap>/cancel

**A fee plan is assigned to an Enrollment**, never to a Student, Group, Course
or Academic Term: the Enrollment names the exact registration the fees apply
to. An Enrollment has at most one ``assigned`` plan at a time. Cancellation is
explicit, keeps the row as history, and is the only change an assignment ever
undergoes; assigning again inserts a new row. Nothing here creates an invoice,
payment, receipt or provider record, and nothing assigns, cancels, copies or
transfers a plan automatically.

**This is the only surface that reads or writes a fee assignment.** There is
no Student, Teacher, Researcher or public endpoint for one. Every route is
gated by ``roles_required``; every write re-proves the acting account against
its locked row.

**Every mutation** is POST-only and CSRF-protected, carries a purpose-specific
signed token (``app/services/student_fee_assignment_tokens.py``) minted by the
GET page that shows exactly what will change, runs the one lock chain in
``app/services/student_fee_assignment_transactions.py``, and re-proves the
actor, the nesting, the academic chain, the token, every lifecycle rule, the
plan's items and the one-assigned-plan rule against the locked rows. An
``IntegrityError`` is rolled back first, the actor is re-authorized from
current state, and one generic sentence is shown.

A URL naming an unknown Group, an Enrollment outside it or referencing a
non-Student account, a Fee Plan that is not active, or an assignment of
another Enrollment is a plain 404.

Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``.
"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plans import _financial_response, _reject, _tz_name, _write_moment
from app.extensions import db
from app.models import StudentFeeAssignment, StudentFeeAssignmentStatus, UserRole
from app.security.decorators import roles_required
from app.services import student_fee_assignment_tokens as tokens
from app.services.fee_plan_queries import active_item_summaries, normalize_page
from app.services.fee_plan_transactions import administrator_authz_broken, locked_active_items
from app.services.invoice_queries import assignments_with_open_invoice
from app.services.invoice_transactions import lock_assignment_invoices, open_invoices
from app.services.money import CURRENCY_CODE
from app.services.student_fee_assignment_queries import (
    BLOCK_ACADEMIC_INACTIVE,
    BLOCK_ALREADY_ASSIGNED,
    BLOCK_ENROLLMENT_INACTIVE,
    BLOCK_STUDENT_INACTIVE,
    PAGE_SIZE,
    assignable_fee_plan,
    assignable_plans_page,
    assignment_block_reason,
    build_enrollment_context_view,
    build_history_view,
    build_plan_choice_view,
    build_plan_confirmation_view,
    context_block_reason,
    enrollment_fee_assignment,
    enrollment_fee_context,
    fee_assignment_history_page,
    fee_plan_active_items,
)
from app.services.student_fee_assignment_transactions import (
    assigned_rows,
    enrollment_nesting_broken,
    fee_plan_assignable,
    fee_plan_items_valid,
    hierarchy_moved,
    latest_change,
    locked_academic_statuses,
    locked_enrollment_assignments,
    lock_fee_assignment_chain,
)

_ASSIGNED = StudentFeeAssignmentStatus.ASSIGNED.value
_CANCELLED = StudentFeeAssignmentStatus.CANCELLED.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

#: The hidden form field every mutating form carries its signed token in.
_STATE_FIELD = "state_token"

_BASE = "/groups/<group_public_id>/enrollments/<enrollment_public_id>"


# ======================================================================
# Administrator-facing sentences, declared once each
# ======================================================================

_STALE_MESSAGE = (
    "This enrollment's fees or the fee plan changed after this page was opened, or the page "
    "has expired. Nothing was saved. Please reload, review the current state, and try again."
)
_INTEGRITY_MESSAGE = (
    "That change could not be saved because the data changed at the same moment. "
    "Nothing was written. Please reload and try again."
)
_BLOCK_MESSAGES = {
    BLOCK_ENROLLMENT_INACTIVE: (
        "This enrollment is withdrawn. A fee plan can only be assigned to an active enrollment."
    ),
    BLOCK_STUDENT_INACTIVE: (
        "This student's account is not active, so a fee plan cannot be assigned."
    ),
    BLOCK_ACADEMIC_INACTIVE: (
        "This enrollment's group, course, level or academic term is archived, so a fee plan "
        "cannot be assigned."
    ),
    BLOCK_ALREADY_ASSIGNED: (
        "This enrollment already has an assigned fee plan. Cancel that assignment explicitly "
        "before assigning another plan."
    ),
}
_PLAN_UNAVAILABLE_MESSAGE = (
    "This fee plan is not available for assignment. Only an active fee plan can be assigned."
)
_PLAN_ITEMS_INVALID_MESSAGE = (
    "This fee plan does not have a valid set of items, so it cannot be assigned."
)
_ALREADY_CANCELLED_MESSAGE = "This fee assignment is already cancelled. Nothing was changed."
_ASSIGNED_OK_MESSAGE = "Fee plan assigned to this enrollment."
_CANCELLED_OK_MESSAGE = (
    "Fee assignment cancelled. It is kept below as history, and a fee plan can be assigned again."
)
#: Phase 5 / M04. Cancelling an assignment never cancels its invoice.
_OPEN_INVOICE_BLOCKS_CANCELLATION = (
    "This fee assignment has a draft or issued invoice. Cancel that invoice explicitly from the "
    "assignment's Invoices page before cancelling the fee assignment."
)


# ======================================================================
# URLs and shared handling
# ======================================================================


def _history_url(group_public_id, enrollment_public_id):
    return url_for(
        "admin.enrollment_fee_assignments",
        group_public_id=group_public_id,
        enrollment_public_id=enrollment_public_id,
    )


def _choices_url(group_public_id, enrollment_public_id):
    return url_for(
        "admin.enrollment_fee_plan_choices",
        group_public_id=group_public_id,
        enrollment_public_id=enrollment_public_id,
    )


def _assign_url(group_public_id, enrollment_public_id, plan_public_id):
    return url_for(
        "admin.enrollment_fee_assignment_create",
        group_public_id=group_public_id,
        enrollment_public_id=enrollment_public_id,
        plan_public_id=plan_public_id,
    )


def _cancel_url(group_public_id, enrollment_public_id, assignment_public_id):
    return url_for(
        "admin.enrollment_fee_assignment_cancel",
        group_public_id=group_public_id,
        enrollment_public_id=enrollment_public_id,
        assignment_public_id=assignment_public_id,
    )


def _context_or_404(group_public_id, enrollment_public_id):
    context = enrollment_fee_context(group_public_id, enrollment_public_id)
    if context is None:
        abort(404)
    return context


def _lock(context, actor_id, **links):
    """The M03 chain for `context`'s Enrollment. `context` is a plain row
    captured before the chain's reset, so it stays readable afterwards."""
    return lock_fee_assignment_chain(
        context.group_public_id,
        context.academic_term_id,
        context.level_id,
        context.course_id,
        context.student_id,
        context.enrollment_id,
        actor_id,
        **links,
    )


def _prove_actor_and_nesting(locks, context):
    """Re-prove the locked actor and the Group -> Enrollment -> Student
    nesting. Any failure rolls back and 404s without saying which."""
    if administrator_authz_broken(locks) or enrollment_nesting_broken(
        locks, context.group_public_id, context.enrollment_id
    ):
        db.session.rollback()
        abort(404)


def _hierarchy_moved(locks, context):
    return hierarchy_moved(locks, context.academic_term_id, context.level_id, context.course_id)


# ======================================================================
# Read-only pages
# ======================================================================


@admin_bp.get(_BASE + "/fee-assignments")
@roles_required(_ADMINISTRATOR)
@_financial_response
def enrollment_fee_assignments(group_public_id, enrollment_public_id):
    """One Enrollment's fee assignments, newest first. Four queries whatever
    the history holds; no ``COUNT``. A cancel token is minted only for a row
    that is still assigned."""
    context = _context_or_404(group_public_id, enrollment_public_id)
    page = normalize_page(request.args.get("page"))
    rows, has_next = fee_assignment_history_page(context.enrollment_id, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = fee_assignment_history_page(context.enrollment_id, page)
    summaries = active_item_summaries(sorted({row.fee_plan_id for row in rows}))
    history = build_history_view(rows, summaries, _tz_name())

    actor_public_id = current_user.public_id
    # Phase 5 / M04: an assignment holding a draft or issued invoice offers no
    # cancel control; the page says why and links to its invoices.
    blocked = assignments_with_open_invoice([entry["public_id"] for entry in history])
    for entry in history:
        entry["plan_url"] = url_for("admin.fee_plan_detail", plan_public_id=entry["plan_public_id"])
        entry["invoices_url"] = url_for(
            "admin.assignment_invoices",
            group_public_id=group_public_id,
            enrollment_public_id=enrollment_public_id,
            assignment_public_id=entry["public_id"],
        )
        if entry["status"] == _ASSIGNED and entry["public_id"] in blocked:
            entry["cancel_blocked"] = True
        elif entry["status"] == _ASSIGNED:
            entry["cancel_url"] = _cancel_url(
                group_public_id, enrollment_public_id, entry["public_id"]
            )
            entry["cancel_token"] = tokens.make_token(
                tokens.PURPOSE_CANCEL,
                actor_public_id=actor_public_id,
                enrollment_public_id=context.enrollment_public_id,
                assignment_public_id=entry["public_id"],
                assignment_version=entry["version"],
            )

    block = context_block_reason(context)
    return render_template(
        "admin/fee_assignments/history.html",
        context=build_enrollment_context_view(context),
        history=history,
        block_message=_BLOCK_MESSAGES.get(block),
        choices_url=None if block else _choices_url(group_public_id, enrollment_public_id),
        members_url=url_for("admin.group_members", group_public_id=group_public_id),
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        page_size=PAGE_SIZE,
        state_field=_STATE_FIELD,
        currency_code=CURRENCY_CODE,
        tz_name=_tz_name(),
    )


@admin_bp.get(_BASE + "/fee-plans")
@roles_required(_ADMINISTRATOR)
@_financial_response
def enrollment_fee_plan_choices(group_public_id, enrollment_public_id):
    """One bounded page of active fee plans to choose from. Drafts and
    archived plans are never listed."""
    context = _context_or_404(group_public_id, enrollment_public_id)
    history_url = _history_url(group_public_id, enrollment_public_id)
    block = context_block_reason(context)
    if block is not None:
        flash(_BLOCK_MESSAGES[block], "warning")
        return redirect(history_url)

    page = normalize_page(request.args.get("page"))
    rows, has_next = assignable_plans_page(page)
    if not rows and page > 1:
        page = 1
        rows, has_next = assignable_plans_page(page)
    plans = build_plan_choice_view(rows, active_item_summaries([row.id for row in rows]))
    for plan in plans:
        plan["assign_url"] = (
            _assign_url(group_public_id, enrollment_public_id, plan["public_id"])
            if plan["selectable"]
            else None
        )

    return render_template(
        "admin/fee_assignments/choose_plan.html",
        context=build_enrollment_context_view(context),
        plans=plans,
        history_url=history_url,
        page=page,
        has_prev=page > 1,
        has_next=has_next,
        page_size=PAGE_SIZE,
        currency_code=CURRENCY_CODE,
    )


# ======================================================================
# Assignment
# ======================================================================


@admin_bp.route(_BASE + "/fee-plans/<plan_public_id>/assign", methods=["GET", "POST"])
@roles_required(_ADMINISTRATOR)
@_financial_response
def enrollment_fee_assignment_create(group_public_id, enrollment_public_id, plan_public_id):
    """GET: the confirmation page -- the Enrollment and the plan exactly as
    it would be assigned, with a token binding both. POST: assign it."""
    context = _context_or_404(group_public_id, enrollment_public_id)
    plan = assignable_fee_plan(plan_public_id)
    if plan is None:
        abort(404)
    history_url = _history_url(group_public_id, enrollment_public_id)

    if request.method == "GET":
        block = context_block_reason(context)
        if block is not None:
            flash(_BLOCK_MESSAGES[block], "warning")
            return redirect(history_url)
        items = fee_plan_active_items(plan.id)
        if not fee_plan_items_valid(items):
            flash(_PLAN_ITEMS_INVALID_MESSAGE, "warning")
            return redirect(_choices_url(group_public_id, enrollment_public_id))
        state_token = tokens.make_token(
            tokens.PURPOSE_ASSIGN,
            actor_public_id=current_user.public_id,
            enrollment_public_id=context.enrollment_public_id,
            plan_public_id=plan.public_id,
            plan_version=plan.version,
        )
        return render_template(
            "admin/fee_assignments/confirm.html",
            context=build_enrollment_context_view(context),
            plan=build_plan_confirmation_view(plan, items),
            assign_url=_assign_url(group_public_id, enrollment_public_id, plan.public_id),
            choices_url=_choices_url(group_public_id, enrollment_public_id),
            history_url=history_url,
            state_field=_STATE_FIELD,
            state_token=state_token,
        )

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    bound = {
        "actor_public_id": actor_public_id,
        "enrollment_public_id": context.enrollment_public_id,
        "plan_public_id": plan.public_id,
    }
    if tokens.token_is_stale(token, tokens.PURPOSE_ASSIGN, plan_version=plan.version, **bound):
        return _reject(_STALE_MESSAGE, history_url, actor_id)
    plan_id = plan.id

    locks = _lock(
        context,
        actor_id,
        plan_id=plan_id,
        include_plan_items=True,
        include_enrollment_assignments=True,
    )
    _prove_actor_and_nesting(locks, context)
    if locks.plan is None or locks.plan.public_id != plan_public_id:
        db.session.rollback()
        abort(404)
    if _hierarchy_moved(locks, context):
        return _reject(_STALE_MESSAGE, history_url, actor_id)
    existing = locked_enrollment_assignments(locks)
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_ASSIGN,
        changed_at=latest_change(existing),
        plan_version=locks.plan.version,
        **bound,
    ):
        return _reject(_STALE_MESSAGE, history_url, actor_id)

    block = assignment_block_reason(
        locks.enrollment.status,
        locks.student.status,
        locked_academic_statuses(
            locks, context.academic_term_id, context.level_id, context.course_id
        ),
        has_assigned_plan=False,
    )
    if block is not None:
        return _reject(_BLOCK_MESSAGES[block], history_url, actor_id, "warning")
    if not fee_plan_assignable(locks.plan):
        return _reject(_PLAN_UNAVAILABLE_MESSAGE, history_url, actor_id, "warning")
    if assigned_rows(existing):
        return _reject(_BLOCK_MESSAGES[BLOCK_ALREADY_ASSIGNED], history_url, actor_id, "warning")
    if not fee_plan_items_valid(locked_active_items(locks)):
        return _reject(_PLAN_ITEMS_INVALID_MESSAGE, history_url, actor_id, "warning")

    moment = _write_moment()
    db.session.add(
        StudentFeeAssignment(
            enrollment_id=locks.enrollment.id,
            fee_plan_id=locks.plan.id,
            status=_ASSIGNED,
            assigned_at=moment,
            assigned_by_id=actor_id,
            cancelled_at=None,
            cancelled_by_id=None,
            version=1,
            created_at=moment,
            updated_at=moment,
        )
    )
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, history_url, actor_id)

    flash(_ASSIGNED_OK_MESSAGE, "success")
    return redirect(history_url)


# ======================================================================
# Cancellation
# ======================================================================


@admin_bp.post(_BASE + "/fee-assignments/<assignment_public_id>/cancel")
@roles_required(_ADMINISTRATOR)
@_financial_response
def enrollment_fee_assignment_cancel(group_public_id, enrollment_public_id, assignment_public_id):
    """Cancel one ``assigned`` row: record who and when, move its version
    once, and keep it. The chosen plan and its items are untouched.

    Safe on replay: a row that is already cancelled is left exactly as it is.
    Cancellation needs no active Enrollment, Student, academic chain or plan
    -- it is how an Administrator corrects a charge, and it must stay possible
    for any assignment that exists.

    Phase 5 / M04: after the assignment lock, the assignment's invoices are
    locked too, and cancellation is refused while any of them is ``draft`` or
    ``issued``. The Administrator cancels that invoice explicitly first;
    nothing here cancels, edits, deletes or creates an invoice.
    """
    context = _context_or_404(group_public_id, enrollment_public_id)
    assignment = enrollment_fee_assignment(context.enrollment_id, assignment_public_id)
    if assignment is None:
        abort(404)
    history_url = _history_url(group_public_id, enrollment_public_id)
    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get(_STATE_FIELD)
    assignment_id = assignment.id

    locks = _lock(context, actor_id, assignment_ids=(assignment_id,))
    _prove_actor_and_nesting(locks, context)
    locked = locks.assignments.get(assignment_id)
    if (
        locked is None
        or locked.enrollment_id != locks.enrollment.id
        or locked.public_id != assignment_public_id
    ):
        db.session.rollback()
        abort(404)
    invoices = lock_assignment_invoices(locked.id)
    if _hierarchy_moved(locks, context):
        return _reject(_STALE_MESSAGE, history_url, actor_id)
    if locked.status == _CANCELLED:
        return _reject(_ALREADY_CANCELLED_MESSAGE, history_url, actor_id, "info")
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_CANCEL,
        actor_public_id=actor_public_id,
        enrollment_public_id=context.enrollment_public_id,
        assignment_public_id=locked.public_id,
        assignment_version=locked.version,
    ):
        return _reject(_STALE_MESSAGE, history_url, actor_id)
    if open_invoices(
        row
        for row in invoices.values()
        if row is not None and row.student_fee_assignment_id == locked.id
    ):
        return _reject(_OPEN_INVOICE_BLOCKS_CANCELLATION, history_url, actor_id, "warning")

    moment = _write_moment()
    locked.status = _CANCELLED
    locked.cancelled_at = moment
    locked.cancelled_by_id = actor_id
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, history_url, actor_id)

    flash(_CANCELLED_OK_MESSAGE, "success")
    return redirect(history_url)
