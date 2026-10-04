"""Student Group discussions (Phase 4 / M12).

Four Student-only routes, every object addressed by ``public_id``::

    GET   /student/discussions
    GET   /student/groups/<gp>/discussions
    GET   /student/groups/<gp>/discussions/<tp>
    POST  /student/groups/<gp>/discussions/<tp>/reply

A Student reads and replies. There is deliberately **no** Student route
that creates, locks, reopens, edits or deletes anything: not a control
hidden in a template, not a permission check that could be relaxed -- no
such endpoint exists, so a request aimed at one returns 404 or 405.

**Authorization is the current Enrollment.** ``roles_required(STUDENT)``
gives the project's standard behaviour -- anonymous users follow the
login redirect, every other role receives 403 -- and every Group is then
found through :func:`~app.services.discussion_queries.member_group`,
which proves in SQL an active Student account, an **active**
``Enrollment`` in exactly that Group and an active Group, Course, Level
and AcademicTerm. A topic is found only with that Group's id in its
``WHERE`` clause. A withdrawn Enrollment, a suspended account and an
archived link end access immediately; a malformed, unknown, cross-Group
or unauthorized identifier is the same non-disclosing 404. A stored
notification about a topic grants nothing: opening one lands here and is
judged on current state like any other request.

A reply is proved again under the locks of
``app/services/discussion_transactions.py`` -- the Enrollment, the
operational chain, the topic's Group, and that the locked topic is still
``open`` at the version the form was opened on -- before it is written.
Signed tokens, duplicate handling and response headers follow the Teacher
surface (``app/blueprints/teacher/discussions.py``).
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user

from app.blueprints.collector.hooks import note_outcome
from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.models import DISCUSSION_BODY_MAX_LENGTH, DiscussionTopicStatus, UserRole
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
    topics_page,
)
from app.services.discussion_text import CONTROL, MISSING, TOO_LONG, normalize_body
from app.services.discussion_tokens import load_reply_token, make_reply_token

_STUDENT = UserRole.STUDENT.value
_OPEN = DiscussionTopicStatus.OPEN.value

_REPLY_ERRORS = {
    MISSING: "Enter a reply.",
    CONTROL: "The reply contains characters that cannot be used.",
    TOO_LONG: f"The reply must be at most {DISCUSSION_BODY_MAX_LENGTH} characters.",
}

_FORM_UNVERIFIED = (
    "This form could not be verified. It may have expired or been opened for a different "
    "group or topic. Review your reply and submit it again."
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


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _group_or_404(actor_id, group_public_id):
    group = member_group(actor_id, _STUDENT, group_public_id)
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
    return url_for("student.discussions_topic", **values)


def _reply_url(group_public_id, topic, reply_public_id):
    page = reply_page_number(topic["id"], reply_public_id)
    return _topic_url(group_public_id, topic["public_id"], page, f"reply-{reply_public_id}")


@student_bp.get("/discussions")
@roles_required(_STUDENT)
def discussions_overview():
    """The Groups this Student is currently enrolled in, with topic counts."""
    groups, truncated = member_groups(current_user.id, _STUDENT)
    counts = topic_counts([group["id"] for group in groups])
    return private_no_store(
        "student/discussions/overview.html",
        groups=build_group_cards(groups, counts),
        truncated=truncated,
        group_limit=GROUP_LIMIT,
    )


@student_bp.get("/groups/<group_public_id>/discussions")
@roles_required(_STUDENT)
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
    return private_no_store(
        "student/discussions/list.html",
        group=public_group(group),
        topics=build_topic_list_view(rows, counts, tz_name),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=TOPIC_PAGE_SIZE,
        tz_name=tz_name,
    )


def _render_topic(group, topic, viewer_id, viewer_public_id, draft="", nonce=None,
                  body_error=None, form_error=None):
    """The topic page. The reply form -- and its token -- exist only while
    the topic is open."""
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))
    rows, has_next = replies_page(topic["id"], page)
    if not rows and page > 1:
        page = 1
        rows, has_next = replies_page(topic["id"], page)
    can_reply = topic["status"] == _OPEN
    return private_no_store(
        "student/discussions/detail.html",
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
        draft=draft,
        body_error=body_error,
        form_error=form_error,
        body_max=DISCUSSION_BODY_MAX_LENGTH,
        tz_name=tz_name,
    )


@student_bp.get("/groups/<group_public_id>/discussions/<topic_public_id>")
@roles_required(_STUDENT)
def discussions_topic(group_public_id, topic_public_id):
    """One topic and its replies. Reads never write."""
    group = _group_or_404(current_user.id, group_public_id)
    topic = _topic_or_404(group, topic_public_id)
    return _render_topic(group, topic, current_user.id, current_user.public_id)


@student_bp.post("/groups/<group_public_id>/discussions/<topic_public_id>/reply")
@roles_required(_STUDENT)
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
        note_outcome("discussion_reply", "rejected")
        flash(_TOPIC_LOCKED, "warning")
        return redirect(topic_url)
    if payload is None:
        note_outcome("discussion_reply", "rejected")
        return _render_topic(
            group, topic, actor_id, actor_public_id, draft=body_raw, form_error=_FORM_UNVERIFIED
        )
    if payload["topic_version"] != topic["version"]:
        note_outcome("discussion_reply", "rejected")
        return _render_topic(
            group, topic, actor_id, actor_public_id, draft=body_raw, form_error=_TOPIC_CHANGED
        )

    body, body_error = normalize_body(body_raw)
    if body_error:
        note_outcome("discussion_reply", "rejected")
        return _render_topic(
            group, topic, actor_id, actor_public_id, draft=body_raw, nonce=payload["nonce"],
            body_error=_REPLY_ERRORS[body_error],
        )

    outcome = tx.create_reply(
        actor_id, _STUDENT, group["public_id"], topic["id"], payload["topic_version"], body,
        payload["nonce"],
    )
    if outcome.status == tx.CREATED:
        note_outcome("discussion_reply", "posted")
        flash("Reply posted.", "success")
        return redirect(_reply_url(group["public_id"], topic, outcome.public_id))
    if outcome.status == tx.DUPLICATE:
        flash("This reply was already posted.", "info")
        return redirect(_reply_url(group["public_id"], topic, outcome.public_id))
    if outcome.status == tx.UNAVAILABLE:
        abort(404)
    if outcome.status == tx.TOPIC_LOCKED:
        note_outcome("discussion_reply", "rejected")
        flash(_TOPIC_LOCKED, "warning")
        return redirect(topic_url)
    if outcome.status == tx.STALE:
        note_outcome("discussion_reply", "rejected")
        current = _topic_or_404(group, topic["public_id"])
        return _render_topic(
            group, current, actor_id, actor_public_id, draft=body_raw, form_error=_TOPIC_CHANGED
        )
    note_outcome("discussion_reply", "rejected")
    flash(_CONFLICT, "danger")
    return redirect(topic_url)
