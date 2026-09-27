"""The Researcher's experiment protocol catalogue (Phase 6 / M02A).

Thirteen rules, every object addressed by ``public_id`` and every nested
object found only inside its parent::

    GET       /research/protocols[?status=][&page=]
    GET|POST  /research/protocols/new
    GET       /research/protocols/<protocol_public_id>
    GET|POST  /research/protocols/<protocol_public_id>/edit
    GET|POST  /research/protocols/<protocol_public_id>/sets/new
    GET|POST  /research/protocols/<protocol_public_id>/sets/<set_public_id>/edit
    POST      /research/protocols/<protocol_public_id>/sets/<set_public_id>/move
    GET|POST  /research/protocols/<protocol_public_id>/sets/<set_public_id>/tasks/new
    GET|POST  /research/protocols/<protocol_public_id>/sets/<set_public_id>/tasks/<task_public_id>/edit
    POST      /research/protocols/<protocol_public_id>/sets/<set_public_id>/tasks/<task_public_id>/move
    GET|POST  /research/protocols/<protocol_public_id>/activate
    POST      /research/protocols/<protocol_public_id>/discard
    GET|POST  /research/protocols/<protocol_public_id>/new-version

**A catalogue, not an experiment.** A Researcher writes a Version A protocol
as a draft -- a header, up to four ordered task sets, up to ten ordered tasks
each -- reviews it, and internally activates it, which freezes the whole
version and seals it with a content digest. Corrections are new drafts
derived from a frozen version; a mistaken draft is discarded, never deleted.
**There is no removal route for a set or a task**, and no route deletes
anything.

**Internal activation is not ethics approval and starts nothing.** No route
here creates an assignment, a session or a participant link, reads any
Student's data, binds a task to a real Lesson, Quiz or Assignment, or shows a
protocol to a Student, Teacher or Administrator. Completion criteria are
recorded as the protocol's intention; nothing verifies them.

**Researcher only.** ``roles_required(RESEARCHER)`` gives the role guard, and
every write re-proves an active Researcher against the locked ``users`` row.
M01's operations are untouched: participant records and consent documents
stay the Administrator's, consent decisions stay the Student's, and a
Researcher's access to them stays read-only.

**Every mutation** is POST-only with CSRF and a purpose-specific signed
token (``app/services/experiment_protocol_tokens.py``) bound to the
protocol's aggregate ``version``, checked once before the locks and again
against the locked rows in ``app/services/experiment_protocol_transactions.py``.
An ``IntegrityError`` is rolled back and answered with one generic sentence.

The ``Cache-Control: private, no-store`` and ``Vary: Cookie`` headers come
from the portal-wide hook in ``app/blueprints/research/routes.py``, which
covers every response under ``/research`` -- including Flask's own 404 and
405 responses, which no view wrapper ever sees.
"""

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.research import research_bp
from app.blueprints.research.protocol_forms import (
    DeriveForm,
    ProtocolForm,
    TaskForm,
    TaskSetForm,
)
from app.models import (
    ALLOWED_CRITERIA_BY_TYPE,
    EQUIVALENCE_RATIONALE_MAX_LENGTH,
    MAX_RECOMMENDED_DURATION_SECONDS,
    MAX_TASK_SETS_PER_PROTOCOL,
    MAX_TASKS_PER_SET,
    MIN_RECOMMENDED_DURATION_SECONDS,
    PROTOCOL_TITLE_MAX_LENGTH,
    PROTOCOL_VERSION_MAX_LENGTH,
    TASK_GOAL_MAX_LENGTH,
    TASK_INSTRUCTIONS_MAX_LENGTH,
    TASK_SET_CODE_MAX_LENGTH,
    TASK_SET_TITLE_MAX_LENGTH,
    TASK_TITLE_MAX_LENGTH,
    ExperimentDefinitionStatus,
    UserRole,
)
from app.security.decorators import roles_required
from app.services import experiment_protocol_queries as queries
from app.services import experiment_protocol_tokens as tokens
from app.services import experiment_protocol_transactions as tx

_RESEARCHER = UserRole.RESEARCHER.value
_DRAFT = ExperimentDefinitionStatus.DRAFT.value
_ACTIVE = ExperimentDefinitionStatus.ACTIVE.value
_SUPERSEDED = ExperimentDefinitionStatus.SUPERSEDED.value
_DISCARDED = ExperimentDefinitionStatus.DISCARDED.value

#: The hidden form field every mutating form carries its signed token in.
_STATE_FIELD = "state_token"

_STALE_MESSAGE = (
    "This page no longer describes the current protocol, so nothing was changed. "
    "Reload it and try again."
)
_INTEGRITY_MESSAGE = (
    "That could not be saved because of a conflicting change. Nothing was written. "
    "Please try again."
)
_UNAUTHORIZED_MESSAGE = "Your account is no longer allowed to make this change."
_FROZEN_MESSAGE = (
    "This protocol version is no longer a draft, so it cannot be changed. Create a new "
    "version to make corrections."
)
_DISCARDED_MESSAGE = (
    "This protocol version is no longer a draft: it was discarded, and it cannot be "
    "changed, activated or copied. Start a new draft instead."
)

_DRAFT_MESSAGES = {
    tx.STALE: (_STALE_MESSAGE, "danger"),
    tx.FROZEN: (_FROZEN_MESSAGE, "warning"),
    tx.UNAUTHORIZED: (_UNAUTHORIZED_MESSAGE, "danger"),
    tx.CONFLICT: (_INTEGRITY_MESSAGE, "danger"),
    tx.UNCHANGED: ("Nothing was changed.", "info"),
    tx.EDGE: ("That item is already at the end of its list. Nothing was changed.", "info"),
    tx.LIMIT: ("That limit has been reached, so nothing was added.", "warning"),
}

_MOVE_ACTIONS = {
    "set": {tx.UP: tokens.ACTION_MOVE_SET_UP, tx.DOWN: tokens.ACTION_MOVE_SET_DOWN},
    "task": {tx.UP: tokens.ACTION_MOVE_TASK_UP, tx.DOWN: tokens.ACTION_MOVE_TASK_DOWN},
}


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _detail_url(protocol_public_id, anchor=None):
    return url_for(
        "research.protocol_detail", protocol_public_id=protocol_public_id, _anchor=anchor
    )


def _header_or_404(protocol_public_id):
    header = queries.protocol_header(protocol_public_id)
    if header is None:
        abort(404)
    return header


def _set_or_404(protocol_public_id, set_public_id):
    row = queries.task_set_row(protocol_public_id, set_public_id)
    if row is None:
        abort(404)
    return row


def _task_or_404(protocol_public_id, set_public_id, task_public_id):
    row = queries.task_row(protocol_public_id, set_public_id, task_public_id)
    if row is None:
        abort(404)
    return row


def _reject(message, target, category="danger"):
    flash(message, category)
    return redirect(target)


def _frozen(header):
    """Refuse a change to a version that is no longer a draft, in words that
    fit what it now is: a frozen version can be copied, a discarded draft
    cannot."""
    message = _DISCARDED_MESSAGE if header.status == _DISCARDED else _FROZEN_MESSAGE
    return _reject(message, _detail_url(header.public_id), "warning")


def _outcome_redirect(outcome, protocol_public_id):
    """Answer a draft-change outcome that is not a success."""
    if outcome == tx.NOT_FOUND:
        abort(404)
    message, category = _DRAFT_MESSAGES.get(outcome, (_INTEGRITY_MESSAGE, "danger"))
    return _reject(message, _detail_url(protocol_public_id), category)


# ---------------------------------------------------------------------------
# Draft-change tokens: one aggregate version, one target, one action
# ---------------------------------------------------------------------------


def _draft_token(header, target_public_id, action):
    return tokens.make_token(
        tokens.PURPOSE_DRAFT_CHANGE,
        actor_public_id=current_user.public_id,
        protocol_public_id=header.public_id,
        protocol_version=header.version,
        target_public_id=target_public_id or tokens.NONE,
        action=action,
    )


def _draft_stale(token, protocol_public_id, version, target_public_id, action):
    return tokens.token_is_stale(
        token,
        tokens.PURPOSE_DRAFT_CHANGE,
        actor_public_id=current_user.public_id,
        protocol_public_id=protocol_public_id,
        protocol_version=version,
        target_public_id=target_public_id or tokens.NONE,
        action=action,
    )


def _draft_stale_check(token, protocol_public_id, target_public_id, action):
    """The closure the transaction calls with the **locked** version."""
    actor_public_id = current_user.public_id

    def _stale_check(locked_version):
        return tokens.token_is_stale(
            token,
            tokens.PURPOSE_DRAFT_CHANGE,
            actor_public_id=actor_public_id,
            protocol_public_id=protocol_public_id,
            protocol_version=locked_version,
            target_public_id=target_public_id or tokens.NONE,
            action=action,
        )

    return _stale_check


def _draft_guard(header, token, target_public_id, action):
    """The pre-lock checks every draft-change POST shares. Returns a
    response to send, or ``None`` to proceed."""
    if header.status != _DRAFT:
        return _frozen(header)
    if _draft_stale(token, header.public_id, header.version, target_public_id, action):
        return _reject(_STALE_MESSAGE, _detail_url(header.public_id))
    return None


# ---------------------------------------------------------------------------
# List and detail
# ---------------------------------------------------------------------------


@research_bp.get("/protocols")
@roles_required(_RESEARCHER)
def protocols():
    """One page of protocol versions, newest first."""
    tz_name = _tz_name()
    status = queries.normalize_status_filter(request.args.get("status"))
    rows, total, page = queries.protocols_page(
        status, queries.normalize_page(request.args.get("page"))
    )
    records = [
        {
            "public_id": row.public_id,
            "version_identifier": row.version_identifier,
            "title": row.title,
            "status": row.status,
            "status_label": queries.STATUS_LABELS.get(row.status, row.status),
            "created_local": queries.local(tz_name, row.created_at),
            "activated_local": queries.local(tz_name, row.activated_at),
            "detail_url": _detail_url(row.public_id),
        }
        for row in rows
    ]
    filters = {"status": status} if status != "all" else {}
    first = (page - 1) * queries.PAGE_SIZE + 1 if records else 0
    last = (page - 1) * queries.PAGE_SIZE + len(records)
    return render_template(
        "research/protocols/list.html",
        records=records,
        status=status,
        status_filters=queries.STATUS_FILTERS,
        filtered=bool(filters),
        list_url=url_for("research.protocols"),
        new_url=url_for("research.protocol_new"),
        tz_name=tz_name,
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": url_for("research.protocols", page=page - 1, **filters)
            if page > 1 else None,
            "next_url": url_for("research.protocols", page=page + 1, **filters)
            if last < total else None,
        },
    )


def _task_view(header, set_public_id, task, index, count, editable):
    view = {
        "public_id": task.public_id,
        "position": index + 1,
        "task_type_label": queries.TASK_TYPE_LABELS.get(task.task_type, task.task_type),
        "title": task.title,
        "participant_instructions": task.participant_instructions,
        "expected_goal": task.expected_goal,
        "difficulty_label": queries.DIFFICULTY_LABELS.get(task.difficulty, task.difficulty),
        "recommended_duration_seconds": task.recommended_duration_seconds,
        "criterion_label": queries.CRITERION_LABELS.get(
            task.completion_criterion, task.completion_criterion
        ),
    }
    if editable:
        ids = {"protocol_public_id": header.public_id, "set_public_id": set_public_id,
               "task_public_id": task.public_id}
        view["edit_url"] = url_for("research.protocol_task_edit", **ids)
        view["move_url"] = url_for("research.protocol_task_move", **ids)
        view["moves"] = [
            (direction, label, _draft_token(header, task.public_id,
                                            _MOVE_ACTIONS["task"][direction]))
            for direction, label, allowed in (
                (tx.UP, "Move up", index > 0),
                (tx.DOWN, "Move down", index < count - 1),
            )
            if allowed
        ]
    return view


def _content_view(header, content, editable):
    sets = []
    for index, (task_set, tasks) in enumerate(content):
        view = {
            "public_id": task_set.public_id,
            "position": index + 1,
            "set_code": task_set.set_code,
            "title": task_set.title,
            "tasks": [
                _task_view(header, task_set.public_id, task, task_index, len(tasks), editable)
                for task_index, task in enumerate(tasks)
            ],
        }
        if editable:
            ids = {"protocol_public_id": header.public_id, "set_public_id": task_set.public_id}
            view["edit_url"] = url_for("research.protocol_set_edit", **ids)
            view["move_url"] = url_for("research.protocol_set_move", **ids)
            view["add_task_url"] = (
                url_for("research.protocol_task_new", **ids)
                if len(tasks) < MAX_TASKS_PER_SET else None
            )
            view["moves"] = [
                (direction, label, _draft_token(header, task_set.public_id,
                                                _MOVE_ACTIONS["set"][direction]))
                for direction, label, allowed in (
                    (tx.UP, "Move up", index > 0),
                    (tx.DOWN, "Move down", index < len(content) - 1),
                )
                if allowed
            ]
        sets.append(view)
    return sets


def _header_view(header, tz_name):
    return {
        "public_id": header.public_id,
        "version_identifier": header.version_identifier,
        "title": header.title,
        "stage_label": queries.STAGE_LABELS.get(header.study_stage, header.study_stage),
        "equivalence_rationale": header.equivalence_rationale,
        "status": header.status,
        "status_label": queries.STATUS_LABELS.get(header.status, header.status),
        "content_digest": header.content_digest,
        "created_local": queries.local(tz_name, header.created_at),
        "activated_local": queries.local(tz_name, header.activated_at),
        "superseded_local": queries.local(tz_name, header.superseded_at),
        "discarded_local": queries.local(tz_name, header.discarded_at),
        "derived_from_version": header.derived_from_version,
        "derived_from_url": (
            _detail_url(header.derived_from_public_id)
            if header.derived_from_public_id else None
        ),
    }


@research_bp.get("/protocols/<protocol_public_id>")
@roles_required(_RESEARCHER)
def protocol_detail(protocol_public_id):
    """One protocol version with its ordered sets and tasks."""
    tz_name = _tz_name()
    header = _header_or_404(protocol_public_id)
    content = queries.protocol_content(header.public_id)
    editable = header.status == _DRAFT
    ids = {"protocol_public_id": header.public_id}
    return render_template(
        "research/protocols/detail.html",
        protocol=_header_view(header, tz_name),
        sets=_content_view(header, content, editable),
        editable=editable,
        derivable=header.status in (_ACTIVE, _SUPERSEDED),
        edit_url=url_for("research.protocol_edit", **ids),
        add_set_url=(
            url_for("research.protocol_set_new", **ids)
            if editable and len(content) < MAX_TASK_SETS_PER_PROTOCOL else None
        ),
        review_url=url_for("research.protocol_activate", **ids),
        discard_url=url_for("research.protocol_discard", **ids),
        derive_url=url_for("research.protocol_new_version", **ids),
        discard_token=(
            tokens.make_token(
                tokens.PURPOSE_DISCARD,
                actor_public_id=current_user.public_id,
                protocol_public_id=header.public_id,
                protocol_version=header.version,
            )
            if editable else None
        ),
        state_field=_STATE_FIELD,
        list_url=url_for("research.protocols"),
        max_sets=MAX_TASK_SETS_PER_PROTOCOL,
        max_tasks=MAX_TASKS_PER_SET,
        tz_name=tz_name,
    )


# ---------------------------------------------------------------------------
# The header: create and edit
# ---------------------------------------------------------------------------


def _protocol_form_context(form, **extra):
    return dict(
        form=form,
        version_max_length=PROTOCOL_VERSION_MAX_LENGTH,
        title_max_length=PROTOCOL_TITLE_MAX_LENGTH,
        rationale_max_length=EQUIVALENCE_RATIONALE_MAX_LENGTH,
        state_field=_STATE_FIELD,
        **extra,
    )


@research_bp.route("/protocols/new", methods=["GET", "POST"])
@roles_required(_RESEARCHER)
def protocol_new():
    """Write one new draft protocol version."""
    form = ProtocolForm()
    if form.validate_on_submit():
        outcome, public_id = tx.create_protocol(
            current_user.id,
            form.version_identifier.data,
            form.title.data,
            form.equivalence_rationale.data,
        )
        if outcome == tx.CREATED:
            flash("Protocol draft saved. Add task sets and tasks, then review it.", "success")
            return redirect(_detail_url(public_id))
        if outcome == tx.VERSION_TAKEN:
            form.version_identifier.errors.append(
                "Another protocol version already uses that identifier."
            )
        elif outcome == tx.UNAUTHORIZED:
            return _reject(_UNAUTHORIZED_MESSAGE, url_for("research.protocols"))
        else:
            flash(_INTEGRITY_MESSAGE, "danger")
    return render_template(
        "research/protocols/protocol_form.html",
        **_protocol_form_context(
            form,
            heading="New protocol draft",
            cancel_url=url_for("research.protocols"),
            state_token=None,
        ),
    )


@research_bp.route("/protocols/<protocol_public_id>/edit", methods=["GET", "POST"])
@roles_required(_RESEARCHER)
def protocol_edit(protocol_public_id):
    """Change a draft's version identifier, title and equivalence rationale."""
    header = _header_or_404(protocol_public_id)
    action = tokens.ACTION_EDIT_PROTOCOL
    form = ProtocolForm()
    if request.method == "POST":
        token = request.form.get(_STATE_FIELD)
        refusal = _draft_guard(header, token, None, action)
        if refusal:
            return refusal
        if form.validate_on_submit():
            outcome = tx.update_protocol(
                current_user.id,
                header.public_id,
                form.version_identifier.data,
                form.title.data,
                form.equivalence_rationale.data,
                _draft_stale_check(token, header.public_id, None, action),
            )
            if outcome == tx.SAVED:
                flash("Protocol details saved.", "success")
                return redirect(_detail_url(header.public_id))
            if outcome == tx.VERSION_TAKEN:
                form.version_identifier.errors.append(
                    "Another protocol version already uses that identifier."
                )
            else:
                return _outcome_redirect(outcome, header.public_id)
    else:
        if header.status != _DRAFT:
            return _frozen(header)
        token = _draft_token(header, None, action)
        form.version_identifier.data = header.version_identifier
        form.title.data = header.title
        form.equivalence_rationale.data = header.equivalence_rationale or ""
    return render_template(
        "research/protocols/protocol_form.html",
        **_protocol_form_context(
            form,
            heading=f"Edit protocol draft {header.version_identifier}",
            cancel_url=_detail_url(header.public_id),
            state_token=token,
        ),
    )


# ---------------------------------------------------------------------------
# Task sets
# ---------------------------------------------------------------------------


def _set_form_page(form, header, heading, token):
    return render_template(
        "research/protocols/set_form.html",
        form=form,
        heading=heading,
        protocol_version_identifier=header.version_identifier,
        cancel_url=_detail_url(header.public_id),
        code_max_length=TASK_SET_CODE_MAX_LENGTH,
        title_max_length=TASK_SET_TITLE_MAX_LENGTH,
        state_field=_STATE_FIELD,
        state_token=token,
    )


@research_bp.route("/protocols/<protocol_public_id>/sets/new", methods=["GET", "POST"])
@roles_required(_RESEARCHER)
def protocol_set_new(protocol_public_id):
    """Append one task set to a draft."""
    header = _header_or_404(protocol_public_id)
    action = tokens.ACTION_ADD_SET
    form = TaskSetForm()
    if request.method == "POST":
        token = request.form.get(_STATE_FIELD)
        refusal = _draft_guard(header, token, None, action)
        if refusal:
            return refusal
        if form.validate_on_submit():
            outcome, _ = tx.add_task_set(
                current_user.id, header.public_id, form.set_code.data, form.title.data,
                _draft_stale_check(token, header.public_id, None, action),
            )
            if outcome == tx.CREATED:
                flash(f"Task set {form.set_code.data} added.", "success")
                return redirect(_detail_url(header.public_id))
            if outcome == tx.CODE_TAKEN:
                form.set_code.errors.append("Another task set in this draft uses that code.")
            elif outcome == tx.LIMIT:
                return _reject(
                    f"A protocol version may have at most {MAX_TASK_SETS_PER_PROTOCOL} task "
                    "sets, so nothing was added.",
                    _detail_url(header.public_id), "warning",
                )
            else:
                return _outcome_redirect(outcome, header.public_id)
    else:
        if header.status != _DRAFT:
            return _frozen(header)
        token = _draft_token(header, None, action)
    return _set_form_page(form, header, "Add task set", token)


@research_bp.route(
    "/protocols/<protocol_public_id>/sets/<set_public_id>/edit", methods=["GET", "POST"]
)
@roles_required(_RESEARCHER)
def protocol_set_edit(protocol_public_id, set_public_id):
    """Change one draft task set's code and title."""
    header = _header_or_404(protocol_public_id)
    task_set = _set_or_404(header.public_id, set_public_id)
    action = tokens.ACTION_EDIT_SET
    form = TaskSetForm()
    if request.method == "POST":
        token = request.form.get(_STATE_FIELD)
        refusal = _draft_guard(header, token, task_set.public_id, action)
        if refusal:
            return refusal
        if form.validate_on_submit():
            outcome = tx.update_task_set(
                current_user.id, header.public_id, task_set.public_id,
                form.set_code.data, form.title.data,
                _draft_stale_check(token, header.public_id, task_set.public_id, action),
            )
            if outcome == tx.SAVED:
                flash(f"Task set {form.set_code.data} saved.", "success")
                return redirect(_detail_url(header.public_id))
            if outcome == tx.CODE_TAKEN:
                form.set_code.errors.append("Another task set in this draft uses that code.")
            else:
                return _outcome_redirect(outcome, header.public_id)
    else:
        if header.status != _DRAFT:
            return _frozen(header)
        token = _draft_token(header, task_set.public_id, action)
        form.set_code.data = task_set.set_code
        form.title.data = task_set.title
    return _set_form_page(form, header, f"Edit task set {task_set.set_code}", token)


def _move(kind, header, target_public_id, run):
    """The shared body of both move routes."""
    token = request.form.get(_STATE_FIELD)
    direction = request.form.get("direction")
    action = _MOVE_ACTIONS[kind].get(direction)
    if action is None:
        return _reject(_STALE_MESSAGE, _detail_url(header.public_id))
    refusal = _draft_guard(header, token, target_public_id, action)
    if refusal:
        return refusal
    outcome = run(direction, _draft_stale_check(token, header.public_id, target_public_id,
                                                 action))
    if outcome == tx.MOVED:
        flash("Order saved.", "success")
        return redirect(_detail_url(header.public_id))
    return _outcome_redirect(outcome, header.public_id)


@research_bp.post("/protocols/<protocol_public_id>/sets/<set_public_id>/move")
@roles_required(_RESEARCHER)
def protocol_set_move(protocol_public_id, set_public_id):
    """Move one draft task set up or down."""
    header = _header_or_404(protocol_public_id)
    task_set = _set_or_404(header.public_id, set_public_id)
    return _move(
        "set", header, task_set.public_id,
        lambda direction, check: tx.move_task_set(
            current_user.id, header.public_id, task_set.public_id, direction, check
        ),
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _task_form_page(form, header, task_set, heading, token):
    return render_template(
        "research/protocols/task_form.html",
        form=form,
        heading=heading,
        protocol_version_identifier=header.version_identifier,
        set_code=task_set.set_code,
        cancel_url=_detail_url(header.public_id),
        allowed_criteria={
            task_type: [queries.CRITERION_LABELS[c] for c in criteria]
            for task_type, criteria in ALLOWED_CRITERIA_BY_TYPE.items()
        },
        task_type_labels=queries.TASK_TYPE_LABELS,
        title_max_length=TASK_TITLE_MAX_LENGTH,
        instructions_max_length=TASK_INSTRUCTIONS_MAX_LENGTH,
        goal_max_length=TASK_GOAL_MAX_LENGTH,
        min_duration=MIN_RECOMMENDED_DURATION_SECONDS,
        max_duration=MAX_RECOMMENDED_DURATION_SECONDS,
        state_field=_STATE_FIELD,
        state_token=token,
    )


@research_bp.route(
    "/protocols/<protocol_public_id>/sets/<set_public_id>/tasks/new", methods=["GET", "POST"]
)
@roles_required(_RESEARCHER)
def protocol_task_new(protocol_public_id, set_public_id):
    """Append one task to a draft task set."""
    header = _header_or_404(protocol_public_id)
    task_set = _set_or_404(header.public_id, set_public_id)
    action = tokens.ACTION_ADD_TASK
    form = TaskForm()
    if request.method == "POST":
        token = request.form.get(_STATE_FIELD)
        refusal = _draft_guard(header, token, task_set.public_id, action)
        if refusal:
            return refusal
        if form.validate_on_submit():
            outcome, _ = tx.add_task(
                current_user.id, header.public_id, task_set.public_id, form.values(),
                _draft_stale_check(token, header.public_id, task_set.public_id, action),
            )
            if outcome == tx.CREATED:
                flash(f"Task added to task set {task_set.set_code}.", "success")
                return redirect(_detail_url(header.public_id))
            if outcome == tx.LIMIT:
                return _reject(
                    f"A task set may have at most {MAX_TASKS_PER_SET} tasks, so nothing was "
                    "added.",
                    _detail_url(header.public_id), "warning",
                )
            return _outcome_redirect(outcome, header.public_id)
    else:
        if header.status != _DRAFT:
            return _frozen(header)
        token = _draft_token(header, task_set.public_id, action)
    return _task_form_page(form, header, task_set, f"Add task to {task_set.set_code}", token)


@research_bp.route(
    "/protocols/<protocol_public_id>/sets/<set_public_id>/tasks/<task_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(_RESEARCHER)
def protocol_task_edit(protocol_public_id, set_public_id, task_public_id):
    """Change one draft task."""
    header = _header_or_404(protocol_public_id)
    task_set = _set_or_404(header.public_id, set_public_id)
    task = _task_or_404(header.public_id, task_set.public_id, task_public_id)
    action = tokens.ACTION_EDIT_TASK
    form = TaskForm()
    if request.method == "POST":
        token = request.form.get(_STATE_FIELD)
        refusal = _draft_guard(header, token, task.public_id, action)
        if refusal:
            return refusal
        if form.validate_on_submit():
            outcome = tx.update_task(
                current_user.id, header.public_id, task_set.public_id, task.public_id,
                form.values(),
                _draft_stale_check(token, header.public_id, task.public_id, action),
            )
            if outcome == tx.SAVED:
                flash("Task saved.", "success")
                return redirect(_detail_url(header.public_id))
            return _outcome_redirect(outcome, header.public_id)
    else:
        if header.status != _DRAFT:
            return _frozen(header)
        token = _draft_token(header, task.public_id, action)
        for field in ("task_type", "title", "participant_instructions", "expected_goal",
                      "difficulty", "completion_criterion"):
            getattr(form, field).data = getattr(task, field)
        form.recommended_duration_seconds.data = str(task.recommended_duration_seconds)
    return _task_form_page(form, header, task_set, f"Edit task in {task_set.set_code}", token)


@research_bp.post(
    "/protocols/<protocol_public_id>/sets/<set_public_id>/tasks/<task_public_id>/move"
)
@roles_required(_RESEARCHER)
def protocol_task_move(protocol_public_id, set_public_id, task_public_id):
    """Move one draft task up or down inside its set."""
    header = _header_or_404(protocol_public_id)
    task_set = _set_or_404(header.public_id, set_public_id)
    task = _task_or_404(header.public_id, task_set.public_id, task_public_id)
    return _move(
        "task", header, task.public_id,
        lambda direction, check: tx.move_task(
            current_user.id, header.public_id, task_set.public_id, task.public_id,
            direction, check,
        ),
    )


# ---------------------------------------------------------------------------
# Review and activation, discard, and a new version
# ---------------------------------------------------------------------------


@research_bp.route("/protocols/<protocol_public_id>/activate", methods=["GET", "POST"])
@roles_required(_RESEARCHER)
def protocol_activate(protocol_public_id):
    """Review a draft's complete content and, if it is structurally
    complete, internally activate it."""
    header = _header_or_404(protocol_public_id)
    detail_url = _detail_url(header.public_id)
    if header.status != _DRAFT:
        if header.status == _ACTIVE:
            return _reject("This protocol version is already the active one.", detail_url,
                           "info")
        return _frozen(header)

    content = queries.protocol_content(header.public_id)
    digest = queries.preview_digest(header, content)
    current = queries.current_protocol(header.study_stage)
    current_public_id = None if current is None else current.public_id

    if request.method == "POST":
        token = request.form.get(_STATE_FIELD)
        if request.form.get("confirm") != "yes":
            return _reject(
                "Tick the confirmation box before activating this protocol version.",
                url_for("research.protocol_activate", protocol_public_id=header.public_id),
                "warning",
            )
        if tokens.token_is_stale(
            token, tokens.PURPOSE_ACTIVATE,
            actor_public_id=current_user.public_id,
            protocol_public_id=header.public_id,
            protocol_version=header.version,
            preview_digest=digest,
            current_public_id=current_public_id or tokens.NONE,
        ):
            return _reject(_STALE_MESSAGE, detail_url)
        actor_public_id = current_user.public_id

        def _stale_check(locked_version, locked_digest, locked_current_public_id):
            return tokens.token_is_stale(
                token, tokens.PURPOSE_ACTIVATE,
                actor_public_id=actor_public_id,
                protocol_public_id=header.public_id,
                protocol_version=locked_version,
                preview_digest=locked_digest,
                current_public_id=locked_current_public_id or tokens.NONE,
            )

        outcome, problems = tx.activate_protocol(current_user.id, header.public_id,
                                                 _stale_check)
        if outcome == tx.ACTIVATED:
            flash(
                f"Protocol version {header.version_identifier} is now the active catalogue "
                "version. Its content is frozen. This is not an ethics approval and it "
                "starts no data collection.",
                "success",
            )
            return redirect(detail_url)
        if outcome == tx.INCOMPLETE:
            return _reject(
                "This draft cannot be activated yet: "
                + " ".join(problem.message for problem in problems),
                detail_url, "warning",
            )
        if outcome == tx.ALREADY:
            return _reject("This protocol version is already the active one.", detail_url,
                           "info")
        return _outcome_redirect(outcome, header.public_id)

    problems = queries.review_problems(header, content)
    return render_template(
        "research/protocols/review.html",
        protocol=_header_view(header, _tz_name()),
        sets=_content_view(header, content, editable=False),
        problems=problems,
        digest=digest,
        current_version=None if current is None else current.version_identifier,
        state_field=_STATE_FIELD,
        state_token=(
            tokens.make_token(
                tokens.PURPOSE_ACTIVATE,
                actor_public_id=current_user.public_id,
                protocol_public_id=header.public_id,
                protocol_version=header.version,
                preview_digest=digest,
                current_public_id=current_public_id or tokens.NONE,
            )
            if not problems else None
        ),
        activate_url=url_for("research.protocol_activate", protocol_public_id=header.public_id),
        detail_url=detail_url,
    )


@research_bp.post("/protocols/<protocol_public_id>/discard")
@roles_required(_RESEARCHER)
def protocol_discard(protocol_public_id):
    """Soft-discard a draft. Nothing is deleted."""
    header = _header_or_404(protocol_public_id)
    detail_url = _detail_url(header.public_id)
    token = request.form.get(_STATE_FIELD)
    if request.form.get("confirm") != "yes":
        return _reject("Tick the confirmation box before discarding this draft.",
                       detail_url, "warning")
    if header.status != _DRAFT:
        return _frozen(header)
    actor_public_id = current_user.public_id

    def _stale_check(locked_version):
        return tokens.token_is_stale(
            token, tokens.PURPOSE_DISCARD,
            actor_public_id=actor_public_id,
            protocol_public_id=header.public_id,
            protocol_version=locked_version,
        )

    if _stale_check(header.version):
        return _reject(_STALE_MESSAGE, detail_url)
    outcome = tx.discard_protocol(current_user.id, header.public_id, _stale_check)
    if outcome == tx.DISCARDED:
        flash(
            f"Draft {header.version_identifier} was discarded. It is kept, frozen, and "
            "listed under Discarded.",
            "success",
        )
        return redirect(detail_url)
    if outcome == tx.ALREADY:
        return _reject("This draft was already discarded.", detail_url, "info")
    return _outcome_redirect(outcome, header.public_id)


@research_bp.route("/protocols/<protocol_public_id>/new-version", methods=["GET", "POST"])
@roles_required(_RESEARCHER)
def protocol_new_version(protocol_public_id):
    """Create a new draft copied from a frozen version."""
    header = _header_or_404(protocol_public_id)
    detail_url = _detail_url(header.public_id)
    if header.status not in (_ACTIVE, _SUPERSEDED):
        return _reject(
            "Only an active or superseded protocol version can be copied into a new draft.",
            detail_url, "warning",
        )
    form = DeriveForm()
    if request.method == "POST":
        token = request.form.get(_STATE_FIELD)
        actor_public_id = current_user.public_id

        def _stale_check(locked_digest):
            return tokens.token_is_stale(
                token, tokens.PURPOSE_DERIVE,
                actor_public_id=actor_public_id,
                source_public_id=header.public_id,
                source_digest=locked_digest,
            )

        if _stale_check(header.content_digest):
            return _reject(_STALE_MESSAGE, detail_url)
        if form.validate_on_submit():
            outcome, public_id = tx.derive_protocol(
                current_user.id, header.public_id, form.version_identifier.data,
                form.title.data, _stale_check,
            )
            if outcome == tx.CREATED:
                flash(
                    f"New draft {form.version_identifier.data} created from "
                    f"{header.version_identifier}. Correct it, then review it.",
                    "success",
                )
                return redirect(_detail_url(public_id))
            if outcome == tx.VERSION_TAKEN:
                form.version_identifier.errors.append(
                    "Another protocol version already uses that identifier."
                )
            else:
                return _outcome_redirect(outcome, header.public_id)
    else:
        token = tokens.make_token(
            tokens.PURPOSE_DERIVE,
            actor_public_id=current_user.public_id,
            source_public_id=header.public_id,
            source_digest=header.content_digest,
        )
        form.title.data = header.title
    return render_template(
        "research/protocols/derive.html",
        form=form,
        source_version=header.version_identifier,
        cancel_url=detail_url,
        version_max_length=PROTOCOL_VERSION_MAX_LENGTH,
        title_max_length=PROTOCOL_TITLE_MAX_LENGTH,
        state_field=_STATE_FIELD,
        state_token=token,
    )
