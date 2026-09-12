"""A Student's announcement feed (Phase 4 / M09).

Two routes, and both of them are ``GET``::

    GET  /student/announcements
    GET  /student/announcements/<announcement_public_id>

There is deliberately **no** POST, PUT, PATCH or DELETE anywhere on the
Student announcement surface -- no acknowledgement, no "mark as read", no
dismiss, no comment, no reaction and no reply. A Student never writes an
announcement or anything attached to one, so no write path exists for one
to reach: a POST to either URL returns 405, because no such endpoint was
ever registered.

**What a Student sees is what is currently addressed to them.** The
visibility formula lives in the SQL ``WHERE`` clause of
``app/services/announcement_queries.py`` and is applied once, keyed off
``current_user.id``:

- the announcement is ``published`` -- never a draft, never a withdrawn
  one;
- and its scope currently reaches this Student: ``center`` always,
  ``course`` when they hold an ``active`` Enrollment in an **operational**
  Group of that Course, ``group`` when they hold one in that exact Group.

**What is deliberately excluded**, and excluded by never being fetched
rather than by being hidden in a template: every draft, every withdrawn
announcement, every announcement of a Course or Group they do not
currently belong to, the author's identity, the lifecycle timestamps
other than the publication moment, the version, and every internal
numeric id.

**One Course announcement is one row.** A Student enrolled in three
Groups of the same Course sees that Course's announcement once, because
the authorization is a correlated ``EXISTS`` rather than a join -- see
the query module's docstring.

**Visibility is current, not historical.** Unlike a released grade or a
finalized attendance record, an announcement is a notice on a board: a
Student whose Enrollment is withdrawn, or whose Group's term has been
archived, stops being able to open it. A Notification they received about
it stays in their inbox as a record that they were told something, but
following it re-authorizes here from scratch and lands on the ordinary
non-disclosing 404.
"""

from flask import abort, current_app, request
from flask_login import current_user

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.models import UserRole
from app.security.decorators import roles_required
from app.services.announcement_queries import (
    PAGE_SIZE,
    build_reader_view,
    normalize_page,
    student_feed_page,
    student_visible_announcement,
)


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


@student_bp.get("/announcements")
@roles_required(UserRole.STUDENT.value)
def announcements_list():
    """One bounded page of the announcements this Student may read right
    now, newest publication first.

    Center, course and group announcements arrive in **one** query and
    therefore in one correctly interleaved chronological order -- not
    three lists stitched together, which could not be paged coherently.

    Fixed page size, deterministic SQL ordering
    (``published_at DESC, id DESC``) and ``LIMIT PAGE_SIZE + 1`` for the
    has-next flag with **no** ``COUNT``: an exact total would be both an
    unbounded scan and a disclosure of how much exists beyond the page.

    The response carries ``Cache-Control: private, no-store`` and
    ``Vary: Cookie``: which announcements a person can see is itself
    information about where they belong, and a shared or reused cache
    entry must never be able to hand it to somebody else.
    """
    page = normalize_page(request.args.get("page"))
    rows, has_next = student_feed_page(current_user.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark, or an announcement
        # withdrawn since the link was made) shows page 1 rather than a
        # confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = student_feed_page(current_user.id, page)

    return private_no_store(
        "student/announcements/list.html",
        announcements=build_reader_view(rows, _tz_name()),
        active_nav="announcements",
        tz_name=_tz_name(),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@student_bp.get("/announcements/<announcement_public_id>")
@roles_required(UserRole.STUDENT.value)
def announcement_detail(announcement_public_id):
    """One published announcement this Student may currently read.

    This is the authorized destination a notification about an
    announcement sends a Student to, and it **re-authorizes from
    scratch** on every request. A draft, a withdrawn announcement,
    another Group's announcement, another Course's announcement, an
    announcement whose Group's term has since been archived, one whose
    Enrollment has since been withdrawn, and an invented public id all
    fail identically with the ordinary non-disclosing **404** -- the page
    never distinguishes "does not exist" from "is not yours" from "is not
    yours any more".
    """
    row = student_visible_announcement(current_user.id, announcement_public_id)
    if row is None:
        abort(404)
    return private_no_store(
        "student/announcements/detail.html",
        announcement=build_reader_view([row], _tz_name())[0],
        active_nav="announcements",
        tz_name=_tz_name(),
    )
