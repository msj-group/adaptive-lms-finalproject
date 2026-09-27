"""Phase 6 / M02A migration checks (``b86838ce23db``).

The revision creates ``experiment_definitions``, ``experiment_task_sets``
and ``experiment_tasks``. It alters nothing, reads no existing row, and
seeds nothing -- in particular no protocol is created or activated.

The execution probes reuse M01's isolated temporary SQLite database, which
holds representative **Phase 5** history at ``b3d8f1a6c472``; M01's revision
runs forward with representative **M01** consent rows, and then this
revision runs forward and back with foreign keys **enforced** and ``PRAGMA
foreign_key_check`` asserted after each step. Every Phase 5 and M01 row is
compared before and after.

The MySQL checks render the revision's offline (``--sql``) MySQL script and
compile dialect DDL; neither connects to a database. The real upgrade of the
authorized development MySQL database is a separate, manually executed
check, and SQLite proves nothing about InnoDB.
"""

import io
import re

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

import tests.test_payments_migration as m05
import tests.test_research_migration as m01
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

_REVISION = "b86838ce23db"
_DOWN_REVISION = "f2a6d1c84b37"

_DEFINITIONS = "experiment_definitions"
_SETS = "experiment_task_sets"
_TASKS = "experiment_tasks"
_NEW_TABLES = [_DEFINITIONS, _SETS, _TASKS]
_MODELS = {_DEFINITIONS: ExperimentDefinition, _SETS: ExperimentTaskSet, _TASKS: ExperimentTask}

_EXPECTED = {
    _DEFINITIONS: {
        "columns": {"id", "public_id", "version_identifier", "title", "study_stage",
                    "equivalence_rationale", "status", "current_marker", "content_digest",
                    "derived_from_id", "version", "created_by_id", "activated_at",
                    "activated_by_id", "superseded_at", "superseded_by_id", "discarded_at",
                    "discarded_by_id", "created_at", "updated_at"},
        "nullable": {"equivalence_rationale", "current_marker", "content_digest",
                     "derived_from_id", "activated_at", "activated_by_id", "superseded_at",
                     "superseded_by_id", "discarded_at", "discarded_by_id"},
        "checks": {
            "ck_experiment_definitions_status_valid", "ck_experiment_definitions_stage_valid",
            "ck_experiment_definitions_version_positive",
            "ck_experiment_definitions_version_identifier_present",
            "ck_experiment_definitions_title_present",
            "ck_experiment_definitions_rationale_present",
            "ck_experiment_definitions_current_marker",
            "ck_experiment_definitions_activation_pair",
            "ck_experiment_definitions_supersession_pair",
            "ck_experiment_definitions_discard_pair",
            "ck_experiment_definitions_lifecycle_state",
            "ck_experiment_definitions_digest_format",
            "ck_experiment_definitions_timestamps_ordered",
        },
        "indexes": {
            "ix_experiment_definitions_status_id": ["status", "id"],
            "ix_experiment_definitions_created_by_id": ["created_by_id"],
            "ix_experiment_definitions_activated_by_id": ["activated_by_id"],
            "ix_experiment_definitions_superseded_by_id": ["superseded_by_id"],
            "ix_experiment_definitions_discarded_by_id": ["discarded_by_id"],
            "ix_experiment_definitions_derived_from_id": ["derived_from_id"],
        },
        "fks": {("derived_from_id", _DEFINITIONS), ("created_by_id", "users"),
                ("activated_by_id", "users"), ("superseded_by_id", "users"),
                ("discarded_by_id", "users")},
        "uniques": sorted([
            ("", ("public_id",)),
            ("uq_experiment_definitions_current", ("study_stage", "current_marker")),
            ("uq_experiment_definitions_version_identifier", ("version_identifier",)),
        ]),
    },
    _SETS: {
        "columns": {"id", "public_id", "definition_id", "set_code", "title", "display_order",
                    "created_at", "updated_at"},
        "nullable": set(),
        "checks": {"ck_experiment_task_sets_code_present",
                   "ck_experiment_task_sets_title_present",
                   "ck_experiment_task_sets_display_order_non_negative",
                   "ck_experiment_task_sets_timestamps_ordered"},
        "indexes": {"ix_experiment_task_sets_definition_order_id":
                    ["definition_id", "display_order", "id"]},
        "fks": {("definition_id", _DEFINITIONS)},
        "uniques": sorted([
            ("", ("public_id",)),
            ("uq_experiment_task_sets_definition_code", ("definition_id", "set_code")),
        ]),
    },
    _TASKS: {
        "columns": {"id", "public_id", "task_set_id", "display_order", "task_type", "title",
                    "participant_instructions", "expected_goal", "difficulty",
                    "recommended_duration_seconds", "completion_criterion", "created_at",
                    "updated_at"},
        "nullable": set(),
        "checks": {"ck_experiment_tasks_type_valid", "ck_experiment_tasks_difficulty_valid",
                   "ck_experiment_tasks_criterion_valid",
                   "ck_experiment_tasks_type_criterion_pair",
                   "ck_experiment_tasks_duration_range", "ck_experiment_tasks_title_present",
                   "ck_experiment_tasks_instructions_present",
                   "ck_experiment_tasks_goal_present",
                   "ck_experiment_tasks_display_order_non_negative",
                   "ck_experiment_tasks_timestamps_ordered"},
        "indexes": {"ix_experiment_tasks_set_order_id": ["task_set_id", "display_order", "id"]},
        "fks": {("task_set_id", _SETS)},
        "uniques": [("", ("public_id",))],
    },
}

_T0 = "'2026-08-01 09:00:00'"
_T1 = "'2026-08-02 09:00:00'"
_DIGEST = "e" * 64
_RESEARCHER = 15  # created by M01's probe

_DEFINITION_INSERT = (
    "INSERT INTO experiment_definitions (id, public_id, version_identifier, title, study_stage,"
    " equivalence_rationale, status, current_marker, content_digest, derived_from_id, version,"
    " created_by_id, activated_at, activated_by_id, superseded_at, superseded_by_id,"
    " discarded_at, discarded_by_id, created_at, updated_at) VALUES "
)
_SET_INSERT = (
    "INSERT INTO experiment_task_sets (id, public_id, definition_id, set_code, title,"
    " display_order, created_at, updated_at) VALUES "
)
_TASK_INSERT = (
    "INSERT INTO experiment_tasks (id, public_id, task_set_id, display_order, task_type, title,"
    " participant_instructions, expected_goal, difficulty, recommended_duration_seconds,"
    " completion_criterion, created_at, updated_at) VALUES "
)

#: A draft with one set and one task, and an active version derived from
#: nothing -- written in the order the application writes them.
_M02A_ROWS = [
    _DEFINITION_INSERT
    + f"(1, 'e-1', 'VA-1', 'Placeholder', 'version_a_collection', NULL, 'draft', NULL, NULL,"
    f" NULL, 3, {_RESEARCHER}, NULL, NULL, NULL, NULL, NULL, NULL, {_T0}, {_T0})",
    _SET_INSERT + f"(1, 's-1', 1, 'SET-A', 'Placeholder set', 0, {_T0}, {_T0})",
    _TASK_INSERT
    + f"(1, 't-1', 1, 0, 'find_lesson', 'Placeholder task', 'Placeholder instructions.',"
    f" 'Placeholder goal.', 'easy', 120, 'lesson_opened', {_T0}, {_T0})",
    _DEFINITION_INSERT
    + f"(2, 'e-2', 'VA-2', 'Placeholder', 'version_a_collection', NULL, 'active', 1,"
    f" '{_DIGEST}', 1, 2, {_RESEARCHER}, {_T1}, {_RESEARCHER}, NULL, NULL, NULL, NULL,"
    f" {_T0}, {_T1})",
]

_M01_TABLES = ("research_consent_documents", "research_participants", "research_consent_events")


def _load_migration():
    return m05._load(_REVISION, "p6m02a")


def _probe(name):
    """M01's Phase 5 probe, advanced through M01 with M01 history in it."""
    tmp, engine, conn = m01._probe(name)
    m01_module, _ = m01._load_migration()
    m01._run(conn, m01_module, "upgrade")
    for statement in m01._M01_ROWS:
        conn.execute(sa.text(statement))
    conn.commit()
    return tmp, engine, conn


def _earlier_rows(conn):
    return {table: m01._rows(conn, table)
            for table in m01._PHASE5_TABLES + _M01_TABLES}


# ===========================================================================
# Revision identity, and what the revision does
# ===========================================================================


def test_revision_identifiers_and_one_linear_head():
    module, _ = _load_migration()
    assert (module.revision, module.down_revision) == (_REVISION, _DOWN_REVISION)
    assert module.branch_labels is None and module.depends_on is None
    parents = {}
    for path in m05._MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        parents[revision] = re.search(
            r"^down_revision = (?:'([^']+)'|None)", source, re.M
        ).group(1)
    heads = set(parents) - {p for p in parents.values() if p is not None}
    assert heads == {_REVISION}
    assert [r for r, p in parents.items() if p == _DOWN_REVISION] == [_REVISION]
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_three_tables_and_alters_or_seeds_nothing():
    _, source = _load_migration()
    code = m05._code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _NEW_TABLES
    upgrade = m05._function(code, "upgrade")
    for forbidden in ("op.add_column", "op.drop_column", "op.alter_column",
                      "op.drop_table", "op.drop_index", "op.drop_constraint",
                      "op.bulk_insert", "op.execute", "INSERT", "UPDATE ", "DELETE FROM",
                      "server_default", "ondelete", "onupdate", "mysql_engine",
                      "mysql_charset", "Enum(", "Float", "JSON"):
        assert forbidden not in upgrade, forbidden
    # Matched at a word start: "supersession" is a lifecycle word, not a
    # session.
    for forbidden in ("research_participants", "participant_id", "student_id", "session",
                      "interaction", "rating", "survey", "lesson_id", "quiz_id",
                      "assignment_id"):
        assert not re.search(rf"\b{forbidden}", code), forbidden


def test_every_declared_check_is_exactly_the_models_check():
    _, source = _load_migration()
    flat = m01._flat(source)
    for table in _NEW_TABLES:
        checks = m01._model_checks(_MODELS[table])
        assert set(checks) == _EXPECTED[table]["checks"], table
        for name, expression in checks.items():
            assert f"name='{name}'" in source, name
            assert expression in flat, (name, expression)


def test_the_migrations_closed_sets_match_the_application_enums():
    _, source = _load_migration()
    flat = m01._flat(source)
    for column, enum in (
        ("status", ExperimentDefinitionStatus),
        ("study_stage", ExperimentStudyStage),
        ("task_type", ExperimentTaskType),
        ("difficulty", ExperimentTaskDifficulty),
        ("completion_criterion", ExperimentCompletionCriterion),
    ):
        expected = f"{column} IN (" + ", ".join(f"'{m.value}'" for m in enum) + ")"
        assert expected in flat, expected


def test_the_current_marker_check_is_null_safe():
    _, source = _load_migration()
    assert ("(status = 'active' AND current_marker IS NOT NULL AND current_marker = 1)"
            " OR (status <> 'active' AND current_marker IS NULL)") in m01._flat(source)


def test_the_model_and_migration_agree_on_every_column(app):
    _, source = _load_migration()
    inspector = inspect(db.engine)
    for table in _NEW_TABLES:
        block = m05._table_block(source, table)
        declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block))
        actual = {c["name"]: str(c["nullable"]) for c in inspector.get_columns(table)}
        declared.pop("id", None)
        actual.pop("id", None)
        assert declared == actual, table
        assert m01._shape(db.engine.connect(), table) == _EXPECTED[table], table


# ===========================================================================
# Execution against an isolated SQLite database with Phase 5 and M01 history
# ===========================================================================


def test_the_migration_applies_leaving_every_earlier_row_untouched():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("m02a-probe.db")
    try:
        before = _earlier_rows(conn)
        tables_before = set(inspect(conn).get_table_names())
        shapes_before = {t: m01._shape(conn, t) for t in m01._PHASE5_TABLES + _M01_TABLES}

        m01._run(conn, module, "upgrade")

        assert set(inspect(conn).get_table_names()) - tables_before == set(_NEW_TABLES)
        for table in _NEW_TABLES:
            assert m01._shape(conn, table) == _EXPECTED[table], table
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert _earlier_rows(conn) == before
        assert {t: m01._shape(conn, t) for t in m01._PHASE5_TABLES + _M01_TABLES} \
            == shapes_before
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_new_constraints_accept_real_rows_and_refuse_broken_ones():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("m02a-constraints.db")
    try:
        m01._run(conn, module, "upgrade")
        for statement in _M02A_ROWS:
            conn.execute(sa.text(statement))
        conn.commit()
        for statement, rule in (
            (_DEFINITION_INSERT + f"(9, 'e-9', NULL, 'T', 'version_a_collection', NULL,"
             f" 'draft', NULL, NULL, NULL, 1, {_RESEARCHER}, NULL, NULL, NULL, NULL, NULL,"
             f" NULL, {_T0}, {_T0})", "version_identifier NOT NULL"),
            (_DEFINITION_INSERT + f"(9, 'e-9', 'VA-9', NULL, 'version_a_collection', NULL,"
             f" 'draft', NULL, NULL, NULL, 1, {_RESEARCHER}, NULL, NULL, NULL, NULL, NULL,"
             f" NULL, {_T0}, {_T0})", "title NOT NULL"),
            (_DEFINITION_INSERT + f"(9, 'e-9', 'VA-9', 'T', 'version_a_collection', NULL,"
             f" 'draft', NULL, NULL, NULL, NULL, {_RESEARCHER}, NULL, NULL, NULL, NULL, NULL,"
             f" NULL, {_T0}, {_T0})", "version NOT NULL"),
            (_DEFINITION_INSERT + f"(9, 'e-9', 'VA-9', 'T', 'version_a_collection', NULL,"
             f" 'active', 1, '{'f' * 64}', NULL, 1, {_RESEARCHER}, {_T1}, {_RESEARCHER},"
             f" NULL, NULL, NULL, NULL, {_T0}, {_T1})", "uq_experiment_definitions_current"),
            (_DEFINITION_INSERT + f"(9, 'e-9', 'VA-1', 'T', 'version_a_collection', NULL,"
             f" 'draft', NULL, NULL, NULL, 1, {_RESEARCHER}, NULL, NULL, NULL, NULL, NULL,"
             f" NULL, {_T0}, {_T0})", "uq_experiment_definitions_version_identifier"),
            (_DEFINITION_INSERT + f"(9, 'e-9', 'VA-9', 'T', 'ab_evaluation', NULL,"
             f" 'draft', NULL, NULL, NULL, 1, {_RESEARCHER}, NULL, NULL, NULL, NULL, NULL,"
             f" NULL, {_T0}, {_T0})", "the study stage CHECK"),
            (_DEFINITION_INSERT + f"(9, 'e-9', 'VA-9', 'T', 'version_a_collection', NULL,"
             f" 'draft', NULL, NULL, 99, 1, {_RESEARCHER}, NULL, NULL, NULL, NULL, NULL,"
             f" NULL, {_T0}, {_T0})", "the lineage foreign key"),
            (_DEFINITION_INSERT + f"(9, 'e-9', 'VA-9', 'T', 'version_a_collection', NULL,"
             f" 'draft', NULL, NULL, NULL, 1, 99, NULL, NULL, NULL, NULL, NULL,"
             f" NULL, {_T0}, {_T0})", "the creator foreign key"),
            (_SET_INSERT + f"(9, 's-9', 99, 'SET-B', 'T', 0, {_T0}, {_T0})",
             "the task set definition foreign key"),
            (_SET_INSERT + f"(9, 's-9', 1, 'SET-A', 'T', 1, {_T0}, {_T0})",
             "uq_experiment_task_sets_definition_code"),
            (_SET_INSERT + f"(9, 's-9', 1, NULL, 'T', 1, {_T0}, {_T0})", "set_code NOT NULL"),
            (_TASK_INSERT + f"(9, 't-9', 99, 0, 'search', 'T', 'I', 'G', 'easy', 60,"
             f" 'lesson_opened', {_T0}, {_T0})", "the task set foreign key"),
            (_TASK_INSERT + f"(9, 't-9', 1, 0, 'quiz_completion', 'T', 'I', 'G', 'easy', 60,"
             f" 'lesson_opened', {_T0}, {_T0})", "the type and criterion pair CHECK"),
            (_TASK_INSERT + f"(9, 't-9', 1, 0, 'login', 'T', 'I', 'G', 'easy', 60,"
             f" 'participant_declared', {_T0}, {_T0})", "the task type CHECK"),
            (_TASK_INSERT + f"(9, 't-9', 1, 0, 'search', 'T', 'I', 'G', 'easy', 1801,"
             f" 'participant_declared', {_T0}, {_T0})", "the duration CHECK"),
            (_TASK_INSERT + f"(9, 't-9', 1, 0, 'search', 'T', NULL, 'G', 'easy', 60,"
             f" 'participant_declared', {_T0}, {_T0})", "instructions NOT NULL"),
            (f"DELETE FROM users WHERE id = {_RESEARCHER}", "no cascade from an actor"),
            ("DELETE FROM experiment_definitions WHERE id = 1", "no cascade from a definition"),
            ("DELETE FROM experiment_task_sets WHERE id = 1", "no cascade from a task set"),
        ):
            m01._refused(conn, statement, rule)
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_downgrade_refuses_while_protocol_rows_exist_then_reverses_cleanly():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("m02a-downgrade.db")
    try:
        before = _earlier_rows(conn)
        tables_before = set(inspect(conn).get_table_names())
        m01._run(conn, module, "upgrade")
        for statement in _M02A_ROWS:
            conn.execute(sa.text(statement))
        conn.commit()
        with_rows = {t: m01._rows(conn, t) for t in _NEW_TABLES}

        # It refuses before touching anything, for every kind of row.
        for table, clear in ((_TASKS, "DELETE FROM experiment_tasks"),
                             (_SETS, "DELETE FROM experiment_task_sets"),
                             (_DEFINITIONS, "DELETE FROM experiment_definitions WHERE id = 2")):
            with pytest.raises(RuntimeError, match="Refusing to downgrade"):
                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
            conn.rollback()
            assert {t: m01._rows(conn, t) for t in _NEW_TABLES} == with_rows
            assert set(inspect(conn).get_table_names()) - tables_before == set(_NEW_TABLES)
            conn.execute(sa.text(clear))
            if table == _DEFINITIONS:
                conn.execute(sa.text("DELETE FROM experiment_definitions"))
            conn.commit()
            with_rows = {t: m01._rows(conn, t) for t in _NEW_TABLES}

        m01._run(conn, module, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        assert _earlier_rows(conn) == before
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_shape():
    module, _ = _load_migration()
    tmp, engine, conn = _probe("m02a-again.db")
    try:
        before = _earlier_rows(conn)
        shapes = []
        for _ in range(2):
            m01._run(conn, module, "upgrade")
            shapes.append({t: m01._shape(conn, t) for t in _NEW_TABLES})
            m01._run(conn, module, "downgrade")
            assert _earlier_rows(conn) == before
        assert shapes[0] == shapes[1]
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# MySQL DDL, compiled and rendered offline
# ===========================================================================

_DDL_FRAGMENTS = {
    _DEFINITIONS: (
        "id BIGINT NOT NULL AUTO_INCREMENT", "public_id VARCHAR(36) NOT NULL",
        "version_identifier VARCHAR(40) NOT NULL", "title VARCHAR(200) NOT NULL",
        "study_stage VARCHAR(32) NOT NULL", "equivalence_rationale TEXT,",
        "status VARCHAR(32) NOT NULL", "current_marker SMALLINT,",
        "content_digest VARCHAR(64),", "derived_from_id BIGINT,",
        "version INTEGER NOT NULL", "created_by_id BIGINT NOT NULL",
        "created_at DATETIME NOT NULL", "updated_at DATETIME NOT NULL",
        "FOREIGN KEY(derived_from_id) REFERENCES experiment_definitions (id)",
        "FOREIGN KEY(created_by_id) REFERENCES users (id)",
        "CONSTRAINT uq_experiment_definitions_current UNIQUE (study_stage, current_marker)",
        "UNIQUE (public_id)",
    ),
    _SETS: (
        "id BIGINT NOT NULL AUTO_INCREMENT", "definition_id BIGINT NOT NULL",
        "set_code VARCHAR(16) NOT NULL", "title VARCHAR(150) NOT NULL",
        "display_order INTEGER NOT NULL",
        "FOREIGN KEY(definition_id) REFERENCES experiment_definitions (id)",
        "CONSTRAINT uq_experiment_task_sets_definition_code UNIQUE (definition_id, set_code)",
    ),
    _TASKS: (
        "id BIGINT NOT NULL AUTO_INCREMENT", "task_set_id BIGINT NOT NULL",
        "display_order INTEGER NOT NULL", "task_type VARCHAR(40) NOT NULL",
        "title VARCHAR(150) NOT NULL", "participant_instructions TEXT NOT NULL",
        "expected_goal TEXT NOT NULL", "difficulty VARCHAR(16) NOT NULL",
        "recommended_duration_seconds INTEGER NOT NULL",
        "completion_criterion VARCHAR(40) NOT NULL",
        "FOREIGN KEY(task_set_id) REFERENCES experiment_task_sets (id)",
    ),
}


@pytest.mark.parametrize("table", _NEW_TABLES)
def test_the_mysql_ddl_for_each_table(table):
    ddl = str(CreateTable(_MODELS[table].__table__).compile(dialect=mysql.dialect()))
    normalized = " ".join(ddl.split())
    for fragment in _DDL_FRAGMENTS[table]:
        assert fragment in normalized, (table, fragment)
    for forbidden in ("ENGINE=", "CHARSET=", "ON DELETE", "ON UPDATE", "ENUM(", "JSON"):
        assert forbidden not in normalized, (table, forbidden)


def _offline(direction):
    module, _ = _load_migration()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        getattr(module, direction)()
    return buffer.getvalue()


def test_the_offline_mysql_script_creates_the_three_tables_and_nothing_else():
    script = _offline("upgrade")
    positions = [script.index(f"CREATE TABLE {table}") for table in _NEW_TABLES]
    assert positions == sorted(positions)
    assert script.count("CREATE TABLE") == 3
    assert script.count("CREATE INDEX") == 8
    for forbidden in ("DROP TABLE", "ALTER TABLE", "INSERT INTO", "UPDATE ", "DELETE FROM",
                      "ENGINE=", "ON DELETE", "ON UPDATE", "SELECT"):
        assert forbidden not in script, forbidden


def test_the_offline_mysql_downgrade_drops_child_before_parent_without_counting():
    script = _offline("downgrade")
    assert [line for line in script.splitlines() if line.startswith("DROP TABLE")] == [
        f"DROP TABLE {_TASKS};", f"DROP TABLE {_SETS};", f"DROP TABLE {_DEFINITIONS};"
    ]
    assert "SELECT" not in script
