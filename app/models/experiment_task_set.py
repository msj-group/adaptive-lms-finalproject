"""One ordered task set inside one experiment protocol version
(Phase 6 / M02A).

**Sets are intended to be equivalent, not proved equivalent.** A protocol may
hold several sets so that a later, separately approved Part can give
different participants parallel tasks. Whether two sets are really
equivalent is a methodological assertion the Researcher records in the
definition's ``equivalence_rationale``; the software checks only structural
parallelism at activation (the same number of tasks, and the same task type
and completion criterion at each position) and never claims more.

**``set_code`` is the stable identifier.** Upper-case ASCII, unique inside
its definition, and copied unchanged when a new version is derived -- so the
same set can be recognised across versions, while ``public_id`` names this
one row of this one version.

**``display_order`` is server-owned** and never taken from the browser.
Moves normalise the order to ``0..n-1`` under the definition lock; the
frozen order is ``(display_order, id)``.

**A set belongs to exactly one definition, forever.** ``definition_id`` can
never change after insertion, so no flush can move a frozen set into a draft
(or a draft's set into a frozen version). Every insert and update is refused
unless the **stored** parent definition is still a ``draft``; nothing is ever
deleted. There is no ``version`` column: the parent definition's
``version`` is the one optimistic signal for the whole aggregate.

Database invariants (final defense only): ``public_id`` and
``uq_experiment_task_sets_definition_code`` unique;
``ck_experiment_task_sets_code_present``,
``ck_experiment_task_sets_title_present``,
``ck_experiment_task_sets_display_order_non_negative`` and
``ck_experiment_task_sets_timestamps_ordered``. Every mandatory column is
``NOT NULL`` explicitly, because a CHECK over NULL passes.

Indexes: ``ix_experiment_task_sets_definition_order_id`` (``definition_id``,
``display_order``, ``id``) -- one definition's sets in their frozen order,
read by the detail page, the review page, the digest and derivation; it also
serves the ``definition_id`` foreign key. **No MySQL execution plan has been
measured.**

No ORM relationship is declared; no participant, Student, session or
behaviour is referenced.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import event

from app.extensions import db
from app.models.enums import ExperimentDefinitionStatus
from app.models.experiment_definition import (
    ExperimentDefinition,
    ExperimentProtocolError,
    changed_columns,
    stored_definition_status,
)
from app.models.submission_feedback import whole_second_utc

#: The ``set_code`` column's own width.
TASK_SET_CODE_MAX_LENGTH = 16

#: The ``title`` column's own width.
TASK_SET_TITLE_MAX_LENGTH = 150

_DRAFT = ExperimentDefinitionStatus.DRAFT.value

#: Columns no flush may ever change.
TASK_SET_FIXED_COLUMNS = frozenset({"public_id", "definition_id", "created_at"})


class ExperimentTaskSet(db.Model):
    __tablename__ = "experiment_task_sets"
    __table_args__ = (
        db.UniqueConstraint(
            "definition_id", "set_code", name="uq_experiment_task_sets_definition_code"
        ),
        db.CheckConstraint("LENGTH(set_code) > 0", name="ck_experiment_task_sets_code_present"),
        db.CheckConstraint("LENGTH(title) > 0", name="ck_experiment_task_sets_title_present"),
        db.CheckConstraint(
            "display_order >= 0", name="ck_experiment_task_sets_display_order_non_negative"
        ),
        db.CheckConstraint(
            "updated_at >= created_at", name="ck_experiment_task_sets_timestamps_ordered"
        ),
        db.Index(
            "ix_experiment_task_sets_definition_order_id",
            "definition_id",
            "display_order",
            "id",
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    definition_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("experiment_definitions.id"),
        nullable=False,
    )
    set_code = db.Column(db.String(TASK_SET_CODE_MAX_LENGTH), nullable=False)
    title = db.Column(db.String(TASK_SET_TITLE_MAX_LENGTH), nullable=False)
    display_order = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


def stored_set_definition_status(connection, task_set_id):
    """The stored status of the definition that **stored** set `task_set_id`
    belongs to, or ``None``."""
    if task_set_id is None:
        return None
    sets = ExperimentTaskSet.__table__
    definitions = ExperimentDefinition.__table__
    return connection.execute(
        sa.select(definitions.c.status)
        .select_from(sets.join(definitions, definitions.c.id == sets.c.definition_id))
        .where(sets.c.id == task_set_id)
    ).scalar()


@event.listens_for(ExperimentTaskSet, "before_insert")
def _only_a_draft_gains_a_set(_mapper, connection, target):
    if stored_definition_status(connection, target.definition_id) != _DRAFT:
        raise ExperimentProtocolError(
            "A task set can only be added to a draft experiment protocol."
        )


@event.listens_for(ExperimentTaskSet, "before_update")
def _refuse_changing_a_frozen_set(_mapper, connection, target):
    changed = changed_columns(target)
    if not changed:
        return
    fixed = sorted(changed & TASK_SET_FIXED_COLUMNS)
    if fixed:
        raise ExperimentProtocolError(
            "A task set's " + ", ".join(fixed) + " cannot change after it is created."
        )
    if stored_set_definition_status(connection, target.id) != _DRAFT:
        raise ExperimentProtocolError(
            "A task set of a frozen experiment protocol version cannot change."
        )


@event.listens_for(ExperimentTaskSet, "before_delete")
def _refuse_deleting_a_set(_mapper, _connection, _target):
    raise ExperimentProtocolError("An experiment task set is never deleted")
