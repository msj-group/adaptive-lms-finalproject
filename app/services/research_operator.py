"""Operator actions outside every portal (Phase 6 replacement, corrected).

These run only from ``scripts/research_operator.py`` on the server, by the
project owners. Collection itself needs **no** operator step: every eligible
Student is collected automatically under the population rule
(``research_scope``). The operator tool exists for the genuine exceptions,
which need an identity that no product view may show:

- ``exclude`` -- the centre's external process excludes a Student. Recorded
  even before the Student was ever collected, enforced on every write, and
  audited; open sessions are closed at once.
- ``reinstate`` -- an operator lifts an exclusion. Audited. A legacy
  exclusion (carried over from a refusal or withdrawal in the superseded
  consent workflow) is lifted only with ``allow_legacy_override``: a
  deliberate statement that the external process changed it.
- ``status`` / ``excluded_accounts`` -- a Student's research code and
  state, or every excluded account: identity recovery, for the operator
  only. The Researcher workspace lists exclusions by subject code alone.
- ``retention_report`` -- what the deployment's retention expires, deleted
  only with an explicit ``execute``.

None of these records consent or an ethics approval, and none is triggered
by an email address or domain: the operator names one existing account.

**Lock order**, the collection order's prefix::

    reset -> users row (the Student) -> research_subjects -> research_sessions
"""

from collections import namedtuple
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ResearchAuditAction,
    ResearchAuditChannel,
    ResearchAuditEvent,
    ResearchCollectionStatus,
    ResearchConfiguration,
    ResearchEvent,
    ResearchExport,
    ResearchExportArchive,
    ResearchFeedbackPrompt,
    ResearchProvenance,
    ResearchSession,
    ResearchSessionEndReason,
    ResearchStatusBasis,
    ResearchSubject,
    ResearchSubjectLink,
    User,
    UserRole,
    generate_subject_code,
    now_ms,
)
from app.models.submission_feedback import whole_second_utc
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy

EXCLUDED = "excluded"
REINSTATED = "reinstated"
UNCHANGED = "unchanged"
NO_ACCOUNT = "no_account"
NOT_A_STUDENT = "not_a_student"
LEGACY_EXCLUSION = "legacy_exclusion"
NO_SUBJECT = "no_subject"
NOT_EXCLUDED = "not_excluded"
CONFLICT = "conflict"

_INCLUDED = ResearchCollectionStatus.INCLUDED.value
_EXCLUDED = ResearchCollectionStatus.EXCLUDED.value
_LEGACY = ResearchStatusBasis.LEGACY_COLLECTION_EXCLUSION.value

SubjectState = namedtuple("SubjectState", "subject_code collection_status status_basis provenance")
ExcludedAccount = namedtuple("ExcludedAccount", "email subject_code status_basis status_changed_at")


def _lock_student(email, actor_id):
    user_id = db.session.query(User.id).filter(User.email == (email or "").strip().lower()).scalar()
    lock_academic_hierarchy()
    ids = sorted({value for value in [user_id, actor_id] if value is not None})
    rows = {row.id: row for row in User.query.filter(User.id.in_(ids)).order_by(User.id).populate_existing().with_for_update().all()}
    actor = rows.get(actor_id)
    if actor is None or actor.role != "researcher" or actor.status != "active":
        raise ValueError("An authenticated active Researcher is required for operator actions.")
    from app.services.actor_authorization import require_operator_authentication
    require_operator_authentication(actor)
    return rows.get(user_id)


def _lock_subject(user_id):
    subject_id = db.session.query(ResearchSubjectLink.subject_id).filter(
        ResearchSubjectLink.user_id == user_id).scalar()
    if subject_id is None:
        return None
    return db.session.query(ResearchSubject).filter(
        ResearchSubject.id == subject_id).with_for_update().first()


def _new_subject(user_id, status, basis, provenance):
    now = whole_second_utc()
    subject = ResearchSubject(
        subject_code=generate_subject_code(), collection_status=status, status_basis=basis,
        provenance=provenance, status_changed_at=now, created_at=now, updated_at=now,
    )
    db.session.add(subject)
    db.session.flush()
    db.session.add(ResearchSubjectLink(subject_id=subject.id, user_id=user_id))
    return subject


def _audit(action, subject_id, detail, count=None, *, actor_id):
    db.session.add(ResearchAuditEvent(
        action=action, channel=ResearchAuditChannel.OPERATOR.value, actor_id=actor_id,
        subject_id=subject_id, detail_code=detail, count_value=count,
    ))


def _commit(result, code):
    """Commit, or roll back on a database refusal (for example a concurrent
    subject link for the same account): nothing partial is kept."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT, None
    return result, code


def _student_or_refusal(email, actor_id):
    user = _lock_student(email, actor_id)
    if user is None:
        db.session.rollback()
        return None, NO_ACCOUNT
    if user.role != UserRole.STUDENT.value:
        db.session.rollback()
        return None, NOT_A_STUDENT
    return user, None


def exclude(email, *, actor_id):
    """Record an exclusion through the external process. ``(status, code)``.

    A Student with no subject yet gets an excluded one, so the population
    rule can never collect them later; open sessions close at once.
    """
    user, refusal = _student_or_refusal(email, actor_id)
    if refusal:
        return refusal, None
    basis = ResearchStatusBasis.EXTERNAL_EXCLUSION.value
    subject = _lock_subject(user.id)
    closed = 0
    if subject is None:
        subject = _new_subject(user.id, _EXCLUDED, basis, ResearchProvenance.STUDY.value)
        db.session.flush()
    else:
        if subject.collection_status == _EXCLUDED:
            db.session.rollback()
            return UNCHANGED, subject.subject_code
        now = whole_second_utc()
        subject.collection_status = _EXCLUDED
        subject.status_basis = basis
        subject.status_changed_at = now
        subject.updated_at = now
        closed = _close_subject_sessions(subject.id)
    _audit(ResearchAuditAction.SUBJECT_EXCLUDED.value, subject.id, basis, closed, actor_id=actor_id)
    return _commit(EXCLUDED, subject.subject_code)


def reinstate(email, allow_legacy_override=False, *, actor_id):
    """Lift an exclusion. ``(status, code)``."""
    user, refusal = _student_or_refusal(email, actor_id)
    if refusal:
        return refusal, None
    subject = _lock_subject(user.id)
    if subject is None:
        db.session.rollback()
        return NO_SUBJECT, None
    if subject.collection_status != _EXCLUDED:
        db.session.rollback()
        return NOT_EXCLUDED, subject.subject_code
    if subject.status_basis == _LEGACY and not allow_legacy_override:
        db.session.rollback()
        return LEGACY_EXCLUSION, subject.subject_code
    lifted = subject.status_basis
    now = whole_second_utc()
    subject.collection_status = _INCLUDED
    subject.status_basis = ResearchStatusBasis.OPERATOR_REINSTATEMENT.value
    subject.status_changed_at = now
    subject.updated_at = now
    _audit(ResearchAuditAction.SUBJECT_REINSTATED.value, subject.id, f"lifted:{lifted}", actor_id=actor_id)
    return _commit(REINSTATED, subject.subject_code)


def _close_subject_sessions(subject_id):
    from app.services.research_collection import close_session

    moment = now_ms()
    sessions = (
        db.session.query(ResearchSession)
        .filter(ResearchSession.subject_id == subject_id, ResearchSession.ended_at_ms.is_(None))
        .order_by(ResearchSession.id.asc())
        .with_for_update()
        .all()
    )
    for session in sessions:
        configuration = db.session.get(ResearchConfiguration, session.configuration_id)
        close_session(session, configuration, moment,
                      ResearchSessionEndReason.SUBJECT_INELIGIBLE.value)
    return len(sessions)


def status(email, *, actor_id):
    """``(status, SubjectState | None)`` -- identity recovery for the
    operator only. Read-only."""
    _authorize_operator_read(actor_id, "status")
    user_id = db.session.query(User.id).filter(User.email == (email or "").strip().lower()).scalar()
    if user_id is None:
        return NO_ACCOUNT, None
    row = (
        db.session.query(ResearchSubject.subject_code, ResearchSubject.collection_status,
                         ResearchSubject.status_basis, ResearchSubject.provenance)
        .join(ResearchSubjectLink, ResearchSubjectLink.subject_id == ResearchSubject.id)
        .filter(ResearchSubjectLink.user_id == user_id)
        .first()
    )
    if row is None:
        return NO_SUBJECT, None
    return "found", SubjectState(*row)


def excluded_accounts(*, actor_id, page=1):
    """Every excluded Student with its account email, newest decision first.
    Identity recovery for the operator only. Read-only."""
    _authorize_operator_read(actor_id, "list_excluded")
    rows = (
        db.session.query(User.email, ResearchSubject.subject_code, ResearchSubject.status_basis,
                         ResearchSubject.status_changed_at)
        .join(ResearchSubjectLink, ResearchSubjectLink.user_id == User.id)
        .join(ResearchSubject, ResearchSubject.id == ResearchSubjectLink.subject_id)
        .filter(ResearchSubject.collection_status == _EXCLUDED)
        .order_by(ResearchSubject.status_changed_at.desc(), ResearchSubject.id.desc())
        .offset((max(1, int(page)) - 1) * 100).limit(100)
        .all()
    )
    return [ExcludedAccount(*row) for row in rows]


def _authorize_operator_read(actor_id, detail):
    actor = User.query.filter_by(id=actor_id, role="researcher", status="active").populate_existing().with_for_update().first()
    if actor is None:
        raise ValueError("An authenticated active Researcher is required for operator actions.")
    from app.services.actor_authorization import require_operator_authentication
    require_operator_authentication(actor)
    _audit("operator_read", None, detail, actor_id=actor_id)
    db.session.commit()


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

RetentionReport = namedtuple(
    "RetentionReport", "cutoff_ms sessions events prompts archives executed"
)


def retention_report(retention_days, moment_ms=None, execute=False, *, actor_id=None, service_principal=None):
    from app.services.research_retention import purge_expired
    return purge_expired(retention_days, moment_ms, execute, actor_id=actor_id, service_principal=service_principal)
