"""The authoritative write path for collection configurations
(Phase 6 replacement).

Flask-independent. Each function takes the locks, re-proves every rule
against the **locked** rows, writes, audits and commits -- or rolls back and
returns a status constant -- before returning.

**Lock order** (a prefix of the collection order in
``research_collection``)::

    lock_academic_hierarchy() reset point
    -> users row of the acting Researcher   (role and status re-proved)
    -> research_configurations rows, ascending id
    -> research_sessions rows, ascending id (only when sessions are closed)

A foreign key to ``users`` proves existence, never a role: the acting account
is re-read under its lock and must still be an active Researcher.

**Stale forms.** Every edit, activation and pause/resume is bound to the
configuration's ``version`` by a signed token (``research_tokens``); the
expected version is compared again against the locked row, so a form
rendered before any change refuses to write.

An ``IntegrityError`` (for example two drafts racing for one version number,
or two activations racing for the current marker) is rolled back and
reported as :data:`CONFLICT`; no SQL or driver text reaches a page.
"""

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    CONFIGURATION_CURRENT_MARKER,
    ResearchAuditAction,
    ResearchAuditChannel,
    ResearchAuditEvent,
    ResearchConfiguration,
    ResearchConfigurationStatus,
    ResearchSessionEndReason,
    User,
    UserRole,
    UserStatus,
    now_ms,
)
from app.models.submission_feedback import whole_second_utc
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.research_collection import close_open_sessions
from app.services.research_event_dictionary import EVENT_SCHEMA_VERSION

CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
ACTIVATED = "activated"
COLLECTING = "collecting"
PAUSED = "paused"
ALREADY = "already"
STALE = "stale"
CONFLICT = "conflict"
NOT_FOUND = "not_found"
UNAUTHORIZED = "unauthorized"
NOT_DRAFT = "not_draft"
NOT_ACTIVE = "not_active"
NO_RETENTION = "no_retention"
PERIOD_ENDED = "period_ended"

_RESEARCHER = UserRole.RESEARCHER.value
_ACTIVE_USER = UserStatus.ACTIVE.value
_DRAFT = ResearchConfigurationStatus.DRAFT.value
_ACTIVE = ResearchConfigurationStatus.ACTIVE.value
_RETIRED = ResearchConfigurationStatus.RETIRED.value

#: The columns a draft form may set. Everything else is server-owned.
EDITABLE_COLUMNS = (
    "label", "collection_starts_at", "collection_ends_at",
    "session_inactivity_minutes", "max_prompts_per_session", "max_prompts_per_day",
    "lookback_seconds", "min_observed_seconds", "random_prompt_permille",
    "activity_end_prompt_permille", "offer_ttl_seconds", "response_window_seconds",
)


def _lock_researcher(actor_id):
    lock_academic_hierarchy()
    actor = db.session.query(User).filter(User.id == actor_id).with_for_update().first()
    if actor is None or actor.role != _RESEARCHER or actor.status != _ACTIVE_USER:
        db.session.rollback()
        return None
    from app.services.actor_authorization import require_current_actor
    actor = require_current_actor(actor_id, "researcher")
    from app.services.research_control_gate import lock_research_control
    lock_research_control()
    return actor


def _lock_configurations(ids):
    rows = (
        db.session.query(ResearchConfiguration)
        .filter(ResearchConfiguration.id.in_(sorted(set(ids))))
        .order_by(ResearchConfiguration.id.asc())
        .with_for_update()
        .all()
    )
    return {row.id: row for row in rows}


def _configuration_id(public_id):
    return (
        db.session.query(ResearchConfiguration.id)
        .filter(ResearchConfiguration.public_id == public_id)
        .scalar()
    )


def _audit(action, actor_id, configuration_id, detail=None, count=None):
    db.session.add(
        ResearchAuditEvent(
            action=action,
            channel=ResearchAuditChannel.WORKSPACE.value,
            actor_id=actor_id,
            configuration_id=configuration_id,
            detail_code=detail,
            count_value=count,
        )
    )


def _commit(success):
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT
    return success


def create_draft(actor_id, values):
    """``(status, public_id)``. The version number is the next free one."""
    actor = _lock_researcher(actor_id)
    if actor is None:
        return UNAUTHORIZED, None
    from app.services.research_control_gate import lock_research_control
    sequence = lock_research_control()
    number = sequence.last_number
    if number >= 2147483647:
        raise ValueError("The configuration version number capacity has been reached.")
    sequence.last_number += 1
    now = whole_second_utc()
    configuration = ResearchConfiguration(
        version_number=number + 1,
        event_schema_version=EVENT_SCHEMA_VERSION,
        status=_DRAFT,
        is_collecting=False,
        created_by_id=actor.id,
        created_at=now,
        updated_at=now,
        **{column: values[column] for column in EDITABLE_COLUMNS},
    )
    db.session.add(configuration)
    db.session.flush()
    _audit(ResearchAuditAction.CONFIGURATION_CREATED.value, actor.id, configuration.id)
    public_id = configuration.public_id
    result = _commit(CREATED)
    return (result, public_id if result == CREATED else None)


def update_draft(actor_id, public_id, expected_version, values):
    actor = _lock_researcher(actor_id)
    if actor is None:
        return UNAUTHORIZED
    configuration_id = _configuration_id(public_id)
    if configuration_id is None:
        db.session.rollback()
        return NOT_FOUND
    configuration = _lock_configurations([configuration_id]).get(configuration_id)
    if configuration.status != _DRAFT:
        db.session.rollback()
        return NOT_DRAFT
    if configuration.version != expected_version:
        db.session.rollback()
        return STALE
    changed = [c for c in EDITABLE_COLUMNS if getattr(configuration, c) != values[c]]
    if not changed:
        db.session.rollback()
        return UNCHANGED
    for column in changed:
        setattr(configuration, column, values[column])
    configuration.version += 1
    configuration.updated_at = max(whole_second_utc(), configuration.created_at)
    _audit(ResearchAuditAction.CONFIGURATION_UPDATED.value, actor.id, configuration.id)
    return _commit(UPDATED)


def activate(actor_id, public_id, expected_version, retention_days):
    """Freeze a draft, make it the one active version (not yet collecting),
    and retire -- closing the sessions of -- whatever was active."""
    actor = _lock_researcher(actor_id)
    if actor is None:
        return UNAUTHORIZED
    target_id = _configuration_id(public_id)
    if target_id is None:
        db.session.rollback()
        return NOT_FOUND
    current_id = (
        db.session.query(ResearchConfiguration.id)
        .filter(ResearchConfiguration.current_marker == CONFIGURATION_CURRENT_MARKER)
        .scalar()
    )
    locked = _lock_configurations([target_id] + ([current_id] if current_id else []))
    target = locked[target_id]
    if target.status != _DRAFT:
        db.session.rollback()
        return NOT_DRAFT
    if target.version != expected_version:
        db.session.rollback()
        return STALE
    if retention_days is None:
        db.session.rollback()
        return NO_RETENTION
    now = whole_second_utc()
    if target.collection_ends_at <= now:
        db.session.rollback()
        return PERIOD_ENDED
    # Re-read which version is current after the locks: a concurrent
    # activation may have moved it.
    current = next(
        (row for row in locked.values() if row.id != target.id and row.status == _ACTIVE), None
    )
    moment = now_ms()
    if current is not None:
        close_open_sessions(
            current, moment, ResearchSessionEndReason.CONFIGURATION_CHANGED.value
        )
        current.status = _RETIRED
        current.current_marker = None
        current.is_collecting = False
        current.retired_at = max(now, current.activated_at)
        current.retired_by_id = actor.id
        current.version += 1
        current.updated_at = max(now, current.updated_at)
        db.session.flush()
    target.status = _ACTIVE
    target.current_marker = CONFIGURATION_CURRENT_MARKER
    target.is_collecting = False
    target.activated_at = max(now, target.created_at)
    target.activated_by_id = actor.id
    target.retention_days = retention_days
    target.version += 1
    target.updated_at = max(now, target.updated_at)
    _audit(ResearchAuditAction.CONFIGURATION_ACTIVATED.value, actor.id, target.id,
           count=retention_days)
    return _commit(ACTIVATED)


def set_collecting(actor_id, public_id, expected_version, collecting):
    """Resume (``True``) or pause (``False``) the active version. Pausing
    closes its open sessions at once."""
    actor = _lock_researcher(actor_id)
    if actor is None:
        return UNAUTHORIZED
    configuration_id = _configuration_id(public_id)
    if configuration_id is None:
        db.session.rollback()
        return NOT_FOUND
    configuration = _lock_configurations([configuration_id]).get(configuration_id)
    if configuration.status != _ACTIVE:
        db.session.rollback()
        return NOT_ACTIVE
    if configuration.version != expected_version:
        db.session.rollback()
        return STALE
    if bool(configuration.is_collecting) == bool(collecting):
        db.session.rollback()
        return ALREADY
    now = whole_second_utc()
    if collecting and configuration.collection_ends_at <= now:
        db.session.rollback()
        return PERIOD_ENDED
    closed = None
    if not collecting:
        closed = close_open_sessions(
            configuration, now_ms(), ResearchSessionEndReason.COLLECTION_STOPPED.value
        )
    configuration.is_collecting = bool(collecting)
    configuration.version += 1
    configuration.updated_at = max(now, configuration.updated_at)
    action = ResearchAuditAction.COLLECTION_RESUMED if collecting else \
        ResearchAuditAction.COLLECTION_PAUSED
    _audit(action.value, actor.id, configuration.id, count=closed)
    return _commit(COLLECTING if collecting else PAUSED)
