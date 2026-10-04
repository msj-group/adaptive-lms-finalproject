"""Private Teacher-Student messaging (Phase 4 / M11).

One shared blueprint under ``/messages`` for both roles::

    GET   /messages                                   the private inbox
    GET   /messages/new                               recipient picker / compose
    POST  /messages/new                               create a thread
    GET   /messages/threads/<thread_public_id>        one conversation
    POST  /messages/threads/<thread_public_id>/reply  append one reply

There is deliberately no edit, delete, unsend, archive or read-receipt
route: a message is immutable and a thread is append-only.

**Authorization.** ``roles_required(STUDENT, TEACHER)`` gives the
project's standard behaviour -- anonymous users follow the login redirect,
and Administrator / Researcher receive 403 and can never list, open or
search a private message. Reading a thread requires a membership row for
the acting user in the same query that finds the thread, so a malformed,
unknown or somebody else's thread id is the same non-disclosing 404.
Sending requires, on every POST, the current academic relationship proved
against locked rows by ``app/services/message_transactions.py``. Nothing
about the sender, the roles, the membership or the relationship is read
from the request; the only client values are the recipient or thread
public id, the text, and the signed state token.

**Historical access versus sending.** A member keeps reading a thread
after the shared Group ends; the page then shows it as read-only and the
reply route refuses a POST regardless of what the page showed.

**Duplicate submissions.** Each compose and reply form carries a signed,
expiring token (``app/services/message_tokens.py``) binding the actor, the
recipient or thread, and a nonce that becomes the UNIQUE
``creation_nonce``. A replay of an already-used nonce leads back to the
thread instead of writing again, and every successful POST redirects
(POST/Redirect/GET) to a page that mints a fresh token.

**Notifications** are delivered after the domain commit, best-effort, to
the other member only (``notify_message_received``).

**Every page carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``**: the content is one person's correspondence.
"""

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

from app.blueprints.collector.hooks import note_outcome
from app.blueprints.messages import messages_bp
from app.models import MESSAGE_BODY_MAX_LENGTH, MESSAGE_SUBJECT_MAX_LENGTH, UserRole
from app.security.decorators import roles_required
from app.services import message_transactions
from app.services.message_queries import (
    CONVERSATION_PAGE_SIZE,
    PAGE_SIZE,
    RECIPIENT_LIMIT,
    ROLE_LABELS,
    build_conversation_view,
    build_inbox_view,
    canonical_public_id,
    conversation_page,
    inbox_page,
    member_thread,
    normalize_page,
    orient_pair,
    permitted_recipient,
    permitted_recipients,
    reply_sent_with_nonce,
    reply_state,
    thread_created_with_nonce,
)
from app.services.message_text import (
    CONTROL,
    MISSING,
    RECIPIENT_QUERY_MAX_LENGTH,
    TOO_LONG,
    normalize_body,
    normalize_recipient_query,
    normalize_subject,
)
from app.services.message_tokens import (
    PURPOSE_CREATE,
    PURPOSE_REPLY,
    load_token,
    make_token,
)
from app.services.notification_delivery import notify_message_received

_MEMBER_ROLES = (UserRole.STUDENT.value, UserRole.TEACHER.value)

_SUBJECT_ERRORS = {
    MISSING: "Enter a subject.",
    CONTROL: "The subject contains characters that cannot be used.",
    TOO_LONG: f"The subject must be at most {MESSAGE_SUBJECT_MAX_LENGTH} characters.",
}

_BODY_ERRORS = {
    MISSING: "Enter a message.",
    CONTROL: "The message contains characters that cannot be used.",
    TOO_LONG: f"The message must be at most {MESSAGE_BODY_MAX_LENGTH} characters.",
}

_RECIPIENT_UNAVAILABLE = (
    "That person is not available to message right now. You can message a teacher or "
    "student you currently share an active group with."
)
_FORM_UNVERIFIED = (
    "This form could not be verified. It may have expired or been opened for a different "
    "conversation. Review your message and send it again."
)
_READ_ONLY = (
    "You can no longer reply in this conversation because you no longer share an active "
    "group. You can still read it."
)
_CONFLICT = "Your message could not be sent because of a conflicting change. Please try again."


#: Research outcomes are noted only for Student senders (Phase 6).
_STUDENT_ROLE = UserRole.STUDENT.value


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _private(template, **context):
    response = make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def _audience_label(role):
    """How the acting role names the people it can message."""
    return "teachers" if role == UserRole.STUDENT.value else "students"


# ----------------------------------------------------------------------
# Inbox
# ----------------------------------------------------------------------


@messages_bp.get("")
@roles_required(*_MEMBER_ROLES)
def inbox():
    """The acting user's own threads, newest message first, 20 per page."""
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))
    rows, has_next = inbox_page(current_user.id, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = inbox_page(current_user.id, page)
    return _private(
        "messages/inbox.html",
        conversations=build_inbox_view(rows, current_user.id, tz_name),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
        audience=_audience_label(current_user.role),
        tz_name=tz_name,
    )


# ----------------------------------------------------------------------
# New thread
# ----------------------------------------------------------------------


def _render_picker(actor_id, actor_role):
    query_text = normalize_recipient_query(request.args.get("q"))
    return _private(
        "messages/new.html",
        mode="picker",
        recipients=permitted_recipients(actor_id, actor_role, query_text),
        query_text=query_text,
        query_max=RECIPIENT_QUERY_MAX_LENGTH,
        recipient_limit=RECIPIENT_LIMIT,
        audience=_audience_label(actor_role),
    )


def _render_compose(recipient, actor_public_id, subject="", body="", nonce=None,
                    errors=None, form_error=None):
    """The compose form for one permitted recipient, with a create token
    bound to this actor and this recipient. A re-render after a text error
    keeps the original nonce; any other render mints a new one."""
    return _private(
        "messages/new.html",
        mode="compose",
        recipient={
            "public_id": recipient["public_id"],
            "full_name": recipient["full_name"],
            "role_label": ROLE_LABELS[recipient["role"]],
        },
        message_state=make_token(
            PURPOSE_CREATE, actor_public_id, recipient["public_id"], nonce
        ),
        subject=subject,
        body=body,
        errors=errors or {},
        form_error=form_error,
        subject_max=MESSAGE_SUBJECT_MAX_LENGTH,
        body_max=MESSAGE_BODY_MAX_LENGTH,
    )


def _to_picker(message=_RECIPIENT_UNAVAILABLE, category="warning"):
    flash(message, category)
    return redirect(url_for("messages.new_thread"))


@messages_bp.get("/new")
@roles_required(*_MEMBER_ROLES)
def new_thread():
    """Without ``to``: the bounded list of currently permitted recipients.
    With ``to=<public id>``: the compose form, only if that person is a
    permitted recipient right now -- otherwise the same generic notice for
    every reason."""
    actor_id, actor_role = current_user.id, current_user.role
    requested = request.args.get("to")
    if requested is None:
        return _render_picker(actor_id, actor_role)
    recipient = permitted_recipient(actor_id, actor_role, requested)
    if recipient is None:
        return _to_picker()
    return _render_compose(recipient, current_user.public_id)


@messages_bp.post("/new")
@roles_required(*_MEMBER_ROLES)
def create_thread():
    """Create a thread with its first message.

    Order: identify the recipient field, verify the token (and resolve an
    already-used nonce back to its thread), re-check the recipient with a
    non-authoritative preview, validate the text, then hand over to the
    locked transaction, which proves everything again before writing.
    """
    # Captured once, before any transaction reset can expire the row.
    actor_id = current_user.id
    actor_role = current_user.role
    actor_public_id = current_user.public_id

    recipient_public_id = canonical_public_id(request.form.get("recipient"))
    if recipient_public_id is None:
        return _to_picker()
    subject_raw = request.form.get("subject", "")
    body_raw = request.form.get("body", "")

    payload = load_token(
        PURPOSE_CREATE, request.form.get("message_state"), actor_public_id, recipient_public_id
    )
    if payload is not None:
        existing = thread_created_with_nonce(actor_id, payload["nonce"])
        if existing is not None:
            flash("This message was already sent.", "info")
            return redirect(url_for("messages.thread", thread_public_id=existing))

    recipient = permitted_recipient(actor_id, actor_role, recipient_public_id)
    if recipient is None:
        return _to_picker()
    if payload is None:
        return _render_compose(
            recipient, actor_public_id, subject=subject_raw, body=body_raw,
            form_error=_FORM_UNVERIFIED,
        )

    subject, subject_error = normalize_subject(subject_raw)
    body, body_error = normalize_body(body_raw)
    errors = {}
    if subject_error:
        errors["subject"] = _SUBJECT_ERRORS[subject_error]
    if body_error:
        errors["body"] = _BODY_ERRORS[body_error]
    if errors:
        if actor_role == _STUDENT_ROLE:
            note_outcome("message_send", "rejected")
        return _render_compose(
            recipient, actor_public_id, subject=subject_raw, body=body_raw,
            nonce=payload["nonce"], errors=errors,
        )

    pair = orient_pair(actor_id, actor_role, recipient["id"], recipient["role"])
    if pair is None:  # pragma: no cover -- the recipient query only yields the opposite role
        return _to_picker()
    student_id, teacher_id = pair
    outcome = message_transactions.send_new_thread(
        actor_id, recipient["id"], student_id, teacher_id, subject, body, payload["nonce"]
    )

    if outcome.status == message_transactions.SENT:
        if actor_role == _STUDENT_ROLE:
            note_outcome("message_send", "sent")
        flash("Message sent.", "success")
        response = redirect(
            url_for("messages.thread", thread_public_id=outcome.thread_public_id)
        )
        notify_message_received(outcome.message_id)
        return response
    if outcome.status == message_transactions.DUPLICATE:
        flash("This message was already sent.", "info")
        return redirect(url_for("messages.thread", thread_public_id=outcome.thread_public_id))
    if outcome.status == message_transactions.UNAVAILABLE:
        return _to_picker()
    return _to_picker(_CONFLICT, "danger")


# ----------------------------------------------------------------------
# Conversation
# ----------------------------------------------------------------------


def _thread_or_404(user_id, thread_public_id):
    thread = member_thread(user_id, thread_public_id)
    if thread is None:
        abort(404)
    return thread


def _render_thread(thread, viewer_id, viewer_public_id, draft="", nonce=None,
                   body_error=None, form_error=None):
    """The conversation page. Read-only when the current relationship no
    longer allows a reply; a reply token is minted only when it does."""
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))
    rows, has_older = conversation_page(thread["id"], page)
    if not rows and page > 1:
        page = 1
        rows, has_older = conversation_page(thread["id"], page)
    state = reply_state(thread["id"], viewer_id)
    can_reply = state.relationship is not None
    other = None
    if state.other is not None:
        other = {
            "full_name": state.other["full_name"],
            "role_label": ROLE_LABELS.get(state.other["role"], "Member"),
        }
    return _private(
        "messages/thread.html",
        thread={"public_id": thread["public_id"], "subject": thread["subject"]},
        messages=build_conversation_view(rows, viewer_id, tz_name),
        other=other,
        page=page,
        has_older=has_older,
        has_newer=page > 1,
        page_size=CONVERSATION_PAGE_SIZE,
        can_reply=can_reply,
        message_state=(
            make_token(PURPOSE_REPLY, viewer_public_id, thread["public_id"], nonce)
            if can_reply
            else None
        ),
        draft=draft,
        body_error=body_error,
        form_error=form_error,
        body_max=MESSAGE_BODY_MAX_LENGTH,
        tz_name=tz_name,
    )


@messages_bp.get("/threads/<thread_public_id>")
@roles_required(*_MEMBER_ROLES)
def thread(thread_public_id):
    """One conversation, 50 messages per page in reading order. Reads
    never write."""
    found = _thread_or_404(current_user.id, thread_public_id)
    return _render_thread(found, current_user.id, current_user.public_id)


@messages_bp.post("/threads/<thread_public_id>/reply")
@roles_required(*_MEMBER_ROLES)
def reply(thread_public_id):
    """Append one reply, only while the relationship still allows it."""
    actor_id = current_user.id
    actor_public_id = current_user.public_id
    actor_role = current_user.role

    found = _thread_or_404(actor_id, thread_public_id)
    thread_url = url_for("messages.thread", thread_public_id=found["public_id"])
    body_raw = request.form.get("body", "")

    payload = load_token(
        PURPOSE_REPLY, request.form.get("message_state"), actor_public_id, found["public_id"]
    )
    if payload is not None and reply_sent_with_nonce(actor_id, found["id"], payload["nonce"]):
        flash("This reply was already sent.", "info")
        return redirect(thread_url)

    state = reply_state(found["id"], actor_id)
    if state.relationship is None:
        flash(_READ_ONLY, "warning")
        return redirect(thread_url)
    if payload is None:
        return _render_thread(
            found, actor_id, actor_public_id, draft=body_raw, form_error=_FORM_UNVERIFIED
        )

    body, body_error = normalize_body(body_raw)
    if body_error:
        if actor_role == _STUDENT_ROLE:
            note_outcome("message_send", "rejected")
        return _render_thread(
            found, actor_id, actor_public_id, draft=body_raw, nonce=payload["nonce"],
            body_error=_BODY_ERRORS[body_error],
        )

    outcome = message_transactions.send_reply(
        actor_id, state.other["user_id"], state.student_id, state.teacher_id,
        found["id"], body, payload["nonce"],
    )
    if outcome.status == message_transactions.SENT:
        if actor_role == _STUDENT_ROLE:
            note_outcome("message_send", "sent")
        flash("Reply sent.", "success")
        response = redirect(thread_url)
        notify_message_received(outcome.message_id)
        return response
    if outcome.status == message_transactions.DUPLICATE:
        flash("This reply was already sent.", "info")
        return redirect(thread_url)
    if outcome.status == message_transactions.UNAVAILABLE:
        flash(_READ_ONLY, "warning")
        return redirect(thread_url)
    flash(_CONFLICT, "danger")
    return redirect(thread_url)
