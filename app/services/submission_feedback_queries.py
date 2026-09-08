"""Read-only query layer for Teacher feedback on Submissions
(Phase 4 / M03).

Flask-independent -- plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring
``app/services/submission_queries.py``. **Every function here is
read-only**: no locks, no writes. The locking write path lives in
``app/blueprints/teacher/feedback.py``, which owns the single transaction
reset and the route-specific lock order.

**Authorization is never performed here.** Each function is handed an
identifier a route has already authorized -- the internal Assignment id
the Teacher holds an active ``GroupTeacherAssignment`` for, or the
internal Submission id the Student has already proven the whole M01/M02
visibility formula against. These functions only *find the feedback for
it*.

**Conditional reviewer-role integrity, in SQL, on every read.**
``submission_feedback.reviewer_id`` is a foreign key into ``users``,
which proves the referenced row exists and nothing more -- not that it is
a Teacher's. Every read here therefore joins ``users`` and returns the
role check as an explicit ``reviewer_is_teacher`` boolean rather than
filtering the row away. Presentation then **fails closed**: a
role-inconsistent row is reported as an integrity problem, never
rendered, and -- crucially -- never reported as "no feedback yet", which
would invite a Teacher to write a duplicate the unique constraint would
reject anyway. That is the project's standing conditional-integrity rule
applied to a second referencing column.

**Bounded rows, not just bounded row counts.** Every function selects
explicit **columns** and returns either a labelled column row or a plain
dict, so no ORM row reaches a template and rendering feedback can never
trigger a lazy load. No ``users`` column beyond ``full_name`` (and the
role *comparison*, which never leaves the database as a value) is
fetched: password hashes, email, ``auth_version`` and account status are
nothing these pages display.

**No unbounded read exists here.** ``uq_submission_feedback_submission``
makes every single-Submission lookup return at most one row by its own
constraint, and the Teacher list helper takes one already-fetched page of
Submission public ids -- at most ``PAGE_SIZE`` of them -- as its ``IN``
list, exactly as ``assignment_ids_with_submissions`` does for the M02
freeze badge. There is deliberately no feedback history read, no
feedback-by-Teacher read, no review queue and no counter.
"""

from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import Submission, SubmissionFeedback, User, UserRole
from app.services.schedule_occurrences import to_app_local

_TEACHER = UserRole.TEACHER.value

#: The three feedback presentation states every page derives -- from the
#: existence of a row and the integrity of its reviewer, never from a
#: stored status column (none exists, and none is wanted: a stored review
#: state is the first step towards a grading workflow this milestone does
#: not deliver).
FEEDBACK_ABSENT = "absent"
FEEDBACK_PRESENT = "present"
#: A row exists, but ``reviewer_id`` does not name a Teacher. Reported as
#: its own state so it can fail closed: it is neither shown as feedback
#: nor misreported as absent.
FEEDBACK_INVALID = "invalid"


def _reviewer_join(query, reviewer):
    """The ``users`` join behind every read here.

    Not redundant with the foreign key: it is what supplies the display
    name *and* proves, in the same statement, that the reviewer really is
    a Teacher. Doing it in the join rather than in a second query is also
    what keeps a page's query count independent of how many rows it
    shows.
    """
    return query.join(reviewer, reviewer.id == SubmissionFeedback.reviewer_id)


def feedback_state(has_row, reviewer_is_teacher):
    """The derived state for one Submission -- the single place the three
    constants above are decided, so the Teacher list, the Teacher detail
    page and the Student receipt can never classify the same row
    differently."""
    if not has_row:
        return FEEDBACK_ABSENT
    return FEEDBACK_PRESENT if reviewer_is_teacher else FEEDBACK_INVALID


# ---------------------------------------------------------------------------
# Teacher -- one bounded page-level metadata query, never one query per row
# ---------------------------------------------------------------------------


def feedback_states_for_page(assignment_id, submission_public_ids):
    """``{submission public_id: state}`` for one already-fetched page of
    Submissions -- **one** bounded query for the whole page.

    The Teacher submission list needs a "Feedback provided" / "Awaiting
    feedback" indicator per row; asking once per row would be exactly the
    N+1 this project forbids, and joining the flag into
    ``teacher_submissions_page`` would change that projection's
    established M02 contract. This mirrors
    ``submission_queries.assignment_ids_with_submissions``, which solved
    the same shape for the M02 freeze badge.

    The input is one page of **public** ids (at most ``PAGE_SIZE`` of
    them), so the ``IN`` list is bounded by construction, and the result
    is keyed by public id so no internal Submission id has to leave the
    query layer to make the join. `assignment_id` re-scopes the lookup to
    the Assignment the route authorized, so a Submission public id from
    another Assignment could not contribute a row even if one were
    somehow passed in.

    Only rows that **exist** appear in the mapping; a Submission with no
    feedback is simply absent from it, and the caller resolves that to
    :data:`FEEDBACK_ABSENT`. ``feedback_text`` is deliberately **not**
    selected: the list shows a badge, not a body.

    The statement is deliberately rooted at ``submission_feedback`` (the
    sparse side) rather than at ``submissions``: this is a lookup of the
    feedback that exists for a known page, not a second scan of the
    submission history the page already fetched.
    """
    ids = [pid for pid in submission_public_ids if pid]
    if not ids:
        return {}
    reviewer = aliased(User)
    rows = (
        _reviewer_join(
            db.session.query(
                Submission.public_id.label("submission_public_id"),
                (reviewer.role == _TEACHER).label("reviewer_is_teacher"),
            )
            .select_from(SubmissionFeedback)
            .join(Submission, Submission.id == SubmissionFeedback.submission_id),
            reviewer,
        )
        .filter(
            Submission.assignment_id == assignment_id,
            Submission.public_id.in_(ids),
        )
        .all()
    )
    return {
        row.submission_public_id: feedback_state(True, bool(row.reviewer_is_teacher))
        for row in rows
    }


def teacher_feedback(assignment_id, submission_public_id):
    """The feedback on one Submission of one already-authorized
    Assignment, or ``None`` -- **one** row, by its own unique constraint.

    Scoped by ``assignment_id`` **and** the Submission's public id
    together, so a Submission public id valid only under another
    Assignment (and therefore possibly another Group) finds nothing here,
    exactly as ``submission_queries.teacher_submission`` behaves.

    Carries ``version`` and the feedback's own ``public_id`` because the
    Teacher editor binds its signed stale-form token to both. Neither is
    an internal id: ``version`` is a small counter and ``public_id`` is a
    UUID.
    """
    reviewer = aliased(User)
    return (
        _reviewer_join(
            db.session.query(
                SubmissionFeedback.public_id.label("feedback_public_id"),
                SubmissionFeedback.feedback_text,
                SubmissionFeedback.version,
                SubmissionFeedback.updated_at,
                reviewer.full_name.label("reviewer_name"),
                (reviewer.role == _TEACHER).label("reviewer_is_teacher"),
            )
            .select_from(SubmissionFeedback)
            .join(Submission, Submission.id == SubmissionFeedback.submission_id),
            reviewer,
        )
        .filter(
            Submission.assignment_id == assignment_id,
            Submission.public_id == submission_public_id,
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Student -- one fixed, bounded lookup for a receipt already authorized
# ---------------------------------------------------------------------------


def student_feedback(submission_id):
    """The feedback on this Student's own Submission, or ``None``.

    A fixed, bounded lookup: the argument is the internal id of the
    Submission the route has already proven belongs to **both** the
    currently visible Assignment and the authenticated Student, and
    ``uq_submission_feedback_submission`` makes the result at most one
    row. There is no history read and no unbounded load.

    ``version`` and the feedback's ``public_id`` are deliberately not
    selected: the Student receipt is read-only, has no form and no token,
    and needs neither.
    """
    reviewer = aliased(User)
    return (
        _reviewer_join(
            db.session.query(
                SubmissionFeedback.feedback_text,
                SubmissionFeedback.updated_at,
                reviewer.full_name.label("reviewer_name"),
                (reviewer.role == _TEACHER).label("reviewer_is_teacher"),
            ).select_from(SubmissionFeedback),
            reviewer,
        )
        .filter(SubmissionFeedback.submission_id == submission_id)
        .first()
    )


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def build_feedback_panel(row, tz_name, include_version=False):
    """The plain presentation dict for one feedback row, or ``None``.

    ``state`` is the derived classification; ``text`` and
    ``reviewer_name`` are present **only** in the
    :data:`FEEDBACK_PRESENT` state, so a role-inconsistent row cannot
    reach a page even by template accident -- the values simply are not
    in the dict. `include_version` adds the two fields only the Teacher
    editor's signed token needs.

    The text is returned as plain text for the template to escape. It is
    never HTML, never Markdown, and never rendered with ``|safe``.
    """
    if row is None:
        return {"state": FEEDBACK_ABSENT}
    state = feedback_state(True, bool(row.reviewer_is_teacher))
    if state != FEEDBACK_PRESENT:
        return {"state": state}
    panel = {
        "state": state,
        "text": row.feedback_text,
        "reviewer_name": row.reviewer_name,
        "updated_local": to_app_local(tz_name, row.updated_at),
    }
    if include_version:
        panel["public_id"] = row.feedback_public_id
        panel["version"] = row.version
    return panel


def attach_feedback_states(items, states):
    """Add the derived ``feedback_state`` to each already-built Teacher
    list item, in place, and return the list.

    Kept here rather than inside ``submission_queries``'s builders so the
    M02 Submission presentation contract stays exactly as M02 wrote it:
    the list row is built from its own bounded projection, and the M03
    indicator is merged onto it afterwards from the one page-level
    metadata query. A Submission the metadata query returned nothing for
    resolves to :data:`FEEDBACK_ABSENT` -- the only place "no row" becomes
    "awaiting feedback", and it can never absorb the
    :data:`FEEDBACK_INVALID` state, which the query reports explicitly.
    """
    for item in items:
        item["feedback_state"] = states.get(item["public_id"], FEEDBACK_ABSENT)
    return items
