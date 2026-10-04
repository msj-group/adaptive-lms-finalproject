"""Phase 4 / M12 migration checks for Group discussions.

The execution probes use an isolated temporary SQLite database seeded with
``users``, a minimal ``groups``, the Phase 4 / M11 shape of
``notifications`` and the three M11 messaging tables, plus representative
rows in each -- so "nothing existing is lost" is executed rather than
asserted. Both directions run with foreign keys **enforced** and
``PRAGMA foreign_key_check`` asserted after each.

The MySQL checks here compile dialect DDL and render the revision's
offline (``--sql``) MySQL script; neither connects to a database. The real
upgrade against the authorized development MySQL database is a separate,
manually executed check.
"""

import importlib.util
import io
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
from app.models import DiscussionReply, DiscussionTopic, NotificationKind

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "f3c8a1d5e927"
_DOWN_REVISION = "a4d9e3f7c215"

_NEW_TABLES = ["discussion_topics", "discussion_replies"]

_EXPECTED_COLUMNS = {
    "discussion_topics": {"id", "public_id", "group_id", "author_id", "title", "body", "status",
                          "version", "creation_nonce", "created_at", "updated_at"},
    "discussion_replies": {"id", "public_id", "topic_id", "author_id", "body", "creation_nonce",
                           "created_at"},
}

_EXPECTED_CHECKS = {
    "discussion_topics": {
        "ck_discussion_topics_title_not_blank",
        "ck_discussion_topics_body_not_blank",
        "ck_discussion_topics_status_valid",
        "ck_discussion_topics_version_positive",
    },
    "discussion_replies": {"ck_discussion_replies_body_not_blank"},
}

_EXPECTED_INDEXES = {
    "discussion_topics": {
        "ix_discussion_topics_group_created_id": ["group_id", "created_at", "id"],
        "ix_discussion_topics_author_created_id": ["author_id", "created_at", "id"],
    },
    "discussion_replies": {
        "ix_discussion_replies_topic_created_id": ["topic_id", "created_at", "id"],
        "ix_discussion_replies_author_created_id": ["author_id", "created_at", "id"],
    },
}

_EXPECTED_FKS = {
    "discussion_topics": {("group_id", "groups"), ("author_id", "users")},
    "discussion_replies": {("topic_id", "discussion_topics"), ("author_id", "users")},
}

_M11_KINDS = (
    "enrollment_activated",
    "enrollment_withdrawn",
    "teacher_assignment_activated",
    "teacher_assignment_removed",
    "schedule_changed",
    "lesson_published",
    "material_available",
    "announcement_published",
    "message_received",
)
_M12_KIND = "discussion_topic_created"

_MOMENT = "'2026-05-13 09:00:00'"


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m12_{_REVISION}", path)
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
    # Later revisions follow this one; the single head is now the Phase 6 replacement's.
    assert revisions - {p for p in parents.values() if p is not None} == {"d574ab56594f"}
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    assert len([r for r, p in parents.items() if p is None]) == 1


def test_the_revision_creates_two_tables_and_only_replaces_the_notification_check():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _NEW_TABLES
    for forbidden in ("add_column", "drop_column", "op.bulk_insert", "ondelete", "onupdate",
                      "mysql_engine", "mysql_charset", "server_default"):
        assert forbidden not in code, forbidden
    assert re.findall(r"batch_alter_table\(\s*'([^']+)'", code) == ["notifications"]
    assert re.findall(r"drop_constraint\(([^,]+),\s*'([^']+)'", code) == [
        ("_KIND_CHECK_NAME", "notifications")
    ]
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == [
        "discussion_topics.id", "groups.id", "users.id"
    ]
    upgrade = _function(code, "upgrade")
    for forbidden in ("drop_table", "drop_index", "op.execute", "message_"):
        assert forbidden not in upgrade, forbidden
    # The CHECK is widened only after both new tables and their indexes exist.
    assert upgrade.index("_replace_notification_kind_check(_KINDS_AFTER)") > upgrade.rindex(
        "op.create_index("
    )


def test_the_downgrade_removes_only_what_the_upgrade_added_in_dependency_order():
    _, source = _load_migration()
    downgrade = _function(_code(source), "downgrade")
    steps = [
        downgrade.index("DELETE FROM notifications WHERE kind = 'discussion_topic_created'"),
        downgrade.index("_replace_notification_kind_check(_KINDS_BEFORE)"),
        downgrade.index("op.drop_table('discussion_replies')"),
        downgrade.index("op.drop_table('discussion_topics')"),
    ]
    assert steps == sorted(steps)
    assert "drop_index" not in downgrade
    assert downgrade.count("op.drop_table(") == 2
    assert downgrade.count("DELETE FROM") == 1
    assert re.findall(r'op\.execute\("([^"]+)"\)', downgrade) == [
        "DELETE FROM notifications WHERE kind = 'discussion_topic_created'"
    ]


def test_the_migrations_kind_lists_keep_every_m11_kind_and_add_exactly_one():
    module, _ = _load_migration()
    assert module._KINDS_BEFORE == _M11_KINDS
    assert module._KINDS_AFTER == _M11_KINDS + (_M12_KIND,)
    assert module._KINDS_AFTER == tuple(kind.value for kind in NotificationKind)


def test_the_revision_declares_every_expected_constraint_and_index():
    _, source = _load_migration()
    for fragment in (
        "name='ck_discussion_topics_title_not_blank'",
        "name='ck_discussion_topics_body_not_blank'",
        "name='ck_discussion_topics_status_valid'",
        "name='ck_discussion_topics_version_positive'",
        "name='ck_discussion_replies_body_not_blank'",
        "'LENGTH(TRIM(title)) > 0'",
        "'LENGTH(TRIM(body)) > 0'",
        "\"status IN ('open', 'locked')\"",
        "'version > 0'",
        "sa.UniqueConstraint('public_id')",
        "sa.UniqueConstraint('creation_nonce')",
        "'ix_discussion_topics_group_created_id'",
        "'ix_discussion_topics_author_created_id'",
        "'ix_discussion_replies_topic_created_id'",
        "'ix_discussion_replies_author_created_id'",
        "sa.String(length=150)",
        "sa.String(length=5000)",
        "sa.String(length=64)",
        "sa.String(length=16)",
    ):
        assert fragment in source, fragment


# ===========================================================================
# Execution against an isolated SQLite database
# ===========================================================================


def _kind_list(values):
    return ", ".join(f"'{kind}'" for kind in values)


_PREREQ = [
    """CREATE TABLE users (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        role VARCHAR(32) NOT NULL,
        status VARCHAR(32) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (public_id)
    )""",
    """CREATE TABLE groups (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        name VARCHAR(100) NOT NULL,
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
    + _kind_list(_M11_KINDS)
    + """)),
        FOREIGN KEY(recipient_id) REFERENCES users (id)
    )""",
    "CREATE INDEX ix_notifications_recipient_created_id ON notifications"
    " (recipient_id, created_at, id)",
    "CREATE INDEX ix_notifications_recipient_unread_created ON notifications"
    " (recipient_id, read_at, created_at)",
    """CREATE TABLE message_threads (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        created_by_id INTEGER NOT NULL,
        subject VARCHAR(150) NOT NULL,
        creation_nonce VARCHAR(64) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_message_threads_subject_not_blank CHECK (LENGTH(TRIM(subject)) > 0),
        FOREIGN KEY(created_by_id) REFERENCES users (id),
        UNIQUE (public_id),
        UNIQUE (creation_nonce)
    )""",
    """CREATE TABLE message_thread_members (
        id INTEGER NOT NULL,
        thread_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(thread_id) REFERENCES message_threads (id),
        FOREIGN KEY(user_id) REFERENCES users (id),
        CONSTRAINT uq_message_thread_members_thread_user UNIQUE (thread_id, user_id)
    )""",
    """CREATE TABLE messages (
        id INTEGER NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        thread_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        body VARCHAR(5000) NOT NULL,
        creation_nonce VARCHAR(64) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_messages_body_not_blank CHECK (LENGTH(TRIM(body)) > 0),
        FOREIGN KEY(sender_id) REFERENCES users (id),
        FOREIGN KEY(thread_id) REFERENCES message_threads (id),
        UNIQUE (public_id),
        UNIQUE (creation_nonce)
    )""",
    "INSERT INTO users (id, public_id, role, status) VALUES (11, 'u-11', 'student', 'active')",
    "INSERT INTO users (id, public_id, role, status) VALUES (12, 'u-12', 'teacher', 'active')",
    "INSERT INTO groups (id, public_id, name) VALUES (21, 'g-21', 'Group A')",
    "INSERT INTO message_threads (id, public_id, created_by_id, subject, creation_nonce,"
    f" created_at) VALUES (1, 't-1', 11, 'Question', '{'1' * 64}', {_MOMENT})",
    "INSERT INTO message_thread_members (id, thread_id, user_id, joined_at) VALUES"
    f" (1, 1, 11, {_MOMENT}), (2, 1, 12, {_MOMENT})",
    "INSERT INTO messages (id, public_id, thread_id, sender_id, body, creation_nonce,"
    f" created_at) VALUES (1, 'm-1', 1, 11, 'Hello', '{'2' * 64}', {_MOMENT}),"
    f" (2, 'm-2', 1, 12, 'Hi back', '{'3' * 64}', '2026-05-13 10:00:00')",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message, target_path,"
    " created_at, read_at) VALUES (1, 'n-1', 11, 'announcement_published', 'Title', 'Message',"
    " '/student/announcements/a-1', '2026-05-13 09:00:00', '2026-05-13 10:00:00')",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message, target_path,"
    " created_at, read_at) VALUES (2, 'n-2', 12, 'message_received', 'New message', 'M',"
    " '/messages/threads/t-1', '2026-05-13 09:00:00', NULL)",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message, target_path,"
    " created_at, read_at) VALUES (3, 'n-3', 11, 'message_received', 'New message', 'M',"
    " '/messages/threads/t-1', '2026-05-13 10:00:00', '2026-05-13 11:00:00')",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message, target_path,"
    " created_at, read_at) VALUES (4, 'n-4', 12, 'schedule_changed', 'Title', 'Message',"
    " '/teacher/dashboard', '2026-05-13 09:00:00', NULL)",
]

_NONCE_A = "a" * 64
_NONCE_B = "b" * 64
_NONCE_C = "c" * 64

_M11_TABLES = ("message_threads", "message_thread_members", "messages")


def _notifications(conn):
    return conn.execute(
        sa.text("SELECT id, public_id, recipient_id, kind, title, message, target_path,"
                " created_at, read_at FROM notifications ORDER BY id")
    ).fetchall()


def _m11_rows(conn):
    return {
        table: conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY id")).fetchall()
        for table in _M11_TABLES
    }


def _notification_check_sql(conn):
    return conn.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'notifications'")
    ).scalar_one()


def _shape(conn):
    schema = inspect(conn)
    return {
        table: (
            {c["name"] for c in schema.get_columns(table)},
            {c["name"] for c in schema.get_check_constraints(table)},
            {i["name"]: i["column_names"] for i in schema.get_indexes(table)},
            {(fk["constrained_columns"][0], fk["referred_table"])
             for fk in schema.get_foreign_keys(table)},
            sorted(tuple(u["column_names"]) for u in schema.get_unique_constraints(table)),
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


_TOPIC_INSERT = ("INSERT INTO discussion_topics (public_id, group_id, author_id, title, body,"
                 " status, version, creation_nonce, created_at, updated_at) VALUES ")
_REPLY_INSERT = ("INSERT INTO discussion_replies (public_id, topic_id, author_id, body,"
                 " creation_nonce, created_at) VALUES ")


def _topic_values(public_id, group_id=21, author_id=12, title="'T'", body="'B'",
                  status="'open'", version=1, nonce=_NONCE_B):
    return (f"('{public_id}', {group_id}, {author_id}, {title}, {body}, {status}, {version},"
            f" '{nonce}', {_MOMENT}, {_MOMENT})")


def test_migration_applies_and_reverses_on_isolated_sqlite():
    tmp, engine, conn = _probe("probe.db")
    try:
        before = _notifications(conn)
        m11_before = _m11_rows(conn)
        tables_before = set(inspect(conn).get_table_names())

        _run(conn, "upgrade")
        assert set(inspect(conn).get_table_names()) - tables_before == set(_NEW_TABLES)
        for table, (columns, checks, indexes, fks, uniques) in _shape(conn).items():
            assert columns == _EXPECTED_COLUMNS[table], table
            assert checks == _EXPECTED_CHECKS[table], table
            for name, cols in _EXPECTED_INDEXES[table].items():
                assert indexes[name] == cols, (table, name)
            assert fks == _EXPECTED_FKS[table], table
            assert uniques == [("creation_nonce",), ("public_id",)], table
        assert {c["name"] for c in inspect(conn).get_check_constraints("notifications")} == {
            "ck_notifications_kind_valid"
        }
        assert {i["name"] for i in inspect(conn).get_indexes("notifications")} >= {
            "ix_notifications_recipient_created_id",
            "ix_notifications_recipient_unread_created",
        }
        assert _kind_list(_M11_KINDS + (_M12_KIND,)) in _notification_check_sql(conn)
        # Every existing notification and every M11 messaging row survives
        # the rebuild unchanged, and nothing is seeded.
        assert _notifications(conn) == before
        assert _m11_rows(conn) == m11_before
        for table in _NEW_TABLES:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0

        conn.execute(sa.text(_TOPIC_INSERT + _topic_values("d-1", nonce=_NONCE_A)))
        conn.execute(sa.text(
            _REPLY_INSERT + f"('r-1', 1, 11, 'Reply', '{_NONCE_A}', {_MOMENT})"
        ))
        conn.execute(sa.text(
            "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message,"
            " target_path, created_at) VALUES (5, 'n-5', 11, 'discussion_topic_created',"
            f" 'New discussion topic', 'M', '/student/groups/g-21/discussions/d-1', {_MOMENT})"
        ))
        # A second message notification after the upgrade is still valid.
        conn.execute(sa.text(
            "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message,"
            " target_path, created_at) VALUES (6, 'n-6', 11, 'message_received', 'New message',"
            f" 'M', '/messages/threads/t-1', {_MOMENT})"
        ))
        conn.commit()
        after_inserts = _notifications(conn)

        _refused(conn, _TOPIC_INSERT + _topic_values("d-1"), "topic public_id uniqueness")
        _refused(conn, _TOPIC_INSERT + _topic_values("d-2", nonce=_NONCE_A),
                 "topic nonce uniqueness")
        _refused(conn, _TOPIC_INSERT + _topic_values("d-3", title="'   '"), "blank title")
        _refused(conn, _TOPIC_INSERT + _topic_values("d-4", body="''"), "blank topic body")
        _refused(conn, _TOPIC_INSERT + _topic_values("d-5", status="'hidden'"), "status set")
        _refused(conn, _TOPIC_INSERT + _topic_values("d-6", version=0), "positive version")
        _refused(conn, _TOPIC_INSERT + _topic_values("d-7", group_id=99), "group foreign key")
        _refused(conn, _TOPIC_INSERT + _topic_values("d-8", author_id=99),
                 "topic author foreign key")
        _refused(conn, _REPLY_INSERT + f"('r-1', 1, 11, 'X', '{_NONCE_C}', {_MOMENT})",
                 "reply public_id uniqueness")
        _refused(conn, _REPLY_INSERT + f"('r-2', 1, 11, 'X', '{_NONCE_A}', {_MOMENT})",
                 "reply nonce uniqueness")
        _refused(conn, _REPLY_INSERT + f"('r-3', 1, 11, '  ', '{_NONCE_C}', {_MOMENT})",
                 "blank reply body")
        _refused(conn, _REPLY_INSERT + f"('r-4', 99, 11, 'X', '{_NONCE_C}', {_MOMENT})",
                 "reply topic foreign key")
        _refused(conn, _REPLY_INSERT + f"('r-5', 1, 99, 'X', '{_NONCE_C}', {_MOMENT})",
                 "reply author foreign key")
        _refused(conn, "INSERT INTO notifications (public_id, recipient_id, kind, title, message,"
                 f" target_path, created_at) VALUES ('n-x', 11, 'discussion_reply_created', 'T',"
                 f" 'M', '/x', {_MOMENT})", "the widened kind CHECK")
        assert _notifications(conn) == after_inserts

        _run(conn, "downgrade")
        assert set(inspect(conn).get_table_names()) == tables_before
        # Only the discussion notification is gone: every earlier row --
        # both message_received rows included, and the one written after
        # the upgrade -- is untouched, as is every M11 messaging row.
        assert _notifications(conn) == [row for row in after_inserts if row[3] != _M12_KIND]
        assert [row[0] for row in _notifications(conn)] == [1, 2, 3, 4, 6]
        assert _m11_rows(conn) == m11_before
        assert {c["name"] for c in inspect(conn).get_check_constraints("notifications")} == {
            "ck_notifications_kind_valid"
        }
        restored = _notification_check_sql(conn)
        assert _kind_list(_M11_KINDS) + ")" in " ".join(restored.split())
        assert _M12_KIND not in restored
        _refused(conn, "INSERT INTO notifications (public_id, recipient_id, kind, title, message,"
                 f" target_path, created_at) VALUES ('n-y', 11, '{_M12_KIND}', 'T', 'M', '/x',"
                 f" {_MOMENT})", "the restored M11 kind CHECK")
        conn.execute(sa.text(
            "INSERT INTO notifications (public_id, recipient_id, kind, title, message,"
            f" target_path, created_at) VALUES ('n-z', 11, 'message_received', 'T', 'M', '/x',"
            f" {_MOMENT})"
        ))
        conn.commit()
    finally:
        conn.close()
        engine.dispose()
        tmp.cleanup()


def test_a_second_upgrade_after_a_downgrade_rebuilds_the_same_shape():
    tmp, engine, conn = _probe("probe2.db")
    try:
        before = _notifications(conn)
        m11_before = _m11_rows(conn)
        shapes = []
        for _ in range(2):
            _run(conn, "upgrade")
            shapes.append(_shape(conn))
            assert _notifications(conn) == before
            _run(conn, "downgrade")
            assert _notifications(conn) == before
            assert _m11_rows(conn) == m11_before
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
    for model, table in ((DiscussionTopic, "discussion_topics"),
                         (DiscussionReply, "discussion_replies")):
        checks = {
            constraint.name: " ".join(str(constraint.sqltext).split())
            for constraint in model.__table__.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }
        assert set(checks) == _EXPECTED_CHECKS[table]
        for expression in checks.values():
            assert expression in flat, expression


def test_mysql_ddl_compiles_without_a_connection():
    dialect = mysql.dialect()
    topics = str(CreateTable(DiscussionTopic.__table__).compile(dialect=dialect))
    replies = str(CreateTable(DiscussionReply.__table__).compile(dialect=dialect))

    assert "FOREIGN KEY(group_id) REFERENCES `groups` (id)" in topics
    assert "FOREIGN KEY(author_id) REFERENCES users (id)" in topics
    assert "title VARCHAR(150) NOT NULL" in topics
    assert "body VARCHAR(5000) NOT NULL" in topics
    assert "status VARCHAR(16) NOT NULL" in topics
    assert "version INTEGER NOT NULL" in topics
    assert "creation_nonce VARCHAR(64) NOT NULL" in topics
    assert "UNIQUE (public_id)" in topics and "UNIQUE (creation_nonce)" in topics
    assert "CONSTRAINT ck_discussion_topics_status_valid CHECK (status IN ('open', 'locked'))" in topics
    assert "CONSTRAINT ck_discussion_topics_version_positive CHECK (version > 0)" in topics

    assert "FOREIGN KEY(topic_id) REFERENCES discussion_topics (id)" in replies
    assert "FOREIGN KEY(author_id) REFERENCES users (id)" in replies
    assert "CONSTRAINT ck_discussion_replies_body_not_blank CHECK" in replies

    for ddl in (topics, replies):
        assert "ON DELETE" not in ddl
        assert "ON UPDATE" not in ddl
        assert "BIGINT" in ddl
        assert "DATETIME(" not in ddl
        assert "ENUM(" not in ddl
        assert "TEXT" not in ddl


def _offline_mysql(direction):
    """The revision's offline ``--sql`` script for MySQL, whitespace-flattened.
    No connection is opened."""
    module, _ = _load_migration()
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        getattr(module, direction)()
    return " ".join(buffer.getvalue().split())


def test_the_offline_mysql_upgrade_replaces_only_the_kind_check_after_both_tables():
    sql = _offline_mysql("upgrade")
    steps = [
        sql.index("CREATE TABLE discussion_topics ("),
        sql.index("CREATE INDEX ix_discussion_topics_group_created_id ON discussion_topics"
                  " (group_id, created_at, id)"),
        sql.index("CREATE INDEX ix_discussion_topics_author_created_id ON discussion_topics"
                  " (author_id, created_at, id)"),
        sql.index("CREATE TABLE discussion_replies ("),
        sql.index("CREATE INDEX ix_discussion_replies_topic_created_id ON discussion_replies"
                  " (topic_id, created_at, id)"),
        sql.index("CREATE INDEX ix_discussion_replies_author_created_id ON discussion_replies"
                  " (author_id, created_at, id)"),
        sql.index("ALTER TABLE notifications DROP CHECK ck_notifications_kind_valid"),
        sql.index("ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind_valid CHECK"
                  " (kind IN (" + _kind_list(_M11_KINDS + (_M12_KIND,)) + "))"),
    ]
    assert steps == sorted(steps)
    assert sql.count("CREATE TABLE") == 2
    assert sql.count("ALTER TABLE") == 2
    assert "FOREIGN KEY(group_id) REFERENCES `groups` (id)" in sql
    assert "FOREIGN KEY(topic_id) REFERENCES discussion_topics (id)" in sql
    for forbidden in ("ON DELETE", "ON UPDATE", "ENGINE=", "CHARSET", "DROP TABLE", "DELETE FROM",
                      "INSERT INTO notifications", "UPDATE notifications"):
        assert forbidden not in sql, forbidden


def test_the_offline_mysql_downgrade_restores_the_exact_m11_check():
    sql = _offline_mysql("downgrade")
    steps = [
        sql.index("DELETE FROM notifications WHERE kind = 'discussion_topic_created'"),
        sql.index("ALTER TABLE notifications DROP CHECK ck_notifications_kind_valid"),
        sql.index("ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind_valid CHECK"
                  " (kind IN (" + _kind_list(_M11_KINDS) + "))"),
        sql.index("DROP TABLE discussion_replies"),
        sql.index("DROP TABLE discussion_topics"),
    ]
    assert steps == sorted(steps)
    assert sql.count("DELETE FROM") == 1
    assert sql.count("DROP TABLE") == 2
    assert "DROP INDEX" not in sql
    assert "message_received'" in sql  # retained in the restored CHECK only
    assert "kind = 'message_received'" not in sql


def test_the_new_tables_use_the_deployment_engine_and_charset():
    for model in (DiscussionTopic, DiscussionReply):
        assert model.__table__.kwargs == {}
