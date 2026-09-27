"""One experiment protocol version (Phase 6 / M02A).

**A catalogue entry, not an experiment.** A definition is a Researcher-written
version of a Version A protocol: a version identifier, a title, the study
stage it belongs to, an optional statement of why its task sets are intended
to be equivalent, and -- in :class:`~app.models.experiment_task_set.
ExperimentTaskSet` and :class:`~app.models.experiment_task.ExperimentTask` --
its ordered task sets and ordered tasks. Nothing here names, references or
stores a participant, a Student, a session, an event, a rating or any
behaviour. What the free-text fields contain is written by a Researcher; the
application refuses obvious participant codes and email addresses in them
(``app/services/experiment_protocol_text.py``) but cannot prove that nobody
ever types personal information into a sentence.

**Internal activation is not ethics approval.** ``active`` means "the one
frozen catalogue version for this study stage inside the application". It is
recorded with who activated it and when, and it starts nothing: M02A has no
assignment, no session and no collection.

**The lifecycle freezes the whole aggregate.** See
:class:`~app.models.enums.ExperimentDefinitionStatus`. While ``draft``, the
header, the sets and the tasks may change, and every successful change moves
``version`` -- the one optimistic-concurrency signal for the entire
definition/set/task aggregate that every signed form is bound to.
Activation computes ``content_digest`` over the complete ordered content
(:func:`protocol_digest`) and freezes it; a correction is a new draft derived
from a frozen version (``derived_from_id``), never an edit.

**At most one version per study stage is active.** ``current_marker`` is
``1`` exactly while ``active`` and NULL otherwise, and
``uq_experiment_definitions_current`` is unique over (``study_stage``,
``current_marker``): MySQL and SQLite both admit many NULLs in a unique
index and exactly one ``1`` per stage. The write path still takes the
documented lock order and re-proves everything after its locks
(``app/services/experiment_protocol_transactions.py``).

**Nothing is ever physically deleted**, there is no cascade and no
``ondelete``; every foreign key is a plain reference. A mistaken draft is
``discarded``, which freezes it too.

Database invariants (final defense only):

- ``public_id``, ``uq_experiment_definitions_version_identifier`` and
  ``uq_experiment_definitions_current`` unique;
- ``ck_experiment_definitions_status_valid`` and
  ``ck_experiment_definitions_stage_valid`` -- the closed sets as literal
  ``IN`` lists, the project's convention;
- ``ck_experiment_definitions_version_positive``;
- ``ck_experiment_definitions_version_identifier_present``,
  ``ck_experiment_definitions_title_present`` and
  ``ck_experiment_definitions_rationale_present`` -- never an empty string;
  an absent rationale is NULL;
- ``ck_experiment_definitions_current_marker`` -- the marker is set exactly
  while ``active``, with an explicit ``IS NOT NULL`` because a CHECK that
  evaluates to NULL passes;
- ``ck_experiment_definitions_activation_pair``, ``_supersession_pair`` and
  ``_discard_pair`` -- a moment and its actor are stored together;
- ``ck_experiment_definitions_lifecycle_state`` -- the truth table of which
  moments and which digest each status carries;
- ``ck_experiment_definitions_digest_format`` -- 64 characters when present;
- ``ck_experiment_definitions_timestamps_ordered``.

**NOT NULL is declared explicitly on every mandatory column.** A CHECK such
as ``LENGTH(title) > 0`` evaluates to NULL for a NULL title, and a CHECK that
evaluates to NULL passes on both backends -- so the CHECKs above reject empty
strings and ``nullable=False`` rejects NULL.

Conditions a CHECK cannot express are stated here rather than hidden: that an
attributed account is an active Researcher, that a frozen version's content
never changes, that ``derived_from_id`` names a frozen version other than the
row itself (MySQL refuses a CHECK that references an ``AUTO_INCREMENT``
column), and that the stored digest matches the stored content. The
application proves each against locked rows, and the guards below refuse a
flush that would change a frozen row.

Indexes -- one per real query path:

- ``ix_experiment_definitions_status_id`` (``status``, ``id``) -- the
  protocol list, an equality on ``status`` ordered by ``id DESC``, and the
  counts on the Researcher dashboard;
- ``ix_experiment_definitions_created_by_id``, ``_activated_by_id``,
  ``_superseded_by_id``, ``_discarded_by_id`` and ``_derived_from_id`` --
  declared for the foreign keys InnoDB requires an index for, rather than
  left implicit.

**No MySQL execution plan has been measured for this table.**

**No ORM relationship is declared in either direction**: every read is an
explicit query in ``app/services/experiment_protocol_queries.py``, so no
template can lazy-load anything, and no ``delete-orphan`` configuration
exists that could remove a row.
"""

import hashlib
import uuid

import sqlalchemy as sa
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session, validates

from app.extensions import db
from app.models.enums import ExperimentDefinitionStatus, ExperimentStudyStage
from app.models.submission_feedback import whole_second_utc

#: The ``version_identifier`` column's own width.
PROTOCOL_VERSION_MAX_LENGTH = 40

#: The ``title`` column's own width.
PROTOCOL_TITLE_MAX_LENGTH = 200

#: The longest equivalence rationale the application accepts, in
#: characters. ``TEXT`` holds 65,535 bytes; 2,000 four-byte characters is
#: 8,000.
EQUIVALENCE_RATIONALE_MAX_LENGTH = 2000

#: A SHA-256 hex digest.
PROTOCOL_DIGEST_LENGTH = 64

#: The value ``current_marker`` carries exactly while the version is
#: ``active``.
PROTOCOL_CURRENT_MARKER = 1

#: The most task sets one protocol version may hold, and the most tasks one
#: set may hold. Enforced under the definition lock, since a CHECK cannot
#: count rows. They also bound every page and every digest.
MAX_TASK_SETS_PER_PROTOCOL = 4
MAX_TASKS_PER_SET = 10

#: The tag every digest input starts with, so a later change to what the
#: digest covers can never collide with this one.
PROTOCOL_DIGEST_FORMAT = "experiment-protocol.v1"

_DRAFT = ExperimentDefinitionStatus.DRAFT.value
_ACTIVE = ExperimentDefinitionStatus.ACTIVE.value


class ExperimentProtocolError(RuntimeError):
    """A write that would change a frozen protocol version, re-parent a task
    set or task, or delete any protocol row.

    Its own class, deliberately separate from ``ResearchHistoryError`` and
    ``FinancialHistoryError``: a protocol catalogue is a different
    obligation from consent history or money, and a handler for one must
    never silently absorb another.
    """


def _digest_field(value):
    """One typed, length-prefixed digest field.

    The type tag keeps ``None``, the empty string and the integer ``0``
    distinct, and the length prefix keeps any value from being re-split into
    its neighbours -- a title ending in what looks like a separator cannot
    borrow the next field's first characters.
    """
    if value is None:
        return b"n|"
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError(f"Unsupported protocol digest value: {value!r}")
    tag = b"i" if isinstance(value, int) else b"s"
    encoded = str(value).encode("utf-8")
    return tag + str(len(encoded)).encode("ascii") + b":" + encoded + b"|"


def protocol_digest(header, task_sets):
    """The stable SHA-256 digest of one complete protocol version.

    `header` is ``(version_identifier, title, study_stage,
    equivalence_rationale)``. `task_sets` is the ordered sequence of
    ``(set_code, title, tasks)``; `tasks` is the ordered sequence of
    ``(task_type, title, participant_instructions, expected_goal,
    difficulty, recommended_duration_seconds, completion_criterion)``.

    Positions enter the digest as **ordinals** (1, 2, 3 ...), never as the
    stored ``display_order``, so gaps in the stored order cannot change the
    digest while the order itself does. Counts enter it too, so no task can
    be read as belonging to a neighbouring set.
    """
    parts = [_digest_field(PROTOCOL_DIGEST_FORMAT)]
    parts.extend(_digest_field(value) for value in header)
    task_sets = list(task_sets)
    parts.append(_digest_field(len(task_sets)))
    for set_ordinal, (set_code, set_title, tasks) in enumerate(task_sets, start=1):
        tasks = list(tasks)
        parts += [
            _digest_field(set_ordinal),
            _digest_field(set_code),
            _digest_field(set_title),
            _digest_field(len(tasks)),
        ]
        for task_ordinal, task in enumerate(tasks, start=1):
            parts.append(_digest_field(task_ordinal))
            parts.extend(_digest_field(value) for value in task)
    return hashlib.sha256(b"".join(parts)).hexdigest()


def _in_list(column, enum):
    return f"{column} IN (" + ", ".join(f"'{member.value}'" for member in enum) + ")"


_STATUS_VALUES = tuple(status.value for status in ExperimentDefinitionStatus)
_STAGE_VALUES = tuple(stage.value for stage in ExperimentStudyStage)
_STATUS_CHECK_SQL = _in_list("status", ExperimentDefinitionStatus)
_STAGE_CHECK_SQL = _in_list("study_stage", ExperimentStudyStage)

#: ``IS NOT NULL`` is not redundant beside ``= 1``: a CHECK that evaluates
#: to NULL passes, and ``NULL = 1`` is NULL, so without it an ``active`` row
#: carrying no marker would be accepted -- and two such rows would satisfy
#: ``uq_experiment_definitions_current`` as well.
_CURRENT_MARKER_SQL = (
    "(status = 'active' AND current_marker IS NOT NULL"
    f" AND current_marker = {PROTOCOL_CURRENT_MARKER})"
    " OR (status <> 'active' AND current_marker IS NULL)"
)

_ACTIVATION_PAIR_SQL = (
    "(activated_at IS NULL AND activated_by_id IS NULL)"
    " OR (activated_at IS NOT NULL AND activated_by_id IS NOT NULL)"
)
_SUPERSESSION_PAIR_SQL = (
    "(superseded_at IS NULL AND superseded_by_id IS NULL)"
    " OR (superseded_at IS NOT NULL AND superseded_by_id IS NOT NULL)"
)
_DISCARD_PAIR_SQL = (
    "(discarded_at IS NULL AND discarded_by_id IS NULL)"
    " OR (discarded_at IS NOT NULL AND discarded_by_id IS NOT NULL)"
)

#: Which moments and which digest each status carries. A draft and a
#: discarded draft were never activated and carry no digest; an active or
#: superseded version was activated and always carries one.
_LIFECYCLE_STATE_SQL = (
    "(status = 'draft' AND activated_at IS NULL AND superseded_at IS NULL"
    " AND discarded_at IS NULL AND content_digest IS NULL)"
    " OR (status = 'active' AND activated_at IS NOT NULL AND superseded_at IS NULL"
    " AND discarded_at IS NULL AND content_digest IS NOT NULL)"
    " OR (status = 'superseded' AND activated_at IS NOT NULL"
    " AND superseded_at IS NOT NULL AND superseded_at >= activated_at"
    " AND discarded_at IS NULL AND content_digest IS NOT NULL)"
    " OR (status = 'discarded' AND activated_at IS NULL AND superseded_at IS NULL"
    " AND discarded_at IS NOT NULL AND content_digest IS NULL)"
)

_DIGEST_FORMAT_SQL = (
    f"content_digest IS NULL OR LENGTH(content_digest) = {PROTOCOL_DIGEST_LENGTH}"
)

_TIMESTAMPS_ORDERED_SQL = (
    "updated_at >= created_at"
    " AND (activated_at IS NULL OR (activated_at >= created_at AND updated_at >= activated_at))"
    " AND (superseded_at IS NULL"
    " OR (superseded_at >= created_at AND updated_at >= superseded_at))"
    " AND (discarded_at IS NULL"
    " OR (discarded_at >= created_at AND updated_at >= discarded_at))"
)

#: Columns no flush may ever change, whatever the status.
DEFINITION_FIXED_COLUMNS = frozenset(
    {"public_id", "study_stage", "derived_from_id", "created_by_id", "created_at"}
)

#: Everything an **active** version may still change: exactly what its
#: supersession moves. Its content, its digest and its activation may not.
DEFINITION_ACTIVE_MUTABLE_COLUMNS = frozenset(
    {"status", "current_marker", "superseded_at", "superseded_by_id", "version",
     "updated_at"}
)


def _fk_to_users():
    return db.ForeignKey("users.id")


class ExperimentDefinition(db.Model):
    __tablename__ = "experiment_definitions"
    __table_args__ = (
        db.UniqueConstraint(
            "version_identifier", name="uq_experiment_definitions_version_identifier"
        ),
        db.UniqueConstraint(
            "study_stage", "current_marker", name="uq_experiment_definitions_current"
        ),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_experiment_definitions_status_valid"),
        db.CheckConstraint(_STAGE_CHECK_SQL, name="ck_experiment_definitions_stage_valid"),
        db.CheckConstraint("version > 0", name="ck_experiment_definitions_version_positive"),
        db.CheckConstraint(
            "LENGTH(version_identifier) > 0",
            name="ck_experiment_definitions_version_identifier_present",
        ),
        db.CheckConstraint(
            "LENGTH(title) > 0", name="ck_experiment_definitions_title_present"
        ),
        db.CheckConstraint(
            "equivalence_rationale IS NULL OR LENGTH(equivalence_rationale) > 0",
            name="ck_experiment_definitions_rationale_present",
        ),
        db.CheckConstraint(
            _CURRENT_MARKER_SQL, name="ck_experiment_definitions_current_marker"
        ),
        db.CheckConstraint(
            _ACTIVATION_PAIR_SQL, name="ck_experiment_definitions_activation_pair"
        ),
        db.CheckConstraint(
            _SUPERSESSION_PAIR_SQL, name="ck_experiment_definitions_supersession_pair"
        ),
        db.CheckConstraint(_DISCARD_PAIR_SQL, name="ck_experiment_definitions_discard_pair"),
        db.CheckConstraint(
            _LIFECYCLE_STATE_SQL, name="ck_experiment_definitions_lifecycle_state"
        ),
        db.CheckConstraint(
            _DIGEST_FORMAT_SQL, name="ck_experiment_definitions_digest_format"
        ),
        db.CheckConstraint(
            _TIMESTAMPS_ORDERED_SQL, name="ck_experiment_definitions_timestamps_ordered"
        ),
        db.Index("ix_experiment_definitions_status_id", "status", "id"),
        db.Index("ix_experiment_definitions_created_by_id", "created_by_id"),
        db.Index("ix_experiment_definitions_activated_by_id", "activated_by_id"),
        db.Index("ix_experiment_definitions_superseded_by_id", "superseded_by_id"),
        db.Index("ix_experiment_definitions_discarded_by_id", "discarded_by_id"),
        db.Index("ix_experiment_definitions_derived_from_id", "derived_from_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    #: Upper-case ASCII, unique across every status, so a new version can
    #: never reuse a retired or discarded version's name.
    version_identifier = db.Column(db.String(PROTOCOL_VERSION_MAX_LENGTH), nullable=False)
    title = db.Column(db.String(PROTOCOL_TITLE_MAX_LENGTH), nullable=False)
    study_stage = db.Column(
        db.String(32),
        nullable=False,
        default=ExperimentStudyStage.VERSION_A_COLLECTION.value,
    )
    #: Why the task sets are *intended* to be equivalent. A methodological
    #: assertion the software does not validate. Required at activation only
    #: when there is more than one set; NULL when absent.
    equivalence_rationale = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default=_DRAFT)
    #: ``1`` exactly while ``active``, ``NULL`` otherwise.
    current_marker = db.Column(db.SmallInteger, nullable=True)
    #: :func:`protocol_digest` of the frozen content; NULL until activation.
    content_digest = db.Column(db.String(PROTOCOL_DIGEST_LENGTH), nullable=True)
    #: The frozen version this draft was copied from, if any.
    derived_from_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("experiment_definitions.id"),
        nullable=True,
    )
    #: The optimistic version of the whole definition/set/task aggregate.
    version = db.Column(db.Integer, nullable=False, default=1)
    created_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"), _fk_to_users(), nullable=False
    )
    activated_at = db.Column(db.DateTime, nullable=True)
    activated_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"), _fk_to_users(), nullable=True
    )
    superseded_at = db.Column(db.DateTime, nullable=True)
    superseded_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"), _fk_to_users(), nullable=True
    )
    discarded_at = db.Column(db.DateTime, nullable=True)
    discarded_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"), _fk_to_users(), nullable=True
    )
    #: Defaults are defense in depth only; every write supplies its own
    #: post-lock whole-second moment.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid experiment definition status: {value}")
        return value

    @validates("study_stage")
    def validate_study_stage(self, _key, value):
        if value not in _STAGE_VALUES:
            raise ValueError(f"Invalid experiment study stage: {value}")
        return value

    @validates("current_marker")
    def validate_current_marker(self, _key, value):
        if value is not None and value != PROTOCOL_CURRENT_MARKER:
            raise ValueError("A protocol's current marker is 1 or NULL")
        return value

    @validates("content_digest")
    def validate_content_digest(self, _key, value):
        if value is not None and (
            not isinstance(value, str)
            or len(value) != PROTOCOL_DIGEST_LENGTH
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise ValueError("A protocol digest is 64 lowercase hex characters")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("A protocol version must be a positive integer")
        return value

    @property
    def is_draft(self):
        return self.status == _DRAFT

    @property
    def is_active(self):
        return self.status == _ACTIVE


def stored_definition_status(connection, definition_id):
    """The status **stored** for `definition_id`, read through the flushing
    connection, or ``None``.

    The guards read the database rather than the object's attribute history:
    a row whose attributes were expired and then assigned carries no "old"
    value in memory, so history alone could be talked into believing a
    frozen version was still a draft. The stored row cannot.
    """
    if definition_id is None:
        return None
    table = ExperimentDefinition.__table__
    return connection.execute(
        sa.select(table.c.status).where(table.c.id == definition_id)
    ).scalar()


def changed_columns(target):
    state = inspect(target)
    return {
        attr.key
        for attr in state.mapper.column_attrs
        if state.attrs[attr.key].history.has_changes()
    }


@event.listens_for(ExperimentDefinition, "before_update")
def _refuse_changing_a_frozen_definition(_mapper, connection, target):
    """A draft may change anything but its identity; an active version may
    change only what its supersession moves; a superseded or discarded
    version may change nothing at all."""
    changed = changed_columns(target)
    if not changed:
        return
    fixed = sorted(changed & DEFINITION_FIXED_COLUMNS)
    if fixed:
        raise ExperimentProtocolError(
            "An experiment protocol's " + ", ".join(fixed) + " cannot change."
        )
    stored = stored_definition_status(connection, target.id)
    if stored == _DRAFT:
        return
    allowed = DEFINITION_ACTIVE_MUTABLE_COLUMNS if stored == _ACTIVE else frozenset()
    refused = sorted(changed - allowed)
    if refused:
        raise ExperimentProtocolError(
            f"The {stored} experiment protocol version's " + ", ".join(refused)
            + " cannot change. Create a new version instead."
        )


@event.listens_for(ExperimentDefinition, "before_delete")
def _refuse_deleting_a_definition(_mapper, _connection, _target):
    raise ExperimentProtocolError("An experiment protocol version is never deleted")


#: No protocol row is ever hard-deleted, in bulk or otherwise, and none is
#: ever rewritten in bulk: every change goes through the guarded ORM path,
#: which re-proves the lifecycle.
PROTOCOL_TABLES = frozenset(
    {"experiment_definitions", "experiment_task_sets", "experiment_tasks"}
)


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_rewrites_of_protocols(orm_execute_state):
    """Mapper events do not see ``session.execute(update(...))`` or
    ``delete(...)``; this refuses those statements for every M02A table, so a
    bulk statement can never edit a frozen version or remove a row."""
    if not (orm_execute_state.is_update or orm_execute_state.is_delete):
        return
    table = getattr(orm_execute_state.statement, "table", None)
    if getattr(table, "name", None) in PROTOCOL_TABLES:
        raise ExperimentProtocolError(f"{table.name} rows cannot be rewritten in bulk")
