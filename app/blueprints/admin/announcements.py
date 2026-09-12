"""Administrator management of Announcements in all three scopes
(Phase 4 / M09).

Six routes, every object addressed by ``public_id``::

    GET       /admin/announcements
    GET|POST  /admin/announcements/new
    GET       /admin/announcements/<ap>
    GET|POST  /admin/announcements/<ap>/edit
    POST      /admin/announcements/<ap>/publish
    POST      /admin/announcements/<ap>/withdraw

**This is the only surface that can write a Center or Course
announcement.** A Teacher's routes are Group-nested and can only ever
produce a ``group``-scoped row; there is no Teacher endpoint for the
other two scopes to be aimed at. Conversely, an Administrator has **no
announcement mutation path outside this module**: nothing under
``/admin/groups/...`` or anywhere else writes an announcement, so the
whole of Administrator announcement authorship is the six routes above.

**What an Administrator may do, and what nobody may do.** They create,
edit drafts, publish, withdraw, inspect and filter announcements of all
three scopes. There is deliberately **no** delete, archive, restore,
unwithdraw, republish, duplicate, schedule, import, export or bulk
action -- none exists server-side either, and no placeholder is left for
one. A correction to something already published is a new announcement,
which is what keeps the record of what was actually said, and for how
long, intact.

**An Administrator is never a recipient.** Publishing here notifies the
Students and Teachers the scope reaches; the acting Administrator gets
nothing, because the notification inbox in this project is restricted to
Students and Teachers and the producer's recipient query selects only
those two roles. This page equally never exposes *who* was notified: the
recipient list and the individual notification rows are not announcement
administration, and no query behind this module fetches one.

**Filters are validated into known shapes before they reach SQL.** The
scope filter must be exactly one of the three approved scopes and the
status filter exactly one of the three approved statuses; anything else
is dropped rather than guessed at, and no raw query-string value is ever
interpolated into a query.

Every response carries ``Cache-Control: private, no-store`` and
``Vary: Cookie``: a management page names the center's Courses, Groups
and unpublished drafts, and a shared or reused cache entry must never be
able to hand it to somebody else.
"""

from flask import abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.admin import admin_bp
from app.blueprints.admin.announcement_forms import SCOPE_CHOICES, AdminAnnouncementForm
from app.extensions import db
from app.models import (
    ANNOUNCEMENT_BODY_MAX_LENGTH,
    ANNOUNCEMENT_TITLE_MAX_LENGTH,
    Announcement,
    AnnouncementScope,
    AnnouncementStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.announcement_queries import (
    PAGE_SIZE,
    SCOPE_LABELS,
    STATUS_LABELS,
    admin_announcement,
    admin_announcements_page,
    build_management_view,
    course_by_public_id,
    course_choice_rows,
    group_by_public_id,
    group_choice_rows,
    normalize_page,
    normalize_scope_filter,
    normalize_status_filter,
)
from app.services.announcement_tokens import (
    CENTER_TARGET_STATE,
    make_token,
    target_lifecycle_state,
    token_is_stale,
)
from app.services.announcement_transactions import (
    administrator_authz_broken,
    archived_locked_labels,
    lock_announcement_chain,
    locked_group_matches,
)
from app.services.notification_delivery import notify_announcement_published
from app.services.schedule_occurrences import utc_reference_now

_CENTER = AnnouncementScope.CENTER.value
_COURSE = AnnouncementScope.COURSE.value
_GROUP = AnnouncementScope.GROUP.value
_DRAFT = AnnouncementStatus.DRAFT.value
_PUBLISHED = AnnouncementStatus.PUBLISHED.value
_WITHDRAWN = AnnouncementStatus.WITHDRAWN.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: This surface's token namespace. A token minted here can never be
#: replayed on the Teacher surface, or the reverse.
_SURFACE = "admin"


def _private_no_store(template, **context):
    """Render a **personalized** Administrator page with the two headers
    every announcement-management response must carry.

    Same contract as the Teacher and Student surfaces, applied here rather
    than imported across blueprints, matching how every other module in
    this project already sets it.
    """
    response = make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _write_moment():
    """The **authoritative** naive-UTC moment for one announcement write,
    truncated to whole seconds. See the Teacher surface's identical
    helper for why the truncation matters on MySQL; declared here so this
    surface's clock can be injected on its own in tests."""
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# Administrator-facing sentences, declared once each
# ======================================================================

_TARGET_GONE_MESSAGE = (
    "The course or group this announcement is for could not be read, or is no longer "
    "active. Nothing was saved."
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
    "This announcement has already been published, so its title, text, scope and target "
    "can no longer be changed. People have already read it. Withdraw it and write a new "
    "one if it needs to be corrected."
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
    "Announcement published. Everyone it is addressed to can read it now, and has been "
    "notified."
)
_WITHDRAWN_OK_MESSAGE = (
    "Announcement withdrawn. Nobody can read it any more. It is kept as a permanent "
    "record of what was posted and when it was taken down."
)


# ======================================================================
# URLs
# ======================================================================


def _overview_url():
    return url_for("admin.announcements_overview")


def _detail_url(public_id):
    return url_for("admin.announcement_detail", announcement_public_id=public_id)


def _new_url():
    return url_for("admin.announcement_create")


def _edit_url(public_id):
    return url_for("admin.announcement_edit", announcement_public_id=public_id)


# ======================================================================
# Target resolution -- scope, public id, locks
# ======================================================================


class _Target:
    """One resolved announcement target: which scope it is, which row it
    names, and the academic ids the lock chain needs.

    ``course_id`` / ``group_id`` are the **columns** to write;
    ``term_id`` / ``level_id`` / ``lock_course_id`` are what the lock
    chain must take. They differ for a group-scoped announcement, whose
    ``course_id`` column stays NULL while its Group's Course is still
    locked.
    """

    __slots__ = (
        "scope", "public_id", "course_id", "group_id",
        "term_id", "level_id", "lock_course_id", "group_public_id", "state",
        "label",
    )

    def __init__(self, scope, public_id="", course_id=None, group_id=None,
                 term_id=None, level_id=None, lock_course_id=None,
                 group_public_id=None, state=CENTER_TARGET_STATE, label=""):
        self.scope = scope
        self.public_id = public_id
        self.course_id = course_id
        self.group_id = group_id
        self.term_id = term_id
        self.level_id = level_id
        self.lock_course_id = lock_course_id
        self.group_public_id = group_public_id
        self.state = state
        self.label = label


def _resolve_target(scope, target_public_id):
    """The :class:`_Target` a validated ``(scope, public id)`` pair names,
    or ``None`` when it names nothing usable.

    ``None`` covers a Course or Group that does not exist, one whose
    public id belongs to a different kind of object, and one that is no
    longer operational. All three produce the same non-disclosing
    message: an Administrator picking from a list of current objects has
    no legitimate need to learn that some *other* id exists.
    """
    if scope == _CENTER:
        return _Target(_CENTER, label="Everybody at the center")
    if scope == _COURSE:
        row = course_by_public_id(target_public_id)
        if row is None or row.status != "active" or row.level_status != "active":
            return None
        return _Target(
            _COURSE,
            public_id=row.public_id,
            course_id=row.id,
            level_id=row.level_id,
            lock_course_id=row.id,
            state=target_lifecycle_state(_COURSE, (row.level_status, row.status)),
            label=f"{row.title} — {row.level_name}",
        )
    if scope == _GROUP:
        row = group_by_public_id(target_public_id)
        if row is None:
            return None
        if (
            row.status != "active"
            or row.course_status != "active"
            or row.level_status != "active"
            or row.term_status != "active"
        ):
            return None
        return _Target(
            _GROUP,
            public_id=row.public_id,
            group_id=row.id,
            term_id=row.academic_term_id,
            level_id=row.level_id,
            lock_course_id=row.course_id,
            group_public_id=row.public_id,
            state=target_lifecycle_state(
                _GROUP,
                (row.term_status, row.level_status, row.course_status, row.status),
            ),
            label=f"{row.name} — {row.course_title} — {row.term_name}",
        )
    return None  # pragma: no cover -- the form validated the scope


def _stored_target(row):
    """The :class:`_Target` an already-stored announcement points at, from
    the management projection.

    Deliberately re-resolved from the **current** Course / Group rather
    than trusted from the stored row: the lifecycle half of a target's
    state is exactly what may have changed since, and a publish token
    binds it.
    """
    if row.scope == _CENTER:
        return _Target(_CENTER, label="Everybody at the center")
    if row.scope == _COURSE:
        return _resolve_target(_COURSE, row.course_public_id)
    return _resolve_target(_GROUP, row.group_public_id)


def _locked_target_state(target, locks):
    """The target's lifecycle as the **locked** rows describe it now. A
    missing row is reported as ``missing`` rather than skipped, so a
    vanished ancestor can never coincidentally match a stale token."""

    def status(row):
        return "missing" if row is None else row.status

    if target.scope == _CENTER:
        return CENTER_TARGET_STATE
    if target.scope == _COURSE:
        return target_lifecycle_state(
            _COURSE,
            (
                status(locks.hierarchy.level(target.level_id)),
                status(locks.hierarchy.course(target.lock_course_id)),
            ),
        )
    return target_lifecycle_state(
        _GROUP,
        (
            status(locks.hierarchy.term(target.term_id)),
            status(locks.hierarchy.level(target.level_id)),
            status(locks.hierarchy.course(target.lock_course_id)),
            status(locks.group),
        ),
    )


def _lock_for(target, actor_id, announcement_id=None):
    """Take the M09 lock chain for one target, passing only the links that
    target actually has. See
    ``app/services/announcement_transactions.py``."""
    return lock_announcement_chain(
        actor_id,
        term_id=target.term_id,
        level_id=target.level_id,
        course_id=target.lock_course_id,
        group_public_id=target.group_public_id,
        lock_assignment=False,
        announcement_id=announcement_id,
    )


def _target_block(target, locks):
    """``None`` when the **locked** target still exists, is still active,
    and (for a Group) is still hanging off the same academic chain -- else
    the one non-disclosing message."""
    if target.scope == _CENTER:
        return None
    if target.scope == _GROUP:
        if not locked_group_matches(
            locks, target.group_id, target.term_id, target.level_id,
            target.lock_course_id,
        ):
            return _TARGET_GONE_MESSAGE
    labels = archived_locked_labels(
        locks,
        term_id=target.term_id,
        level_id=target.level_id,
        course_id=target.lock_course_id,
        include_group=target.scope == _GROUP,
    )
    return _TARGET_GONE_MESSAGE if labels else None


def _fresh_admin_authorization(actor_id):
    """Prove from **current database state** that `actor_id` is still an
    active Administrator, for a path that has rolled back and released its
    locks. The actor is identified by a **scalar id captured before the
    reset**, never by ``current_user``."""
    if actor_id is None:  # pragma: no cover -- an authenticated view always has one
        abort(404)
    actor = db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    if actor is None or actor.role != _ADMINISTRATOR or actor.status != _USER_ACTIVE:
        abort(404)


def _reject(message, url, level="danger"):
    """Release any lock, re-prove authorization from current state, then
    flash and redirect to a plain GET (PRG)."""
    actor_id = current_user.id
    db.session.rollback()
    _fresh_admin_authorization(actor_id)
    flash(message, level)
    return redirect(url)


def _lifecycle_block(status):
    if status == _PUBLISHED:
        return _NOT_DRAFT_MESSAGE
    if status == _WITHDRAWN:
        return _WITHDRAWN_MESSAGE
    return None


def _announcement_or_404(public_id):
    row = admin_announcement(public_id)
    if row is None:
        abort(404)
    return row


# ======================================================================
# Overview
# ======================================================================


@admin_bp.get("/announcements")
@roles_required(UserRole.ADMINISTRATOR.value)
def announcements_overview():
    """One bounded page of the center's announcements, newest first, with
    the scope and status filters.

    Fixed page size, deterministic SQL ordering,
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag and **no** ``COUNT``.
    Both filters are normalised to a known value or dropped before they
    reach SQL.
    """
    page = normalize_page(request.args.get("page"))
    scope = normalize_scope_filter(request.args.get("scope"))
    status = normalize_status_filter(request.args.get("status"))

    rows, has_next = admin_announcements_page(page, scope=scope, status=status)
    if not rows and page > 1:
        page = 1
        rows, has_next = admin_announcements_page(page, scope=scope, status=status)

    return _private_no_store(
        "admin/announcements/overview.html",
        announcements=build_management_view(rows, _tz_name()),
        scope_choices=SCOPE_CHOICES,
        scope_labels=SCOPE_LABELS,
        status_labels=STATUS_LABELS,
        filter_scope=scope or "",
        filter_status=status or "",
        tz_name=_tz_name(),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@admin_bp.get("/announcements/<announcement_public_id>")
@roles_required(UserRole.ADMINISTRATOR.value)
def announcement_detail(announcement_public_id):
    """One announcement in full, with whichever of the two lifecycle
    controls its current state allows.

    Management data -- the author, the version, the lifecycle timestamps
    -- appears here and only here and on the Teacher's own group page:
    the reader surfaces never fetch any of it. What does **not** appear,
    on any surface, is who was notified: no recipient list and no
    individual notification row is part of announcement administration.
    """
    row = _announcement_or_404(announcement_public_id)
    target = _stored_target(row)

    publish_token = None
    withdraw_token = None
    if row.status == _DRAFT and target is not None:
        publish_token = make_token(
            _SURFACE,
            "announcement-publish",
            actor_public_id=current_user.public_id,
            announcement_public_id=row.public_id,
            announcement_version=row.version,
            scope=row.scope,
            target_public_id=target.public_id,
            target_state=target.state,
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
        "admin/announcements/detail.html",
        announcement=build_management_view([row], _tz_name())[0],
        target_available=target is not None,
        publish_token=publish_token,
        withdraw_token=withdraw_token,
        edit_url=_edit_url(row.public_id),
        overview_url=_overview_url(),
        tz_name=_tz_name(),
    )


# ======================================================================
# Draft creation and editing
# ======================================================================


def _render_form(form, announcement=None, message=None, level="danger"):
    """Render the create / edit page against **current persisted state**,
    with a freshly minted token."""
    if message is not None:
        flash(message, level)
    # The draft token binds the scope and target the **stored row** has --
    # never whatever the form currently shows.
    #
    # On a **create** there is no stored row at all, so all four bound
    # values are the "no row yet" sentinels: empty id, version 0, empty
    # scope, empty target. Binding the scope of a blank form would be
    # binding the *author's unmade choice* rather than any persisted
    # state, and would reject the ordinary act of picking a scope other
    # than the one the select happened to open on. The scope and target
    # an author does choose are validated by the form and then resolved
    # and re-proved against the locked rows, which is where that decision
    # actually belongs.
    #
    # On an **edit** the bound values are the persisted ones, so a
    # retarget attempted against a row somebody else has already
    # retargeted is caught rather than applied.
    payload = {
        "actor_public_id": current_user.public_id,
        "announcement_public_id": "" if announcement is None else announcement.public_id,
        "announcement_version": 0 if announcement is None else announcement.version,
        "scope": "",
        "target_public_id": "",
    }
    if announcement is not None:
        payload["scope"] = announcement.scope
        payload["target_public_id"] = (
            announcement.course_public_id
            if announcement.scope == _COURSE
            else (announcement.group_public_id or "")
        )
    return _private_no_store(
        "admin/announcements/form.html",
        form=form,
        announcement=announcement,
        title_max=ANNOUNCEMENT_TITLE_MAX_LENGTH,
        body_max=ANNOUNCEMENT_BODY_MAX_LENGTH,
        cancel_url=(
            _overview_url() if announcement is None else _detail_url(announcement.public_id)
        ),
        announcement_state=make_token(_SURFACE, "announcement-draft", **payload),
    )


def _build_form(announcement=None, formdata=None, data=None):
    return AdminAnnouncementForm(
        formdata=formdata,
        course_choices=course_choice_rows(),
        group_choices=group_choice_rows(),
        data=data,
    )


@admin_bp.route("/announcements/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def announcement_create():
    """Write one new **draft** announcement in any of the three scopes.

    A new announcement is always a draft. There is no "create and
    publish" path and no ``status`` input: publication is a separate,
    deliberate action with its own confirmation, its own token and its own
    notification, and collapsing the two would mean a slip of the mouse
    could tell the whole center something.
    """
    form = _build_form(formdata=request.form if request.method == "POST" else None)
    if request.method == "GET" or not form.validate_on_submit():
        return _render_form(form)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("announcement_state")
    scope = form.scope.data
    title, body = form.normalized_title, form.normalized_body

    target = _resolve_target(scope, form.target_public_id)
    if target is None:
        return _render_form(form, message=_TARGET_GONE_MESSAGE)

    locks = _lock_for(target, actor_id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)

    block = _target_block(target, locks)
    if block is not None:
        return _reject(block, _new_url())

    if token_is_stale(
        _SURFACE,
        token,
        "announcement-draft",
        actor_public_id=actor_public_id,
        announcement_public_id="",
        announcement_version=0,
        scope="",
        target_public_id="",
    ):
        return _reject(_STALE_MESSAGE, _new_url())

    moment = _write_moment()
    announcement = Announcement(
        author_id=actor_id,
        scope=scope,
        course_id=target.course_id,
        group_id=target.group_id,
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
        return _reject(_INTEGRITY_MESSAGE, _new_url())

    created_public_id = announcement.public_id
    flash(_DRAFT_SAVED_MESSAGE, "success")
    return redirect(_detail_url(created_public_id))


@admin_bp.route("/announcements/<announcement_public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def announcement_edit(announcement_public_id):
    """Change one **draft** announcement's scope, target, title or text.

    Only a draft can be edited. A published announcement's title, text,
    scope, target and author are frozen permanently, and a withdrawn one
    is frozen for ever; both are re-proved against the **locked** row.

    A save whose normalized title, body, scope **and** target all equal
    the stored ones is a no-op: no version moves, no timestamp moves, and
    the transaction is rolled back.
    """
    row = _announcement_or_404(announcement_public_id)
    detail_url = _detail_url(announcement_public_id)

    block = _lifecycle_block(row.status)
    if block is not None:
        flash(block, "warning")
        return redirect(detail_url)

    stored_target_public_id = (
        row.course_public_id if row.scope == _COURSE else (row.group_public_id or "")
    )
    form = _build_form(
        formdata=request.form if request.method == "POST" else None,
        data={
            "scope": row.scope,
            "course": row.course_public_id or "",
            "group": row.group_public_id or "",
            "title": row.title,
            "body": row.body,
        },
    )
    if request.method == "GET" or not form.validate_on_submit():
        return _render_form(form, announcement=row)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("announcement_state")
    scope = form.scope.data
    title, body = form.normalized_title, form.normalized_body
    announcement_id, version = row.id, row.version
    edit_url = _edit_url(announcement_public_id)

    target = _resolve_target(scope, form.target_public_id)
    if target is None:
        return _render_form(form, announcement=row, message=_TARGET_GONE_MESSAGE)

    locks = _lock_for(target, actor_id, announcement_id=announcement_id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)
    locked = locks.announcement
    if locked is None or locked.public_id != announcement_public_id:
        db.session.rollback()
        abort(404)

    target_block = _target_block(target, locks)
    if target_block is not None:
        return _reject(target_block, edit_url)

    if token_is_stale(
        _SURFACE,
        token,
        "announcement-draft",
        actor_public_id=actor_public_id,
        announcement_public_id=announcement_public_id,
        announcement_version=locked.version,
        scope=locked.scope,
        target_public_id=stored_target_public_id,
    ):
        return _reject(_STALE_MESSAGE, edit_url)

    lifecycle = _lifecycle_block(locked.status)
    if lifecycle is not None:
        return _reject(lifecycle, detail_url, "warning")
    if locked.version != version:  # pragma: no cover -- the token already caught it
        return _reject(_STALE_MESSAGE, edit_url)

    if (
        locked.title == title
        and locked.body == body
        and locked.scope == scope
        and locked.course_id == target.course_id
        and locked.group_id == target.group_id
    ):
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(detail_url)

    moment = _write_moment()
    locked.title = title
    locked.body = body
    locked.scope = scope
    locked.course_id = target.course_id
    locked.group_id = target.group_id
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, edit_url)

    flash(_DRAFT_SAVED_MESSAGE, "success")
    return redirect(detail_url)


# ======================================================================
# Publication and withdrawal
# ======================================================================


@admin_bp.post("/announcements/<announcement_public_id>/publish")
@roles_required(UserRole.ADMINISTRATOR.value)
def announcement_publish(announcement_public_id):
    """Publish one draft announcement.

    POST only, CSRF-protected, and behind a signed publish token that
    binds the announcement's version **and** the target's current
    lifecycle. The state transition is one atomic commit; the notification
    producer runs only afterwards, in its own transaction, on the one
    request that actually performed it.
    """
    row = _announcement_or_404(announcement_public_id)
    detail_url = _detail_url(announcement_public_id)

    if row.status == _PUBLISHED:
        flash(_ALREADY_PUBLISHED_MESSAGE, "warning")
        return redirect(detail_url)
    if row.status == _WITHDRAWN:
        flash(_WITHDRAWN_MESSAGE, "warning")
        return redirect(detail_url)

    target = _stored_target(row)
    if target is None:
        flash(_TARGET_GONE_MESSAGE, "danger")
        return redirect(detail_url)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("announcement_state")
    announcement_id = row.id

    locks = _lock_for(target, actor_id, announcement_id=announcement_id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)
    locked = locks.announcement
    if locked is None or locked.public_id != announcement_public_id:
        db.session.rollback()
        abort(404)

    block = _target_block(target, locks)
    if block is not None:
        return _reject(block, detail_url)

    if token_is_stale(
        _SURFACE,
        token,
        "announcement-publish",
        actor_public_id=actor_public_id,
        announcement_public_id=announcement_public_id,
        announcement_version=locked.version,
        scope=locked.scope,
        target_public_id=target.public_id,
        target_state=_locked_target_state(target, locks),
    ):
        return _reject(_STALE_MESSAGE, detail_url)

    if locked.status == _PUBLISHED:
        return _reject(_ALREADY_PUBLISHED_MESSAGE, detail_url, "warning")
    if locked.status != _DRAFT:
        return _reject(_WITHDRAWN_MESSAGE, detail_url, "warning")

    scope, course_id, group_id = locked.scope, locked.course_id, locked.group_id
    moment = _write_moment()
    locked.status = _PUBLISHED
    locked.published_at = moment
    locked.version = locked.version + 1
    locked.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject(_INTEGRITY_MESSAGE, detail_url)

    # Committed and the response already decided: plain scalars only, then
    # best-effort delivery in its own transaction. The acting
    # Administrator is not among the recipients -- the producer selects
    # Students and Teachers, and nothing else.
    flash(_PUBLISHED_OK_MESSAGE, "success")
    response = redirect(detail_url)
    notify_announcement_published(announcement_id, scope, course_id, group_id)
    return response


@admin_bp.post("/announcements/<announcement_public_id>/withdraw")
@roles_required(UserRole.ADMINISTRATOR.value)
def announcement_withdraw(announcement_public_id):
    """Withdraw one published announcement, permanently.

    The announcement stops being readable the instant this commits.
    Notifications already delivered about it remain as personal
    historical rows, but following one now lands on the ordinary
    non-disclosing 404. There is no unwithdraw, no restore and no
    republish, and no endpoint exists for one.

    Deliberately **not** gated on the target still being operational: a
    notice that should no longer be standing must always be removable,
    including after the Course or Group it belonged to has been archived.
    """
    row = _announcement_or_404(announcement_public_id)
    detail_url = _detail_url(announcement_public_id)

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

    # Withdrawal takes the same chain the target would take **even when
    # that target is no longer operational**: the locks are about
    # serializing against concurrent writers, not about proving the target
    # is alive. A target that can no longer be resolved therefore falls
    # back to locking only the actor and the announcement, so a notice
    # under an archived Course or Group can always still be taken down.
    target = _stored_target(row) or _Target(row.scope, public_id="")
    locks = _lock_for(target, actor_id, announcement_id=row.id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        abort(404)
    locked = locks.announcement
    if locked is None or locked.public_id != announcement_public_id:
        db.session.rollback()
        abort(404)

    if token_is_stale(
        _SURFACE,
        token,
        "announcement-withdraw",
        actor_public_id=actor_public_id,
        announcement_public_id=announcement_public_id,
        announcement_version=locked.version,
    ):
        return _reject(_STALE_MESSAGE, detail_url)

    if locked.status != _PUBLISHED:
        return _reject(
            _WITHDRAWN_MESSAGE if locked.status == _WITHDRAWN else _NOT_PUBLISHED_MESSAGE,
            detail_url,
            "warning",
        )

    moment = _write_moment()
    # ``withdrawn_at >= published_at`` is a CHECK, and a clock injected by
    # a test (or a machine whose clock moved backwards) could violate it.
    # Taking the later of the two keeps the record honest without refusing
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
        return _reject(_INTEGRITY_MESSAGE, detail_url)

    flash(_WITHDRAWN_OK_MESSAGE, "success")
    return redirect(detail_url)
