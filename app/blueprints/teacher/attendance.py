"""Teacher recording of Group attendance for real scheduled meetings
(Phase 4 / M07).

Group- and session-centered routes only, every object addressed by
``public_id`` and no internal numeric id anywhere in a URL, a form value,
a signed token or the rendered HTML::

    GET       /teacher/attendance
    GET       /teacher/groups/<gp>/attendance
    GET|POST  /teacher/groups/<gp>/attendance/new
    POST      /teacher/groups/<gp>/attendance/new/confirm
    GET       /teacher/groups/<gp>/attendance/<sp>
    GET|POST  /teacher/groups/<gp>/attendance/<sp>/mark
    POST      /teacher/groups/<gp>/attendance/<sp>/finalize

The only flat route is the Teacher's own overview of the Groups they are
assigned to; everything else is nested under a Group. There is
deliberately **no delete, archive, reopen, unlock, restore, duplicate,
bulk-generate, export or grade action** for a session or a record --
none of that exists server-side either, and no placeholder is left for
one.

**A session is a scheduled occurrence, not a free-form event.** The
create flow is two explicit steps for one reason: the signed create token
must bind the Teacher, the Group, the **Schedule** and the **selected
local date**, and none of those last two exist until the Teacher has
chosen them. Step one (``GET|POST .../new``) validates the choice and
renders a confirmation page carrying that exact-shape token; step two
(``POST .../new/confirm``) takes the locks and writes. Step one writes
nothing at all.

**The roster is captured once and then frozen.** Creation inserts the
session and one ``absent`` record per eligible active Student in the same
transaction. Afterwards the *set* of records never changes: a later
enrollment adds nothing, and a withdrawal, suspension, re-assignment,
Schedule change or Group archival removes nothing. A Group with no
eligible active Student cannot open a session at all.

**Two different protections, both preserved.** The lock chain
(``app/services/attendance_transactions.py``) decides against the rows as
they are *now*; the signed exact-shape tokens decide against the state the
form was *opened* on. Neither replaces the other: the locks stop two
co-teachers interleaving a write, and the token turns the loser of that
race into an explicit "reload and review" rejection instead of a silent
overwrite.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then server-side
and nested, reusing the exact helpers every other Teacher surface uses:
``_teacher_group_or_404`` proves an **active**
``GroupTeacherAssignment`` to the Group in the URL, and every nested
lookup is constrained by that Group in SQL. A missing Group, a missing
session, a session belonging to another Group, a Schedule belonging to
another Group, an unassigned Teacher and a removed assignment all return
the same non-disclosing **404** -- never a 403, and never a hint that the
object exists. Multiple active assigned Teachers are equal collaborators
on the same session.

**Reading is historical; writing is not.** The overview, the list, the
session detail and the record notes stay available to an actively
assigned Teacher even when the Schedule, the Group or an academic
ancestor is archived, so a recorded meeting can always be read back.
Creating a session, saving a draft and finalizing additionally require an
operational chain, re-checked against the **locked** rows.

**Finalization is permanent.** It sets one authoritative whole-second UTC
``finalized_at``, increments the session's ``version`` once, and freezes
the session and every record. A replayed finalization safely returns the
already-finalized detail without touching a timestamp, a version, a
status or a note.
"""

from datetime import date

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import _private_no_store, _tz_name
from app.blueprints.teacher.attendance_forms import (
    NOTE_MAX,
    AttendanceSessionCreateForm,
    parse_marking_submission,
)
from app.blueprints.teacher.units import (
    _archived_chain_labels,
    _authz_broken,
    _group_is_operational,
    _join_labels,
    _teacher_group_or_404,
)
from app.extensions import db
from app.models import (
    AcademicStatus,
    Course,
    Group,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.attendance_queries import (
    OCCURRENCE_FUTURE,
    OCCURRENCE_OUTSIDE_RANGE,
    OCCURRENCE_SCHEDULE_ARCHIVED,
    OCCURRENCE_WRONG_GROUP,
    OCCURRENCE_WRONG_WEEKDAY,
    PAGE_SIZE,
    STATUS_LABELS,
    STATUS_ORDER,
    active_schedules_for_group,
    build_record_view,
    build_session_list_view,
    captured_record_ids,
    captured_student_ids,
    eligible_roster_rows,
    normalize_page,
    occurrence_rejection,
    ordered_counts,
    schedule_for_group,
    session_for_group,
    session_for_occurrence,
    session_records,
    session_schedule_weekday,
    status_counts_for_sessions,
    teacher_group_cards,
    teacher_is_actively_assigned,
    teacher_sessions_page,
)
from app.services.attendance_transactions import (
    create_session_with_roster,
    eligible_locked_student_ids,
    lock_creation_chain,
    lock_session_chain,
)
from app.services.schedule_occurrences import to_app_local, utc_reference_now

_ACTIVE = AcademicStatus.ACTIVE.value
_TEACHER = UserRole.TEACHER.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: The canonical, deterministic serialization of the one date a create
#: token carries. A fixed ISO day, so a value that survives a JSON round
#: trip inside the signed token compares byte-for-byte against a freshly
#: rendered one and can never look "changed" merely because it was
#: formatted differently.
_DATE_FORMAT = "%Y-%m-%d"


def _write_moment():
    """The **authoritative** naive-UTC moment for one attendance write,
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


def _today_local():
    """Today's **local civil date** in the center timezone.

    Every "is this date in the future?" decision compares against this,
    never against a UTC date: near midnight the two differ, and a Teacher
    recording the evening class must not be told that today has not
    happened yet. Propagates ``TimezoneConfigError`` from
    ``to_app_local`` rather than silently pretending the center runs on
    UTC.
    """
    return to_app_local(_tz_name(), utc_reference_now()).date()


# ======================================================================
# Teacher-facing sentences, declared once each
# ======================================================================
#
# Every rule a Teacher can hit is stated in exactly one place, so the
# friendly pre-lock check and the authoritative post-lock check can never
# explain the same rule differently.

_NOT_OPERATIONAL_MESSAGE = (
    "Attendance can only be recorded while the group and its academic term, course, and "
    "level are all active. Existing attendance stays readable."
)
_OPERATIONAL_WORDING = (
    "Attendance can only be recorded",
    "Existing attendance stays readable.",
)

_OCCURRENCE_MESSAGES = {
    OCCURRENCE_WRONG_GROUP: (
        "That scheduled class is not one of this group's. Please choose a class from the "
        "list and try again."
    ),
    OCCURRENCE_SCHEDULE_ARCHIVED: (
        "That scheduled class has been archived, so it no longer has meetings you can "
        "record. Attendance already recorded from it stays readable."
    ),
    OCCURRENCE_WRONG_WEEKDAY: (
        "That date is not a day this class meets. Choose a date that falls on the class's "
        "own weekday."
    ),
    OCCURRENCE_OUTSIDE_RANGE: (
        "That date is outside the period this class runs for. Choose a date inside the "
        "class's start and end dates."
    ),
    OCCURRENCE_FUTURE: (
        "That date has not arrived yet in the center timezone. You can record attendance "
        "for today or for a class that has already happened."
    ),
}

_NO_SCHEDULE_MESSAGE = (
    "This group has no active scheduled class, so there is no meeting to record attendance "
    "for. Ask an administrator to add a schedule first."
)
_EMPTY_ROSTER_MESSAGE = (
    "This group has no active enrolled students, so there is nobody to record attendance "
    "for. An attendance session is never created empty."
)
_DUPLICATE_MESSAGE = (
    "Attendance for that class date has already been opened. You are looking at it now."
)
_STALE_MESSAGE = (
    "This attendance session was changed by someone else since this page was opened. Your "
    "changes were not saved. Please reload, read the current marks, and make your change "
    "against them."
)
_FINALIZED_MESSAGE = (
    "This attendance session has been finalized, so its marks and notes can no longer be "
    "changed. Finalized attendance is permanent — there is no way to reopen, edit or "
    "delete it."
)
_ALREADY_FINALIZED_MESSAGE = (
    "This attendance session was already finalized. Nothing was changed."
)
_NO_CHANGES_MESSAGE = "Nothing was changed, so nothing was saved."
_SAVED_MESSAGE = "Attendance saved as a draft. It is not final until you finalize it."
_FINALIZE_CONFIRM_MESSAGE = (
    "Please tick the confirmation box before finalizing. Finalizing cannot be undone."
)
_FINALIZED_OK_MESSAGE = (
    "Attendance finalized. It is now permanent and can no longer be changed."
)
_ROSTER_BROKEN_MESSAGE = (
    "This attendance session's student list could not be read completely, so nothing was "
    "changed. Nothing has been deleted. Please reload, and ask an administrator to review "
    "this session if the problem continues."
)
_INTEGRITY_MESSAGE = (
    "That change could not be saved because the data changed at the same moment. Nothing "
    "was written. Please reload and try again."
)
_GROUP_CHANGED_MESSAGE = (
    "This group changed while you were working. Reload the page and try again."
)


# ======================================================================
# URLs
# ======================================================================


def _list_url(group_public_id):
    return url_for("teacher.group_attendance", group_public_id=group_public_id)


def _new_url(group_public_id):
    return url_for("teacher.attendance_create", group_public_id=group_public_id)


def _detail_url(group_public_id, session_public_id):
    return url_for(
        "teacher.attendance_detail",
        group_public_id=group_public_id,
        session_public_id=session_public_id,
    )


def _mark_url(group_public_id, session_public_id):
    return url_for(
        "teacher.attendance_mark",
        group_public_id=group_public_id,
        session_public_id=session_public_id,
    )


# ======================================================================
# Signed exact-shape state tokens (Phase 4 / M07)
# ======================================================================
#
# Three dedicated M07 salts and three exact purpose markers. A token
# minted under any other salt -- every M01 assignment snapshot, M02
# submission context, M03 feedback state, M04 quiz token, M05 listening
# token and M06 speaking token -- fails signature verification here even
# though all of them are signed with the same application SECRET_KEY, and
# a token minted under one of these salts but for another M07 purpose
# fails the purpose check. The reverse holds too.
#
# Every payload carries **public identifiers, versions, one date and a
# purpose only**. A signed token is authenticated, not encrypted: anyone
# holding it can read its payload, so no Student name, no attendance
# status, no note, no roster size, no internal database id and no
# schedule detail is ever placed in one. The draft token does carry every
# captured record's public id and version -- that is exactly the state it
# must bind -- and nothing about who those records are for.
#
# The shape check below is exact and typed rather than merely "is a
# dict": the key set must match exactly, the purpose must be the expected
# one, identifiers must be strings, versions must be genuine positive
# ints (``bool`` excluded explicitly, since it is an ``int`` subclass and
# ``True`` must never pass as version 1), and the record state must be a
# list of well-formed ``[public_id, version]`` pairs.

_SALTS = {
    "attendance-create": "teacher.attendance-create.phase4-m07.v1",
    "attendance-draft": "teacher.attendance-draft.phase4-m07.v1",
    "attendance-finalize": "teacher.attendance-finalize.phase4-m07.v1",
}

_FIELDS = {
    # Creation binds the Teacher, the Group, the chosen Schedule and the
    # chosen local civil date -- the Part's exact required shape. There is
    # no session to bind yet, and nothing about the roster is bound:
    # the roster is whatever the locked rows say at the instant of
    # capture, which a token minted a moment earlier could not promise.
    "attendance-create": (
        "purpose",
        "teacher_public_id",
        "group_public_id",
        "schedule_public_id",
        "session_date",
    ),
    # A draft save binds the session's current version AND the exact
    # captured record public ids with their versions. Binding the session
    # version alone would miss a co-teacher who changed one record; binding
    # the records alone would miss a finalization. Together they are the
    # complete state the form was written against.
    "attendance-draft": (
        "purpose",
        "teacher_public_id",
        "group_public_id",
        "session_public_id",
        "session_version",
        "records",
    ),
    "attendance-finalize": (
        "purpose",
        "teacher_public_id",
        "group_public_id",
        "session_public_id",
        "session_version",
    ),
}


def _serializer(salt):
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=salt)


def _positive_int(value):
    """True for a genuine positive ``int``.

    ``bool`` is excluded explicitly: it is a subclass of ``int`` in
    Python, and ``True`` must never be accepted as version 1.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _record_state(records):
    """The canonical bound state of a session's captured records:
    ``[[public_id, version], ...]`` sorted by ``public_id``.

    Sorted by identifier rather than by the page's display order on
    purpose. The marking page lists Students by name, and an
    administrator renaming an account between the GET and the POST would
    otherwise reorder the list and make an untouched form look stale.
    Sorting by ``public_id`` binds the *set and its versions*, which is
    what actually matters, and nothing about presentation.
    """
    return sorted(
        [record["public_id"], record["version"]] for record in records
    )


def _make_token(purpose, **payload):
    """Sign one exact-shape M07 token.

    Only ever called with freshly read persisted state, never with
    attempted form values: a fresh token may only pair with fresh state,
    which is precisely the bypass the stale rejection exists to close.
    """
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(_SALTS[purpose]).dumps(body)


def _load_token(token, purpose):
    """The token's payload, or ``None`` for a missing, malformed,
    invalidly signed, wrong-purpose, wrong-salt or wrong-shaped one.

    Every ``None`` is treated exactly like an outdated token: rejected,
    never trusted, and never silently upgraded into a claim about a row.
    """
    if not token:
        return None
    try:
        payload = _serializer(_SALTS[purpose]).loads(token)
    except BadSignature:
        return None
    fields = _FIELDS[purpose]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return None
    if payload["purpose"] != purpose:
        return None
    for field in fields:
        value = payload[field]
        if field == "session_version":
            if not _positive_int(value):
                return None
        elif field == "records":
            if not isinstance(value, list) or not value:
                return None
            for entry in value:
                if not isinstance(entry, list) or len(entry) != 2:
                    return None
                if not isinstance(entry[0], str) or not _positive_int(entry[1]):
                    return None
        elif not isinstance(value, str):
            return None
    return payload


def _token_is_stale(token, purpose, **expected):
    """True when `token` does not exactly describe `expected`.

    The expected values must come from the rows this request **locked**,
    never from a pre-lock preview: that is what closes the window between
    the form's GET and the write, and what turns a losing co-teacher race
    into an explicit "reload and review" rejection rather than a silent
    overwrite.
    """
    payload = _load_token(token, purpose)
    if payload is None:
        return True
    for field, value in expected.items():
        if field == "records":
            if sorted(list(item) for item in payload[field]) != sorted(
                list(item) for item in value
            ):
                return True
        elif payload[field] != value:
            return True
    return False


# ======================================================================
# Fresh authorization -- the only evidence a post-rollback path may use
# ======================================================================


def _fresh_attendance_authorization(actor_id, group_public_id, session_public_id=None):
    """Prove from **current database state** that `actor_id` may read this
    Group -- and, when asked, this session -- *right now*, and return
    ``(group, session)``.

    ``roles_required`` runs once, before the view, so a path that has
    rolled back and released its locks no longer holds current evidence,
    and the same concurrent change that forced the rollback may have ended
    this Teacher's access. ``_teacher_group_or_404`` alone is not enough
    either -- it proves an active ``GroupTeacherAssignment`` but never
    re-reads the actor's own ``role`` and ``status``. The actor is
    identified by a **scalar id captured before the reset**, never by
    ``current_user``.

    What is proved, in order, all as current reads: the acting User row
    exists, its role is ``teacher``, its status is ``active``, an active
    ``GroupTeacherAssignment`` links it to the exact Group named in the
    URL, and the session belongs to that exact Group.

    What is deliberately **not** proved, because reading is historical:
    the AcademicTerm / Level / Course / Group / Schedule need not be
    active, and the session need not be a draft.
    """
    if actor_id is None:
        abort(404)

    actor = db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    if actor is None or actor.role != _TEACHER or actor.status != _USER_ACTIVE:
        abort(404)

    group = (
        Group.query.options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter_by(public_id=group_public_id)
        .first()
    )
    if group is None:
        abort(404)

    if not teacher_is_actively_assigned(actor_id, group.id):
        abort(404)

    if session_public_id is None:
        return group, None
    session = session_for_group(group.id, session_public_id)
    if session is None:
        abort(404)
    return group, session


def _session_or_404(group, session_public_id):
    """One session of this Group, or the established non-disclosing 404.
    Another Group's session public id and a nonexistent one fail
    identically here."""
    session = session_for_group(group.id, session_public_id)
    if session is None:
        abort(404)
    return session


def _hierarchy_context(group):
    return group.academic_term_id, group.course.level_id, group.course_id


def _operational_block(locks, group_id_expected, term_id, level_id, course_id):
    """``None`` if the **locked** Group and its locked AcademicTerm /
    Level / Course all exist and are active (so this write may proceed),
    else a Teacher-facing message.

    Same shape and same reasoning as M01's, M05's and M06's equivalents;
    the wording differs because what is blocked differs, and it comes from
    a constant declared once.
    """
    group = locks.group
    term = locks.hierarchy.term(term_id)
    level = locks.hierarchy.level(level_id)
    course = locks.hierarchy.course(course_id)
    if (
        group is None
        or course is None
        or group.id != group_id_expected
        or group.course_id != course.id
        or group.academic_term_id != term_id
        or course.level_id != level_id
    ):
        return _GROUP_CHANGED_MESSAGE
    labels = [
        label
        for label, row in (
            ("academic term", term),
            ("level", level),
            ("course", course),
            ("group", group),
        )
        if row is None or row.status != _ACTIVE
    ]
    if labels:
        verb = "is" if len(labels) == 1 else "are"
        lead, tail = _OPERATIONAL_WORDING
        return (
            f"{lead} while the group and its academic term, course, and level are all "
            f"active. The {_join_labels(labels)} {verb} archived. {tail}"
        )
    return None


def _roster_broken(locks, session_id, expected_record_ids):
    """True when the **locked** record rows are no longer the complete,
    intact roster this session captured.

    Every one of the ids the preview read must have locked to a real row
    that still belongs to this exact session, and the set must be
    non-empty. This is what "finalization requires the original complete
    roster to remain intact and valid" means in practice, and it is
    checked on a draft save too: a partially readable roster must never
    be half-written.
    """
    if not expected_record_ids:
        return True
    if set(locks.records) != set(expected_record_ids):
        return True
    for record in locks.records.values():
        if record is None or record.attendance_session_id != session_id:
            return True
    return False


# ======================================================================
# Overview -- the Teacher's assigned Groups
# ======================================================================


@teacher_bp.get("/attendance")
@roles_required(UserRole.TEACHER.value)
def attendance_overview():
    """Every Group this Teacher is actively assigned to, as the way in to
    each Group's attendance history.

    Bounded by how many Groups one Teacher is assigned to -- an
    operational number, not a history that grows with time. Archived
    Groups are deliberately listed: reading attendance is historical, and
    this is the page that reaches it.
    """
    return _private_no_store(
        "teacher/attendance/overview.html",
        cards=teacher_group_cards(current_user.id),
        tz_name=_tz_name(),
    )


# ======================================================================
# Group-scoped session list
# ======================================================================


@teacher_bp.get("/groups/<group_public_id>/attendance")
@roles_required(UserRole.TEACHER.value)
def group_attendance(group_public_id):
    """One bounded page of a Group's attendance sessions, newest class
    date first.

    Fixed page size, SQL ordering (``session_date DESC, id DESC``), SQL
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag with **no** ``COUNT``,
    and the same page normalization and past-the-end fallback every other
    Teacher list uses. **One** extra bounded aggregate resolves the status
    counts for the at-most-``PAGE_SIZE`` sessions on the page -- asking
    per row would be an N+1.

    Stays available under an archived Group, Schedule or ancestor: an
    eligible assigned Teacher can always read back what was recorded.
    """
    group = _teacher_group_or_404(group_public_id)
    operational = _group_is_operational(group)
    page = normalize_page(request.args.get("page"))

    rows, has_next = teacher_sessions_page(group.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = teacher_sessions_page(group.id, page)

    counts = status_counts_for_sessions([row.id for row in rows])
    return _private_no_store(
        "teacher/attendance/list.html",
        group=group,
        sessions=build_session_list_view(rows, counts),
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=_tz_name(),
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


# ======================================================================
# Create -- step one: choose a scheduled occurrence
# ======================================================================


def _schedule_choices(schedules, tz_name):
    """``(public_id, label)`` pairs for the create form's select.

    The label names the weekday, the wall-clock window, the effective
    period and the location -- everything the Teacher needs to pick the
    right slot -- and the *value* is the Schedule's ``public_id``, never
    an internal id.
    """
    from app.blueprints.admin.forms import WEEKDAY_NAMES

    choices = []
    for schedule in schedules:
        weekday = WEEKDAY_NAMES[schedule.day_of_week]
        window = (
            f"{schedule.start_time.strftime('%H:%M')}–"
            f"{schedule.end_time.strftime('%H:%M')}"
        )
        period = (
            f"{schedule.effective_start_date.isoformat()} to "
            f"{schedule.effective_end_date.isoformat()}"
        )
        place = f" · {schedule.location}" if schedule.location else ""
        choices.append(
            (
                schedule.public_id,
                f"{weekday} {window} ({tz_name}) · {period}{place}",
            )
        )
    return choices


def _render_create_page(form, group, schedules, message=None, category="danger"):
    """Render the chooser. Writes nothing and takes no lock."""
    if message is not None:
        flash(message, category)
    tz_name = _tz_name()
    return _private_no_store(
        "teacher/attendance/new.html",
        form=form,
        group=group,
        has_schedules=bool(schedules),
        tz_name=tz_name,
        today_local=_today_local(),
        cancel_url=_list_url(group.public_id),
    )


@teacher_bp.route("/groups/<group_public_id>/attendance/new", methods=["GET", "POST"])
@roles_required(UserRole.TEACHER.value)
def attendance_create(group_public_id):
    """Choose which scheduled occurrence to open attendance for.

    **This route never writes and never takes a lock.** On a valid choice
    it renders a confirmation page carrying the signed create token that
    binds Teacher, Group, Schedule and the selected local date; the write
    happens only in :func:`attendance_create_confirm`, which re-proves
    every one of those facts against the locked rows. Everything decided
    here is therefore a friendly preview, including the roster size shown
    on the confirmation page -- the authoritative roster is whatever the
    locked rows say at the instant of capture.
    """
    group = _teacher_group_or_404(group_public_id)
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_list_url(group_public_id))

    schedules = active_schedules_for_group(group.id)
    tz_name = _tz_name()
    form = AttendanceSessionCreateForm(
        schedule_choices=_schedule_choices(schedules, tz_name)
    )

    if not schedules:
        return _render_create_page(form, group, schedules, _NO_SCHEDULE_MESSAGE, "warning")
    if not form.validate_on_submit():
        return _render_create_page(form, group, schedules)

    schedule = schedule_for_group(group.id, form.schedule.data)
    session_date = form.session_date.data
    if not isinstance(session_date, date):  # pragma: no cover -- DateField guarantees it
        return _render_create_page(form, group, schedules, _GROUP_CHANGED_MESSAGE)

    rejection = occurrence_rejection(schedule, group.id, session_date, _today_local())
    if rejection is not None:
        return _render_create_page(form, group, schedules, _OCCURRENCE_MESSAGES[rejection])

    existing = session_for_occurrence(schedule.id, session_date)
    if existing is not None:
        flash(_DUPLICATE_MESSAGE, "warning")
        return redirect(_detail_url(group_public_id, existing.public_id))

    roster = eligible_roster_rows(group.id)
    if not roster:
        return _render_create_page(form, group, schedules, _EMPTY_ROSTER_MESSAGE, "warning")

    from app.blueprints.admin.forms import WEEKDAY_NAMES

    return _private_no_store(
        "teacher/attendance/confirm.html",
        group=group,
        tz_name=tz_name,
        session_date=session_date,
        weekday=WEEKDAY_NAMES[schedule.day_of_week],
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        location=schedule.location,
        roster_size=len(roster),
        back_url=_new_url(group_public_id),
        create_token=_make_token(
            "attendance-create",
            teacher_public_id=current_user.public_id,
            group_public_id=group_public_id,
            schedule_public_id=schedule.public_id,
            session_date=session_date.strftime(_DATE_FORMAT),
        ),
    )


# ======================================================================
# Create -- step two: capture the roster under the required locks
# ======================================================================


def _reject_create(group_public_id, message, category="danger"):
    """Release any lock, re-prove authorization from current state, then
    flash and return to the chooser.

    The message is flashed **only after** authorization has passed, so a
    request whose access ended in the same window that caused the failure
    404s silently instead of leaving a message behind for whatever page
    the actor reaches next.
    """
    actor_id = current_user.id
    db.session.rollback()
    _fresh_attendance_authorization(actor_id, group_public_id)
    flash(message, category)
    return redirect(_new_url(group_public_id))


def _resolve_duplicate(group_public_id, schedule_id, session_date):
    """After a lost create race, send the Teacher to the session that
    actually won -- never an error page, and never a second session."""
    actor_id = current_user.id
    db.session.rollback()
    _fresh_attendance_authorization(actor_id, group_public_id)
    existing = session_for_occurrence(schedule_id, session_date)
    if existing is None:
        flash(_INTEGRITY_MESSAGE, "danger")
        return redirect(_new_url(group_public_id))
    flash(_DUPLICATE_MESSAGE, "warning")
    return redirect(_detail_url(group_public_id, existing.public_id))


@teacher_bp.post("/groups/<group_public_id>/attendance/new/confirm")
@roles_required(UserRole.TEACHER.value)
def attendance_create_confirm(group_public_id):
    """Create one attendance session and its complete roster, atomically.

    The full M07 creation lock order is taken by
    ``lock_creation_chain``; **every** authoritative condition is then
    re-checked against those locked rows before a single row is written:
    the acting Teacher's role, account status and active assignment; the
    Group and its academic ancestors being operational; the Schedule
    existing, being active and belonging to this exact Group; the date
    still being a real, already-reached occurrence of it (re-derived from
    the locked Schedule, not from the token); no session already existing
    for that occurrence; and a non-empty eligible roster.

    ``uq_attendance_sessions_schedule_date`` is the final defense behind
    the locked duplicate check, and its ``IntegrityError`` is caught,
    rolled back, re-authorized and resolved to the existing session --
    never surfaced as SQL, driver or transaction detail.
    """
    group = _teacher_group_or_404(group_public_id)
    actor_id, actor_public_id = current_user.id, current_user.public_id

    payload = _load_token(request.form.get("attendance_state"), "attendance-create")
    if (
        payload is None
        or payload["teacher_public_id"] != actor_public_id
        or payload["group_public_id"] != group_public_id
    ):
        flash(_STALE_MESSAGE, "danger")
        return redirect(_new_url(group_public_id))
    try:
        session_date = date.fromisoformat(payload["session_date"])
    except ValueError:
        flash(_STALE_MESSAGE, "danger")
        return redirect(_new_url(group_public_id))

    # Pre-lock preview: it only discovers which rows to lock.
    preview_schedule = schedule_for_group(group.id, payload["schedule_public_id"])
    if preview_schedule is None:
        flash(_OCCURRENCE_MESSAGES[OCCURRENCE_WRONG_GROUP], "danger")
        return redirect(_new_url(group_public_id))
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, schedule_id = group.id, preview_schedule.id
    roster_preview = eligible_roster_rows(group_id)

    locks = lock_creation_chain(
        group_public_id,
        term_id,
        level_id,
        course_id,
        actor_id,
        schedule_id,
        session_date,
        student_ids=[user_id for user_id, _ in roster_preview],
        enrollment_ids=[enrollment_id for _, enrollment_id in roster_preview],
    )

    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_create(group_public_id, block)

    rejection = occurrence_rejection(
        locks.schedule, group_id, session_date, _today_local()
    )
    if rejection is not None:
        return _reject_create(group_public_id, _OCCURRENCE_MESSAGES[rejection])

    if locks.session is not None:
        return _resolve_duplicate(group_public_id, schedule_id, session_date)

    eligible_ids = eligible_locked_student_ids(locks, group_id)
    if not eligible_ids:
        return _reject_create(group_public_id, _EMPTY_ROSTER_MESSAGE, "warning")

    moment = _write_moment()
    try:
        created = create_session_with_roster(
            group_id, locks.schedule, session_date, eligible_ids, moment
        )
        session_public_id = created.public_id
        db.session.commit()
    except IntegrityError:
        return _resolve_duplicate(group_public_id, schedule_id, session_date)

    flash(
        "Attendance opened for this class. Every student starts as absent — mark them and "
        "save as often as you like, then finalize when you are done.",
        "success",
    )
    return redirect(_mark_url(group_public_id, session_public_id))


# ======================================================================
# Session detail -- read only, draft or finalized, current or historical
# ======================================================================


@teacher_bp.get("/groups/<group_public_id>/attendance/<session_public_id>")
@roles_required(UserRole.TEACHER.value)
def attendance_detail(group_public_id, session_public_id):
    """One attendance session, read only, or a non-disclosing 404.

    **This route writes nothing and renders no marking control.** It is
    the finalized session's detail page, and it is equally the historical
    detail page for a session whose Schedule, Group or academic ancestor
    has since been archived -- reading is historical, so neither state
    hides it. A draft under an operational chain additionally offers a
    link to the marking page, which is a plain ``GET`` elsewhere.
    """
    group = _teacher_group_or_404(group_public_id)
    session = _session_or_404(group, session_public_id)
    operational = _group_is_operational(group)
    records = build_record_view(session_records(session.id))
    counts = status_counts_for_sessions([session.id])

    return _private_no_store(
        "teacher/attendance/detail.html",
        group=group,
        session=session,
        weekday=session_schedule_weekday(session.id),
        records=records,
        counts=ordered_counts(counts.get(session.id)),
        is_finalized=session.is_finalized(),
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=_tz_name(),
        list_url=_list_url(group_public_id),
        mark_url=(
            _mark_url(group_public_id, session_public_id)
            if operational and not session.is_finalized()
            else None
        ),
    )


# ======================================================================
# Draft marking -- save repeatedly, change nothing else
# ======================================================================


def _render_marking_page(group, session, submitted=None, messages=(), category="danger"):
    """Render the marking form against **current persisted state**, with
    freshly minted draft and finalize tokens.

    `submitted` (a ``{public_id: (status, note)}`` mapping) is echoed back
    on an ordinary validation failure so a Teacher does not lose the rest
    of their marking over one long note. It is applied on top of the
    persisted rows purely for display; nothing about it is trusted, and
    the tokens are minted from the persisted versions only.
    """
    for message in messages:
        flash(message, category)
    records = build_record_view(session_records(session.id))
    if submitted:
        for record in records:
            if record["public_id"] in submitted:
                status, note = submitted[record["public_id"]]
                record["status"] = status
                record["status_label"] = STATUS_LABELS.get(status, status)
                record["note"] = note
    state = _record_state(records)
    return _private_no_store(
        "teacher/attendance/mark.html",
        group=group,
        session=session,
        weekday=session_schedule_weekday(session.id),
        records=records,
        status_order=STATUS_ORDER,
        status_labels=STATUS_LABELS,
        note_max=NOTE_MAX,
        tz_name=_tz_name(),
        detail_url=_detail_url(group.public_id, session.public_id),
        finalize_url=url_for(
            "teacher.attendance_finalize",
            group_public_id=group.public_id,
            session_public_id=session.public_id,
        ),
        draft_token=_make_token(
            "attendance-draft",
            teacher_public_id=current_user.public_id,
            group_public_id=group.public_id,
            session_public_id=session.public_id,
            session_version=session.version,
            records=state,
        ),
        finalize_token=_make_token(
            "attendance-finalize",
            teacher_public_id=current_user.public_id,
            group_public_id=group.public_id,
            session_public_id=session.public_id,
            session_version=session.version,
        ),
    )


def _reject_mark(group_public_id, session_public_id, message, category="danger"):
    """Release any lock, re-prove authorization and the nested session
    from current state, then flash and redirect to a plain GET (PRG), so
    no submitted value survives a rejection."""
    actor_id = current_user.id
    db.session.rollback()
    _fresh_attendance_authorization(actor_id, group_public_id, session_public_id)
    flash(message, category)
    return redirect(_mark_url(group_public_id, session_public_id))


def _redirect_finalized(group_public_id, session_public_id, message):
    actor_id = current_user.id
    db.session.rollback()
    _fresh_attendance_authorization(actor_id, group_public_id, session_public_id)
    flash(message, "warning")
    return redirect(_detail_url(group_public_id, session_public_id))


@teacher_bp.route(
    "/groups/<group_public_id>/attendance/<session_public_id>/mark",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def attendance_mark(group_public_id, session_public_id):
    """Mark a draft session's captured Students, and save as often as
    needed.

    **A draft save changes only ``status`` and ``note`` values.** The set
    of records is read from the database and the submitted field set is
    matched against it, never the other way round: a request naming an
    extra record, omitting one, or renaming one adds, removes, replaces
    and reorders nothing.

    **A no-op save is a no-op.** If every status and every normalised note
    is already what is stored, no version moves, no timestamp moves, and
    the transaction is rolled back -- re-saving unchanged marks is not an
    edit.

    **A meaningful save increments each changed record's ``version``
    exactly once and the session's ``version`` exactly once**, whatever
    the number of records changed, and stamps one authoritative post-lock
    whole-second moment on each of them.
    """
    group = _teacher_group_or_404(group_public_id)
    session = _session_or_404(group, session_public_id)

    if session.is_finalized():
        flash(_FINALIZED_MESSAGE, "warning")
        return redirect(_detail_url(group_public_id, session_public_id))
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, session_public_id))

    if request.method == "GET":
        return _render_marking_page(group, session)

    # --- ordinary validation, before any lock and before any write -----
    preview_records = build_record_view(session_records(session.id))
    submission = parse_marking_submission(request.form, preview_records)
    if not submission.ok:
        return _render_marking_page(group, session, submission.values, submission.errors)

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("attendance_state")
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, schedule_id, session_id = group.id, session.schedule_id, session.id
    student_ids = captured_student_ids(session_id)
    record_ids = captured_record_ids(session_id)

    locks = lock_session_chain(
        group_public_id,
        term_id,
        level_id,
        course_id,
        actor_id,
        schedule_id,
        session_id,
        student_ids=student_ids,
        record_ids=record_ids,
    )

    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)
    locked_session = locks.session
    if (
        locked_session is None
        or locked_session.group_id != group_id
        or locked_session.public_id != session_public_id
        or locked_session.schedule_id != schedule_id
    ):
        db.session.rollback()
        abort(404)

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_mark(group_public_id, session_public_id, block)
    if locked_session.is_finalized():
        return _redirect_finalized(group_public_id, session_public_id, _FINALIZED_MESSAGE)
    if _roster_broken(locks, session_id, record_ids):
        return _reject_mark(group_public_id, session_public_id, _ROSTER_BROKEN_MESSAGE)

    locked_records = list(locks.records.values())
    locked_state = _record_state(
        [{"public_id": row.public_id, "version": row.version} for row in locked_records]
    )
    if _token_is_stale(
        token,
        "attendance-draft",
        teacher_public_id=actor_public_id,
        group_public_id=group_public_id,
        session_public_id=session_public_id,
        session_version=locked_session.version,
        records=locked_state,
    ):
        return _reject_mark(group_public_id, session_public_id, _STALE_MESSAGE)

    # The submitted values were matched against a pre-lock read of the
    # roster; the locked rows are the authority, so the two must describe
    # the same set before anything is written.
    if {row.public_id for row in locked_records} != set(submission.values):
        return _reject_mark(group_public_id, session_public_id, _STALE_MESSAGE)

    moment = _write_moment()
    changed = 0
    for record in locked_records:
        status, note = submission.values[record.public_id]
        if record.status == status and record.note == note:
            continue
        record.status = status
        record.note = note
        record.version = record.version + 1
        record.updated_at = moment
        changed += 1

    if not changed:
        db.session.rollback()
        flash(_NO_CHANGES_MESSAGE, "info")
        return redirect(_mark_url(group_public_id, session_public_id))

    locked_session.version = locked_session.version + 1
    locked_session.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject_mark(group_public_id, session_public_id, _INTEGRITY_MESSAGE)

    flash(_SAVED_MESSAGE, "success")
    return redirect(_mark_url(group_public_id, session_public_id))


# ======================================================================
# Finalization -- one permanent freeze, and a safe replay
# ======================================================================


@teacher_bp.post("/groups/<group_public_id>/attendance/<session_public_id>/finalize")
@roles_required(UserRole.TEACHER.value)
def attendance_finalize(group_public_id, session_public_id):
    """Freeze one attendance session permanently.

    POST-only and CSRF-protected, behind an explicit confirmation the
    Teacher must tick. Requires the complete captured roster to still be
    intact, and the signed finalize token to still describe the session's
    current version -- so a co-teacher's save landing in between produces
    a "reload and review" rejection rather than a finalization of marks
    nobody read.

    Sets one authoritative whole-second UTC ``finalized_at``, increments
    ``version`` exactly once, and writes nothing else. **A replay is
    safe**: a second finalization of an already-finalized session changes
    no timestamp, no version, no status and no note, and simply returns
    its detail page.
    """
    group = _teacher_group_or_404(group_public_id)
    session = _session_or_404(group, session_public_id)

    if session.is_finalized():
        # The replay, answered before any lock is taken: there is nothing
        # to decide and nothing to write.
        flash(_ALREADY_FINALIZED_MESSAGE, "warning")
        return redirect(_detail_url(group_public_id, session_public_id))
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, session_public_id))
    if request.form.get("confirm_finalize") != "yes":
        flash(_FINALIZE_CONFIRM_MESSAGE, "danger")
        return redirect(_mark_url(group_public_id, session_public_id))

    actor_id, actor_public_id = current_user.id, current_user.public_id
    token = request.form.get("attendance_state")
    term_id, level_id, course_id = _hierarchy_context(group)
    group_id, schedule_id, session_id = group.id, session.schedule_id, session.id
    student_ids = captured_student_ids(session_id)
    record_ids = captured_record_ids(session_id)

    locks = lock_session_chain(
        group_public_id,
        term_id,
        level_id,
        course_id,
        actor_id,
        schedule_id,
        session_id,
        student_ids=student_ids,
        record_ids=record_ids,
    )

    if _authz_broken(locks.group, locks.teacher, locks.teacher_assignment):
        db.session.rollback()
        abort(404)
    locked_session = locks.session
    if (
        locked_session is None
        or locked_session.group_id != group_id
        or locked_session.public_id != session_public_id
        or locked_session.schedule_id != schedule_id
    ):
        db.session.rollback()
        abort(404)

    if locked_session.is_finalized():
        return _redirect_finalized(
            group_public_id, session_public_id, _ALREADY_FINALIZED_MESSAGE
        )

    block = _operational_block(locks, group_id, term_id, level_id, course_id)
    if block is not None:
        return _reject_mark(group_public_id, session_public_id, block)
    if _roster_broken(locks, session_id, record_ids):
        return _reject_mark(group_public_id, session_public_id, _ROSTER_BROKEN_MESSAGE)

    if _token_is_stale(
        token,
        "attendance-finalize",
        teacher_public_id=actor_public_id,
        group_public_id=group_public_id,
        session_public_id=session_public_id,
        session_version=locked_session.version,
    ):
        return _reject_mark(group_public_id, session_public_id, _STALE_MESSAGE)

    moment = _write_moment()
    locked_session.finalized_at = moment
    locked_session.version = locked_session.version + 1
    locked_session.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        return _reject_mark(group_public_id, session_public_id, _INTEGRITY_MESSAGE)

    flash(_FINALIZED_OK_MESSAGE, "success")
    return redirect(_detail_url(group_public_id, session_public_id))
