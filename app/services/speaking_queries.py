"""Read-only query layer for Group-owned Speaking activities
(Phase 4 / M06).

Flask-independent -- plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring
``app/services/assignment_queries.py`` and
``app/services/listening_queries.py``. **Every function here is
read-only**: no locks, no writes. The locking write paths live in
``app/blueprints/teacher/speaking.py`` and
``app/blueprints/student/speaking.py``.

**Authorization is never performed here**, with one exception that is the
opposite of a loophole: :func:`student_speaking_query` *is* the
authorization, because its ``WHERE`` clause is the complete Student
visibility formula. It is built from
``assignment_queries._visible_assignment_query``'s own clauses -- the
same effective-visibility rule the ordinary Assignment surface uses --
scoped the other way, to Assignments that **do** carry the extension.
Every other function is handed an internal id the calling route has
already proven.

**This module deliberately owns almost nothing.** A Speaking activity is
one Assignment plus one extension row, so Group ownership, the title, the
instructions, the time window, the publication lifecycle, the derived
Scheduled / Open / Past due states and the deadline arithmetic all come
from ``assignment_queries`` unchanged. What is added here is exactly what
that module cannot answer: which Assignments carry the extension, whose
recordings exist for one, and what the one shared feedback record on a
recording says.

**Feedback presentation is reused, not re-declared.** The three derived
states (``absent`` / ``present`` / ``invalid``), the fail-closed
reviewer-role rule and the presentation builder all come from
``app/services/submission_feedback_queries.py``: M06 stores its feedback
in its own table, but a Teacher and a Student must not meet two different
vocabularies for the same idea. Only the *queries* are new, and they
select the same labelled columns those builders already read.

**Nothing here ever selects or returns a storage key, a filesystem path,
a SHA-256 digest, an uploader identity or an internal id.** The recording
reaches a page only as an authorized route URL that the blueprint builds,
and every audio lookup re-proves the ``audio`` category rather than
trusting the foreign key.
"""

from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    FileCategory,
    Group,
    Level,
    SpeakingActivity,
    SpeakingFeedback,
    SpeakingSubmission,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.services.assignment_queries import (
    PAGE_SIZE,
    STATE_LABELS,
    derived_state,
    student_list_order,
)
from app.services.schedule_occurrences import to_app_local
from app.services.submission_feedback_queries import (
    FEEDBACK_ABSENT,
    feedback_state,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_AUDIO = FileCategory.AUDIO.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_PUBLISHED = AssignmentStatus.PUBLISHED.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value


# ---------------------------------------------------------------------------
# The extension row
# ---------------------------------------------------------------------------


def speaking_activity_for_assignment(assignment_id):
    """The Speaking extension of this Assignment, or ``None`` for an
    ordinary Assignment.

    One indexed read through ``speaking_activities.assignment_id``'s own
    unique index. Used by the write paths, which have already locked the
    Assignment and need the extension as a row rather than as a query
    fragment.
    """
    return SpeakingActivity.query.filter_by(assignment_id=assignment_id).first()


def audio_upload_for_submission(submission):
    """A submission's recording, but **only** if it is still a valid one:
    the row exists and its server-determined category is ``audio``.

    Returns ``None`` otherwise, and the caller turns that into the same
    non-disclosing 404 a missing submission produces. A foreign key
    proves the ``uploaded_files`` row exists; it never proves the row is a
    recording -- exactly as a foreign key into ``users`` never proves a
    role. That condition is therefore re-checked on **every** audio
    request, not only when the association was created.
    """
    if submission is None or submission.audio_file_id is None:
        return None
    uploaded_file = UploadedFile.query.filter_by(id=submission.audio_file_id).first()
    if uploaded_file is None or uploaded_file.category != _AUDIO:
        return None
    return uploaded_file


# ---------------------------------------------------------------------------
# Submission history -- the freeze question, and the page-level flags
# ---------------------------------------------------------------------------


def activity_has_submissions(speaking_activity_id):
    """True if **any** SpeakingSubmission row exists for this activity.

    Deliberately unfiltered, exactly like M02's
    ``assignment_has_submissions``. Current Student eligibility is
    irrelevant: a withdrawn or suspended Student's recording, and even a
    row with broken conditional Student-role integrity, still means the
    activity's wording and time window have been acted on and must no
    longer change -- and that it may no longer be withdrawn.

    Used both as the Teacher's helpful pre-lock check and, inside the
    locked transaction, as the authoritative check before any field is
    assigned.
    """
    return (
        db.session.query(SpeakingSubmission.id)
        .filter(SpeakingSubmission.speaking_activity_id == speaking_activity_id)
        .first()
        is not None
    )


def activity_ids_with_submissions(activity_ids):
    """The subset of `activity_ids` that already have recordings, as a
    set -- **one** bounded query for a whole page.

    The Teacher Speaking list needs the freeze flag per row; asking once
    per row would be exactly the N+1 this project forbids. The input is
    one page of ids (at most :data:`PAGE_SIZE`), so the ``IN`` list is
    bounded by construction.
    """
    ids = [i for i in activity_ids if i is not None]
    if not ids:
        return frozenset()
    rows = (
        db.session.query(SpeakingSubmission.speaking_activity_id)
        .filter(SpeakingSubmission.speaking_activity_id.in_(ids))
        .distinct()
        .all()
    )
    return frozenset(row[0] for row in rows)


# ---------------------------------------------------------------------------
# Teacher reads -- already authorized by the route's active assignment check
# ---------------------------------------------------------------------------


def teacher_speaking_page(group_id, page):
    """One bounded page of a Group's Speaking activities, newest deadline
    first.

    Returns ``(rows, has_next)``. Same contract as
    ``assignment_queries.teacher_assignments_page``: ``due_at DESC,
    id DESC``, ``LIMIT PAGE_SIZE + 1`` for the has-next flag with no
    ``COUNT`` and no disclosed total, and explicit **columns** rather than
    entities.

    ``instructions`` is deliberately **not** selected: a list row shows a
    title, a status and three timestamps, and loading up to 10,000
    characters of body text per row to render none of it would make the
    page's cost grow with how much Teachers have written.

    The join to ``speaking_activities`` is what makes this list Speaking
    activities *only* -- an ordinary Assignment has no row to join to, so
    it can never appear here.
    """
    rows = (
        db.session.query(
            SpeakingActivity.id.label("activity_id"),
            SpeakingActivity.public_id,
            Assignment.title,
            Assignment.status,
            Assignment.opens_at,
            Assignment.due_at,
            Assignment.published_at,
        )
        .join(Assignment, SpeakingActivity.assignment_id == Assignment.id)
        .filter(Assignment.group_id == group_id)
        .order_by(Assignment.due_at.desc(), Assignment.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def build_teacher_speaking_list_view(rows, tz_name, reference_utc, frozen_ids=frozenset()):
    """Plain presentation dicts for the Teacher Speaking list -- public
    ids, localized times and display strings only.

    No ORM row and no internal id is carried, so rendering the list can
    never trigger a lazy load or an ORM-driven authorization decision.
    `frozen_ids` is the set of internal activity ids that already have
    recordings, resolved by the route in **one** bounded query for the
    whole page (:func:`activity_ids_with_submissions`) rather than per
    row; it becomes the ``has_submissions`` flag and the ids are consumed
    here rather than placed in the resulting dict.
    """
    view = []
    for row in rows:
        state = derived_state(row.status, row.opens_at, row.due_at, reference_utc)
        view.append(
            {
                "public_id": row.public_id,
                "title": row.title,
                "status": row.status,
                "is_published": row.status == _PUBLISHED,
                "has_submissions": row.activity_id in frozen_ids,
                "opens_local": to_app_local(tz_name, row.opens_at),
                "due_local": to_app_local(tz_name, row.due_at),
                "published_local": (
                    to_app_local(tz_name, row.published_at)
                    if row.published_at is not None
                    else None
                ),
                "state": state,
                "state_label": STATE_LABELS.get(state),
            }
        )
    return view


def teacher_speaking(group_id, speaking_public_id):
    """``(assignment, activity)`` for one Speaking activity by **its own**
    ``public_id``, constrained to `group_id`, or ``None``.

    Addressing the activity by the extension row's identifier rather than
    the Assignment's is what makes "Speaking routes can never reach an
    ordinary Assignment" a structural property instead of a check somebody
    could forget: an ordinary Assignment has no ``speaking_activities``
    row, so its ``public_id`` resolves to nothing here at all. The Group
    constraint does the same for a Speaking activity belonging to another
    Group, and the route turns both into the identical non-disclosing 404.

    Returns ORM rows: the detail page needs ``instructions``, and the
    write paths need the internal ids and the lifecycle columns.
    """
    return (
        db.session.query(Assignment, SpeakingActivity)
        .join(SpeakingActivity, SpeakingActivity.assignment_id == Assignment.id)
        .filter(
            SpeakingActivity.public_id == speaking_public_id,
            Assignment.group_id == group_id,
        )
        .first()
    )


def build_speaking_detail(assignment, activity, tz_name, reference_utc):
    """One plain presentation dict for a Speaking activity, for a
    **Teacher** page.

    Carries the instructions because a Teacher assigned to the Group may
    always read what they authored. No internal id, no Student identity
    and no recording metadata of any kind.
    """
    state = derived_state(
        assignment.status, assignment.opens_at, assignment.due_at, reference_utc
    )
    return {
        "public_id": activity.public_id,
        "title": assignment.title,
        "instructions": assignment.instructions,
        "status": assignment.status,
        "is_published": assignment.status == _PUBLISHED,
        "opens_local": to_app_local(tz_name, assignment.opens_at),
        "due_local": to_app_local(tz_name, assignment.due_at),
        "published_local": (
            to_app_local(tz_name, assignment.published_at)
            if assignment.published_at is not None
            else None
        ),
        "created_local": to_app_local(tz_name, activity.created_at),
        "updated_local": to_app_local(tz_name, activity.updated_at),
        "state": state,
        "state_label": STATE_LABELS.get(state),
    }


# ---------------------------------------------------------------------------
# Publication readiness
# ---------------------------------------------------------------------------


def speaking_publication_blockers(assignment, activity):
    """Every reason this Speaking activity may not be published, as
    Teacher-facing sentences. An empty list means it is ready.

    Returns **all** failures rather than the first, and the read-only
    readiness panel calls exactly this function, so the page a Teacher
    reads and the rule that decides can never disagree. The caller runs it
    against the **locked** rows.

    The rules are the M01 Assignment ones, restated as sentences rather
    than re-derived: a title, instructions and a valid window
    ``opens_at < due_at``. The window rule is additionally a database
    CHECK and a form validator; it is repeated here because a Teacher
    reading a readiness panel deserves to be told what is missing, and
    because publication must never depend on a check that only ran in a
    form.
    """
    blockers = []
    if activity is None or assignment is None:
        return [
            "This speaking activity could not be read. Please reload the page and try again."
        ]
    if not (assignment.title or "").strip():
        blockers.append("This speaking activity needs a title before it can be published.")
    if not (assignment.instructions or "").strip():
        blockers.append(
            "This speaking activity needs instructions telling students what to say before it "
            "can be published."
        )
    if assignment.opens_at is None or assignment.due_at is None:
        blockers.append(
            "This speaking activity needs both an opening time and a deadline before it can "
            "be published."
        )
    elif assignment.opens_at >= assignment.due_at:
        blockers.append(
            "The deadline must be after the opening time before this speaking activity can be "
            "published."
        )
    return blockers


# ---------------------------------------------------------------------------
# Teacher submission review
# ---------------------------------------------------------------------------


#: The metadata every Teacher submission row is presented with. Selected
#: as explicit **columns**, not as whole ORM entities: the list shows a
#: name, a time and a link, so loading the whole ``users`` row
#: (``password_hash``, ``email``, ``auth_version``, ...) for every row of
#: every page would be fetching secrets nothing renders.
_SUBMISSION_METADATA = (
    SpeakingSubmission.public_id,
    SpeakingSubmission.submitted_at,
    User.full_name.label("student_name"),
)


def _teacher_submission_query(activity_id):
    """The one shared base query behind both Teacher submission reads.

    Defined once so the list and the detail page can never disagree about
    which rows exist: the same ``speaking_activity_id`` scoping and the
    same Student-role integrity filter back both. The ``users`` join is
    not redundant with the foreign key -- it is what proves the row really
    is a Student's work -- and it also supplies the display name in the
    same statement, so neither read lazy-loads a User per row.

    ``SpeakingSubmission.id`` is deliberately **not** projected. It is
    still used inside the SQL as the list's final ``ORDER BY`` tie-break,
    so the internal id orders the statement without ever leaving it.
    """
    return (
        db.session.query(*_SUBMISSION_METADATA)
        .join(User, SpeakingSubmission.student_id == User.id)
        .filter(
            SpeakingSubmission.speaking_activity_id == activity_id,
            User.role == _STUDENT,
        )
    )


def teacher_speaking_submissions_page(activity_id, page):
    """One bounded page of an activity's recordings, most recent first.

    Returns ``(rows, has_next)``. Ordering is ``submitted_at DESC,
    id DESC`` -- fully deterministic even when two recordings share a
    timestamp, which on MySQL's second-precision ``DATETIME`` is not a
    remote possibility. Its column order mirrors
    ``ix_speaking_submissions_activity_submitted_id``, whose ordering
    columns follow the ``speaking_activity_id`` equality directly -- but
    no MySQL plan has been measured, so that is a reasoned design, not a
    proven index-ordered read.

    Fetches ``PAGE_SIZE + 1`` rows and drops the extra, so "is there a
    next page" costs no second query and discloses no total count. There
    is deliberately no ``COUNT`` and no pending-review metric.

    Authorization is **not** performed here: the Teacher route has already
    proven an active ``GroupTeacherAssignment`` to the activity's exact
    Group and passes the nested-verified internal activity id.
    """
    rows = (
        _teacher_submission_query(activity_id)
        .order_by(SpeakingSubmission.submitted_at.desc(), SpeakingSubmission.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def teacher_speaking_submission(activity_id, submission_public_id):
    """One SpeakingSubmission ORM row by its own public id, constrained to
    the already-authorized activity -- or ``None``.

    A submission public id that is valid only under another activity (and
    therefore possibly another Group) produces no row here, and the route
    turns that into the same non-disclosing 404 as a missing one. The
    Student-role integrity filter applies here too: a row whose
    ``student_id`` does not name a Student is not presented as Student
    work.

    Returns the ORM row because the detail page, the audio route and the
    feedback editor all need its internal ids.
    """
    return (
        db.session.query(SpeakingSubmission)
        .join(User, SpeakingSubmission.student_id == User.id)
        .filter(
            SpeakingSubmission.speaking_activity_id == activity_id,
            SpeakingSubmission.public_id == submission_public_id,
            User.role == _STUDENT,
        )
        .first()
    )


def student_name_for_submission(submission):
    """The display name of the Student who made this recording, or
    ``None`` when the row's ``student_id`` no longer names a Student.

    One bounded, column-projected lookup. Fails closed rather than
    rendering an unknown account's work as a Student's: the caller turns
    ``None`` into the same non-disclosing 404.
    """
    row = (
        db.session.query(User.full_name, User.role)
        .filter(User.id == submission.student_id)
        .first()
    )
    if row is None or row.role != _STUDENT:
        return None
    return row.full_name


def build_teacher_submission_item(row, tz_name):
    """One plain presentation dict for a Teacher-visible recording.

    Display strings, one localized time and public ids only -- no ORM row,
    no internal id, no email, no storage key, no digest, no byte size and
    no review/score/feedback field, because none exists.
    """
    return {
        "public_id": row.public_id,
        "student_name": row.student_name,
        "submitted_local": to_app_local(tz_name, row.submitted_at),
    }


def build_teacher_submission_view(rows, tz_name):
    """:func:`build_teacher_submission_item` over one page of rows."""
    return [build_teacher_submission_item(row, tz_name) for row in rows]


# ---------------------------------------------------------------------------
# Student reads -- the WHERE clause IS the authorization
# ---------------------------------------------------------------------------


def student_speaking_query(student_id, reference_utc):
    """The one fully scoped base query behind every Student Speaking read.

    Its ``WHERE`` clause is the complete effective-visibility formula --
    this Student, with the Student role and an active account, an
    **active** ``Enrollment`` in the Group, the whole academic chain
    active, the Assignment ``published`` and its ``opens_at`` reached --
    scoped to Assignments that carry the Speaking extension. It is
    deliberately the *same* formula
    ``assignment_queries._visible_assignment_query`` applies, with the
    extension predicate inverted, so the two surfaces cannot come to
    disagree about who may see what.

    The extension row is joined so the activity's own ``public_id`` comes
    back in the same statement, never as a second lookup per row.

    A **past-due** activity stays visible on purpose: what ``due_at``
    withdraws is the ability to submit a first recording, never the
    ability to reach a receipt.
    """
    return (
        db.session.query(Assignment, Group, Course, Level, AcademicTerm, SpeakingActivity)
        .join(Group, Assignment.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .join(User, Enrollment.student_id == User.id)
        .join(SpeakingActivity, SpeakingActivity.assignment_id == Assignment.id)
        .filter(
            User.id == student_id,
            User.role == _STUDENT,
            User.status == UserStatus.ACTIVE.value,
            Enrollment.status == _ENROLLMENT_ACTIVE,
            AcademicTerm.status == _ACTIVE,
            Level.status == _ACTIVE,
            Course.status == _ACTIVE,
            Group.status == _ACTIVE,
            Assignment.status == _PUBLISHED,
            Assignment.opens_at <= reference_utc,
        )
    )


def student_speaking_page(student_id, reference_utc, page):
    """One bounded page of the Speaking activities this Student may
    currently see: open work by nearest deadline, then past-due work most
    recent first.

    The bucketing is ``assignment_queries.student_list_order``, reused
    verbatim rather than restated -- a Student should not meet two
    different orderings for two kinds of work with the same deadline
    semantics. ``LIMIT PAGE_SIZE + 1`` supplies the has-next flag with no
    ``COUNT`` and no disclosed total.
    """
    rows = (
        student_speaking_query(student_id, reference_utc)
        .order_by(*student_list_order(reference_utc))
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def student_speaking(student_id, group_public_id, speaking_public_id, reference_utc):
    """One visible Speaking activity plus its authorized hierarchy
    context, or ``None``.

    Adds only the two nested public-id predicates to the shared visibility
    query, so a draft, a not-yet-open activity, an activity whose
    ``public_id`` belongs to another Group, an **ordinary Assignment's**
    ``public_id``, a withdrawn or missing Enrollment, an archived ancestor
    and a simply non-existent id all produce no row -- and the route turns
    every one of them into the identical non-disclosing 404.
    """
    return (
        student_speaking_query(student_id, reference_utc)
        .filter(
            SpeakingActivity.public_id == speaking_public_id,
            Group.public_id == group_public_id,
        )
        .first()
    )


def student_speaking_submission(activity_id, student_id):
    """This Student's own recording for this activity, or ``None``.

    Both identifiers are required and both are applied in SQL. The pair is
    exactly ``uq_speaking_submissions_activity_student``, so this is a
    unique lookup by its own constraint and can never return more than one
    row. Never look a Speaking submission up by ``speaking_activity_id``
    alone when rendering a Student page -- that is how one Student's
    recording reaches another's screen.
    """
    return SpeakingSubmission.query.filter(
        SpeakingSubmission.speaking_activity_id == activity_id,
        SpeakingSubmission.student_id == student_id,
    ).first()


def build_student_speaking_item(row, tz_name, reference_utc, submitted_ids=frozenset()):
    """One plain presentation dict for a Student-visible Speaking activity.

    `row` is the ``(Assignment, Group, Course, Level, AcademicTerm,
    SpeakingActivity)`` tuple returned by the queries above. Only display
    strings, localized times and public ids -- no ORM row, no Teacher
    identity, no recording metadata and no internal id.
    """
    assignment, group, course, level, term, activity = row
    state = derived_state(
        assignment.status, assignment.opens_at, assignment.due_at, reference_utc
    )
    return {
        "public_id": activity.public_id,
        "group_public_id": group.public_id,
        "title": assignment.title,
        "instructions": assignment.instructions,
        "group_name": group.name,
        "course_title": course.title,
        "level_name": level.name,
        "term_name": term.name,
        "opens_local": to_app_local(tz_name, assignment.opens_at),
        "due_local": to_app_local(tz_name, assignment.due_at),
        "state": state,
        "state_label": STATE_LABELS.get(state),
        "has_submitted": activity.id in submitted_ids,
    }


def student_submitted_activity_ids(activity_ids, student_id):
    """The subset of `activity_ids` this Student has already submitted a
    recording for -- **one** bounded query for a whole page.

    The Student list shows a Submitted / Not submitted badge per row;
    asking once per row would be exactly the N+1 this project forbids. The
    input is one page of ids (at most :data:`PAGE_SIZE`), so the ``IN``
    list is bounded by construction, and the ``student_id`` predicate is
    what keeps one Student from learning anything about another's work.
    """
    ids = [i for i in activity_ids if i is not None]
    if not ids:
        return frozenset()
    rows = (
        db.session.query(SpeakingSubmission.speaking_activity_id)
        .filter(
            SpeakingSubmission.speaking_activity_id.in_(ids),
            SpeakingSubmission.student_id == student_id,
        )
        .all()
    )
    return frozenset(row[0] for row in rows)


def build_student_speaking_view(rows, tz_name, reference_utc, submitted_ids):
    """:func:`build_student_speaking_item` over one page of rows."""
    return [
        build_student_speaking_item(row, tz_name, reference_utc, submitted_ids)
        for row in rows
    ]


def build_student_receipt(submission, tz_name):
    """The plain presentation dict for a Student's own recording, or
    ``None``.

    Public id and one localized time only. The ``storage_key``, the
    resolved filesystem path, the SHA-256 digest, the byte size, the
    uploader identity and every internal id are deliberately absent --
    none of them is information the page needs, and each of them is
    something the audio route must never disclose. The playback URL is
    built by the blueprint from the public identifiers already in the URL.
    """
    if submission is None:
        return None
    return {
        "public_id": submission.public_id,
        "submitted_local": to_app_local(tz_name, submission.submitted_at),
    }


# ---------------------------------------------------------------------------
# Feedback -- one row per recording, reviewer-role integrity in SQL
# ---------------------------------------------------------------------------


def _reviewer_join(query, reviewer):
    """The ``users`` join behind every feedback read here.

    Not redundant with the foreign key: it is what supplies the display
    name *and* proves, in the same statement, that the reviewer really is
    a Teacher. Doing it in the join rather than in a second query is also
    what keeps a page's query count independent of how many rows it shows.
    """
    return query.join(reviewer, reviewer.id == SpeakingFeedback.reviewer_id)


def feedback_states_for_page(activity_id, submission_public_ids):
    """``{submission public_id: state}`` for one already-fetched page of
    recordings -- **one** bounded query for the whole page.

    Mirrors ``submission_feedback_queries.feedback_states_for_page``
    exactly, against the Speaking tables. The input is one page of
    **public** ids (at most ``PAGE_SIZE``), so the ``IN`` list is bounded
    by construction, and ``activity_id`` re-scopes the lookup to the
    activity the route authorized. Only rows that **exist** appear in the
    mapping; a recording with no feedback is simply absent from it, and
    the caller resolves that to ``FEEDBACK_ABSENT``. ``feedback_text`` is
    deliberately not selected: the list shows a badge, not a body.
    """
    ids = [pid for pid in submission_public_ids if pid]
    if not ids:
        return {}
    reviewer = aliased(User)
    rows = (
        _reviewer_join(
            db.session.query(
                SpeakingSubmission.public_id.label("submission_public_id"),
                (reviewer.role == _TEACHER).label("reviewer_is_teacher"),
            )
            .select_from(SpeakingFeedback)
            .join(
                SpeakingSubmission,
                SpeakingSubmission.id == SpeakingFeedback.speaking_submission_id,
            ),
            reviewer,
        )
        .filter(
            SpeakingSubmission.speaking_activity_id == activity_id,
            SpeakingSubmission.public_id.in_(ids),
        )
        .all()
    )
    return {
        row.submission_public_id: feedback_state(True, bool(row.reviewer_is_teacher))
        for row in rows
    }


def attach_feedback_states(items, states):
    """Add the derived ``feedback_state`` to each already-built Teacher
    list item, in place, and return the list. A recording the metadata
    query returned nothing for resolves to ``FEEDBACK_ABSENT``."""
    for item in items:
        item["feedback_state"] = states.get(item["public_id"], FEEDBACK_ABSENT)
    return items


def teacher_speaking_feedback(activity_id, submission_public_id):
    """The feedback on one recording of one already-authorized activity,
    or ``None`` -- **one** row, by its own unique constraint.

    Scoped by ``activity_id`` **and** the submission's public id together,
    so a submission public id valid only under another activity (and
    therefore possibly another Group) finds nothing here.

    Carries ``version`` and the feedback's own ``public_id`` because the
    Teacher editor binds its signed stale-form token to both. Neither is
    an internal id: ``version`` is a small counter and ``public_id`` is a
    UUID. The labels match what
    ``submission_feedback_queries.build_feedback_panel`` reads, so that
    builder is reused rather than duplicated.
    """
    reviewer = aliased(User)
    return (
        _reviewer_join(
            db.session.query(
                SpeakingFeedback.public_id.label("feedback_public_id"),
                SpeakingFeedback.feedback_text,
                SpeakingFeedback.version,
                SpeakingFeedback.updated_at,
                reviewer.full_name.label("reviewer_name"),
                (reviewer.role == _TEACHER).label("reviewer_is_teacher"),
            )
            .select_from(SpeakingFeedback)
            .join(
                SpeakingSubmission,
                SpeakingSubmission.id == SpeakingFeedback.speaking_submission_id,
            ),
            reviewer,
        )
        .filter(
            SpeakingSubmission.speaking_activity_id == activity_id,
            SpeakingSubmission.public_id == submission_public_id,
        )
        .first()
    )


def student_speaking_feedback(speaking_submission_id):
    """The feedback on this Student's own recording, or ``None``.

    A fixed, bounded lookup: the argument is the internal id of the
    submission the route has already proven belongs to **both** the
    currently visible activity and the authenticated Student, and
    ``uq_speaking_feedback_submission`` makes the result at most one row.

    ``version`` and the feedback's ``public_id`` are deliberately not
    selected: the Student receipt is read-only, has no form and no token,
    and needs neither.
    """
    reviewer = aliased(User)
    return (
        _reviewer_join(
            db.session.query(
                SpeakingFeedback.feedback_text,
                SpeakingFeedback.updated_at,
                reviewer.full_name.label("reviewer_name"),
                (reviewer.role == _TEACHER).label("reviewer_is_teacher"),
            ).select_from(SpeakingFeedback),
            reviewer,
        )
        .filter(SpeakingFeedback.speaking_submission_id == speaking_submission_id)
        .first()
    )
