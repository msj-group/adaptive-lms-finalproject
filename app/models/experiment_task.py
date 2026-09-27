"""One ordered task inside one task set of one experiment protocol version
(Phase 6 / M02A).

**A definition of a future task, not a record of anybody doing it.** A task
says which existing LMS workflow it concerns (``task_type``), what the
participant would be told (``participant_instructions``), what the
Researcher means by success (``expected_goal``), the intended difficulty and
recommended duration, and the completion criterion the protocol **intends**
a later Part to apply. Nothing here binds the task to a real Lesson, Quiz or
Assignment, reads any Student's data, or verifies anything: see
:class:`~app.models.enums.ExperimentCompletionCriterion`.

**Structured, never executable.** Every rule is an explicit closed set or a
bounded integer -- no JSON, no expression, no script. Which criterion a task
type may use is the database CHECK
``ck_experiment_tasks_type_criterion_pair``, mirrored by
:data:`ALLOWED_CRITERIA_BY_TYPE` for the forms.

**A task belongs to exactly one set, forever.** ``task_set_id`` can never
change after insertion, so no flush can detach a frozen task into a draft.
Every insert and update is refused unless the **stored** definition above the
task's stored set is still a ``draft``; nothing is ever deleted.

Database invariants (final defense only): ``public_id`` unique; the closed
sets ``ck_experiment_tasks_type_valid``, ``_difficulty_valid`` and
``_criterion_valid``; ``ck_experiment_tasks_type_criterion_pair``;
``ck_experiment_tasks_duration_range`` (30 to 1,800 seconds);
``ck_experiment_tasks_title_present``, ``_instructions_present`` and
``_goal_present``; ``ck_experiment_tasks_display_order_non_negative``;
``ck_experiment_tasks_timestamps_ordered``. Every mandatory column is
``NOT NULL`` explicitly, because a CHECK over NULL passes.

Indexes: ``ix_experiment_tasks_set_order_id`` (``task_set_id``,
``display_order``, ``id``) -- one set's tasks in their frozen order, and the
``task_set_id`` foreign key. **No MySQL execution plan has been measured.**

There is deliberately **no** ``definition_id`` here: the definition is
reached through the set, and a second copy could disagree with it.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import (
    ExperimentCompletionCriterion,
    ExperimentDefinitionStatus,
    ExperimentTaskDifficulty,
    ExperimentTaskType,
)
from app.models.experiment_definition import (
    ExperimentDefinition,
    ExperimentProtocolError,
    changed_columns,
)
from app.models.experiment_task_set import (
    ExperimentTaskSet,
    stored_set_definition_status,
)
from app.models.submission_feedback import whole_second_utc

#: The ``title`` column's own width.
TASK_TITLE_MAX_LENGTH = 150

#: The longest participant instructions and expected goal the application
#: accepts, in characters. Both columns are ``TEXT``.
TASK_INSTRUCTIONS_MAX_LENGTH = 2000
TASK_GOAL_MAX_LENGTH = 1000

#: The recommended duration's bounds, in seconds. Guidance for a Researcher,
#: not a time limit: M02A enforces no timeout because it runs no task.
MIN_RECOMMENDED_DURATION_SECONDS = 30
MAX_RECOMMENDED_DURATION_SECONDS = 1800

_DRAFT = ExperimentDefinitionStatus.DRAFT.value

_TYPE = ExperimentTaskType
_CRITERION = ExperimentCompletionCriterion

#: The completion criteria each task type may intend. Objective criteria are
#: offered only where an existing workflow has a stored end state a later
#: Part could check; navigation and search have none of their own.
ALLOWED_CRITERIA_BY_TYPE = {
    _TYPE.DASHBOARD_NAVIGATION.value: (_CRITERION.PARTICIPANT_DECLARED.value,),
    _TYPE.FIND_LESSON.value: (
        _CRITERION.LESSON_OPENED.value,
        _CRITERION.PARTICIPANT_DECLARED.value,
    ),
    _TYPE.SEARCH.value: (
        _CRITERION.LESSON_OPENED.value,
        _CRITERION.PARTICIPANT_DECLARED.value,
    ),
    _TYPE.QUIZ_COMPLETION.value: (_CRITERION.QUIZ_ATTEMPT_SUBMITTED.value,),
    _TYPE.ASSIGNMENT_SUBMISSION.value: (_CRITERION.ASSIGNMENT_SUBMITTED.value,),
}


def _in_list(column, values):
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


_TYPE_VALUES = tuple(member.value for member in ExperimentTaskType)
_DIFFICULTY_VALUES = tuple(member.value for member in ExperimentTaskDifficulty)
_CRITERION_VALUES = tuple(member.value for member in ExperimentCompletionCriterion)

#: One disjunct per task type, generated from :data:`ALLOWED_CRITERIA_BY_TYPE`
#: so the database and the forms cannot disagree.
_TYPE_CRITERION_PAIR_SQL = " OR ".join(
    f"(task_type = '{task_type}' AND {_in_list('completion_criterion', criteria)})"
    for task_type, criteria in ALLOWED_CRITERIA_BY_TYPE.items()
)

_DURATION_RANGE_SQL = (
    f"recommended_duration_seconds >= {MIN_RECOMMENDED_DURATION_SECONDS}"
    f" AND recommended_duration_seconds <= {MAX_RECOMMENDED_DURATION_SECONDS}"
)

#: Columns no flush may ever change.
TASK_FIXED_COLUMNS = frozenset({"public_id", "task_set_id", "created_at"})


class ExperimentTask(db.Model):
    __tablename__ = "experiment_tasks"
    __table_args__ = (
        db.CheckConstraint(_in_list("task_type", _TYPE_VALUES),
                           name="ck_experiment_tasks_type_valid"),
        db.CheckConstraint(_in_list("difficulty", _DIFFICULTY_VALUES),
                           name="ck_experiment_tasks_difficulty_valid"),
        db.CheckConstraint(_in_list("completion_criterion", _CRITERION_VALUES),
                           name="ck_experiment_tasks_criterion_valid"),
        db.CheckConstraint(_TYPE_CRITERION_PAIR_SQL,
                           name="ck_experiment_tasks_type_criterion_pair"),
        db.CheckConstraint(_DURATION_RANGE_SQL, name="ck_experiment_tasks_duration_range"),
        db.CheckConstraint("LENGTH(title) > 0", name="ck_experiment_tasks_title_present"),
        db.CheckConstraint(
            "LENGTH(participant_instructions) > 0",
            name="ck_experiment_tasks_instructions_present",
        ),
        db.CheckConstraint(
            "LENGTH(expected_goal) > 0", name="ck_experiment_tasks_goal_present"
        ),
        db.CheckConstraint(
            "display_order >= 0", name="ck_experiment_tasks_display_order_non_negative"
        ),
        db.CheckConstraint(
            "updated_at >= created_at", name="ck_experiment_tasks_timestamps_ordered"
        ),
        db.Index("ix_experiment_tasks_set_order_id", "task_set_id", "display_order", "id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    task_set_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("experiment_task_sets.id"),
        nullable=False,
    )
    display_order = db.Column(db.Integer, nullable=False)
    task_type = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(TASK_TITLE_MAX_LENGTH), nullable=False)
    participant_instructions = db.Column(db.Text, nullable=False)
    #: What the Researcher means by success. Researcher-facing only.
    expected_goal = db.Column(db.Text, nullable=False)
    difficulty = db.Column(db.String(16), nullable=False)
    recommended_duration_seconds = db.Column(db.Integer, nullable=False)
    #: The criterion the protocol intends; never verified in M02A.
    completion_criterion = db.Column(db.String(40), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("task_type")
    def validate_task_type(self, _key, value):
        if value not in _TYPE_VALUES:
            raise ValueError(f"Invalid experiment task type: {value}")
        return value

    @validates("difficulty")
    def validate_difficulty(self, _key, value):
        if value not in _DIFFICULTY_VALUES:
            raise ValueError(f"Invalid experiment task difficulty: {value}")
        return value

    @validates("completion_criterion")
    def validate_completion_criterion(self, _key, value):
        if value not in _CRITERION_VALUES:
            raise ValueError(f"Invalid experiment completion criterion: {value}")
        return value

    @validates("recommended_duration_seconds")
    def validate_recommended_duration(self, _key, value):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not MIN_RECOMMENDED_DURATION_SECONDS <= value <= MAX_RECOMMENDED_DURATION_SECONDS
        ):
            raise ValueError("A recommended duration must be 30 to 1800 whole seconds")
        return value


def stored_task_definition_status(connection, task_id):
    """The stored status of the definition above **stored** task
    `task_id`, or ``None``."""
    if task_id is None:
        return None
    tasks = ExperimentTask.__table__
    sets = ExperimentTaskSet.__table__
    definitions = ExperimentDefinition.__table__
    return connection.execute(
        sa.select(definitions.c.status)
        .select_from(
            tasks.join(sets, sets.c.id == tasks.c.task_set_id).join(
                definitions, definitions.c.id == sets.c.definition_id
            )
        )
        .where(tasks.c.id == task_id)
    ).scalar()


@event.listens_for(ExperimentTask, "before_insert")
def _only_a_draft_gains_a_task(_mapper, connection, target):
    if stored_set_definition_status(connection, target.task_set_id) != _DRAFT:
        raise ExperimentProtocolError(
            "A task can only be added to a task set of a draft experiment protocol."
        )


@event.listens_for(ExperimentTask, "before_update")
def _refuse_changing_a_frozen_task(_mapper, connection, target):
    changed = changed_columns(target)
    if not changed:
        return
    fixed = sorted(changed & TASK_FIXED_COLUMNS)
    if fixed:
        raise ExperimentProtocolError(
            "A task's " + ", ".join(fixed) + " cannot change after it is created."
        )
    if stored_task_definition_status(connection, target.id) != _DRAFT:
        raise ExperimentProtocolError(
            "A task of a frozen experiment protocol version cannot change."
        )


@event.listens_for(ExperimentTask, "before_delete")
def _refuse_deleting_a_task(_mapper, _connection, _target):
    raise ExperimentProtocolError("An experiment task is never deleted")
