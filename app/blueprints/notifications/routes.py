"""The shared Student / Teacher notification inbox (M14).

One blueprint under ``/notifications`` serving both roles -- the rows are
already per-recipient, so there is nothing role-specific to separate.

**Authorization.** ``roles_required(STUDENT, TEACHER)`` gives the project's
standard behaviour: anonymous users follow the normal login redirect, and
Administrator / Researcher get **403** (they have no inbox in M14 and no
notification is ever addressed to them). On top of that, *every*
per-notification lookup is scoped with ``recipient_id == current_user.id``
in the ``WHERE`` clause, so a missing ``public_id`` and another
recipient's ``public_id`` produce the identical non-disclosing **404**.

**Reads never write.** The inbox is GET-only and never changes
``read_at``; the three state changes (open-and-read, read one, read all)
are POST-only, CSRF-protected, and idempotent -- replaying any of them
changes nothing further.

**Targets are never links.** A stored ``target_path`` is not rendered in
the page at all. Opening a notification POSTs here; this route marks the
row read, re-validates the stored path against the *current* recipient's
role namespace (``app/services/notification_targets.py``), and redirects.
A stored value that is unsafe -- corrupted, hand-edited, or written by a
future bug -- falls back to the inbox with a generic message rather than
being followed. Passing validation is not authorization: the destination
route enforces its own current rules, so an old notification can never
be used as proof of access to content the recipient has since lost.
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

from app.blueprints.notifications import notifications_bp
from app.extensions import db
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.notification_queries import (
    PAGE_SIZE,
    KIND_FILTERS,
    build_inbox_view,
    inbox_page,
    mark_all_read,
    mark_read,
    normalize_filter,
    normalize_kind,
    normalize_page,
    own_notification,
    preview_rows,
    unread_count,
)
from app.services.notification_targets import validate_notification_target

_RECIPIENT_ROLES = (UserRole.STUDENT.value, UserRole.TEACHER.value)


def _redirect_to_inbox():
    """Back to the inbox, preserving the filter and page the request came
    from. Both are re-normalised, so a tampered value cannot survive the
    round trip into the redirect URL.
    """
    return redirect(
        url_for(
            "notifications.inbox",
            filter=normalize_filter(request.form.get("filter")),
            page=normalize_page(request.form.get("page")),
            kind=normalize_kind(request.form.get("kind")),
        )
    )


def _own_notification_or_404(public_id):
    notification = own_notification(current_user.id, public_id)
    if notification is None:
        abort(404)
    return notification


@notifications_bp.get("")
@roles_required(*_RECIPIENT_ROLES)
def inbox():
    """Newest-first, bounded, read-only inbox for the current recipient.

    Reads a bounded filtered page and unread counts for the inbox/header.
    A stale empty page retries page 1 with the same filters. Nothing here writes.
    """
    filter_name = normalize_filter(request.args.get("filter"))
    kind = normalize_kind(request.args.get("kind"))
    page = normalize_page(request.args.get("page"))
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")

    rows, has_next = inbox_page(current_user.id, filter_name, page, kind)
    if not rows and page > 1:
        # A page past the end (a stale bookmark, or everything on the
        # last unread page was just read) shows page 1 rather than a
        # confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = inbox_page(current_user.id, filter_name, page, kind)

    response = make_response(
        render_template(
            "notifications/inbox.html",
            notifications=build_inbox_view(rows, tz_name),
            filter_name=filter_name,
            selected_kind=kind,
            kind_choices=KIND_FILTERS,
            page=page,
            has_next=has_next,
            has_prev=page > 1,
            page_size=PAGE_SIZE,
            unread_total=unread_count(current_user.id),
            tz_name=tz_name,
        )
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@notifications_bp.get("/preview")
@roles_required(*_RECIPIENT_ROLES)
def preview():
    """Read-only, escaped HTML for the desktop bell; no private message body."""
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    response = make_response(render_template("notifications/_preview.html",
        notifications=build_inbox_view(preview_rows(current_user.id), tz_name),
        unread_total=unread_count(current_user.id), tz_name=tz_name))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


@notifications_bp.post("/<public_id>/open")
@roles_required(*_RECIPIENT_ROLES)
def open_notification(public_id):
    """Mark one notification read and redirect to its stored target.

    Order matters: the row is marked read and committed **first**, so the
    read state is not lost if the redirect target later turns out to be
    unusable. Re-validating the stored path against the current
    recipient's role is what stops a corrupted or hostile stored value
    from becoming an open redirect; a rejected value logs server-side and
    lands the user safely back in the inbox.
    """
    notification = _own_notification_or_404(public_id)
    # Captured before the commit expires the row -- the redirect decision
    # must not depend on re-reading anything after the write.
    stored_target = notification.target_path
    stored_public_id = notification.public_id
    mark_read(notification)
    db.session.commit()

    target = validate_notification_target(current_user.role, stored_target)
    if target is None:
        current_app.logger.error(
            "Refusing to follow an invalid stored notification target for notification %s; "
            "falling back to the inbox",
            stored_public_id,
        )
        flash(
            "That notification could not be opened. It has been marked as read.",
            "warning",
        )
        return _redirect_to_inbox()
    return redirect(target)


@notifications_bp.post("/<public_id>/read")
@roles_required(*_RECIPIENT_ROLES)
def read_notification(public_id):
    """Mark one notification read without leaving the inbox. Idempotent."""
    notification = _own_notification_or_404(public_id)
    mark_read(notification)
    db.session.commit()
    return _redirect_to_inbox()


@notifications_bp.post("/read-all")
@roles_required(*_RECIPIENT_ROLES)
def read_all():
    """Mark every unread notification of the current recipient read.

    One bounded UPDATE scoped to ``recipient_id``; a second submission
    matches nothing and changes nothing.
    """
    changed = mark_all_read(current_user.id)
    db.session.commit()
    if changed:
        flash(f"{changed} notification(s) marked as read.", "success")
    else:
        flash("You have no unread notifications.", "warning")
    return _redirect_to_inbox()
