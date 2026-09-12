"""Phase 4 / M11 migration checks for private messaging.

The execution probes use an isolated temporary SQLite database seeded
with ``users`` and the Phase 4 / M09 shape of ``notifications`` plus
representative rows, so "nothing existing is lost" is executed rather than
asserted. Both directions run with foreign keys **enforced** and
``PRAGMA foreign_key_check`` asserted after each.

MySQL checks here compile dialect DDL only and never connect; the real
upgrade / downgrade / upgrade round trip against the authorized
development MySQL database is a separate, manually executed check.
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
from app.models import Message, MessageThread, MessageThreadMember, NotificationKind

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "a4d9e3f7c215"
_DOWN_REVISION = "e5b83c7d1a49"

_NEW_TABLES = ["message_threads", "message_thread_members", "messages"]

_EXPECTED_COLUMNS = {
    "message_threads": {"id", "public_id", "created_by_id", "subject", "creation_nonce",
                        "created_at"},
    "message_thread_members": {"id", "thread_id", "user_id", "joined_at"},
    "messages": {"id", "public_id", "thread_id", "sender_id", "body", "creation_nonce",
                 "created_at"},
}

_EXPECTED_CHECKS = {
    "message_threads": {"ck_message_threads_subject_not_blank"},
    "message_thread_members": set(),
    "messages": {"ck_messages_body_not_blank"},
}

_EXPECTED_INDEXES = {
    "message_threads": {"ix_message_threads_created_by_created_id"},
    "message_thread_members": {"ix_message_thread_members_user_thread"},
    "messages": {"ix_messages_thread_created_id", "ix_messages_sender_created_id"},
}

_EXPECTED_FKS = {
    "message_threads": {("created_by_id", "users")},
    "message_thread_members": {("thread_id", "message_threads"), ("user_id", "users")},
    "messages": {("thread_id", "message_threads"), ("sender_id", "users")},
}

_M09_KINDS = (
    "enrollment_activated",
    "enrollment_withdrawn",
    "teacher_assignment_activated",
    "teacher_assignment_removed",
    "schedule_changed",
    "lesson_published",
    "material_available",
    "announcement_published",
)

_MOMENT = "'2026-05-13 09:00:00'"


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m11_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def _flat(source):
    return " ".join(source.split()).replace('" "', "")


def _code(source):
    return source.split("from alembic import op", 1)[1]


def _function(code, name):
    return code.split(f"def {name}():", 1)[1].split("\ndef ", 1)[0]


# ===========================================================================
# Revision identity and what the revision does
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
    assert revisions - {p for p in parents.values() if p is not None} == {_REVISION}
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_three_tables_and_only_replaces_the_notification_check():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _NEW_TABLES
    for forbidden in ("add_column", "drop_column", "op.bulk_insert", "ondelete", "onupdate",
                      "mysql_engine", "mysql_charset"):
        assert forbidden not in code, forbidden
    assert re.findall(r"batch_alter_table\(\s*'([^']+)'", code) == ["notifications"]
    assert re.findall(r"drop_constraint\(([^,]+),\s*'([^']+)'", code) == [
        ("_KIND_CHECK_NAME", "notifications")
    ]
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == [
        "message_threads.id", "users.id"
    ]
    upgrade = _function(code, "upgrade")
    for forbidden in ("drop_table", "drop_index", "op.execute"):
        assert forbidden not in upgrade, forbidden
    # The CHECK is widened only after every new table exists.
    assert upgrade.index("_replace_notification_kind_check(_KINDS_AFTER)") > upgrade.rindex(
        "op.create_index("
    )


def test_the_downgrade_removes_only_what_the_upgrade_added_in_dependency_order():
    _, source = _load_migration()
    downgrade = _function(_code(source), "downgrade")
    steps = [
        downgrade.index("DELETE FROM notifications WHERE kind = 'message_received'"),
        downgrade.index("_replace_notification_kind_check(_KINDS_BEFORE)"),
        downgrade.index("op.drop_table('messages')"),
        downgrade.index("op.drop_table('message_thread_members')"),
        downgrade.index("op.drop_table('message_threads')"),
    ]
    assert steps == sorted(steps)
    assert "drop_index" not in downgrade
    assert downgrade.count("op.drop_table(") == 3
    assert downgrade.count("DELETE FROM") == 1


def test_the_migrations_kind_lists_match_the_application_enum():
    module, _ = _load_migration()
    assert module._KINDS_BEFORE == _M09_KINDS
    assert module._KINDS_AFTER == tuple(kind.value for kind in NotificationKind)
    assert module._KINDS_AFTER[-1] == "message_received"


def test_the_revision_declares_every_expected_constraint_and_index():
    _, source = _load_migration()
    for fragment in (
        "name='ck_message_threads_subject_not_blank'",
        "name='ck_messages_body_not_blank'",
        "'LENGTH(TRIM(subject)) > 0'",
        "'LENGTH(TRIM(body)) > 0'",
        "name='uq_message_thread_members_thread_user'",
        "sa.UniqueConstraint('public_id')",
        "sa.UniqueConstraint('creation_nonce')",
        "'ix_message_threads_created_by_created_id'",
        "'ix_message_thread_members_user_thread'",
        "'ix_messages_thread_created_id'",
        "'ix_messages_sender_created_id'",
        "sa.String(length=150)",
        "sa.String(length=5000)",
        "sa.String(length=64)",
    ):
        assert fragment in source, fragment


# ===========================================================================
# Execution against an isolated SQLite database
# ===========================================================================


_PREREQ = [
    """CREATE TABLE users (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        role VARCHAR(32) NOT NULL,
        status VARCHAR(32) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id)
    )""",
    """CREATE TABLE notifications (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        recipient_id INTEGER NOT NULL,
        kind VARCHAR(48) NOT NULL,
        title VARCHAR(150) NOT NULL,
        message VARCHAR(500) NOT NULL,
        target_path VARCHAR(512) NOT NULL,
        created_at DATETIME NOT NULL,
        read_at DATETIME,
        PRIMARY KEY (id),
        UNIQUE (public_id),
        CONSTRAINT ck_notifications_kind_valid CHECK (kind IN ("""
    + ", ".join(f"'{kind}'" for kind in _M09_KINDS)
    + """)),
        FOREIGN KEY(recipient_id) REFERENCES users (id)
    )""",
    "CREATE INDEX ix_notifications_recipient_created_id ON notifications"
    " (recipient_id, created_at, id)",
    "CREATE INDEX ix_notifications_recipient_unread_created ON notifications"
    " (recipient_id, read_at, created_at)",
    "INSERT INTO users (id, public_id, role, status) VALUES (11, 'u-11', 'student', 'active')",
    "INSERT INTO users (id, public_id, role, status) VALUES (12, 'u-12', 'teacher', 'active')",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message, target_path,"
    " created_at, read_at) VALUES (1, 'n-1', 11, 'announcement_published', 'Title', 'Message',"
    " '/student/announcements/a-1', '2026-05-13 09:00:00', '2026-05-13 10:00:00')",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message, target_path,"
    " created_at, read_at) VALUES (2, 'n-2', 12, 'schedule_changed', 'Title', 'Message',"
    " '/teacher/dashboard', '2026-05-13 09:00:00', NULL)",
]

_NONCE_A = "a" * 64
_NONCE_B = "b" * 64
_NONCE_C = "c" * 64


def _notifications(conn):
    return conn.execute(
        sa.text("SELECT id, public_id, recipient_id, kind, title, message, target_path,"
                " created_at, read_at FROM notifications ORDER BY id")
    ).fetchall()


def _shape(conn):
    schema = inspect(conn)
    return {
        table: (
            {c["name"] for c in schema.get_columns(table)},
            {c["name"] for c in schema.get_check_constraints(table)},
            {i["name"] for i in schema.get_indexes(table)},
            {(fk["constrained_columns"][0], fk["referred_table"])
             for fk in schema.get_foreign_keys(table)},
        )
        for table in _NEW_TABLES
    }


def _run(conn, direction):
    module, _ = _load_migration()
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()
    conn.commit()
    assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []


def _refused(conn, statement, rule):
    try:
        conn.execute(sa.text(statement))
    except sa.exc.IntegrityError:
        conn.rollback()
        return
    raise AssertionError(f"{rule} was not enforced")


def _probe(name):
    tmp = tempfile.TemporaryDirectory()
    url = "sqlite:///" + os.path.join(tmp.name, name).replace(os.sep, "/")
    engine = sa.create_engine(url)
    conn = engine.connect()
    conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    for statement in _PREREQ:
        conn.execute(sa.text(statement))
    conn.commit()
    return tmp, engine, conn


def test_migration_applies_and_reverses_on_isolated_sqlite():
    tmp, engine, conn = _probe("probe.db")
    try:
        before = _notifications(conn)
        tables_before = set(inspect(conn).get_table_names())

        _run(conn, "upgrade")
        assert set(inspect(conn).get_table_names()) - tables_before == set(_NEW_TABLES)
        for table, (columns, checks, indexes, fks) in _shape(conn).items():
            assert columns == _EXPECTED_COLUMNS[table], table
            assert checks == _EXPECTED_CHECKS[table], table
            assert indexes >= _EXPECTED_INDEXES[table], table
            assert fks == _EXPECTED_FKS[table], table
        assert {
            tuple(u["column_names"])
            for u in inspect(conn).get_unique_constraints("message_thread_members")
        } >= {("thread_id", "user_id")}
        assert {c["name"] for c in inspect(conn).get_check_constraints("notifications")} == {
            "ck_notifications_kind_valid"
        }
        assert {i["name"] for i in inspect(conn).get_indexes("notifications")} >= {
            "ix_notifications_recipient_created_id",
            "ix_notifications_recipient_unread_created",
        }
        # Every existing notification survives the rebuild unchanged, and
        # nothing is seeded.
        assert _notifications(conn) == before
        for table in _NEW_TABLES:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0

        conn.execute(sa.text(
            "INSERT INTO message_threads (id, public_id, created_by_id, subject, creation_nonce,"
            f" created_at) VALUES (1, 't-1', 11, 'Question', '{_NONCE_A}', {_MOMENT})"
        ))
        conn.execute(sa.text(
            "INSERT INTO message_thread_members (id, thread_id, user_id, joined_at) VALUES"
            f" (1, 1, 11, {_MOMENT}), (2, 1, 12, {_MOMENT})"
        ))
        conn.execute(sa.text(
            "INSERT INTO messages (id, public_id, thread_id, sender_id, body, creation_nonce,"
            f" created_at) VALUES (1, 'm-1', 1, 11, 'Hello', '{_NONCE_A}', {_MOMENT})"
        ))
        conn.execute(sa.text(
            "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message,"
            " target_path, created_at) VALUES (3, 'n-3', 12, 'message_received', 'New message',"
            f" 'M', '/messages/threads/t-1', {_MOMENT})"
        ))
        conn.commit()

        thread_insert = ("INSERT INTO message_threads (public_id, created_by_id, subject,"
                         " creation_nonce, created_at) VALUES ")
        message_insert = ("INSERT INTO messages (public_id, thread_id, sender_id, body,"
                          " creation_nonce, created_at) VALUES ")
        member_insert = "INSERT INTO message_thread_members (thread_id, user_id, joined_at) VALUES "
        _refused(conn, thread_insert + f"('t-1', 11, 'X', '{_NONCE_B}', {_MOMENT})",
                 "thread public_id uniqueness")
        _refused(conn, thread_insert + f"('t-2', 11, 'X', '{_NONCE_A}', {_MOMENT})",
                 "thread nonce uniqueness")
        _refused(conn, thread_insert + f"('t-3', 11, '   ', '{_NONCE_C}', {_MOMENT})",
                 "blank subject")
        _refused(conn, thread_insert + f"('t-4', 99, 'X', '{_NONCE_C}', {_MOMENT})",
                 "created_by foreign key")
        _refused(conn, member_insert + f"(1, 11, {_MOMENT})", "membership uniqueness")
        _refused(conn, member_insert + f"(99, 11, {_MOMENT})", "member thread foreign key")
        _refused(conn, member_insert + f"(1, 99, {_MOMENT})", "member user foreign key")
        _refused(conn, message_insert + f"('m-1', 1, 11, 'X', '{_NONCE_B}', {_MOMENT})",
                 "message public_id uniqueness")
        _refused(conn, message_insert + f"('m-2', 1, 11, 'X', '{_NONCE_A}', {_MOMENT})",
                 "message nonce uniqueness")
        _refused(conn, message_insert + f"('m-3', 1, 11, '  ', '{_NONCE_C}', {_MOMENT})",
                 "blank body")
        _refused(conn, message_insert + f"('m-4', 99, 11, 'X', '{_NONCE_C}', {_MOMENT})",
                 "message thread foreign key")
        _refused(conn, message_insert + f"('m-5', 1, 99, 'X', '{_NONCE_C}', {_MOMENT})",
                 "sender foreign key")
        _refused(conn, "INSERT INTO notifications (public_id, recipient_id, kind, title, message,"
                 f" target_path, created_at) VALUES ('n-x', 11, 'message_edited', 'T', 'M', '/x',"
                 f" {_MOMENT})", "the widened kind CHECK")

        _run(conn, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        # Only the message notification is gone; the rest are untouched.
        assert _notifications(conn) == before
        assert {c["name"] for c in inspect(conn).get_check_constraints("notifications")} == {
            "ck_notifications_kind_valid"
        }
        _refused(conn, "INSERT INTO notifications (public_id, recipient_id, kind, title, message,"
                 f" target_path, created_at) VALUES ('n-y', 11, 'message_received', 'T', 'M',"
                 f" '/x', {_MOMENT})", "the restored narrower kind CHECK")
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_shape():
    tmp, engine, conn = _probe("probe2.db")
    try:
        before = _notifications(conn)
        shapes = []
        for _ in range(2):
            _run(conn, "upgrade")
            shapes.append(_shape(conn))
            assert _notifications(conn) == before
            _run(conn, "downgrade")
            assert _notifications(conn) == before
        assert shapes[0] == shapes[1]
        _run(conn, "upgrade")
        assert _shape(conn) == shapes[0]
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


# ===========================================================================
# Model / migration agreement, and MySQL DDL
# ===========================================================================


def test_models_and_migration_agree_on_the_new_tables(app):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        for table in _NEW_TABLES:
            block = source.split(f"op.create_table('{table}',", 1)[1].split("\n    )", 1)[0]
            declared = dict(re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block))
            actual = {
                column["name"]: str(column["nullable"])
                for column in inspector.get_columns(table)
            }
            declared.pop("id", None)
            actual.pop("id", None)
            assert declared == actual, table


def test_the_models_check_expressions_match_the_migrations():
    _, source = _load_migration()
    flat = _flat(source)
    for model, expected in ((MessageThread, _EXPECTED_CHECKS["message_threads"]),
                            (MessageThreadMember, set()),
                            (Message, _EXPECTED_CHECKS["messages"])):
        checks = {
            constraint.name: " ".join(str(constraint.sqltext).split())
            for constraint in model.__table__.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }
        assert set(checks) == expected
        for expression in checks.values():
            assert expression in flat, expression


def test_mysql_ddl_compiles_without_a_connection():
    dialect = mysql.dialect()
    threads = str(CreateTable(MessageThread.__table__).compile(dialect=dialect))
    members = str(CreateTable(MessageThreadMember.__table__).compile(dialect=dialect))
    messages = str(CreateTable(Message.__table__).compile(dialect=dialect))

    assert "FOREIGN KEY(created_by_id) REFERENCES users (id)" in threads
    assert "subject VARCHAR(150) NOT NULL" in threads
    assert "creation_nonce VARCHAR(64) NOT NULL" in threads
    assert "UNIQUE (public_id)" in threads and "UNIQUE (creation_nonce)" in threads
    assert "CONSTRAINT ck_message_threads_subject_not_blank CHECK" in threads

    assert "FOREIGN KEY(thread_id) REFERENCES message_threads (id)" in members
    assert "FOREIGN KEY(user_id) REFERENCES users (id)" in members
    assert "CONSTRAINT uq_message_thread_members_thread_user UNIQUE (thread_id, user_id)" in members

    assert "FOREIGN KEY(thread_id) REFERENCES message_threads (id)" in messages
    assert "FOREIGN KEY(sender_id) REFERENCES users (id)" in messages
    assert "body VARCHAR(5000) NOT NULL" in messages
    assert "UNIQUE (public_id)" in messages and "UNIQUE (creation_nonce)" in messages
    assert "CONSTRAINT ck_messages_body_not_blank CHECK" in messages

    for ddl in (threads, members, messages):
        assert "ON DELETE" not in ddl
        assert "ON UPDATE" not in ddl
        assert "BIGINT" in ddl
        assert "DATETIME(" not in ddl
        assert "ENUM(" not in ddl
        assert "TEXT" not in ddl


def test_the_new_tables_use_the_deployment_engine_and_charset():
    for model in (MessageThread, MessageThreadMember, Message):
        assert model.__table__.kwargs == {}
