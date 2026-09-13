"""Teacher Group discussions (Phase 4 / M12).

Eight Teacher-only routes, every object addressed by ``public_id`` and no
internal numeric id anywhere in a URL, a form value, a signed token or the
rendered HTML::

    GET   /teacher/discussions
    GET   /teacher/groups/<gp>/discussions
    GET   /teacher/groups/<gp>/discussions/new
    POST  /teacher/groups/<gp>/discussions/new
    GET   /teacher/groups/<gp>/discussions/<tp>
    POST  /teacher/groups/<gp>/discussions/<tp>/reply
    POST  /teacher/groups/<gp>/discussions/<tp>/lock
    POST  /teacher/groups/<gp>/discussions/<tp>/reopen

There is deliberately no edit, delete, hide, restore, pin or reaction
route: a topic's content and every reply are immutable, and locking is the
only moderation there is.

**Authorization is the current Group relationship.**
``roles_required(TEACHER)`` gives the project's standard behaviour --
anonymous users follow the login redirect, every other role receives 403.
Every Group is then found through
:func:`~app.services.discussion_queries.member_group`, which proves in SQL
an active account with the Teacher role, an **active**
``GroupTeacherAssignment`` to exactly that Group, and an active Group,
Course, Level and AcademicTerm; a topic is found only with that Group's
id in its ``WHERE`` clause. A malformed, unknown, cross-Group or
unauthorized identifier, a removed assignment and an archived link are
therefore the same non-disclosing 404. Unlike the Group management pages,
nothing here stays reachable under an archived chain: a classroom
discussion belongs to a running class.

**Every write is proved again under locks.**
``app/services/discussion_transactions.py`` takes the route-specific lock
chain and re-proves the relationship, the operational chain, the topic's
Group and its lock state before writing; the checks here are previews
that choose the response, never the authority.

**Signed state and duplicates.** Each form carries an exact-shape signed
token (``app/services/discussion_tokens.py``). Creation and reply tokens
carry the nonce that becomes the UNIQUE ``creation_nonce``, so a replay
leads back to the row it already created; reply and moderation tokens
carry the topic's version, so a form opened before a lock or reopen is
recognised as stale rather than silently applied. Every successful POST
redirects (POST/Redirect/GET) to a page that mints fresh state.

**One notification, once.** Only a topic's original creation notifies,
after its commit, through the best-effort producer.

**Every page carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``**: it shows one Group's classroom conversation to the
people currently in that Group.
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import _private_no_store
from app.models import (
    DISCUSSION_BODY_MAX_LENGTH,
    DISCUSSION_TITLE_MAX_LENGTH,
    DiscussionTopicStatus,
    UserRole,
)
from app.security.decorators import roles_required
from app.services import discussion_transactions as tx
from app.services.discussion_queries import (
    GROUP_LIMIT,
    REPLY_PAGE_SIZE,
    TOPIC_PAGE_SIZE,
    build_group_cards,
    build_reply_view,
    build_topic_list_view,
    group_topic,
    member_group,
    member_groups,
    normalize_page,
    public_group,
    public_topic,
    replies_page,
    reply_counts,
    reply_created_with_nonce,
    reply_page_number,
    topic_counts,
    topic_created_with_nonce,
    topics_page,
)
from app.services.discussion_text import (
    CONTROL,
    MISSING,
    TOO_LONG,
    normalize_body,
    normalize_title,
)
from app.services.discussion_tokens import (
    ACTION_LOCK,
    ACTION_REOPEN,
    load_create_token,
    load_moderation_token,
    load_reply_token,
    make_create_token,
    make_moderation_token,
    make_reply_token,
)
from app.services.notification_delivery import notify_discussion_topic_created

_TEACHER = UserRole.TEACHER.value
_OPEN = DiscussionTopicStatus.OPEN.value

_TITLE_ERRORS = {
    MISSING: "Enter a title.",
    CONTROL: "The title contains characters that cannot be used.",
    TOO_LONG: f"The title must be at most {DISCUSSION_TITLE_MAX_LENGTH} characters.",
}
_BODY_ERRORS = {
    MISSING: "Enter the topic text.",
    CONTROL: "The topic text contains characters that cannot be used.",
    TOO_LONG: f"The topic text must be at most {DISCUSSION_BODY_MAX_LENGTH} characters.",
}
_REPLY_ERRORS = {
    MISSING: "Enter a reply.",
    CONTROL: "The reply contains characters that cannot be used.",
    TOO_LONG: f"The reply must be at most {DISCUSSION_BODY_MAX_LENGTH} characters.",
}

_FORM_UNVERIFIED = (
    "This form could not be verified. It may have expired or been opened for a different "
    "group or topic. Review your text and submit it again."
)
_TOPIC_CHANGED = (
    "This topic was locked or reopened after the page was opened, so your reply was not "
    "posted. Review the discussion and submit your reply again."
)
_TOPIC_LOCKED = "This topic is locked. It can still be read, but no new replies can be added."
_CONFLICT = (
    "That could not be saved because of a conflicting change. Nothing was written. "
    "Please try again."
)
_MODERATION_UNVERIFIED = (
    "That action could not be verified. It may have expired. Reload the topic and try again."
)
_MODERATION_STALE = (
    "This topic was locked or reopened by someone else after the page was opened. Nothing "
    "was changed. Review its current state and try again."
)
_MODERATION_DONE = {
    ACTION_LOCK: "Topic locked. It stays readable, and no new replies can be added.",
    ACTION_REOPEN: "Topic reopened. Replies can be added again.",
}
_MODERATION_ALREADY = {
    ACTION_LOCK: "This topic is already locked. Nothing was changed.",
    ACTION_REOPEN: "This topic is already open. Nothing was changed.",
}


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _group_or_404(actor_id, group_public_id):
    group = member_group(actor_id, _TEACHER, group_public_id)
    if group is None:
        abort(404)
    return group


def _topic_or_404(group, topic_public_id):
    topic = group_topic(group["id"], topic_public_id)
    if topic is None:
        abort(404)
    return topic


def _topic_url(group_public_id, topic_public_id, page=1, anchor=None):
    values = {"group_public_id": group_public_id, "topic_public_id": topic_public_id}
    if page > 1:
        values["page"] = page
    if anchor:
        values["_anchor"] = anchor
    return url_for("teacher.discussions_topic", **values)


def _reply_url(group_public_id, topic, reply_public_id):
    """The timeline page that shows one reply, anchored at it."""
    page = reply_page_number(topic["id"], reply_public_id)
    return _topic_url(group_public_id, topic["public_id"], page, f"reply-{reply_public_id}")


# ----------------------------------------------------------------------
# Overview and topic list
# ----------------------------------------------------------------------


@teacher_bp.get("/discussions")
@roles_required(_TEACHER)
def discussions_overview():
    """The Groups this Teacher may currently discuss in, with topic counts."""
    groups, truncated = member_groups(current_user.id, _TEACHER)
    counts = topic_counts([group["id"] for group in groups])
    return _private_no_store(
        "teacher/discussions/overview.html",
        groups=build_group_cards(groups, counts),
        truncated=truncated,
        group_limit=GROUP_LIMIT,
    )


@teacher_bp.get("/groups/<group_public_id>/discussions")
@roles_required(_TEACHER)
def discussions_group(group_public_id):
    """One Group's topics, newest first, 20 per page."""
    group = _group_or_404(current_user.id, group_public_id)
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))
    rows, has_next = topics_page(group["id"], page)
    if not rows and page > 1:
        page = 1
        rows, has_next = topics_page(group["id"], page)
    counts = reply_counts([row[0] for row in rows])
    return _private_no_store(
        "teacher/discussions/list.html",
        group=public_group(group),
        topics=build_topic_list_view(rows, counts, tz_name),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=TOPIC_PAGE_SIZE,
        tz_name=tz_name,
    )


# ----------------------------------------------------------------------
# Topic creation
# ----------------------------------------------------------------------


def _render_new(group, actor_public_id, title="", body="", nonce=None, errors=None,
                form_error=None):
    """The creation form with a token bound to this Teacher and Group. A
    re-render after a text error keeps the original nonce; any other
    render mints a new one."""
    return _private_no_store(
        "teacher/discussions/new.html",
        group=public_group(group),
        topic_state=make_create_token(actor_public_id, group["public_id"], nonce),
        title=title,
        body=body,
        errors=errors or {},
        form_error=form_error,
        title_max=DISCUSSION_TITLE_MAX_LENGTH,
        body_max=DISCUSSION_BODY_MAX_LENGTH,
    )


@teacher_bp.get("/groups/<group_public_id>/discussions/new")
@roles_required(_TEACHER)
def discussions_new(group_public_id):
    group = _group_or_404(current_user.id, group_public_id)
    return _render_new(group, current_user.public_id)


@teacher_bp.post("/groups/<group_public_id>/discussions/new")
@roles_required(_TEACHER)
def discussions_create(group_public_id):
    """Create one open topic.

    Order: authorize the Group, verify the token (and resolve an already-
    used nonce back to its topic), validate the text, then hand over to
    the locked transaction, which proves everything again before writing.
    """
    # Captured once, before any transaction reset can expire the row.
    actor_id = current_user.id
    actor_public_id = current_user.public_id

    group = _group_or_404(actor_id, group_public_id)
    title_raw = request.form.get("title", "")
    body_raw = request.form.get("body", "")

    payload = load_create_token(
        request.form.get("topic_state"), actor_public_id, group["public_id"]
    )
    if payload is None:
        return _render_new(
            group, actor_public_id, title_raw, body_raw, form_error=_FORM_UNVERIFIED
        )
    existing = topic_created_with_nonce(actor_id, group["id"], payload["nonce"])
    if existing is not None:
        flash("This topic was already created.", "info")
        return redirect(_topic_url(group["public_id"], existing))

    title, title_error = normalize_title(title_raw)
    body, body_error = normalize_body(body_raw)
    errors = {}
    if title_error:
        errors["title"] = _TITLE_ERRORS[title_error]
    if body_error:
        errors["body"] = _BODY_ERRORS[body_error]
    if errors:
        return _render_new(
            group, actor_public_id, title_raw, body_raw, nonce=payload["nonce"], errors=errors
        )

    outcome = tx.create_topic(actor_id, group["public_id"], title, body, payload["nonce"])
    if outcome.status == tx.CREATED:
        flash("Topic created.", "success")
        response = redirect(_topic_url(group["public_id"], outcome.public_id))
        notify_discussion_topic_created(outcome.object_id)
        return response
    if outcome.status == tx.DUPLICATE:
        flash("This topic was already created.", "info")
        return redirect(_topic_url(group["public_id"], outcome.public_id))
    if outcome.status == tx.UNAVAILABLE:
        abort(404)
    return _render_new(group, actor_public_id, title_raw, body_raw, form_error=_CONFLICT)


# ----------------------------------------------------------------------
# Topic page and replies
# ----------------------------------------------------------------------


def _render_topic(group, topic, viewer_id, viewer_public_id, draft="", nonce=None,
                  body_error=None, form_error=None):
    """The topic page: the topic, 50 replies per page in reading order,
    the moderation control for the current state, and -- only while the
    topic is open -- the reply form."""
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))
    rows, has_next = replies_page(topic["id"], page)
    if not rows and page > 1:
        page = 1
        rows, has_next = replies_page(topic["id"], page)
    can_reply = topic["status"] == _OPEN
    moderation_action = ACTION_LOCK if can_reply else ACTION_REOPEN
    return _private_no_store(
        "teacher/discussions/detail.html",
        group=public_group(group),
        topic=public_topic(topic, tz_name),
        replies=build_reply_view(rows, viewer_id, tz_name),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=REPLY_PAGE_SIZE,
        can_reply=can_reply,
        reply_state=(
            make_reply_token(
                viewer_public_id, group["public_id"], topic["public_id"], topic["version"], nonce
            )
            if can_reply
            else None
        ),
        moderation_action=moderation_action,
        moderation_state=make_moderation_token(
            viewer_public_id, group["public_id"], topic["public_id"], topic["version"],
            moderation_action,
        ),
        draft=draft,
        body_error=body_error,
        form_error=form_error,
        body_max=DISCUSSION_BODY_MAX_LENGTH,
        tz_name=tz_name,
    )


@teacher_bp.get("/groups/<group_public_id>/discussions/<topic_public_id>")
@roles_required(_TEACHER)
def discussions_topic(group_public_id, topic_public_id):
    """One topic and its replies. Reads never write."""
    group = _group_or_404(current_user.id, group_public_id)
    topic = _topic_or_404(group, topic_public_id)
    return _render_topic(group, topic, current_user.id, current_user.public_id)


@teacher_bp.post("/groups/<group_public_id>/discussions/<topic_public_id>/reply")
@roles_required(_TEACHER)
def discussions_reply(group_public_id, topic_public_id):
    """Append one reply, only while the topic is open."""
    actor_id = current_user.id
    actor_public_id = current_user.public_id

    group = _group_or_404(actor_id, group_public_id)
    topic = _topic_or_404(group, topic_public_id)
    topic_url = _topic_url(group["public_id"], topic["public_id"])
    body_raw = request.form.get("body", "")

    payload = load_reply_token(
        request.form.get("reply_state"), actor_public_id, group["public_id"], topic["public_id"]
    )
    if payload is not None:
        existing = reply_created_with_nonce(actor_id, topic["id"], payload["nonce"])
        if existing is not None:
            flash("This reply was already posted.", "info")
            return redirect(_reply_url(group["public_id"], topic, existing))
    if topic["status"] != _OPEN:
        flash(_TOPIC_LOCKED, "warning")
        return redirect(topic_url)
    if payload is None:
        return _render_topic(
            group, topic, actor_id, actor_public_id, draft=body_raw, form_error=_FORM_UNVERIFIED
        )
    if payload["topic_version"] != topic["version"]:
        return _render_topic(
            group, topic, actor_id, actor_public_id, draft=body_raw, form_error=_TOPIC_CHANGED
        )

    body, body_error = normalize_body(body_raw)
    if body_error:
        return _render_topic(
            group, topic, actor_id, actor_public_id, draft=body_raw, nonce=payload["nonce"],
            body_error=_REPLY_ERRORS[body_error],
        )

    outcome = tx.create_reply(
        actor_id, _TEACHER, group["public_id"], topic["id"], payload["topic_version"], body,
        payload["nonce"],
    )
    if outcome.status == tx.CREATED:
        flash("Reply posted.", "success")
        return redirect(_reply_url(group["public_id"], topic, outcome.public_id))
    if outcome.status == tx.DUPLICATE:
        flash("This reply was already posted.", "info")
        return redirect(_reply_url(group["public_id"], topic, outcome.public_id))
    if outcome.status == tx.UNAVAILABLE:
        abort(404)
    if outcome.status == tx.TOPIC_LOCKED:
        flash(_TOPIC_LOCKED, "warning")
        return redirect(topic_url)
    if outcome.status == tx.STALE:
        current = _topic_or_404(group, topic["public_id"])
        return _render_topic(
            group, current, actor_id, actor_public_id, draft=body_raw, form_error=_TOPIC_CHANGED
        )
    flash(_CONFLICT, "danger")
    return redirect(topic_url)


# ----------------------------------------------------------------------
# Lock and reopen
# ----------------------------------------------------------------------


def _moderate(group_public_id, topic_public_id, action):
    actor_id = current_user.id
    actor_public_id = current_user.public_id

    group = _group_or_404(actor_id, group_public_id)
    topic = _topic_or_404(group, topic_public_id)
    topic_url = _topic_url(group["public_id"], topic["public_id"])

    payload = load_moderation_token(
        request.form.get("moderation_state"), actor_public_id, group["public_id"],
        topic["public_id"], action,
    )
    if payload is None:
        flash(_MODERATION_UNVERIFIED, "danger")
        return redirect(topic_url)

    outcome = tx.moderate_topic(
        actor_id, group["public_id"], topic["id"], action, payload["topic_version"]
    )
    if outcome.status == tx.UNAVAILABLE:
        abort(404)
    if outcome.status == tx.CHANGED:
        flash(_MODERATION_DONE[action], "success")
    elif outcome.status == tx.ALREADY:
        flash(_MODERATION_ALREADY[action], "info")
    elif outcome.status == tx.STALE:
        flash(_MODERATION_STALE, "warning")
    else:
        flash(_CONFLICT, "danger")
    return redirect(topic_url)


@teacher_bp.post("/groups/<group_public_id>/discussions/<topic_public_id>/lock")
@roles_required(_TEACHER)
def discussions_lock(group_public_id, topic_public_id):
    """Lock an open topic: it stays readable and accepts no new reply."""
    return _moderate(group_public_id, topic_public_id, ACTION_LOCK)


@teacher_bp.post("/groups/<group_public_id>/discussions/<topic_public_id>/reopen")
@roles_required(_TEACHER)
def discussions_reopen(group_public_id, topic_public_id):
    """Reopen a locked topic for replies."""
    return _moderate(group_public_id, topic_public_id, ACTION_REOPEN)
