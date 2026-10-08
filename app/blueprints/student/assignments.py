"""Student Assignment reading and the single final teacher-configured
text or private-file submission (Version A D02).

Two GET-only reads -- ``/student/assignments`` and
``/student/groups/<group_public_id>/assignments/<assignment_public_id>``
-- plus **one** POST endpoint nested beneath the detail page,
``.../assignments/<assignment_public_id>/submit``. There is deliberately
no update, delete, resubmit or draft endpoint: a Submission is created
once and never written again.

**Authorization is SQL-scoped** to ``current_user.id`` in
``app/services/assignment_queries.py`` -- an Assignment is never loaded
broadly and then authorized. A Student receives a row only when the query
itself proves the whole effective-visibility formula: the account is that
Student with a valid role and active status, holds an **active**
``Enrollment`` for the Group, the AcademicTerm / Level / Course / Group
are all active, the Assignment is ``published``, and its ``opens_at`` has
been reached. Every failure -- a draft, a published Assignment that has
not opened yet, a withdrawn or missing Enrollment, an archived ancestor,
another Group's Assignment public id, a mismatched nested pair, or a
simply non-existent id -- returns the identical non-disclosing **404**.
The submission endpoint re-proves that same formula against **locked**
rows, so an existing submission never buys access after a withdrawal, a
suspension, an unpublish or an ancestor archival; the stored row itself
is left untouched.

Visibility deliberately does **not** depend on a Schedule existing, nor
on any current Teacher assignment: those answer different questions.
A **past-due** Assignment stays visible and readable -- what the deadline
withdraws is the ability to submit, never the record.

**The Student's own submission is looked up by BOTH the authorized
``assignment_id`` and the authenticated ``student_id``** (see
``app/services/submission_queries.student_submission``). ``submissions``
is never joined by ``assignment_id`` alone on a Student page.

**Teacher feedback (Phase 4 / M03) is an extension of that receipt, not a
route of its own.** It is read only after the full visibility formula has
already yielded this Student's own Submission, and only for that exact
Submission id, so withdrawal, suspension, unpublishing or ancestor
archival hide it exactly as they hide the receipt -- without deleting
either. It stays readable after ``due_at``. A reviewing Teacher's later
suspension or removal from the Group is deliberately **not** a condition:
that would erase valid history for an irrelevant reason. What *is* still
enforced is conditional role integrity on the stored ``reviewer_id`` --
a row naming a non-Teacher fails closed and is reported as an integrity
problem, never rendered and never shown as "no feedback yet". There is no
Student write path: no reply, no comment, no edit.

``roles_required(STUDENT)`` gives the role guard (anonymous -> login, any
other role -> 403); a suspended Student cannot hold a session at all (the
Flask-Login ``user_loader`` rejects it), and the queries re-prove role and
active status regardless. CSRF is enforced globally by ``CSRFProtect``,
so a POST without a valid token never reaches the view at all.

All three responses carry ``Cache-Control: private, no-store`` and
``Vary: Cookie`` -- including the form-error re-render and the receipt --
because these pages are per-Student and time-gated, so a shared or reused
cache entry could show one Student another's answer, or show an
Assignment after it stopped being visible.
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError

from app.blueprints.collector.hooks import note_outcome
from app.blueprints.student import student_bp
from app.blueprints.student.forms import FileSubmissionForm, SubmissionForm
from app.blueprints.student.routes import private_no_store, private_redirect
from app.extensions import db
from app.models import (
    AcademicStatus,
    Assignment,
    AssignmentStatus,
    Enrollment,
    EnrollmentStatus,
    Submission,
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
    build_student_item,
    build_student_view,
    normalize_page,
    student_assignment_detail,
    student_assignments_page,
)
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.assignment_files import submission_uploaded_file, upload_policy_view
from app.services.file_storage import delete_stored_file, store_validated_upload
from app.services.file_validation import FileValidationError
from app.services.material_config import current_material_config
from app.services.material_serving import serve_uploaded_file
from app.services.submission_feedback_queries import build_feedback_panel, student_feedback
from app.services.submission_queries import build_student_receipt, student_submission
from app.services.schedule_occurrences import utc_reference_now

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_PUBLISHED = AssignmentStatus.PUBLISHED.value


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _detail_url(group_public_id, assignment_public_id):
    return url_for(
        "student.assignment_detail",
        group_public_id=group_public_id,
        assignment_public_id=assignment_public_id,
    )


def _acceptance_moment():
    """The **authoritative** naive-UTC moment for one submission POST,
    truncated to whole seconds.

    ``submissions.submitted_at`` is a plain ``DateTime``, which on MySQL
    is ``DATETIME`` with fractional precision **0**. MySQL does not
    truncate an excess fraction -- it *rounds* it -- so an acceptance
    decided at ``11:59:59.900000`` against a ``12:00:00`` deadline would
    be compared as "in time" in Python and then persisted as
    ``12:00:00``: a receipt claiming the exact moment this project
    defines as past due. Truncating here makes the value the decision
    used and the value the database stores the same instant, on every
    backend.

    Truncation floors, never rounds, so it can only ever make the
    request *earlier* -- it fails closed at ``opens_at`` and stays
    honest at ``due_at``. Assignment ``opens_at`` / ``due_at`` already
    carry no microseconds (``AssignmentForm`` parses to second
    precision), so both comparisons happen entirely in whole seconds.

    Deliberately local to this route: it wraps the shared
    :func:`~app.services.schedule_occurrences.utc_reference_now` rather
    than changing it, so the M01 read paths and every other module keep
    their existing clock behaviour untouched.
    """
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# Signed submission-context snapshot (Phase 4 / M02)
# ======================================================================

_SUBMISSION_CONTEXT_SALT = "student.assignment-submission-context.phase4-m02.v1"
_SUBMISSION_CONTEXT_FIELDS = (
    "student_public_id",
    "group_public_id",
    "assignment_public_id",
    "title",
    "instructions",
    "submission_type",
    "opens_at",
    "due_at",
)

#: Canonical, deterministic serialization for the two datetime fields --
#: a fixed second-precision ISO string, so a value that survives a JSON
#: round trip inside the signed token compares byte-for-byte against a
#: freshly rendered one and can never look "changed" merely because it
#: was formatted differently. Same convention as the Teacher edit
#: snapshot.
_CONTEXT_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _submission_context_serializer():
    return URLSafeSerializer(
        current_app.config["SECRET_KEY"], salt=_SUBMISSION_CONTEXT_SALT
    )


def _submission_context_payload(student_public_id, group_public_id, assignment):
    """The exact context a Student's open answer form was written
    against.

    Row locks alone cannot protect this form: the Student may have opened
    it minutes before a Teacher rewrote the task, and the lock the POST
    takes would happily accept an answer to a question that no longer
    exists. Binding the answer to the wording and the window the Student
    actually read is what closes that.

    Bound to the Student, the Group, the Assignment, the **title**, the
    **instructions** and the canonical UTC ``opens_at`` / ``due_at``.

    ``status`` and ``published_at`` are deliberately excluded: a
    publication-only toggle changes nothing the Student read, so it must
    not invalidate an unchanged form -- and current publication and
    visibility are authoritatively re-checked against the locked rows
    before acceptance regardless, so excluding them weakens nothing.

    Only **public** identifiers appear. A signed token is authenticated,
    not encrypted: anyone holding it can read its payload, so no internal
    database id is ever placed in it.
    """
    return {
        "student_public_id": student_public_id,
        "group_public_id": group_public_id,
        "assignment_public_id": assignment.public_id,
        "title": assignment.title,
        "instructions": assignment.instructions,
        "submission_type": assignment.submission_type,
        "opens_at": assignment.opens_at.strftime(_CONTEXT_DATETIME_FORMAT),
        "due_at": assignment.due_at.strftime(_CONTEXT_DATETIME_FORMAT),
    }


def _make_submission_context_token(student_public_id, group_public_id, assignment):
    return _submission_context_serializer().dumps(
        _submission_context_payload(student_public_id, group_public_id, assignment)
    )


def _load_submission_context(token):
    """The token's payload, or ``None`` for a missing, malformed,
    invalidly signed, or wrong-shaped one. Every ``None`` is treated
    exactly like an outdated snapshot -- rejected, never trusted."""
    if not token:
        return None
    try:
        payload = _submission_context_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_SUBMISSION_CONTEXT_FIELDS):
        return None
    return payload


def _submission_context_is_stale(
    token, student_public_id, group_public_id, assignment_public_id, assignment
):
    """True when `token` does not exactly describe the **locked**
    Assignment as read by **this** Student under **this** Group.

    A cross-Student, cross-Group or cross-Assignment token fails on the
    three identifier fields; a Teacher edit to any bound field fails on
    the content comparison.
    """
    payload = _load_submission_context(token)
    if payload is None:
        return True
    if payload["student_public_id"] != student_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["assignment_public_id"] != assignment_public_id:
        return True
    return payload != _submission_context_payload(
        student_public_id, group_public_id, assignment
    )


# ======================================================================
# Shared detail rendering
# ======================================================================


def _render_assignment_detail(
    group_public_id, assignment_public_id, form=None, context_token=None
):
    """Render the nested Assignment detail page, or 404.

    Re-runs the whole SQL-scoped visibility query and this Student's own
    receipt lookup, so the page is always built from freshly authorized
    state -- including on the POST paths that re-render after a rejection.

    `form` and `context_token` are supplied only by the validation-error
    path, which must show the Student their attempted answer again with
    the **original** token, never a freshly minted one paired with
    attempted values.
    """
    tz_name = _tz_name()
    reference_utc = utc_reference_now()
    row = student_assignment_detail(
        current_user.id, group_public_id, assignment_public_id, reference_utc
    )
    if row is None:
        abort(404)

    assignment = row[0]
    submission = student_submission(assignment.id, current_user.id)
    item = build_student_item(row, tz_name, reference_utc)

    # Teacher feedback (Phase 4 / M03) is fetched ONLY after the whole
    # visibility formula above has already produced this Student's own
    # submission, and ONLY for that exact Submission id -- so it rides
    # entirely on the receipt's authorization and can never be reached
    # through a classmate's row. One fixed, bounded lookup by the
    # `uq_submission_feedback_submission` unique key; no history load, and
    # nothing at all when there is no submission to attach it to.
    feedback = (
        build_feedback_panel(student_feedback(submission.id), tz_name)
        if submission is not None
        else None
    )

    # The answer form appears only when there is nothing submitted yet
    # AND the Assignment is currently open. A past-due Assignment with no
    # submission stays readable and shows "Not submitted" instead.
    can_submit = submission is None and item["state"] == STATE_OPEN
    if can_submit:
        if form is None:
            form = (FileSubmissionForm if assignment.submission_type == "file" else SubmissionForm)(formdata=None)
        if context_token is None:
            context_token = _make_submission_context_token(
                current_user.public_id, group_public_id, assignment
            )
    else:
        form = None
        context_token = None

    return private_no_store(
        "student/assignments/detail.html",
        assignment=item,
        tz_name=tz_name,
        submission=build_student_receipt(submission, tz_name),
        feedback=feedback,
        form=form,
        can_submit=can_submit,
        submission_context_token=context_token,
        upload_policy=upload_policy_view() if assignment.submission_type == "file" else None,
    )


# ======================================================================
# Reads
# ======================================================================


@student_bp.get("/assignments")
@roles_required(UserRole.STUDENT.value)
def assignments_list():
    """Preserve old bookmarks through the unified Activities hub."""
    return private_redirect(url_for("student.activities", type="assignment"))


@student_bp.get("/groups/<group_public_id>/assignments/<assignment_public_id>")
@roles_required(UserRole.STUDENT.value)
def assignment_detail(group_public_id, assignment_public_id):
    """One Assignment, or a non-disclosing 404.

    Both public ids are supplied to the same fully scoped query, so a
    valid Assignment id paired with the wrong Group id -- and every other
    unauthorized combination -- produces no row and the same 404.

    The page shows exactly one of three states: the answer form (visible,
    open, nothing submitted), this Student's own immutable receipt
    (already submitted), or a "Not submitted" notice (visible, past due,
    nothing submitted). A GET never creates or changes anything.
    """
    return _render_assignment_detail(group_public_id, assignment_public_id)


@student_bp.get("/groups/<group_public_id>/assignments/<assignment_public_id>/submissions/<submission_public_id>/file")
@roles_required(UserRole.STUDENT.value)
def assignment_file_download(group_public_id, assignment_public_id, submission_public_id):
    row = student_assignment_detail(current_user.id, group_public_id, assignment_public_id, utc_reference_now())
    if row is None:
        abort(404)
    uploaded = submission_uploaded_file(row[0].id, submission_public_id, student_id=current_user.id)
    return serve_uploaded_file(uploaded, current_user.id, force_attachment=True)


# ======================================================================
# Submission -- one POST, one INSERT, never an update
# ======================================================================


def _lock_submission_chain(
    group_public_id, term_id, level_id, course_id, student_id, assignment_id
):
    """Acquire the route-specific Phase 4 / M02 lock order in one open
    transaction:

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group -> Student User -> Enrollment -> Assignment
        -> the existing Submission for this Assignment + Student, if any

    This is the shared hierarchy/Group prefix every Group-affecting
    mutation in the project uses, extended with the two rows this
    workflow actually decides on. Because a Teacher Assignment edit and a
    publication toggle lock the **same** Group and the **same**
    Assignment row (``app/blueprints/teacher/assignments.py``), and every
    membership and Group lifecycle mutation locks the same Group, a first
    submission serializes against all of them rather than racing.

    Returns ``(hierarchy, group, student, enrollment, assignment,
    submission)``. Any of them may be ``None`` -- the caller must treat
    that as a business/authorization rejection, roll back, and 404 or
    redirect; it must never "keep going".
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    student = User.query.filter_by(id=student_id).with_for_update().first()
    enrollment = None
    if group is not None:
        enrollment = (
            Enrollment.query.filter_by(group_id=group.id, student_id=student_id, status="active")
            .with_for_update()
            .first()
        )
    assignment = Assignment.query.filter_by(id=assignment_id).with_for_update().first()
    submission = (
        Submission.query.filter_by(assignment_id=assignment_id, student_id=student_id, enrollment_id=enrollment.id if enrollment else None)
        .with_for_update()
        .first()
    )
    return hierarchy, group, student, enrollment, assignment, submission


def _locked_visibility_broken(
    hierarchy,
    group,
    student,
    enrollment,
    assignment,
    term_id,
    level_id,
    course_id,
    assignment_public_id,
    reference_utc,
):
    """True when the **locked** rows no longer satisfy the M01 effective
    visibility formula for this Student and this Assignment.

    Every clause of that formula is re-proved here against current-read
    locked rows rather than trusted from the pre-lock preview: the exact
    hierarchy identities and linkages, every ancestor and Group status,
    the Student's role and account status, the exact Enrollment ownership
    and its active status, the Assignment's existence and nested Group
    ownership, and both publication and opening visibility.

    The caller turns a ``True`` into the same non-disclosing 404 the read
    routes produce, with no partial write of any kind.
    """
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    if term is None or level is None or course is None:
        return True
    if group is None or student is None or enrollment is None or assignment is None:
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
    if any(
        row.status != _ACTIVE for row in (term, level, course, group)
    ):
        return True

    # A foreign key into `users` proves the row exists, never that it is
    # still a Student or still active.
    if student.role != UserRole.STUDENT.value or student.status != UserStatus.ACTIVE.value:
        return True
    if enrollment.student_id != student.id or enrollment.group_id != group.id:
        return True
    if enrollment.status != _ENROLLMENT_ACTIVE:
        return True

    if assignment.group_id != group.id or assignment.public_id != assignment_public_id:
        return True
    if assignment.status != _PUBLISHED or assignment.opens_at > reference_utc:
        return True
    return False


@student_bp.post("/groups/<group_public_id>/assignments/<assignment_public_id>/submit")
@roles_required(UserRole.STUDENT.value)
def assignment_submit(group_public_id, assignment_public_id):
    """Accept this Student's one final answer to this Assignment.

    **Nothing about ownership is taken from the request.** The Student is
    the authenticated session, the Assignment and Group are the
    authorized nested public identifiers, ``submitted_at`` is generated
    on the server, and ``public_id`` is generated by the model. A forged
    ``student_id`` / ``assignment_id`` / ``submitted_at`` / ``status``
    field in the POST body has nowhere to land. The teacher-configured
    form carries only ``answer_text`` or exactly one ``file`` payload.

    **Acceptance window.** A FIRST submission is accepted only when
    ``opens_at <= authoritative_now < due_at``. At exactly ``due_at`` the
    deadline has passed. The authoritative moment is read **after** every
    potentially blocking lock, so a request that arrived in time but
    waited behind another transaction until after the deadline is
    rejected -- reusing the pre-lock preview moment would let it through.
    The same moment is used for the acceptance decision and persisted as
    ``submitted_at``, so a receipt can never claim a time the decision
    did not use.

    **Order of the post-lock checks.** Authorization and visibility come
    first, so a hidden Assignment fails identically whether or not a row
    already exists. An existing submission then short-circuits to its
    receipt -- a duplicate is an authorized no-op and needs no deadline,
    context or answer validation, which is what makes a double click, a
    replay, a changed payload and a late retry all safe. Only a FIRST
    insertion is validated further, and the stale-context check runs
    before the deadline check so that a Teacher edit is always reported
    as a changed Assignment rather than as whatever the edit did to the
    window.

    **Failure recovery re-authorizes.** If the INSERT raises
    ``IntegrityError``, the transaction is rolled back and the whole
    visibility formula is proved again from scratch, with a fresh
    reference moment and the original nested public identifiers, before
    any row is allowed to influence the response. A rolled-back read is
    not authorization evidence, and the same concurrent change that
    caused the conflict may also have ended this Student's access.
    """
    detail_url = _detail_url(group_public_id, assignment_public_id)

    # ------------------------------------------------------------------
    # Pre-lock preview: the same SQL-scoped authorization the GET uses.
    # Friendly and fast, but NOT authoritative -- everything it proves is
    # proved again below against locked rows.
    # ------------------------------------------------------------------
    preview_reference = utc_reference_now()
    preview = student_assignment_detail(
        current_user.id, group_public_id, assignment_public_id, preview_reference
    )
    if preview is None:
        abort(404)
    preview_assignment, _preview_group, preview_course, preview_level, preview_term = preview

    # Ordinary field validation (empty / whitespace-only / too long) runs
    # here, while the session is still cheap to touch. Its RESULT is
    # applied only after the locks, so a rejection order can never depend
    # on it.
    submission_type = preview_assignment.submission_type
    form = (FileSubmissionForm if submission_type == "file" else SubmissionForm)()
    form_is_valid = form.validate_on_submit()
    answer_text = form.normalized_answer() if submission_type == "text" else None

    # Every scalar the rest of this request needs is captured BEFORE the
    # transaction reset below, so nothing between that reset and the
    # required locks triggers a lazy ORM or current_user reload that
    # would establish a fresh read snapshot ahead of the locks.
    student_id = current_user.id
    student_public_id = current_user.public_id
    assignment_id = preview_assignment.id
    term_id = preview_term.id
    level_id = preview_level.id
    course_id = preview_course.id
    submitted_token = request.form.get("submission_context", "")

    stored = None
    committed = False
    material_config = current_material_config()
    # Never stream file bytes while holding a database write lock. The locked
    # checks below still own visibility, episode, stale context and finality.
    if submission_type == "file" and form_is_valid:
        try:
            stored = store_validated_upload(material_config, form.file.data, form.file.data.filename)
        except FileValidationError as exc:
            form.file.errors.append(str(exc))
            form_is_valid = False
        except OSError:
            current_app.logger.error("Assignment private-file storage unavailable")
            form.file.errors.append("Your file could not be stored. Please select it again and retry.")
            form_is_valid = False

    try:

        # ------------------------------------------------------------------
        # The single deliberate reset + the route-specific lock order.
        # ------------------------------------------------------------------
        hierarchy, group, student, enrollment, assignment, submission = _lock_submission_chain(
            group_public_id, term_id, level_id, course_id, student_id, assignment_id
        )

        # The authoritative acceptance moment: read only now, after every
        # lock that could have blocked, and truncated to the canonical whole
        # second the column can actually hold. This ONE value backs every
        # check below and is the value persisted as `submitted_at`.
        now_utc = _acceptance_moment()

        if _locked_visibility_broken(
            hierarchy, group, student, enrollment, assignment,
            term_id, level_id, course_id, assignment_public_id, now_utc,
        ):
            db.session.rollback()
            abort(404)

        # ------------------------------------------------------------------
        # Already submitted -- an authorized no-op, never a rewrite.
        # ------------------------------------------------------------------
        if submission is not None:
            db.session.rollback()
            note_outcome("assignment_submission", "duplicate")
            flash(
                "You have already submitted this assignment. Submissions are final and cannot be "
                "changed or replaced.",
                "info",
            )
            return redirect(detail_url)

        # ------------------------------------------------------------------
        # FIRST-insertion validation only, against the LOCKED Assignment.
        # ------------------------------------------------------------------
        if _submission_context_is_stale(
            submitted_token, student_public_id, group_public_id, assignment_public_id, assignment
        ):
            # Post/Redirect/Get, discarding every attempted value: pairing a
            # fresh token with the answer that was written against the OLD
            # wording is precisely the bypass this rejection exists to close.
            db.session.rollback()
            note_outcome("assignment_submission", "rejected_stale")
            flash(
                "This assignment was changed since this page was opened. Please read the current "
                "version and prepare your submission again.",
                "danger",
            )
            return redirect(detail_url)

        if now_utc >= assignment.due_at:
            db.session.rollback()
            note_outcome("assignment_submission", "rejected_closed")
            flash(
                "The deadline for this assignment has passed, so it can no longer be submitted.",
                "danger",
            )
            return redirect(detail_url)

        if not form_is_valid:
            # Ordinary validation failure with a still-valid context: show
            # the attempted answer again with the ORIGINAL token, so the
            # Student can fix it without losing what they wrote.
            db.session.rollback()
            note_outcome("assignment_submission", "rejected_invalid")
            return _render_assignment_detail(
                group_public_id,
                assignment_public_id,
                form=form,
                context_token=submitted_token,
            )

        # ------------------------------------------------------------------
        # Insert. No field of any existing row is ever assigned: this path is
        # only reachable when no submission exists, and the only write is
        # this INSERT.
        # ------------------------------------------------------------------
        try:
            file_id = None
            if submission_type == "file":
                # Streaming completed before any write lock. Only metadata is
                # inserted under the locked, still-authorized Assignment/episode.
                if stored is None or assignment.submission_type != "file":
                    raise RuntimeError("Validated assignment file payload missing")
                uploaded = UploadedFile(
                    **{name: getattr(stored, name) for name in stored.__slots__},
                    uploaded_by_id=student.id,
                )
                db.session.add(uploaded)
                db.session.flush()
                file_id = uploaded.id
            db.session.add(Submission(
                assignment_id=assignment.id,
                student_id=student.id,
                enrollment_id=enrollment.id,
                answer_text=answer_text,
                uploaded_file_id=file_id,
                submitted_at=now_utc,
            ))
            db.session.commit()
            committed = True
        except IntegrityError:
            # 1. Roll back FIRST. Everything read before this point is now
            #    discarded state and must not be used as evidence of
            #    anything -- least of all authorization.
            db.session.rollback()

            # 2. Re-establish current authorization from scratch, with a
            #    fresh reference moment and the ORIGINAL nested public
            #    identifiers, through the same fully SQL-scoped visibility
            #    formula the read routes use. Whatever raised the
            #    IntegrityError may well have been a concurrent change that
            #    ALSO ended this Student's access -- a withdrawal, an
            #    unpublish, an ancestor archival, a suspension. Reporting
            #    "already submitted" in that case would disclose both that
            #    the Assignment exists and that a submission exists for it.
            recovered = student_assignment_detail(
                student_id, group_public_id, assignment_public_id, _acceptance_moment()
            )
            if recovered is None:
                abort(404)

            # 3. Only now may an existing row speak. A concurrent first
            #    submission for the same pair is the one IntegrityError this
            #    route can turn into a receipt -- and only after PROVING,
            #    against the freshly authorized Assignment, that a row for
            #    BOTH this Assignment and this Student exists. Anything else
            #    (a vanished foreign key, a constraint this code does not
            #    know about) stays a generic failure: no SQL, driver text,
            #    parameter or internal id ever reaches the Student. Nothing
            #    is written, overwritten or deleted on this path.
            if student_submission(recovered[0].id, student_id) is not None:
                note_outcome("assignment_submission", "duplicate")
                flash(
                    "You have already submitted this assignment. Submissions are final and cannot "
                    "be changed or replaced.",
                    "info",
                )
                return redirect(detail_url)
            note_outcome("assignment_submission", "failed")
            flash(
                "Your submission could not be submitted. Please reload the page and try again.",
                "danger",
            )
            return redirect(detail_url)

        note_outcome("assignment_submission", "submitted")
        flash(
            "Your submission was submitted. It is final and cannot be edited or resubmitted.",
            "success",
        )
        return redirect(detail_url)

    finally:
        if stored is not None and not committed:
            # Only this request's newly published, uncommitted bytes may be
            # removed. Committed LMS work is never retention-cleaned here.
            db.session.rollback()
            try:
                if not delete_stored_file(material_config, stored.storage_key):
                    current_app.logger.error("Uncommitted assignment file retained; reconciliation required")
            except OSError:
                current_app.logger.error("Uncommitted assignment file cleanup failed; reconciliation required")
