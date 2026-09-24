"""Student research consent (Phase 6 / M01).

Four rules, and no object identifier anywhere in any of them::

    GET   /student/research-consent
    POST  /student/research-consent/accept
    POST  /student/research-consent/decline
    GET   /student/research-consent/withdraw
    POST  /student/research-consent/withdraw

**The participant is found from the session, never from the request.**
``student_participant(current_user.id)`` is scoped to the acting account in
SQL, so there is no participant id, Student id, consent-document id, version
or status in a URL or a hidden field for anybody to change -- and
``participant.student_id == current_user.id`` is proved *again* against the
locked row inside the transaction. A Student with no invitation gets a plain
404 here, the same answer a bad identifier would get anywhere else: no
Student is ever shown a consent prompt that is not theirs.

**Consent is an explicit act by the Student, and only by the Student.**
Logging in is not consent. Using the LMS is not consent. Being invited is
not consent. There is no default, no pre-ticked box, no implicit acceptance
and no way for an Administrator or a Researcher to accept on somebody's
behalf -- no route in the project writes an ``accepted`` event with any
other actor.

**Only the active document can be accepted.** A draft or superseded document
is never presented and never accepted; the transaction re-proves the
document is still the current one, and that its digest still matches its
wording, under its lock. If the wording changed after this page was opened,
the submission is refused and the Student is asked to read the current
version -- nothing is written.

**Withdrawal** is a separate confirmation page and a separate POST. It is
recorded against the document that was actually accepted, so it does not
depend on the center having published newer wording. It cannot happen
without a prior acceptance, and nothing reactivates a withdrawn participant:
an old form, a replayed POST or a back-button submit all fail closed.

**A repeated request creates no false history.** An action the current state
already satisfies is an authorized no-op -- no write, no version move, no
second event -- and anything else whose state has moved is refused as stale.

**The activated document is the only substantive wording (M01R, M01R2).**
The page carries the Student's own status, operational instructions, the
action, a precise statement of what this milestone records and what it does
not collect, and nothing else. Anything that could materially affect the
decision -- what the study looks at, what would be collected, how long it is
kept, what withdrawing means for it, whether a reason is required, and
whether a record is ever removed -- is in the document body, which is exactly
what the consent event's stored version and digest seal. Static template text
is not covered by that digest and can be edited without any consent record
changing, so it must never carry such a claim.

**Participant rights are terms, not implementation notes (M01R2).** This
implementation really is append-only: the mapper guards refuse an update or a
delete of a consent event, and no route deletes one. That is a property of
the code, which a later Part could change; a term a participant accepted
cannot be changed. So "you can withdraw at any time", "without giving a
reason", "a permanent record" and "never edited or deleted" are no longer
stated here or in any flash message -- they belong to the document, and the
pages point at it.

**What M01 does record** is the invitation and the consent decision: the
participant row, its status, the consent-document reference and the
append-only consent events. **What M01 does not collect** is behavioural
interaction data -- no interaction events, experiment or task sessions,
surveys, frustration ratings, observer annotations, exports, datasets, model
training, inference or adaptive intervention.

Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: this is one person's decision about taking part in
research.
"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.models import ResearchConsentAction, UserRole
from app.security.decorators import roles_required
from app.services import research_queries as queries
from app.services import research_tokens as tokens
from app.services import research_transactions as tx

_STUDENT = UserRole.STUDENT.value

#: The hidden form field every consent form carries its signed token in.
_STATE_FIELD = "state_token"

#: The intended action each route asks for, and the history action it
#: records. Kept together so a route cannot ask for one and record another.
_ACTIONS = {
    tokens.ACTION_ACCEPT: ResearchConsentAction.ACCEPTED.value,
    tokens.ACTION_DECLINE: ResearchConsentAction.DECLINED.value,
    tokens.ACTION_WITHDRAW: ResearchConsentAction.WITHDRAWN.value,
}

_STALE_MESSAGE = (
    "This consent page no longer describes the current wording or your current status, "
    "so nothing was recorded. Read the current page and decide again."
)
_DOCUMENT_CHANGED_MESSAGE = (
    "The consent document changed after this page was opened, so nothing was recorded. "
    "Please read the current version before deciding."
)
_NO_DOCUMENT_MESSAGE = (
    "There is no active consent document right now, so no decision can be recorded."
)
_NOT_ALLOWED_MESSAGE = "That is not a decision you can take from your current status."
_CONFLICT_MESSAGE = (
    "That could not be saved because of a conflicting change. Nothing was written. "
    "Please try again."
)
_UNAUTHORIZED_MESSAGE = "Your account is no longer able to record a consent decision."
_CONFIRM_MESSAGE = "Tick the confirmation box before withdrawing from the study."

#: M01R, tightened by M01R2: each message confirms what was *recorded* and
#: nothing else -- not the study's scope, not retention, not consequences,
#: and not a participant right such as withdrawing at any time or history
#: being kept permanently. Those belong to the activated document, whose
#: digest the consent event seals; a flash message is unversioned text that
#: could be edited without any record changing.
_DONE = {
    tokens.ACTION_ACCEPT: (
        "Your acceptance was recorded against the consent document version you read.",
        "success",
    ),
    tokens.ACTION_DECLINE: (
        "Recorded: you have declined to take part.",
        "info",
    ),
    tokens.ACTION_WITHDRAW: (
        "Your withdrawal was recorded.",
        "success",
    ),
}
_ALREADY = {
    tokens.ACTION_ACCEPT: "You have already accepted. Nothing was changed.",
    tokens.ACTION_DECLINE: "You have already declined. Nothing was changed.",
    tokens.ACTION_WITHDRAW: "You have already withdrawn. Nothing was changed.",
}


def _consent_url():
    return url_for("student.research_consent")


def _state(participant, document, action):
    """The exact facts a decision token is bound to."""
    return {
        "participant_public_id": participant.public_id,
        "participant_status": participant.status,
        "participant_version": participant.version,
        "document_public_id": document.public_id,
        "consent_version": document.version_identifier,
        "consent_digest": document.body_digest,
        "action": action,
    }


def _token_for(participant, document, action):
    return tokens.make_token(
        tokens.PURPOSE_CONSENT_DECISION,
        actor_public_id=current_user.public_id,
        **_state(participant, document, action),
    )


def _own_participant_or_404():
    participant = queries.student_participant(current_user.id)
    if participant is None:
        abort(404)
    return participant


@student_bp.get("/research-consent")
@roles_required(_STUDENT)
def research_consent():
    """The acting Student's own consent page."""
    participant, document = queries.student_consent_state(current_user.id)
    if participant is None:
        abort(404)
    # A document whose stored digest no longer describes its stored wording
    # is never presented as consent text, however it came to disagree.
    presentable = (
        document is not None
        and tokens.document_status_is_presentable(document.status)
        and document.digest_matches()
    )
    offered = tokens.decision_action_for(participant.status)
    # Withdrawal is offered against the document that was accepted, which
    # may have been superseded since -- so it does not require a presentable
    # current document, only the Student's own accepted one.
    can_act = document is not None and (
        presentable or offered == (tokens.ACTION_WITHDRAW,)
    )
    return private_no_store(
        "student/research/consent.html",
        participant=participant,
        status_label=queries.PARTICIPANT_STATUS_LABELS.get(
            participant.status, participant.status
        ),
        document=document if (presentable or participant.consent_document_id) else None,
        document_presentable=presentable,
        offered=offered if can_act else (),
        accept_token=_token_for(participant, document, tokens.ACTION_ACCEPT)
        if can_act and tokens.ACTION_ACCEPT in offered
        else None,
        decline_token=_token_for(participant, document, tokens.ACTION_DECLINE)
        if can_act and tokens.ACTION_DECLINE in offered
        else None,
        state_field=_STATE_FIELD,
        accept_url=url_for("student.research_consent_accept"),
        decline_url=url_for("student.research_consent_decline"),
        withdraw_url=url_for("student.research_consent_withdraw"),
        actions=tokens,
    )


@student_bp.get("/research-consent/withdraw")
@roles_required(_STUDENT)
def research_consent_withdraw():
    """The separate withdrawal confirmation page."""
    participant, document = queries.student_consent_state(current_user.id)
    if participant is None:
        abort(404)
    if not participant.is_active or document is None:
        # Nothing to withdraw from. Not an error page: the consent page
        # itself states the current status honestly.
        return redirect(_consent_url())
    return private_no_store(
        "student/research/withdraw.html",
        participant=participant,
        document=document,
        state_field=_STATE_FIELD,
        state_token=_token_for(participant, document, tokens.ACTION_WITHDRAW),
        withdraw_url=url_for("student.research_consent_withdraw_confirm"),
        cancel_url=_consent_url(),
    )


@student_bp.post("/research-consent/accept")
@roles_required(_STUDENT)
def research_consent_accept():
    """Record an explicit acceptance by the acting Student."""
    return _decide(tokens.ACTION_ACCEPT)


@student_bp.post("/research-consent/decline")
@roles_required(_STUDENT)
def research_consent_decline():
    """Record an explicit refusal by the acting Student."""
    return _decide(tokens.ACTION_DECLINE)


@student_bp.post("/research-consent/withdraw")
@roles_required(_STUDENT)
def research_consent_withdraw_confirm():
    """Withdraw an earlier acceptance, after the confirmation page."""
    if request.form.get("confirm") != "yes":
        flash(_CONFIRM_MESSAGE, "warning")
        return redirect(url_for("student.research_consent_withdraw"))
    return _decide(tokens.ACTION_WITHDRAW)


def _decide(action):
    """One consent decision: verify, then let the transaction decide."""
    participant = _own_participant_or_404()
    token = request.form.get(_STATE_FIELD)
    actor_public_id = current_user.public_id

    def _stale_check(status, version, document_public_id, consent_version, consent_digest):
        """Re-proved against the **locked** participant and document rows,
        inside the transaction."""
        return tokens.token_is_stale(
            token,
            tokens.PURPOSE_CONSENT_DECISION,
            actor_public_id=actor_public_id,
            participant_public_id=participant.public_id,
            participant_status=status,
            participant_version=version,
            document_public_id=document_public_id,
            consent_version=consent_version,
            consent_digest=consent_digest,
            action=action,
        )

    outcome = tx.record_consent_decision(current_user.id, _ACTIONS[action], _stale_check)
    if outcome == tx.RECORDED:
        message, category = _DONE[action]
        flash(message, category)
    elif outcome == tx.ALREADY:
        flash(_ALREADY[action], "info")
    elif outcome == tx.NOT_FOUND:
        abort(404)
    elif outcome == tx.NO_ACTIVE_DOCUMENT:
        flash(_NO_DOCUMENT_MESSAGE, "warning")
    elif outcome == tx.DOCUMENT_CHANGED:
        flash(_DOCUMENT_CHANGED_MESSAGE, "warning")
    elif outcome == tx.NOT_ALLOWED:
        flash(_NOT_ALLOWED_MESSAGE, "warning")
    elif outcome == tx.STALE:
        flash(_STALE_MESSAGE, "warning")
    elif outcome == tx.UNAUTHORIZED:
        flash(_UNAUTHORIZED_MESSAGE, "danger")
    else:
        flash(_CONFLICT_MESSAGE, "danger")
    return redirect(_consent_url())
