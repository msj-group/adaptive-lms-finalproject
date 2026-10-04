"""Phase 6 replacement migrations (``69c4bae553fe`` and ``d574ab56594f``).

Isolated temporary SQLite databases only; nothing here connects to MySQL.

- A **fresh install** runs every revision from the base to the head.
- An **upgrade from the observed parent** (``b86838ce23db``) starts from the
  existing M02A probe, which holds representative Phase 5 history, M01
  consent rows and M02A protocol rows, with foreign keys enforced and
  ``PRAGMA foreign_key_check`` asserted after each step. Legacy participants
  that declined and withdrew are added, so the exclusion transfer is proved
  on real rows.
- The destructive revision's refusal, its reviewed disposition and its
  exclusion-state refusal (a missing link, a subject now included, or any
  basis other than ``legacy_collection_exclusion``) are each proved with
  nothing changed on refusal.
- Both revisions **refuse offline (``--sql``) rendering** of their
  data-dependent paths; the additive revision's pure DDL (``_create_tables``)
  is rendered for MySQL separately so its statements can still be reviewed.

SQLite proves nothing about InnoDB, MySQL collation or MySQL's
non-transactional DDL, and an offline rendering is not an execution.
"""

import io
import re

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

import tests.test_experiment_protocol_migration as m02a
import tests.test_payments_migration as m05
import tests.test_research_migration as m01
from app.extensions import db

_A = "69c4bae553fe"
_B = "d574ab56594f"
_PARENT = "b86838ce23db"

NEW_TABLES = [
    "research_subjects", "research_subject_links", "research_configurations",
    "research_sessions", "research_events", "research_feedback_prompts", "research_exports",
    "research_export_archives", "research_audit_events",
]
LEGACY_TABLES = ["research_consent_events", "research_participants",
                 "research_consent_documents", "experiment_tasks", "experiment_task_sets",
                 "experiment_definitions"]
_EARLIER_TABLES = m01._PHASE5_TABLES


def _load(revision, prefix):
    return m05._load(revision, prefix)


def _run(conn, module, direction):
    m01._run(conn, module, direction)


def _at_parent(name, populated=True, refusals=True):
    """A probe at ``b86838ce23db``: Phase 5 history, M01 consent rows and
    M02A protocol rows, plus a declined and a withdrawn participant."""
    tmp, engine, conn = m02a._probe(name)
    module, _ = m02a._load_migration()
    _run(conn, module, "upgrade")
    for statement in m02a._M02A_ROWS:
        conn.execute(sa.text(statement))
    if refusals:
        conn.execute(sa.text("INSERT INTO users (id, public_id, role, status) VALUES"
                             " (16, 'u-16', 'student', 'active')"))
        conn.execute(sa.text(
            m01._PARTICIPANT_INSERT
            + f"(2, 'p-2', 14, 'RP-BBBBBBBBBB', 'declined', 2, {m01._T1}, NULL, 11, 2,"
            f" {m01._T0}, {m01._T1}), (3, 'p-3', 16, 'RP-CCCCCCCCCC', 'withdrawn', 2,"
            f" {m01._T2}, {m01._T2}, 11, 3, {m01._T0}, {m01._T2})"))
        conn.execute(sa.text(
            m01._EVENT_INSERT
            + f"(2, 2, 2, 'declined', 14, 'v2.0', '{'b' * 64}', {m01._T1}),"
            f" (3, 3, 2, 'accepted', 16, 'v2.0', '{'b' * 64}', {m01._T1}),"
            f" (4, 3, 2, 'withdrawn', 16, 'v2.0', '{'b' * 64}', {m01._T2})"))
    if not populated:
        for table in LEGACY_TABLES:
            conn.execute(sa.text(f"DELETE FROM {table}"))
    conn.commit()
    return tmp, engine, conn


def _earlier(conn):
    return {table: m01._rows(conn, table) for table in _EARLIER_TABLES}


def _counts(conn, tables):
    return {t: conn.execute(sa.text(f"SELECT COUNT(*) FROM {t}")).scalar_one() for t in tables}


def _with_arguments(module, **arguments):
    module._x_arguments = lambda: dict(arguments)
    return module


def _model_shapes(app):
    with db.engine.connect() as model:
        return {table: m01._shape(model, table) for table in NEW_TABLES}


# ===========================================================================
# Identity, and what each revision does
# ===========================================================================


def test_two_linear_revisions_after_the_observed_parent():
    parents = {}
    for path in m05._MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        parents[revision] = re.search(r"^down_revision = (?:'([^']+)'|None)", source, re.M).group(1)
    assert parents[_A] == _PARENT and parents[_B] == _A
    assert set(parents) - {p for p in parents.values() if p is not None} == {_B}
    assert [r for r, p in parents.items() if p == _PARENT] == [_A]
    for module in (_load(_A, "p6a")[0], _load(_B, "p6b")[0]):
        assert module.branch_labels is None and module.depends_on is None


def test_the_additive_revision_creates_nine_tables_and_destroys_nothing():
    _, source = _load(_A, "p6a")
    code = m05._code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == NEW_TABLES
    upgrade = m05._function(code, "upgrade") + m05._function(code, "_create_tables")
    assert "_refuse_offline()" in m05._function(code, "upgrade")
    for forbidden in ("op.drop_table", "op.drop_column", "op.alter_column", "op.drop_index",
                      "op.drop_constraint", "DELETE FROM", "UPDATE ", "DROP TABLE",
                      "ondelete", "onupdate", "server_default", "mysql_engine",
                      "mysql_charset", "Enum(", "Float", "JSON", "sa.Text("):
        assert forbidden not in upgrade, forbidden
    for forbidden in ("'active'", "'invited'", "'included'", "accepted"):
        assert forbidden not in m05._function(code, "_transfer_legacy_exclusions"), forbidden


def test_every_model_check_is_declared_verbatim_by_the_migration(app):
    _, source = _load(_A, "p6a")
    flat = m01._flat(source)
    for table in NEW_TABLES:
        checks = {c.name: " ".join(str(c.sqltext).split())
                  for c in db.metadata.tables[table].constraints
                  if isinstance(c, sa.CheckConstraint)}
        assert m01._declared_checks(source, table) == set(checks), table
        for name, expression in checks.items():
            assert expression in flat, (name, expression)


def test_the_destructive_revision_drops_only_the_six_legacy_tables():
    _, source = _load(_B, "p6b")
    code = m05._code(source)
    upgrade = m05._function(code, "upgrade")
    assert "op.drop_table(table)" in upgrade and "op.create_table" not in upgrade
    module, _ = _load(_B, "p6b")
    assert list(module.LEGACY_TABLES) == LEGACY_TABLES


# ===========================================================================
# Fresh install
# ===========================================================================


def test_a_fresh_install_runs_the_whole_history_to_the_new_schema(app, tmp_path):
    config = Config()
    config.set_main_option("script_location", str(m05._MIGRATIONS.parent))
    revisions = list(reversed(list(ScriptDirectory.from_config(config).walk_revisions(
        "base", "heads"))))
    assert revisions[-1].revision == _B
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    conn = engine.connect()
    try:
        # Several historical revisions rebuild SQLite tables, which SQLite
        # allows only with foreign keys off; they are re-enabled and checked
        # at the end.
        conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
        conn.commit()
        for revision in revisions:
            with Operations.context(MigrationContext.configure(conn)):
                revision.module.upgrade()
            conn.commit()
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
        assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []
        tables = set(inspect(conn).get_table_names())
        assert tables == set(db.metadata.tables)
        assert not tables & set(LEGACY_TABLES)
        assert {t: m01._shape(conn, t) for t in NEW_TABLES} == _model_shapes(app)
    finally:
        conn.close()
        engine.dispose()


# ===========================================================================
# Upgrade from the observed parent
# ===========================================================================


def test_empty_legacy_tables_are_replaced_by_the_ordinary_upgrade(app):
    tmp, engine, conn = _at_parent("p6-empty.db", populated=False)
    try:
        before = _earlier(conn)
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        assert _counts(conn, NEW_TABLES) == {t: 0 for t in NEW_TABLES}
        _run(conn, _load(_B, "p6b")[0], "upgrade")
        tables = set(inspect(conn).get_table_names())
        assert not tables & set(LEGACY_TABLES)
        assert {t: m01._shape(conn, t) for t in NEW_TABLES} == _model_shapes(app)
        assert _earlier(conn) == before
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_refusals_and_withdrawals_become_exclusions_and_nothing_else(app):
    tmp, engine, conn = _at_parent("p6-transfer.db")
    try:
        legacy_before = {t: m01._rows(conn, t) for t in LEGACY_TABLES}
        before = _earlier(conn)
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        links = conn.execute(sa.text(
            "SELECT a.user_id, s.collection_status, s.status_basis, s.provenance,"
            " s.subject_code FROM research_subject_links a JOIN research_subjects s"
            " ON s.id = a.subject_id ORDER BY a.user_id")).fetchall()
        assert [tuple(row[:4]) for row in links] == [
            (14, "excluded", "legacy_collection_exclusion", "study"),
            (16, "excluded", "legacy_collection_exclusion", "study"),
        ]
        assert all(re.fullmatch(r"RS-[23456789A-Z]{10}", row[4]) for row in links)
        assert {row[4] for row in links}.isdisjoint({"RP-BBBBBBBBBB", "RP-CCCCCCCCCC"})
        # The legacy acceptance (user 13, 'active') is not carried over.
        audit = conn.execute(sa.text(
            "SELECT action, channel, actor_id, detail_code FROM research_audit_events")).fetchall()
        assert audit == [("subject_excluded", "migration", None,
                          "legacy_collection_exclusion")] * 2
        assert _counts(conn, ["research_configurations", "research_sessions",
                              "research_events", "research_feedback_prompts"]) == {
            "research_configurations": 0, "research_sessions": 0, "research_events": 0,
            "research_feedback_prompts": 0}
        # The additive revision touches no legacy or earlier row.
        assert {t: m01._rows(conn, t) for t in LEGACY_TABLES} == legacy_before
        assert _earlier(conn) == before
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_populated_legacy_tables_are_refused_by_default_with_counts_only(app):
    tmp, engine, conn = _at_parent("p6-refuse.db")
    try:
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        legacy = {t: m01._rows(conn, t) for t in LEGACY_TABLES}
        module = _with_arguments(_load(_B, "p6b")[0])
        with pytest.raises(RuntimeError) as refusal:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        conn.rollback()
        message = str(refusal.value)
        assert "Refusing to remove populated superseded research tables" in message
        assert "research_consent_documents=2" in message and "experiment_tasks=1" in message
        for leaked in ("Placeholder", "RP-", "p-1", "u-13", "v1.0", "wording", "@"):
            assert leaked not in message, leaked
        assert {t: m01._rows(conn, t) for t in LEGACY_TABLES} == legacy
        assert set(LEGACY_TABLES) <= set(inspect(conn).get_table_names())
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


@pytest.mark.parametrize("arguments", [
    {"legacy_research_disposition": "discard"},
    {"legacy_research_disposition": "keep", "legacy_research_reviewed_counts": "x"},
    {"legacy_research_disposition": "discard",
     "legacy_research_reviewed_counts": "experiment_definitions=0"},
])
def test_an_unreviewed_or_stale_disposition_is_refused(app, arguments):
    tmp, engine, conn = _at_parent("p6-stale.db")
    try:
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        module = _with_arguments(_load(_B, "p6b")[0], **arguments)
        with pytest.raises(RuntimeError, match="Refusing to remove populated"):
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        conn.rollback()
        assert set(LEGACY_TABLES) <= set(inspect(conn).get_table_names())
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_reviewed_disposition_removes_the_legacy_tables_and_keeps_the_rest(app):
    tmp, engine, conn = _at_parent("p6-discard.db")
    try:
        before = _earlier(conn)
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        exclusions = m01._rows(conn, "research_subjects")
        module, _ = _load(_B, "p6b")
        reviewed = module.counts_text(_counts(conn, LEGACY_TABLES))
        _with_arguments(module, legacy_research_disposition="discard",
                        legacy_research_reviewed_counts=reviewed)
        _run(conn, module, "upgrade")
        assert not set(inspect(conn).get_table_names()) & set(LEGACY_TABLES)
        assert m01._rows(conn, "research_subjects") == exclusions
        assert _earlier(conn) == before
        assert {t: m01._shape(conn, t) for t in NEW_TABLES} == _model_shapes(app)
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def _reviewed_b(conn):
    module, _ = _load(_B, "p6b")
    reviewed = module.counts_text(_counts(conn, LEGACY_TABLES))
    return _with_arguments(module, legacy_research_disposition="discard",
                           legacy_research_reviewed_counts=reviewed)


def test_a_missing_exclusion_refuses_even_a_reviewed_disposition(app):
    tmp, engine, conn = _at_parent("p6-missing.db")
    try:
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        conn.execute(sa.text("DELETE FROM research_audit_events"))
        conn.execute(sa.text("DELETE FROM research_subject_links WHERE user_id = 16"))
        conn.commit()
        module = _reviewed_b(conn)
        with pytest.raises(RuntimeError, match="1 legacy refusal or withdrawal"):
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        conn.rollback()
        assert set(LEGACY_TABLES) <= set(inspect(conn).get_table_names())
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


@pytest.mark.parametrize("status, basis", [
    ("included", "operator_reinstatement"),
    ("included", "population_rule"),
    ("excluded", "external_exclusion"),
])
def test_a_legacy_refusal_no_longer_excluded_on_its_basis_refuses_the_drop(app, status, basis):
    """The linked Student of a legacy withdrawal was changed after the
    transfer (here: to ``included``, or to another basis). The destructive
    revision must not pass: the legacy rows are the only evidence behind
    that exclusion. Nothing is dropped and no row changes."""
    tmp, engine, conn = _at_parent("p6-changed.db")
    try:
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        conn.execute(sa.text(
            "UPDATE research_subjects SET collection_status = :status, status_basis = :basis"
            " WHERE id = (SELECT subject_id FROM research_subject_links WHERE user_id = 16)"),
            {"status": status, "basis": basis})
        conn.commit()
        legacy = {t: m01._rows(conn, t) for t in LEGACY_TABLES}
        subjects = m01._rows(conn, "research_subjects")
        module = _reviewed_b(conn)
        with pytest.raises(RuntimeError) as refusal:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        conn.rollback()
        message = str(refusal.value)
        assert "1 legacy refusal or withdrawal row(s) are not excluded with basis" \
            " legacy_collection_exclusion" in message
        assert "Nothing was changed" in message and "RP-" not in message
        assert set(LEGACY_TABLES) <= set(inspect(conn).get_table_names())
        assert {t: m01._rows(conn, t) for t in LEGACY_TABLES} == legacy
        assert m01._rows(conn, "research_subjects") == subjects
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_an_old_acceptance_is_neither_checked_nor_reinterpreted(app):
    """User 13's legacy ``active`` row concerned one specific consent
    document. It gets no subject from the transfer, the destructive check
    ignores it, and the reviewed upgrade proceeds."""
    tmp, engine, conn = _at_parent("p6-active.db")
    try:
        assert conn.execute(sa.text(
            "SELECT status FROM research_participants WHERE student_id = 13")).scalar() == \
            "active"
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        assert conn.execute(sa.text(
            "SELECT COUNT(*) FROM research_subject_links WHERE user_id = 13")).scalar() == 0
        module = _reviewed_b(conn)
        _run(conn, module, "upgrade")
        assert not set(inspect(conn).get_table_names()) & set(LEGACY_TABLES)
        assert conn.execute(sa.text(
            "SELECT COUNT(*) FROM research_subject_links WHERE user_id = 13")).scalar() == 0
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_downgrades_restore_the_empty_legacy_schema_and_refuse_new_rows(app):
    tmp, engine, conn = _at_parent("p6-down.db", populated=False)
    try:
        before = _earlier(conn)
        tables_before = set(inspect(conn).get_table_names())
        legacy_shapes = {t: m01._shape(conn, t) for t in LEGACY_TABLES}
        module_a, _ = _load(_A, "p6a")
        module_b, _ = _load(_B, "p6b")
        _run(conn, module_a, "upgrade")
        _run(conn, module_b, "upgrade")
        _run(conn, module_b, "downgrade")
        assert {t: m01._shape(conn, t) for t in LEGACY_TABLES} == legacy_shapes
        assert _counts(conn, LEGACY_TABLES) == {t: 0 for t in LEGACY_TABLES}
        conn.execute(sa.text(
            "INSERT INTO research_audit_events (action, channel, detail_code, occurred_at)"
            " VALUES ('retention_purged', 'operator', 'days=1', '2026-01-01 00:00:00')"))
        conn.commit()
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            with Operations.context(MigrationContext.configure(conn)):
                module_a.downgrade()
        conn.rollback()
        conn.execute(sa.text("DELETE FROM research_audit_events"))
        conn.commit()
        _run(conn, module_a, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        assert _earlier(conn) == before
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_the_new_foreign_keys_have_no_referential_action_and_are_enforced(app):
    tmp, engine, conn = _at_parent("p6-fk.db")
    try:
        _run(conn, _load(_A, "p6a")[0], "upgrade")
        for statement in (
            "DELETE FROM users WHERE id = 14",
            "DELETE FROM research_subjects",
            "INSERT INTO research_subject_links (subject_id, user_id, created_at)"
            " VALUES (999, 13, '2026-01-01 00:00:00')",
        ):
            m01._refused(conn, statement, statement)
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# MySQL, rendered offline
# ===========================================================================


def _offline(module, function):
    buffer = io.StringIO()
    context = MigrationContext.configure(dialect_name="mysql",
                                         opts={"as_sql": True, "output_buffer": buffer})
    with Operations.context(context):
        getattr(module, function)()
    return buffer.getvalue()


def _offline_refused(module, direction):
    buffer = io.StringIO()
    context = MigrationContext.configure(dialect_name="mysql",
                                         opts={"as_sql": True, "output_buffer": buffer})
    with pytest.raises(RuntimeError) as refusal:
        with Operations.context(context):
            getattr(module, direction)()
    assert buffer.getvalue() == ""        # refused before emitting anything
    return str(refusal.value)


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_the_additive_revision_refuses_offline_rendering(app, direction):
    message = _offline_refused(_load(_A, "p6a")[0], direction)
    assert "cannot be rendered offline with --sql" in message
    assert "live database connection" in message


@pytest.mark.parametrize("arguments", [
    {},
    {"legacy_research_disposition": "discard", "legacy_research_reviewed_counts": "reviewed"},
])
def test_the_destructive_revision_refuses_offline_even_with_reviewed_arguments(app,
                                                                               arguments):
    module = _with_arguments(_load(_B, "p6b")[0], **arguments)
    message = _offline_refused(module, "upgrade")
    assert "Refusing to render the removal" in message and "live database" in message


def test_the_reviewed_mysql_ddl_of_the_additive_revision(app):
    """The pure DDL still renders for review, without the data step."""
    script = _offline(_load(_A, "p6a")[0], "_create_tables")
    positions = [script.index(f"CREATE TABLE {table} (") for table in NEW_TABLES]
    assert positions == sorted(positions) and script.count("CREATE TABLE") == 9
    for forbidden in ("DROP TABLE", "ALTER TABLE", "INSERT INTO", "DELETE FROM", "SELECT",
                      "ENGINE=", "ON DELETE", "ON UPDATE", "ENUM(", "JSON", " TEXT", "scope_"):
        assert forbidden not in script, forbidden
    flat = " ".join(script.split())
    for fragment in ("id BIGINT NOT NULL AUTO_INCREMENT", "event_uid VARCHAR(36) NOT NULL",
                     "occurred_at_ms BIGINT NOT NULL", "prompt_day DATE",
                     "is_collecting BOOL NOT NULL",
                     "CONSTRAINT uq_research_configurations_current UNIQUE (current_marker)",
                     "CONSTRAINT uq_research_feedback_prompts_session_slot UNIQUE"
                     " (session_id, display_slot)",
                     "timezone VARCHAR(64) NOT NULL", "oldest_last_seen_ms BIGINT,",
                     "content LONGBLOB NOT NULL", "archive_sha256 VARCHAR(64) NOT NULL",
                     "CONSTRAINT uq_research_export_archives_export_id UNIQUE (export_id)",
                     "FOREIGN KEY(export_id) REFERENCES research_exports (id)",
                     "status_basis IN ('population_rule', 'operator_reinstatement',"
                     " 'external_exclusion', 'legacy_collection_exclusion')"):
        assert fragment in flat, fragment
    assert "external_arrangement" not in flat and "subject_included" not in flat


def test_the_destructive_downgrade_is_plain_ddl_and_renders(app):
    script = _offline(_load(_B, "p6b")[0], "downgrade")
    assert script.count("CREATE TABLE") == len(LEGACY_TABLES)
    assert "SELECT" not in script and "DROP TABLE" not in script


# ===========================================================================
# The per-run arguments through the real Alembic environment
# ===========================================================================


def test_the_disposition_arguments_reach_the_revision_through_flask_migrate(tmp_path):
    """``flask db upgrade -x ...`` end to end: ``env.py``, Flask-Migrate's
    ``x_arg`` plumbing and the revision's own reading of it. A refusal exits
    non-zero with nothing changed; the reviewed arguments proceed."""
    from flask_migrate import upgrade

    from app import create_app

    tmp, engine, conn = _at_parent("p6-cli.db")
    try:
        database = engine.url.database
        conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL,"
                             " CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
        conn.execute(sa.text(f"INSERT INTO alembic_version VALUES ('{_PARENT}')"))
        conn.commit()
        conn.close()
        engine.dispose()
        cli_app = create_app("testing", SQLALCHEMY_DATABASE_URI=f"sqlite:///{database}")
        directory = str(m05._MIGRATIONS.parent)
        with cli_app.app_context():
            with pytest.raises(SystemExit) as refused:
                upgrade(directory=directory, revision=_B)
            assert refused.value.code == 1
            version = db.session.execute(sa.text("SELECT version_num FROM alembic_version"))
            assert version.scalar() == _A          # the additive step applied, then stopped
            module, _ = _load(_B, "p6b-cli")
            with db.engine.connect() as probe:
                reviewed = module.counts_text(_counts(probe, LEGACY_TABLES))
            upgrade(directory=directory, revision=_B, x_arg=[
                "legacy_research_disposition=discard",
                f"legacy_research_reviewed_counts={reviewed}",
            ])
            assert db.session.execute(sa.text("SELECT version_num FROM alembic_version")) \
                .scalar() == _B
            tables = set(inspect(db.engine).get_table_names())
            assert not tables & set(LEGACY_TABLES) and set(NEW_TABLES) <= tables
            db.session.remove()
            db.engine.dispose()
    finally:
        tmp.cleanup()
