"""Read-only query layer for immutable text Submissions (Phase 4 / M02).

Flask-independent -- plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring
``app/services/assignment_queries.py``. **Every function here is
read-only**: no locks, no writes. The locking write path lives in
``app/blueprints/student/assignments.py``, which owns the single
transaction reset and the route-specific lock order.

Three separate audiences, three deliberately different scoping rules:

- **The Student receipt** (:func:`student_submission`) is constrained by
  ``assignment_id`` **and** ``student_id`` together. Never join
  ``submissions`` by ``assignment_id`` alone when rendering a Student
  page -- that is how one Student's answer reaches another's screen. The
  route has already proven the Assignment is currently visible to that
  Student before calling; this function only finds *their own* row under
  it.
- **The Teacher history reads** (:func:`teacher_submissions_page`,
  :func:`teacher_submission`) are scoped to one ``assignment_id`` the
  route has already authorized through an active
  ``GroupTeacherAssignment`` to the Assignment's exact Group. They are
  deliberately **not** filtered by current Enrollment or by the Student's
  current account status: a Teacher must still be able to read the work
  of a Student who has since been withdrawn or suspended, and history
  must stay readable under an archived chain, an unpublished Assignment
  and a passed deadline. They *are* filtered by ``User.role ==
  'student'``: a foreign key into ``users`` proves the row exists, never
  that it belongs to a Student, so a corrupted row is excluded from
  anything presented as Student work -- the project's standing
  conditional-integrity rule.
- **The Assignment edit freeze** (:func:`assignment_has_submissions`,
  :func:`assignment_ids_with_submissions`) asks a different question
  entirely: *did this Assignment ever receive work?* It therefore applies
  **no** role, Enrollment, account-status or visibility filter at all. A
  withdrawn Student's row, a suspended Student's row and even a row whose
  ``student_id`` points at a non-Student User all freeze the Assignment,
  because all three are evidence that its wording and its time window
  have already been acted on.

Every row that reaches a template is converted to a plain presentation
dict first, so rendering a submission can never trigger a lazy load or an
ORM-driven authorization decision, and no internal id is ever carried
across that boundary. Ordering is fully deterministic and expressed in
SQL, tie-broken by the internal ``id`` inside the SQL only. Every list is
bounded: ``PAGE_SIZE + 1`` rows to derive a non-disclosing "there is a
next page" flag without a COUNT. Nothing here calls ``.all()`` on an
unbounded history or sorts in Python.
"""

from app.extensions import db
from app.models import Submission, User, UserRole
from app.services.assignment_queries import PAGE_SIZE
from app.services.schedule_occurrences import to_app_local
from app.services.assignment_files import file_metadata

_STUDENT = UserRole.STUDENT.value


# ---------------------------------------------------------------------------
# Student -- the personal receipt, always scoped by BOTH identifiers
# ---------------------------------------------------------------------------


def student_submission(assignment_id, student_id):
    """This Student's own Submission for this Assignment, or ``None``.

    Both identifiers are required and both are applied in SQL. The pair
    is exactly ``uq_submissions_assignment_student``, so this is a unique
    lookup by its own constraint and can never return more than one row.
    """
    return Submission.query.filter(
        Submission.assignment_id == assignment_id,
        Submission.student_id == student_id, active_episode_record(Submission),
    ).first()


def build_student_receipt(submission, tz_name):
    """The plain presentation dict for a Student's own submission, or
    ``None``. Public id and localized time only -- no ORM row, no
    internal id, and the answer as plain text the template escapes."""
    if submission is None:
        return None
    return {
        "public_id": submission.public_id,
        "answer_text": submission.answer_text,
        "file": file_metadata(submission.uploaded_file_id, submission.student_id),
        "submitted_local": to_app_local(tz_name, submission.submitted_at),
    }


# ---------------------------------------------------------------------------
# Assignment edit freeze -- historical existence, no filters at all
# ---------------------------------------------------------------------------


def assignment_has_submissions(assignment_id):
    """True if **any** Submission row exists for this Assignment.

    Deliberately unfiltered. Current Student eligibility is irrelevant:
    a withdrawn or suspended Student's row, and even a row with broken
    conditional Student-role integrity, still means the Assignment's
    wording and time window have been acted on and must no longer change.
    Publication is equally irrelevant -- unpublishing an Assignment does
    not un-submit anything.

    Used both as the Teacher's helpful pre-lock check and, inside the
    locked transaction, as the authoritative check before any field is
    assigned.
    """
    return (
        db.session.query(Submission.id)
        .filter(Submission.assignment_id == assignment_id)
        .first()
        is not None
    )


def assignment_ids_with_submissions(assignment_ids):
    """The subset of `assignment_ids` that already have submission
    history, as a set -- **one** bounded query for a whole page.

    The Teacher Assignment list needs the freeze flag per row; asking
    once per row would be exactly the N+1 this project forbids. The input
    is one page of ids (at most :data:`PAGE_SIZE`), so the ``IN`` list is
    bounded by construction.
    """
    ids = [i for i in assignment_ids if i is not None]
    if not ids:
        return frozenset()
    rows = (
        db.session.query(Submission.assignment_id)
        .filter(Submission.assignment_id.in_(ids))
        .distinct()
        .all()
    )
    return frozenset(row[0] for row in rows)


# ---------------------------------------------------------------------------
# Teacher -- read-only history for one already-authorized Assignment
# ---------------------------------------------------------------------------


#: The metadata every Teacher submission row is presented with. Selected
#: as explicit **columns**, not as whole ORM entities: the list shows a
#: name, a time and a link, so loading ``submissions.answer_text`` and the
#: whole ``users`` row (``password_hash``, ``email``, ``auth_version``,
#: ...) for every row of every page would be fetching secrets and bodies
#: nothing renders. The label is what lets both builders read the row by
#: name regardless of which projection produced it.
_SUBMISSION_METADATA = (
    Submission.public_id,
    Submission.submitted_at,
    User.full_name.label("student_name"),
)


def _teacher_submission_query(assignment_id, *extra_columns):
    """The one shared base query behind both Teacher reads.

    Defined once so the list and the detail page can never disagree about
    which rows exist: the same ``assignment_id`` scoping and the same
    Student-role integrity filter back both. The ``users`` join is not
    redundant with the foreign key -- it is what proves the row really is
    a Student's work -- and it also supplies the display name in the same
    statement, so neither read lazy-loads a User per row.

    Only the projection differs: the list takes
    :data:`_SUBMISSION_METADATA` alone, the detail page adds
    ``answer_text`` through `extra_columns`. Because both select plain
    columns rather than ORM entities, there is no deferred attribute left
    behind that a template could touch and turn into a second query.

    ``Submission.id`` is deliberately **not** projected. It is still used
    inside the SQL as the list's final ``ORDER BY`` tie-break -- ordering
    by an unselected column is ordinary SQL here (no ``DISTINCT`` is
    involved) -- so the internal id orders the statement without ever
    leaving it.
    """
    return (
        db.session.query(*_SUBMISSION_METADATA, *extra_columns)
        .join(User, Submission.student_id == User.id)
        .filter(Submission.assignment_id == assignment_id, User.role == _STUDENT)
    )


def teacher_submissions_page(assignment_id, page):
    """One bounded page of an Assignment's submissions, most recent
    first.

    Returns ``(rows, has_next)`` where each row carries only
    :data:`_SUBMISSION_METADATA` -- ``public_id``, ``submitted_at`` and
    ``student_name``. No answer body and no ``users`` column beyond the
    display name is fetched. Ordering is ``submitted_at DESC,
    id DESC`` -- fully deterministic even when two submissions share a
    timestamp, which on MySQL's second-precision ``DATETIME`` is not a
    remote possibility. Its column order mirrors
    ``ix_submissions_assignment_submitted_id``, whose ordering columns
    follow the ``assignment_id`` equality directly -- but no MySQL plan
    has been measured, so that is a reasoned design, not a proven
    index-ordered read.

    Fetches ``PAGE_SIZE + 1`` rows and drops the extra, so "is there a
    next page" costs no second query and discloses no total count. There
    is deliberately no COUNT and no pending-review metric.

    Authorization is **not** performed here: the Teacher route has
    already proven an active ``GroupTeacherAssignment`` to the
    Assignment's exact Group and passes the nested-verified internal
    Assignment id.
    """
    rows = (
        _teacher_submission_query(assignment_id)
        .order_by(Submission.submitted_at.desc(), Submission.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)
        .all()
    )
    has_next = len(rows) > PAGE_SIZE
    return rows[:PAGE_SIZE], has_next


def teacher_submission(assignment_id, submission_public_id):
    """One Submission by its own public id, constrained to the
    already-authorized Assignment -- or ``None``.

    A Submission public id that is valid only under another Assignment
    (and therefore possibly another Group) produces no row here, and the
    route turns that into the same non-disclosing 404 as a missing one.

    This is the **only** read that fetches ``answer_text``, and it fetches
    exactly one row.
    """
    return (
        _teacher_submission_query(assignment_id, Submission.answer_text, Submission.uploaded_file_id, Submission.student_id)
        .filter(Submission.public_id == submission_public_id)
        .first()
    )


def build_teacher_submission_item(row, tz_name, include_answer=False):
    """One plain presentation dict for a Teacher-visible submission.

    `row` is a labelled column row from :func:`_teacher_submission_query`.
    Display strings, one localized time and public ids only -- no ORM row,
    no internal id, no email, and no review/score/feedback field, because
    none exists. The answer body is read only when `include_answer` is
    set, which only the detail page does and only for a projection that
    actually selected it -- the list row does not carry it at all.
    """
    item = {
        "public_id": row.public_id,
        "student_name": row.student_name,
        "submitted_local": to_app_local(tz_name, row.submitted_at),
    }
    if include_answer:
        item["answer_text"] = row.answer_text
        item["file"] = file_metadata(row.uploaded_file_id, row.student_id)
    return item


def build_teacher_submission_view(rows, tz_name):
    """:func:`build_teacher_submission_item` over a list of rows (list
    page -- no answer bodies)."""
    return [build_teacher_submission_item(row, tz_name) for row in rows]

from app.services.episode_queries import active_episode_record
