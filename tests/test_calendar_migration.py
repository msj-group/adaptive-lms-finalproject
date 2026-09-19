"""M10 migration checks for ``calendar_events``.

The execution probe uses an isolated temporary SQLite database seeded
with the prerequisite tables and representative existing rows, so the
"nothing existing is touched and nothing is seeded" claims are executed
rather than asserted. Both directions are run, foreign keys stay
**enforced** throughout, and ``PRAGMA foreign_key_check`` is asserted
after each direction.

MySQL checks compile dialect DDL only and never connect to the real
application database; the real upgrade / downgrade / upgrade round trip
against the authorized development MySQL database is a separate,
manually executed check.
"""
import importlib.util
import os
import pathlib
import re
import tempfile

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from app.extensions import db
from app.models import CalendarEvent, CalendarEventStatus

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "e5b83c7d1a49"
_DOWN_REVISION = "c7a91f4b2d68"

_NEW_TABLES = ["calendar_events"]

_EXPECTED_COLUMNS = {
    "id", "public_id", "created_by_id", "title", "details", "event_date",
    "start_time", "end_time", "location", "status", "cancelled_at", "version",
    "created_at", "updated_at",
}

_EXPECTED_CHECKS = {
    "ck_calendar_events_status_valid",
    "ck_calendar_events_time_shape",
    "ck_calendar_events_cancelled_state",
    "ck_calendar_events_version_positive",
}

_EXPECTED_INDEXES = {
    "ix_calendar_events_status_date_id",
    "ix_calendar_events_date_id",
    "ix_calendar_events_created_by_date_id",
}


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m10_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def _flat(source):
    """The source as one whitespace-normalised line, with adjacent string
    literals collapsed.

    A long CHECK expression is written in the migration as two adjacent
    Python string literals, which is one SQL expression but two pieces of
    text; collapsing the ``" "`` between them is what lets a single
    expression be compared against it.
    """
    return " ".join(source.split()).replace('" "', "")


def _code(source):
    """The migration's executable body, without its module docstring.

    The docstring legitimately *names* the operations this revision does
    not perform, so scanning the whole file for those words would fail on
    the explanation rather than on any code.
    """
    return source.split("from alembic import op", 1)[1]


# ===========================================================================
# Revision identity and one linear head
# ===========================================================================


def test_revision_identifiers_and_one_linear_head():
    module, _ = _load_migration()
    assert module.revision == _REVISION
    assert module.down_revision == _DOWN_REVISION
    assert module.branch_labels is None
    assert module.depends_on is None

    parents, revisions = {}, set()
    for path in _MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision = '([^']+)'", source, re.M).group(1)
        down = re.search(r"^down_revision = (?:'([^']+)'|None)", source, re.M).group(1)
        revisions.add(revision)
        parents[revision] = down
    # Exactly one head. Phase 4 / M11, M12 and then M13 follow this revision,
    # so the single head is now Phase 5 / M10's rather than this one.
    assert revisions - {p for p in parents.values() if p is not None} == {
        "b3d8f1a6c472"
    }
    # No revision is claimed as the parent of two others (no branch).
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    # Exactly one root.
    assert len([r for r, p in parents.items() if p is None]) == 1


# ===========================================================================
# What the revision does, and what it must not do
# ===========================================================================


def test_the_revision_creates_exactly_one_table_and_touches_nothing_existing():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _NEW_TABLES
    # No existing object is altered anywhere, and no row is written.
    for forbidden in (
        "add_column",
        "drop_column",
        "alter_column",
        "op.bulk_insert",
        "op.execute",
        "batch_alter_table",
        "drop_constraint",
        "create_check_constraint",
    ):
        assert forbidden not in code, forbidden
    assert "ondelete" not in code
    assert "onupdate" not in code
    # The upgrade drops nothing.
    upgrade = code.split("def downgrade():")[0]
    assert "drop_table" not in upgrade
    assert "drop_index" not in upgrade
    # The only foreign-key target is the one existing parent.
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == ["users.id"]


def test_the_revision_declares_every_expected_column_constraint_and_index():
    _, source = _load_migration()
    block = source.split("op.create_table('calendar_events',", 1)[1].split("\n    )", 1)[0]
    assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == _EXPECTED_COLUMNS
    for fragment in (
        "name='ck_calendar_events_status_valid'",
        "name='ck_calendar_events_time_shape'",
        "name='ck_calendar_events_cancelled_state'",
        "name='ck_calendar_events_version_positive'",
        "sa.UniqueConstraint('public_id')",
        "'ix_calendar_events_status_date_id'",
        "'ix_calendar_events_date_id'",
        "'ix_calendar_events_created_by_date_id'",
        "status IN ('scheduled', 'cancelled')",
        "version > 0",
        "sa.String(length=150)",
        "sa.String(length=5000)",
        "sa.String(length=255)",
        "sa.Date()",
        "sa.Time()",
    ):
        assert fragment in source, fragment
    # The two multi-line CHECK expressions, compared as one flat SQL line.
    flat = _flat(source)
    for expression in (
        "(start_time IS NULL AND end_time IS NULL)"
        " OR (start_time IS NOT NULL AND end_time IS NOT NULL"
        " AND start_time < end_time)",
        "(status = 'scheduled' AND cancelled_at IS NULL)"
        " OR (status = 'cancelled' AND cancelled_at IS NOT NULL)",
    ):
        assert expression in flat, expression


def test_the_migrations_status_list_matches_the_application_enum():
    """The two values the migration writes into the CHECK are exactly the
    members of the application enum -- written out in the revision rather
    than imported, so the migration keeps describing the schema it
    produced, but verified here so the two cannot drift silently."""
    _, source = _load_migration()
    expected = (
        "status IN ("
        + ", ".join(f"'{status.value}'" for status in CalendarEventStatus)
        + ")"
    )
    assert expected in source


def test_the_revision_stores_no_second_copy_of_any_existing_date():
    """The central M10 decision, as something this file can be checked
    for: no occurrence table, no denormalized deadline, no calendar-row
    table, and no column added to any existing table."""
    _, source = _load_migration()
    code = _code(source)
    for forbidden in (
        "calendar_rows",
        "schedule_occurrences",
        "calendar_occurrences",
        "occurrences",
        "opens_at",
        "due_at",
        "closes_at",
        "schedule_id",
        "assignment_id",
        "quiz_id",
        "group_id",
        "course_id",
    ):
        assert forbidden not in code, forbidden


def test_the_downgrade_drops_the_table_and_nothing_else():
    _, source = _load_migration()
    downgrade = source.split("def downgrade():")[1]
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == _NEW_TABLES
    assert "drop_column" not in downgrade
    assert "op.execute" not in downgrade
    # Tables only, deliberately: ix_calendar_events_created_by_date_id
    # leads with a foreign-key column, and MySQL refuses to drop such an
    # index while the constraint exists (errno 1553).
    assert "op.drop_index" not in downgrade


# ===========================================================================
# Execution -- both directions, on an isolated SQLite database
# ===========================================================================

_PREREQ = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, public_id VARCHAR(36) NOT NULL UNIQUE,"
    " role VARCHAR(32), status VARCHAR(32))",
    "CREATE TABLE academic_terms (id INTEGER PRIMARY KEY, name VARCHAR(120),"
    " status VARCHAR(32))",
    "CREATE TABLE levels (id INTEGER PRIMARY KEY, name VARCHAR(120), status VARCHAR(32))",
    """CREATE TABLE courses (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        level_id INTEGER NOT NULL,
        title VARCHAR(150) NOT NULL,
        status VARCHAR(32) NOT NULL,
        FOREIGN KEY(level_id) REFERENCES levels (id)
    )""",
    """CREATE TABLE groups (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        academic_term_id INTEGER NOT NULL,
        course_id INTEGER NOT NULL,
        name VARCHAR(100) NOT NULL,
        status VARCHAR(32) NOT NULL,
        FOREIGN KEY(academic_term_id) REFERENCES academic_terms (id),
        FOREIGN KEY(course_id) REFERENCES courses (id)
    )""",
    """CREATE TABLE schedules (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        group_id INTEGER NOT NULL,
        day_of_week INTEGER NOT NULL,
        start_time TIME NOT NULL,
        end_time TIME NOT NULL,
        effective_start_date DATE NOT NULL,
        effective_end_date DATE NOT NULL,
        status VARCHAR(32) NOT NULL,
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    "INSERT INTO users (id, public_id, role, status)"
    " VALUES (11, 'up-11', 'student', 'active')",
    "INSERT INTO users (id, public_id, role, status)"
    " VALUES (13, 'up-13', 'administrator', 'active')",
    "INSERT INTO academic_terms (id, name, status) VALUES (1, 'Term 1', 'active')",
    "INSERT INTO levels (id, name, status) VALUES (1, 'Level 1', 'active')",
    "INSERT INTO courses (id, public_id, level_id, title, status)"
    " VALUES (2, 'course-public-1', 1, 'English', 'active')",
    "INSERT INTO groups (id, public_id, academic_term_id, course_id, name, status)"
    " VALUES (7, 'group-public-1', 1, 2, 'Group A', 'active')",
    "INSERT INTO schedules (id, public_id, group_id, day_of_week, start_time, end_time,"
    " effective_start_date, effective_end_date, status)"
    " VALUES (3, 'sched-3', 7, 0, '09:00:00', '11:00:00', '2026-05-01', '2026-06-30',"
    " 'active')",
]

_PRESERVED = (
    ("users", 2),
    ("academic_terms", 1),
    ("levels", 1),
    ("courses", 1),
    ("groups", 1),
    ("schedules", 1),
)

_EVENT_COLUMNS = (
    "id, public_id, created_by_id, title, details, event_date, start_time, end_time,"
    " location, status, cancelled_at, version, created_at, updated_at"
)
_MOMENT = "'2026-05-13 09:00:00'"
_LATER = "'2026-05-14 10:00:00'"


def _counts(conn):
    return {
        table: conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table, _ in _PRESERVED
    }


def _schedule_snapshot(conn):
    return conn.execute(
        sa.text(
            "SELECT id, public_id, group_id, day_of_week, start_time, end_time,"
            " effective_start_date, effective_end_date, status FROM schedules ORDER BY id"
        )
    ).fetchall()


def test_migration_applies_and_reverses_on_isolated_sqlite():
    module, _ = _load_migration()
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "probe.db").replace(os.sep, "/")
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("PRAGMA foreign_keys=ON"))
                for statement in _PREREQ:
                    conn.execute(sa.text(statement))
                conn.commit()

                before = _counts(conn)
                schedules_before = _schedule_snapshot(conn)
                tables_before = set(inspect(conn).get_table_names())

                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.commit()
                assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []

                schema = inspect(conn)
                assert "calendar_events" in set(schema.get_table_names())
                # Exactly one new table, and nothing existing removed.
                assert set(schema.get_table_names()) - tables_before == {
                    "calendar_events"
                }
                assert {
                    c["name"] for c in schema.get_columns("calendar_events")
                } == _EXPECTED_COLUMNS
                assert {
                    c["name"] for c in schema.get_check_constraints("calendar_events")
                } == _EXPECTED_CHECKS
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("calendar_events")
                } == {"created_by_id": "users"}
                assert {
                    index["name"] for index in schema.get_indexes("calendar_events")
                } >= _EXPECTED_INDEXES

                # Nothing existing was rewritten, and no row was invented.
                assert _counts(conn) == before == dict(_PRESERVED)
                assert _schedule_snapshot(conn) == schedules_before
                assert conn.execute(
                    sa.text("SELECT count(*) FROM calendar_events")
                ).scalar_one() == 0

                # One legitimate all-day row, and one timed row.
                conn.execute(sa.text(
                    f"INSERT INTO calendar_events ({_EVENT_COLUMNS}) VALUES"
                    f" (1, 'e-1', 13, 'Holiday', 'Closed.', '2026-05-25', NULL, NULL,"
                    f" 'Center', 'scheduled', NULL, 1, {_MOMENT}, {_MOMENT})"
                ))
                conn.execute(sa.text(
                    f"INSERT INTO calendar_events ({_EVENT_COLUMNS}) VALUES"
                    f" (2, 'e-2', 13, 'Meeting', NULL, '2026-05-20', '14:00:00',"
                    f" '15:30:00', NULL, 'scheduled', NULL, 1, {_MOMENT}, {_MOMENT})"
                ))
                conn.commit()

                # Every rule is really enforced.
                for values, rule in (
                    ("(3, 'e-1', 13, 'X', NULL, '2026-05-20', NULL, NULL, NULL,"
                     " 'scheduled', NULL, 1,", "public_id uniqueness"),
                    ("(4, 'e-4', 13, 'X', NULL, '2026-05-20', NULL, NULL, NULL,"
                     " 'draft', NULL, 1,", "status CHECK"),
                    ("(5, 'e-5', 13, 'X', NULL, '2026-05-20', '09:00:00', NULL, NULL,"
                     " 'scheduled', NULL, 1,", "a start with no end"),
                    ("(6, 'e-6', 13, 'X', NULL, '2026-05-20', NULL, '11:00:00', NULL,"
                     " 'scheduled', NULL, 1,", "an end with no start"),
                    ("(7, 'e-7', 13, 'X', NULL, '2026-05-20', '11:00:00', '09:00:00',"
                     " NULL, 'scheduled', NULL, 1,", "reversed times"),
                    ("(8, 'e-8', 13, 'X', NULL, '2026-05-20', '09:00:00', '09:00:00',"
                     " NULL, 'scheduled', NULL, 1,", "zero-length window"),
                    (f"(9, 'e-9', 13, 'X', NULL, '2026-05-20', NULL, NULL, NULL,"
                     f" 'scheduled', {_LATER}, 1,", "a scheduled row with a cancellation"),
                    ("(10, 'e-10', 13, 'X', NULL, '2026-05-20', NULL, NULL, NULL,"
                     " 'cancelled', NULL, 1,", "a cancelled row with no cancellation"),
                    ("(11, 'e-11', 13, 'X', NULL, '2026-05-20', NULL, NULL, NULL,"
                     " 'scheduled', NULL, 0,", "version > 0"),
                    ("(12, 'e-12', 99, 'X', NULL, '2026-05-20', NULL, NULL, NULL,"
                     " 'scheduled', NULL, 1,", "the created_by foreign key"),
                ):
                    try:
                        conn.execute(sa.text(
                            f"INSERT INTO calendar_events ({_EVENT_COLUMNS})"
                            f" VALUES {values} {_MOMENT}, {_MOMENT})"
                        ))
                        raise AssertionError(f"{rule} was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                # A cancelled row with a cancellation moment is legal, and
                # so is a one-minute window.
                for index, values in enumerate(
                    (
                        f"'X', NULL, '2026-05-20', NULL, NULL, NULL, 'cancelled',"
                        f" {_LATER}, 2",
                        "'X', NULL, '2026-05-20', '09:00:00', '09:01:00', NULL,"
                        " 'scheduled', NULL, 1",
                    ),
                    start=20,
                ):
                    conn.execute(sa.text(
                        f"INSERT INTO calendar_events ({_EVENT_COLUMNS}) VALUES"
                        f" ({index}, 'e-ok-{index}', 13, {values}, {_MOMENT},"
                        f" {_MOMENT})"
                    ))
                conn.commit()

                # No cascade: the creator cannot be deleted out from under
                # an event.
                try:
                    conn.execute(sa.text("DELETE FROM users WHERE id = 13"))
                    raise AssertionError("no-cascade was not enforced")
                except sa.exc.IntegrityError:
                    conn.rollback()

                # Leave no probe row behind.
                conn.execute(sa.text("DELETE FROM calendar_events"))
                conn.commit()

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()
                assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []

                after_down = set(inspect(conn).get_table_names())
                assert "calendar_events" not in after_down
                assert after_down == tables_before
                # Every pre-existing row survives the whole round trip.
                assert _counts(conn) == dict(_PRESERVED)
                assert _schedule_snapshot(conn) == schedules_before
        finally:
            engine.dispose()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_shape():
    """The round trip is really a round trip: upgrade, downgrade, upgrade
    leaves the same columns, checks, foreign key and indexes."""
    module, _ = _load_migration()
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "probe2.db").replace(os.sep, "/")
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("PRAGMA foreign_keys=ON"))
                for statement in _PREREQ:
                    conn.execute(sa.text(statement))
                conn.commit()

                shapes = []
                for _ in range(2):
                    with Operations.context(MigrationContext.configure(conn)):
                        module.upgrade()
                    conn.commit()
                    schema = inspect(conn)
                    shapes.append(
                        (
                            tuple(
                                sorted(
                                    c["name"]
                                    for c in schema.get_columns("calendar_events")
                                )
                            ),
                            tuple(
                                sorted(
                                    c["name"]
                                    for c in schema.get_check_constraints(
                                        "calendar_events"
                                    )
                                )
                            ),
                            tuple(
                                sorted(
                                    index["name"]
                                    for index in schema.get_indexes("calendar_events")
                                )
                            ),
                        )
                    )
                    with Operations.context(MigrationContext.configure(conn)):
                        module.downgrade()
                    conn.commit()
                assert shapes[0] == shapes[1]
                assert "calendar_events" not in set(inspect(conn).get_table_names())
        finally:
            engine.dispose()


# ===========================================================================
# Model / migration agreement, and MySQL DDL
# ===========================================================================


def test_models_and_migration_agree_on_the_new_table(app):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        block = source.split("op.create_table('calendar_events',", 1)[1].split(
            "\n    )", 1
        )[0]
        declared = dict(
            re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block)
        )
        actual = {
            column["name"]: str(column["nullable"])
            for column in inspector.get_columns("calendar_events")
        }
        declared.pop("id", None)
        actual.pop("id", None)
        assert declared == actual


def test_the_models_check_expressions_match_the_migrations(app):
    """The four CHECK expressions the model declares are the four the
    migration writes -- compared as normalised SQL text, so a silent
    divergence in either direction is caught."""
    module, source = _load_migration()
    with app.app_context():
        model_checks = {
            constraint.name: " ".join(str(constraint.sqltext).split())
            for constraint in CalendarEvent.__table__.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }
    assert set(model_checks) == _EXPECTED_CHECKS
    flat = _flat(source)
    for expression in model_checks.values():
        assert expression in flat, expression


def test_mysql_ddl_compiles_without_a_connection():
    dialect = mysql.dialect()
    ddl = str(CreateTable(CalendarEvent.__table__).compile(dialect=dialect))

    assert "FOREIGN KEY(created_by_id) REFERENCES users (id)" in ddl
    assert "UNIQUE (public_id)" in ddl
    assert "title VARCHAR(150) NOT NULL" in ddl
    assert "details VARCHAR(5000)" in ddl
    assert "location VARCHAR(255)" in ddl
    assert "status VARCHAR(32) NOT NULL" in ddl
    assert "event_date DATE NOT NULL" in ddl
    assert "start_time TIME" in ddl
    assert "end_time TIME" in ddl
    for name in _EXPECTED_CHECKS:
        assert name in ddl, name
    # No cascade, and no fractional-precision DATETIME / TIME.
    assert "ON DELETE" not in ddl
    assert "ON UPDATE" not in ddl
    assert "BIGINT" in ddl
    assert "DATETIME(" not in ddl
    assert "TIME(" not in ddl
    # No MySQL ENUM column: the closed set is VARCHAR + CHECK, the
    # convention every other closed-set column in this project uses.
    assert "ENUM(" not in ddl
    # A calendar event carries no number that could look like a grade.
    assert "FLOAT" not in ddl.upper()
    assert "DOUBLE" not in ddl.upper()
    assert "NUMERIC" not in ddl.upper()
    assert "DECIMAL" not in ddl.upper()


def test_the_new_table_uses_the_deployment_engine_and_charset(app):
    """The engine and charset are a **deployment** property, not
    something this migration sets: no ``mysql_engine`` /
    ``mysql_charset`` argument is declared, so the table inherits the
    MySQL server's (or the database's) default exactly as every earlier
    table in this project does. Asserted here so a future silent
    divergence is caught, and verified for real against development
    MySQL separately -- SQLite can prove neither."""
    _, source = _load_migration()
    # The docstring legitimately *names* both arguments to say neither is
    # used, so only the executable body is scanned.
    code = _code(source)
    assert "mysql_engine" not in code
    assert "mysql_charset" not in code
    with app.app_context():
        assert CalendarEvent.__table__.kwargs == {}
