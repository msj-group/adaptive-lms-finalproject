"""Read queries and presentation for the Phase 6 / M02A experiment protocol
catalogue.

Flask-independent. **Read-only**: nothing here adds, changes, flushes, locks
or commits a row.

**Column tuples, never entities, and never an internal id.** Every function
selects the exact columns declared beside it. None selects a numeric id, a
``*_by_id`` actor column, or any ``users`` column -- there is no join to
``users`` at all, not even for the Researcher who activated a version -- so
no identity value is loaded into a Researcher page. Nested objects are
resolved by their public identifiers through their parents, so a task set is
found only *inside* the protocol named by the URL, and a task only inside
that set: a well-formed identifier from another protocol is simply not
found.

**Bounded and free of N+1.** The protocol list is counted once and paged
once (:data:`PAGE_SIZE`). One protocol's complete content is exactly two
queries -- its sets, then all of its tasks -- and is bounded by the four-set
and ten-task limits the write path enforces.

No participant, Student, session or behavioural table is read by anything
in this module.
"""

from collections import namedtuple

from sqlalchemy import func
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    ExperimentCompletionCriterion,
    ExperimentDefinition,
    ExperimentDefinitionStatus,
    ExperimentStudyStage,
    ExperimentTask,
    ExperimentTaskDifficulty,
    ExperimentTaskSet,
    ExperimentTaskType,
)
from app.services.experiment_protocol_review import (
    content_digest,
    structural_problems,
)
from app.services.research_queries import normalize_page, public_id_is_well_formed
from app.services.schedule_occurrences import to_app_local

__all__ = [
    "PAGE_SIZE",
    "STATUS_ORDER",
    "STATUS_LABELS",
    "STATUS_FILTERS",
    "TASK_TYPE_LABELS",
    "DIFFICULTY_LABELS",
    "CRITERION_LABELS",
    "STAGE_LABELS",
    "normalize_page",
    "normalize_status_filter",
    "protocol_counts",
    "protocols_page",
    "protocol_header",
    "current_protocol",
    "protocol_content",
    "task_set_row",
    "task_row",
    "preview_digest",
    "review_problems",
    "local",
]

#: Protocol versions per page on the list.
PAGE_SIZE = 25

_DRAFT = ExperimentDefinitionStatus.DRAFT.value
_ACTIVE = ExperimentDefinitionStatus.ACTIVE.value

STATUS_ORDER = tuple(status.value for status in ExperimentDefinitionStatus)

STATUS_LABELS = {
    ExperimentDefinitionStatus.DRAFT.value: "Draft",
    ExperimentDefinitionStatus.ACTIVE.value: "Active",
    ExperimentDefinitionStatus.SUPERSEDED.value: "Superseded",
    ExperimentDefinitionStatus.DISCARDED.value: "Discarded",
}

STATUS_FILTERS = {"all": "All protocol versions", **STATUS_LABELS}

STAGE_LABELS = {
    ExperimentStudyStage.VERSION_A_COLLECTION.value: "Version A collection",
}

TASK_TYPE_LABELS = {
    ExperimentTaskType.DASHBOARD_NAVIGATION.value: "Dashboard navigation",
    ExperimentTaskType.FIND_LESSON.value: "Find a lesson",
    ExperimentTaskType.SEARCH.value: "Search",
    ExperimentTaskType.QUIZ_COMPLETION.value: "Quiz completion",
    ExperimentTaskType.ASSIGNMENT_SUBMISSION.value: "Assignment submission (text)",
}

DIFFICULTY_LABELS = {
    ExperimentTaskDifficulty.EASY.value: "Easy",
    ExperimentTaskDifficulty.MEDIUM.value: "Medium",
    ExperimentTaskDifficulty.HARD.value: "Hard",
}

CRITERION_LABELS = {
    ExperimentCompletionCriterion.PARTICIPANT_DECLARED.value:
        "Participant states the task is finished",
    ExperimentCompletionCriterion.LESSON_OPENED.value: "A target lesson is opened",
    ExperimentCompletionCriterion.QUIZ_ATTEMPT_SUBMITTED.value:
        "A target quiz attempt is submitted",
    ExperimentCompletionCriterion.ASSIGNMENT_SUBMITTED.value:
        "A target assignment is submitted",
}

#: The exact columns of one protocol list row.
PROTOCOL_LIST_COLUMNS = (
    ExperimentDefinition.public_id,
    ExperimentDefinition.version_identifier,
    ExperimentDefinition.title,
    ExperimentDefinition.status,
    ExperimentDefinition.created_at,
    ExperimentDefinition.activated_at,
)

_Source = aliased(ExperimentDefinition, name="source")

#: The exact columns of one protocol's header. The lineage is read as the
#: source version's public id and version identifier, never its numeric id.
PROTOCOL_DETAIL_COLUMNS = (
    ExperimentDefinition.public_id,
    ExperimentDefinition.version_identifier,
    ExperimentDefinition.title,
    ExperimentDefinition.study_stage,
    ExperimentDefinition.equivalence_rationale,
    ExperimentDefinition.status,
    ExperimentDefinition.version,
    ExperimentDefinition.content_digest,
    ExperimentDefinition.created_at,
    ExperimentDefinition.activated_at,
    ExperimentDefinition.superseded_at,
    ExperimentDefinition.discarded_at,
    _Source.public_id.label("derived_from_public_id"),
    _Source.version_identifier.label("derived_from_version"),
)

#: The exact columns of one task set.
SET_COLUMNS = (
    ExperimentTaskSet.public_id,
    ExperimentTaskSet.set_code,
    ExperimentTaskSet.title,
)

#: The exact columns of one task, with its set's public id.
TASK_COLUMNS = (
    ExperimentTaskSet.public_id.label("set_public_id"),
    ExperimentTask.public_id,
    ExperimentTask.task_type,
    ExperimentTask.title,
    ExperimentTask.participant_instructions,
    ExperimentTask.expected_goal,
    ExperimentTask.difficulty,
    ExperimentTask.recommended_duration_seconds,
    ExperimentTask.completion_criterion,
)

#: One task set with its ordered tasks.
SetContent = namedtuple("SetContent", "task_set tasks")


def normalize_status_filter(value):
    """One of :data:`STATUS_FILTERS`, or ``"all"``."""
    value = (value or "").strip()
    return value if value in STATUS_FILTERS else "all"


def protocol_counts():
    """``{status: count}`` over every protocol version. One grouped query."""
    rows = (
        db.session.query(ExperimentDefinition.status, func.count(ExperimentDefinition.id))
        .group_by(ExperimentDefinition.status)
        .all()
    )
    counted = dict(rows)
    return {status: int(counted.get(status, 0)) for status in STATUS_ORDER}


def protocols_page(status, page):
    """``(rows, total, page)`` -- one page of protocol versions, newest
    first."""
    count = db.session.query(func.count(ExperimentDefinition.id))
    listing = db.session.query(*PROTOCOL_LIST_COLUMNS)
    if status != "all":
        count = count.filter(ExperimentDefinition.status == status)
        listing = listing.filter(ExperimentDefinition.status == status)
    total = int(count.scalar() or 0)
    if page > 1 and (page - 1) * PAGE_SIZE >= total:
        page = 1
    rows = (
        listing.order_by(ExperimentDefinition.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page


def protocol_header(public_id):
    """One protocol version's header row, or ``None``."""
    if not public_id_is_well_formed(public_id):
        return None
    return (
        db.session.query(*PROTOCOL_DETAIL_COLUMNS)
        .select_from(ExperimentDefinition)
        .outerjoin(_Source, _Source.id == ExperimentDefinition.derived_from_id)
        .filter(ExperimentDefinition.public_id == public_id)
        .first()
    )


def current_protocol(study_stage):
    """``(public_id, version_identifier)`` of the active version for
    `study_stage`, or ``None``."""
    return (
        db.session.query(ExperimentDefinition.public_id, ExperimentDefinition.version_identifier)
        .filter(ExperimentDefinition.study_stage == study_stage,
                ExperimentDefinition.status == _ACTIVE)
        .first()
    )


def protocol_content(public_id):
    """The ordered sets of protocol `public_id`, each with its ordered
    tasks, as a list of :class:`SetContent`. Two queries."""
    sets = (
        db.session.query(*SET_COLUMNS)
        .join(ExperimentDefinition, ExperimentDefinition.id == ExperimentTaskSet.definition_id)
        .filter(ExperimentDefinition.public_id == public_id)
        .order_by(ExperimentTaskSet.display_order, ExperimentTaskSet.id)
        .all()
    )
    if not sets:
        return []
    tasks = (
        db.session.query(*TASK_COLUMNS)
        .join(ExperimentTaskSet, ExperimentTaskSet.id == ExperimentTask.task_set_id)
        .join(ExperimentDefinition, ExperimentDefinition.id == ExperimentTaskSet.definition_id)
        .filter(ExperimentDefinition.public_id == public_id)
        .order_by(ExperimentTask.display_order, ExperimentTask.id)
        .all()
    )
    by_set = {task_set.public_id: [] for task_set in sets}
    for task in tasks:
        by_set[task.set_public_id].append(task)
    return [SetContent(task_set, by_set[task_set.public_id]) for task_set in sets]


def task_set_row(protocol_public_id, set_public_id):
    """One task set **of that protocol**, or ``None``."""
    if not (public_id_is_well_formed(protocol_public_id)
            and public_id_is_well_formed(set_public_id)):
        return None
    return (
        db.session.query(*SET_COLUMNS)
        .join(ExperimentDefinition, ExperimentDefinition.id == ExperimentTaskSet.definition_id)
        .filter(ExperimentDefinition.public_id == protocol_public_id,
                ExperimentTaskSet.public_id == set_public_id)
        .first()
    )


def task_row(protocol_public_id, set_public_id, task_public_id):
    """One task **of that set of that protocol**, or ``None``."""
    if not all(public_id_is_well_formed(value)
               for value in (protocol_public_id, set_public_id, task_public_id)):
        return None
    return (
        db.session.query(*TASK_COLUMNS)
        .join(ExperimentTaskSet, ExperimentTaskSet.id == ExperimentTask.task_set_id)
        .join(ExperimentDefinition, ExperimentDefinition.id == ExperimentTaskSet.definition_id)
        .filter(ExperimentDefinition.public_id == protocol_public_id,
                ExperimentTaskSet.public_id == set_public_id,
                ExperimentTask.public_id == task_public_id)
        .first()
    )


def preview_digest(header, content):
    """The digest activation would seal for `header` and `content` exactly
    as read."""
    return content_digest(header, content)


def review_problems(header, content):
    return structural_problems(header.equivalence_rationale, content)


def local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)
