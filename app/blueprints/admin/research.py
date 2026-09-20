"""The Administrator research area (Phase 6 / M01).

Seven routes, every object addressed by ``public_id``::

    GET       /admin/research
    GET|POST  /admin/research/consent-documents/new
    GET       /admin/research/consent-documents/<document_public_id>
    POST      /admin/research/consent-documents/<document_public_id>/activate
    GET       /admin/research/participants[?status=][&q=][&page=]
    GET|POST  /admin/research/participants/new[?q=]
    GET       /admin/research/participants/<participant_public_id>

**Its own area, beside the financial workspaces and inside neither.** No
route here reads, writes or links an invoice, payment, receipt, fee plan,
fee assignment, enrollment or balance, and no research control appears on a
Group page or in Student Accounts or Deleted Records. The Phase 5 order of
Student Accounts, Invoices, Payments, Fee Plans, Financial reports and
Deleted Records is unchanged; Research is a separate entry after them.

**Consent documents.** Only an Administrator may write them, and a
Researcher has no route to them at all -- ethics wording is not a
Researcher's field to edit. A new document is a ``draft`` and is never
presented to a Student. Activating it freezes its version, title, body and
digest **forever** and supersedes whatever was current, so at most one
document is ever current (``uq_research_consent_documents_current``, and the
lock chain besides). A draft is not editable in M01: correcting wording
means a new version, which is the same rule that already governs an
activated one, applied earlier. **Nothing here ships approved ethics text**
-- the area starts empty and says so.

**Participant invitations.** The form offers only active Students who have
no participant yet, and *offering* authorizes nothing: the create
transaction re-reads the chosen account under its lock and refuses a
Teacher, an Administrator, a Researcher, a suspended Student or a second
participant, whatever the browser submitted. The code is generated on the
server. Creating a participant **is not consent** -- it records ``invited``,
writes no consent event, and changes no User, Enrollment, Group, academic or
financial row.

**The protected mapping.** The participant list and detail here are the one
place the participant code and the Student account appear together, because
an Administrator inviting or auditing a participant needs both. The
Researcher surface never loads either side of that link
(``app/services/research_queries.py``).

**Every mutation** is POST-only and CSRF-protected, carries a
purpose-specific signed token (``app/services/research_tokens.py``), runs the
one lock chain in ``app/services/research_transactions.py`` -- the
hierarchy reset point, the ``users`` rows ascending, the consent documents
ascending, then the participant -- and re-proves the actor, the lifecycle,
the ownership and the token's exact state against the locked rows. An
``IntegrityError`` is rolled back and answered with one generic sentence
carrying no SQL and no driver text.

Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: these pages name who is taking part in a study, and a
shared or reused cache entry must never hand one to somebody else.
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
from werkzeug.exceptions import HTTPException

from app.blueprints.admin import admin_bp
from app.blueprints.admin.research_forms import ConsentDocumentForm
from app.models import (
    CONSENT_BODY_MAX_LENGTH,
    CONSENT_TITLE_MAX_LENGTH,
    CONSENT_VERSION_MAX_LENGTH,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services import research_queries as queries
from app.services import research_tokens as tokens
from app.services import research_transactions as tx

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

#: The hidden form field every mutating form carries its signed token in.
_STATE_FIELD = "state_token"

_STALE_MESSAGE = (
    "This page no longer describes the current state, so nothing was changed. "
    "Reload it and try again."
)
_INTEGRITY_MESSAGE = (
    "That could not be saved because of a conflicting change. Nothing was written. "
    "Please try again."
)
_UNAUTHORIZED_MESSAGE = "Your account is no longer allowed to make this change."
_CONFIRM_MESSAGE = "Tick the confirmation box before activating this consent document."

_CREATE_MESSAGES = {
    tx.NOT_A_STUDENT: "Only a Student account can become a research participant.",
    tx.SUSPENDED_STUDENT: "That Student account is suspended, so it cannot be invited.",
    tx.ALREADY_LINKED: "That Student is already a research participant.",
    tx.UNAUTHORIZED: _UNAUTHORIZED_MESSAGE,
    tx.STALE: _STALE_MESSAGE,
    tx.CONFLICT: _INTEGRITY_MESSAGE,
}
_ACTIVATE_MESSAGES = {
    tx.ALREADY: "That consent document is already the current one. Nothing was changed.",
    tx.NOT_ACTIVATABLE: "Only a draft consent document can be activated.",
    tx.UNAUTHORIZED: _UNAUTHORIZED_MESSAGE,
    tx.STALE: _STALE_MESSAGE,
    tx.CONFLICT: _INTEGRITY_MESSAGE,
}


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _research_response(view_func):
    """``Cache-Control: private, no-store`` and ``Vary: Cookie`` on every
    response this route produces -- the page, the redirect, the login
    redirect, the 403 and the 404 alike."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        try:
            response = make_response(view_func(*args, **kwargs))
        except HTTPException as exc:
            response = make_response(exc.get_response())
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
        return response

    return wrapped


def _overview_url():
    return url_for("admin.research_overview")


def _document_url(public_id):
    return url_for("admin.research_consent_document", document_public_id=public_id)


def _document_or_404(public_id):
    document = queries.consent_document(public_id)
    if document is None:
        abort(404)
    return document


def _activation_state(document, current):
    """The exact facts an activation token is bound to, read from rows the
    caller has just read or locked."""
    return {
        "document_public_id": document.public_id,
        "document_digest": document.body_digest,
        "current_public_id": (
            tokens.NO_CURRENT_DOCUMENT if current is None else current.public_id
        ),
        "current_digest": (
            tokens.NO_CURRENT_DOCUMENT if current is None else current.body_digest
        ),
    }


def _reject(message, target, category="danger"):
    flash(message, category)
    return redirect(target)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@admin_bp.get("/research")
@_research_response
@roles_required(_ADMINISTRATOR)
def research_overview():
    """Consent documents, participation counts, and the two entry points."""
    tz_name = _tz_name()
    rows, total = queries.consent_documents()
    counts = queries.participant_counts()
    return render_template(
        "admin/research/index.html",
        documents=queries.build_document_list_view(rows, tz_name),
        document_total=total,
        document_cap=queries.PAGE_SIZE,
        counts=counts,
        status_order=queries.STATUS_ORDER,
        status_labels=queries.PARTICIPANT_STATUS_LABELS,
        participant_total=sum(counts.values()),
        has_active_document=queries.active_consent_document() is not None,
        tz_name=tz_name,
    )


# ---------------------------------------------------------------------------
# Consent documents
# ---------------------------------------------------------------------------


@admin_bp.route("/research/consent-documents/new", methods=["GET", "POST"])
@_research_response
@roles_required(_ADMINISTRATOR)
def research_consent_document_new():
    """Write one new draft consent document."""
    form = ConsentDocumentForm()
    if form.validate_on_submit():
        outcome, document = tx.create_consent_document(
            current_user.id,
            form.version_identifier.data,
            form.title.data,
            form.body.data,
        )
        if outcome == tx.CREATED:
            flash(
                "Consent document draft saved. Review it, then activate it to make it "
                "the current consent wording.",
                "success",
            )
            return redirect(_document_url(document.public_id))
        if outcome == tx.VERSION_TAKEN:
            form.version_identifier.errors.append(
                "Another consent document already uses that version identifier."
            )
        elif outcome == tx.UNAUTHORIZED:
            return _reject(_UNAUTHORIZED_MESSAGE, _overview_url())
        else:
            flash(_INTEGRITY_MESSAGE, "danger")
    return render_template(
        "admin/research/consent_document_form.html",
        form=form,
        cancel_url=_overview_url(),
        version_max_length=CONSENT_VERSION_MAX_LENGTH,
        title_max_length=CONSENT_TITLE_MAX_LENGTH,
        body_max_length=CONSENT_BODY_MAX_LENGTH,
    )


@admin_bp.get("/research/consent-documents/<document_public_id>")
@_research_response
@roles_required(_ADMINISTRATOR)
def research_consent_document(document_public_id):
    """One consent document, with its wording and its activation control."""
    document = _document_or_404(document_public_id)
    current = queries.active_consent_document()
    activatable = document.is_draft and document.digest_matches()
    return render_template(
        "admin/research/consent_document.html",
        document=document,
        status_label=queries.DOCUMENT_STATUS_LABELS.get(document.status, document.status),
        digest_intact=document.digest_matches(),
        activatable=activatable,
        current_version=None if current is None else current.version_identifier,
        state_token=(
            tokens.make_token(
                tokens.PURPOSE_CONSENT_ACTIVATE,
                actor_public_id=current_user.public_id,
                **_activation_state(document, current),
            )
            if activatable
            else None
        ),
        state_field=_STATE_FIELD,
        activate_url=url_for(
            "admin.research_consent_document_activate",
            document_public_id=document.public_id,
        ),
        overview_url=_overview_url(),
        tz_name=_tz_name(),
    )


@admin_bp.post("/research/consent-documents/<document_public_id>/activate")
@_research_response
@roles_required(_ADMINISTRATOR)
def research_consent_document_activate(document_public_id):
    """Make this draft the one current consent document."""
    document = _document_or_404(document_public_id)
    detail_url = _document_url(document_public_id)
    token = request.form.get(_STATE_FIELD)
    actor_public_id = current_user.public_id

    if request.form.get("confirm") != "yes":
        return _reject(_CONFIRM_MESSAGE, detail_url, "warning")
    if tokens.token_is_stale(
        token,
        tokens.PURPOSE_CONSENT_ACTIVATE,
        actor_public_id=actor_public_id,
        **_activation_state(document, queries.active_consent_document()),
    ):
        return _reject(_STALE_MESSAGE, detail_url)

    def _stale_check(draft_digest, current_public_id, current_digest):
        """Re-proved against the **locked** rows, inside the transaction."""
        return tokens.token_is_stale(
            token,
            tokens.PURPOSE_CONSENT_ACTIVATE,
            actor_public_id=actor_public_id,
            document_public_id=document_public_id,
            document_digest=draft_digest,
            current_public_id=current_public_id or tokens.NO_CURRENT_DOCUMENT,
            current_digest=current_digest or tokens.NO_CURRENT_DOCUMENT,
        )

    outcome = tx.activate_consent_document(current_user.id, document.id, _stale_check)
    if outcome == tx.ACTIVATED:
        flash(
            "This consent document is now the current one. Its wording is frozen and "
            "any earlier version has been superseded.",
            "success",
        )
        return redirect(detail_url)
    if outcome == tx.NOT_FOUND:
        abort(404)
    return _reject(
        _ACTIVATE_MESSAGES.get(outcome, _INTEGRITY_MESSAGE),
        detail_url,
        "warning" if outcome in (tx.ALREADY, tx.NOT_ACTIVATABLE) else "danger",
    )


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------


@admin_bp.get("/research/participants")
@_research_response
@roles_required(_ADMINISTRATOR)
def research_participants():
    """One page of participants, with the protected Student mapping."""
    tz_name = _tz_name()
    status = queries.normalize_status_filter(request.args.get("status"))
    search = queries.normalize_code_search(request.args.get("q"))
    rows, total, page = queries.admin_participants_page(
        status, search, queries.normalize_page(request.args.get("page"))
    )
    records = queries.build_admin_participant_list_view(rows, tz_name)
    for record in records:
        record["detail_url"] = url_for(
            "admin.research_participant", participant_public_id=record["public_id"]
        )
    filters = {
        key: value
        for key, value in (("status", status), ("q", search))
        if value and value != "all"
    }
    first = (page - 1) * queries.PAGE_SIZE + 1 if records else 0
    last = (page - 1) * queries.PAGE_SIZE + len(records)
    return render_template(
        "admin/research/participants.html",
        records=records,
        status=status,
        search=search,
        status_filters=queries.STATUS_FILTERS,
        filtered=bool(filters),
        list_url=url_for("admin.research_participants"),
        new_url=url_for("admin.research_participant_new"),
        overview_url=_overview_url(),
        tz_name=tz_name,
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": url_for("admin.research_participants", page=page - 1, **filters)
            if page > 1
            else None,
            "next_url": url_for("admin.research_participants", page=page + 1, **filters)
            if last < total
            else None,
        },
    )


@admin_bp.route("/research/participants/new", methods=["GET", "POST"])
@_research_response
@roles_required(_ADMINISTRATOR)
def research_participant_new():
    """Invite one active Student into the study."""
    list_url = url_for("admin.research_participants")
    if request.method == "POST":
        return _create_participant(list_url)
    return _render_invitation_form(request.args.get("q"))


#: What ``invitable_students`` guarantees about every row it returns, and
#: therefore what each candidate's token records. It is a claim about the
#: page, not an authorization: ``create_participant`` re-proves all three
#: facts against the locked ``users`` row before writing anything.
_OFFERED_STATE = (UserRole.STUDENT.value, UserStatus.ACTIVE.value, False)


def _render_invitation_form(search):
    search = " ".join((search or "").split())[: queries.MAX_SEARCH_LENGTH].strip()
    candidates = [
        {
            "public_id": row.public_id,
            "full_name": row.full_name,
            "email": row.email,
            "state_token": tokens.make_token(
                tokens.PURPOSE_PARTICIPANT_CREATE,
                actor_public_id=current_user.public_id,
                student_public_id=row.public_id,
                student_state=tokens.student_state(*_OFFERED_STATE),
            ),
        }
        for row in queries.invitable_students(search)
    ]
    return render_template(
        "admin/research/participant_form.html",
        candidates=candidates,
        candidate_limit=queries.CANDIDATE_LIMIT,
        search=search,
        form_url=url_for("admin.research_participant_new"),
        cancel_url=url_for("admin.research_participants"),
        state_field=_STATE_FIELD,
    )


def _create_participant(list_url):
    token = request.form.get(_STATE_FIELD)
    actor_public_id = current_user.public_id
    student_public_id = (request.form.get("student_public_id") or "").strip()

    student = queries.student_by_public_id(student_public_id)
    if student is None:
        # The chosen account does not exist, or the identifier is malformed.
        # Neither is disclosed: the form comes back with the same sentence.
        flash(_CREATE_MESSAGES[tx.STALE], "warning")
        return _render_invitation_form(request.form.get("q"))

    def _stale_check(role, status, has_participant):
        """Re-proved against the **locked** ``users`` row and the live
        participant link, inside the transaction."""
        return tokens.token_is_stale(
            token,
            tokens.PURPOSE_PARTICIPANT_CREATE,
            actor_public_id=actor_public_id,
            student_public_id=student_public_id,
            student_state=tokens.student_state(role, status, has_participant),
        )

    outcome, participant = tx.create_participant(current_user.id, student.id, _stale_check)
    if outcome == tx.CREATED:
        flash(
            f"Research participant {participant.participant_code} created. The student has "
            "not consented yet: only they can accept, and only from their own portal.",
            "success",
        )
        return redirect(
            url_for(
                "admin.research_participant",
                participant_public_id=participant.public_id,
            )
        )
    flash(
        _CREATE_MESSAGES.get(outcome, _INTEGRITY_MESSAGE),
        "warning" if outcome in (tx.NOT_A_STUDENT, tx.SUSPENDED_STUDENT,
                                 tx.ALREADY_LINKED, tx.STALE) else "danger",
    )
    return _render_invitation_form(request.form.get("q"))


@admin_bp.get("/research/participants/<participant_public_id>")
@_research_response
@roles_required(_ADMINISTRATOR)
def research_participant(participant_public_id):
    """One participant, its Student mapping and its consent history."""
    row = queries.admin_participant(participant_public_id)
    if row is None:
        abort(404)
    tz_name = _tz_name()
    return render_template(
        "admin/research/participant.html",
        record=queries.build_admin_participant_view(row, tz_name),
        history=queries.participant_history(participant_public_id, tz_name),
        list_url=url_for("admin.research_participants"),
        tz_name=tz_name,
    )
