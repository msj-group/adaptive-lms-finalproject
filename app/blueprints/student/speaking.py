"""Student Speaking activities: opening one, recording an answer in the
browser, submitting it once, and reading the receipt and the Teacher's
feedback (Phase 4 / M06).

Routes, all Group- and activity-scoped by public identifier::

    GET   /student/speaking
    GET   /student/groups/<gp>/speaking/<sp>
    GET   /student/groups/<gp>/speaking/<sp>/record
    POST  /student/groups/<gp>/speaking/<sp>/submit
    GET   /student/groups/<gp>/speaking/<sp>/receipt
    GET   /student/groups/<gp>/speaking/<sp>/audio
    GET   /student/groups/<gp>/speaking/<sp>/audio/download

There is deliberately **one** POST endpoint and no update, delete,
resubmit, replace or draft endpoint: a recording is uploaded once and
never written again. Re-recording happens **entirely in the browser**,
before anything is sent, so a discarded take never reaches the server at
all -- no file, no ``uploaded_files`` row, no access-log entry and no
submission row.

**Authorization is SQL-scoped** to ``current_user.id`` in
``app/services/speaking_queries.py`` -- an activity is never loaded
broadly and then authorized. A Student receives a row only when the query
itself proves the whole effective-visibility formula: the account is that
Student with a valid role and active status, holds an **active**
``Enrollment`` for the Group, the AcademicTerm / Level / Course / Group
are all active, the backing Assignment is ``published``, its ``opens_at``
has been reached, and it carries the Speaking extension. Every failure --
a draft, a published activity that has not opened yet, a withdrawn or
missing Enrollment, an archived ancestor, another Group's activity, an
**ordinary Assignment's** public id, a mismatched nested pair, or a simply
non-existent id -- returns the identical non-disclosing **404**. The
submission endpoint and both audio routes re-prove that same formula
against **locked** or freshly read rows, so an existing recording never
buys access after a withdrawal, a suspension, an unpublish or an ancestor
archival; the stored row itself is left untouched.

**A past-due activity stays visible and readable** -- what the deadline
withdraws is the ability to hand in a first recording, never the record.
A Student who submitted in time keeps their receipt, their playback and
their feedback forever.

**The Student's own recording is looked up by BOTH the authorized
activity id and the authenticated ``student_id``** (see
``speaking_queries.student_speaking_submission``), which is exactly
``uq_speaking_submissions_activity_student``. Nothing here ever joins
``speaking_submissions`` by ``speaking_activity_id`` alone, which is how
one Student's recording would otherwise reach another's screen.

**The upload never touches a database lock.** The bytes are streamed,
size-checked, extension-checked, declared-MIME-checked and
signature-checked to private storage *before* the lock chain is taken --
holding a write lock while streaming a recording is exactly what Part M12
forbids. Everything the locked re-check needs is then proved against the
locked rows, and every non-success exit before a confirmed commit deletes
the file this request wrote.

All responses carry ``Cache-Control: private, no-store`` and
``Vary: Cookie``: these pages are per-Student and time-gated, so a shared
or reused cache entry could show one Student another's work, or show a
recording page for an activity that has since closed.
"""

import secrets

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.blueprints.student.speaking_forms import SpeakingSubmissionForm
from app.blueprints.teacher.materials import _cleanup_orphan_upload
from app.extensions import db
from app.models import (
    AcademicStatus,
    Assignment,
    AssignmentStatus,
    Enrollment,
    EnrollmentStatus,
    FileAccessAction,
    FileAccessLog,
    SpeakingActivity,
    SpeakingSubmission,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.assignment_queries import (
    PAGE_SIZE,
    STATE_OPEN,
    normalize_page,
)
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.material_config import current_material_config
from app.services.material_serving import serve_uploaded_file
from app.services.schedule_occurrences import utc_reference_now
from app.services.speaking_audio import (
    SPEAKING_AUDIO_ACCEPT,
    SPEAKING_AUDIO_EXTENSIONS,
    SPEAKING_FORMATS_LABEL,
    UNSUPPORTED_AUDIO_MESSAGE,
    candidate_extension,
    store_speaking_audio,
)
from app.services.file_validation import FileValidationError
from app.services.speaking_queries import (
    audio_upload_for_submission,
    build_student_receipt,
    build_student_speaking_item,
    build_student_speaking_view,
    student_speaking,
    student_speaking_feedback,
    student_speaking_page,
    student_speaking_submission,
    student_submitted_activity_ids,
)
from app.services.submission_feedback_queries import build_feedback_panel

_ACTIVE = AcademicStatus.ACTIVE.value
_AUDIO_CATEGORY = "audio"
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_PUBLISHED = AssignmentStatus.PUBLISHED.value


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _now():
    """The reference moment for a read. One per request, injected into
    every comparison, so a page can never straddle a deadline and
    contradict itself."""
    return utc_reference_now()


def _acceptance_moment():
    """The **authoritative** naive-UTC moment for one submission POST,
    truncated to whole seconds.

    ``speaking_submissions.submitted_at`` is a plain ``DateTime``, which
    on MySQL is ``DATETIME`` with fractional precision **0**. MySQL does
    not truncate an excess fraction -- it *rounds* it -- so an acceptance
    decided at ``11:59:59.900000`` against a ``12:00:00`` deadline would
    be compared as "in time" in Python and then persisted as
    ``12:00:00``: a receipt claiming the exact moment this project defines
    as past due. Truncating here makes the value the decision used and the
    value the database stores the same instant, on every backend.

    Truncation floors, never rounds, so it can only ever make the request
    *earlier* -- it fails closed at ``opens_at`` and stays honest at
    ``due_at``. Assignment ``opens_at`` / ``due_at`` already carry no
    microseconds (``AssignmentForm`` parses to second precision), so both
    comparisons happen entirely in whole seconds.

    Read only **after** every lock that could have blocked, so a request
    that arrived in time but waited behind another transaction until after
    the deadline is rejected.
    """
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# URLs
# ======================================================================


def _list_url():
    return url_for("student.speaking_list")


def _detail_url(group_public_id, speaking_public_id):
    return url_for(
        "student.speaking_detail",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
    )


def _record_url(group_public_id, speaking_public_id):
    return url_for(
        "student.speaking_record",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
    )


def _receipt_url(group_public_id, speaking_public_id):
    return url_for(
        "student.speaking_receipt",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
    )


def _submit_url(group_public_id, speaking_public_id):
    return url_for(
        "student.speaking_submit",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
    )


def _audio_urls(group_public_id, speaking_public_id):
    return {
        "src": url_for(
            "student.speaking_audio",
            group_public_id=group_public_id,
            speaking_public_id=speaking_public_id,
        ),
        "download": url_for(
            "student.speaking_audio_download",
            group_public_id=group_public_id,
            speaking_public_id=speaking_public_id,
        ),
    }


# ======================================================================
# The signed submission token (Phase 4 / M06)
# ======================================================================
#
# One dedicated M06 salt and one exact purpose marker. A token minted
# under any other salt -- the M02 submission context, the M03 feedback
# state, every M04/M05 token, every Teacher-side M06 token -- fails
# signature verification here even though all of them are signed with the
# same application SECRET_KEY.
#
# The payload carries **public identifiers, a server-generated nonce and
# the exact task the Student read**. A signed token is authenticated, not
# encrypted: anyone holding it can read its payload, so no recording
# bytes, no filename, no MIME type, no storage key, no digest and no
# internal database id is ever placed in one.

_SUBMISSION_SALT = "student.speaking-submission.phase4-m06.v1"
_SUBMISSION_PURPOSE = "speaking-submission"
_SUBMISSION_FIELDS = (
    "purpose",
    "student_public_id",
    "group_public_id",
    "speaking_public_id",
    "title",
    "instructions",
    "opens_at",
    "due_at",
    "nonce",
)

#: Canonical, deterministic serialization for the two datetime fields --
#: a fixed second-precision ISO string, so a value that survives a JSON
#: round trip inside the signed token compares byte-for-byte against a
#: freshly rendered one and can never look "changed" merely because it was
#: formatted differently. Same convention as the M02 submission context.
_TOKEN_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_SUBMISSION_SALT)


def _token_payload(student_public_id, group_public_id, assignment, activity, nonce):
    """The exact context a Student's open recording page was written
    against.

    Row locks alone cannot protect this page: the Student may have opened
    it minutes before a Teacher withdrew the activity, rewrote the task
    and republished it, and the lock the POST takes would happily accept a
    recording of a question that no longer exists. Binding the submission
    to the wording and the window the Student actually read is what closes
    that.

    ``status`` and ``published_at`` are deliberately excluded: current
    publication and visibility are authoritatively re-checked against the
    locked rows before acceptance regardless, so binding them would only
    invalidate forms without adding a guarantee.

    ``nonce`` is a fresh random value per rendered page. It becomes
    ``speaking_submissions.creation_nonce``, which is UNIQUE, so a
    replayed POST resolves to the one existing receipt instead of a second
    recording.
    """
    return {
        "purpose": _SUBMISSION_PURPOSE,
        "student_public_id": student_public_id,
        "group_public_id": group_public_id,
        "speaking_public_id": activity.public_id,
        "title": assignment.title,
        "instructions": assignment.instructions,
        "opens_at": assignment.opens_at.strftime(_TOKEN_DATETIME_FORMAT),
        "due_at": assignment.due_at.strftime(_TOKEN_DATETIME_FORMAT),
        "nonce": nonce,
    }


def _make_token(student_public_id, group_public_id, assignment, activity, nonce=None):
    return _serializer().dumps(
        _token_payload(
            student_public_id,
            group_public_id,
            assignment,
            activity,
            nonce or secrets.token_hex(32),
        )
    )


def _load_token(token):
    """The token's payload, or ``None`` for a missing, malformed,
    invalidly signed, wrong-purpose or wrong-shaped one.

    The shape check is exact and typed, not merely "is a dict": the key
    set must match exactly, the purpose must be this one, and every value
    must be a string. Every ``None`` is treated exactly like an outdated
    token -- rejected, never trusted.
    """
    if not token:
        return None
    try:
        payload = _serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_SUBMISSION_FIELDS):
        return None
    if payload["purpose"] != _SUBMISSION_PURPOSE:
        return None
    if any(not isinstance(payload[field], str) for field in _SUBMISSION_FIELDS):
        return None
    return payload


def _token_is_stale(
    payload, student_public_id, group_public_id, speaking_public_id, assignment, activity
):
    """True when `payload` does not exactly describe the **locked**
    activity as read by **this** Student under **this** Group.

    A cross-Student, cross-Group or cross-activity token fails on the
    three identifier fields; a Teacher edit to any bound field fails on
    the content comparison. `nonce` is excluded from the comparison
    because it is this request's own value, not persisted state.
    """
    if payload is None:
        return True
    if payload["student_public_id"] != student_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["speaking_public_id"] != speaking_public_id:
        return True
    expected = _token_payload(
        student_public_id, group_public_id, assignment, activity, payload["nonce"]
    )
    return payload != expected


# ======================================================================
# Shared reads
# ======================================================================


def _visible_or_404(group_public_id, speaking_public_id, reference_utc):
    row = student_speaking(
        current_user.id, group_public_id, speaking_public_id, reference_utc
    )
    if row is None:
        abort(404)
    return row


def _feedback_panel_for(submission, tz_name):
    """The Teacher's latest feedback on this Student's own recording, or
    ``None`` when nothing has been submitted yet.

    Fetched **only** after the whole visibility formula has produced this
    Student's own submission, and **only** for that exact submission id --
    so it rides entirely on the receipt's authorization and can never be
    reached through a classmate's row. One fixed, bounded lookup by the
    ``uq_speaking_feedback_submission`` unique key.
    """
    if submission is None:
        return None
    return build_feedback_panel(student_speaking_feedback(submission.id), tz_name)


# ======================================================================
# Reads
# ======================================================================


@student_bp.get("/speaking")
@roles_required(UserRole.STUDENT.value)
def speaking_list():
    """Every Speaking activity this Student can currently see, in fixed
    pages: **open work by nearest deadline, then past-due work most recent
    first**.

    That bucketing is the M01 ``student_list_order``, reused rather than
    restated, so a Student meets one ordering rule for time-gated work
    rather than two. One reference moment is derived here and passed into
    the query and the presentation builder, so the visibility gate, the
    bucketing, the ordering and the Open / Past due labels on this page
    all agree.

    **One** extra bounded query resolves which of the at-most-PAGE_SIZE
    rows this Student has already submitted -- scoped by ``student_id``,
    so it discloses nothing about anybody else, and asked once for the
    whole page rather than per row.
    """
    tz_name = _tz_name()
    reference_utc = _now()
    page = normalize_page(request.args.get("page"))

    rows, has_next = student_speaking_page(current_user.id, reference_utc, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = student_speaking_page(current_user.id, reference_utc, page)

    submitted_ids = student_submitted_activity_ids(
        [row[5].id for row in rows], current_user.id
    )

    return private_no_store(
        "student/speaking/list.html",
        activities=build_student_speaking_view(
            rows, tz_name, reference_utc, submitted_ids
        ),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@student_bp.get("/groups/<group_public_id>/speaking/<speaking_public_id>")
@roles_required(UserRole.STUDENT.value)
def speaking_detail(group_public_id, speaking_public_id):
    """One Speaking activity, or a non-disclosing 404.

    Both public ids are supplied to the same fully scoped query, so a
    valid activity id paired with the wrong Group id -- and every other
    unauthorized combination -- produces no row and the same 404.

    The page shows exactly one of three states: a link to the recording
    page (visible, open, nothing submitted), a link to this Student's own
    immutable receipt (already submitted), or a "Not submitted" notice
    (visible, past due, nothing submitted). A GET never creates or changes
    anything.
    """
    tz_name = _tz_name()
    reference_utc = _now()
    row = _visible_or_404(group_public_id, speaking_public_id, reference_utc)
    activity = row[5]
    submission = student_speaking_submission(activity.id, current_user.id)
    item = build_student_speaking_item(row, tz_name, reference_utc)

    return private_no_store(
        "student/speaking/detail.html",
        activity=item,
        tz_name=tz_name,
        submission=build_student_receipt(submission, tz_name),
        can_record=submission is None and item["state"] == STATE_OPEN,
        record_url=_record_url(group_public_id, speaking_public_id),
        receipt_url=_receipt_url(group_public_id, speaking_public_id),
        list_url=_list_url(),
    )


@student_bp.get("/groups/<group_public_id>/speaking/<speaking_public_id>/record")
@roles_required(UserRole.STUDENT.value)
def speaking_record(group_public_id, speaking_public_id):
    """The browser recording page: microphone guidance, the recorder, a
    local preview, "record again", and the one final submit control.

    Reachable **only** while the activity is open and nothing has been
    submitted. An already-submitted Student is sent to their receipt; a
    past-due activity is sent back to the detail page. Both are redirects
    with an explanation, never a silent empty form -- and both rules are
    re-proved authoritatively in the POST, so this page decides
    presentation and never authorization.

    The signed submission token is minted here, from **freshly read
    persisted state**, and travels as a hidden input. A GET writes
    nothing.
    """
    tz_name = _tz_name()
    reference_utc = _now()
    row = _visible_or_404(group_public_id, speaking_public_id, reference_utc)
    assignment, activity = row[0], row[5]
    submission = student_speaking_submission(activity.id, current_user.id)
    if submission is not None:
        flash(
            "You have already submitted a recording for this speaking activity. It is final "
            "and cannot be changed or replaced.",
            "info",
        )
        return redirect(_receipt_url(group_public_id, speaking_public_id))

    item = build_student_speaking_item(row, tz_name, reference_utc)
    if item["state"] != STATE_OPEN:
        flash(
            "The deadline for this speaking activity has passed, so a recording can no longer "
            "be submitted.",
            "danger",
        )
        return redirect(_detail_url(group_public_id, speaking_public_id))

    return _render_record_page(
        group_public_id, speaking_public_id, item,
        SpeakingSubmissionForm(formdata=None),
        _make_token(
            current_user.public_id, group_public_id, assignment, activity
        ),
    )


def _render_record_page(group_public_id, speaking_public_id, item, form, token):
    """Render the recording page with a form and a token the caller
    supplies.

    The caller decides which token: a fresh one on a GET (paired with
    freshly loaded persisted values) or the **original** submitted one on
    a validation failure. A freshly minted token may never be paired with
    attempted values -- that is precisely the bypass the stale rejection
    exists to close.
    """
    return private_no_store(
        "student/speaking/record.html",
        activity=item,
        form=form,
        submission_token=token,
        submit_url=_submit_url(group_public_id, speaking_public_id),
        detail_url=_detail_url(group_public_id, speaking_public_id),
        audio_accept=SPEAKING_AUDIO_ACCEPT,
        supported_formats=SPEAKING_FORMATS_LABEL,
        tz_name=_tz_name(),
    )


@student_bp.get("/groups/<group_public_id>/speaking/<speaking_public_id>/receipt")
@roles_required(UserRole.STUDENT.value)
def speaking_receipt(group_public_id, speaking_public_id):
    """This Student's own immutable receipt: when it was submitted, an
    authorized player for their own recording, and the Teacher's latest
    feedback.

    A Student with no submission is sent back to the detail page rather
    than shown an empty receipt. The player's ``src`` is this module's own
    authorized audio route, and the association is re-proved here so the
    page never offers a player that could only 404.

    There is deliberately no edit, delete, replace or resubmit control
    anywhere on this page -- none exists server-side either.
    """
    tz_name = _tz_name()
    reference_utc = _now()
    row = _visible_or_404(group_public_id, speaking_public_id, reference_utc)
    activity = row[5]
    submission = student_speaking_submission(activity.id, current_user.id)
    if submission is None:
        return redirect(_detail_url(group_public_id, speaking_public_id))
    if audio_upload_for_submission(submission) is None:
        abort(404)

    item = build_student_speaking_item(row, tz_name, reference_utc)
    urls = _audio_urls(group_public_id, speaking_public_id)
    return private_no_store(
        "student/speaking/receipt.html",
        activity=item,
        submission=build_student_receipt(submission, tz_name),
        feedback=_feedback_panel_for(submission, tz_name),
        player={"src": urls["src"], "label": item["title"]},
        download_url=urls["download"],
        detail_url=_detail_url(group_public_id, speaking_public_id),
        list_url=_list_url(),
        tz_name=tz_name,
    )


# ======================================================================
# Submission -- one POST, one INSERT, never an update
# ======================================================================


def _lock_submission_chain(
    group_public_id, term_id, level_id, course_id, student_id, assignment_id, activity_id
):
    """Acquire the route-specific Phase 4 / M06 lock order in one open
    transaction::

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group -> acting Student User -> Enrollment
        -> Assignment -> SpeakingActivity
        -> the existing SpeakingSubmission for this activity + Student,
           if any

    This is the M02 submission chain with the extension row inserted after
    its Assignment -- the shared hierarchy/Group prefix every
    Group-affecting mutation in the project uses, extended with the rows
    this workflow actually decides on. Because the Teacher authoring,
    publication and feedback routes lock the **same** Group, the **same**
    Assignment row and the **same** extension row, and every membership
    and Group lifecycle mutation locks the same Group, a first submission
    serializes against all of them rather than racing.

    Returns ``(hierarchy, group, student, enrollment, assignment, activity,
    submission)``. Any of them may be ``None`` -- the caller must treat
    that as a business/authorization rejection, roll back, and 404 or
    redirect; it must never "keep going".

    SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
    REPEATABLE READ snapshot isolation, so tests can assert the
    *requested* reset and lock order and nothing about real InnoDB
    blocking.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    student = User.query.filter_by(id=student_id).with_for_update().first()
    enrollment = None
    if group is not None:
        enrollment = (
            Enrollment.query.filter_by(group_id=group.id, student_id=student_id)
            .with_for_update()
            .first()
        )
    assignment = Assignment.query.filter_by(id=assignment_id).with_for_update().first()
    activity = SpeakingActivity.query.filter_by(id=activity_id).with_for_update().first()
    submission = (
        SpeakingSubmission.query.filter_by(
            speaking_activity_id=activity_id, student_id=student_id
        )
        .with_for_update()
        .first()
    )
    return hierarchy, group, student, enrollment, assignment, activity, submission


def _locked_visibility_broken(
    hierarchy, group, student, enrollment, assignment, activity,
    term_id, level_id, course_id, speaking_public_id, reference_utc,
):
    """True when the **locked** rows no longer satisfy the effective
    visibility formula for this Student and this Speaking activity.

    Every clause of that formula is re-proved here against current-read
    locked rows rather than trusted from the pre-lock preview: the exact
    hierarchy identities and linkages, every ancestor and Group status,
    the Student's role and account status, the exact Enrollment ownership
    and its active status, the Assignment's existence and nested Group
    ownership, the extension row's existence and its exact ownership of
    that Assignment and its own public id, and both publication and
    opening visibility.

    The caller turns a ``True`` into the same non-disclosing 404 the read
    routes produce, with no partial write of any kind.
    """
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    if term is None or level is None or course is None:
        return True
    if group is None or student is None or enrollment is None:
        return True
    if assignment is None or activity is None:
        return True

    # The hierarchy the preview walked must still be the hierarchy this
    # Group belongs to -- an Administrator retarget takes the same Group
    # lock, so a completed retarget is visible here.
    if (
        group.academic_term_id != term_id
        or group.course_id != course.id
        or course.level_id != level_id
    ):
        return True
    if any(row.status != _ACTIVE for row in (term, level, course, group)):
        return True

    # A foreign key into `users` proves the row exists, never that it is
    # still a Student or still active.
    if student.role != UserRole.STUDENT.value or student.status != UserStatus.ACTIVE.value:
        return True
    if enrollment.student_id != student.id or enrollment.group_id != group.id:
        return True
    if enrollment.status != _ENROLLMENT_ACTIVE:
        return True

    if assignment.group_id != group.id:
        return True
    if (
        activity.assignment_id != assignment.id
        or activity.public_id != speaking_public_id
    ):
        return True
    if assignment.status != _PUBLISHED or assignment.opens_at > reference_utc:
        return True
    return False


@student_bp.post("/groups/<group_public_id>/speaking/<speaking_public_id>/submit")
@roles_required(UserRole.STUDENT.value)
def speaking_submit(group_public_id, speaking_public_id):
    """Accept this Student's one final recording for this activity.

    **Nothing about ownership or storage is taken from the request.** The
    Student is the authenticated session, the activity and Group are the
    authorized nested public identifiers, ``submitted_at`` is generated on
    the server, ``public_id`` is generated by the model, the
    ``creation_nonce`` comes from the signed token this server minted, and
    the stored extension, category, content type, byte size, SHA-256 and
    random storage key are all determined by the validation pipeline
    rather than by anything the browser sent. A forged ``student_id`` /
    ``speaking_activity_id`` / ``audio_file_id`` / ``submitted_at`` /
    ``storage_key`` field has nowhere to land -- the form carries only the
    audio part.

    **Acceptance window.** A FIRST submission is accepted only when
    ``opens_at <= authoritative_now < due_at``. At exactly ``due_at`` the
    deadline has passed. The authoritative moment is read **after** every
    potentially blocking lock, so a request that arrived in time but
    waited behind another transaction until after the deadline is
    rejected. The same moment is used for the acceptance decision and
    persisted as ``submitted_at``, so a receipt can never claim a time the
    decision did not use.

    **Order of the checks.** An already-submitted Student short-circuits
    to their receipt *before anything is streamed to disk at all* -- a
    duplicate is an authorized no-op and needs no file, no deadline check,
    no token check and no validation, which is what makes a double click,
    a replay, a changed payload and a late retry all safe and all
    file-free. Only a FIRST insertion streams and validates a recording.
    After the locks, authorization and visibility come first, then the
    duplicate re-check, then the stale-token check, then the deadline.

    **The bytes never touch a lock**, and every non-success exit before a
    confirmed commit deletes the file this request wrote -- including the
    losing side of a genuinely concurrent race, whose file is
    unreferenced. The winner's recording is never deleted.
    """
    detail_url = _detail_url(group_public_id, speaking_public_id)
    receipt_url = _receipt_url(group_public_id, speaking_public_id)
    record_url = _record_url(group_public_id, speaking_public_id)

    # ------------------------------------------------------------------
    # Pre-lock preview: the same SQL-scoped authorization the GET uses.
    # Friendly and fast, but NOT authoritative -- everything it proves is
    # proved again below against locked rows.
    # ------------------------------------------------------------------
    preview_reference = _now()
    preview = student_speaking(
        current_user.id, group_public_id, speaking_public_id, preview_reference
    )
    if preview is None:
        abort(404)
    (
        preview_assignment, _preview_group, preview_course, preview_level, preview_term,
        preview_activity,
    ) = preview

    student_id = current_user.id
    student_public_id = current_user.public_id

    # THE replay short-circuit, taken before a single byte is streamed:
    # an already-submitted Student gets their existing receipt, and this
    # request writes no file, no UploadedFile, no access-log row and no
    # submission. It is re-proved under the locks below as well.
    if student_speaking_submission(preview_activity.id, student_id) is not None:
        flash(
            "You have already submitted a recording for this speaking activity. It is final "
            "and cannot be changed or replaced.",
            "info",
        )
        return redirect(receipt_url)

    submitted_token = request.form.get("speaking_submission", "")
    payload = _load_token(submitted_token)
    if (
        payload is None
        or payload["student_public_id"] != student_public_id
        or payload["group_public_id"] != group_public_id
        or payload["speaking_public_id"] != speaking_public_id
    ):
        flash(
            "This page could not be verified (it may be old or was opened in another tab). "
            "Please open the recording page again.",
            "danger",
        )
        return redirect(detail_url)
    nonce = payload["nonce"]

    tz_name = _tz_name()
    item = build_student_speaking_item(preview, tz_name, preview_reference)

    form = SpeakingSubmissionForm()
    if not form.validate_on_submit():
        return _render_record_page(
            group_public_id, speaking_public_id, item, form, submitted_token
        )

    # A cheap, safe pre-check on the claimed extension so a document, an
    # image or a video is refused *before* its bytes are streamed to disk
    # at all. It is not the authority -- `store_speaking_audio` re-derives
    # the extension, checks the declared MIME and checks the binary
    # signature, and the stored category is re-asserted below -- but there
    # is no reason to write a file that can only be deleted again.
    upload = form.audio.data
    if candidate_extension(getattr(upload, "filename", "") or "") not in SPEAKING_AUDIO_EXTENSIONS:
        form.audio.errors.append(UNSUPPORTED_AUDIO_MESSAGE)
        return _render_record_page(
            group_public_id, speaking_public_id, item, form, submitted_token
        )

    material_config = current_material_config()
    try:
        stored = store_speaking_audio(material_config, upload, upload.filename)
    except FileValidationError as exc:
        # Student-caused: this message is deliberately safe -- no path, no
        # SQL, no exception internals.
        form.audio.errors.append(str(exc))
        return _render_record_page(
            group_public_id, speaking_public_id, item, form, submitted_token
        )
    # Any other exception from store_speaking_audio (containment, OSError,
    # ...) propagates: it has already deleted its own temp/final files,
    # Flask logs it server-side, and the generic 500 handler responds
    # without leaking details.

    # From here a final file exists on disk. Every non-success exit before
    # a confirmed commit must delete it; `committed` gates that so a
    # successfully-saved recording is never removed.
    committed = False
    try:
        if stored.category != _AUDIO_CATEGORY:  # pragma: no cover -- defense in depth
            # Unreachable while `store_speaking_audio` forces the category.
            # Refused rather than trusted, because the `audio` category is
            # an invariant of the submission row.
            form.audio.errors.append(UNSUPPORTED_AUDIO_MESSAGE)
            return _render_record_page(
                group_public_id, speaking_public_id, item, form, submitted_token
            )

        assignment_id = preview_assignment.id
        activity_id = preview_activity.id
        term_id = preview_term.id
        level_id = preview_level.id
        course_id = preview_course.id

        (
            hierarchy, group, student, enrollment, assignment, activity, submission,
        ) = _lock_submission_chain(
            group_public_id, term_id, level_id, course_id, student_id,
            assignment_id, activity_id,
        )

        # The authoritative acceptance moment: read only now, after every
        # lock that could have blocked, and truncated to the canonical
        # whole second the column can actually hold. This ONE value backs
        # every check below and is the value persisted as `submitted_at`.
        now_utc = _acceptance_moment()

        if _locked_visibility_broken(
            hierarchy, group, student, enrollment, assignment, activity,
            term_id, level_id, course_id, speaking_public_id, now_utc,
        ):
            db.session.rollback()
            abort(404)

        # Already submitted -- an authorized no-op, never a rewrite. This
        # is the authoritative version of the pre-stream check above and
        # covers a submission that committed while this one was streaming.
        if submission is not None:
            db.session.rollback()
            flash(
                "You have already submitted a recording for this speaking activity. It is "
                "final and cannot be changed or replaced.",
                "info",
            )
            return redirect(receipt_url)  # loser -- `finally` cleans its own file

        # FIRST-insertion validation only, against the LOCKED rows. The
        # stale check runs before the deadline check so that a Teacher
        # rewrite is always reported as a changed activity rather than as
        # whatever the rewrite did to the window.
        if _token_is_stale(
            payload, student_public_id, group_public_id, speaking_public_id,
            assignment, activity,
        ):
            db.session.rollback()
            flash(
                "This speaking activity was changed since this page was opened. Please read "
                "the current version and record your answer again.",
                "danger",
            )
            return redirect(detail_url)

        if now_utc >= assignment.due_at:
            db.session.rollback()
            flash(
                "The deadline for this speaking activity has passed, so your recording was "
                "not submitted.",
                "danger",
            )
            return redirect(detail_url)

        uploaded_file = UploadedFile(
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            extension=stored.extension,
            category=stored.category,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            uploaded_by_id=student_id,
        )
        # `SpeakingSubmission` declares no ORM relationship in either
        # direction (see the model), so the upload row is flushed inside
        # this same open transaction to obtain its id. A flush is not a
        # commit: all three rows still land -- or fail -- together.
        db.session.add(uploaded_file)
        db.session.flush()
        new_submission = SpeakingSubmission(
            speaking_activity_id=activity.id,
            student_id=student.id,
            audio_file_id=uploaded_file.id,
            creation_nonce=nonce,
            submitted_at=now_utc,
        )
        upload_log = FileAccessLog(
            uploaded_file=uploaded_file,
            actor_id=student_id,
            action=FileAccessAction.UPLOAD.value,
        )
        db.session.add_all([new_submission, upload_log])
        try:
            db.session.commit()
        except IntegrityError:
            # 1. Roll back FIRST. Everything read before this point is now
            #    discarded state and must not be used as evidence of
            #    anything -- least of all authorization.
            db.session.rollback()

            # 2. Re-establish current authorization from scratch, with a
            #    fresh reference moment and the ORIGINAL nested public
            #    identifiers. Whatever raised the IntegrityError may well
            #    have been a concurrent change that ALSO ended this
            #    Student's access -- a withdrawal, an unpublish, an
            #    ancestor archival, a suspension. Reporting "already
            #    submitted" in that case would disclose both that the
            #    activity exists and that a recording exists for it.
            recovered = student_speaking(
                student_id, group_public_id, speaking_public_id, _acceptance_moment()
            )
            if recovered is None:
                abort(404)

            # 3. Only now may an existing row speak. A concurrent first
            #    submission for the same pair is the one IntegrityError
            #    this route can turn into a receipt -- and only after
            #    PROVING, against the freshly authorized activity, that a
            #    row for BOTH this activity and this Student exists.
            #    Anything else stays a generic failure: no SQL, driver
            #    text, parameter or internal id ever reaches the Student.
            #    Nothing is written, overwritten or deleted on this path,
            #    and `finally` removes only THIS request's own file.
            if student_speaking_submission(recovered[5].id, student_id) is not None:
                flash(
                    "You have already submitted a recording for this speaking activity. It "
                    "is final and cannot be changed or replaced.",
                    "info",
                )
                return redirect(receipt_url)
            flash(
                "Your recording could not be submitted. Please reload the page and try "
                "again.",
                "danger",
            )
            return redirect(record_url)
        committed = True
    except HTTPException:
        # An intentional abort() (e.g. the non-disclosing 404 from the
        # post-lock visibility re-check). Not an error to log -- but the
        # file has no submission, so `finally` cleans it.
        db.session.rollback()
        raise
    except Exception:
        # Unexpected (lock/DB failure, programming error). Roll back, log
        # server-side, and let the generic 500 handler respond -- never
        # surface the exception text; `finally` cleans the file.
        db.session.rollback()
        current_app.logger.exception(
            "Unexpected error while storing a Speaking submission"
        )
        raise
    finally:
        if not committed:
            _cleanup_orphan_upload(material_config, stored.storage_key)

    flash(
        "Your recording was submitted. It is final and cannot be edited, replaced, or "
        "submitted again.",
        "success",
    )
    return redirect(receipt_url)


# ======================================================================
# Authorized playback of the Student's OWN recording
# ======================================================================


def _serve_own_audio(group_public_id, speaking_public_id, force_attachment):
    """Re-authorize, re-validate, then serve.

    **Every audio request is re-authorized from current state**, never
    trusted because a page once rendered a ``<source>``: the whole
    visibility formula is re-evaluated in SQL (this Student, active
    account and role, active Enrollment, active academic chain, published
    activity, opening moment reached), the activity's ownership by the
    Group in the URL is proved by the same query, the recording is looked
    up by **both** that activity and the authenticated ``student_id``, and
    the ``uploaded_files`` row must still exist with the server-determined
    category ``audio``. Any break yields the identical non-disclosing 404
    -- a classmate's recording, another Group's activity, a withdrawn
    Enrollment and an ordinary Assignment's public id are
    indistinguishable from a nonexistent id.

    The bytes go through the shared M12 serving core, which resolves a
    containment-checked path from the random storage key, writes the
    ``inline`` / ``download`` ``FileAccessLog`` entry **before** any byte
    is sent and refuses to serve at all if that audit row cannot be
    committed, sends the stored canonical **audio** content type with
    ``nosniff``, and sets ``Cache-Control: private, no-store, max-age=0``.
    A missing physical file 404s without exposing the resolved path.

    Nothing in the response discloses the storage key, the resolved path,
    the digest, the uploader or any internal id.
    """
    row = _visible_or_404(group_public_id, speaking_public_id, _now())
    submission = student_speaking_submission(row[5].id, current_user.id)
    if submission is None:
        abort(404)
    uploaded_file = audio_upload_for_submission(submission)
    if uploaded_file is None:
        abort(404)
    return serve_uploaded_file(uploaded_file, current_user.id, force_attachment)


@student_bp.get("/groups/<group_public_id>/speaking/<speaking_public_id>/audio")
@roles_required(UserRole.STUDENT.value)
def speaking_audio(group_public_id, speaking_public_id):
    return _serve_own_audio(group_public_id, speaking_public_id, force_attachment=False)


@student_bp.get(
    "/groups/<group_public_id>/speaking/<speaking_public_id>/audio/download"
)
@roles_required(UserRole.STUDENT.value)
def speaking_audio_download(group_public_id, speaking_public_id):
    return _serve_own_audio(group_public_id, speaking_public_id, force_attachment=True)
