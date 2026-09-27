"""The authoritative write path for the Phase 6 / M02A experiment protocol
catalogue.

Flask-independent -- no ``request``, ``abort``, ``flash``, ``redirect``,
template or logger, exactly like ``app/services/research_transactions.py``.
Each function takes the locks, re-proves every rule against the **locked**
rows, writes, and commits or rolls back before returning, so no lock outlives
a call. Callers pass **public** identifiers; internal ids are resolved here
by non-locking reads that decide only *which* rows to lock.

**The project's research lock order, extended rather than replaced**::

    lock_academic_hierarchy() reset point
    -> users rows (ascending internal id)
    -> research_consent_documents (ascending id)        [M01; never locked here]
    -> research_participants                            [M01; never locked here]
    -> experiment_definitions (ascending id)            [M02A]
    -> experiment_task_sets (ascending id)              [M02A]
    -> experiment_tasks (ascending id)                  [M02A]

An M02A write takes a prefix-respecting subsequence: the reset point, the
acting Researcher's ``users`` row, then protocol rows. It never locks a
consent document or a participant, and no M01 write ever locks a protocol
row, so neither module adds a reverse edge to the other's graph.

**The definition row is the aggregate lock.** Every header, set and task
change locks its definition first, re-proves the definition is still a
``draft``, compares the form's aggregate ``version`` against the locked row,
and moves that version on success. Once the definition is locked, no other
writer can change its sets or tasks, so the child rows read afterwards are
stable; the ones a write actually updates are locked as well, ascending.

**Stale-state checks run twice**: the route checks its signed token against
freshly read state before the locks (cheap rejection), and passes a
``stale_check`` callable that this module calls **again** with the locked
values before it writes anything.

**A foreign key to ``users`` proves existence, never a role.** Every function
re-reads the acting account from its locked row and requires an active
Researcher. Nothing here touches a participant, a Student, a consent
document or any LMS content, and no function deletes a row.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no REPEATABLE
READ snapshot, so this runs correctly in tests without locking anything.
Tests assert the *requested* lock set and order (structural); they prove
nothing about real InnoDB blocking.
"""

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    MAX_TASK_SETS_PER_PROTOCOL,
    MAX_TASKS_PER_SET,
    PROTOCOL_CURRENT_MARKER,
    ExperimentDefinition,
    ExperimentDefinitionStatus,
    ExperimentStudyStage,
    ExperimentTask,
    ExperimentTaskSet,
    User,
    UserRole,
    UserStatus,
)
from app.models.submission_feedback import whole_second_utc
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.experiment_protocol_review import (
    TASK_DIGEST_FIELDS,
    content_digest,
    structural_problems,
)
from app.services.research_queries import public_id_is_well_formed

_RESEARCHER = UserRole.RESEARCHER.value
_USER_ACTIVE = UserStatus.ACTIVE.value

_DRAFT = ExperimentDefinitionStatus.DRAFT.value
_ACTIVE = ExperimentDefinitionStatus.ACTIVE.value
_SUPERSEDED = ExperimentDefinitionStatus.SUPERSEDED.value
_DISCARDED = ExperimentDefinitionStatus.DISCARDED.value

UP = "up"
DOWN = "down"

# Outcome constants. Every caller must handle every one it can receive.
CREATED = "created"
SAVED = "saved"
UNCHANGED = "unchanged"
MOVED = "moved"
EDGE = "edge"
ACTIVATED = "activated"
DISCARDED = "discarded"
ALREADY = "already"
STALE = "stale"
CONFLICT = "conflict"
NOT_FOUND = "not_found"
UNAUTHORIZED = "unauthorized"
FROZEN = "frozen"
VERSION_TAKEN = "version_taken"
CODE_TAKEN = "code_taken"
LIMIT = "limit"
INCOMPLETE = "incomplete"
NOT_DERIVABLE = "not_derivable"

_TASK_FIELDS = TASK_DIGEST_FIELDS


class ProtocolLocks:
    """The rows one M02A lock chain returned.

    Any value may be ``None`` -- a vanished row, a suspended actor -- and
    every such case is a rejection. ``__slots__``-ed, so a typo in a caller
    raises instead of silently reading ``None``.
    """

    __slots__ = ("hierarchy", "actor", "definitions", "sets", "tasks")

    def __init__(self, hierarchy, actor, definitions, sets, tasks):
        self.hierarchy = hierarchy
        self.actor = actor
        self.definitions = definitions
        self.sets = sets
        self.tasks = tasks


def _ascending(ids):
    return sorted({value for value in ids if value is not None})


def lock_protocol_chain(actor_id, definition_ids=(), set_ids=(), task_ids=()):
    """Take the M02A lock order in one open transaction, stopping wherever
    the caller's arguments stop. Returns a :class:`ProtocolLocks`."""
    hierarchy = lock_academic_hierarchy()
    actor = User.query.filter_by(id=actor_id).with_for_update().first()
    definitions = {
        definition_id: ExperimentDefinition.query.filter_by(id=definition_id)
        .with_for_update()
        .first()
        for definition_id in _ascending(definition_ids)
    }
    sets = {
        set_id: ExperimentTaskSet.query.filter_by(id=set_id).with_for_update().first()
        for set_id in _ascending(set_ids)
    }
    tasks = {
        task_id: ExperimentTask.query.filter_by(id=task_id).with_for_update().first()
        for task_id in _ascending(task_ids)
    }
    return ProtocolLocks(hierarchy, actor, definitions, sets, tasks)


def researcher_authz_broken(locks):
    """``True`` when the **locked** acting account is no longer an active
    Researcher."""
    actor = locks.actor
    return actor is None or actor.role != _RESEARCHER or actor.status != _USER_ACTIVE


# ---------------------------------------------------------------------------
# Non-locking id resolution -- decides only which rows to lock
# ---------------------------------------------------------------------------


def _definition_id(public_id):
    if not public_id_is_well_formed(public_id):
        return None
    row = (
        db.session.query(ExperimentDefinition.id)
        .filter(ExperimentDefinition.public_id == public_id)
        .first()
    )
    return None if row is None else row[0]


def _set_id(definition_id, set_public_id):
    if definition_id is None or not public_id_is_well_formed(set_public_id):
        return None
    row = (
        db.session.query(ExperimentTaskSet.id)
        .filter(ExperimentTaskSet.public_id == set_public_id,
                ExperimentTaskSet.definition_id == definition_id)
        .first()
    )
    return None if row is None else row[0]


def _task_id(set_id, task_public_id):
    if set_id is None or not public_id_is_well_formed(task_public_id):
        return None
    row = (
        db.session.query(ExperimentTask.id)
        .filter(ExperimentTask.public_id == task_public_id,
                ExperimentTask.task_set_id == set_id)
        .first()
    )
    return None if row is None else row[0]


def _set_ids(definition_id):
    return [
        row[0]
        for row in db.session.query(ExperimentTaskSet.id)
        .filter(ExperimentTaskSet.definition_id == definition_id)
        .all()
    ]


def _task_ids(set_id):
    return [
        row[0]
        for row in db.session.query(ExperimentTask.id)
        .filter(ExperimentTask.task_set_id == set_id)
        .all()
    ]


def current_definition_id(study_stage):
    """The internal id of the active version for `study_stage`, or
    ``None``. Read twice by activation: once to decide what to lock, and
    once **after** the locks to prove the answer did not move."""
    row = (
        db.session.query(ExperimentDefinition.id)
        .filter(ExperimentDefinition.study_stage == study_stage,
                ExperimentDefinition.status == _ACTIVE)
        .first()
    )
    return None if row is None else row[0]


def _version_taken(version_identifier, excluding_id=None):
    query = db.session.query(ExperimentDefinition.id).filter(
        ExperimentDefinition.version_identifier == version_identifier
    )
    if excluding_id is not None:
        query = query.filter(ExperimentDefinition.id != excluding_id)
    return query.first() is not None


def _code_taken(definition_id, set_code, excluding_id=None):
    query = db.session.query(ExperimentTaskSet.id).filter(
        ExperimentTaskSet.definition_id == definition_id,
        ExperimentTaskSet.set_code == set_code,
    )
    if excluding_id is not None:
        query = query.filter(ExperimentTaskSet.id != excluding_id)
    return query.first() is not None


# ---------------------------------------------------------------------------
# Ordered content, read under the definition lock
# ---------------------------------------------------------------------------


def _ordered_sets(definition_id):
    return (
        ExperimentTaskSet.query.filter_by(definition_id=definition_id)
        .order_by(ExperimentTaskSet.display_order, ExperimentTaskSet.id)
        .all()
    )


def _ordered_tasks(set_id):
    return (
        ExperimentTask.query.filter_by(task_set_id=set_id)
        .order_by(ExperimentTask.display_order, ExperimentTask.id)
        .all()
    )


def _content(definition_id):
    """``[(task_set, [tasks])]`` in frozen order. Two queries."""
    sets = _ordered_sets(definition_id)
    if not sets:
        return []
    tasks = (
        ExperimentTask.query.filter(ExperimentTask.task_set_id.in_([s.id for s in sets]))
        .order_by(ExperimentTask.display_order, ExperimentTask.id)
        .all()
    )
    by_set = {task_set.id: [] for task_set in sets}
    for task in tasks:
        by_set[task.task_set_id].append(task)
    return [(task_set, by_set[task_set.id]) for task_set in sets]


def _next_order(rows):
    return max((row.display_order for row in rows), default=-1) + 1


def _commit(outcome):
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT
    return outcome


def _bump(definition, moment):
    """Move the aggregate version: every successful draft change does."""
    definition.version = definition.version + 1
    definition.updated_at = moment


def _draft_refusal(locks, definition_id, stale_check):
    """The shared checks of every draft change, against the locked
    definition. ``None`` when the change may proceed."""
    if researcher_authz_broken(locks):
        return UNAUTHORIZED
    definition = locks.definitions.get(definition_id)
    if definition is None:
        return NOT_FOUND
    if definition.status != _DRAFT:
        return FROZEN
    if stale_check(definition.version):
        return STALE
    return None


def _refuse(outcome):
    db.session.rollback()
    return outcome


# ---------------------------------------------------------------------------
# The draft header
# ---------------------------------------------------------------------------


def create_protocol(actor_id, version_identifier, title, equivalence_rationale):
    """Create one ``draft`` protocol version. Returns ``(outcome,
    public_id_or_None)``. No token is needed: a repeated submission is
    refused by the unique version identifier."""
    locks = lock_protocol_chain(actor_id)
    if researcher_authz_broken(locks):
        return _refuse(UNAUTHORIZED), None
    if _version_taken(version_identifier):
        return _refuse(VERSION_TAKEN), None
    moment = whole_second_utc()
    definition = ExperimentDefinition(
        version_identifier=version_identifier,
        title=title,
        study_stage=ExperimentStudyStage.VERSION_A_COLLECTION.value,
        equivalence_rationale=equivalence_rationale,
        status=_DRAFT,
        current_marker=None,
        content_digest=None,
        version=1,
        created_by_id=actor_id,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(definition)
    outcome = _commit(CREATED)
    return outcome, definition.public_id if outcome == CREATED else None


def update_protocol(actor_id, protocol_public_id, version_identifier, title,
                    equivalence_rationale, stale_check):
    """Change a draft's version identifier, title and equivalence
    rationale. `stale_check(locked_version)` must return ``True`` when the
    submitted form no longer describes the locked draft."""
    definition_id = _definition_id(protocol_public_id)
    if definition_id is None:
        return NOT_FOUND
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id,))
    refusal = _draft_refusal(locks, definition_id, stale_check)
    if refusal:
        return _refuse(refusal)
    definition = locks.definitions[definition_id]
    if _version_taken(version_identifier, excluding_id=definition.id):
        return _refuse(VERSION_TAKEN)
    values = {"version_identifier": version_identifier, "title": title,
              "equivalence_rationale": equivalence_rationale}
    if all(getattr(definition, key) == value for key, value in values.items()):
        return _refuse(UNCHANGED)
    for key, value in values.items():
        setattr(definition, key, value)
    _bump(definition, whole_second_utc())
    return _commit(SAVED)


# ---------------------------------------------------------------------------
# Task sets
# ---------------------------------------------------------------------------


def add_task_set(actor_id, protocol_public_id, set_code, title, stale_check):
    """Append one task set to a draft. Returns ``(outcome,
    set_public_id_or_None)``."""
    definition_id = _definition_id(protocol_public_id)
    if definition_id is None:
        return NOT_FOUND, None
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id,))
    refusal = _draft_refusal(locks, definition_id, stale_check)
    if refusal:
        return _refuse(refusal), None
    definition = locks.definitions[definition_id]
    existing = _ordered_sets(definition.id)
    if len(existing) >= MAX_TASK_SETS_PER_PROTOCOL:
        return _refuse(LIMIT), None
    if _code_taken(definition.id, set_code):
        return _refuse(CODE_TAKEN), None
    moment = whole_second_utc()
    task_set = ExperimentTaskSet(
        definition_id=definition.id,
        set_code=set_code,
        title=title,
        display_order=_next_order(existing),
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(task_set)
    _bump(definition, moment)
    outcome = _commit(CREATED)
    return outcome, task_set.public_id if outcome == CREATED else None


def update_task_set(actor_id, protocol_public_id, set_public_id, set_code, title,
                    stale_check):
    """Change one draft task set's code and title."""
    definition_id = _definition_id(protocol_public_id)
    set_id = _set_id(definition_id, set_public_id)
    if set_id is None:
        return NOT_FOUND
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id,), set_ids=(set_id,))
    refusal = _draft_refusal(locks, definition_id, stale_check)
    if refusal:
        return _refuse(refusal)
    definition = locks.definitions[definition_id]
    task_set = locks.sets.get(set_id)
    if task_set is None or task_set.definition_id != definition.id:
        return _refuse(NOT_FOUND)
    if _code_taken(definition.id, set_code, excluding_id=task_set.id):
        return _refuse(CODE_TAKEN)
    if task_set.set_code == set_code and task_set.title == title:
        return _refuse(UNCHANGED)
    moment = whole_second_utc()
    task_set.set_code = set_code
    task_set.title = title
    task_set.updated_at = moment
    _bump(definition, moment)
    return _commit(SAVED)


def _reorder(rows, target_id, direction, moment):
    """Move `target_id` one place in `direction` and normalise every row's
    ``display_order`` to ``0..n-1``. Returns :data:`EDGE` when it cannot
    move further, ``None`` otherwise."""
    ids = [row.id for row in rows]
    position = ids.index(target_id)
    neighbour = position - 1 if direction == UP else position + 1
    if neighbour < 0 or neighbour >= len(rows):
        return EDGE
    rows[position], rows[neighbour] = rows[neighbour], rows[position]
    for index, row in enumerate(rows):
        if row.display_order != index:
            row.display_order = index
            row.updated_at = moment
    return None


def move_task_set(actor_id, protocol_public_id, set_public_id, direction, stale_check):
    """Move one draft task set up or down."""
    definition_id = _definition_id(protocol_public_id)
    set_id = _set_id(definition_id, set_public_id)
    if set_id is None or direction not in (UP, DOWN):
        return NOT_FOUND
    previewed = _set_ids(definition_id)
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id,), set_ids=previewed)
    refusal = _draft_refusal(locks, definition_id, stale_check)
    if refusal:
        return _refuse(refusal)
    rows = _ordered_sets(definition_id)
    # Every locked row is still exactly this definition's set list; the
    # version check above already proves nobody changed it, and this makes
    # the rows being renumbered provably the rows that were locked.
    if sorted(row.id for row in rows) != sorted(previewed) or set_id not in previewed:
        return _refuse(STALE)
    moment = whole_second_utc()
    edge = _reorder(rows, set_id, direction, moment)
    if edge:
        return _refuse(edge)
    _bump(locks.definitions[definition_id], moment)
    return _commit(MOVED)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _task_values_changed(task, values):
    return any(getattr(task, key) != value for key, value in values.items())


def add_task(actor_id, protocol_public_id, set_public_id, values, stale_check):
    """Append one task to a draft task set. `values` maps every field in
    :data:`~app.services.experiment_protocol_review.TASK_DIGEST_FIELDS`.
    Returns ``(outcome, task_public_id_or_None)``."""
    definition_id = _definition_id(protocol_public_id)
    set_id = _set_id(definition_id, set_public_id)
    if set_id is None:
        return NOT_FOUND, None
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id,), set_ids=(set_id,))
    refusal = _draft_refusal(locks, definition_id, stale_check)
    if refusal:
        return _refuse(refusal), None
    task_set = locks.sets.get(set_id)
    if task_set is None or task_set.definition_id != definition_id:
        return _refuse(NOT_FOUND), None
    existing = _ordered_tasks(task_set.id)
    if len(existing) >= MAX_TASKS_PER_SET:
        return _refuse(LIMIT), None
    moment = whole_second_utc()
    task = ExperimentTask(
        task_set_id=task_set.id,
        display_order=_next_order(existing),
        created_at=moment,
        updated_at=moment,
        **{key: values[key] for key in _TASK_FIELDS},
    )
    db.session.add(task)
    _bump(locks.definitions[definition_id], moment)
    outcome = _commit(CREATED)
    return outcome, task.public_id if outcome == CREATED else None


def update_task(actor_id, protocol_public_id, set_public_id, task_public_id, values,
                stale_check):
    """Change one draft task's fields."""
    definition_id = _definition_id(protocol_public_id)
    set_id = _set_id(definition_id, set_public_id)
    task_id = _task_id(set_id, task_public_id)
    if task_id is None:
        return NOT_FOUND
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id,),
                                set_ids=(set_id,), task_ids=(task_id,))
    refusal = _draft_refusal(locks, definition_id, stale_check)
    if refusal:
        return _refuse(refusal)
    task_set = locks.sets.get(set_id)
    task = locks.tasks.get(task_id)
    if (task_set is None or task is None or task_set.definition_id != definition_id
            or task.task_set_id != task_set.id):
        return _refuse(NOT_FOUND)
    values = {key: values[key] for key in _TASK_FIELDS}
    if not _task_values_changed(task, values):
        return _refuse(UNCHANGED)
    moment = whole_second_utc()
    for key, value in values.items():
        setattr(task, key, value)
    task.updated_at = moment
    _bump(locks.definitions[definition_id], moment)
    return _commit(SAVED)


def move_task(actor_id, protocol_public_id, set_public_id, task_public_id, direction,
              stale_check):
    """Move one draft task up or down inside its set."""
    definition_id = _definition_id(protocol_public_id)
    set_id = _set_id(definition_id, set_public_id)
    task_id = _task_id(set_id, task_public_id)
    if task_id is None or direction not in (UP, DOWN):
        return NOT_FOUND
    previewed = _task_ids(set_id)
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id,),
                                set_ids=(set_id,), task_ids=previewed)
    refusal = _draft_refusal(locks, definition_id, stale_check)
    if refusal:
        return _refuse(refusal)
    task_set = locks.sets.get(set_id)
    if task_set is None or task_set.definition_id != definition_id:
        return _refuse(NOT_FOUND)
    rows = _ordered_tasks(set_id)
    if sorted(row.id for row in rows) != sorted(previewed) or task_id not in previewed:
        return _refuse(STALE)
    moment = whole_second_utc()
    edge = _reorder(rows, task_id, direction, moment)
    if edge:
        return _refuse(edge)
    _bump(locks.definitions[definition_id], moment)
    return _commit(MOVED)


# ---------------------------------------------------------------------------
# Activation, discard and derivation
# ---------------------------------------------------------------------------


def activate_protocol(actor_id, protocol_public_id, stale_check):
    """Internally activate a draft: seal its content digest, freeze it, and
    supersede the version that was active for the same study stage.

    Returns ``(outcome, problems)``; `problems` is non-empty only with
    :data:`INCOMPLETE`. **This is a catalogue action.** It records who
    activated which content and when; it is not an ethics approval and it
    starts no session and no collection.

    `stale_check(locked_version, digest, current_public_id_or_None)` must
    return ``True`` when the review page no longer describes the locked
    draft, the content it showed, or the version it showed as current.
    """
    definition_id = _definition_id(protocol_public_id)
    if definition_id is None:
        return NOT_FOUND, ()
    stage = (
        db.session.query(ExperimentDefinition.study_stage)
        .filter(ExperimentDefinition.id == definition_id)
        .scalar()
    )
    previewed_current = current_definition_id(stage)
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id, previewed_current))
    if researcher_authz_broken(locks):
        return _refuse(UNAUTHORIZED), ()
    draft = locks.definitions.get(definition_id)
    if draft is None:
        return _refuse(NOT_FOUND), ()
    if draft.status == _ACTIVE:
        return _refuse(ALREADY), ()
    if draft.status != _DRAFT:
        return _refuse(FROZEN), ()

    # Re-read which version is current now that every candidate row is
    # locked. A different answer means somebody activated between the
    # preview and the locks, so this request holds the wrong row.
    if current_definition_id(draft.study_stage) != previewed_current:
        return _refuse(STALE), ()
    current = locks.definitions.get(previewed_current) if previewed_current else None
    if previewed_current is not None and (current is None or current.status != _ACTIVE):
        return _refuse(STALE), ()

    content = _content(draft.id)
    digest = content_digest(draft, content)
    if stale_check(draft.version, digest, None if current is None else current.public_id):
        return _refuse(STALE), ()
    problems = structural_problems(draft.equivalence_rationale, content)
    if problems:
        return _refuse(INCOMPLETE), problems

    moment = whole_second_utc()
    if current is not None:
        # Cleared and flushed **before** the new marker is set: within one
        # flush SQLAlchemy orders UPDATEs by primary key, not by assignment,
        # and neither backend defers a unique index to commit.
        current.status = _SUPERSEDED
        current.current_marker = None
        current.superseded_at = moment
        current.superseded_by_id = actor_id
        current.version = current.version + 1
        current.updated_at = moment
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            return CONFLICT, ()

    draft.status = _ACTIVE
    draft.current_marker = PROTOCOL_CURRENT_MARKER
    draft.content_digest = digest
    draft.activated_at = moment
    draft.activated_by_id = actor_id
    _bump(draft, moment)
    return _commit(ACTIVATED), ()


def discard_protocol(actor_id, protocol_public_id, stale_check):
    """Soft-discard a draft. Nothing is deleted; the discarded draft is
    frozen and stays listed under its own status."""
    definition_id = _definition_id(protocol_public_id)
    if definition_id is None:
        return NOT_FOUND
    locks = lock_protocol_chain(actor_id, definition_ids=(definition_id,))
    if researcher_authz_broken(locks):
        return _refuse(UNAUTHORIZED)
    definition = locks.definitions.get(definition_id)
    if definition is None:
        return _refuse(NOT_FOUND)
    if definition.status == _DISCARDED:
        return _refuse(ALREADY)
    if definition.status != _DRAFT:
        return _refuse(FROZEN)
    if stale_check(definition.version):
        return _refuse(STALE)
    moment = whole_second_utc()
    definition.status = _DISCARDED
    definition.discarded_at = moment
    definition.discarded_by_id = actor_id
    _bump(definition, moment)
    return _commit(DISCARDED)


def derive_protocol(actor_id, source_public_id, version_identifier, title, stale_check):
    """Create a new draft that copies a frozen (active or superseded)
    version's header, sets and tasks exactly, recording the lineage.

    Returns ``(outcome, public_id_or_None)``. The source must still match
    its own sealed digest; a copy of content that no longer matches its seal
    would carry the discrepancy forward, so it is refused as a conflict.
    `stale_check(source_digest)` must return ``True`` when the form no
    longer describes the locked source.
    """
    source_id = _definition_id(source_public_id)
    if source_id is None:
        return NOT_FOUND, None
    locks = lock_protocol_chain(actor_id, definition_ids=(source_id,))
    if researcher_authz_broken(locks):
        return _refuse(UNAUTHORIZED), None
    source = locks.definitions.get(source_id)
    if source is None:
        return _refuse(NOT_FOUND), None
    if source.status not in (_ACTIVE, _SUPERSEDED):
        return _refuse(NOT_DERIVABLE), None
    content = _content(source.id)
    if content_digest(source, content) != source.content_digest:
        return _refuse(CONFLICT), None
    if stale_check(source.content_digest):
        return _refuse(STALE), None
    if _version_taken(version_identifier):
        return _refuse(VERSION_TAKEN), None

    moment = whole_second_utc()
    draft = ExperimentDefinition(
        version_identifier=version_identifier,
        title=title,
        study_stage=source.study_stage,
        equivalence_rationale=source.equivalence_rationale,
        status=_DRAFT,
        current_marker=None,
        content_digest=None,
        derived_from_id=source.id,
        version=1,
        created_by_id=actor_id,
        created_at=moment,
        updated_at=moment,
    )
    db.session.add(draft)
    try:
        # Each level is flushed before the next, so every child insert finds
        # its parent already stored -- and its guard finds that parent a
        # draft.
        db.session.flush()
        copies = []
        for order, (task_set, tasks) in enumerate(content):
            copy = ExperimentTaskSet(
                definition_id=draft.id,
                set_code=task_set.set_code,
                title=task_set.title,
                display_order=order,
                created_at=moment,
                updated_at=moment,
            )
            db.session.add(copy)
            copies.append((copy, tasks))
        db.session.flush()
        for copy, tasks in copies:
            for order, task in enumerate(tasks):
                db.session.add(ExperimentTask(
                    task_set_id=copy.id,
                    display_order=order,
                    created_at=moment,
                    updated_at=moment,
                    **{key: getattr(task, key) for key in _TASK_FIELDS},
                ))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT, None
    return CREATED, draft.public_id
