"""Read-only query layer for the notification inbox and the shared portal
header badge (M14).

Every function is scoped to one ``recipient_id`` in the SQL ``WHERE``
clause -- a notification is never loaded broadly and filtered afterwards,
and there is no query here that can return another user's row. All reads
are bounded: the inbox fetches at most ``PAGE_SIZE + 1`` rows to derive a
non-disclosing "there is a next page" flag without a COUNT, and the
header badge is a single indexed ``COUNT`` capped for display at
``99+``.

Templates receive plain presentation dicts (``build_inbox_view``), never
ORM rows, so rendering can never trigger a lazy load or an ORM-driven
authorization decision -- the same rule M11/M12/M13 already follow.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from flask import current_app, has_request_context, request

from app.extensions import db
from app.models import Notification, NotificationKind, UserRole
from app.services.schedule_occurrences import to_app_local

#: Fixed inbox page size. Not configurable and not client-supplied.
PAGE_SIZE = 20

#: Above this the header badge shows "99+" instead of a growing number.
BADGE_DISPLAY_CAP = 99

#: The two filters the inbox understands. Anything else normalises to
#: "all" rather than erroring or leaking that a value was rejected.
FILTERS = ("all", "unread")

#: Short, recipient-facing label per kind. Kept here (not in the
#: database) so wording can change without a migration and without
#: rewriting stored history.
KIND_LABELS = {
    NotificationKind.ENROLLMENT_ACTIVATED.value: "Enrollment",
    NotificationKind.ENROLLMENT_WITHDRAWN.value: "Enrollment",
    NotificationKind.TEACHER_ASSIGNMENT_ACTIVATED.value: "Assignment",
    NotificationKind.TEACHER_ASSIGNMENT_REMOVED.value: "Assignment",
    NotificationKind.SCHEDULE_CHANGED.value: "Schedule",
    NotificationKind.LESSON_PUBLISHED.value: "Lesson",
    NotificationKind.MATERIAL_AVAILABLE.value: "Material",
    NotificationKind.ANNOUNCEMENT_PUBLISHED.value: "Announcement",
    NotificationKind.MESSAGE_RECEIVED.value: "Message",
}

#: Attribute name used to memoise the badge on the current request
#: object. Stored on the request itself rather than on ``flask.g``:
#: ``g`` is scoped to the *app* context, which can outlive a single
#: request (a caller that already holds one -- notably the test suite
#: -- has every request reuse it), and an identity-based key would be
#: unreliable because CPython reuses object addresses.
_BADGE_CACHE_ATTR = "_m14_notification_badge"


def normalize_filter(value):
    """Normalise the ``filter`` query argument to one of :data:`FILTERS`."""
    return value if value in FILTERS else "all"


def normalize_page(value):
    """Normalise the ``page`` query argument to a positive integer.

    A missing, non-numeric, zero, negative, or absurdly large value all
    become page 1 rather than reaching the database as an offset.
    """
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > 10000:
        return 1
    return page


def inbox_page(recipient_id, filter_name, page):
    """One bounded page of `recipient_id`'s own notifications.

    Returns ``(rows, has_next)``. Ordering is newest-first and fully
    deterministic (``created_at DESC``, then ``id DESC``), so two
    notifications written in the same transaction -- e.g. the whole class
    being told a lesson was published -- still page stably. Fetches
    ``PAGE_SIZE + 1`` rows and drops the extra, so "is there a next page"
    costs no second query and reveals no total count.
    """
    query = Notification.query.filter(Notification.recipient_id == recipient_id)
    if filter_name == "unread":
        query = query.filter(Notification.read_at.is_(None))
    rows = (
        query.order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def own_notification(recipient_id, public_id):
    """One notification by ``public_id``, **scoped to its recipient**.

    Returns ``None`` for a missing public id and for one that belongs to
    somebody else -- the caller turns both into the same 404, so the
    response never distinguishes "does not exist" from "is not yours".
    """
    if not public_id:
        return None
    return Notification.query.filter(
        Notification.public_id == public_id,
        Notification.recipient_id == recipient_id,
    ).first()


def mark_read(notification):
    """Stamp `notification` read if it is not already. Idempotent: a
    second call is a no-op and never moves the original read time.
    Returns ``True`` when this call was the one that changed it.
    """
    if notification.read_at is not None:
        return False
    notification.read_at = datetime.now(timezone.utc)
    return True


def mark_all_read(recipient_id):
    """Mark every *unread* notification of `recipient_id` read in one
    bounded UPDATE. Returns the number of rows changed; running it twice
    changes nothing the second time.
    """
    return (
        db.session.query(Notification)
        .filter(
            Notification.recipient_id == recipient_id,
            Notification.read_at.is_(None),
        )
        .update(
            {Notification.read_at: datetime.now(timezone.utc)},
            synchronize_session=False,
        )
    )


def build_inbox_view(rows, tz_name):
    """Turn ORM rows into plain presentation dicts.

    ``created_at`` is stored naive-UTC and rendered here in
    ``APP_TIMEZONE`` through the M09 :func:`to_app_local` utility, so the
    template never does timezone arithmetic and never touches an ORM
    attribute. ``target_path`` is deliberately **not** included: a target
    is never rendered as a link, it is only ever resolved server-side by
    the open route.
    """
    return [
        {
            "public_id": row.public_id,
            "kind": row.kind,
            "kind_label": KIND_LABELS.get(row.kind, "Notice"),
            "title": row.title,
            "message": row.message,
            "created_local": to_app_local(tz_name, row.created_at),
            "is_unread": row.read_at is None,
        }
        for row in rows
    ]


# ----------------------------------------------------------------------
# Shared portal header badge -- bounded, role-gated, and fail-open
# ----------------------------------------------------------------------


def _unread_count_isolated(recipient_id):
    """A single indexed ``COUNT`` of unread notifications, run on its
    **own** connection checked out from the shared engine.

    Deliberately not issued through ``db.session``: this number decorates
    every Student/Teacher page in the portal, so a failure -- a missing
    table during a partial deploy, a broken query, a lock timeout -- must
    not be able to poison the request session and take the dashboard,
    lesson, material, or search page down with it. On its own connection
    the worst case is a caught exception and a badge of zero.

    Uses the Core table directly (no ORM identity map, no autoflush) so
    nothing about this read can interact with pending session state.

    Portability note: on the SQLite in-memory test backend the engine
    uses ``StaticPool``, so this "separate" connection is physically the
    same DBAPI connection as the session's. The failure-isolation
    property therefore genuinely holds on MySQL and is only structurally
    exercised by the tests -- the same honest limitation the project
    already records for ``SELECT ... FOR UPDATE``.
    """
    table = Notification.__table__
    statement = (
        sa.select(sa.func.count())
        .select_from(table)
        .where(table.c.recipient_id == recipient_id, table.c.read_at.is_(None))
    )
    with db.engine.connect() as connection:
        return int(connection.execute(statement).scalar() or 0)


def unread_count(recipient_id):
    """The unread count for `recipient_id`, or ``0`` if it cannot be
    read. Never raises; logs the failure server-side without surfacing
    any driver or SQL detail to the page.
    """
    try:
        return _unread_count_isolated(recipient_id)
    except Exception:
        current_app.logger.exception(
            "Unread notification count failed for recipient %s; the header badge falls "
            "back to zero and the page renders normally",
            recipient_id,
        )
        return 0


def badge_label(count):
    """``"7"``, or ``"99+"`` once the real number stops being useful."""
    return f"{BADGE_DISPLAY_CAP}+" if count > BADGE_DISPLAY_CAP else str(count)


def header_badge(user):
    """The shared portal header's notification context, or ``None``.

    ``None`` -- meaning "render no Notifications link and no badge at
    all" -- for anonymous users and for every role without an inbox
    (Administrator, Researcher, anything unrecognised), and those cases
    cost **no** query. For a Student or Teacher this issues exactly one
    bounded, indexed count per request, however many templates that
    request renders.

    The result is memoised on the current request object itself (see
    :data:`_BADGE_CACHE_ATTR`), so a request that renders several
    templates -- or renders one twice after a form rejection -- issues the
    count exactly once. Outside a request context there is nothing to
    memoise on, and the count is simply computed.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "role", None) not in (UserRole.STUDENT.value, UserRole.TEACHER.value):
        return None

    holder = request._get_current_object() if has_request_context() else None
    if holder is not None:
        cached = getattr(holder, _BADGE_CACHE_ATTR, None)
        if cached is not None and cached[0] == user.id:
            return cached[1]

    count = unread_count(user.id)
    badge = {"unread_count": count, "unread_label": badge_label(count)}
    if holder is not None:
        setattr(holder, _BADGE_CACHE_ATTR, (user.id, badge))
    return badge
