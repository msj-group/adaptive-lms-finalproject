"""The authoritative write path for the Phase 6 / M01 research workspace.

Flask-independent -- no ``request``, ``abort``, ``flash``, ``redirect``,
template or logger, exactly like
``app/services/lesson_progress_transactions.py``. Each function takes the
locks, re-proves every rule against the **locked** rows, writes, and commits
or rolls back before returning, so no lock outlives a call. The route reads
the returned status constant and decides the response.

**One lock order, every M01 write**::

    lock_academic_hierarchy() reset point
    -> User rows (ascending internal id): the acting account,
       and the linked Student when the write has one
    -> ResearchConsentDocument rows (ascending internal id)
    -> ResearchParticipant

It is one order, not one per route, because the order is the thing that must
not vary. A caller passes only the links its write has and the chain stops
where the arguments stop -- a participant **create** locks the acting
Administrator and the Student and no document, because no document is
involved and no participant row exists yet.

Why that order, specifically:

- ``lock_academic_hierarchy`` with no ids locks nothing; it is called
  because it owns this project's single deliberate
  ``db.session.rollback()`` before a request's first lock, so an M01 write
  never starts inside a stale REPEATABLE READ snapshot.
- **Users ascending by internal id**, never "actor then student", so two
  requests that involve the same two accounts in opposite roles cannot
  deadlock against each other.
- **Documents before participants**: document activation locks documents and
  never a participant, and a consent decision locks the document before the
  participant, so the lock graph gains no reverse edge.
- **Locking the Student's User row** is what serialises two concurrent
  invitations for the same Student, and what makes "still an active
  Student" a fact rather than a memory --
  ``uq_research_participants_student_id`` remains the final defense.

**A foreign key to ``users`` proves existence, never a role.** Every
function here re-reads the acting account, and the linked Student, from
their locked rows and re-proves role and status there. ``roles_required``
ran once, before the view; a request that waited behind somebody else's lock
needs the answer as it is now.

**Stale-state checks run twice**: the route checks its signed token against
freshly read state before the locks (cheap rejection), and passes a
``stale_check`` callable that this module calls **again** with the locked
values before it writes anything. The callable is how the token check stays
in ``app/services/research_tokens.py`` and the route while the authoritative
comparison happens inside the transaction.

**Nothing here is consent by implication.** Creating a participant records
``invited`` and no event. Only :func:`record_consent_decision` writes a
consent event, only the linked Student can reach it, and it writes the
participant's new state and the append-only event in **one** transaction and
one commit -- so a stored status and its history can never disagree.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no REPEATABLE
READ snapshot isolation, so this runs correctly in tests without locking
anything. Tests assert the *requested* lock set and order (structural); they
prove nothing about real InnoDB blocking.
"""

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    CURRENT_MARKER,
    ResearchConsentAction,
    ResearchConsentDocument,
    ResearchConsentDocumentStatus,
    ResearchConsentEvent,
    ResearchParticipant,
    ResearchParticipantStatus,
    User,
    UserRole,
    UserStatus,
    consent_digest,
    generate_participant_code,
)
from app.models.research_participant import ALLOWED_PARTICIPANT_TRANSITIONS
from app.models.submission_feedback import whole_second_utc
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value
_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value

_DRAFT = ResearchConsentDocumentStatus.DRAFT.value
_DOCUMENT_ACTIVE = ResearchConsentDocumentStatus.ACTIVE.value
_SUPERSEDED = ResearchConsentDocumentStatus.SUPERSEDED.value

_INVITED = ResearchParticipantStatus.INVITED.value
_PARTICIPANT_ACTIVE = ResearchParticipantStatus.ACTIVE.value
_DECLINED = ResearchParticipantStatus.DECLINED.value
_WITHDRAWN = ResearchParticipantStatus.WITHDRAWN.value

#: Fresh participant codes tried before a create gives up. A collision in a
#: 30-character, 10-position alphabet is already implausible; this exists so
#: that if one ever happens it is a retry rather than a failed invitation.
MAX_CODE_ATTEMPTS = 5

# Outcome constants. Every caller must handle every one it can receive.
CREATED = "created"
RECORDED = "recorded"
ACTIVATED = "activated"
ALREADY = "already"
STALE = "stale"
CONFLICT = "conflict"
NOT_FOUND = "not_found"
UNAUTHORIZED = "unauthorized"
NOT_A_STUDENT = "not_a_student"
SUSPENDED_STUDENT = "suspended_student"
ALREADY_LINKED = "already_linked"
NO_ACTIVE_DOCUMENT = "no_active_document"
DOCUMENT_CHANGED = "document_changed"
NOT_ALLOWED = "not_allowed"
NOT_ACTIVATABLE = "not_activatable"
VERSION_TAKEN = "version_taken"

#: Which participant status each intended action asks for.
_TARGET_STATUS = {
    ResearchConsentAction.ACCEPTED.value: _PARTICIPANT_ACTIVE,
    ResearchConsentAction.DECLINED.value: _DECLINED,
    ResearchConsentAction.WITHDRAWN.value: _WITHDRAWN,
}


class ResearchLocks:
    """The rows one M01 lock chain returned.

    Any value may be ``None`` -- a vanished row, a suspended actor -- and
    every such case is a rejection, never "keep going". ``documents`` maps
    internal document id to the locked row. ``__slots__``-ed, so a typo in a
    caller raises instead of silently reading ``None``.
    """

    __slots__ = ("hierarchy", "actor", "student", "documents", "participant")

    def __init__(self, hierarchy, actor=None, student=None, documents=None, participant=None):
        self.hierarchy = hierarchy
        self.actor = actor
        self.student = student
        self.documents = {} if documents is None else documents
        self.participant = participant


def lock_research_chain(actor_id, student_id=None, document_ids=(), participant_id=None):
    """Take the M01 lock order in one open transaction, stopping wherever the
    caller's arguments stop. Returns a :class:`ResearchLocks`.

    Every id is an **internal** id discovered by non-locking reads before
    this call. Those reads only decide *which* rows to lock; roles, statuses,
    ownership, lifecycles and versions are all re-proved against the locked
    rows afterwards.
    """
    hierarchy = lock_academic_hierarchy()
    wanted_users = sorted({uid for uid in (actor_id, student_id) if uid is not None})
    locked_users = {
        user_id: User.query.filter_by(id=user_id).with_for_update().first()
        for user_id in wanted_users
    }
    documents = {
        document_id: ResearchConsentDocument.query.filter_by(id=document_id)
        .with_for_update()
        .first()
        for document_id in sorted({d for d in document_ids if d is not None})
    }
    participant = None
    if participant_id is not None:
        participant = (
            ResearchParticipant.query.filter_by(id=participant_id).with_for_update().first()
        )
    return ResearchLocks(
        hierarchy,
        actor=locked_users.get(actor_id),
        student=locked_users.get(student_id),
        documents=documents,
        participant=participant,
    )


def administrator_authz_broken(locks):
    """``True`` when the **locked** acting account is no longer an active
    Administrator."""
    actor = locks.actor
    return actor is None or actor.role != _ADMINISTRATOR or actor.status != _USER_ACTIVE


def student_authz_broken(locks):
    """``True`` when the **locked** acting account is no longer an active
    Student."""
    actor = locks.actor
    return actor is None or actor.role != _STUDENT or actor.status != _USER_ACTIVE


def current_document_id():
    """The internal id of the one ``active`` consent document, or ``None``.

    A non-locking read used twice: once to decide what to lock, and once
    **after** the locks to prove the answer did not move underneath. It reads
    a single id column, never the wording.
    """
    row = (
        db.session.query(ResearchConsentDocument.id)
        .filter(ResearchConsentDocument.status == _DOCUMENT_ACTIVE)
        .first()
    )
    return None if row is None else row[0]


def participant_id_for_student(student_id):
    """The internal id of `student_id`'s participant, or ``None``. One id
    column; no name, email or wording is loaded."""
    row = (
        db.session.query(ResearchParticipant.id)
        .filter(ResearchParticipant.student_id == student_id)
        .first()
    )
    return None if row is None else row[0]


def _code_is_taken(code):
    return (
        db.session.query(ResearchParticipant.id)
        .filter(ResearchParticipant.participant_code == code)
        .first()
        is not None
    )


def _fresh_participant_code():
    """A code no live row holds, chosen under the caller's open locks.
    ``uq_research_participants_code`` stays the final defense."""
    for _ in range(MAX_CODE_ATTEMPTS):
        code = generate_participant_code()
        if not _code_is_taken(code):
            return code
    return None


# ---------------------------------------------------------------------------
# Administrator: create a participant for an active Student
# ---------------------------------------------------------------------------


def create_participant(actor_id, student_id, stale_check):
    """Create one ``invited`` :class:`ResearchParticipant` for `student_id`.

    Returns ``(outcome, participant_or_None)``. Creating a participant is an
    **invitation**, never consent: no consent event is written, no status
    beyond ``invited`` is reachable here, and the Student is the only one who
    can move it.

    Writes nothing outside ``research_participants``: no ``User``,
    ``Enrollment``, ``Group``, academic or financial row is read for writing,
    touched or flushed.

    `stale_check` is called with the Student's **locked** role, status and
    participant-existence and must return ``True`` when the submitted form no
    longer describes them.
    """
    locks = lock_research_chain(actor_id, student_id=student_id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        return UNAUTHORIZED, None

    student = locks.student
    if student is None:
        db.session.rollback()
        return NOT_FOUND, None
    if student.role != _STUDENT:
        db.session.rollback()
        return NOT_A_STUDENT, None
    if student.status != _USER_ACTIVE:
        db.session.rollback()
        return SUSPENDED_STUDENT, None

    existing = participant_id_for_student(student.id)
    if stale_check(student.role, student.status, existing is not None):
        db.session.rollback()
        return STALE, None
    if existing is not None:
        db.session.rollback()
        return ALREADY_LINKED, None

    code = _fresh_participant_code()
    if code is None:  # pragma: no cover -- implausible; kept honest anyway
        db.session.rollback()
        return CONFLICT, None

    moment = whole_second_utc()
    participant = ResearchParticipant(
        student_id=student.id,
        participant_code=code,
        status=_INVITED,
        invited_by_id=actor_id,
        version=1,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(participant)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT, None
    return CREATED, participant


# ---------------------------------------------------------------------------
# Administrator: write a draft consent document
# ---------------------------------------------------------------------------


def create_consent_document(actor_id, version_identifier, title, body):
    """Create one ``draft`` :class:`ResearchConsentDocument`.

    Returns ``(outcome, document_or_None)``. A draft is **not** consent text
    yet: it is never presented to a Student, and only
    :func:`activate_consent_document` makes it presentable.

    The digest is computed here, from the normalised values that are about
    to be stored, so what is hashed and what is saved cannot differ. The
    caller normalises the text (``app/services/research_text.py``); this
    function stores it and nothing else.

    A version identifier another document already holds comes back as
    :data:`VERSION_TAKEN` -- proved once under the actor's lock and again by
    ``uq_research_consent_documents_version``, which stays the final defense.
    """
    locks = lock_research_chain(actor_id)
    if administrator_authz_broken(locks):
        db.session.rollback()
        return UNAUTHORIZED, None

    taken = (
        db.session.query(ResearchConsentDocument.id)
        .filter(ResearchConsentDocument.version_identifier == version_identifier)
        .first()
    )
    if taken is not None:
        db.session.rollback()
        return VERSION_TAKEN, None

    moment = whole_second_utc()
    document = ResearchConsentDocument(
        version_identifier=version_identifier,
        title=title,
        body=body,
        body_digest=consent_digest(version_identifier, title, body),
        status=_DRAFT,
        current_marker=None,
        created_by_id=actor_id,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(document)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT, None
    return CREATED, document


# ---------------------------------------------------------------------------
# Administrator: activate a draft consent document
# ---------------------------------------------------------------------------


def activate_consent_document(actor_id, document_id, stale_check):
    """Activate the draft `document_id`, superseding whatever is current.

    Returns one outcome constant. Activation freezes the document's wording
    permanently and is the only way a document becomes presentable to a
    Student.

    `stale_check` is called with the locked draft's digest and the locked
    current document's public id and digest (or ``None`` for both when
    nothing is current) and must return ``True`` when the submitted form no
    longer describes them.
    """
    previewed_current = current_document_id()
    locks = lock_research_chain(
        actor_id, document_ids=(document_id, previewed_current)
    )
    if administrator_authz_broken(locks):
        db.session.rollback()
        return UNAUTHORIZED

    draft = locks.documents.get(document_id)
    if draft is None:
        db.session.rollback()
        return NOT_FOUND
    if draft.status == _DOCUMENT_ACTIVE:
        db.session.rollback()
        return ALREADY
    if draft.status != _DRAFT:
        db.session.rollback()
        return NOT_ACTIVATABLE
    if not draft.digest_matches():
        db.session.rollback()
        return CONFLICT

    # Re-read which document is current now that every candidate row is
    # locked. A different answer means somebody activated between the
    # preview and the locks, so this request is holding the wrong row.
    if current_document_id() != previewed_current:
        db.session.rollback()
        return STALE
    current = locks.documents.get(previewed_current) if previewed_current else None
    if previewed_current is not None and (
        current is None or current.status != _DOCUMENT_ACTIVE
    ):
        db.session.rollback()
        return STALE

    if stale_check(
        draft.body_digest,
        None if current is None else current.public_id,
        None if current is None else current.body_digest,
    ):
        db.session.rollback()
        return STALE

    moment = whole_second_utc()
    if current is not None:
        # Cleared and flushed **before** the new marker is set. Within one
        # flush SQLAlchemy orders UPDATEs by primary key, not by assignment,
        # so a draft with a lower id than the outgoing document would
        # otherwise momentarily give two rows ``current_marker = 1`` and
        # break uq_research_consent_documents_current, which neither MySQL
        # nor SQLite defers to the end of the transaction.
        current.status = _SUPERSEDED
        current.current_marker = None
        current.superseded_at = moment
        current.superseded_by_id = actor_id
        current.updated_at = moment
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            return CONFLICT

    draft.status = _DOCUMENT_ACTIVE
    draft.current_marker = CURRENT_MARKER
    draft.activated_at = moment
    draft.activated_by_id = actor_id
    draft.updated_at = moment
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT
    return ACTIVATED


# ---------------------------------------------------------------------------
# Student: accept, decline or withdraw
# ---------------------------------------------------------------------------


def record_consent_decision(actor_id, action, stale_check):
    """Record one explicit consent decision by the acting **Student**.

    `action` is a :class:`~app.models.enums.ResearchConsentAction` value.
    Returns one outcome constant.

    The participant is found *from* ``actor_id`` -- there is no participant
    identifier in the request at all -- and ``participant.student_id ==
    actor_id`` is then proved again against the locked row, so no
    Administrator, Researcher or other Student can consent for anybody.

    An acceptance or a refusal is taken against the one ``active`` document,
    re-proved active and digest-consistent under its lock; a draft or
    superseded document is never accepted. A withdrawal is recorded against
    the document the participant actually accepted, so it needs no active
    document at all -- withdrawing must not depend on the center having
    published new wording.

    The participant's new state and the append-only consent event are written
    in **one** transaction and one commit.

    `stale_check` is called with the locked participant's status and version
    and the locked document's public id, version identifier and digest, and
    must return ``True`` when the submitted form no longer describes them.
    """
    target = _TARGET_STATUS.get(action)
    if target is None:  # pragma: no cover -- programming error
        raise ValueError(f"Unknown research consent action: {action!r}")

    participant_id = participant_id_for_student(actor_id)
    if participant_id is None:
        return NOT_FOUND

    withdrawing = action == ResearchConsentAction.WITHDRAWN.value
    if withdrawing:
        row = (
            db.session.query(ResearchParticipant.consent_document_id)
            .filter(ResearchParticipant.id == participant_id)
            .first()
        )
        previewed_document = None if row is None else row[0]
    else:
        previewed_document = current_document_id()
        if previewed_document is None:
            return NO_ACTIVE_DOCUMENT

    locks = lock_research_chain(
        actor_id, document_ids=(previewed_document,), participant_id=participant_id
    )
    if student_authz_broken(locks):
        db.session.rollback()
        return UNAUTHORIZED

    participant = locks.participant
    if participant is None or participant.student_id != actor_id:
        db.session.rollback()
        return NOT_FOUND

    # An identical repeated request is a safe no-op: nothing is written, the
    # version does not move, and no second history row is created.
    if participant.status == target:
        db.session.rollback()
        return ALREADY
    if (participant.status, target) not in ALLOWED_PARTICIPANT_TRANSITIONS:
        db.session.rollback()
        return NOT_ALLOWED

    document = locks.documents.get(previewed_document)
    if document is None:
        db.session.rollback()
        return NOT_FOUND
    if withdrawing:
        # The withdrawal is recorded against the document that was actually
        # accepted, which the locked participant names.
        if participant.consent_document_id != document.id:
            db.session.rollback()
            return DOCUMENT_CHANGED
    else:
        if current_document_id() != document.id or document.status != _DOCUMENT_ACTIVE:
            db.session.rollback()
            return DOCUMENT_CHANGED
        if not document.digest_matches():
            db.session.rollback()
            return DOCUMENT_CHANGED

    if stale_check(
        participant.status,
        participant.version,
        document.public_id,
        document.version_identifier,
        document.body_digest,
    ):
        db.session.rollback()
        return STALE

    moment = whole_second_utc()
    participant.status = target
    participant.consent_document_id = document.id
    participant.decided_at = moment
    participant.withdrawn_at = moment if target == _WITHDRAWN else None
    participant.version = participant.version + 1
    participant.updated_at = moment
    db.session.add(
        ResearchConsentEvent(
            participant_id=participant.id,
            consent_document_id=document.id,
            action=action,
            actor_id=actor_id,
            consent_version=document.version_identifier,
            consent_digest=document.body_digest,
            occurred_at=moment,
        )
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT
    return RECORDED
