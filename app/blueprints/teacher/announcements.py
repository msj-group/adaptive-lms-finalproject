"""Teacher reading and authoring of Announcements (Phase 4 / M09).

Two flat **read** routes and six Group-nested routes, every object
addressed by ``public_id`` and no internal numeric id anywhere in a URL,
a form value, a signed token or the rendered HTML::

    GET       /teacher/announcements
    GET       /teacher/announcements/<ap>
    GET       /teacher/groups/<gp>/announcements
    GET|POST  /teacher/groups/<gp>/announcements/new
    GET       /teacher/groups/<gp>/announcements/<ap>
    GET|POST  /teacher/groups/<gp>/announcements/<ap>/edit
    POST      /teacher/groups/<gp>/announcements/<ap>/publish
    POST      /teacher/groups/<gp>/announcements/<ap>/withdraw

**Every mutation route is Group-nested and takes a Group public id.**
That is not cosmetic: a Teacher may only ever write a ``group``-scoped
announcement, so the Group is part of the object's identity, and putting
it in the path means the authorization check and the write target are the
same value rather than two things a route has to remember to compare.
There is deliberately **no** Teacher route that can create or alter a
Center or Course announcement -- not disabled in a template, not refused
by a permission check somebody could later relax: no such endpoint
exists, so a POST aimed at one returns 404 or 405.

The two flat routes are reads, and only reads. The feed is what a Teacher
sees *as a recipient* -- the Center, Course and Group announcements
currently addressed to them -- and the flat detail page is the authorized
destination a notification about one sends them to.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then server-side
and nested, reusing the exact helper every other Teacher surface uses:
``_teacher_group_or_404`` proves an **active** ``GroupTeacherAssignment``
to the Group in the URL, and the nested announcement lookup is
constrained by that Group in SQL. A missing Group, a missing
announcement, one belonging to another Group, a Center or Course
announcement's public id, an unassigned Teacher and a removed assignment
all return the same non-disclosing **404** -- never a 403, and never a
hint that the object exists. Every actively assigned Teacher is an equal
collaborator: a co-teacher's draft is editable, publishable and
withdrawable by any of them, exactly as the Group's Units, Assignments,
Quizzes, attendance and gradebook already are.

**Reading the board is current; reading your own group's notices is
management.** The flat feed and flat detail apply the M09 visibility
rule, which requires an **operational** chain -- a notice on an archived
term's board is not standing any more. The Group-nested management pages
deliberately stay reachable under an archived Group or ancestor, so a
Teacher can always read back what was posted and can always take down
something that should no longer be standing.

**What each mutation requires.** Creating and editing a draft, and
publishing, additionally require the Group *and* its AcademicTerm,
Course and Level to be active, re-checked against the **locked** rows.
**Withdrawal deliberately does not**: taking a notice down must never
become impossible because a term ended, and the Teacher's active
assignment is still required for it.

**Two different protections, both preserved.** The lock chain
(``app/services/announcement_transactions.py``) decides against the rows
as they are *now*; the signed exact-shape tokens
(``app/services/announcement_tokens.py``) decide against the state the
form was *opened* on. Neither replaces the other.

**Publication notifies once, after the commit.** The state transition is
one atomic commit; delivery then runs in its own transaction through the
project's established best-effort producer, so a notification failure can
never turn a successful publication into an error -- and can never
produce a second notification either, because no code path publishes an
announcement twice.
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.announcement_forms import AnnouncementTextForm
from app.blueprints.teacher.assignments import _private_no_store
from app.blueprints.teacher.units import (
    _archived_chain_labels,
    _group_is_operational,
    _join_labels,
    _teacher_group_or_404,
)
from app.extensions import db
from app.models import (
    ANNOUNCEMENT_BODY_MAX_LENGTH,
    ANNOUNCEMENT_TITLE_MAX_LENGTH,
    Announcement,
    AnnouncementScope,
    AnnouncementStatus,
    Group,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.announcement_queries import (
    PAGE_SIZE,
    build_management_view,
    build_reader_view,
    group_announcement,
    group_announcements_page,
    normalize_page,
    teacher_feed_page,
    teacher_group_cards,
    teacher_is_actively_assigned,
    teacher_visible_announcement,
)
from app.services.announcement_tokens import (
    make_token,
    target_lifecycle_state,
    token_is_stale,
)
from app.services.announcement_transactions import (
    archived_locked_labels,
    lock_announcement_chain,
    locked_group_matches,
    teacher_authz_broken,
)
from app.services.notification_delivery import notify_announcement_published
from app.services.schedule_occurrences import utc_reference_now
from app.services.search_terms import MIN_QUERY_LENGTH, normalize_query

_GROUP_SCOPE = AnnouncementScope.GROUP.value
_DRAFT = AnnouncementStatus.DRAFT.value
_PUBLISHED = AnnouncementStatus.PUBLISHED.value
_WITHDRAWN = AnnouncementStatus.WITHDRAWN.value
_TEACHER = UserRole.TEACHER.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: This surface's token namespace. A token minted here can never be
#: replayed on the Administrator surface, or the reverse.
_SURFACE = "teacher"


def _write_moment():
    """The **authoritative** naive-UTC moment for one announcement write,
    truncated to whole seconds.

    ``DATETIME`` on MySQL carries fractional precision 0 and *rounds* an
    excess fraction rather than truncating it, so a value carrying
    microseconds would be stored as a different instant from the one the
    request used. Read only **after** every lock that could have blocked,
    so a request that waited behind a competing co-teacher records the
    moment it actually wrote.

    Declared here rather than imported so this surface's clock can be
    injected on its own in tests, exactly as each blueprint already does.
    """
    return utc_reference_now().replace(microsecond=0)


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


# ======================================================================
# Teacher-facing sentences, declared once each
# ======================================================================

_NOT_OPERATIONAL_MESSAGE = (
    "Announcements can only be written or published while the group and its academic "
    "term, course, and level are all active. Existing announcements stay readable, and "
    "a published one can still be withdrawn."
)
_OPERATIONAL_WORDING = (
    "Announcements can only be written or published",
    "Existing announcements stay readable, and a published one can still be withdrawn.",
)
_GROUP_CHANGED_MESSAGE = (
    "This group changed while you were working. Reload the page and try again."
)
_STALE_MESSAGE = (
    "This announcement was changed by someone else since this page was opened. Your "
    "changes were not saved. Please reload, read the current text, and make your change "
    "against it."
)
_INTEGRITY_MESSAGE = (
    "That change could not be saved because the data changed at the same moment. Nothing "
    "was written. Please reload and try again."
)
_NOT_DRAFT_MESSAGE = (
    "This announcement has already been published, so its title, text and group can no "
    "longer be changed. People have already read it. Withdraw it and write a new one if "
    "it needs to be corrected."
)
_WITHDRAWN_MESSAGE = (
    "This announcement was withdrawn. A withdrawn announcement is permanent — it can "
    "never be edited, published again, or restored. Write a new announcement instead."
)
_ALREADY_PUBLISHED_MESSAGE = "This announcement was already published. Nothing was changed."
_NOT_PUBLISHED_MESSAGE = (
    "Only a published announcement can be withdrawn. Nothing was changed."
)
_WITHDRAW_CONFIRM_MESSAGE = (
    "Please tick the confirmation box before withdrawing. Withdrawing cannot be undone."
)
_NO_CHANGES_MESSAGE = "Nothing was changed, so nothing was saved."
_DRAFT_SAVED_MESSAGE = "Announcement draft saved."
_PUBLISHED_OK_MESSAGE = (
    "Announcement published. Everyone currently in this group can read it now, and has "
    "been notified."
)
_WITHDRAWN_OK_MESSAGE = (
    "Announcement withdrawn. Nobody can read it any more. It is kept as a permanent "
    "record of what was posted and when it was taken down."
)


# ======================================================================
# URLs
# ======================================================================


def _group_list_url(group_public_id):
    return url_for("teacher.group_announcements", group_public_id=group_public_id)


def _group_detail_url(group_public_id, announcement_public_id):
    return url_for(
        "teacher.group_announcement_detail",
        group_public_id=group_public_id,
        announcement_public_id=announcement_public_id,
    )


def _new_url(group_public_id):
    return url_for("teacher.announcement_create", group_public_id=group_public_id)


def _edit_url(group_public_id, announcement_public_id):
    return url_for(
        "teacher.announcement_edit",
        group_public_id=group_public_id,
        announcement_public_id=announcement_public_id,
    )


# ======================================================================
# Shared lookups and post-lock helpers
# ======================================================================


def _announcement_or_404(group, announcement_public_id):
    """One announcement of this Group, or the established non-disclosing
    404.

    Another Group's announcement, a Center or Course announcement, and a
    nonexistent public id all fail identically here, because the Group
    constraint is part of the SQL rather than a check applied to a row
    that was already found.
    """
    row = group_announcement(group.id, announcement_public_id)
    if row is None:
        abort(404)
    return row


def _hierarchy_context(group):
    return group.academic_term_id, group.course.level_id, group.course_id


def _group_target_state(term_status, level_status, course_status, group_status):
    return target_lifecycle_state(
        _GROUP_SCOPE, (term_status, level_status, course_status, group_status)
    )


def _preview_target_state(group):
    """The target's lifecycle as a **plain** read, for minting a token
    onto a freshly rendered page. The authoritative comparison always
    rebuilds this from the locked rows instead."""
    return _group_target_state(
        group.academic_term.status,
        group.course.level.status,
        group.course.status,
        group.status,
    )


def _locked_target_state(locks, term_id, level_id, course_id):
    """The target's lifecycle as the **locked** rows describe it right
    now. A missing row is reported as ``missing`` rather than skipped, so
    a vanished ancestor can never coincidentally match a stale token."""

    def status(row):
        return "missing" if row is None else row.status

    return _group_target_state(
        status(locks.hierarchy.term(term_id)),
        status(locks.hierarchy.level(level_id)),
        status(locks.hierarchy.course(course_id)),
        status(locks.group),
    )


def _operational_block(locks, group_id, term_id, level_id, course_id):
    """``None`` if the **locked** Group and its locked AcademicTerm /
    Level / Course all exist, are active, and still form the same chain
    this request validated against -- else a Teacher-facing message.

    Same shape and same reasoning as M01's, M05's, M06's, M07's and M08's
    equivalents; the wording differs because what is blocked differs, and
    it comes from a constant declared once.
    """
    if not locked_group_matches(locks, group_id, term_id, level_id, course_id):
        return _GROUP_CHANGED_MESSAGE
    labels = archived_locked_labels(
        locks,
        term_id=term_id,
        level_id=level_id,
        course_id=course_id,
        include_group=True,
    )
    if labels:
        verb = "is" if len(labels) == 1 else "are"
        lead, tail = _OPERATIONAL_WORDING
        return (
            f"{lead} while the group and its academic term, course, and level are all "
            f"active. The {_join_labels(labels)} {verb} archived. {tail}"
        )
    return None


def _locked_announcement_broken(locks, group_id, announcement_public_id):
    """``True`` when the **locked** announcement is no longer the row this
    request is about -- it vanished, or it belongs to another Group, or
    its public id does not match. The caller rolls back and 404s."""
    row = locks.announcement
    return (
        row is None
        or row.group_id != group_id
        or row.public_id != announcement_public_id
    )


def _fresh_teacher_authorization(actor_id, group_public_id):
    """Prove from **current database state** that `actor_id` may reach
    this Group's announcements *right now*, and return the Group.

    ``roles_required`` runs once, before the view, so a path that has
    rolled back and released its locks no longer holds current evidence,
    and the same concurrent change that forced the rollback may have ended
    this Teacher's access. ``_teacher_group_or_404`` alone is not enough
    either -- it proves an active ``GroupTeacherAssignment`` but never
    re-reads the actor's own ``role`` and ``status``. The actor is
    identified by a **scalar id captured before the reset**, never by
    ``current_user``.
    """
    if actor_id is None:  # pragma: no cover -- an authenticated view always has one
        abort(404)
    actor = db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    if actor is None or actor.role != _TEACHER or actor.status != _USER_ACTIVE:
        abort(404)
    group = Group.query.filter_by(public_id=group_public_id).first()
    if group is None:
        abort(404)
    if not teacher_is_actively_assigned(actor_id, group.id):
        abort(404)
    return group


def _reject(group_public_id, message, url, level="danger"):
    """Release any lock, re-prove authorization from current state, then
    flash and redirect to a plain GET (PRG).

    The message is flashed **only after** authorization has passed, so a
    request whose access ended in the same window that caused the failure
    404s silently instead of leaving a message behind for whatever page
    the actor reaches next.
    """
    actor_id = current_user.id
    db.session.rollback()
    _fresh_teacher_authorization(actor_id, group_public_id)
    flash(message, level)
    return redirect(url)


def _lifecycle_block(status):
    """The Teacher-facing sentence for a lifecycle state that forbids the
    attempted change, or ``None`` when the state is ``draft``."""
    if status == _PUBLISHED:
        return _NOT_DRAFT_MESSAGE
    if status == _WITHDRAWN:
        return _WITHDRAWN_MESSAGE
    return None


# ======================================================================
# The Teacher's own feed -- what is currently addressed to them
# ======================================================================


@teacher_bp.get("/announcements")
@roles_required(UserRole.TEACHER.value)
def announcements_feed():
    """Every published announcement this Teacher may currently read --
    Center, Course and their assigned Groups -- newest publication first,
    plus the way in to each assigned Group's management page.

    One bounded page, deterministic ordering, ``LIMIT PAGE_SIZE + 1`` for
    the has-next flag and **no** ``COUNT``. The optional ``q`` filter
    narrows the same authorized rows through exactly the normalisation and
    escaping M13's search already uses; it can never widen them, because
    it is applied on top of the visibility clause rather than instead of
    it.

    Drafts and withdrawn announcements never appear here, including this
    Teacher's own: this page is the board, not the workshop. Their own
    group's drafts are on the group management page.
    """
    page = normalize_page(request.args.get("page"))
    raw_q = request.args.get("q", "")
    query_norm = normalize_query(raw_q)
    tokens = query_norm.tokens if query_norm.is_searchable else ()

    rows, has_next = teacher_feed_page(current_user.id, page, tokens)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than a
        # confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = teacher_feed_page(current_user.id, page, tokens)

    return _private_no_store(
        "teacher/announcements/feed.html",
        announcements=build_reader_view(rows, _tz_name()),
        cards=teacher_group_cards(current_user.id),
        q=query_norm.text,
        raw_q=raw_q,
        searching=bool(tokens),
        too_short=bool(raw_q.strip()) and query_norm.too_short,
        min_query_length=MIN_QUERY_LENGTH,
        tz_name=_tz_name(),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@teacher_bp.get("/announcements/<announcement_public_id>")
@roles_required(UserRole.TEACHER.value)
def announcement_detail(announcement_public_id):
    """One published announcement this Teacher may currently read.

    This is the authorized destination a notification about an
    announcement sends a Teacher to, and it **re-authorizes from
    scratch**: the visibility rule is re-applied on every request, so a
    Teacher whose assignment was removed, or whose Group's term was
    archived, lands on the ordinary non-disclosing 404 even though the
    notification row about it still sits in their inbox. A draft, a
    withdrawn announcement, another Course's announcement and an invented
    public id all fail identically here.
    """
    row = teacher_visible_announcement(current_user.id, announcement_public_id)
    if row is None:
        abort(404)
    return _private_no_store(
        "teacher/announcements/detail.html",
        announcement=build_reader_view([row], _tz_name())[0],
        tz_name=_tz_name(),
    )


# ======================================================================
# Group management -- list and detail
# ======================================================================


@teacher_bp.get("/groups/<group_public_id>/announcements")
@roles_required(UserRole.TEACHER.value)
def group_announcements(group_public_id):
    """One bounded page of **every** announcement this Group owns --
    drafts, published and withdrawn alike -- newest first.

    Stays available under an archived Group or ancestor: an actively
    assigned Teacher can always read back what was posted, and can always
    withdraw something that should no longer be standing.
    """
    group = _teacher_group_or_404(group_public_id)
    page = normalize_page(request.args.get("page"))
    rows, has_next = group_announcements_page(group.id, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = group_announcements_page(group.id, page)

    operational = _group_is_operational(group)
    return _private_no_store(
        "teacher/announcements/group_list.html",
        group=group,
        announcements=build_management_view(rows, _tz_name()),
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=_tz_name(),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@teacher_bp.get("/groups/<group_public_id>/announcements/<announcement_public_id>")
@roles_required(UserRole.TEACHER.value)
def group_announcement_detail(group_public_id, announcement_public_id):
    """One of this Group's announcements in full, with whichever of the
    two lifecycle controls its current state allows.

    Each control is a POST form carrying CSRF and its own freshly minted,
    purpose-specific signed token; a state that allows neither (a
    withdrawn announcement) renders no form at all, because there is no
    endpoint left for one to aim at.
    """
    group = _teacher_group_or_404(group_public_id)
    row = _announcement_or_404(group, announcement_public_id)
    operational = _group_is_operational(group)

    publish_token = None
    withdraw_token = None
    if row.status == _DRAFT and operational:
        publish_token = make_token(
            _SURFACE,
            "announcement-publish",
            actor_public_id=current_user.public_id,
            announcement_public_id=row.public_id,
            announcement_version=row.version,
            scope=_GROUP_SCOPE,
            target_public_id=group.public_id,
            target_state=_preview_target_state(group),
        )
    if row.status == _PUBLISHED:
        withdraw_token = make_token(
            _SURFACE,
            "announcement-withdraw",
            actor_public_id=current_user.public_id,
            announcement_public_id=row.public_id,
            announcement_version=row.version,
        )

    return _private_no_store(
        "teacher/announcements/group_detail.html",
        group=group,
        announcement=build_management_view([row], _tz_name())[0],
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        publish_token=publish_token,
        withdraw_token=withdraw_token,
        edit_url=_edit_url(group.public_id, row.public_id),
        list_url=_group_list_url(group.public_id),
        tz_name=_tz_name(),
    )


# ======================================================================
# Draft creation and editing
# ======================================================================


def _render_form(group, form, announcement=None, message=None, level="danger"):
    """Render the create / edit page against **current persisted state**,
    with a freshly minted token."""
    if message is not None:
        flash(message, level)
    payload = {
        "actor_public_id": current_user.public_id,
        "announcement_public_id": "" if announcement is None else announcement.public_id,
        "announcement_version": 0 if announcement is None else announcement.version,
        "scope": _GROUP_SCOPE,
        "target_public_id": group.public_id,
    }
    return _private_no_store(
        "teacher/announcements/form.html",
        group=group,
        form=form,
        announcement=announcement,
        title_max=ANNOUNCEMENT_TITLE_MAX_LENGTH,
        body_max=ANNOUNCEMENT_BODY_MAX_LENGTH,
        cancel_url=(
            _group_list_url(group.public_id)
            if announcement is None
            else _group_detail_url(group.public_id, announcement.public_id)
        ),
        announcement_state=make_token(_SURFACE, "announcement-draft", **payload),
    )


@teacher_bp.route(
    "/groups/<group_public_id>/announcements/new", methods=["GET", "POST"]
)
@roles_required(UserRole.TEACHER.value)
def announcement_create(group_public_id):
    """Write one new **draft** announcement for this Group.

    A new announcement is always a draft. There is no "create and publish"
    path and no ``status`` input: publication is a separate, deliberate
    action with its own confirmation, its own token and its own
    notification, and collapsing the two would mean a slip of the mouse
    could tell a whole group something.

    The scope is ``group`` and the target is the Group in the URL, both
    server-decided. Nothing in the request body can change either.
    """
    group = _teacher_group_or_404(group_public_id)
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_group_list_url(group_public_id))

    form = AnnouncementTextForm()
    if request.method == "GET" or not form.validate_on_submit():
        return _render_form(group, form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("announcement_state")
    title, body = form.normalized_title, form.normalized_body
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id = group.id

    locks = lock_announcement_chain(
        actor_id,
        term_id=term_id,
        level_id=level_id,
        course_id=course_id,
        group_public_id=group_public_id,
        lock_assignment=True,
    )
    if teacher_authz_broken(locks):
        db.session.rollback()
        abort(404)

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject(group_public_id, block, _group_list_url(group_public_id))

    if token_is_stale(
        _SURFACE,
        token,
        "announcement-draft",
        actor_public_id=actor_public_id,
        announcement_public_id="",
        announcement_version=0,
        scope=_GROUP_SCOPE,
        target_public_id=group_public_id,
    ):
        return _reject(group_public_id, _STALE_MESSAGE, _new_url(group_public_id))

    moment = _write_moment()
    announcement = Announcement(
        author_id=actor_id,
        scope=_GROUP_SCOPE,
        course_id=None,
        group_id=group_id,
        title=title,
        body=body,
        status=_DRAFT,
        published_at=None,
        withdrawn_at=None,
        version=1,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(announcement)
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(group_public_id, _INTEGRITY_MESSAGE, _new_url(group_public_id))

    created_public_id = announcement.public_id
    flash(_DRAFT_SAVED_MESSAGE, "success")
    return redirect(_group_detail_url(group_public_id, created_public_id))


@teacher_bp.route(
    "/groups/<group_public_id>/announcements/<announcement_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def announcement_edit(group_public_id, announcement_public_id):
    """Change one **draft** announcement's title or text.

    Only a draft can be edited. A published announcement's title, text,
    scope, target and author are frozen permanently -- people have already
    read them -- and a withdrawn one is frozen for ever. Both cases say so
    in a clear sentence rather than silently succeeding or silently doing
    nothing, and both are re-proved against the **locked** row: a
    co-teacher may have published the draft between the GET and the POST.

    A save whose normalized title **and** body equal the stored ones is a
    no-op: no version moves, no timestamp moves, and the transaction is
    rolled back.
    """
    group = _teacher_group_or_404(group_public_id)
    row = _announcement_or_404(group, announcement_public_id)
    detail_url = _group_detail_url(group_public_id, announcement_public_id)

    block = _lifecycle_block(row.status)
    if block is not None:
        flash(block, "warning")
        return redirect(detail_url)
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(detail_url)

    form = AnnouncementTextForm(
        formdata=request.form if request.method == "POST" else None,
        data={"title": row.title, "body": row.body},
    )
    if request.method == "GET" or not form.validate_on_submit():
        return _render_form(group, form, announcement=row)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("announcement_state")
    title, body = form.normalized_title, form.normalized_body
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, announcement_id, version = group.id, row.id, row.version
    edit_url = _edit_url(group_public_id, announcement_public_id)

    locks = lock_announcement_chain(
        actor_id,
        term_id=term_id,
        level_id=level_id,
        course_id=course_id,
        group_public_id=group_public_id,
        lock_assignment=True,
        announcement_id=announcement_id,
    )
    if teacher_authz_broken(locks):
        db.session.rollback()
        abort(404)
    if _locked_announcement_broken(locks, group_id, announcement_public_id):
        db.session.rollback()
        abort(404)
    locked = locks.announcement

    operational_block = _operational_block(
        locks, group_id, term_id, level_id, course_id
    )
    if operational_block is not None:
        return _reject(group_public_id, operational_block, detail_url)

    if token_is_stale(
        _SURFACE,
        token,
        "announcement-draft",
        actor_public_id=actor_public_id,
        announcement_public_id=announcement_public_id,
        announcement_version=locked.version,
        scope=_GROUP_SCOPE,
        target_public_id=group_public_id,
    ):
        return _reject(group_public_id, _STALE_MESSAGE, edit_url)

    # Re-proved against the locked row: a co-teacher may have published or
    # withdrawn this draft between the GET and now.
    lifecycle = _lifecycle_block(locked.status)
    if lifecycle is not None:
        return _reject(group_public_id, lifecycle, detail_url, "warning")
    if locked.version != version:  # pragma: no cover -- the token already caught it
        return _reject(group_public_id, _STALE_MESSAGE, edit_url)

    if locked.title == title and locked.body == body:
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(detail_url)

    moment = _write_moment()
    locked.title = title
    locked.body = body
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(group_public_id, _INTEGRITY_MESSAGE, edit_url)

    flash(_DRAFT_SAVED_MESSAGE, "success")
    return redirect(detail_url)


# ======================================================================
# Publication and withdrawal
# ======================================================================


@teacher_bp.post(
    "/groups/<group_public_id>/announcements/<announcement_public_id>/publish"
)
@roles_required(UserRole.TEACHER.value)
def announcement_publish(group_public_id, announcement_public_id):
    """Publish one draft announcement to this Group.

    POST only, CSRF-protected, and behind a signed publish token that
    binds the announcement's version **and** the target's current
    lifecycle -- so a form opened while the Group was operational cannot
    publish into a Group that has since been archived.

    The state transition is one atomic commit: ``status``,
    ``published_at``, ``version`` and ``updated_at`` move together or not
    at all. Only **after** that commit, and only on the request that
    actually performed it, does the notification producer run -- in its
    own transaction, fault-isolated, so a delivery failure cannot undo the
    publication. There is no second delivery to guard against, because
    there is no second publication: ``published`` is a one-way door.
    """
    group = _teacher_group_or_404(group_public_id)
    row = _announcement_or_404(group, announcement_public_id)
    detail_url = _group_detail_url(group_public_id, announcement_public_id)

    if row.status == _PUBLISHED:
        flash(_ALREADY_PUBLISHED_MESSAGE, "warning")
        return redirect(detail_url)
    if row.status == _WITHDRAWN:
        flash(_WITHDRAWN_MESSAGE, "warning")
        return redirect(detail_url)
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(detail_url)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("announcement_state")
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, announcement_id = group.id, row.id

    locks = lock_announcement_chain(
        actor_id,
        term_id=term_id,
        level_id=level_id,
        course_id=course_id,
        group_public_id=group_public_id,
        lock_assignment=True,
        announcement_id=announcement_id,
    )
    if teacher_authz_broken(locks):
        db.session.rollback()
        abort(404)
    if _locked_announcement_broken(locks, group_id, announcement_public_id):
        db.session.rollback()
        abort(404)
    locked = locks.announcement

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject(group_public_id, block, detail_url)

    if token_is_stale(
        _SURFACE,
        token,
        "announcement-publish",
        actor_public_id=actor_public_id,
        announcement_public_id=announcement_public_id,
        announcement_version=locked.version,
        scope=_GROUP_SCOPE,
        target_public_id=group_public_id,
        target_state=_locked_target_state(locks, term_id, level_id, course_id),
    ):
        return _reject(group_public_id, _STALE_MESSAGE, detail_url)

    if locked.status == _PUBLISHED:
        return _reject(group_public_id, _ALREADY_PUBLISHED_MESSAGE, detail_url, "warning")
    if locked.status != _DRAFT:
        return _reject(group_public_id, _WITHDRAWN_MESSAGE, detail_url, "warning")

    moment = _write_moment()
    locked.status = _PUBLISHED
    locked.published_at = moment
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(group_public_id, _INTEGRITY_MESSAGE, detail_url)

    # The publication is committed and the response is already decided.
    # Capture plain scalars, build the response, and only then attempt
    # best-effort delivery in its own transaction -- a notification
    # failure never turns this successful publication into an error.
    flash(_PUBLISHED_OK_MESSAGE, "success")
    response = redirect(detail_url)
    notify_announcement_published(announcement_id, _GROUP_SCOPE, None, group_id)
    return response


@teacher_bp.post(
    "/groups/<group_public_id>/announcements/<announcement_public_id>/withdraw"
)
@roles_required(UserRole.TEACHER.value)
def announcement_withdraw(group_public_id, announcement_public_id):
    """Withdraw one published announcement, permanently.

    The announcement stops being readable the instant this commits: every
    reader surface filters on ``status == 'published'``, so the feed, the
    detail page, the dashboard preview and the search all drop it at once.
    Notifications already delivered about it remain as personal historical
    rows -- they are a record that somebody was told something, which
    stays true -- but following one now lands on the ordinary
    non-disclosing 404.

    This is terminal. There is no unwithdraw, no restore and no republish,
    and no endpoint exists for one.

    Deliberately **not** gated on an operational chain: a notice that
    should no longer be standing must always be removable, including after
    the term it belonged to has been archived. The Teacher's active
    assignment is still required, and is re-proved against the locked
    rows.
    """
    group = _teacher_group_or_404(group_public_id)
    row = _announcement_or_404(group, announcement_public_id)
    detail_url = _group_detail_url(group_public_id, announcement_public_id)

    if row.status != _PUBLISHED:
        flash(
            _WITHDRAWN_MESSAGE if row.status == _WITHDRAWN else _NOT_PUBLISHED_MESSAGE,
            "warning",
        )
        return redirect(detail_url)
    if request.form.get("confirm") != "yes":
        flash(_WITHDRAW_CONFIRM_MESSAGE, "warning")
        return redirect(detail_url)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("announcement_state")
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, announcement_id = group.id, row.id

    locks = lock_announcement_chain(
        actor_id,
        term_id=term_id,
        level_id=level_id,
        course_id=course_id,
        group_public_id=group_public_id,
        lock_assignment=True,
        announcement_id=announcement_id,
    )
    if teacher_authz_broken(locks):
        db.session.rollback()
        abort(404)
    if _locked_announcement_broken(locks, group_id, announcement_public_id):
        db.session.rollback()
        abort(404)
    locked = locks.announcement

    if not locked_group_matches(locks, group_id, term_id, level_id, course_id):
        return _reject(group_public_id, _GROUP_CHANGED_MESSAGE, detail_url)

    if token_is_stale(
        _SURFACE,
        token,
        "announcement-withdraw",
        actor_public_id=actor_public_id,
        announcement_public_id=announcement_public_id,
        announcement_version=locked.version,
    ):
        return _reject(group_public_id, _STALE_MESSAGE, detail_url)

    if locked.status != _PUBLISHED:
        return _reject(
            group_public_id,
            _WITHDRAWN_MESSAGE if locked.status == _WITHDRAWN else _NOT_PUBLISHED_MESSAGE,
            detail_url,
            "warning",
        )

    moment = _write_moment()
    # ``withdrawn_at >= published_at`` is a CHECK, and a clock injected by
    # a test (or a machine whose clock moved backwards) could violate it.
    # Taking the later of the two keeps the record honest -- a withdrawal
    # never claims to predate the publication it ends -- without refusing
    # to take a notice down.
    if locked.published_at is not None and moment < locked.published_at:
        moment = locked.published_at
    locked.status = _WITHDRAWN
    locked.withdrawn_at = moment
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(group_public_id, _INTEGRITY_MESSAGE, detail_url)

    flash(_WITHDRAWN_OK_MESSAGE, "success")
    return redirect(detail_url)
