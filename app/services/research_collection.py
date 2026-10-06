"""The natural-use collection write path (Phase 6 replacement).

Flask-independent: the caller passes the acting account's id, the research
session reference it read from the signed session cookie, the deployment
settings and the parsed request data, and stores whatever reference this
module hands back.

**One lock order, every collection write**::

    lock_academic_hierarchy() reset point (locks nothing)
    -> users row of the acting Student          (role and status re-proved)
    -> research_configurations active row       (shared: live, collecting, period)
    -> research_subjects row                    (provisioned if absent; included re-proved)
    -> research_sessions rows                   (resolution chain, in order)
    -> research_feedback_prompts rows

Researcher writes take ``users`` (the Researcher) and then configuration rows
exclusively, then sessions; operator writes take ``users`` (the Student), the
subject, then sessions. No path locks in the reverse direction, so the graph
has no cycle. SQLite honours neither ``FOR UPDATE`` nor ``FOR SHARE``: the
tests assert the *requested* order and prove nothing about InnoDB blocking.

**Eligibility is re-proved after the locks on every write** -- a batch that
was delayed until after a suspension, an exclusion, a pause or the end of the
collection period is refused, whatever the unlocked preview said.

**Automatic, idempotent subjects.** An eligible Student with no research
subject gets one at the first collection write, decided here on the server
from the locked account -- never from anything the client sends. The
``users`` row lock serialises a Student's concurrent first writes on InnoDB,
and ``uq_research_subject_links_user_id`` is the final defense: a request that
loses that race rolls back and retries, finding and reusing the winner's
subject. Later writes reuse the same pseudonymous identity. An excluded
subject is never touched by this path, so logging in or opening a page cannot
re-enrol anybody. The row records the population rule, not consent.

**Failure isolation.** Nothing here runs inside an academic transaction:
ingestion is its own request, and server outcomes are flushed after the LMS
request has already committed or rolled back (``record_outcomes``), exactly
like the M14 notification producers. :func:`record_outcomes` never raises.
"""

import uuid
from collections import namedtuple
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ResearchCollectionStatus,
    ResearchConfiguration,
    ResearchEvent,
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
from app.services import research_sampling as sampling
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.research_delivery_scope import DeliveryProof
from app.services.research_event_dictionary import SOURCE_CLIENT, SOURCE_SERVER, activity_end
from app.services.research_event_validation import is_uuid, validate_event
from app.services.research_scope import (
    account_is_eligible_student,
    configuration_is_live,
    session_provenance,
    subject_is_included,
)

#: A client event must not claim to have happened after the batch was sent
#: (with one second of rounding tolerance) ...
FUTURE_TOLERANCE_MS = 1_000
#: ... nor more than ten minutes before it. Older buffered events are late.
MAX_EVENT_AGE_MS = 10 * 60 * 1000
#: An event earlier than its session's start by more than this belongs to a
#: session that has already ended, and is late.
SESSION_START_TOLERANCE_MS = 60 * 1000
#: The largest gap between accepted observations that still counts as one
#: continuous observation run (the collector's heartbeat is 30 s).
OBSERVATION_GAP_MS = 75 * 1000
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

IngestResult = namedtuple(
    "IngestResult", "status session_ref accepted duplicates invalid late prompt_public_id"
)


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
                          delivery_proof=None, delivery_scope=None):
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


def _classify(batch, session_started_ms, received_ms):
    """Split a validated batch into accepted rows, and counts of invalid and
    late events. Duplicates are decided afterwards against the database."""
    offset = received_ms - batch.sent_at
    accepted, invalid, late = [], 0, 0
    for raw in batch.events:
        event = validate_event(raw)
        if event is None:
            invalid += 1
            continue
        if event.client_ts_ms - batch.sent_at > FUTURE_TOLERANCE_MS:
            invalid += 1
            continue
        corrected = event.client_ts_ms + offset
        too_old = batch.sent_at - event.client_ts_ms > MAX_EVENT_AGE_MS
        before_session = (
            session_started_ms is not None
            and corrected < session_started_ms - SESSION_START_TOLERANCE_MS
        )
        if too_old or before_session:
            late += 1
            continue
        accepted.append((event, corrected, offset))
    return accepted, invalid, late


def _existing_uids(uids):
    if not uids:
        return set()
    rows = db.session.query(ResearchEvent.event_uid).filter(ResearchEvent.event_uid.in_(uids))
    return {row[0] for row in rows}


def _advance_observation(session, accepted):
    """Move the observation run through the accepted events in time order.
    A hidden tab ends the run; a gap longer than :data:`OBSERVATION_GAP_MS`
    starts a new one. Only visible observation is ever counted."""
    since, last = session.observed_since_ms, session.last_observed_at_ms
    for event, corrected, _offset in sorted(accepted, key=lambda item: item[1]):
        if event.event_type == "visibility_hidden":
            since, last = None, None
            continue
        if since is None or corrected - last > OBSERVATION_GAP_MS:
            since = corrected
        last = corrected if last is None else max(last, corrected)
    session.observed_since_ms, session.last_observed_at_ms = since, last


def ingest_batch(user_id, session_ref, batch, deployment_provenance, tz_name,
                 received_ms=None, *, delivery_proof):
    """Validate, deduplicate and store one parsed client batch.

    Returns an :class:`IngestResult`. ``status`` is :data:`NOT_COLLECTING`
    (nothing written, the collector should stop), :data:`RECORDED`, or
    :data:`RETRY` (a concurrent duplicate raced this batch; nothing written).
    """
    received_ms = now_ms() if received_ms is None else received_ms
    if not isinstance(delivery_proof, DeliveryProof):
        db.session.rollback()
        return IngestResult(STALE_DELIVERY_SCOPE, None, 0, 0, 0, 0, None)
    chain, refused = lock_collection_chain(
        user_id, received_ms, delivery_proof=delivery_proof,
        delivery_scope=batch.delivery_scope,
    )
    if refused:
        return IngestResult(refused, None, 0, 0, 0, 0, None)

    # Classify against the session the reference names, when it is usable,
    # so events buffered before a rotation are recognised as late.
    probe_start = None
    if is_uuid(session_ref):
        existing = _lock_session(session_ref)
        if existing is not None and existing.subject_id == chain.subject.id \
                and _usable(existing, chain.configuration, received_ms):
            probe_start = existing.started_at_ms
    accepted, invalid, late = _classify(batch, probe_start, received_ms)

    seen = _existing_uids([event.event_uid for event, _c, _o in accepted])
    fresh, duplicates, batch_uids = [], 0, set()
    for item in accepted:
        uid = item[0].event_uid
        if uid in seen or uid in batch_uids:
            duplicates += 1
            continue
        batch_uids.add(uid)
        fresh.append(item)

    first_ms = min((corrected for _e, corrected, _o in fresh), default=None)
    session, ref = resolve_session(
        chain, session_ref, received_ms, deployment_provenance, start_ms=first_ms
    )
    for event, corrected, offset in fresh:
        db.session.add(
            ResearchEvent(
                event_uid=event.event_uid,
                session_id=session.id,
                source=SOURCE_CLIENT,
                event_type=event.event_type,
                page_id=event.page_id,
                element_id=event.element_id,
                detail_code=event.detail_code,
                count_value=event.count_value,
                duration_ms=event.duration_ms,
                position_value=event.position_value,
                page_view_ref=event.page_view_ref,
                tab_ref=event.tab_ref,
                sequence_number=event.sequence_number,
                client_ts_ms=event.client_ts_ms,
                clock_offset_ms=offset,
                occurred_at_ms=corrected,
                received_at_ms=received_ms,
            )
        )

    # A replayed batch re-sends events whose first answer never reached the
    # browser. A batch is stored atomically, so a replay whose accepted events
    # are all stored already is a re-delivery of a batch that was counted
    # when it first arrived: it adds no batch, duplicate, invalid, late or
    # dropped count. A replay of a batch that never arrived counts normally,
    # except that it reports no duplicates.
    redelivery = batch.replay and duplicates > 0 and not fresh
    if not redelivery:
        session.batches_received += 1
        session.events_accepted += len(fresh)
        if not batch.replay:
            session.events_duplicate += duplicates
        session.events_invalid += invalid
        session.events_late += late
        session.events_dropped_client = min(
            session.events_dropped_client + batch.dropped, 2**31 - 1
        )
    session.last_seen_at_ms = max(session.last_seen_at_ms, received_ms)
    _advance_observation(session, fresh)

    prompt = sampling.consider_offer(chain, session, received_ms, tz_name)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return IngestResult(RETRY, session_ref, 0, 0, 0, 0, None)
    return IngestResult(
        RECORDED, ref, len(fresh), duplicates, invalid, late,
        prompt.public_id if prompt is not None else None,
    )


# ---------------------------------------------------------------------------
# Server outcomes
# ---------------------------------------------------------------------------

Outcome = namedtuple("Outcome", "event_type detail count position page_id")


def record_outcomes(user_id, session_ref, outcomes, deployment_provenance):
    """Store server-confirmed outcomes after the LMS request finished.

    Returns the session reference to keep (or the one passed in). **Never
    raises**: a failure is rolled back, logged without any Student value,
    and swallowed, so research can never turn a completed LMS operation into
    an error.
    """
    try:
        return _record_outcomes(user_id, session_ref, outcomes, deployment_provenance)
    except Exception:
        try:
            db.session.rollback()
        except Exception:  # pragma: no cover - rollback of an unusable session
            pass
        current_app.logger.exception(
            "Research outcome recording failed; the LMS operation itself is unaffected"
        )
        return session_ref


def _record_outcomes(user_id, session_ref, outcomes, deployment_provenance):
    moment = now_ms()
    chain, refused = lock_collection_chain(user_id, moment)
    if refused:
        return session_ref
    kept = list(outcomes)
    session, ref = resolve_session(chain, session_ref, moment, deployment_provenance)
    for outcome in kept:
        db.session.add(
            ResearchEvent(
                event_uid=str(uuid.uuid4()),
                session_id=session.id,
                source=SOURCE_SERVER,
                event_type=outcome.event_type,
                page_id=outcome.page_id,
                detail_code=outcome.detail,
                count_value=outcome.count,
                position_value=outcome.position,
                occurred_at_ms=moment,
                received_at_ms=moment,
            )
        )
        if activity_end(outcome.event_type, outcome.detail):
            session.activity_end_pending_at_ms = moment
    session.events_accepted += len(kept)
    session.last_seen_at_ms = max(session.last_seen_at_ms, moment)
    db.session.commit()
    return ref


# ---------------------------------------------------------------------------
# Ending sessions
# ---------------------------------------------------------------------------


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
