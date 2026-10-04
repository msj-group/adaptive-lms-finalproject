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
- ``mark_demo`` -- a demonstration account. Its data is collected as
  ``demo`` and never exported. Audited.
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
MARKED_DEMO = "marked_demo"
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
_DEMO = ResearchProvenance.DEMO.value

SubjectState = namedtuple("SubjectState", "subject_code collection_status status_basis provenance")
ExcludedAccount = namedtuple("ExcludedAccount", "email subject_code status_basis status_changed_at")


def _lock_student(email):
    lock_academic_hierarchy()
    user_id = db.session.query(User.id).filter(User.email == (email or "").strip().lower()).scalar()
    if user_id is None:
        return None
    return db.session.query(User).filter(User.id == user_id).with_for_update().first()


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


def _audit(action, subject_id, detail, count=None):
    db.session.add(ResearchAuditEvent(
        action=action, channel=ResearchAuditChannel.OPERATOR.value,
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


def _student_or_refusal(email):
    user = _lock_student(email)
    if user is None:
        db.session.rollback()
        return None, NO_ACCOUNT
    if user.role != UserRole.STUDENT.value:
        db.session.rollback()
        return None, NOT_A_STUDENT
    return user, None


def exclude(email):
    """Record an exclusion through the external process. ``(status, code)``.

    A Student with no subject yet gets an excluded one, so the population
    rule can never collect them later; open sessions close at once.
    """
    user, refusal = _student_or_refusal(email)
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
    _audit(ResearchAuditAction.SUBJECT_EXCLUDED.value, subject.id, basis, closed)
    return _commit(EXCLUDED, subject.subject_code)


def reinstate(email, allow_legacy_override=False):
    """Lift an exclusion. ``(status, code)``."""
    user, refusal = _student_or_refusal(email)
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
    _audit(ResearchAuditAction.SUBJECT_REINSTATED.value, subject.id, f"lifted:{lifted}")
    return _commit(REINSTATED, subject.subject_code)


def mark_demo(email):
    """Mark a demonstration account. ``(status, code)``. Its sessions are
    collected as ``demo`` from now on, and nothing of the subject -- earlier
    sessions included -- is ever exported."""
    user, refusal = _student_or_refusal(email)
    if refusal:
        return refusal, None
    subject = _lock_subject(user.id)
    if subject is None:
        subject = _new_subject(user.id, _INCLUDED, ResearchStatusBasis.POPULATION_RULE.value,
                               _DEMO)
        db.session.flush()
    elif subject.provenance == _DEMO:
        db.session.rollback()
        return UNCHANGED, subject.subject_code
    else:
        subject.provenance = _DEMO
        subject.updated_at = whole_second_utc()
    _audit(ResearchAuditAction.SUBJECT_MARKED_DEMO.value, subject.id, _DEMO)
    return _commit(MARKED_DEMO, subject.subject_code)


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


def status(email):
    """``(status, SubjectState | None)`` -- identity recovery for the
    operator only. Read-only."""
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


def excluded_accounts():
    """Every excluded Student with its account email, newest decision first.
    Identity recovery for the operator only. Read-only."""
    rows = (
        db.session.query(User.email, ResearchSubject.subject_code, ResearchSubject.status_basis,
                         ResearchSubject.status_changed_at)
        .join(ResearchSubjectLink, ResearchSubjectLink.user_id == User.id)
        .join(ResearchSubject, ResearchSubject.id == ResearchSubjectLink.subject_id)
        .filter(ResearchSubject.collection_status == _EXCLUDED)
        .order_by(ResearchSubject.status_changed_at.desc(), ResearchSubject.id.desc())
        .all()
    )
    return [ExcludedAccount(*row) for row in rows]


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

RetentionReport = namedtuple(
    "RetentionReport", "cutoff_ms sessions events prompts archives executed"
)


def retention_report(retention_days, moment_ms=None, execute=False):
    """Sessions whose last activity is older than `retention_days`, with
    their events and prompts, and every export archive that holds any data
    that old. Counts only unless `execute` is true.

    Deleting is irreversible and is the only hard deletion research data
    ever sees; it runs only on an explicit operator request, removes child
    rows first, and writes operator audit events. Subjects, their links,
    configurations, export descriptions and audit events are kept: exclusions
    must stay enforceable, and an export's description records what existed.
    An export archive never outlives the oldest data it contains.
    """
    if retention_days is None:
        raise ValueError("RESEARCH_RETENTION_DAYS is not configured")
    moment_ms = now_ms() if moment_ms is None else moment_ms
    cutoff = moment_ms - int(timedelta(days=retention_days).total_seconds() * 1000)
    expired = db.session.query(ResearchSession.id).filter(
        ResearchSession.last_seen_at_ms < cutoff)
    sessions = int(expired.count())
    events = int(db.session.query(func.count(ResearchEvent.id)).filter(
        ResearchEvent.session_id.in_(expired.scalar_subquery())).scalar() or 0)
    prompts = int(db.session.query(func.count(ResearchFeedbackPrompt.id)).filter(
        ResearchFeedbackPrompt.session_id.in_(expired.scalar_subquery())).scalar() or 0)
    expired_archives = (
        db.session.query(ResearchExportArchive.id)
        .join(ResearchExport, ResearchExport.id == ResearchExportArchive.export_id)
        .filter(ResearchExport.oldest_last_seen_ms < cutoff)
    )
    archives = int(expired_archives.count())
    if not execute or (sessions == 0 and archives == 0):
        db.session.rollback()
        return RetentionReport(cutoff, sessions, events, prompts, archives, False)
    archive_ids = [row[0] for row in expired_archives.all()]
    if archive_ids:
        db.session.query(ResearchExportArchive).filter(
            ResearchExportArchive.id.in_(archive_ids)).delete(synchronize_session=False)
        db.session.add(ResearchAuditEvent(
            action=ResearchAuditAction.RETENTION_PURGED.value,
            channel=ResearchAuditChannel.OPERATOR.value,
            detail_code=f"export_archives;days={retention_days}", count_value=archives,
        ))
    ids = [row[0] for row in expired.order_by(ResearchSession.id.asc()).all()]
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        db.session.query(ResearchEvent).filter(
            ResearchEvent.session_id.in_(chunk)).delete(synchronize_session=False)
        db.session.query(ResearchFeedbackPrompt).filter(
            ResearchFeedbackPrompt.session_id.in_(chunk)).delete(synchronize_session=False)
        db.session.query(ResearchSession).filter(
            ResearchSession.id.in_(chunk)).delete(synchronize_session=False)
    if ids:
        db.session.add(ResearchAuditEvent(
            action=ResearchAuditAction.RETENTION_PURGED.value,
            channel=ResearchAuditChannel.OPERATOR.value,
            detail_code=f"days={retention_days}", count_value=sessions,
        ))
    db.session.commit()
    return RetentionReport(cutoff, sessions, events, prompts, archives, True)
