"""Phase 6 / M02A model, constraint and guard checks.

The three catalogue tables: their exact columns and explicit ``NOT NULL``s,
their closed sets and type/criterion pairs, the lifecycle truth table, the
one-active-version-per-stage invariant, the immutability of a frozen
version's whole aggregate, immutable parent links, the refusal of every
delete and bulk rewrite, and the content digest.

SQLite (the test backend) enforces CHECK, UNIQUE and -- with the project's
``PRAGMA foreign_keys=ON`` -- foreign keys. It proves nothing about
MySQL/InnoDB blocking, isolation or collation.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

import tests.protocol_fixtures as px
import tests.research_fixtures as rx
from app.extensions import db
from app.models import (
    ALLOWED_CRITERIA_BY_TYPE,
    ExperimentCompletionCriterion,
    ExperimentDefinition,
    ExperimentProtocolError,
    ExperimentTask,
    ExperimentTaskSet,
    ExperimentTaskType,
    protocol_digest,
)
from app.models.submission_feedback import whole_second_utc

_DEFINITIONS = "experiment_definitions"
_SETS = "experiment_task_sets"
_TASKS = "experiment_tasks"
_TABLES = (_DEFINITIONS, _SETS, _TASKS)

_COLUMNS = {
    _DEFINITIONS: {
        "id", "public_id", "version_identifier", "title", "study_stage",
        "equivalence_rationale", "status", "current_marker", "content_digest",
        "derived_from_id", "version", "created_by_id", "activated_at", "activated_by_id",
        "superseded_at", "superseded_by_id", "discarded_at", "discarded_by_id",
        "created_at", "updated_at",
    },
    _SETS: {"id", "public_id", "definition_id", "set_code", "title", "display_order",
            "created_at", "updated_at"},
    _TASKS: {"id", "public_id", "task_set_id", "display_order", "task_type", "title",
             "participant_instructions", "expected_goal", "difficulty",
             "recommended_duration_seconds", "completion_criterion", "created_at",
             "updated_at"},
}

#: The only columns allowed to hold NULL. Everything else is NOT NULL.
_NULLABLE = {
    _DEFINITIONS: {"equivalence_rationale", "current_marker", "content_digest",
                   "derived_from_id", "activated_at", "activated_by_id", "superseded_at",
                   "superseded_by_id", "discarded_at", "discarded_by_id"},
    _SETS: set(),
    _TASKS: set(),
}

_T0 = "2026-07-01 09:00:00"
_T1 = "2026-07-02 09:00:00"


@pytest.fixture
def people(app):
    return rx.world(app)


def _raw_insert(table, values):
    columns = ", ".join(values)
    marks = ", ".join(f":{name}" for name in values)
    db.session.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({marks})"), values)
    db.session.commit()


def _refused(table, values):
    with pytest.raises(IntegrityError):
        _raw_insert(table, values)
    db.session.rollback()


def _definition_values(researcher, **overrides):
    values = {
        "public_id": "d-raw", "version_identifier": "VA-RAW", "title": "T",
        "study_stage": "version_a_collection", "equivalence_rationale": None,
        "status": "draft", "current_marker": None, "content_digest": None,
        "derived_from_id": None, "version": 1, "created_by_id": researcher.id,
        "activated_at": None, "activated_by_id": None, "superseded_at": None,
        "superseded_by_id": None, "discarded_at": None, "discarded_by_id": None,
        "created_at": _T0, "updated_at": _T0,
    }
    values.update(overrides)
    return values


def _set_values(definition, **overrides):
    values = {"public_id": "s-raw", "definition_id": definition.id, "set_code": "RAW",
              "title": "T", "display_order": 0, "created_at": _T0, "updated_at": _T0}
    values.update(overrides)
    return values


def _task_values(task_set, **overrides):
    values = {"public_id": "t-raw", "task_set_id": task_set.id, "display_order": 0,
              "created_at": _T0, "updated_at": _T0, **px.TASK}
    values.update(overrides)
    return values


def _refuse_flush(fn):
    fn()
    with pytest.raises(ExperimentProtocolError):
        db.session.commit()
    db.session.rollback()


# ===========================================================================
# Shape
# ===========================================================================


def test_the_three_tables_exist_with_exactly_the_expected_columns(app):
    inspector = sa.inspect(db.engine)
    for table in _TABLES:
        assert {c["name"] for c in inspector.get_columns(table)} == _COLUMNS[table], table


def test_every_mandatory_column_is_declared_not_null(app):
    """A CHECK over NULL passes, so NOT NULL must be declared explicitly."""
    inspector = sa.inspect(db.engine)
    for table in _TABLES:
        nullable = {c["name"] for c in inspector.get_columns(table) if c["nullable"]}
        assert nullable == _NULLABLE[table], table


@pytest.mark.parametrize("table", _TABLES)
def test_the_database_refuses_null_in_every_mandatory_column(app, people, table):
    researcher = people["researcher"]
    draft = px.definition_row(researcher)
    task_set = px.set_row(draft)
    builders = {
        _DEFINITIONS: lambda **o: _definition_values(researcher, **o),
        _SETS: lambda **o: _set_values(draft, **{"set_code": "NULLTEST", **o}),
        _TASKS: lambda **o: _task_values(task_set, **o),
    }
    for column in sorted(_COLUMNS[table] - _NULLABLE[table] - {"id"}):
        _refused(table, builders[table](**{column: None}))


def test_no_column_is_shaped_to_hold_participant_or_behaviour_data(app):
    forbidden = (
        "participant_id", "participant_code", "student", "session", "event", "rating",
        "frustration", "emotion", "survey", "answer", "observ", "annotation", "payload",
        "ip_address", "agent", "fingerprint", "device", "keystroke", "audio", "recording",
        "email", "password", "lesson_id", "quiz_id", "assignment_id", "group_id", "json",
        "expression", "script",
    )
    inspector = sa.inspect(db.engine)
    for table in _TABLES:
        for column in inspector.get_columns(table):
            for word in forbidden:
                assert word not in column["name"], (table, column["name"], word)


def test_no_foreign_key_references_a_participant_and_only_actors_reference_users(app):
    inspector = sa.inspect(db.engine)
    references = {
        table: {(fk["constrained_columns"][0], fk["referred_table"])
                for fk in inspector.get_foreign_keys(table)}
        for table in _TABLES
    }
    assert references == {
        _DEFINITIONS: {("derived_from_id", _DEFINITIONS), ("created_by_id", "users"),
                       ("activated_by_id", "users"), ("superseded_by_id", "users"),
                       ("discarded_by_id", "users")},
        _SETS: {("definition_id", _DEFINITIONS)},
        _TASKS: {("task_set_id", _SETS)},
    }
    for table in _TABLES:
        for fk in inspector.get_foreign_keys(table):
            assert not fk.get("options"), (table, fk)


def test_the_indexes_are_exactly_the_declared_query_paths(app):
    inspector = sa.inspect(db.engine)
    indexes = {table: {i["name"]: i["column_names"] for i in inspector.get_indexes(table)}
               for table in _TABLES}
    assert indexes == {
        _DEFINITIONS: {
            "ix_experiment_definitions_status_id": ["status", "id"],
            "ix_experiment_definitions_created_by_id": ["created_by_id"],
            "ix_experiment_definitions_activated_by_id": ["activated_by_id"],
            "ix_experiment_definitions_superseded_by_id": ["superseded_by_id"],
            "ix_experiment_definitions_discarded_by_id": ["discarded_by_id"],
            "ix_experiment_definitions_derived_from_id": ["derived_from_id"],
        },
        _SETS: {"ix_experiment_task_sets_definition_order_id":
                ["definition_id", "display_order", "id"]},
        _TASKS: {"ix_experiment_tasks_set_order_id": ["task_set_id", "display_order", "id"]},
    }


def test_no_orm_relationship_is_declared_on_any_m02a_model():
    for model in (ExperimentDefinition, ExperimentTaskSet, ExperimentTask):
        assert not sa.inspect(model).relationships, model


# ===========================================================================
# Closed sets, pairs, bounds and uniqueness
# ===========================================================================


def test_the_closed_sets_are_enforced_by_the_database(app, people):
    researcher = people["researcher"]
    draft = px.definition_row(researcher)
    task_set = px.set_row(draft)
    _refused(_DEFINITIONS, _definition_values(researcher, status="archived"))
    _refused(_DEFINITIONS, _definition_values(researcher, study_stage="ab_evaluation"))
    _refused(_TASKS, _task_values(task_set, task_type="login",
                                  completion_criterion="participant_declared"))
    _refused(_TASKS, _task_values(task_set, task_type="profile_settings",
                                  completion_criterion="participant_declared"))
    _refused(_TASKS, _task_values(task_set, difficulty="extreme"))
    _refused(_TASKS, _task_values(task_set, completion_criterion="verified_by_model"))


def test_login_and_profile_settings_are_deliberately_absent():
    values = {member.value for member in ExperimentTaskType}
    assert values == {"dashboard_navigation", "find_lesson", "search", "quiz_completion",
                      "assignment_submission"}


def test_every_type_and_criterion_pair_is_enforced_exactly(app, people):
    task_set = px.set_row(px.definition_row(people["researcher"]))
    number = 0
    for task_type in ExperimentTaskType:
        for criterion in ExperimentCompletionCriterion:
            number += 1
            values = _task_values(task_set, public_id=f"t-{number}", display_order=number,
                                  task_type=task_type.value,
                                  completion_criterion=criterion.value)
            if criterion.value in ALLOWED_CRITERIA_BY_TYPE[task_type.value]:
                _raw_insert(_TASKS, values)
            else:
                _refused(_TASKS, values)


def test_the_recommended_duration_bounds(app, people):
    task_set = px.set_row(px.definition_row(people["researcher"]))
    _raw_insert(_TASKS, _task_values(task_set, public_id="t-30",
                                     recommended_duration_seconds=30))
    _raw_insert(_TASKS, _task_values(task_set, public_id="t-1800",
                                     recommended_duration_seconds=1800))
    _refused(_TASKS, _task_values(task_set, recommended_duration_seconds=29))
    _refused(_TASKS, _task_values(task_set, recommended_duration_seconds=1801))


def test_empty_text_and_negative_order_are_refused(app, people):
    researcher = people["researcher"]
    draft = px.definition_row(researcher)
    task_set = px.set_row(draft)
    _refused(_DEFINITIONS, _definition_values(researcher, version_identifier=""))
    _refused(_DEFINITIONS, _definition_values(researcher, title=""))
    _refused(_DEFINITIONS, _definition_values(researcher, equivalence_rationale=""))
    _refused(_DEFINITIONS, _definition_values(researcher, version=0))
    _refused(_SETS, _set_values(draft, set_code=""))
    _refused(_SETS, _set_values(draft, set_code="X", title=""))
    _refused(_SETS, _set_values(draft, set_code="X", display_order=-1))
    for column in ("title", "participant_instructions", "expected_goal"):
        _refused(_TASKS, _task_values(task_set, **{column: ""}))
    _refused(_TASKS, _task_values(task_set, display_order=-1))


def test_uniqueness_of_version_identifier_set_code_and_public_ids(app, people):
    researcher = people["researcher"]
    first = px.definition_row(researcher, version="VA-1")
    second = px.definition_row(researcher, version="VA-2")
    px.set_row(first, code="SET-A")
    _refused(_DEFINITIONS, _definition_values(researcher, version_identifier="VA-1"))
    _refused(_DEFINITIONS, _definition_values(researcher, public_id=first.public_id))
    _refused(_SETS, _set_values(first, set_code="SET-A"))
    # The same code in another version is allowed -- it is the stable
    # identifier that a derived version keeps.
    _raw_insert(_SETS, _set_values(second, set_code="SET-A"))


def test_only_one_version_per_stage_can_be_active(app, people):
    researcher = people["researcher"]
    active = px.activate_row(px.complete_row(researcher, version="VA-1"), researcher)
    assert active.current_marker == 1
    _refused(_DEFINITIONS, _definition_values(
        researcher, status="active", current_marker=1, content_digest="b" * 64,
        activated_at=_T0, activated_by_id=researcher.id))


def test_the_current_marker_must_match_the_status(app, people):
    researcher = people["researcher"]
    # An active row with no marker would escape the unique index entirely.
    _refused(_DEFINITIONS, _definition_values(
        researcher, status="active", current_marker=None, content_digest="b" * 64,
        activated_at=_T0, activated_by_id=researcher.id))
    _refused(_DEFINITIONS, _definition_values(researcher, current_marker=1))
    _refused(_DEFINITIONS, _definition_values(researcher, current_marker=2))


def test_the_lifecycle_check_rejects_impossible_combinations(app, people):
    researcher = people["researcher"]
    rid = researcher.id
    digest = "c" * 64
    for overrides in (
        {"activated_at": _T0, "activated_by_id": rid},                        # draft, activated
        {"content_digest": digest},                                          # draft, sealed
        {"discarded_at": _T0, "discarded_by_id": rid},                        # draft, discarded
        {"status": "active", "current_marker": 1, "activated_at": _T0,
         "activated_by_id": rid},                                             # active, no digest
        {"status": "active", "current_marker": 1, "content_digest": digest},   # never activated
        {"status": "superseded", "content_digest": digest, "activated_at": _T1,
         "activated_by_id": rid, "superseded_at": _T0, "superseded_by_id": rid,
         "updated_at": _T1},                                                  # superseded first
        {"status": "superseded", "content_digest": digest, "activated_at": _T0,
         "activated_by_id": rid},                                             # never superseded
        {"status": "discarded"},                                              # no moment
        {"status": "discarded", "discarded_at": _T0, "discarded_by_id": rid,
         "content_digest": digest},                                           # sealed discard
        {"status": "discarded", "discarded_at": _T0, "discarded_by_id": rid,
         "activated_at": _T0, "activated_by_id": rid},                        # activated discard
        {"activated_at": _T0},                                                # moment, no actor
        {"discarded_by_id": rid},                                             # actor, no moment
        {"content_digest": "short", "status": "active", "current_marker": 1,
         "activated_at": _T0, "activated_by_id": rid},                        # digest format
        {"updated_at": "2026-06-30 09:00:00"},                                # before created
    ):
        _refused(_DEFINITIONS, _definition_values(researcher, **overrides))


def test_the_model_validators_refuse_values_outside_the_closed_sets(app, people):
    researcher = people["researcher"]
    definition = px.definition_row(researcher)
    task = px.task_row(px.set_row(definition))
    for obj, attribute, value in (
        (definition, "status", "archived"),
        (definition, "study_stage", "ab_evaluation"),
        (definition, "current_marker", 2),
        (definition, "content_digest", "Z" * 64),
        (definition, "version", 0),
        (definition, "version", True),
        (task, "task_type", "login"),
        (task, "difficulty", "extreme"),
        (task, "completion_criterion", "verified"),
        (task, "recommended_duration_seconds", 29),
        (task, "recommended_duration_seconds", "120"),
    ):
        with pytest.raises(ValueError):
            setattr(obj, attribute, value)
    db.session.rollback()


# ===========================================================================
# Frozen versions, immutable parents, no deletes, no bulk rewrites
# ===========================================================================


def test_a_draft_may_change_its_content(app, people):
    researcher = people["researcher"]
    definition = px.definition_row(researcher)
    task_set = px.set_row(definition)
    task = px.task_row(task_set)
    definition.title = "Changed"
    task_set.title = "Changed set"
    task.title = "Changed task"
    db.session.commit()
    assert (definition.title, task_set.title, task.title) == (
        "Changed", "Changed set", "Changed task")


@pytest.mark.parametrize("column", ["public_id", "derived_from_id", "created_by_id",
                                    "created_at"])
def test_a_drafts_identity_columns_never_change(app, people, column):
    """``study_stage`` is fixed too, but it has one legal value, so assigning
    it is never a change; the other four are exercised here."""
    researcher = people["researcher"]
    definition = px.definition_row(researcher)
    other = px.definition_row(researcher, version="VA-2")
    replacement = {
        "public_id": "7b8b2f3e-0000-4000-8000-000000000000",
        "derived_from_id": other.id,
        "created_by_id": people["admin"].id,
        "created_at": whole_second_utc().replace(year=2020),
    }[column]
    _refuse_flush(lambda: setattr(definition, column, replacement))


@pytest.mark.parametrize("column, value", [
    ("version_identifier", "VA-9"), ("title", "Rewritten"),
    ("equivalence_rationale", "Rewritten rationale"), ("content_digest", "d" * 64),
])
def test_an_active_versions_content_and_seal_never_change(app, people, column, value):
    researcher = people["researcher"]
    active = px.activate_row(px.complete_row(researcher), researcher)
    _refuse_flush(lambda: setattr(active, column, value))


def test_an_active_version_may_only_be_superseded(app, people):
    researcher = people["researcher"]
    old = px.activate_row(px.complete_row(researcher, version="VA-1"), researcher)
    new = px.complete_row(researcher, version="VA-2")
    px.activate_row(new, researcher, supersede=old)
    db.session.expire_all()
    assert (old.status, old.current_marker, new.status, new.current_marker) == (
        px.SUPERSEDED, None, px.ACTIVE, 1)


@pytest.mark.parametrize("terminal", [px.SUPERSEDED, px.DISCARDED])
def test_a_superseded_or_discarded_version_is_completely_frozen(app, people, terminal):
    researcher = people["researcher"]
    if terminal == px.SUPERSEDED:
        target = px.activate_row(px.complete_row(researcher, version="VA-1"), researcher)
        px.activate_row(px.complete_row(researcher, version="VA-2"), researcher, supersede=target)
    else:
        target = px.discard_row(px.complete_row(researcher, version="VA-1"), researcher)
    for column, value in (("status", "draft"), ("version", 99), ("title", "x"),
                          ("updated_at", whole_second_utc().replace(year=2030))):
        _refuse_flush(lambda: setattr(target, column, value))


def test_a_frozen_versions_sets_and_tasks_never_change(app, people):
    researcher = people["researcher"]
    active = px.complete_row(researcher)
    task_set = px.sets_of(active)[0]
    task = px.tasks_of(task_set)[0]
    px.activate_row(active, researcher)
    for column, value in (("set_code", "SET-Z"), ("title", "x"), ("display_order", 5)):
        _refuse_flush(lambda: setattr(task_set, column, value))
    for column, value in (("title", "x"), ("participant_instructions", "x"),
                          ("expected_goal", "x"), ("difficulty", "hard"),
                          ("recommended_duration_seconds", 600), ("display_order", 3)):
        _refuse_flush(lambda: setattr(task, column, value))


def test_reparenting_can_never_detach_frozen_content(app, people):
    """Moving a frozen set into a draft, or a frozen task into a draft's set,
    is refused -- and the frozen content stays exactly where it was."""
    researcher = people["researcher"]
    active = px.complete_row(researcher, version="VA-1")
    frozen_set = px.sets_of(active)[0]
    frozen_task = px.tasks_of(frozen_set)[0]
    px.activate_row(active, researcher)
    draft = px.definition_row(researcher, version="VA-2")
    draft_set = px.set_row(draft)
    draft_id, draft_set_id = draft.id, draft_set.id
    _refuse_flush(lambda: setattr(frozen_set, "definition_id", draft_id))
    _refuse_flush(lambda: setattr(frozen_task, "task_set_id", draft_set_id))
    # Even between two drafts a parent never changes.
    other_set = px.set_row(draft, code="SET-B")
    draft_task = px.task_row(draft_set)
    other_set_id = other_set.id
    _refuse_flush(lambda: setattr(draft_task, "task_set_id", other_set_id))
    db.session.expire_all()
    assert frozen_set.definition_id == active.id
    assert frozen_task.task_set_id == frozen_set.id
    assert px.content_digest(active, px._content(active.id)) == active.content_digest


def test_nothing_is_added_to_a_frozen_or_discarded_version(app, people):
    researcher = people["researcher"]
    active = px.activate_row(px.complete_row(researcher, version="VA-1"), researcher)
    discarded = px.discard_row(px.complete_row(researcher, version="VA-2"), researcher)
    for target in (active, discarded):
        with pytest.raises(ExperimentProtocolError):
            px.set_row(target, code="SET-NEW")
        db.session.rollback()
        with pytest.raises(ExperimentProtocolError):
            px.task_row(px.sets_of(target)[0], order=9)
        db.session.rollback()


def test_the_guard_reads_the_stored_status_not_the_attribute_history(app, people):
    """An expired attribute carries no old value in memory; the guard reads
    the stored row, so expiring before assigning cannot slip past it."""
    researcher = people["researcher"]
    active = px.activate_row(px.complete_row(researcher), researcher)
    task = px.tasks_of(px.sets_of(active)[0])[0]
    db.session.expire(active)
    db.session.expire(task)

    def _assign():
        active.title = "Expired then assigned"

    _refuse_flush(_assign)
    db.session.expire(task)
    _refuse_flush(lambda: setattr(task, "title", "Expired then assigned"))


@pytest.mark.parametrize("which", ["definition", "set", "task"])
def test_no_protocol_row_is_ever_deleted_even_a_draft(app, people, which):
    researcher = people["researcher"]
    definition = px.definition_row(researcher)
    task_set = px.set_row(definition)
    task = px.task_row(task_set)
    target = {"definition": definition, "set": task_set, "task": task}[which]
    with pytest.raises(ExperimentProtocolError):
        db.session.delete(target)
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize("model", [ExperimentDefinition, ExperimentTaskSet, ExperimentTask])
def test_no_m02a_table_can_be_rewritten_or_emptied_in_bulk(app, people, model):
    researcher = people["researcher"]
    px.task_row(px.set_row(px.definition_row(researcher)))
    with pytest.raises(ExperimentProtocolError):
        db.session.execute(update(model).values(updated_at=whole_second_utc()))
    db.session.rollback()
    with pytest.raises(ExperimentProtocolError):
        db.session.execute(delete(model))
    db.session.rollback()
    assert model.query.count() == 1


# ===========================================================================
# The content digest
# ===========================================================================

_HEADER = ("VA-1", "Title", "version_a_collection", "Rationale")
_TASK_VALUES = ("find_lesson", "Task", "Instructions", "Goal", "easy", 120, "lesson_opened")


def test_the_digest_is_deterministic_and_changes_with_every_field():
    base = protocol_digest(_HEADER, [("SET-A", "Set", [_TASK_VALUES])])
    assert base == protocol_digest(_HEADER, [("SET-A", "Set", [_TASK_VALUES])])
    assert len(base) == 64 and set(base) <= set("0123456789abcdef")
    for index in range(len(_HEADER)):
        header = list(_HEADER)
        header[index] = "changed" if index != 3 else None
        assert protocol_digest(header, [("SET-A", "Set", [_TASK_VALUES])]) != base
    for index in range(len(_TASK_VALUES)):
        task = list(_TASK_VALUES)
        task[index] = 121 if index == 5 else "changed"
        assert protocol_digest(_HEADER, [("SET-A", "Set", [task])]) != base
    assert protocol_digest(_HEADER, [("SET-B", "Set", [_TASK_VALUES])]) != base
    assert protocol_digest(_HEADER, [("SET-A", "Other", [_TASK_VALUES])]) != base


def test_the_digest_covers_order_and_cannot_be_re_split():
    first = ("find_lesson", "One", "I", "G", "easy", 60, "lesson_opened")
    second = ("search", "Two", "I", "G", "hard", 90, "participant_declared")
    in_order = protocol_digest(_HEADER, [("SET-A", "Set", [first, second])])
    assert protocol_digest(_HEADER, [("SET-A", "Set", [second, first])]) != in_order
    # Two sets of one task are not one set of two tasks.
    assert protocol_digest(_HEADER, [("SET-A", "Set", [first]), ("SET-B", "Set", [second])]) \
        != protocol_digest(_HEADER, [("SET-A", "Set", [first, second])])
    # A missing rationale, an empty one and a shifted field all differ.
    assert protocol_digest(("VA-1", "Title", "version_a_collection", None), []) \
        != protocol_digest(("VA-1", "Title", "version_a_collection", ""), [])
    assert protocol_digest(("VA-1|", "Title", "version_a_collection", None), []) \
        != protocol_digest(("VA-1", "|Title", "version_a_collection", None), [])


def test_the_digest_uses_ordinals_so_order_gaps_do_not_change_it(app, people):
    researcher = people["researcher"]
    gapped = px.definition_row(researcher, version="VA-1")
    px.task_row(px.set_row(gapped, order=7), order=40)
    contiguous = px.definition_row(researcher, version="VA-1B")
    px.task_row(px.set_row(contiguous, order=0), order=0)
    header = lambda d: ("SAME", d.title, d.study_stage, d.equivalence_rationale)  # noqa: E731
    from app.services.experiment_protocol_review import task_values
    digests = [
        protocol_digest(header(d), [(s.set_code, s.title, [task_values(t) for t in tasks])
                                    for s, tasks in px._content(d.id)])
        for d in (gapped, contiguous)
    ]
    assert digests[0] == digests[1]
