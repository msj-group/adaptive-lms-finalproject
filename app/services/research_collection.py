"""Research account/subject locking and natural-use session lifecycle."""
from collections import namedtuple
import uuid
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ResearchCollectionStatus,
    ResearchConfiguration,
    ResearchProvenance,
    ResearchSession,
    ResearchSessionEndReason,
    ResearchStatusBasis,
    ResearchSubject,
    ResearchSubjectLink,
    User,
    generate_subject_code,
    now_ms,
)
from app.models.submission_feedback import whole_second_utc
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.research_delivery_scope import DeliveryProof
from app.services.research_dictionary import is_opaque_ref as is_uuid
from app.services.research_scope import (
    account_is_eligible_student,
    configuration_is_live,
    session_provenance,
    subject_is_included,
)

#: How many successor references one resolution may walk.
_MAX_CHAIN = 5
#: Attempts at the lock chain when a concurrent first write (or, in theory,
#: a subject-code collision) makes the automatic provisioning lose a race.
_PROVISION_ATTEMPTS = 3
_SUCCESSOR_NAMESPACE = uuid.UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")

# Outcome codes.
NOT_COLLECTING = "not_collecting"
RECORDED = "recorded"
RETRY = "retry"
STALE_DELIVERY_SCOPE = "stale_delivery_scope"



# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


def _lock_user(user_id):
    return db.session.query(User).filter(User.id == user_id).with_for_update().first()


def _lock_active_configuration(shared=True):
    # Writers lock configuration primary records before changing the unique
    # current-marker index. Locking that index first can create an InnoDB
    # cycle with activation. Resolve the marker without locks, then lock its
    # primary record in the same order as writers; eligibility/scope are
    # re-proved on that locked row, including if it retired while waiting.
    configuration_id = (
        db.session.query(ResearchConfiguration.id)
        .filter(ResearchConfiguration.current_marker == 1)
        .scalar()
    )
    if configuration_id is None:
        return None
    return (
        db.session.query(ResearchConfiguration)
        .filter(ResearchConfiguration.id == configuration_id)
        .with_for_update(read=shared)
        .first()
    )


def _lock_subject_for_user(user_id):
    subject_id = (
        db.session.query(ResearchSubjectLink.subject_id)
        .filter(ResearchSubjectLink.user_id == user_id)
        .scalar()
    )
    if subject_id is None:
        return None
    return (
        db.session.query(ResearchSubject)
        .filter(ResearchSubject.id == subject_id)
        .with_for_update()
        .first()
    )


def _lock_session(public_id):
    return (
        db.session.query(ResearchSession)
        .filter(ResearchSession.public_id == public_id)
        .with_for_update()
        .first()
    )


LockedChain = namedtuple("LockedChain", "user configuration subject")


def _provision_subject(user_id):
    """Create the subject and its link for an eligible Student who has none.
    The caller holds the Student's ``users`` lock."""
    now = whole_second_utc()
    subject = ResearchSubject(
        subject_code=generate_subject_code(),
        collection_status=ResearchCollectionStatus.INCLUDED.value,
        status_basis=ResearchStatusBasis.POPULATION_RULE.value,
        provenance=ResearchProvenance.STUDY.value,
        status_changed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.session.add(subject)
    db.session.flush()
    db.session.add(ResearchSubjectLink(subject_id=subject.id, user_id=user_id))
    db.session.flush()
    return subject


def lock_collection_chain(user_id, moment, provision=True, *,
                          delivery_proof=None, delivery_scope=None, required_schema=None):
    """Take the documented chain through the subject and re-prove
    eligibility. ``(chain, None)`` or ``(None, NOT_COLLECTING)``; on refusal
    the transaction is rolled back and nothing is written.

    With `provision`, an eligible Student without a subject gets one (see the
    module docstring); without it -- the feedback-prompt actions, which can
    only concern an existing subject -- the absence is a refusal.
    """
    for _ in range(_PROVISION_ATTEMPTS):
        lock_academic_hierarchy()
        user = _lock_user(user_id)
        configuration = _lock_active_configuration()
        if not (
            account_is_eligible_student(user)
            and configuration_is_live(configuration, _naive(moment))
            and (required_schema is None or configuration.event_schema_version == required_schema)
        ):
            db.session.rollback()
            return None, NOT_COLLECTING
        if delivery_proof is not None or delivery_scope is not None:
            if not isinstance(delivery_proof, DeliveryProof) or not delivery_proof.matches(
                delivery_scope, user.public_id, user.auth_version,
                configuration.public_id, configuration.version_number,
            ):
                db.session.rollback()
                return None, STALE_DELIVERY_SCOPE
        subject = _lock_subject_for_user(user_id)
        if subject is None:
            if not provision:
                db.session.rollback()
                return None, NOT_COLLECTING
            try:
                subject = _provision_subject(user_id)
            except IntegrityError:
                # A concurrent first write won the unique link (or a code
                # collided): start again and reuse what is there now.
                db.session.rollback()
                continue
        if not subject_is_included(subject):
            db.session.rollback()
            return None, NOT_COLLECTING
        return LockedChain(user, configuration, subject), None
    db.session.rollback()
    return None, NOT_COLLECTING


def _naive(moment_ms):
    return datetime.fromtimestamp(moment_ms / 1000, tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


def successor_ref(ref):
    """The deterministic next reference after `ref`. Two tabs that find the
    same session stale at the same moment rotate to the **same** successor,
    so they keep sharing one session instead of forking two."""
    return str(uuid.uuid5(_SUCCESSOR_NAMESPACE, f"{ref}/next"))


def new_session_ref():
    return str(uuid.uuid4())


def _usable(session, configuration, moment):
    inactivity_ms = configuration.session_inactivity_minutes * 60 * 1000
    return (
        session.is_open
        and session.configuration_id == configuration.id
        and moment - session.last_seen_at_ms <= inactivity_ms
    )


def close_session(session, configuration, moment, reason=None):
    """End an open session. With no explicit `reason` the end is derived:
    ``configuration_changed`` if the session belongs to another version,
    otherwise ``inactivity`` when its idle time exceeds the timeout. An idle
    session ends at its last activity, never at a moment nobody was seen."""
    if not session.is_open:
        return
    inactivity_ms = configuration.session_inactivity_minutes * 60 * 1000
    idle = moment - session.last_seen_at_ms > inactivity_ms
    if reason is None:
        if session.configuration_id != configuration.id:
            reason = ResearchSessionEndReason.CONFIGURATION_CHANGED.value
        else:
            reason = ResearchSessionEndReason.INACTIVITY.value
    if idle or reason == ResearchSessionEndReason.CONFIGURATION_CHANGED.value:
        session.ended_at_ms = session.last_seen_at_ms
    else:
        session.ended_at_ms = max(moment, session.last_seen_at_ms)
    session.end_reason = reason


def resolve_session(chain, session_ref, moment, deployment_provenance, start_ms=None):
    """The open session this browser should write into, created if needed,
    and the reference the browser must keep.

    The reference is a server-generated UUID held in the signed session
    cookie -- the client can read it but never choose it. A row carrying the
    reference is used only if it belongs to the acting subject; anything
    else (a copied cookie, a reference minted for another account) is never
    adopted, and a fresh random reference replaces it.
    """
    ref = session_ref if is_uuid(session_ref) else new_session_ref()
    for _ in range(_MAX_CHAIN):
        session = _lock_session(ref)
        if session is None:
            break
        if session.subject_id != chain.subject.id:
            ref = new_session_ref()
            continue
        if _usable(session, chain.configuration, moment):
            return session, ref
        close_session(session, chain.configuration, moment)
        ref = successor_ref(ref)
    else:
        ref = new_session_ref()

    started = moment if start_ms is None else min(start_ms, moment)
    session = ResearchSession(
        public_id=ref,
        subject_id=chain.subject.id,
        configuration_id=chain.configuration.id,
        provenance=session_provenance(chain.subject.provenance, deployment_provenance),
        started_at_ms=started,
        last_seen_at_ms=moment,
    )
    db.session.add(session)
    db.session.flush()
    return session, ref


# ---------------------------------------------------------------------------
# Client batches
# ---------------------------------------------------------------------------


Outcome = namedtuple("Outcome", "event_type detail count position page_id")


def end_session_on_logout(user_id, session_ref):
    """Close the browser's session on logout. Best-effort: never raises."""
    if not is_uuid(session_ref):
        return
    try:
        moment = now_ms()
        lock_academic_hierarchy()
        _lock_user(user_id)
        configuration = _lock_active_configuration()
        subject = _lock_subject_for_user(user_id)
        session = _lock_session(session_ref)
        if subject is None or session is None or session.subject_id != subject.id \
                or not session.is_open:
            db.session.rollback()
            return
        if configuration is None:
            configuration = db.session.get(ResearchConfiguration, session.configuration_id)
        close_session(session, configuration, moment, ResearchSessionEndReason.LOGOUT.value)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:  # pragma: no cover
            pass
        current_app.logger.exception("Ending a research session on logout failed")


def close_open_sessions(configuration, moment, reason, subject_id=None):
    """Close every open session of `configuration` (or of one subject),
    locked in ascending id. The caller holds the earlier locks and commits."""
    query = db.session.query(ResearchSession).filter(ResearchSession.ended_at_ms.is_(None))
    if subject_id is not None:
        query = query.filter(ResearchSession.subject_id == subject_id)
    else:
        query = query.filter(ResearchSession.configuration_id == configuration.id)
    sessions = query.order_by(ResearchSession.id.asc()).with_for_update().all()
    for session in sessions:
        own = configuration if session.configuration_id == configuration.id else \
            db.session.get(ResearchConfiguration, session.configuration_id)
        inactivity_ms = own.session_inactivity_minutes * 60 * 1000
        if moment - session.last_seen_at_ms > inactivity_ms:
            close_session(session, own, moment, ResearchSessionEndReason.INACTIVITY.value)
        else:
            close_session(session, own, moment, reason)
    return len(sessions)
