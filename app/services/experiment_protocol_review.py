"""Structural review and content digest of one protocol version
(Phase 6 / M02A).

Flask-independent pure functions over plain values: the review page calls
them on the rows it read, and the activation transaction calls them again on
the rows it locked -- so the page and the write can never disagree about
what "complete" means or what the digest covers.

`content` is the ordered sequence of ``(task_set, tasks)`` pairs, where a
task set exposes ``set_code`` and ``title`` and each task exposes the fields
in :data:`TASK_DIGEST_FIELDS`. ORM entities and query rows both qualify.

**What the review checks is structure, never methodology.** At least one
task set; no empty set; the four-set and ten-task limits; and, when there is
more than one set, the same number of tasks with the same task type and
completion criterion at every position, plus a written equivalence
rationale. It does **not** decide that two sets are equivalent -- that is a
methodological assertion the Researcher makes and the software records but
cannot validate -- and it does not verify that any task can be completed.
"""

from collections import namedtuple

from app.models import MAX_TASK_SETS_PER_PROTOCOL, MAX_TASKS_PER_SET, protocol_digest

#: The task fields the digest covers, in order.
TASK_DIGEST_FIELDS = (
    "task_type",
    "title",
    "participant_instructions",
    "expected_goal",
    "difficulty",
    "recommended_duration_seconds",
    "completion_criterion",
)

ProtocolProblem = namedtuple("ProtocolProblem", "code message")

NO_SETS = "no_sets"
TOO_MANY_SETS = "too_many_sets"
EMPTY_SET = "empty_set"
TOO_MANY_TASKS = "too_many_tasks"
UNEQUAL_SETS = "unequal_sets"
NOT_PARALLEL = "not_parallel"
MISSING_RATIONALE = "missing_rationale"


def header_values(definition):
    return (
        definition.version_identifier,
        definition.title,
        definition.study_stage,
        definition.equivalence_rationale,
    )


def task_values(task):
    return tuple(getattr(task, field) for field in TASK_DIGEST_FIELDS)


def content_digest(definition, content):
    """:func:`~app.models.protocol_digest` of `definition` and its ordered
    `content`."""
    return protocol_digest(
        header_values(definition),
        [
            (task_set.set_code, task_set.title, [task_values(task) for task in tasks])
            for task_set, tasks in content
        ],
    )


def structural_problems(equivalence_rationale, content):
    """Every reason `content` cannot be activated yet, in reading order, or
    an empty tuple."""
    problems = []
    content = [(task_set, list(tasks)) for task_set, tasks in content]
    if not content:
        return (ProtocolProblem(NO_SETS, "Add at least one task set."),)
    if len(content) > MAX_TASK_SETS_PER_PROTOCOL:
        problems.append(ProtocolProblem(
            TOO_MANY_SETS,
            f"A protocol version may have at most {MAX_TASK_SETS_PER_PROTOCOL} task sets.",
        ))
    for task_set, tasks in content:
        if not tasks:
            problems.append(ProtocolProblem(
                EMPTY_SET, f"Task set {task_set.set_code} has no tasks yet."
            ))
        elif len(tasks) > MAX_TASKS_PER_SET:
            problems.append(ProtocolProblem(
                TOO_MANY_TASKS,
                f"Task set {task_set.set_code} has more than {MAX_TASKS_PER_SET} tasks.",
            ))
    if len(content) > 1:
        counts = {len(tasks) for _task_set, tasks in content}
        if len(counts) > 1:
            problems.append(ProtocolProblem(
                UNEQUAL_SETS, "Every task set must contain the same number of tasks."
            ))
        else:
            for position in range(counts.pop()):
                shapes = {
                    (tasks[position].task_type, tasks[position].completion_criterion)
                    for _task_set, tasks in content
                }
                if len(shapes) > 1:
                    problems.append(ProtocolProblem(
                        NOT_PARALLEL,
                        f"Task {position + 1} must have the same task type and completion "
                        "criterion in every task set.",
                    ))
        if not equivalence_rationale:
            problems.append(ProtocolProblem(
                MISSING_RATIONALE,
                "Explain why the task sets are intended to be equivalent. More than one "
                "task set needs a written equivalence rationale.",
            ))
    return tuple(problems)
