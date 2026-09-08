"""Teacher management of Group-owned Assignments (Phase 4 / M01).

Group-centered routes only
(``/teacher/groups/<group_public_id>/assignments/...``) -- there is
deliberately no flat ``/teacher/assignments`` collection. Every object is
addressed by ``public_id``; no internal numeric id ever appears in a URL,
a form value, or the rendered HTML.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then
server-side: the current Teacher must hold an **active**
``GroupTeacherAssignment`` to the Group in the URL, and every nested
Assignment lookup is constrained with ``Assignment.group_id ==
group.id``. A missing Group, a missing Assignment, an Assignment
``public_id`` belonging to another Group, an unassigned Teacher, and a
removed assignment all return the same non-disclosing **404** -- never a
403 and never a hint that the object exists. Multiple active assigned
Teachers are equal collaborators. The GET list stays available to an
actively assigned Teacher even when the Group or an academic ancestor is
archived, so history can always be read.

**Mutations.** Creation, editing, and publishing require -- re-checked
against the *locked* rows -- an active Teacher account with role
``teacher``, an active assignment to the Group, and an active
AcademicTerm / Level / Course / Group. **Unpublishing** is allowed while
the assignment is active even under an archived Group or ancestor, so
published work can always be withdrawn. Editing never alters ``status``
or ``published_at``; a Group or ancestor lifecycle change never rewrites
either; nothing is ever hard-deleted.

**Time.** Both datetime fields are entered and rendered in
``APP_TIMEZONE`` and persisted as canonical naive UTC (the form owns that
conversion). Each request derives **one** reference moment via
``utc_reference_now`` and passes it down, so the Scheduled / Open / Past
due labels on a page can never disagree with each other.

**Concurrency.** Every mutation follows the canonical lock order

    AcademicTerm -> Level -> Course -> Group -> Teacher User ->
    GroupTeacherAssignment -> Assignment rows (ascending internal id)

via ``lock_academic_hierarchy`` (which owns the single deliberate reset)
then the Group / User / assignment / Assignment ``SELECT ... FOR UPDATE``
in the same open transaction, with no second reset. The Group lock is
what serializes same-Group Assignment creation against an Administrator
Group retarget, which takes the same Group lock -- so a new Assignment
can never slip past the identity freeze. All authoritative conditions are
re-checked post-lock, and no model field is assigned until every one of
them has passed. A signed snapshot protects the edit form against a
time-separated co-teacher overwrite. ``IntegrityError`` is caught, rolled
back, and reported generically.
"""

from datetime import datetime, timezone

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
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.forms import AssignmentForm
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
    Assignment,
    AssignmentStatus,
    GroupTeacherAssignment,
    User,
    UserRole,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.assignment_queries import (
    PAGE_SIZE,
    build_teacher_view,
    normalize_page,
    teacher_assignments_page,
)
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.schedule_occurrences import to_app_local, utc_reference_now
from app.services.submission_feedback_queries import (
    FEEDBACK_INVALID,
    attach_feedback_states,
    build_feedback_panel,
    feedback_states_for_page,
    teacher_feedback,
)
from app.services.submission_queries import (
    assignment_has_submissions,
    assignment_ids_with_submissions,
    build_teacher_submission_item,
    build_teacher_submission_view,
    teacher_submission,
    teacher_submissions_page,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_DRAFT = AssignmentStatus.DRAFT.value
_PUBLISHED = AssignmentStatus.PUBLISHED.value


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


#: The one Teacher-facing sentence for the Phase 4 / M02 edit freeze, used
#: by every rejection path so the early check and the authoritative
#: post-lock check can never explain the same rule differently.
_FROZEN_MESSAGE = (
    "This assignment has student submissions, so its title, instructions, and time window "
    "can no longer be changed. Students answered exactly this wording within exactly this "
    "window, and rewriting it afterwards would change what their work was for. You can still "
    "publish or unpublish it, and you can read every submission."
)


def _private_no_store(template, **context):
    """Render a **personalized** Teacher page with the two headers it must
    carry (Phase 4 / M02).

    ``private, no-store`` because a submission list and a submission body
    are one Group's Students' work, shown only to that Group's actively
    assigned Teachers -- a shared or reused cache entry could serve them
    to somebody whose assignment has since been removed.
    ``Vary: Cookie`` so a cache can never hand one session's page to
    another. Same contract as the Student pages
    (``app/blueprints/student/routes.private_no_store``), applied
    here rather than imported across blueprints, matching how
    ``student/search.py`` and ``notifications/routes.py`` already set it.
    """
    response = make_response(render_template(template, **context))
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


# ----------------------------------------------------------------------
# Nested lookup
# ----------------------------------------------------------------------


def _assignment_for_group_or_404(group, assignment_public_id):
    """An Assignment by its own public_id, constrained to `group`. An
    Assignment public_id valid only for another Group 404s here."""
    return Assignment.query.filter_by(
        public_id=assignment_public_id, group_id=group.id
    ).first_or_404()


def _redirect_assignments(group_public_id):
    return redirect(
        url_for("teacher.group_assignments", group_public_id=group_public_id)
    )


# ----------------------------------------------------------------------
# Canonical lock chain + post-lock re-checks
# ----------------------------------------------------------------------


def _lock_assignment_chain(
    group_public_id, term_id, level_id, course_id, teacher_id, assignment_ids=()
):
    """Acquire the canonical Phase 4 / M01 lock order in one open
    transaction:

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group -> Teacher User -> GroupTeacherAssignment
        -> Assignment rows (ascending internal id)

    Returns ``(hierarchy, group, teacher, assignment_row, {id: assignment})``.
    Any of group / teacher / assignment_row / an Assignment may be
    ``None`` -- the caller must treat that as a business/authorization
    rejection, roll back, and 404 or redirect; it must never "keep going".
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    teacher = User.query.filter_by(id=teacher_id).with_for_update().first()
    teacher_assignment = None
    if group is not None:
        teacher_assignment = (
            GroupTeacherAssignment.query.filter_by(
                group_id=group.id, teacher_id=teacher_id
            )
            .with_for_update()
            .first()
        )
    assignments = {}
    for aid in sorted({a for a in assignment_ids if a is not None}):
        assignments[aid] = Assignment.query.filter_by(id=aid).with_for_update().first()
    return hierarchy, group, teacher, teacher_assignment, assignments


def _operational_block(hierarchy, group, term_id, level_id, course_id):
    """`None` if the locked Group and its locked AcademicTerm / Level /
    Course all exist and are active (so a create / edit / publish may
    proceed), else a Teacher-facing message."""
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    if (
        course is None
        or group.course_id != course.id
        or group.academic_term_id != term_id
        or course.level_id != level_id
    ):
        return "This group changed while you were working. Reload the page and try again."
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
        return (
            "Assignments can only be created, edited, or published while the group and its "
            f"academic term, course, and level are all active. The {_join_labels(labels)} "
            f"{verb} archived."
        )
    return None


# ----------------------------------------------------------------------
# List page -- bounded, paginated history
# ----------------------------------------------------------------------


@teacher_bp.get("/groups/<group_public_id>/assignments")
@roles_required(UserRole.TEACHER.value)
def group_assignments(group_public_id):
    group = _teacher_group_or_404(group_public_id)
    operational = _group_is_operational(group)
    tz_name = _tz_name()
    reference_utc = utc_reference_now()
    page = normalize_page(request.args.get("page"))

    rows, has_next = teacher_assignments_page(group.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = teacher_assignments_page(group.id, page)

    # ONE extra bounded query for the whole page (Phase 4 / M02): which of
    # these at most PAGE_SIZE Assignments already have submission history,
    # and are therefore frozen. Asking per row would be an N+1.
    frozen_ids = assignment_ids_with_submissions([row.id for row in rows])

    return render_template(
        "teacher/assignments/list.html",
        group=group,
        assignments=build_teacher_view(rows, tz_name, reference_utc, frozen_ids),
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


# ----------------------------------------------------------------------
# Create -- always starts draft
# ----------------------------------------------------------------------


@teacher_bp.route("/groups/<group_public_id>/assignments/new", methods=["GET", "POST"])
@roles_required(UserRole.TEACHER.value)
def assignment_create(group_public_id):
    preview_group = _teacher_group_or_404(group_public_id)

    if not _group_is_operational(preview_group):
        flash(
            "Assignments can only be created while the group and its academic term, course, "
            "and level are all active.",
            "danger",
        )
        return _redirect_assignments(group_public_id)

    form = AssignmentForm(tz_name=_tz_name(), group_id=preview_group.id)
    if form.validate_on_submit():
        title = form.title.data.strip()
        instructions = form.instructions.data.strip()
        opens_at = form.opens_at_utc
        due_at = form.due_at_utc
        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy, group, teacher, teacher_assignment, _ = _lock_assignment_chain(
            group_public_id, term_id, level_id, course_id, current_user.id
        )
        if _authz_broken(group, teacher, teacher_assignment):
            db.session.rollback()
            abort(404)

        blocked = _operational_block(hierarchy, group, term_id, level_id, course_id)
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_assignments(group_public_id)

        # A new Assignment is ALWAYS a draft with no publication time --
        # `status` and `published_at` are set here from constants, never
        # from the request, so a forged field in the POST body cannot
        # publish anything.
        assignment = Assignment(
            group_id=group.id,
            title=title,
            instructions=instructions,
            opens_at=opens_at,
            due_at=due_at,
            status=_DRAFT,
            published_at=None,
        )
        db.session.add(assignment)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This assignment could not be saved. An assignment with this title may already "
                "exist in the group, or the group may have just changed. Please reload and try "
                "again.",
                "danger",
            )
            return _render_assignment_form(
                form, group_public_id, assignment=None, snapshot_token=None
            )

        flash(f"Assignment '{assignment.title}' created as a draft.", "success")
        return _redirect_assignments(group_public_id)

    return _render_assignment_form(
        form, group_public_id, assignment=None, snapshot_token=None
    )


def _render_assignment_form(form, group_public_id, assignment, snapshot_token):
    """Re-render the create/edit form -- releases any write lock first
    (harmless on GET / a plain form failure) and refetches a display copy
    of the Group. `assignment` is None for create."""
    db.session.rollback()
    group = _teacher_group_or_404(group_public_id)
    return render_template(
        "teacher/assignments/form.html",
        form=form,
        group=group,
        assignment=assignment,
        tz_name=_tz_name(),
        edit_snapshot_token=snapshot_token,
    )


# ----------------------------------------------------------------------
# Edit -- with a signed stale-form snapshot
# ----------------------------------------------------------------------

_ASSIGNMENT_EDIT_SNAPSHOT_SALT = "teacher.assignment-edit-snapshot.phase4-m01.v1"
_ASSIGNMENT_EDIT_SNAPSHOT_FIELDS = ("public_id", "title", "instructions", "opens_at", "due_at")

#: Canonical, deterministic serialization for the two datetime snapshot
#: fields. A fixed second-precision ISO string, so a value that survives
#: a JSON round trip inside the signed token compares byte-for-byte
#: against a freshly rendered one and can never look "changed" merely
#: because it was formatted differently.
_SNAPSHOT_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _assignment_snapshot_serializer():
    return URLSafeSerializer(
        current_app.config["SECRET_KEY"], salt=_ASSIGNMENT_EDIT_SNAPSHOT_SALT
    )


def _assignment_snapshot_payload(assignment):
    """The exact editable state the snapshot covers.

    ``status``, ``published_at``, ``created_at`` and ``updated_at`` are
    deliberately excluded: the edit route never writes them, so a
    concurrent publish or unpublish must NOT invalidate an open edit form
    -- there is nothing it could overwrite. The two datetimes are the
    canonical **UTC** values, formatted deterministically.
    """
    return {
        "public_id": assignment.public_id,
        "title": assignment.title,
        "instructions": assignment.instructions,
        "opens_at": assignment.opens_at.strftime(_SNAPSHOT_DATETIME_FORMAT),
        "due_at": assignment.due_at.strftime(_SNAPSHOT_DATETIME_FORMAT),
    }


def _make_assignment_snapshot_token(assignment):
    return _assignment_snapshot_serializer().dumps(_assignment_snapshot_payload(assignment))


def _load_assignment_snapshot(token):
    if not token:
        return None
    try:
        payload = _assignment_snapshot_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_ASSIGNMENT_EDIT_SNAPSHOT_FIELDS):
        return None
    return payload


def _assignment_edit_is_stale(snapshot, assignment_public_id, current_assignment):
    if snapshot is None:
        return True
    if snapshot["public_id"] != assignment_public_id:
        return True
    return snapshot != _assignment_snapshot_payload(current_assignment)


def _redirect_stale_assignment_edit(group_public_id, assignment_public_id):
    """Post/Redirect/Get rejection for a missing, malformed, wrong-shaped,
    cross-Assignment, or genuinely outdated snapshot token.

    Discards every submitted value and reloads the current persisted ones
    through a fresh GET -- never pairs a freshly generated token with the
    attempted values, which is precisely the bypass this rejection exists
    to close (see ``admin/groups.py``'s equivalent for the full
    reasoning).
    """
    db.session.rollback()
    flash(
        "This assignment was changed since this form was opened. Please review the current "
        "values and try again.",
        "danger",
    )
    return redirect(
        url_for(
            "teacher.assignment_edit",
            group_public_id=group_public_id,
            assignment_public_id=assignment_public_id,
        )
    )


def _form_defaults_from(assignment, tz_name):
    """The local wall-clock values a GET of the edit form starts from --
    the stored UTC instants rendered through ``APP_TIMEZONE``, so what the
    Teacher sees is what they originally typed."""
    return {
        "title": assignment.title,
        "instructions": assignment.instructions,
        "opens_at": to_app_local(tz_name, assignment.opens_at),
        "due_at": to_app_local(tz_name, assignment.due_at),
    }


@teacher_bp.route(
    "/groups/<group_public_id>/assignments/<assignment_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def assignment_edit(group_public_id, assignment_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_assignment = _assignment_for_group_or_404(preview_group, assignment_public_id)
    tz_name = _tz_name()

    if not _group_is_operational(preview_group):
        flash(
            "Assignments can only be edited while the group and its academic term, course, and "
            "level are all active.",
            "danger",
        )
        return _redirect_assignments(group_public_id)

    # Helpful early check (Phase 4 / M02): keeps a Teacher from writing a
    # revision that will be rejected anyway, and keeps a bookmarked edit
    # URL from silently rendering a form that can no longer save. It is
    # deliberately NOT the enforcement point -- a submission can arrive
    # between here and the locks, so the authoritative current-read check
    # below runs inside the locked transaction.
    if assignment_has_submissions(preview_assignment.id):
        flash(_FROZEN_MESSAGE, "danger")
        return _redirect_assignments(group_public_id)

    submitted_token = None
    if request.method == "POST":
        submitted_token = request.form.get("edit_snapshot", "")
        if _assignment_edit_is_stale(
            _load_assignment_snapshot(submitted_token),
            assignment_public_id,
            preview_assignment,
        ):
            return _redirect_stale_assignment_edit(group_public_id, assignment_public_id)

    if request.method == "GET":
        form = AssignmentForm(
            data=_form_defaults_from(preview_assignment, tz_name),
            tz_name=tz_name,
            group_id=preview_group.id,
            assignment_id=preview_assignment.id,
        )
    else:
        form = AssignmentForm(
            tz_name=tz_name,
            group_id=preview_group.id,
            assignment_id=preview_assignment.id,
        )

    if form.validate_on_submit():
        title = form.title.data.strip()
        instructions = form.instructions.data.strip()
        opens_at = form.opens_at_utc
        due_at = form.due_at_utc
        term_id = preview_group.academic_term_id
        level_id = preview_group.course.level_id
        course_id = preview_group.course_id

        hierarchy, group, teacher, teacher_assignment, assignments = _lock_assignment_chain(
            group_public_id, term_id, level_id, course_id, current_user.id,
            assignment_ids=[preview_assignment.id],
        )
        if _authz_broken(group, teacher, teacher_assignment):
            db.session.rollback()
            abort(404)
        assignment = assignments.get(preview_assignment.id)
        if assignment is None or assignment.group_id != group.id:
            db.session.rollback()
            abort(404)

        # Repeat the staleness check against the locked, current row --
        # this is what closes the window between the preview read above
        # and these locks.
        if _assignment_edit_is_stale(
            _load_assignment_snapshot(submitted_token), assignment_public_id, assignment
        ):
            return _redirect_stale_assignment_edit(group_public_id, assignment_public_id)

        blocked = _operational_block(hierarchy, group, term_id, level_id, course_id)
        if blocked is not None:
            db.session.rollback()
            flash(blocked, "danger")
            return _redirect_assignments(group_public_id)

        # THE authoritative edit freeze (Phase 4 / M02). It is a current
        # read taken while this Assignment row is locked, so a first
        # submission -- which locks the same Group and the same Assignment
        # row before it inserts -- either committed before this
        # transaction acquired the lock and is seen here, or waits behind
        # it and finds the edit already applied. A forged POST or a form
        # opened before the first submission cannot get past this: it is
        # checked here, not in the template, and it runs BEFORE any field
        # is assigned, so a rejected edit leaves every column -- including
        # `updated_at` -- exactly as it was.
        if assignment_has_submissions(assignment.id):
            db.session.rollback()
            flash(_FROZEN_MESSAGE, "danger")
            return _redirect_assignments(group_public_id)

        # No field is assigned until every check above has passed, so a
        # rejection never leaves a partial update. `status` and
        # `published_at` are never assigned here: editing a published
        # Assignment keeps it published with its original publication
        # time, and a publish/unpublish that committed while this form
        # was open is left intact.
        assignment.title = title
        assignment.instructions = instructions
        assignment.opens_at = opens_at
        assignment.due_at = due_at
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "This assignment could not be saved. An assignment with this title may already "
                "exist in the group. Please reload and try again.",
                "danger",
            )
            return _render_assignment_form(
                form, group_public_id, preview_assignment, submitted_token
            )

        flash(f"Assignment '{assignment.title}' updated.", "success")
        return _redirect_assignments(group_public_id)

    # GET, or a POST whose token passed both checks but failed ordinary
    # field validation: the ORIGINAL submitted token is re-embedded
    # unchanged, never a fresh one -- a fresh token may only ever pair
    # with freshly loaded persisted values.
    edit_snapshot_token = (
        submitted_token
        if request.method == "POST"
        else _make_assignment_snapshot_token(preview_assignment)
    )
    return render_template(
        "teacher/assignments/form.html",
        form=form,
        group=preview_group,
        assignment=preview_assignment,
        tz_name=tz_name,
        edit_snapshot_token=edit_snapshot_token,
    )


# ----------------------------------------------------------------------
# Publication toggle -- POST only, sole owner of Assignment status
# ----------------------------------------------------------------------


@teacher_bp.post(
    "/groups/<group_public_id>/assignments/<assignment_public_id>/toggle-publication"
)
@roles_required(UserRole.TEACHER.value)
def assignment_toggle_publication(group_public_id, assignment_public_id):
    preview_group = _teacher_group_or_404(group_public_id)
    preview_assignment = _assignment_for_group_or_404(preview_group, assignment_public_id)

    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id

    hierarchy, group, teacher, teacher_assignment, assignments = _lock_assignment_chain(
        group_public_id, term_id, level_id, course_id, current_user.id,
        assignment_ids=[preview_assignment.id],
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)
    assignment = assignments.get(preview_assignment.id)
    if assignment is None or assignment.group_id != group.id:
        db.session.rollback()
        abort(404)

    if assignment.status == _PUBLISHED:
        # Unpublish -- allowed while the assignment is active even under
        # an archived Group / ancestor, so published work can always be
        # withdrawn. Returns the Assignment to draft and clears the
        # publication time.
        assignment.status = _DRAFT
        assignment.published_at = None
        db.session.commit()
        flash(
            f"Assignment '{assignment.title}' unpublished (back to draft).", "success"
        )
        return _redirect_assignments(group_public_id)

    # Publish / republish -- requires the operational chain, and always
    # stamps a fresh current publication time.
    blocked = _operational_block(hierarchy, group, term_id, level_id, course_id)
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_assignments(group_public_id)

    assignment.status = _PUBLISHED
    assignment.published_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            "This assignment could not be published. Please reload and try again.", "danger"
        )
        return _redirect_assignments(group_public_id)

    flash(f"Assignment '{assignment.title}' published.", "success")
    return _redirect_assignments(group_public_id)


# ----------------------------------------------------------------------
# Read-only submission views (Phase 4 / M02, + the M03 feedback surface)
# ----------------------------------------------------------------------
#
# Both routes here are still GET only and still read only: neither ever
# writes anything, and the Student's answer stays immutable and
# uneditable. Phase 4 / M03 adds two derived, read-only feedback surfaces
# to them -- a "Feedback provided" / "Awaiting feedback" indicator on the
# list and the current feedback with its last-editor attribution on the
# detail page -- plus a LINK to the separate feedback editor
# (`app/blueprints/teacher/feedback.py`), which is the only route that
# writes. There is still deliberately no score, grade, rubric, pass/fail,
# publish, delete or resubmit control anywhere, and neither indicator is
# read from a stored review-status column: none exists.
#
# Authorization reuses the exact same two steps every other nested
# Teacher route uses: `_teacher_group_or_404` proves an ACTIVE
# GroupTeacherAssignment to the Group in the URL (so all actively
# assigned co-teachers have equal access, and an unassigned or removed
# Teacher gets a non-disclosing 404), and `_assignment_for_group_or_404`
# constrains the Assignment to that Group. The submission queries are
# then scoped to that verified internal Assignment id, so a Submission
# public id from another Assignment -- or another Group -- finds nothing
# and 404s identically.
#
# History stays readable when the Group or an ancestor is archived, when
# the Assignment is unpublished, after the deadline, and when the
# submitting Student has since been withdrawn or suspended: none of those
# is a reason to hide work that was really done. What IS still enforced
# is conditional User role integrity -- a row whose `student_id` points
# at a non-Student User is excluded from both reads, because it is not
# Student work, even though it still counts for the Assignment edit
# freeze. The same rule is applied a second time in M03 to a feedback
# row's `reviewer_id`: such a row fails closed, and is never reported as
# "awaiting feedback".


def _submissions_url(group_public_id, assignment_public_id):
    return url_for(
        "teacher.assignment_submissions",
        group_public_id=group_public_id,
        assignment_public_id=assignment_public_id,
    )


@teacher_bp.get("/groups/<group_public_id>/assignments/<assignment_public_id>/submissions")
@roles_required(UserRole.TEACHER.value)
def assignment_submissions(group_public_id, assignment_public_id):
    """One bounded page of an Assignment's submissions, newest first.

    Fixed page size, SQL ordering (``submitted_at DESC, id DESC``), SQL
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag, and the same page
    normalization and past-the-end fallback the other lists use. The
    Student display name comes from the same joined statement, so the
    page costs a fixed number of queries no matter how many rows it
    shows. No answer body is loaded here.
    """
    group = _teacher_group_or_404(group_public_id)
    assignment = _assignment_for_group_or_404(group, assignment_public_id)
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))

    rows, has_next = teacher_submissions_page(assignment.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = teacher_submissions_page(assignment.id, page)

    # ONE extra bounded query for the whole page (Phase 4 / M03): which of
    # these at most PAGE_SIZE Submissions already carry feedback, and
    # whether each such row is intact. Asking per row would be an N+1, and
    # the indicator is derived from that existence -- there is no stored
    # review-status column anywhere.
    feedback_states = feedback_states_for_page(
        assignment.id, [row.public_id for row in rows]
    )

    return _private_no_store(
        "teacher/submissions/list.html",
        group=group,
        assignment=build_teacher_view([assignment], tz_name, utc_reference_now())[0],
        submissions=attach_feedback_states(
            build_teacher_submission_view(rows, tz_name), feedback_states
        ),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


@teacher_bp.get(
    "/groups/<group_public_id>/assignments/<assignment_public_id>"
    "/submissions/<submission_public_id>"
)
@roles_required(UserRole.TEACHER.value)
def submission_detail(group_public_id, assignment_public_id, submission_public_id):
    """One submission, read only, or a non-disclosing 404.

    All three public ids must name the same nested chain: the Group must
    be one this Teacher is actively assigned to, the Assignment must
    belong to that Group, and the Submission must belong to that
    Assignment. Any other combination produces no row and the same 404.
    """
    group = _teacher_group_or_404(group_public_id)
    assignment = _assignment_for_group_or_404(group, assignment_public_id)
    tz_name = _tz_name()

    row = teacher_submission(assignment.id, submission_public_id)
    if row is None:
        abort(404)

    # ONE extra bounded lookup (Phase 4 / M03) for this one Submission's
    # feedback, by its own unique constraint. The Add/Edit control is
    # offered only when a write could actually succeed -- an archived
    # chain, or a role-inconsistent existing row, hides it -- but the
    # editor re-proves both against locked rows regardless, so this is
    # presentation, never authorization.
    feedback = build_feedback_panel(
        teacher_feedback(assignment.id, submission_public_id), tz_name
    )

    return _private_no_store(
        "teacher/submissions/detail.html",
        group=group,
        assignment=build_teacher_view([assignment], tz_name, utc_reference_now())[0],
        submission=build_teacher_submission_item(row, tz_name, include_answer=True),
        feedback=feedback,
        can_write_feedback=(
            _group_is_operational(group) and feedback["state"] != FEEDBACK_INVALID
        ),
        tz_name=tz_name,
        submissions_url=_submissions_url(group_public_id, assignment_public_id),
    )
