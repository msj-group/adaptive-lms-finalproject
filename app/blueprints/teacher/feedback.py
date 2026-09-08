"""Teacher feedback on immutable text Submissions (Phase 4 / M03).

**One** nested GET/POST endpoint,

    /teacher/groups/<group_public_id>/assignments/<assignment_public_id>
        /submissions/<submission_public_id>/feedback

beneath the M02 read pages. GET renders the editor (or a read-only panel
when writing is not currently allowed); POST creates or revises the one
shared feedback record. There is deliberately no delete endpoint, no
draft/publish endpoint, no score or grade field, and no Student-facing
write path of any kind: a Student reads feedback on their own receipt and
cannot reply to it.

**Feedback is a comment, not a grade.** Nothing this module writes means
graded, passed, completed or officially approved, and the Teacher UI says
so before a save. Grades remain an undecided module.

**One record, latest text only.** All actively assigned co-teachers of
the Group are equal collaborators on the *same* row -- feedback is not
privately owned by whoever wrote it first. A revision overwrites the
text, moves ``updated_at``, reassigns ``reviewer_id`` to the acting
Teacher and increments ``version`` by exactly one. Earlier wordings are
not retained anywhere. A save whose normalized text equals the stored
text is a **no-op**: version, timestamps and attribution are all left
alone, because re-saving unchanged wording is not an edit.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403) on entry. Object authorization is then
server-side and reuses the exact helpers every other nested Teacher route
uses: ``_teacher_group_or_404`` proves an **active**
``GroupTeacherAssignment`` to the Group in the URL,
``_assignment_for_group_or_404`` constrains the Assignment to that Group,
and the Submission read is scoped to that verified Assignment. A missing
object, a wrong nested identifier, an unassigned or removed Teacher and
any cross-Group attempt all produce the same non-disclosing **404**.

**A path that has rolled back needs its own evidence.**
``roles_required`` runs once, before the view, and a cached
``current_user`` is not a current read. Once locks are released, the
acting Teacher's account or assignment may have changed inside exactly
that window, and ``_teacher_group_or_404`` alone would not notice: it
proves an active assignment but never re-reads the actor's own ``role``
and ``status``. Every post-rollback path here therefore goes through
:func:`_fresh_teacher_authorization`, which re-proves the actor from
current state using a **scalar id captured before the reset** -- the
editor render, the ordinary-validation re-render, and the
``IntegrityError`` recovery alike -- before any private answer or
feedback body is fetched, any token is minted, or any recovery response
is chosen.

**Reading is historical; writing is not.** Reads follow M02 exactly:
they survive an archived hierarchy, an unpublished Assignment, a passed
deadline, and a Student who has since been withdrawn or suspended.
Writing additionally requires an active acting Teacher with an active
assignment and an active AcademicTerm / Level / Course / Group -- but
deliberately **not** an active Student account or Enrollment, **not** a
published Assignment, **not** an open time window, and **not** a
Schedule: genuine historical work must still be reviewable. Under an
archived chain the page renders read-only and explains why; a forged POST
is rejected server-side against the locked rows, not by the template.

**Conditional role integrity in both directions.** A foreign key into
``users`` proves a row exists, never its role. The Submission owner must
still be a Student for the work to be presented as Student work (M02's
rule, unchanged), and an existing feedback row whose ``reviewer_id`` does
not name a Teacher fails **closed**: it is neither rendered nor treated
as absent, and no write is allowed to silently overwrite or "repair" it.

**Concurrency.** One transaction, one deliberate reset (owned by
``lock_academic_hierarchy``), and the route-specific order documented on
:func:`_lock_feedback_chain`. Every authoritative condition is re-checked
against the locked rows and no field is assigned until all of them pass.
A signed token bound to the *version* -- not to a timestamp -- protects
against a time-separated co-teacher overwrite, including two edits inside
one whole second and an A -> B -> A round trip. ``IntegrityError`` is
caught, rolled back, re-authorized from scratch and reported generically.
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import (
    _assignment_for_group_or_404,
    _private_no_store,
    _tz_name,
)
from app.blueprints.teacher.forms import SubmissionFeedbackForm
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
    Course,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Submission,
    SubmissionFeedback,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.assignment_queries import build_teacher_view
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.schedule_occurrences import utc_reference_now
from app.services.submission_feedback_queries import (
    FEEDBACK_INVALID,
    FEEDBACK_PRESENT,
    build_feedback_panel,
    teacher_feedback,
)
from app.services.submission_queries import (
    build_teacher_submission_item,
    teacher_submission,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: The one Teacher-facing sentence for a role-inconsistent existing
#: feedback row, used by the read and the write path alike so neither can
#: explain the same integrity problem differently. It names no reviewer,
#: quotes no feedback text, and promises no automatic repair.
_INTEGRITY_MESSAGE = (
    "The saved feedback on this submission is linked to an account that is no longer a "
    "teacher, so it cannot be shown or changed here. Nothing has been deleted. Please ask an "
    "administrator to review this record."
)

#: The one sentence for a stale form. Written to send the Teacher back to
#: the current text rather than to encourage a retry of what they typed.
_STALE_MESSAGE = (
    "This feedback was changed by someone else since this form was opened. Your text was not "
    "saved. Please reload, read the current feedback, and write your revision against it."
)


def _write_moment():
    """The **authoritative** naive-UTC moment for one feedback write,
    truncated to whole seconds.

    ``created_at`` / ``updated_at`` are plain ``DateTime`` columns, which
    on MySQL are ``DATETIME`` with fractional precision **0**; MySQL
    *rounds* an excess fraction rather than truncating it, so a value
    carrying microseconds would be stored as a different instant from the
    one the request used. Truncating here makes the two the same on every
    backend.

    Read only **after** every lock that could have blocked, so a request
    that waited behind a competing co-teacher records the moment it
    actually wrote, not the moment it arrived. Deliberately local to this
    route: it wraps the shared
    :func:`~app.services.schedule_occurrences.utc_reference_now` rather
    than changing it.

    Timestamps are **not** the staleness signal -- ``version`` is. Two
    edits inside one whole second are still distinguishable.
    """
    return utc_reference_now().replace(microsecond=0)


# ======================================================================
# URLs
# ======================================================================


def _feedback_url(group_public_id, assignment_public_id, submission_public_id):
    return url_for(
        "teacher.submission_feedback",
        group_public_id=group_public_id,
        assignment_public_id=assignment_public_id,
        submission_public_id=submission_public_id,
    )


def _submission_detail_url(group_public_id, assignment_public_id, submission_public_id):
    return url_for(
        "teacher.submission_detail",
        group_public_id=group_public_id,
        assignment_public_id=assignment_public_id,
        submission_public_id=submission_public_id,
    )


# ======================================================================
# Signed feedback-state token (Phase 4 / M03)
# ======================================================================

_FEEDBACK_STATE_SALT = "teacher.submission-feedback-state.phase4-m03.v1"
_FEEDBACK_STATE_FIELDS = (
    "teacher_public_id",
    "group_public_id",
    "assignment_public_id",
    "submission_public_id",
    "feedback_public_id",
    "version",
)


def _feedback_state_serializer():
    return URLSafeSerializer(
        current_app.config["SECRET_KEY"], salt=_FEEDBACK_STATE_SALT
    )


def _feedback_state_payload(
    teacher_public_id,
    group_public_id,
    assignment_public_id,
    submission_public_id,
    feedback_public_id,
    version,
):
    """The exact state a Teacher's open feedback form was written
    against.

    Row locks alone cannot protect this form: a co-teacher may have
    rewritten the feedback minutes after this form was rendered, and the
    lock the POST takes would happily overwrite their text with a
    revision of an older wording. Binding the save to the row **and the
    version** the Teacher actually read is what closes that.

    ``feedback_public_id`` and ``version`` are both ``None`` -- together
    -- for the explicit *expected absence* state, which is what makes
    "there was no feedback when I opened this form" a signed claim rather
    than an assumption. It is distinguishable from a missing or malformed
    token, which yields no payload at all.

    ``updated_at`` is deliberately **not** bound: whole-second timestamps
    cannot separate two edits inside one second, and ``version`` can.
    ``feedback_text`` is not bound either -- it can be long, the token is
    readable, and the version already identifies the exact row state.

    Only **public** identifiers appear. A signed token is authenticated,
    not encrypted: anyone holding it can read its payload, so no internal
    database id and no feedback text is ever placed in it.
    """
    return {
        "teacher_public_id": teacher_public_id,
        "group_public_id": group_public_id,
        "assignment_public_id": assignment_public_id,
        "submission_public_id": submission_public_id,
        "feedback_public_id": feedback_public_id,
        "version": version,
    }


def _make_feedback_state_token(
    teacher_public_id, group_public_id, assignment_public_id, submission_public_id, panel
):
    """A token for the state `panel` describes -- an existing row's public
    id and version, or the explicit absence state.

    Only ever called with a freshly loaded panel, never with attempted
    form values: a fresh token may only pair with freshly read persisted
    state.
    """
    present = panel.get("state") == FEEDBACK_PRESENT
    return _feedback_state_serializer().dumps(
        _feedback_state_payload(
            teacher_public_id,
            group_public_id,
            assignment_public_id,
            submission_public_id,
            panel.get("public_id") if present else None,
            panel.get("version") if present else None,
        )
    )


def _load_feedback_state(token):
    """The token's payload, or ``None`` for a missing, malformed,
    invalidly signed, or wrong-shaped one.

    The shape check is exact and typed, not merely "is a dict": the four
    identifiers must be strings, and the two state fields must be either
    both ``None`` (expected absence) or a string plus a positive integer.
    ``bool`` is excluded explicitly -- it is a subclass of ``int`` in
    Python, and ``True`` must not be accepted as version 1.

    Every ``None`` returned here is treated exactly like an outdated
    token: rejected, never trusted, and never silently upgraded into an
    absence claim.
    """
    if not token:
        return None
    try:
        payload = _feedback_state_serializer().loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_FEEDBACK_STATE_FIELDS):
        return None
    for field in _FEEDBACK_STATE_FIELDS[:4]:
        if not isinstance(payload[field], str):
            return None
    public_id, version = payload["feedback_public_id"], payload["version"]
    if public_id is None and version is None:
        return payload
    if not isinstance(public_id, str):
        return None
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return None
    return payload


def _feedback_state_is_stale(
    token,
    teacher_public_id,
    group_public_id,
    assignment_public_id,
    submission_public_id,
    current_feedback,
):
    """True when `token` does not exactly describe `current_feedback` as
    read by **this** Teacher under **this** nested chain.

    A cross-Teacher, cross-Group, cross-Assignment or cross-Submission
    token fails on the four identifier fields. A token claiming absence
    when a row now exists -- two co-teachers racing to write the first
    feedback -- fails, and so does the reverse. A token naming an older
    version fails, which is what makes an A -> B -> A round trip and two
    edits inside the same second detectable, and what makes replaying a
    successful save a no-write rejection rather than a second increment.

    `current_feedback` must be the **locked** row on the write path.
    """
    payload = _load_feedback_state(token)
    if payload is None:
        return True
    if payload["teacher_public_id"] != teacher_public_id:
        return True
    if payload["group_public_id"] != group_public_id:
        return True
    if payload["assignment_public_id"] != assignment_public_id:
        return True
    if payload["submission_public_id"] != submission_public_id:
        return True
    if current_feedback is None:
        return payload["feedback_public_id"] is not None or payload["version"] is not None
    return (
        payload["feedback_public_id"] != current_feedback.public_id
        or payload["version"] != current_feedback.version
    )


# ======================================================================
# Locking + post-lock re-checks
# ======================================================================


def _lock_feedback_chain(
    group_public_id,
    term_id,
    level_id,
    course_id,
    teacher_id,
    owner_id,
    assignment_id,
    submission_id,
):
    """Acquire the route-specific Phase 4 / M03 lock order in one open
    transaction:

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group
        -> the involved User rows, ascending internal id
           (acting Teacher and Submission owner)
        -> the acting Teacher's GroupTeacherAssignment
        -> Assignment -> Submission
        -> the existing SubmissionFeedback for that Submission, if any

    This is the shared hierarchy/Group prefix every Group-affecting
    mutation in the project already uses, extended with the rows this
    workflow actually decides on, and it keeps the project-wide "User
    rows in ascending internal id" rule that the Administrator membership
    and account write paths rely on -- which is why two co-teachers
    reviewing two different Students cannot deadlock against each other
    or against a membership change. No existing route's contract is
    redesigned here.

    Because a Teacher Assignment edit, a publication toggle and a Student
    submission all lock the **same** Group and the **same** Assignment
    row, and every membership and Group lifecycle mutation locks the same
    Group, a feedback write serializes against all of them rather than
    racing.

    **What actually serializes two co-teachers racing to write the FIRST
    feedback** is the chain of locks on rows that already exist: both
    requests take the same Group, Assignment and Submission row locks
    *before* either one reads or inserts feedback, so the second waits for
    the first to commit and then re-reads a row that is no longer missing.
    That is the serialization this transaction design relies on.

    The last statement locks by ``submission_id``
    (``uq_submission_feedback_submission``), which for a missing row can
    only take a gap/next-key lock. A gap lock is **not** a mutex:
    MySQL/InnoDB documents that gap locks on the same gap can be held by
    several transactions at once and do not block one another, so
    acquiring one is not by itself what makes competing creators mutually
    exclusive -- see
    https://dev.mysql.com/doc/refman/8.0/en/innodb-locking.html. The
    statement is issued to read the current row under the same
    transaction, not as the exclusion mechanism.

    ``uq_submission_feedback_submission`` remains the final duplicate
    defense behind both of those, and the signed version token is what
    turns a losing race into an explicit "reload and review" rejection
    rather than an overwrite. None of this is claimed to be measured: the
    SQLite test backend can demonstrate none of it.

    Returns ``(hierarchy, group, users, teacher_assignment, assignment,
    submission, feedback)``. Any of them may be ``None`` -- the caller
    must treat that as a business/authorization rejection, roll back, and
    404 or redirect; it must never "keep going".
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = lock_group_in_open_transaction(group_public_id)
    users = {}
    for user_id in sorted({uid for uid in (teacher_id, owner_id) if uid is not None}):
        users[user_id] = User.query.filter_by(id=user_id).with_for_update().first()
    teacher_assignment = None
    if group is not None:
        teacher_assignment = (
            GroupTeacherAssignment.query.filter_by(
                group_id=group.id, teacher_id=teacher_id
            )
            .with_for_update()
            .first()
        )
    assignment = Assignment.query.filter_by(id=assignment_id).with_for_update().first()
    submission = Submission.query.filter_by(id=submission_id).with_for_update().first()
    feedback = (
        SubmissionFeedback.query.filter_by(submission_id=submission_id)
        .with_for_update()
        .first()
    )
    return hierarchy, group, users, teacher_assignment, assignment, submission, feedback


def _nested_ownership_broken(
    group,
    assignment,
    submission,
    owner,
    assignment_public_id,
    submission_public_id,
):
    """True when the **locked** rows no longer form the exact nested chain
    the URL claims, or the Submission's owner is not a Student.

    Every link is re-proved against current-read locked rows rather than
    trusted from the pre-lock preview: the Assignment's Group ownership
    and its own public id, the Submission's Assignment ownership and its
    own public id, and the owner's identity and role. A foreign key into
    ``users`` proves the row exists, never that it is a Student's -- the
    owner's *account status* and *Enrollment* are deliberately **not**
    checked, because a withdrawn or suspended Student's genuine
    historical work must still be reviewable.

    The caller turns a ``True`` into the same non-disclosing 404 the read
    routes produce, with no partial write of any kind.
    """
    if assignment is None or submission is None or owner is None:
        return True
    if assignment.group_id != group.id or assignment.public_id != assignment_public_id:
        return True
    if (
        submission.assignment_id != assignment.id
        or submission.public_id != submission_public_id
    ):
        return True
    return owner.id != submission.student_id or owner.role != _STUDENT


def _feedback_operational_block(hierarchy, group, term_id, level_id, course_id):
    """``None`` if the locked Group and its locked AcademicTerm / Level /
    Course all exist and are active (so feedback may be written), else a
    Teacher-facing message.

    Same shape as the M01 Assignment check, with the M03 sentence: what is
    blocked here is *writing*, never reading -- feedback already saved
    stays visible to the Teacher under an archived chain.
    """
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
            "Feedback can only be written or changed while the group and its academic term, "
            f"course, and level are all active. The {_join_labels(labels)} {verb} archived. "
            "Existing feedback stays readable."
        )
    return None


def _existing_reviewer_is_teacher(feedback):
    """Whether the existing feedback row's reviewer still has the Teacher
    role.

    **This is an integrity gate on write eligibility**, not a cosmetic
    check: a ``False`` refuses the save outright, so that a row whose
    ``reviewer_id`` no longer names a Teacher is neither silently
    overwritten nor treated as absent.

    It is an **ordinary ``SELECT``**, not ``SELECT ... FOR UPDATE``. The
    approved M03 lock order covers the acting Teacher and the Submission
    owner, and taking a third, out-of-order User lock here would break the
    project-wide ascending-internal-id rule that keeps this route
    deadlock-compatible with the Administrator membership and account
    write paths.

    What that buys, honestly stated: the reviewer's role is read from the
    current committed state inside this transaction, as a bare column
    rather than an entity, so the identity map cannot answer it from a row
    read before the locks. What it does **not** buy: the reviewer's
    ``users`` row is not locked, so a role change committing between this
    read and this request's commit is not excluded. The consequences of
    that window are bounded and non-destructive -- a save may be refused
    on a row that has just become valid again, or may proceed on a row
    whose reviewer lost the Teacher role in that instant, in which case
    the save reassigns ``reviewer_id`` to the acting Teacher and the next
    read is consistent. Neither outcome deletes or discloses anything.
    No real-MySQL behaviour is claimed here; the SQLite test backend
    demonstrates none of it.
    """
    if feedback is None:
        return True
    role = (
        db.session.query(User.role).filter(User.id == feedback.reviewer_id).first()
    )
    return role is not None and role[0] == _TEACHER


# ======================================================================
# Fresh authorization -- the only evidence a post-rollback path may use
# ======================================================================


def _fresh_teacher_authorization(
    actor_id, group_public_id, assignment_public_id, submission_public_id
):
    """Prove from **current database state** that `actor_id` may read this
    exact nested chain *right now*, and return
    ``(group, assignment, submission_row)`` -- or abort with the
    established non-disclosing 404.

    **Why this exists.** ``roles_required`` runs once, before the view. A
    path that rolls back has released its locks, so neither that decorator
    nor a cached ``current_user`` object is current evidence any more: the
    acting Teacher's account or assignment may have changed inside exactly
    that window. ``_teacher_group_or_404`` alone is not enough either --
    it proves an active ``GroupTeacherAssignment`` but never re-reads the
    actor's own ``role`` and ``status``.

    The actor is identified by a **scalar id captured before the reset**,
    never by ``current_user``, so nothing here depends on a lazily
    reloaded proxy or on which session object happens to be cached.

    What is proved, in order, all as current reads:

    1. the acting User row exists;
    2. its ``role`` is ``teacher``;
    3. its ``status`` is ``active``;
    4. an **active** ``GroupTeacherAssignment`` links it to the exact
       Group named in the URL;
    5. the Assignment belongs to that exact Group;
    6. the Submission belongs to that exact Assignment and its owner is
       still a Student (via ``teacher_submission``, unchanged from M02).

    What is deliberately **not** proved, because M03 reading is
    historical: the AcademicTerm / Level / Course / Group need not be
    active, the Assignment need not be published or open, the Submission's
    owner need not have an active account or Enrollment, and the
    *historical reviewer* of any existing feedback need not still be
    active or assigned. Only the **acting** Teacher is checked here.

    This is scoped to M03 rather than folded into
    ``_teacher_group_or_404``, which every other Teacher route shares:
    widening that helper would change behaviour well outside this Part.
    """
    if actor_id is None:
        abort(404)

    # The actor's own row, read fresh and projected to the two columns
    # that decide eligibility -- never `current_user`, and never a whole
    # entity the identity map could answer from a pre-rollback read.
    actor = (
        db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    )
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

    still_assigned = db.session.query(
        GroupTeacherAssignment.query.filter_by(
            group_id=group.id, teacher_id=actor_id, status=_ASSIGNMENT_ACTIVE
        ).exists()
    ).scalar()
    if not still_assigned:
        abort(404)

    assignment = Assignment.query.filter_by(
        public_id=assignment_public_id, group_id=group.id
    ).first()
    if assignment is None:
        abort(404)

    # Last, and only once everything above has passed: the row that
    # carries the Student's answer.
    row = teacher_submission(assignment.id, submission_public_id)
    if row is None:
        abort(404)
    return group, assignment, row


# ======================================================================
# Rendering
# ======================================================================


def _render_feedback_page(
    actor_id,
    actor_public_id,
    group_public_id,
    assignment_public_id,
    submission_public_id,
    form=None,
    state_token=None,
):
    """Render the feedback editor, or 404.

    Releases any write lock first (harmless on a GET or a plain form
    failure) and then re-proves the **whole** authorization chain --
    including the acting Teacher's own role and account status -- through
    :func:`_fresh_teacher_authorization`, before anything private is
    fetched, rendered, or signed into a token. The rollback above is
    exactly why that is necessary: the locks are gone, and the decorator
    that ran at the start of the request is no longer current evidence.

    `actor_id` / `actor_public_id` are **scalars captured before any
    reset** in this request. They are passed in rather than read from
    ``current_user`` here so this function cannot accidentally depend on a
    proxy reloaded after the rollback.

    `form` and `state_token` are supplied only by the ordinary-validation
    path, which must show the Teacher their attempted text again with the
    **original** token, never a freshly minted one.
    """
    db.session.rollback()
    group, assignment, row = _fresh_teacher_authorization(
        actor_id, group_public_id, assignment_public_id, submission_public_id
    )
    tz_name = _tz_name()

    panel = build_feedback_panel(
        teacher_feedback(assignment.id, submission_public_id), tz_name,
        include_version=True,
    )
    operational = _group_is_operational(group)

    # Writing is offered only when the chain is operational AND the
    # existing row (if any) is intact. A role-inconsistent row fails
    # closed: no form, no token, and therefore nothing a save could
    # overwrite or duplicate.
    can_write = operational and panel["state"] != FEEDBACK_INVALID
    if can_write:
        if form is None:
            form = SubmissionFeedbackForm(
                formdata=None,
                data={"feedback_text": panel.get("text", "")},
            )
        if state_token is None:
            state_token = _make_feedback_state_token(
                actor_public_id,
                group_public_id,
                assignment_public_id,
                submission_public_id,
                panel,
            )
    else:
        form = None
        state_token = None

    return _private_no_store(
        "teacher/submissions/feedback.html",
        group=group,
        assignment=build_teacher_view([assignment], tz_name, utc_reference_now())[0],
        submission=build_teacher_submission_item(row, tz_name, include_answer=True),
        feedback=panel,
        form=form,
        can_write=can_write,
        feedback_state_token=state_token,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=tz_name,
        submission_url=_submission_detail_url(
            group_public_id, assignment_public_id, submission_public_id
        ),
    )


def _redirect_feedback(group_public_id, assignment_public_id, submission_public_id):
    return redirect(
        _feedback_url(group_public_id, assignment_public_id, submission_public_id)
    )


def _reject_stale(group_public_id, assignment_public_id, submission_public_id):
    """Post/Redirect/Get rejection for a missing, malformed, wrong-shaped,
    invalidly signed, cross-object, or genuinely outdated token.

    Discards every submitted value and reloads the current persisted
    feedback through a fresh GET -- it never pairs a freshly generated
    token with the attempted text, which is precisely the bypass this
    rejection exists to close (the same reasoning as the M01 Assignment
    edit snapshot and the M02 submission context).
    """
    db.session.rollback()
    flash(_STALE_MESSAGE, "danger")
    return _redirect_feedback(
        group_public_id, assignment_public_id, submission_public_id
    )


# ======================================================================
# The one endpoint
# ======================================================================


@teacher_bp.route(
    "/groups/<group_public_id>/assignments/<assignment_public_id>"
    "/submissions/<submission_public_id>/feedback",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def submission_feedback(group_public_id, assignment_public_id, submission_public_id):
    """Read, create, or revise the one feedback record on one Submission.

    **Nothing about ownership is taken from the request.** The reviewer is
    the authenticated session, the Submission / Assignment / Group are the
    authorized nested public identifiers in the URL, ``version`` comes
    from the locked row, and both timestamps are generated on the server
    after the locks. A forged ``reviewer_id`` / ``submission_id`` /
    ``version`` / ``updated_at`` field in the POST body has nowhere to
    land -- the form carries only ``feedback_text``.

    **Order of the post-lock checks.** Authorization and nested ownership
    come first, so an unauthorized attempt fails identically whether or
    not feedback already exists. The operational chain, the existing row's
    reviewer integrity, the signed token and finally ordinary field
    validation follow. Only then is the no-op compared, and only then is
    any field assigned -- so every rejection leaves the stored row, its
    version, its timestamps and its attribution exactly as they were.
    """
    if request.method == "GET":
        # The actor's identity is captured as plain scalars HERE, before
        # `_render_feedback_page` performs its rollback, and the page then
        # re-proves that actor against current state.
        return _render_feedback_page(
            current_user.id, current_user.public_id,
            group_public_id, assignment_public_id, submission_public_id,
        )

    # ------------------------------------------------------------------
    # Pre-lock preview: the same object authorization every nested
    # Teacher route uses. Friendly and fast, but NOT authoritative --
    # everything it proves is proved again below against locked rows.
    # ------------------------------------------------------------------
    preview_group = _teacher_group_or_404(group_public_id)
    preview_assignment = _assignment_for_group_or_404(
        preview_group, assignment_public_id
    )
    preview_submission = Submission.query.filter_by(
        public_id=submission_public_id, assignment_id=preview_assignment.id
    ).first()
    if preview_submission is None:
        abort(404)

    if not _group_is_operational(preview_group):
        # Helpful early check: keeps a bookmarked editor URL from silently
        # accepting a write that the post-lock check would reject anyway.
        # It is deliberately NOT the enforcement point.
        flash(
            "Feedback can only be written or changed while the group and its academic term, "
            "course, and level are all active. Existing feedback stays readable.",
            "danger",
        )
        return _redirect_feedback(
            group_public_id, assignment_public_id, submission_public_id
        )

    # Ordinary field validation (empty / whitespace-only / too long) runs
    # here, while the session is still cheap to touch. Its RESULT is
    # applied only after the locks, so a rejection order can never depend
    # on it.
    form = SubmissionFeedbackForm()
    form_is_valid = form.validate_on_submit()
    feedback_text = form.normalized_feedback()

    # Every scalar the rest of this request needs is captured BEFORE the
    # transaction reset below, so nothing between that reset and the
    # required locks triggers a lazy ORM or `current_user` reload that
    # would establish a fresh read snapshot ahead of the locks.
    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    owner_id = preview_submission.student_id
    submission_id = preview_submission.id
    assignment_id = preview_assignment.id
    term_id = preview_group.academic_term_id
    level_id = preview_group.course.level_id
    course_id = preview_group.course_id
    submitted_token = request.form.get("feedback_state", "")

    # ------------------------------------------------------------------
    # The single deliberate reset + the route-specific lock order.
    # ------------------------------------------------------------------
    (
        hierarchy,
        group,
        users,
        teacher_assignment,
        assignment,
        submission,
        feedback,
    ) = _lock_feedback_chain(
        group_public_id, term_id, level_id, course_id,
        teacher_id, owner_id, assignment_id, submission_id,
    )

    teacher = users.get(teacher_id)
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)
    if _nested_ownership_broken(
        group, assignment, submission, users.get(owner_id),
        assignment_public_id, submission_public_id,
    ):
        db.session.rollback()
        abort(404)

    blocked = _feedback_operational_block(
        hierarchy, group, term_id, level_id, course_id
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_feedback(
            group_public_id, assignment_public_id, submission_public_id
        )

    # The locked row must really be this Submission's feedback. It was
    # located BY `submission_id`, so this can only fail on genuine
    # corruption -- it is asserted rather than assumed because the write
    # below trusts the row's identity.
    if feedback is not None and feedback.submission_id != submission.id:
        db.session.rollback()
        flash(_INTEGRITY_MESSAGE, "danger")
        return _redirect_feedback(
            group_public_id, assignment_public_id, submission_public_id
        )

    # A role-inconsistent existing row fails closed. It is NOT treated as
    # absent (which would attempt a duplicate insertion the unique
    # constraint would reject) and it is NOT silently overwritten, which
    # would destroy the record while pretending to repair it.
    if not _existing_reviewer_is_teacher(feedback):
        db.session.rollback()
        flash(_INTEGRITY_MESSAGE, "danger")
        return _redirect_feedback(
            group_public_id, assignment_public_id, submission_public_id
        )

    # The token is re-checked against the LOCKED current row -- this is
    # what closes the window between the form's GET and these locks, and
    # what makes two co-teachers racing on an empty record resolve as
    # "first save wins, second is told to reload".
    if _feedback_state_is_stale(
        submitted_token, teacher_public_id, group_public_id,
        assignment_public_id, submission_public_id, feedback,
    ):
        return _reject_stale(
            group_public_id, assignment_public_id, submission_public_id
        )

    if not form_is_valid:
        # Ordinary validation failure with a still-valid context: show the
        # attempted text again with the ORIGINAL token, so the Teacher can
        # fix it without losing what they wrote and without the expected
        # version being silently refreshed underneath them.
        return _render_feedback_page(
            teacher_id, teacher_public_id,
            group_public_id, assignment_public_id, submission_public_id,
            form=form, state_token=submitted_token,
        )

    # An authorized save of identical normalized text is a no-op: not an
    # edit, so no version increment, no new timestamp and no change of
    # attribution. It still had to pass every check above.
    if feedback is not None and feedback.feedback_text == feedback_text:
        db.session.rollback()
        flash("This feedback is unchanged, so nothing was saved.", "info")
        return redirect(
            _submission_detail_url(
                group_public_id, assignment_public_id, submission_public_id
            )
        )

    # The authoritative write moment: read only now, after every lock that
    # could have blocked, and truncated to the whole second the columns
    # can actually hold.
    now_utc = _write_moment()

    # No field is assigned until every check above has passed, so a
    # rejection never leaves a partial update.
    created = feedback is None
    if created:
        db.session.add(
            SubmissionFeedback(
                submission_id=submission.id,
                reviewer_id=teacher.id,
                feedback_text=feedback_text,
                version=1,
                created_at=now_utc,
                updated_at=now_utc,
            )
        )
    else:
        feedback.feedback_text = feedback_text
        feedback.reviewer_id = teacher.id
        feedback.version = feedback.version + 1
        feedback.updated_at = now_utc
        # `created_at`, `id`, `public_id` and `submission_id` are never
        # assigned here: a revision is the same record, not a new one.

    try:
        db.session.commit()
    except IntegrityError:
        # 1. Roll back FIRST. Everything read before this point is now
        #    discarded state and must not be used as evidence of
        #    anything -- least of all authorization.
        db.session.rollback()

        # 2. Re-establish current authorization from scratch, using the
        #    pre-reset actor scalars and the SAME fresh check the render
        #    path uses. Whatever raised the IntegrityError may well have
        #    been a concurrent change that ALSO ended this Teacher's
        #    access -- a suspended or demoted account, a removed
        #    assignment, a vanished object. Proving the object alone would
        #    not catch the first two. This aborts 404 without disclosure,
        #    and it runs BEFORE the response below is chosen.
        _fresh_teacher_authorization(
            teacher_id, group_public_id, assignment_public_id, submission_public_id
        )

        # 3. A generic, safe message. A constraint failure is NOT reported
        #    as success, and nothing here retries the write or overwrites
        #    a co-teacher who won the race: the Teacher is sent back to
        #    read the current feedback. No SQL, driver text, parameter or
        #    internal id ever reaches the page.
        flash(
            "This feedback could not be saved. Someone may have just changed it. Please "
            "reload, read the current feedback, and try again.",
            "danger",
        )
        return _redirect_feedback(
            group_public_id, assignment_public_id, submission_public_id
        )

    # Conditional on purpose. A save is deliberately allowed on genuinely
    # historical work -- a withdrawn or suspended Student, an unpublished
    # Assignment -- and in those cases the Student cannot open the receipt
    # at all right now. Promising that they "can see it now" would be
    # false, so the wording states the rule instead of a current fact.
    flash(
        "Feedback saved. It is visible to the student whenever they can open this "
        "submission."
        if created
        else "Feedback updated. Only this latest version is kept, and it is what the "
        "student sees whenever they can open this submission.",
        "success",
    )
    return redirect(
        _submission_detail_url(
            group_public_id, assignment_public_id, submission_public_id
        )
    )
