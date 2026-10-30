"""Exact research-v2 migration with a cooperative lease and bounded checks.

An approved external backup and quiescent writers are execution prerequisites.
The named lease coordinates migrators, never ordinary application writes.
In-memory fingerprints are not a backup. Already-current releases preserve v2 data.
"""
import hashlib
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
PARENT_HEAD = "8e5f3b0d7124"
EXPECTED_HEAD = "a6f2c9d84e71"
MIGRATION_SHA256 = "17134427fa9bb31193210497db14a60bbaa9ba0d39426c3d446371f25fa97ba6"
LEASE = "aelms_schema_migration"
MAX_SNAPSHOT_ROWS = 250_000
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_SNAPSHOT_SECONDS = 45
NEW_TABLES = (
    "research_v2_protocols", "research_v2_sampling_state",
    "research_v2_sampling_opportunities", "research_v2_events",
    "research_v2_prompts", "research_v2_feature_memberships",
    "research_v2_delivery_receipts", "research_v2_evaluation_partitions",
    "research_quality_runs", "research_quality_reports", "research_quality_incidents",
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _normalized_check(expression):
    # Formatting normalization for these reviewed expressions, not arbitrary SQL.
    # Protect machine-code literals before stripping MySQL charset introducers.
    literals = []

    def protect(match):
        value = match.group(0)
        literals.append(value[value.index("'"):])
        return "__literal_" + str(len(literals) - 1) + "__"

    expression = re.sub(r"(?:_[A-Za-z0-9]+)?'(?:''|\\.|[^'])*'", protect, expression).lower()
    expression = expression.replace("`", "").replace("(", "").replace(")", "")
    expression = re.sub(
        r"\b([a-z_]\w*(?:\s*[-+*/]\s*[a-z_]\w*)*)\s+between\s+(-?\s*\d+)\s+and\s+(-?\s*\d+)\b",
        lambda match: match[1] + ">=" + match[2] + " and " + match[1] + "<=" + match[3],
        expression,
    )
    expression = re.sub(r"\s+", "", expression)
    for index, literal in enumerate(literals):
        expression = expression.replace("__literal_" + str(index) + "__", literal)
    return expression


def _row_digest(row):
    """Unambiguous typed original-column fingerprint; values remain in memory."""
    digest = hashlib.sha256()
    byte_count = 0
    for value in row:
        tag = type(value).__name__.encode("ascii")
        if value is None:
            encoded = b""
        elif isinstance(value, (bytes, bytearray, memoryview)):
            encoded = bytes(value)
        elif isinstance(value, float):
            encoded = value.hex().encode("ascii")
        else:
            encoded = str(value).encode("utf-8")
        digest.update(len(tag).to_bytes(2, "big"))
        digest.update(tag)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        byte_count += len(tag) + len(encoded) + 10
    return digest.digest(), byte_count


def _snapshot(connection, original=None):
    """Bounded SELECT/SHOW-only multiset fingerprints in a read-only snapshot."""
    from sqlalchemy import inspect

    deadline = time.monotonic() + MAX_SNAPSHOT_SECONDS
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
    inspector = inspect(connection)
    names = sorted(original if original is not None else inspector.get_table_names())
    _require(len(names) == 75, "Unexpected original table inventory")
    quote = connection.dialect.identifier_preparer.quote
    total_rows = total_bytes = 0
    result = {}
    try:
        for name in names:
            _require(time.monotonic() <= deadline, "Preservation snapshot time limit")
            columns = (original[name]["columns"] if original is not None else
                       [column["name"] for column in inspector.get_columns(name)])
            statement = (
                "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
                + ",".join(quote(column) for column in columns) + " FROM " + quote(name)
                + " LIMIT " + str(MAX_SNAPSHOT_ROWS - total_rows + 1)
            )
            rows = connection.execution_options(stream_results=True, max_row_buffer=128).exec_driver_sql(statement)
            fingerprints = []
            failed = False
            try:
                for row in rows:
                    row_digest, byte_count = _row_digest(row)
                    total_rows += 1
                    total_bytes += byte_count
                    _require(total_rows <= MAX_SNAPSHOT_ROWS, "Preservation snapshot row limit")
                    _require(total_bytes <= MAX_SNAPSHOT_BYTES, "Preservation snapshot byte limit")
                    _require(time.monotonic() <= deadline, "Preservation snapshot time limit")
                    fingerprints.append(row_digest)
            except BaseException:
                failed = True
                # Stop an over-budget unbuffered query without draining it first.
                connection.invalidate()
                raise
            finally:
                try:
                    rows.close()
                except Exception:
                    if not failed:
                        raise
            result[name] = {
                "columns": columns, "count": len(fingerprints),
                "sha256": hashlib.sha256(b"".join(sorted(fingerprints))).hexdigest(),
            }
    finally:
        connection.rollback()
    return result


def _verify_schema(connection, metadata, *, newly_applied):
    """Enforced CHECK/FK/index review supplements Alembic model comparison."""
    import sqlalchemy as sa
    from alembic.runtime.migration import MigrationContext

    inspector = sa.inspect(connection)
    # Alembic owns its revision table; it is not an application model.
    _require(set(inspector.get_table_names()) == set(metadata.tables) | {"alembic_version"},
             "Unexpected table inventory")
    _require(len(inspector.get_table_names()) == 86, "Unexpected table count")
    states = {(row[0], row[1]): row[2] for row in connection.execute(sa.text(
        "SELECT TABLE_NAME,CONSTRAINT_NAME,ENFORCED FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_TYPE='CHECK'"
    ))}
    check_count = foreign_key_count = index_count = 0
    comparison = MigrationContext.configure(connection)
    for name in NEW_TABLES:
        model = metadata.tables[name]
        options = inspector.get_table_options(name)
        _require(options.get("mysql_engine", "").lower() == "innodb"
                 and options.get("mysql_row_format", "").upper() == "DYNAMIC"
                 and options.get("mysql_collate") == "utf8mb4_0900_bin", "Unexpected v2 storage options")
        if newly_applied:
            _require(connection.exec_driver_sql("SELECT 1 FROM `" + name + "` LIMIT 1").first() is None,
                     "New v2 tables must be empty")
        columns = inspector.get_columns(name)
        _require([column["name"] for column in columns] == list(model.columns.keys()), "Unexpected v2 columns")
        _require(inspector.get_pk_constraint(name)["constrained_columns"]
                 == list(model.primary_key.columns.keys()), "Unexpected v2 primary key")
        expected_unique = {tuple(column.name for column in constraint.columns)
                           for constraint in model.constraints if isinstance(constraint, sa.UniqueConstraint)}
        actual_unique = {tuple(constraint["column_names"])
                         for constraint in inspector.get_unique_constraints(name)}
        _require(actual_unique == expected_unique, "Unexpected v2 unique keys")
        for column in columns:
            expected = model.columns[column["name"]]
            reflected = sa.Column(column["name"], column["type"], nullable=column["nullable"])
            _require(not comparison.impl.compare_type(reflected, expected)
                     and column["nullable"] == expected.nullable, "Unexpected v2 column type")
            if isinstance(expected.type, sa.BigInteger):
                _require(not getattr(column["type"], "unsigned", False), "Unexpected unsigned v2 integer")
            if isinstance(expected.type, sa.String):
                collation = connection.execute(sa.text(
                    "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND COLUMN_NAME=:column"
                ), {"table": name, "column": column["name"]}).scalar_one()
                _require(collation == "utf8mb4_0900_bin", "Unexpected v2 code collation")
        expected_fks = {(tuple(item.parent.name for item in fk.elements), fk.elements[0].column.table.name,
                         tuple(item.column.name for item in fk.elements)) for fk in model.foreign_key_constraints}
        actual_fks = set()
        for fk in inspector.get_foreign_keys(name):
            _require(fk.get("referred_schema") in (None, connection.engine.url.database)
                     and all(value in (None, "RESTRICT", "NO ACTION")
                             for value in fk.get("options", {}).values()), "Unexpected v2 foreign-key action")
            actual_fks.add((tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"])))
        _require(actual_fks == expected_fks, "Unexpected v2 foreign keys")
        foreign_key_count += len(actual_fks)
        actual_indexes = {index["name"]: index for index in inspector.get_indexes(name)}
        for index in model.indexes:
            actual = actual_indexes.get(index.name, {})
            _require(actual.get("column_names") == [column.name for column in index.columns]
                     and bool(actual.get("unique")) == bool(index.unique), "Unexpected v2 index")
            index_count += 1
        expected_checks = {str(check.name): str(check.sqltext)
                           for check in model.constraints if isinstance(check, sa.CheckConstraint)}
        actual_checks = {check["name"]: check["sqltext"] for check in inspector.get_check_constraints(name)}
        _require(set(actual_checks) == set(expected_checks), "Unexpected v2 CHECK inventory")
        for check_name, expression in expected_checks.items():
            _require(_normalized_check(actual_checks[check_name]) == _normalized_check(expression)
                     and states.get((name, check_name)) == "YES", "Unexpected or unenforced v2 CHECK")
        check_count += len(actual_checks)
    _require((foreign_key_count, index_count, check_count) == (16, 12, 70), "Unexpected v2 constraint totals")
    old_checks = (
        ("research_export_archives", "ck_research_export_archives_expiry_ms", "expiry_at_ms IS NULL OR expiry_at_ms>0"),
        ("research_configurations", "ck_research_configurations_schema_valid",
         "event_schema_version IN ('natural-use-events.v1','natural-use-events.v2')"),
        ("research_exports", "ck_research_exports_format_valid",
         "export_format IN ('natural-use-export.v1','natural-use-v2-export.v1')"),
    )
    for table, name, expression in old_checks:
        actual = {check["name"]: check["sqltext"] for check in inspector.get_check_constraints(table)}
        _require(name in actual and _normalized_check(actual[name]) == _normalized_check(expression)
                 and states.get((table, name)) == "YES", "Unexpected or unenforced extended CHECK")
    archive_columns = {column["name"]: column for column in inspector.get_columns("research_export_archives")}
    expiry = archive_columns.get("expiry_at_ms", {})
    _require(isinstance(expiry.get("type"), sa.BigInteger)
             and not getattr(expiry.get("type"), "unsigned", False)
             and expiry.get("nullable") and expiry.get("default") is None, "Unexpected archive expiry column")
    archive_indexes = {index["name"]: index for index in inspector.get_indexes("research_export_archives")}
    expiry_index = archive_indexes.get("ix_research_export_archives_expiry_ms", {})
    _require(expiry_index.get("column_names") == ["expiry_at_ms"] and not expiry_index.get("unique"),
             "Unexpected archive expiry index")
    if newly_applied:
        _require(connection.exec_driver_sql(
            "SELECT 1 FROM research_export_archives WHERE expiry_at_ms IS NOT NULL LIMIT 1"
        ).first() is None, "Legacy archive expiry changed")


def main():
    phase = "configuration"
    try:
        _require(os.environ.get("FLASK_ENV") == "production", "FLASK_ENV must be production")
        for source, target in (("MIGRATION_DATABASE_USER", "DATABASE_USER"),
                               ("MIGRATION_DATABASE_PASSWORD", "DATABASE_PASSWORD")):
            _require(bool(os.environ.get(source)), "Separate migration credentials are required")
            os.environ[target] = os.environ[source]
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        from wsgi import application
        from app.extensions import db
        from alembic import command
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from sqlalchemy import text

        with application.app_context(), db.engine.connect() as lease:
            phase = "migration_lease"
            _require(lease.execute(text("SELECT GET_LOCK(:name,30)"), {"name": LEASE}).scalar_one() == 1,
                     "The schema migration lock is unavailable")
            lease.commit()
            try:
                phase = "source_and_parent"
                config = application.extensions["migrate"].migrate.get_config(str(ROOT / "migrations"))
                graph = ScriptDirectory.from_config(config)
                _require(graph.get_heads() == [EXPECTED_HEAD]
                         and graph.get_revision(EXPECTED_HEAD).down_revision == PARENT_HEAD
                         and len(list(graph.walk_revisions())) == 49, "Unexpected source migration graph")
                migration = ROOT / "migrations/versions/a6f2c9d84e71_research_v2_collection.py"
                _require(hashlib.sha256(migration.read_bytes()).hexdigest() == MIGRATION_SHA256,
                         "Reviewed migration bytes changed")
                with db.engine.connect() as connection:
                    _require(connection.dialect.name == "mysql", "MySQL is required")
                    _require(connection.exec_driver_sql("SELECT @@innodb_page_size").scalar_one() >= 8192,
                             "InnoDB page size is too small")
                    heads = MigrationContext.configure(connection).get_current_heads()
                    _require(heads in ((PARENT_HEAD,), (EXPECTED_HEAD,)), "Unexpected database revision")
                    connection.rollback()
                    before = None
                    if heads == (PARENT_HEAD,):
                        phase = "preservation_before"
                        before = _snapshot(connection)
                if before is not None:
                    phase = "exact_upgrade"
                    print("Applying reviewed research-v2 transition: " + PARENT_HEAD + " -> " + EXPECTED_HEAD,
                          flush=True)
                    # Never substitute reset/stamp/downgrade or repair partial DDL.
                    command.upgrade(config, EXPECTED_HEAD)
                else:
                    print("Approved schema already current; verifying without DDL", flush=True)
                phase = "schema_verification"
                with db.engine.connect() as connection:
                    _require(MigrationContext.configure(connection).get_current_heads() == (EXPECTED_HEAD,),
                             "Unexpected final database revision")
                    _verify_schema(connection, db.metadata, newly_applied=before is not None)
                    connection.rollback()
                    if before is not None:
                        phase = "preservation_after"
                        after = _snapshot(connection, before)
                        for table in before:
                            if table == "alembic_version":
                                _require(before[table]["count"] == after[table]["count"] == 1
                                         and before[table]["columns"] == after[table]["columns"] == ["version_num"],
                                         "Unexpected revision metadata shape")
                            else:
                                _require(before[table] == after[table], "Original row fingerprints changed")
                phase = "model_comparison"
                command.check(config)
            finally:
                lease.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LEASE})
                lease.commit()
        print("Research-v2 head, enforced constraints and model schema verified: " + EXPECTED_HEAD, flush=True)
        return 0
    except (Exception, SystemExit) as error:
        # Never print SQL/values, driver messages, URLs, credentials or row digests.
        print("Railway migration failed phase=" + phase + " type=" + type(error).__name__,
              file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
