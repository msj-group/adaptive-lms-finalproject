"""Teacher authoring, publication and review of Group-owned **Speaking
activities** (Phase 4 / M06).

Group- and activity-centered routes only, every object addressed by
``public_id`` and no internal numeric id anywhere in a URL, a form value
or the rendered HTML::

    GET       /teacher/groups/<gp>/speaking
    GET|POST  /teacher/groups/<gp>/speaking/new
    GET       /teacher/groups/<gp>/speaking/<sp>
    GET|POST  /teacher/groups/<gp>/speaking/<sp>/edit
    POST      /teacher/groups/<gp>/speaking/<sp>/publish
    POST      /teacher/groups/<gp>/speaking/<sp>/withdraw
    GET       /teacher/groups/<gp>/speaking/<sp>/submissions
    GET       /teacher/groups/<gp>/speaking/<sp>/submissions/<bp>
    GET|POST  /teacher/groups/<gp>/speaking/<sp>/submissions/<bp>/feedback
    GET       /teacher/groups/<gp>/speaking/<sp>/submissions/<bp>/audio
    GET       /teacher/groups/<gp>/speaking/<sp>/submissions/<bp>/audio/download

There is deliberately no flat ``/teacher/speaking`` collection, and no
delete, archive, duplicate, replace, re-open, resubmit, score, grade or
manual-marking action for an activity, a recording or a feedback record
-- none of that exists server-side either, and no placeholder is left for
one.

**A Speaking activity is one Assignment plus one extension row, and this
module is built on exactly that.** Group ownership, the title, the
instructions, ``opens_at`` / ``due_at``, the ``draft`` / ``published``
lifecycle, ``published_at``, the ``opens_at < due_at`` rule and the
derived Scheduled / Open / Past due states all come from the accepted M01
implementation and are *called* here rather than re-implemented --
``AssignmentForm``, ``_lock_assignment_chain``, ``_teacher_group_or_404``,
``_authz_broken``, ``_group_is_operational``, ``_archived_chain_labels``,
``derived_state`` and ``_private_no_store`` are all imported. What this
module adds is exactly what an ordinary Assignment has no concept of:
recordings made by Students in the browser, an authorized private audio
route for each of them, and one shared textual feedback record per
recording.

**Addressing an activity by the *extension row's* ``public_id`` is what
keeps the two surfaces apart structurally.** An ordinary Assignment has
no ``speaking_activities`` row, so its ``public_id`` resolves to nothing
on any route here; a Speaking activity is excluded from
``assignment_queries.teacher_assignments_page`` and from the ordinary
Teacher Assignment lookup, so it cannot be reached or listed through the
ordinary Assignment routes either. Neither direction depends on a check
somebody could forget.

**Authorization.** ``roles_required(TEACHER)`` handles anonymous (login
redirect) and non-Teacher (403). Object authorization is then server-side
and nested, reusing the exact helpers every other Teacher route uses:
``_teacher_group_or_404`` proves an **active**
``GroupTeacherAssignment`` to the Group in the URL, and every nested
lookup is constrained by that Group. A missing Group, a missing activity,
an activity belonging to another Group, an ordinary Assignment's public
id, a recording from another activity, an unassigned Teacher and a removed
assignment all return the same non-disclosing **404** -- never a 403, and
never a hint that the object exists. Multiple active assigned Teachers are
equal collaborators on the same activity and the same feedback record.

**Reading is historical; writing is not.** The list, the detail page, the
submission pages, the feedback panel and the audio routes stay available
to an actively assigned Teacher even when the Group or an academic
ancestor is archived, so an activity and the work handed in for it can
always be read back. Creating, editing, publishing, withdrawing and
writing feedback additionally require an operational chain, re-checked
against the **locked** rows.

**Two freezes.** Publishing makes the authored activity read-only --
Students may already be reading exactly those instructions and working to
exactly that deadline. The **first recording** freezes it permanently and
forbids withdrawal, because somebody's spoken answer is now an answer *to*
it.

**No Teacher audio is uploaded anywhere in this module.** The Teacher
authors a task; the Student records the answer. Every audio route here
*serves* an already-stored Student recording after re-proving the whole
nested chain, and nothing here writes a file.
"""

import secrets

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.blueprints.teacher import teacher_bp
from app.blueprints.teacher.assignments import (
    _lock_assignment_chain,
    _private_no_store,
    _tz_name,
)
from app.blueprints.teacher.speaking_forms import (
    SpeakingActivityForm,
    SpeakingFeedbackForm,
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
    Assignment,
    AssignmentStatus,
    Course,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    SpeakingActivity,
    SpeakingFeedback,
    SpeakingSubmission,
    User,
    UserRole,
    UserStatus,
)
from app.security.decorators import roles_required
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.assignment_queries import PAGE_SIZE, normalize_page
from app.services.group_transactions import lock_group_in_open_transaction
from app.services.material_serving import serve_uploaded_file
from app.services.schedule_occurrences import to_app_local, utc_reference_now
from app.services.speaking_queries import (
    activity_has_submissions,
    activity_ids_with_submissions,
    attach_feedback_states,
    audio_upload_for_submission,
    build_speaking_detail,
    build_teacher_speaking_list_view,
    build_teacher_submission_view,
    feedback_states_for_page,
    speaking_publication_blockers,
    student_name_for_submission,
    teacher_speaking,
    teacher_speaking_feedback,
    teacher_speaking_page,
    teacher_speaking_submission,
    teacher_speaking_submissions_page,
)
from app.services.submission_feedback_queries import (
    FEEDBACK_INVALID,
    FEEDBACK_PRESENT,
    build_feedback_panel,
)

_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_DRAFT = AssignmentStatus.DRAFT.value
_PUBLISHED = AssignmentStatus.PUBLISHED.value
_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value
_USER_ACTIVE = UserStatus.ACTIVE.value

_PUBLISH_ACTION = "publish"
_WITHDRAW_ACTION = "withdraw"
_PUBLICATION_ACTIONS = (_PUBLISH_ACTION, _WITHDRAW_ACTION)


def _write_moment():
    """The **authoritative** naive-UTC moment for one Speaking write,
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


# ======================================================================
# Teacher-facing sentences, declared once each
# ======================================================================
#
# Every rule a Teacher can hit is stated in exactly one place, so the
# early courtesy check and the authoritative post-lock check can never
# explain the same rule differently.

_STALE_MESSAGE = (
    "This speaking activity was changed by someone else since this form was opened. Your "
    "changes were not saved. Please reload, read the current version, and make your change "
    "against it."
)
_FEEDBACK_STALE_MESSAGE = (
    "This feedback was changed by someone else since this form was opened. Your text was not "
    "saved. Please reload, read the current feedback, and write your revision against it."
)
_NOT_OPERATIONAL_MESSAGE = (
    "Speaking activities can only be created or edited while the group and its academic "
    "term, course, and level are all active. Existing activities stay readable."
)
_PUBLICATION_NOT_OPERATIONAL_MESSAGE = (
    "A speaking activity can only be published or withdrawn while the group and its academic "
    "term, course, and level are all active. Existing recordings stay readable."
)
_FEEDBACK_NOT_OPERATIONAL_MESSAGE = (
    "Feedback can only be written or changed while the group and its academic term, course, "
    "and level are all active. Existing feedback stays readable."
)

_SPEAKING_BLOCK_WORDING = (
    "Speaking activities can only be created or edited",
    "Existing activities stay readable.",
)
_PUBLICATION_BLOCK_WORDING = (
    "Speaking activities can only be published or withdrawn",
    "Existing recordings stay readable.",
)
_FEEDBACK_BLOCK_WORDING = (
    "Feedback can only be written or changed",
    "Existing feedback stays readable.",
)

_FROZEN_BY_SUBMISSIONS_MESSAGE = (
    "Students have already recorded answers for this speaking activity, so its title, "
    "instructions, opening time and deadline can no longer be changed, and it can no longer "
    "be withdrawn. Students recorded answers to exactly this task within exactly this window, "
    "and rewriting it afterwards would change what their recordings were for. You can still "
    "listen to every recording and write feedback."
)
_FROZEN_BY_PUBLICATION_MESSAGE = (
    "This speaking activity is published, so its title, instructions, opening time and "
    "deadline are read-only. Withdraw it first if you need to change something -- that is "
    "possible only while no student has recorded an answer."
)

#: The one sentence for a role-inconsistent existing feedback row, used by
#: the read and the write path alike so neither can explain the same
#: integrity problem differently. It names no reviewer, quotes no feedback
#: text, and promises no automatic repair.
_FEEDBACK_INTEGRITY_MESSAGE = (
    "The saved feedback on this recording is linked to an account that is no longer a "
    "teacher, so it cannot be shown or changed here. Nothing has been deleted. Please ask an "
    "administrator to review this record."
)


def _authoring_block(assignment, activity):
    """``None`` when this activity's authored content may still be
    changed, else the sentence explaining why not.

    The submission freeze is checked **first** because it is the stronger
    and permanent one, and a Teacher whose activity has recordings must
    not be told to "withdraw it first", which would send them at a door
    that is already locked.

    Must be called against the **locked** rows on every write path.
    """
    if activity_has_submissions(activity.id):
        return _FROZEN_BY_SUBMISSIONS_MESSAGE
    if assignment.status == _PUBLISHED:
        return _FROZEN_BY_PUBLICATION_MESSAGE
    return None


# ======================================================================
# URLs
# ======================================================================


def _list_url(group_public_id):
    return url_for("teacher.group_speaking", group_public_id=group_public_id)


def _new_url(group_public_id):
    return url_for("teacher.speaking_create", group_public_id=group_public_id)


def _detail_url(group_public_id, speaking_public_id):
    return url_for(
        "teacher.speaking_detail",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
    )


def _edit_url(group_public_id, speaking_public_id):
    return url_for(
        "teacher.speaking_edit",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
    )


def _submissions_url(group_public_id, speaking_public_id):
    return url_for(
        "teacher.speaking_submissions",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
    )


def _submission_url(group_public_id, speaking_public_id, submission_public_id):
    return url_for(
        "teacher.speaking_submission_detail",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
        submission_public_id=submission_public_id,
    )


def _feedback_url(group_public_id, speaking_public_id, submission_public_id):
    return url_for(
        "teacher.speaking_submission_feedback",
        group_public_id=group_public_id,
        speaking_public_id=speaking_public_id,
        submission_public_id=submission_public_id,
    )


def _audio_urls(group_public_id, speaking_public_id, submission_public_id):
    return {
        "src": url_for(
            "teacher.speaking_submission_audio",
            group_public_id=group_public_id,
            speaking_public_id=speaking_public_id,
            submission_public_id=submission_public_id,
        ),
        "download": url_for(
            "teacher.speaking_submission_audio_download",
            group_public_id=group_public_id,
            speaking_public_id=speaking_public_id,
            submission_public_id=submission_public_id,
        ),
    }


# ======================================================================
# Signed exact-shape state tokens (Phase 4 / M06)
# ======================================================================
#
# Four dedicated M06 salts and four exact purpose markers. A token minted
# under any other salt -- the M01 assignment snapshot, the M02 submission
# context, the M03 feedback state, every M04 quiz token, every M05
# listening token -- fails signature verification here even though all of
# them are signed with the same application SECRET_KEY, and a token minted
# under one of these salts but for another M06 purpose fails the purpose
# check. The reverse holds too.
#
# Every payload carries **public identifiers, versions, a nonce and known
# enumerated values only**. A signed token is authenticated, not
# encrypted: anyone holding it can read its payload, so no recording
# bytes, no filename, no storage metadata, no feedback text, no Student
# answer and no internal database id is ever placed in one.
#
# The shape check below is exact and typed rather than merely "is a dict":
# the key set must match exactly, the purpose must be the expected one,
# identifiers must be strings, versions must be genuine positive ints
# (``bool`` excluded explicitly, since it is an ``int`` subclass and
# ``True`` must never pass as version 1), and each enumerated field must
# be a known member.

_SALTS = {
    "speaking-create": "teacher.speaking-create.phase4-m06.v1",
    "speaking-edit": "teacher.speaking-edit.phase4-m06.v1",
    "speaking-publication": "teacher.speaking-publication.phase4-m06.v1",
    "speaking-feedback": "teacher.speaking-feedback.phase4-m06.v1",
}

_FIELDS = {
    # Creation binds the Teacher, the Group and the Speaking purpose --
    # and nothing else, because no activity exists yet to bind to.
    "speaking-create": ("purpose", "teacher_public_id", "group_public_id", "nonce"),
    # Editing binds the exact authored state the form was written
    # against. There is no `version` column on a Speaking activity --
    # `Assignment` has none either -- so, exactly as M01 does for an
    # ordinary Assignment, the snapshot IS the four editable values.
    # `status` and `published_at` are deliberately excluded: a
    # publication toggle changes nothing the editor writes, so it must
    # not invalidate an otherwise unchanged form.
    "speaking-edit": (
        "purpose", "teacher_public_id", "group_public_id", "speaking_public_id",
        "title", "instructions", "opens_at", "due_at",
    ),
    "speaking-publication": (
        "purpose", "action", "teacher_public_id", "group_public_id",
        "speaking_public_id", "status",
    ),
    # The Part's exact required shape: Teacher, Group, Assignment,
    # SpeakingActivity, SpeakingSubmission, feedback public id and
    # feedback version. The last two are BOTH None -- together -- for the
    # explicit *expected absence* state, which is what makes "there was
    # no feedback when I opened this form" a signed claim rather than an
    # assumption. No feedback text is ever bound.
    "speaking-feedback": (
        "purpose", "teacher_public_id", "group_public_id", "assignment_public_id",
        "speaking_public_id", "submission_public_id", "feedback_public_id", "version",
    ),
}

#: Fields whose value must be a member of a small known set.
_ENUM_FIELDS = {
    "action": frozenset(_PUBLICATION_ACTIONS),
    "status": frozenset({_DRAFT, _PUBLISHED}),
}

#: Fields that may legitimately be ``None`` -- but only as a matched pair
#: (see the feedback shape above).
_FEEDBACK_STATE_FIELDS = ("feedback_public_id", "version")

#: Canonical, deterministic serialization for the two datetime snapshot
#: fields. A fixed second-precision ISO string, so a value that survives a
#: JSON round trip inside the signed token compares byte-for-byte against
#: a freshly rendered one and can never look "changed" merely because it
#: was formatted differently. Same convention as the M01 assignment
#: snapshot and the M02 submission context.
_SNAPSHOT_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _serializer(salt):
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=salt)


def _positive_int(value):
    """True for a genuine positive ``int``.

    ``bool`` is excluded explicitly: it is a subclass of ``int`` in
    Python, and ``True`` must never be accepted as version 1.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _make_token(purpose, **payload):
    """Sign one exact-shape M06 token.

    Only ever called with freshly read persisted state or a
    server-generated nonce, never with attempted form values: a fresh
    token may only pair with fresh state, which is precisely the bypass
    the stale rejection exists to close.
    """
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(_SALTS[purpose]).dumps(body)


def _load_token(token, purpose):
    """The token's payload, or ``None`` for a missing, malformed,
    invalidly signed, wrong-purpose, wrong-salt or wrong-shaped one.

    Every ``None`` is treated exactly like an outdated token: rejected,
    never trusted, and never silently upgraded into a claim about a row --
    in particular never into the feedback *absence* claim, which must be
    signed to count.
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
    if purpose == "speaking-feedback":
        public_id, version = payload["feedback_public_id"], payload["version"]
        if public_id is None and version is None:
            pass  # the explicit, signed "there was no feedback" state
        elif not isinstance(public_id, str) or not _positive_int(version):
            return None
    for field in fields:
        if field in _FEEDBACK_STATE_FIELDS and purpose == "speaking-feedback":
            continue  # already validated as a matched pair above
        value = payload[field]
        if field in _ENUM_FIELDS:
            if value not in _ENUM_FIELDS[field]:
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
    return any(payload[field] != value for field, value in expected.items())


def _edit_snapshot(assignment):
    """The exact editable state the edit token covers.

    ``status``, ``published_at``, ``created_at`` and ``updated_at`` are
    deliberately excluded: the edit route never writes them, so a
    concurrent publish or withdraw must NOT invalidate an open edit form
    -- there is nothing it could overwrite. The two datetimes are the
    canonical **UTC** values, formatted deterministically.
    """
    return {
        "title": assignment.title,
        "instructions": assignment.instructions,
        "opens_at": assignment.opens_at.strftime(_SNAPSHOT_DATETIME_FORMAT),
        "due_at": assignment.due_at.strftime(_SNAPSHOT_DATETIME_FORMAT),
    }


def _feedback_state_values(panel):
    """``(feedback_public_id, version)`` for the state `panel` describes
    -- an existing row's public id and version, or the explicit absence
    state (``None, None``)."""
    if panel.get("state") != FEEDBACK_PRESENT:
        return None, None
    return panel.get("public_id"), panel.get("version")


# ======================================================================
# Locking -- the M01 chain, extended by exactly one link
# ======================================================================


def _lock_speaking_chain(
    group_public_id, term_id, level_id, course_id, teacher_id, assignment_id=None,
    activity_id=None,
):
    """Acquire the established lock order in one open transaction, with
    the SpeakingActivity row taking its documented place::

        AcademicTerm -> Level -> Course   (lock_academic_hierarchy, which
        owns the single deliberate reset)
        -> Group -> acting Teacher User -> GroupTeacherAssignment
        -> Assignment
        -> SpeakingActivity

    ``_lock_assignment_chain`` is reused verbatim for the whole prefix
    rather than restated, so the Group lock that already serializes an
    Assignment write against an Administrator Group retarget serializes a
    Speaking write against it too, and the Assignment lock serializes it
    against the ordinary M01 edit and publication routes. The extension
    row is locked **after** its Assignment, never before: the Assignment
    remains the serialization point for its whole aggregate.

    Returns ``(hierarchy, group, teacher, teacher_assignment, assignment,
    activity)``. Any of them may be ``None`` -- the caller must treat that
    as a business/authorization rejection, roll back, and 404 or redirect;
    it must never "keep going".

    SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
    REPEATABLE READ snapshot isolation, so tests can assert the
    *requested* reset and lock order and nothing about real InnoDB
    blocking.
    """
    hierarchy, group, teacher, teacher_assignment, assignments = _lock_assignment_chain(
        group_public_id, term_id, level_id, course_id, teacher_id,
        assignment_ids=[] if assignment_id is None else [assignment_id],
    )
    assignment = None if assignment_id is None else assignments.get(assignment_id)
    activity = None
    if activity_id is not None:
        activity = (
            SpeakingActivity.query.filter_by(id=activity_id).with_for_update().first()
        )
    return hierarchy, group, teacher, teacher_assignment, assignment, activity


def _lock_feedback_chain(
    group_public_id, term_id, level_id, course_id, teacher_id, owner_id,
    assignment_id, activity_id, submission_id,
):
    """Acquire the route-specific Phase 4 / M06 feedback lock order in one
    open transaction::

        AcademicTerm -> Level -> Course  (via lock_academic_hierarchy,
        which owns the single deliberate reset)
        -> Group
        -> the involved User rows, ascending internal id
           (acting Teacher and recording owner)
        -> the acting Teacher's GroupTeacherAssignment
        -> Assignment -> SpeakingActivity -> SpeakingSubmission
        -> the existing SpeakingFeedback for that submission, if any

    This is the shared hierarchy/Group prefix every Group-affecting
    mutation in the project already uses, extended with the rows this
    workflow actually decides on, and it keeps the project-wide "User rows
    in ascending internal id" rule that the Administrator membership and
    account write paths rely on -- which is why two co-teachers reviewing
    two different Students cannot deadlock against each other or against a
    membership change. It is the M03 chain with the Speaking rows in place
    of the text Submission's, deliberately built the same way rather than
    invented anew. No existing route's contract is redesigned here.

    **What actually serializes two co-teachers racing to write the FIRST
    feedback** is the chain of locks on rows that already exist: both
    requests take the same Group, Assignment, activity and submission row
    locks *before* either one reads or inserts feedback, so the second
    waits for the first to commit and then re-reads a row that is no
    longer missing.

    The last statement locks by ``speaking_submission_id``
    (``uq_speaking_feedback_submission``), which for a missing row can
    only take a gap/next-key lock. A gap lock is **not** a mutex:
    MySQL/InnoDB documents that gap locks on the same gap can be held by
    several transactions at once and do not block one another, so
    acquiring one is not by itself what makes competing creators mutually
    exclusive -- see
    https://dev.mysql.com/doc/refman/8.0/en/innodb-locking.html. The
    statement is issued to read the current row under the same
    transaction, not as the exclusion mechanism.
    ``uq_speaking_feedback_submission`` remains the final duplicate
    defense behind both, and the signed version token is what turns a
    losing race into an explicit "reload and review" rejection rather than
    an overwrite. None of this is claimed to be measured: the SQLite test
    backend can demonstrate none of it.
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
    activity = SpeakingActivity.query.filter_by(id=activity_id).with_for_update().first()
    submission = (
        SpeakingSubmission.query.filter_by(id=submission_id).with_for_update().first()
    )
    feedback = (
        SpeakingFeedback.query.filter_by(speaking_submission_id=submission_id)
        .with_for_update()
        .first()
    )
    return (
        hierarchy, group, users, teacher_assignment, assignment, activity, submission,
        feedback,
    )


def _speaking_ownership_broken(group, assignment, activity, speaking_public_id):
    """True when the **locked** rows are no longer the nested chain the URL
    claims.

    Re-proved against the locked current reads rather than trusted from
    the pre-lock preview, and proved at **every** level: the Assignment
    must still exist and still belong to this exact Group, the extension
    row must still exist, must still belong to that exact Assignment, and
    must still carry this exact ``public_id``. The caller turns a ``True``
    into the same non-disclosing 404 the read routes produce, with no
    partial write of any kind.
    """
    if group is None or assignment is None or activity is None:
        return True
    if assignment.group_id != group.id:
        return True
    return (
        activity.assignment_id != assignment.id
        or activity.public_id != speaking_public_id
    )


def _submission_ownership_broken(activity, submission, owner, submission_public_id):
    """True when the **locked** recording is no longer this activity's, or
    its owner is not a Student.

    A foreign key into ``users`` proves the row exists, never that it is a
    Student's -- the owner's *account status* and *Enrollment* are
    deliberately **not** checked, because a withdrawn or suspended
    Student's genuine historical work must still be reviewable.
    """
    if submission is None or owner is None:
        return True
    if (
        submission.speaking_activity_id != activity.id
        or submission.public_id != submission_public_id
    ):
        return True
    return owner.id != submission.student_id or owner.role != _STUDENT


def _operational_block(hierarchy, group, term_id, level_id, course_id, wording):
    """``None`` if the locked Group and its locked AcademicTerm / Level /
    Course all exist and are active (so this write may proceed), else a
    Teacher-facing message built from `wording`.

    Same shape and same reasoning as M01's and M05's equivalents; the
    wording differs per surface because what is blocked differs, and it is
    supplied by the caller from a constant declared once.
    """
    term = hierarchy.term(term_id)
    level = hierarchy.level(level_id)
    course = hierarchy.course(course_id)
    if (
        group is None
        or course is None
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
        lead, tail = wording
        return (
            f"{lead} while the group and its academic term, course, and level are all "
            f"active. The {_join_labels(labels)} {verb} archived. {tail}"
        )
    return None


# ======================================================================
# Fresh authorization -- the only evidence a post-rollback path may use
# ======================================================================


def _fresh_speaking_authorization(
    actor_id, group_public_id, speaking_public_id=None, submission_public_id=None
):
    """Prove from **current database state** that `actor_id` may read this
    Group -- and, when asked, this activity and this recording -- *right
    now*, and return ``(group, assignment, activity, submission)``.

    Exists for the reason M03 already documents: ``roles_required`` runs
    once, before the view, so a path that has rolled back and released its
    locks no longer holds current evidence, and the same concurrent change
    that forced the rollback may have ended this Teacher's access.
    ``_teacher_group_or_404`` alone is not enough either -- it proves an
    active ``GroupTeacherAssignment`` but never re-reads the actor's own
    ``role`` and ``status``. The actor is identified by a **scalar id
    captured before the reset**, never by ``current_user``.

    What is proved, in order, all as current reads: the acting User row
    exists, its role is ``teacher``, its status is ``active``, an active
    ``GroupTeacherAssignment`` links it to the exact Group named in the
    URL, the Speaking activity belongs to that exact Group, and the
    recording belongs to that exact activity and is a Student's.

    What is deliberately **not** proved, because reading is historical:
    the AcademicTerm / Level / Course / Group need not be active, the
    activity need not be published or open, the owner need not have an
    active account or Enrollment, and the *historical reviewer* of any
    existing feedback need not still be active or assigned. Only the
    **acting** Teacher is checked here.
    """
    if actor_id is None:
        abort(404)

    # The actor's own row, read fresh and projected to the two columns
    # that decide eligibility -- never `current_user`, and never a whole
    # entity the identity map could answer from a pre-rollback read.
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

    still_assigned = db.session.query(
        GroupTeacherAssignment.query.filter_by(
            group_id=group.id, teacher_id=actor_id, status=_ASSIGNMENT_ACTIVE
        ).exists()
    ).scalar()
    if not still_assigned:
        abort(404)

    if speaking_public_id is None:
        return group, None, None, None

    row = teacher_speaking(group.id, speaking_public_id)
    if row is None:
        abort(404)
    assignment, activity = row
    if submission_public_id is None:
        return group, assignment, activity, None

    submission = teacher_speaking_submission(activity.id, submission_public_id)
    if submission is None:
        abort(404)
    return group, assignment, activity, submission


def _speaking_or_404(group, speaking_public_id):
    """``(assignment, activity)`` for this Group, or the established
    non-disclosing 404. An ordinary Assignment's ``public_id``, another
    Group's activity and a nonexistent id all fail identically here."""
    row = teacher_speaking(group.id, speaking_public_id)
    if row is None:
        abort(404)
    return row


def _submission_or_404(activity, submission_public_id):
    submission = teacher_speaking_submission(activity.id, submission_public_id)
    if submission is None:
        abort(404)
    return submission


def _hierarchy_context(group):
    return group.academic_term_id, group.course.level_id, group.course_id


# ======================================================================
# List
# ======================================================================


@teacher_bp.get("/groups/<group_public_id>/speaking")
@roles_required(UserRole.TEACHER.value)
def group_speaking(group_public_id):
    """One bounded page of a Group's Speaking activities, newest deadline
    first.

    Fixed page size, SQL ordering (``due_at DESC, id DESC``), SQL
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag with no ``COUNT``, and
    the same page normalization and past-the-end fallback every other
    Teacher list uses. The rows carry no instructions, so the page's cost
    does not grow with how much has been written into each activity, and
    **one** extra bounded query resolves which of the at-most-PAGE_SIZE
    activities already have recordings -- asking per row would be an N+1.

    Stays available under an archived Group or ancestor: an eligible
    assigned Teacher can always read back what was authored.
    """
    group = _teacher_group_or_404(group_public_id)
    operational = _group_is_operational(group)
    tz_name = _tz_name()
    reference_utc = utc_reference_now()
    page = normalize_page(request.args.get("page"))

    rows, has_next = teacher_speaking_page(group.id, page)
    if not rows and page > 1:
        # A page past the end (a stale bookmark) shows page 1 rather than
        # a confusing empty page with a "Previous" button.
        page = 1
        rows, has_next = teacher_speaking_page(group.id, page)

    frozen_ids = activity_ids_with_submissions([row.activity_id for row in rows])

    return _private_no_store(
        "teacher/speaking/list.html",
        group=group,
        activities=build_teacher_speaking_list_view(
            rows, tz_name, reference_utc, frozen_ids
        ),
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
    )


# ======================================================================
# Detail -- read only
# ======================================================================


@teacher_bp.get("/groups/<group_public_id>/speaking/<speaking_public_id>")
@roles_required(UserRole.TEACHER.value)
def speaking_detail(group_public_id, speaking_public_id):
    """One Speaking activity, or a non-disclosing 404.

    **This route writes nothing.** Every control it renders is a POST to
    its own route with its own CSRF token, its own signed state and its
    own authoritative post-lock checks.

    The readiness panel calls ``speaking_publication_blockers``, which is
    the same function the publish route runs against the locked rows, so
    what a Teacher reads and the rule that decides can never disagree.
    """
    group = _teacher_group_or_404(group_public_id)
    assignment, activity = _speaking_or_404(group, speaking_public_id)
    tz_name = _tz_name()
    operational = _group_is_operational(group)
    reference_utc = utc_reference_now()

    published = assignment.status == _PUBLISHED
    has_submissions = activity_has_submissions(activity.id)
    frozen_message = _authoring_block(assignment, activity)
    actor_public_id = current_user.public_id

    return _private_no_store(
        "teacher/speaking/detail.html",
        group=group,
        activity=build_speaking_detail(assignment, activity, tz_name, reference_utc),
        operational=operational,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=tz_name,
        published=published,
        has_submissions=has_submissions,
        frozen_message=frozen_message,
        readiness=[] if published else speaking_publication_blockers(assignment, activity),
        submissions_url=_submissions_url(group_public_id, speaking_public_id),
        edit_url=_edit_url(group_public_id, speaking_public_id),
        publish_token=(
            _make_token(
                "speaking-publication", action=_PUBLISH_ACTION,
                teacher_public_id=actor_public_id, group_public_id=group_public_id,
                speaking_public_id=speaking_public_id, status=assignment.status,
            )
            if operational and not published
            else None
        ),
        withdraw_token=(
            _make_token(
                "speaking-publication", action=_WITHDRAW_ACTION,
                teacher_public_id=actor_public_id, group_public_id=group_public_id,
                speaking_public_id=speaking_public_id, status=assignment.status,
            )
            if operational and published and not has_submissions
            else None
        ),
    )


# ======================================================================
# Create -- one Assignment and one extension row, one transaction
# ======================================================================


def _render_create_page(form, actor_id, actor_public_id, group_public_id,
                        nonce=None, message=None):
    """Render the create form, redirect, or 404.

    Releases any write lock first (harmless on a GET or a plain form
    failure) and then re-proves the **whole** authorization chain --
    including the acting Teacher's own role and account status -- from
    current state, before anything is rendered or any token is minted.
    `message` is flashed **only after** that has passed, so a request
    whose access ended in the same window that caused the failure 404s
    silently instead of leaving a message behind for whatever page the
    actor reaches next.

    A rejected create keeps the **same** nonce, so the Teacher's retry is
    still covered by the duplicate-request protection rather than being
    handed a brand-new one that would let a double submission through.
    """
    db.session.rollback()
    group, _, _, _ = _fresh_speaking_authorization(actor_id, group_public_id)
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        # The chain was archived while this request was in flight. Never
        # hand back a form that cannot save.
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_list_url(group_public_id))
    return _private_no_store(
        "teacher/speaking/form.html",
        form=form,
        group=group,
        activity=None,
        state_token=_make_token(
            "speaking-create",
            teacher_public_id=actor_public_id,
            group_public_id=group_public_id,
            nonce=nonce or secrets.token_hex(32),
        ),
        cancel_url=_list_url(group_public_id),
        tz_name=_tz_name(),
    )


def _replay_response(group_public_id, nonce):
    """An ordinary or concurrent replay of an already-succeeded create
    request: redirect to the activity that nonce already created, without
    creating anything new. Idempotent by design.

    Returns ``None`` when the nonce names nothing, which is the ordinary
    first-submission case.
    """
    existing = SpeakingActivity.query.filter_by(creation_nonce=nonce).first()
    if existing is None:
        return None
    flash(
        f"Speaking activity '{existing.assignment.title}' was already created.", "success"
    )
    return redirect(_detail_url(group_public_id, existing.public_id))


@teacher_bp.route("/groups/<group_public_id>/speaking/new", methods=["GET", "POST"])
@roles_required(UserRole.TEACHER.value)
def speaking_create(group_public_id):
    """Create one Speaking activity: one ``assignments`` row and one
    ``speaking_activities`` row, atomically.

    **Nothing about identity, ownership or lifecycle is taken from the
    request.** The Group is the authorized public identifier in the URL;
    ``status`` is ``draft``; ``published_at`` is ``NULL``; every
    ``public_id`` is server-generated; both extension timestamps are the
    request's post-lock authoritative moment; and the ``creation_nonce``
    comes from the signed token this server minted. A forged ``group_id``
    / ``assignment_id`` / ``status`` / ``published_at`` / ``public_id``
    field has nowhere to land.

    **The two rows commit together.** An extension can never exist without
    its backing Assignment, and an Assignment created here is never left
    as an ordinary Assignment by a partial write.

    **Replay is idempotent.** The signed create token carries a random
    nonce, checked before anything is written and again inside the locked
    transaction; ``speaking_activities.creation_nonce`` is UNIQUE, so a
    genuinely concurrent replay loses at the database and is turned into
    the existing activity rather than a second one.
    """
    preview_group = _teacher_group_or_404(group_public_id)
    actor_id = current_user.id
    actor_public_id = current_user.public_id
    tz_name = _tz_name()

    if not _group_is_operational(preview_group):
        # Helpful early check: keeps a bookmarked URL from silently
        # rendering a form that can no longer save. Deliberately NOT the
        # enforcement point -- the post-lock check below is.
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_list_url(group_public_id))

    if request.method == "GET":
        return _render_create_page(
            SpeakingActivityForm(tz_name=tz_name, group_id=preview_group.id),
            actor_id, actor_public_id, group_public_id,
        )

    submitted_token = request.form.get("speaking_state", "")
    payload = _load_token(submitted_token, "speaking-create")
    if (
        payload is None
        or payload["teacher_public_id"] != actor_public_id
        or payload["group_public_id"] != group_public_id
    ):
        db.session.rollback()
        flash(
            "This form could not be verified (it may be old or was opened in another tab). "
            "Please try again.",
            "danger",
        )
        return redirect(_new_url(group_public_id))
    nonce = payload["nonce"]

    # An ordinary replay -- the Teacher pressed Create twice, or reloaded
    # the POST -- resolves here, before anything is written.
    replay = _replay_response(group_public_id, nonce)
    if replay is not None:
        return replay

    form = SpeakingActivityForm(tz_name=tz_name, group_id=preview_group.id)
    if not form.validate_on_submit():
        return _render_create_page(
            form, actor_id, actor_public_id, group_public_id, nonce=nonce
        )

    title = form.title.data.strip()
    instructions = form.instructions.data.strip()
    opens_at = form.opens_at_utc
    due_at = form.due_at_utc
    term_id, level_id, course_id = _hierarchy_context(preview_group)

    hierarchy, group, teacher, teacher_assignment, _, _ = _lock_speaking_chain(
        group_public_id, term_id, level_id, course_id, actor_id
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id, _SPEAKING_BLOCK_WORDING
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_list_url(group_public_id))

    # A genuinely concurrent replay: another request may have committed
    # this same nonce while this one was validating.
    replay = _replay_response(group_public_id, nonce)
    if replay is not None:
        db.session.rollback()
        return replay

    # The authoritative write moment: read only now, after every lock that
    # could have blocked, and truncated to the whole second the columns
    # can actually hold. Both extension timestamps get the SAME value.
    now_utc = _write_moment()

    assignment = Assignment(
        group_id=group.id,
        title=title,
        instructions=instructions,
        opens_at=opens_at,
        due_at=due_at,
        status=_DRAFT,
        published_at=None,
    )
    activity = SpeakingActivity(
        assignment=assignment,
        creation_nonce=nonce,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.session.add_all([assignment, activity])
    try:
        db.session.commit()
    except IntegrityError:
        # Roll back FIRST -- everything read is now discarded state and
        # must not be used as evidence of anything, least of all
        # authorization.
        db.session.rollback()
        replay = _replay_response(group_public_id, nonce)
        if replay is not None:
            return replay  # a concurrent winner committed this nonce
        return _render_create_page(
            form, actor_id, actor_public_id, group_public_id, nonce=nonce,
            message=(
                "This speaking activity could not be saved. An assignment or speaking "
                "activity with this title may already exist in the group, or the group may "
                "have just changed. Please reload and try again."
            ),
        )

    activity_public_id = activity.public_id
    flash(
        f"Speaking activity '{title}' created as a draft. Students cannot see it yet.",
        "success",
    )
    return redirect(_detail_url(group_public_id, activity_public_id))


# ======================================================================
# Edit -- title, instructions and the time window
# ======================================================================


def _render_edit_page(
    actor_id, actor_public_id, group_public_id, speaking_public_id,
    form=None, state_token=None, message=None,
):
    """Render the editor, redirect, or 404.

    Rolls back first, re-proves the whole nested chain from current state,
    and only then flashes, mints a token or renders anything. `form` and
    `state_token` are supplied only by the ordinary-validation and
    ``IntegrityError`` paths, which must show the Teacher their attempted
    values again with the **original** token -- a freshly minted token may
    only ever pair with freshly loaded persisted values, which is exactly
    the bypass the stale rejection exists to close.
    """
    db.session.rollback()
    group, assignment, activity, _ = _fresh_speaking_authorization(
        actor_id, group_public_id, speaking_public_id
    )
    if message is not None:
        flash(message, "danger")
    if not _group_is_operational(group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    # A courtesy check with the same rule the write path enforces: never
    # hand back a form whose save is already refused.
    frozen = _authoring_block(assignment, activity)
    if frozen is not None:
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    tz_name = _tz_name()
    if form is None:
        form = SpeakingActivityForm(
            formdata=None,
            data={
                "title": assignment.title,
                "instructions": assignment.instructions,
                "opens_at": to_app_local(tz_name, assignment.opens_at),
                "due_at": to_app_local(tz_name, assignment.due_at),
            },
            tz_name=tz_name,
            group_id=group.id,
            assignment_id=assignment.id,
        )
    if state_token is None:
        state_token = _make_token(
            "speaking-edit",
            teacher_public_id=actor_public_id, group_public_id=group_public_id,
            speaking_public_id=speaking_public_id, **_edit_snapshot(assignment),
        )

    return _private_no_store(
        "teacher/speaking/form.html",
        form=form,
        group=group,
        activity=build_speaking_detail(
            assignment, activity, tz_name, utc_reference_now()
        ),
        state_token=state_token,
        cancel_url=_detail_url(group_public_id, speaking_public_id),
        tz_name=tz_name,
    )


@teacher_bp.route(
    "/groups/<group_public_id>/speaking/<speaking_public_id>/edit",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def speaking_edit(group_public_id, speaking_public_id):
    """Revise this activity's title, instructions and time window.

    **Order of the post-lock checks.** Authorization and nested ownership
    first, so an unauthorized attempt fails identically whatever the
    activity contains; then the operational chain; then the authoring
    freeze; then the signed snapshot against the locked rows; then
    ordinary field validation. Only then is any field assigned -- so every
    rejection leaves the stored rows and their timestamps exactly as they
    were.

    ``status`` and ``published_at`` are never assigned here, so a publish
    or withdraw that committed while this form was open is left intact.
    An authorized, non-stale save of unchanged values is a **no-op**:
    nothing is written at all, not even ``updated_at``.
    """
    if request.method == "GET":
        # The actor's identity is captured as plain scalars HERE, before
        # `_render_edit_page` performs its rollback, and the page then
        # re-proves that actor against current state.
        return _render_edit_page(
            current_user.id, current_user.public_id,
            group_public_id, speaking_public_id,
        )

    preview_group = _teacher_group_or_404(group_public_id)
    preview_assignment, preview_activity = _speaking_or_404(
        preview_group, speaking_public_id
    )
    tz_name = _tz_name()

    if not _group_is_operational(preview_group):
        flash(_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    form = SpeakingActivityForm(
        tz_name=tz_name, group_id=preview_group.id, assignment_id=preview_assignment.id
    )
    form_is_valid = form.validate_on_submit()

    # Every scalar the rest of this request needs is captured BEFORE the
    # transaction reset below, so nothing between that reset and the
    # required locks triggers a lazy ORM or `current_user` reload that
    # would establish a fresh read snapshot ahead of the locks.
    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    assignment_id = preview_assignment.id
    activity_id = preview_activity.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)
    submitted_token = request.form.get("speaking_state", "")

    hierarchy, group, teacher, teacher_assignment, assignment, activity = (
        _lock_speaking_chain(
            group_public_id, term_id, level_id, course_id, teacher_id,
            assignment_id, activity_id,
        )
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)
    if _speaking_ownership_broken(group, assignment, activity, speaking_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id, _SPEAKING_BLOCK_WORDING
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    frozen = _authoring_block(assignment, activity)
    if frozen is not None:
        db.session.rollback()
        flash(frozen, "danger")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    # The snapshot is re-checked against the LOCKED current row -- this is
    # what closes the window between the form's GET and these locks.
    if _token_is_stale(
        submitted_token, "speaking-edit",
        teacher_public_id=teacher_public_id, group_public_id=group_public_id,
        speaking_public_id=speaking_public_id, **_edit_snapshot(assignment),
    ):
        # Post/Redirect/Get, discarding every attempted value: pairing a
        # fresh token with values written against the OLD state is
        # precisely the bypass this rejection exists to close.
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_edit_url(group_public_id, speaking_public_id))

    if not form_is_valid:
        # Ordinary validation failure with a still-valid snapshot: show
        # the attempted values again with the ORIGINAL token.
        return _render_edit_page(
            teacher_id, teacher_public_id, group_public_id, speaking_public_id,
            form=form, state_token=submitted_token,
        )

    title = form.title.data.strip()
    instructions = form.instructions.data.strip()
    opens_at = form.opens_at_utc
    due_at = form.due_at_utc

    # An authorized save of identical values is a no-op: not an edit, so
    # no timestamp moves. It still had to pass every check above.
    if (
        assignment.title == title
        and assignment.instructions == instructions
        and assignment.opens_at == opens_at
        and assignment.due_at == due_at
    ):
        db.session.rollback()
        flash("This speaking activity is unchanged, so nothing was saved.", "info")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    now_utc = _write_moment()
    assignment.title = title
    assignment.instructions = instructions
    assignment.opens_at = opens_at
    assignment.due_at = due_at
    activity.updated_at = now_utc
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_edit_page(
            teacher_id, teacher_public_id, group_public_id, speaking_public_id,
            form=form, state_token=submitted_token,
            message=(
                "This speaking activity could not be saved. An assignment or speaking "
                "activity with this title may already exist in the group. Please reload and "
                "try again."
            ),
        )

    flash(f"Speaking activity '{title}' updated.", "success")
    return redirect(_detail_url(group_public_id, speaking_public_id))


# ======================================================================
# Publish and withdraw
# ======================================================================


def _publication_transition(group_public_id, speaking_public_id, action):
    """The shared body of Publish and Withdraw.

    One function so the two directions cannot drift in their
    authorization, locking, freeze or staleness handling -- only the
    decision they reach and the sentence they flash differ.

    Publishing stamps a **fresh** authoritative whole-second UTC
    ``published_at``; withdrawing returns the activity to ``draft`` and
    clears it. Withdrawal is permitted only while **no** recording exists:
    once one does, the activity is frozen permanently.
    """
    preview_group = _teacher_group_or_404(group_public_id)
    preview_assignment, preview_activity = _speaking_or_404(
        preview_group, speaking_public_id
    )

    if not _group_is_operational(preview_group):
        flash(_PUBLICATION_NOT_OPERATIONAL_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    assignment_id = preview_assignment.id
    activity_id = preview_activity.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)
    submitted_token = request.form.get("speaking_state", "")

    hierarchy, group, teacher, teacher_assignment, assignment, activity = (
        _lock_speaking_chain(
            group_public_id, term_id, level_id, course_id, teacher_id,
            assignment_id, activity_id,
        )
    )
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)
    if _speaking_ownership_broken(group, assignment, activity, speaking_public_id):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id, _PUBLICATION_BLOCK_WORDING
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    if _token_is_stale(
        submitted_token, "speaking-publication", action=action,
        teacher_public_id=teacher_public_id, group_public_id=group_public_id,
        speaking_public_id=speaking_public_id, status=assignment.status,
    ):
        db.session.rollback()
        flash(_STALE_MESSAGE, "danger")
        return redirect(_detail_url(group_public_id, speaking_public_id))

    now_utc = _write_moment()

    if action == _PUBLISH_ACTION:
        if assignment.status == _PUBLISHED:
            db.session.rollback()
            flash("This speaking activity is already published.", "info")
            return redirect(_detail_url(group_public_id, speaking_public_id))
        blockers = speaking_publication_blockers(assignment, activity)
        if blockers:
            db.session.rollback()
            for sentence in blockers:
                flash(sentence, "danger")
            return redirect(_detail_url(group_public_id, speaking_public_id))
        assignment.status = _PUBLISHED
        assignment.published_at = now_utc
    else:
        if assignment.status != _PUBLISHED:
            db.session.rollback()
            flash("This speaking activity is already a draft.", "info")
            return redirect(_detail_url(group_public_id, speaking_public_id))
        # Withdrawal is permitted only while nobody has recorded. Once a
        # recording exists the activity is frozen permanently.
        if activity_has_submissions(activity.id):
            db.session.rollback()
            flash(_FROZEN_BY_SUBMISSIONS_MESSAGE, "danger")
            return redirect(_detail_url(group_public_id, speaking_public_id))
        assignment.status = _DRAFT
        assignment.published_at = None

    activity.updated_at = now_utc
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _fresh_speaking_authorization(teacher_id, group_public_id, speaking_public_id)
        flash(
            "This speaking activity could not be updated. Someone may have just changed it. "
            "Please reload and try again.",
            "danger",
        )
        return redirect(_detail_url(group_public_id, speaking_public_id))

    flash(
        "Speaking activity published. Enrolled students can open it once its opening time "
        "arrives."
        if action == _PUBLISH_ACTION
        else "Speaking activity withdrawn. It is a draft again and students cannot see it.",
        "success",
    )
    return redirect(_detail_url(group_public_id, speaking_public_id))


@teacher_bp.post("/groups/<group_public_id>/speaking/<speaking_public_id>/publish")
@roles_required(UserRole.TEACHER.value)
def speaking_publish(group_public_id, speaking_public_id):
    return _publication_transition(group_public_id, speaking_public_id, _PUBLISH_ACTION)


@teacher_bp.post("/groups/<group_public_id>/speaking/<speaking_public_id>/withdraw")
@roles_required(UserRole.TEACHER.value)
def speaking_withdraw(group_public_id, speaking_public_id):
    return _publication_transition(group_public_id, speaking_public_id, _WITHDRAW_ACTION)


# ======================================================================
# Submission review -- read only
# ======================================================================


@teacher_bp.get(
    "/groups/<group_public_id>/speaking/<speaking_public_id>/submissions"
)
@roles_required(UserRole.TEACHER.value)
def speaking_submissions(group_public_id, speaking_public_id):
    """One bounded page of this activity's recordings, newest first.

    Fixed page size, SQL ordering (``submitted_at DESC, id DESC``), SQL
    ``LIMIT PAGE_SIZE + 1`` for the has-next flag with **no** ``COUNT``
    and no disclosed total, and the same page normalization and
    past-the-end fallback every other Teacher list uses. The Student
    display name comes from the same joined statement, and **one** extra
    bounded page-level query resolves every row's feedback state, so the
    page costs a fixed number of queries no matter how many rows it shows.

    Read-only: there is no score, no grade, no delete, no replace and no
    resubmit control here, and none exists server-side either.
    """
    group = _teacher_group_or_404(group_public_id)
    assignment, activity = _speaking_or_404(group, speaking_public_id)
    tz_name = _tz_name()
    page = normalize_page(request.args.get("page"))

    rows, has_next = teacher_speaking_submissions_page(activity.id, page)
    if not rows and page > 1:
        page = 1
        rows, has_next = teacher_speaking_submissions_page(activity.id, page)

    states = feedback_states_for_page(activity.id, [row.public_id for row in rows])

    return _private_no_store(
        "teacher/speaking/submissions.html",
        group=group,
        activity=build_speaking_detail(
            assignment, activity, tz_name, utc_reference_now()
        ),
        submissions=attach_feedback_states(
            build_teacher_submission_view(rows, tz_name), states
        ),
        tz_name=tz_name,
        page=page,
        has_next=has_next,
        has_prev=page > 1,
        page_size=PAGE_SIZE,
        back_url=_detail_url(group_public_id, speaking_public_id),
    )


@teacher_bp.get(
    "/groups/<group_public_id>/speaking/<speaking_public_id>"
    "/submissions/<submission_public_id>"
)
@roles_required(UserRole.TEACHER.value)
def speaking_submission_detail(
    group_public_id, speaking_public_id, submission_public_id
):
    """One recording, read only, with an authorized player -- or a
    non-disclosing 404.

    All three public ids must name the same nested chain: the Group must
    be one this Teacher is actively assigned to, the activity must belong
    to that Group, and the recording must belong to that activity. Any
    other combination produces no row and the same 404.

    The player's ``src`` is this module's own authorized audio route, not
    a static URL and not a storage key. Nothing on the page discloses the
    storage key, the resolved path, the digest, the byte size, the
    uploader or any internal id.
    """
    group = _teacher_group_or_404(group_public_id)
    assignment, activity = _speaking_or_404(group, speaking_public_id)
    submission = _submission_or_404(activity, submission_public_id)
    tz_name = _tz_name()

    student_name = student_name_for_submission(submission)
    if student_name is None:
        # A foreign key proves the row exists, never its role. A
        # role-inconsistent recording fails closed rather than being
        # presented as Student work.
        abort(404)

    if audio_upload_for_submission(submission) is None:
        # The association is broken or is not an audio file. The page
        # would offer a player that could only 404; fail closed instead.
        abort(404)

    panel = build_feedback_panel(
        teacher_speaking_feedback(activity.id, submission_public_id), tz_name
    )
    urls = _audio_urls(group_public_id, speaking_public_id, submission_public_id)

    return _private_no_store(
        "teacher/speaking/submission_detail.html",
        group=group,
        activity=build_speaking_detail(
            assignment, activity, tz_name, utc_reference_now()
        ),
        submission={
            "public_id": submission.public_id,
            "student_name": student_name,
            "submitted_local": to_app_local(tz_name, submission.submitted_at),
        },
        player={"src": urls["src"], "label": student_name},
        download_url=urls["download"],
        feedback=panel,
        can_write_feedback=(
            _group_is_operational(group) and panel["state"] != FEEDBACK_INVALID
        ),
        feedback_url=_feedback_url(
            group_public_id, speaking_public_id, submission_public_id
        ),
        submissions_url=_submissions_url(group_public_id, speaking_public_id),
        tz_name=tz_name,
    )


# ======================================================================
# Feedback -- one shared record per recording
# ======================================================================


def _render_feedback_page(
    actor_id, actor_public_id, group_public_id, speaking_public_id,
    submission_public_id, form=None, state_token=None, message=None,
):
    """Render the feedback editor, or 404.

    Releases any write lock first and then re-proves the **whole**
    authorization chain -- including the acting Teacher's own role and
    account status -- before anything private is fetched, rendered, or
    signed into a token. The rollback is exactly why that is necessary:
    the locks are gone, and the decorator that ran at the start of the
    request is no longer current evidence.

    `form` and `state_token` are supplied only by the ordinary-validation
    path, which must show the Teacher their attempted text again with the
    **original** token, never a freshly minted one.
    """
    db.session.rollback()
    group, assignment, activity, submission = _fresh_speaking_authorization(
        actor_id, group_public_id, speaking_public_id, submission_public_id
    )
    if message is not None:
        flash(message, "danger")
    tz_name = _tz_name()

    student_name = student_name_for_submission(submission)
    if student_name is None:
        abort(404)
    if audio_upload_for_submission(submission) is None:
        abort(404)

    panel = build_feedback_panel(
        teacher_speaking_feedback(activity.id, submission_public_id), tz_name,
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
            form = SpeakingFeedbackForm(
                formdata=None, data={"feedback_text": panel.get("text", "")}
            )
        if state_token is None:
            feedback_public_id, version = _feedback_state_values(panel)
            state_token = _make_token(
                "speaking-feedback",
                teacher_public_id=actor_public_id,
                group_public_id=group_public_id,
                assignment_public_id=assignment.public_id,
                speaking_public_id=speaking_public_id,
                submission_public_id=submission_public_id,
                feedback_public_id=feedback_public_id,
                version=version,
            )
    else:
        form = None
        state_token = None

    urls = _audio_urls(group_public_id, speaking_public_id, submission_public_id)
    return _private_no_store(
        "teacher/speaking/feedback.html",
        group=group,
        activity=build_speaking_detail(
            assignment, activity, tz_name, utc_reference_now()
        ),
        submission={
            "public_id": submission.public_id,
            "student_name": student_name,
            "submitted_local": to_app_local(tz_name, submission.submitted_at),
        },
        player={"src": urls["src"], "label": student_name},
        download_url=urls["download"],
        feedback=panel,
        form=form,
        can_write=can_write,
        feedback_state_token=state_token,
        archived_labels=[] if operational else _archived_chain_labels(group),
        tz_name=tz_name,
        submission_url=_submission_url(
            group_public_id, speaking_public_id, submission_public_id
        ),
    )


def _redirect_feedback(group_public_id, speaking_public_id, submission_public_id):
    return redirect(
        _feedback_url(group_public_id, speaking_public_id, submission_public_id)
    )


@teacher_bp.route(
    "/groups/<group_public_id>/speaking/<speaking_public_id>"
    "/submissions/<submission_public_id>/feedback",
    methods=["GET", "POST"],
)
@roles_required(UserRole.TEACHER.value)
def speaking_submission_feedback(
    group_public_id, speaking_public_id, submission_public_id
):
    """Read, create, or revise the one feedback record on one recording.

    **Nothing about ownership is taken from the request.** The reviewer is
    the authenticated session, the recording / activity / Group are the
    authorized nested public identifiers in the URL, ``version`` comes
    from the locked row, and both timestamps are generated on the server
    after the locks. A forged ``reviewer_id`` / ``speaking_submission_id``
    / ``version`` / ``updated_at`` field in the POST body has nowhere to
    land -- the form carries only ``feedback_text``.

    **Order of the post-lock checks.** Authorization and nested ownership
    come first, so an unauthorized attempt fails identically whether or
    not feedback already exists. The operational chain, the existing row's
    reviewer integrity, the signed token and finally ordinary field
    validation follow. Only then is the no-op compared, and only then is
    any field assigned -- so every rejection leaves the stored row, its
    version, its timestamps and its attribution exactly as they were.

    **The deadline is deliberately irrelevant here.** Feedback is written
    *after* work is handed in, so a past-due activity is the normal case,
    and existing feedback stays readable forever. What a *write* still
    requires is an operational academic chain and a current active Teacher
    assignment.
    """
    if request.method == "GET":
        return _render_feedback_page(
            current_user.id, current_user.public_id,
            group_public_id, speaking_public_id, submission_public_id,
        )

    # ------------------------------------------------------------------
    # Pre-lock preview: the same object authorization every nested Teacher
    # route uses. Friendly and fast, but NOT authoritative.
    # ------------------------------------------------------------------
    preview_group = _teacher_group_or_404(group_public_id)
    preview_assignment, preview_activity = _speaking_or_404(
        preview_group, speaking_public_id
    )
    preview_submission = _submission_or_404(preview_activity, submission_public_id)

    if not _group_is_operational(preview_group):
        flash(_FEEDBACK_NOT_OPERATIONAL_MESSAGE, "danger")
        return _redirect_feedback(
            group_public_id, speaking_public_id, submission_public_id
        )

    form = SpeakingFeedbackForm()
    form_is_valid = form.validate_on_submit()
    feedback_text = form.normalized_feedback()

    teacher_id = current_user.id
    teacher_public_id = current_user.public_id
    assignment_public_id = preview_assignment.public_id
    owner_id = preview_submission.student_id
    submission_id = preview_submission.id
    assignment_id = preview_assignment.id
    activity_id = preview_activity.id
    term_id, level_id, course_id = _hierarchy_context(preview_group)
    submitted_token = request.form.get("feedback_state", "")

    (
        hierarchy, group, users, teacher_assignment, assignment, activity, submission,
        feedback,
    ) = _lock_feedback_chain(
        group_public_id, term_id, level_id, course_id, teacher_id, owner_id,
        assignment_id, activity_id, submission_id,
    )

    teacher = users.get(teacher_id)
    if _authz_broken(group, teacher, teacher_assignment):
        db.session.rollback()
        abort(404)
    if _speaking_ownership_broken(group, assignment, activity, speaking_public_id):
        db.session.rollback()
        abort(404)
    if _submission_ownership_broken(
        activity, submission, users.get(owner_id), submission_public_id
    ):
        db.session.rollback()
        abort(404)

    blocked = _operational_block(
        hierarchy, group, term_id, level_id, course_id, _FEEDBACK_BLOCK_WORDING
    )
    if blocked is not None:
        db.session.rollback()
        flash(blocked, "danger")
        return _redirect_feedback(
            group_public_id, speaking_public_id, submission_public_id
        )

    # The locked row must really be this recording's feedback. It was
    # located BY `submission_id`, so this can only fail on genuine
    # corruption -- it is asserted rather than assumed because the write
    # below trusts the row's identity.
    if feedback is not None and feedback.speaking_submission_id != submission.id:
        db.session.rollback()
        flash(_FEEDBACK_INTEGRITY_MESSAGE, "danger")
        return _redirect_feedback(
            group_public_id, speaking_public_id, submission_public_id
        )

    # A role-inconsistent existing row fails closed. It is NOT treated as
    # absent (which would attempt a duplicate insertion the unique
    # constraint would reject) and it is NOT silently overwritten, which
    # would destroy the record while pretending to repair it.
    if not _existing_reviewer_is_teacher(feedback):
        db.session.rollback()
        flash(_FEEDBACK_INTEGRITY_MESSAGE, "danger")
        return _redirect_feedback(
            group_public_id, speaking_public_id, submission_public_id
        )

    # The token is re-checked against the LOCKED current row -- this is
    # what closes the window between the form's GET and these locks, and
    # what makes two co-teachers racing on an empty record resolve as
    # "first save wins, second is told to reload".
    if _token_is_stale(
        submitted_token, "speaking-feedback",
        teacher_public_id=teacher_public_id,
        group_public_id=group_public_id,
        assignment_public_id=assignment_public_id,
        speaking_public_id=speaking_public_id,
        submission_public_id=submission_public_id,
        feedback_public_id=None if feedback is None else feedback.public_id,
        version=None if feedback is None else feedback.version,
    ):
        db.session.rollback()
        flash(_FEEDBACK_STALE_MESSAGE, "danger")
        return _redirect_feedback(
            group_public_id, speaking_public_id, submission_public_id
        )

    if not form_is_valid:
        # Ordinary validation failure with a still-valid context: show the
        # attempted text again with the ORIGINAL token, so the Teacher can
        # fix it without losing what they wrote and without the expected
        # version being silently refreshed underneath them.
        return _render_feedback_page(
            teacher_id, teacher_public_id, group_public_id, speaking_public_id,
            submission_public_id, form=form, state_token=submitted_token,
        )

    # An authorized save of identical normalized text is a no-op: not an
    # edit, so no version increment, no new timestamp and no change of
    # attribution. It still had to pass every check above.
    if feedback is not None and feedback.feedback_text == feedback_text:
        db.session.rollback()
        flash("This feedback is unchanged, so nothing was saved.", "info")
        return redirect(
            _submission_url(group_public_id, speaking_public_id, submission_public_id)
        )

    now_utc = _write_moment()
    created = feedback is None
    if created:
        db.session.add(
            SpeakingFeedback(
                speaking_submission_id=submission.id,
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
        # `created_at`, `id`, `public_id` and `speaking_submission_id` are
        # never assigned here: a revision is the same record, not a new one.

    try:
        db.session.commit()
    except IntegrityError:
        # 1. Roll back FIRST. Everything read before this point is now
        #    discarded state and must not be used as evidence of anything
        #    -- least of all authorization.
        db.session.rollback()
        # 2. Re-establish current authorization from scratch, using the
        #    pre-reset actor scalars. Whatever raised the IntegrityError
        #    may well have been a concurrent change that ALSO ended this
        #    Teacher's access. This aborts 404 without disclosure.
        _fresh_speaking_authorization(
            teacher_id, group_public_id, speaking_public_id, submission_public_id
        )
        # 3. A generic, safe message. Nothing here retries the write or
        #    overwrites a co-teacher who won the race.
        flash(
            "This feedback could not be saved. Someone may have just changed it. Please "
            "reload, read the current feedback, and try again.",
            "danger",
        )
        return _redirect_feedback(
            group_public_id, speaking_public_id, submission_public_id
        )

    # Conditional on purpose. A save is deliberately allowed on genuinely
    # historical work -- a withdrawn or suspended Student -- and in those
    # cases the Student cannot open the receipt at all right now.
    flash(
        "Feedback saved. It is visible to the student whenever they can open this speaking "
        "activity."
        if created
        else "Feedback updated. Only this latest version is kept, and it is what the student "
        "sees whenever they can open this speaking activity.",
        "success",
    )
    return redirect(
        _submission_url(group_public_id, speaking_public_id, submission_public_id)
    )


def _existing_reviewer_is_teacher(feedback):
    """Whether the existing feedback row's reviewer still has the Teacher
    role.

    **This is an integrity gate on write eligibility**, not a cosmetic
    check: a ``False`` refuses the save outright, so that a row whose
    ``reviewer_id`` no longer names a Teacher is neither silently
    overwritten nor treated as absent.

    It is an **ordinary ``SELECT``**, not ``SELECT ... FOR UPDATE``. The
    approved M06 lock order covers the acting Teacher and the recording's
    owner, and taking a third, out-of-order User lock here would break the
    project-wide ascending-internal-id rule that keeps this route
    deadlock-compatible with the Administrator membership and account
    write paths -- exactly the reasoning M03 already recorded.

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
    read is consistent. Neither outcome deletes or discloses anything. No
    real-MySQL behaviour is claimed here.
    """
    if feedback is None:
        return True
    role = db.session.query(User.role).filter(User.id == feedback.reviewer_id).first()
    return role is not None and role[0] == _TEACHER


# ======================================================================
# Authorized audio serving + audit
# ======================================================================


def _serve_submission_audio(
    group_public_id, speaking_public_id, submission_public_id, force_attachment
):
    """Re-authorize, re-validate, then serve.

    Every audio request proves the **complete** nested chain again from
    current state rather than trusting that a page once rendered a
    ``<source>``: an active assigned Teacher, this Group, this activity
    under this Group, this recording under this activity, an owner who is
    still a Student, and an ``uploaded_files`` row that still exists and
    whose server-determined category is still ``audio``. Any break yields
    the identical non-disclosing 404 -- another Group's recording, another
    activity's recording, an ordinary Assignment's public id and a
    nonexistent id are indistinguishable.

    The bytes themselves go through the shared M12 serving core, which
    resolves a containment-checked path from the random ``storage_key``
    (so a traversal or a direct storage path can never be requested),
    persists the ``inline`` / ``download`` ``FileAccessLog`` entry
    **before** any byte is sent and refuses to serve at all if that audit
    row cannot be committed, sends the stored canonical **audio** content
    type with ``X-Content-Type-Options: nosniff``, and sets
    ``Cache-Control: private, no-store, max-age=0``. A missing physical
    file 404s without exposing the resolved path.

    Nothing in the response discloses the ``storage_key``, the resolved
    filesystem path, the SHA-256 digest, the uploader's identity or any
    internal id.
    """
    group = _teacher_group_or_404(group_public_id)
    _assignment, activity = _speaking_or_404(group, speaking_public_id)
    submission = _submission_or_404(activity, submission_public_id)
    uploaded_file = audio_upload_for_submission(submission)
    if uploaded_file is None:
        abort(404)
    return serve_uploaded_file(uploaded_file, current_user.id, force_attachment)


@teacher_bp.get(
    "/groups/<group_public_id>/speaking/<speaking_public_id>"
    "/submissions/<submission_public_id>/audio"
)
@roles_required(UserRole.TEACHER.value)
def speaking_submission_audio(
    group_public_id, speaking_public_id, submission_public_id
):
    return _serve_submission_audio(
        group_public_id, speaking_public_id, submission_public_id, force_attachment=False
    )


@teacher_bp.get(
    "/groups/<group_public_id>/speaking/<speaking_public_id>"
    "/submissions/<submission_public_id>/audio/download"
)
@roles_required(UserRole.TEACHER.value)
def speaking_submission_audio_download(
    group_public_id, speaking_public_id, submission_public_id
):
    return _serve_submission_audio(
        group_public_id, speaking_public_id, submission_public_id, force_attachment=True
    )
