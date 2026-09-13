"""M09 migration checks for ``announcements`` and for the replacement of
the ``notifications`` ``kind`` CHECK.

The execution probe uses an isolated temporary SQLite database seeded
with the prerequisite tables **and representative existing rows** --
including three notifications of three different kinds, read and unread
-- so the "every existing notification survives" claim is executed rather
than asserted. That matters more here than in earlier revisions: this is
the first migration in the project that rewrites an existing table on the
SQLite backend at all, and a table rebuild that lost, reordered or
truncated a row would be exactly the failure worth catching.

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
from app.models import Announcement, NotificationKind

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "c7a91f4b2d68"
_DOWN_REVISION = "2f6d1c83ab47"

_NEW_TABLES = ["announcements"]

_EXPECTED_COLUMNS = {
    "announcements": {
        "id", "public_id", "author_id", "scope", "course_id", "group_id",
        "title", "body", "status", "published_at", "withdrawn_at", "version",
        "created_at", "updated_at",
    },
}

_KINDS_BEFORE = (
    "enrollment_activated",
    "enrollment_withdrawn",
    "teacher_assignment_activated",
    "teacher_assignment_removed",
    "schedule_changed",
    "lesson_published",
    "material_available",
)
_KINDS_AFTER = _KINDS_BEFORE + ("announcement_published",)


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m09_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


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
    # Exactly one head. Phase 4 / M10, M11 and then M12 follow this
    # revision, so the single head is now M12's rather than this one.
    assert revisions - {p for p in parents.values() if p is not None} == {
        "f3c8a1d5e927"
    }
    # No revision is claimed as the parent of two others (no branch).
    claimed = [p for p in parents.values() if p is not None]
    assert len(claimed) == len(set(claimed))
    # Exactly one root.
    assert len([r for r, p in parents.items() if p is None]) == 1


def _code(source):
    """The migration's executable body, without its module docstring.

    The docstring legitimately *names* the operations this revision does
    not perform, so scanning the whole file for those words would fail on
    the explanation rather than on any code.
    """
    return source.split("from alembic import op", 1)[1]


def test_migration_creates_exactly_one_table_and_touches_one_existing_constraint():
    _, source = _load_migration()
    code = _code(source)
    assert re.findall(r"op\.create_table\('([^']+)'", code) == _NEW_TABLES
    # The ONLY existing object this revision touches is the notification
    # kind CHECK: no column is added, altered or dropped anywhere.
    for forbidden in ("add_column", "drop_column", "alter_column('", "op.bulk_insert"):
        assert forbidden not in code, forbidden
    assert "ondelete" not in code
    assert "onupdate" not in code
    # The only data statement in the whole revision is the downgrade's
    # removal of the rows the narrowed CHECK could not accept.
    executes = re.findall(r"op\.execute\(\s*\n?\s*\"([^\"]+)\"", code)
    assert executes == [
        "DELETE FROM notifications WHERE kind = 'announcement_published'"
    ]
    upgrade = code.split("def downgrade():")[0]
    assert "op.execute" not in upgrade
    assert "drop_table" not in upgrade
    # The only foreign-key targets are the three existing parents.
    assert sorted(set(re.findall(r"\['([a-z_]+\.id)'\]", code))) == [
        "courses.id",
        "groups.id",
        "users.id",
    ]


def test_migration_declares_every_expected_column_and_constraint():
    _, source = _load_migration()
    block = source.split("op.create_table('announcements',", 1)[1].split("\n    )", 1)[0]
    assert set(re.findall(r"sa\.Column\('([^']+)'", block)) == _EXPECTED_COLUMNS[
        "announcements"
    ]
    for fragment in (
        "name='ck_announcements_scope_valid'",
        "name='ck_announcements_status_valid'",
        "name='ck_announcements_scope_target'",
        "name='ck_announcements_status_timestamps'",
        "name='ck_announcements_version_positive'",
        "sa.UniqueConstraint('public_id')",
        "'ix_announcements_scope_status_published'",
        "'ix_announcements_course_status_published'",
        "'ix_announcements_group_status_published'",
        "'ix_announcements_author_status_created'",
        "'ix_announcements_status_scope_created'",
        "scope IN ('center', 'course', 'group')",
        "status IN ('draft', 'published', 'withdrawn')",
        "withdrawn_at >= published_at",
        "sa.String(length=5000)",
    ):
        assert fragment in source, fragment


def test_the_notification_kind_lists_match_the_application_enum():
    module, _ = _load_migration()
    application = tuple(kind.value for kind in NotificationKind)
    # This revision's list is exactly the enum as it stood after M09;
    # Phase 4 / M11 and then M12 each appended one kind in its own revision.
    assert module._KINDS_AFTER == application[: len(module._KINDS_AFTER)]
    assert application[len(module._KINDS_AFTER):] == (
        "message_received",
        "discussion_topic_created",
    )
    assert set(module._KINDS_AFTER) - set(module._KINDS_BEFORE) == {
        "announcement_published"
    }


def test_the_two_backends_get_genuinely_different_constraint_replacements():
    """SQLite table-alter behaviour is **not** assumed to match MySQL's.

    MySQL drops and re-adds the CHECK in place and never copies a row;
    SQLite has no such statement at all and must rebuild the table. The
    revision therefore branches on the dialect rather than pointing one
    ``batch_alter_table`` at both, and the SQLite path supplies an
    explicit ``copy_from`` so the rebuild cannot reflect -- and thereby
    reinstate -- the old CHECK.
    """
    _, source = _load_migration()
    code = _code(source)
    assert "op.get_bind().dialect.name == 'sqlite'" in code
    assert "copy_from=_notifications_table(values)" in code
    assert "recreate='always'" in code
    assert "op.drop_constraint(_KIND_CHECK_NAME, 'notifications', type_='check')" in code
    assert "op.create_check_constraint(" in code


def test_downgrade_reverses_both_steps(app):
    _, source = _load_migration()
    downgrade = source.split("def downgrade():")[1]
    assert re.findall(r"op\.drop_table\('([^']+)'", downgrade) == _NEW_TABLES
    assert "_replace_notification_kind_check(_KINDS_BEFORE)" in downgrade
    # The CHECK is narrowed BEFORE the table is dropped, and the rows it
    # could not accept are removed first.
    assert downgrade.index("DELETE FROM notifications") < downgrade.index(
        "_replace_notification_kind_check"
    )
    assert downgrade.index("_replace_notification_kind_check") < downgrade.index(
        "op.drop_table"
    )
    assert "drop_column" not in downgrade


def test_the_downgrade_drops_no_index_explicitly():
    """Tables only, and deliberately so: three of the five indexes lead
    with a foreign-key column, and MySQL refuses to drop such an index
    while the constraint exists (errno 1553). ``DROP TABLE`` removes a
    table's own indexes and constraints with it."""
    _, source = _load_migration()
    downgrade = source.split("def downgrade():")[1]
    assert "op.drop_index" not in downgrade


_PREREQ = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, role VARCHAR(32), status VARCHAR(32))",
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
    """CREATE TABLE enrollments (
        id INTEGER PRIMARY KEY,
        student_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        status VARCHAR(32) NOT NULL,
        FOREIGN KEY(student_id) REFERENCES users (id),
        FOREIGN KEY(group_id) REFERENCES groups (id)
    )""",
    """CREATE TABLE notifications (
        id INTEGER PRIMARY KEY,
        public_id VARCHAR(36) NOT NULL UNIQUE,
        recipient_id INTEGER NOT NULL,
        kind VARCHAR(48) NOT NULL,
        title VARCHAR(150) NOT NULL,
        message VARCHAR(500) NOT NULL,
        target_path VARCHAR(512) NOT NULL,
        created_at DATETIME NOT NULL,
        read_at DATETIME,
        CONSTRAINT ck_notifications_kind_valid CHECK (kind IN ('enrollment_activated',
            'enrollment_withdrawn', 'teacher_assignment_activated',
            'teacher_assignment_removed', 'schedule_changed', 'lesson_published',
            'material_available')),
        FOREIGN KEY(recipient_id) REFERENCES users (id)
    )""",
    "CREATE INDEX ix_notifications_recipient_created_id ON notifications"
    " (recipient_id, created_at, id)",
    "CREATE INDEX ix_notifications_recipient_unread_created ON notifications"
    " (recipient_id, read_at, created_at)",
    "INSERT INTO users (id, role, status) VALUES (11, 'student', 'active')",
    "INSERT INTO users (id, role, status) VALUES (12, 'teacher', 'active')",
    "INSERT INTO users (id, role, status) VALUES (13, 'administrator', 'active')",
    "INSERT INTO academic_terms (id, name, status) VALUES (1, 'Term 1', 'active')",
    "INSERT INTO levels (id, name, status) VALUES (1, 'Level 1', 'active')",
    "INSERT INTO courses (id, public_id, level_id, title, status)"
    " VALUES (2, 'course-public-1', 1, 'English', 'active')",
    "INSERT INTO groups (id, public_id, academic_term_id, course_id, name, status)"
    " VALUES (7, 'group-public-1', 1, 2, 'Group A', 'active')",
    "INSERT INTO enrollments (id, student_id, group_id, status)"
    " VALUES (4, 11, 7, 'active')",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message,"
    " target_path, created_at, read_at) VALUES (1, 'np-1', 11, 'lesson_published',"
    " 'New lesson published', 'The lesson is available.', '/student/dashboard',"
    " '2026-05-01 12:00:00', NULL)",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message,"
    " target_path, created_at, read_at) VALUES (2, 'np-2', 12, 'schedule_changed',"
    " 'Class schedule changed', 'A class time was added.', '/teacher/dashboard',"
    " '2026-05-02 08:30:00', '2026-05-02 09:00:00')",
    "INSERT INTO notifications (id, public_id, recipient_id, kind, title, message,"
    " target_path, created_at, read_at) VALUES (3, 'np-3', 11, 'material_available',"
    " 'New material available', 'A material was added.',"
    " '/student/dashboard#material-x', '2026-05-03 07:15:00', NULL)",
]

_PRESERVED = (
    ("users", 3),
    ("academic_terms", 1),
    ("levels", 1),
    ("courses", 1),
    ("groups", 1),
    ("enrollments", 1),
    ("notifications", 3),
)

_ANNOUNCEMENT_COLUMNS = (
    "id, public_id, author_id, scope, course_id, group_id, title, body, status,"
    " published_at, withdrawn_at, version, created_at, updated_at"
)
_NOTIFICATION_COLUMNS = (
    "id, public_id, recipient_id, kind, title, message, target_path, created_at, read_at"
)
_MOMENT = "'2026-05-13 09:00:00'"
_LATER = "'2026-05-13 10:30:00'"


def _notification_snapshot(conn):
    return conn.execute(
        sa.text(f"SELECT {_NOTIFICATION_COLUMNS} FROM notifications ORDER BY id")
    ).fetchall()


def test_migration_applies_and_reverses_on_isolated_sqlite():
    """Execute both directions against a temporary database seeded with
    the prerequisite tables and representative existing rows.

    Foreign keys stay **enforced** throughout, and
    ``PRAGMA foreign_key_check`` is asserted after each direction --
    which matters here because the SQLite path really does rebuild
    ``notifications``. Every probe row is removed again before the
    downgrade, so nothing this test inserted is left behind.
    """
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

                before = {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                }
                notifications_before = _notification_snapshot(conn)

                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.commit()
                assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []

                schema = inspect(conn)
                assert "announcements" in set(schema.get_table_names())
                assert {
                    c["name"] for c in schema.get_columns("announcements")
                } == _EXPECTED_COLUMNS["announcements"]
                assert {
                    c["name"] for c in schema.get_check_constraints("announcements")
                } == {
                    "ck_announcements_scope_valid",
                    "ck_announcements_status_valid",
                    "ck_announcements_scope_target",
                    "ck_announcements_status_timestamps",
                    "ck_announcements_version_positive",
                }
                assert {
                    fk["constrained_columns"][0]: fk["referred_table"]
                    for fk in schema.get_foreign_keys("announcements")
                } == {
                    "author_id": "users",
                    "course_id": "courses",
                    "group_id": "groups",
                }
                assert {
                    index["name"] for index in schema.get_indexes("announcements")
                } >= {
                    "ix_announcements_scope_status_published",
                    "ix_announcements_course_status_published",
                    "ix_announcements_group_status_published",
                    "ix_announcements_author_status_created",
                    "ix_announcements_status_scope_created",
                }

                # THE data-preservation claim, executed: every existing
                # notification survives the rebuild byte for byte, in the
                # same order, with its read state intact...
                assert _notification_snapshot(conn) == notifications_before
                after = {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                }
                assert after == before == dict(_PRESERVED)
                # ...and the rebuilt table keeps BOTH of its indexes and
                # its uniqueness rule.
                assert {
                    index["name"] for index in inspect(conn).get_indexes("notifications")
                } >= {
                    "ix_notifications_recipient_created_id",
                    "ix_notifications_recipient_unread_created",
                }
                try:
                    conn.execute(sa.text(
                        f"INSERT INTO notifications ({_NOTIFICATION_COLUMNS}) VALUES"
                        f" (99, 'np-1', 11, 'lesson_published', 'T', 'M', '/student/x',"
                        f" {_MOMENT}, NULL)"
                    ))
                    raise AssertionError("public_id uniqueness was lost in the rebuild")
                except sa.exc.IntegrityError:
                    conn.rollback()

                # ...and the new table starts EMPTY: no announcement is
                # invented for any existing group, course or term.
                assert conn.execute(
                    sa.text("SELECT count(*) FROM announcements")
                ).scalar_one() == 0

                # The widened notification CHECK accepts the new kind...
                conn.execute(sa.text(
                    f"INSERT INTO notifications ({_NOTIFICATION_COLUMNS}) VALUES"
                    f" (50, 'np-50', 11, 'announcement_published', 'New announcement',"
                    f" 'A new center announcement was published.',"
                    f" '/student/announcements/a-1', {_MOMENT}, NULL)"
                ))
                conn.commit()
                # ...and still refuses one nobody approved.
                try:
                    conn.execute(sa.text(
                        f"INSERT INTO notifications ({_NOTIFICATION_COLUMNS}) VALUES"
                        f" (51, 'np-51', 11, 'announcement_withdrawn', 'X', 'Y',"
                        f" '/student/x', {_MOMENT}, NULL)"
                    ))
                    raise AssertionError("the widened CHECK accepts anything")
                except sa.exc.IntegrityError:
                    conn.rollback()

                # Every announcement rule is really enforced.
                conn.execute(sa.text(
                    f"INSERT INTO announcements ({_ANNOUNCEMENT_COLUMNS}) VALUES"
                    f" (1, 'a-1', 13, 'center', NULL, NULL, 'Holiday', 'Closed.',"
                    f" 'draft', NULL, NULL, 1, {_MOMENT}, {_MOMENT})"
                ))
                conn.commit()
                for values, rule in (
                    ("(2, 'a-1', 13, 'center', NULL, NULL, 'X', 'Y', 'draft', NULL,"
                     " NULL, 1,", "public_id"),
                    ("(3, 'a-3', 13, 'level', NULL, NULL, 'X', 'Y', 'draft', NULL,"
                     " NULL, 1,", "scope CHECK"),
                    ("(4, 'a-4', 13, 'center', NULL, NULL, 'X', 'Y', 'archived', NULL,"
                     " NULL, 1,", "status CHECK"),
                    ("(5, 'a-5', 13, 'center', 2, NULL, 'X', 'Y', 'draft', NULL,"
                     " NULL, 1,", "center carries no target"),
                    ("(6, 'a-6', 13, 'course', NULL, NULL, 'X', 'Y', 'draft', NULL,"
                     " NULL, 1,", "course needs a course_id"),
                    ("(7, 'a-7', 13, 'group', NULL, 7, 'X', 'Y', 'published', NULL,"
                     " NULL, 1,", "published needs published_at"),
                    (f"(8, 'a-8', 13, 'group', NULL, 7, 'X', 'Y', 'withdrawn', {_LATER},"
                     f" {_MOMENT}, 1,", "withdrawn_at >= published_at"),
                    ("(9, 'a-9', 13, 'center', NULL, NULL, 'X', 'Y', 'draft', NULL,"
                     " NULL, 0,", "version > 0"),
                    (f"(10, 'a-10', 13, 'group', 2, 7, 'X', 'Y', 'draft', NULL,"
                     f" NULL, 1,", "group carries exactly one target"),
                ):
                    try:
                        conn.execute(sa.text(
                            f"INSERT INTO announcements ({_ANNOUNCEMENT_COLUMNS})"
                            f" VALUES {values} {_MOMENT}, {_MOMENT})"
                        ))
                        raise AssertionError(f"{rule} was not enforced")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                # Every legitimate shape is accepted.
                for index, values in enumerate(
                    (
                        "'course', 2, NULL, 'X', 'Y', 'draft', NULL, NULL",
                        f"'group', NULL, 7, 'X', 'Y', 'published', {_MOMENT}, NULL",
                        f"'center', NULL, NULL, 'X', 'Y', 'withdrawn', {_MOMENT},"
                        f" {_LATER}",
                        f"'center', NULL, NULL, 'X', 'Y', 'withdrawn', {_MOMENT},"
                        f" {_MOMENT}",
                    ),
                    start=20,
                ):
                    conn.execute(sa.text(
                        f"INSERT INTO announcements ({_ANNOUNCEMENT_COLUMNS}) VALUES"
                        f" ({index}, 'a-ok-{index}', 13, {values}, 1, {_MOMENT},"
                        f" {_MOMENT})"
                    ))
                conn.commit()

                # No cascade: the parents cannot be deleted out from under
                # the announcements.
                for statement in (
                    "DELETE FROM groups WHERE id = 7",
                    "DELETE FROM courses WHERE id = 2",
                    "DELETE FROM users WHERE id = 13",
                ):
                    try:
                        conn.execute(sa.text(statement))
                        raise AssertionError(f"no-cascade was not enforced: {statement}")
                    except sa.exc.IntegrityError:
                        conn.rollback()

                # Leave no probe row behind. The 'announcement_published'
                # notification is deliberately LEFT so the downgrade's own
                # cleanup is what removes it.
                conn.execute(sa.text("DELETE FROM announcements"))
                conn.commit()

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()
                assert conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall() == []

                after_down = set(inspect(conn).get_table_names())
                assert "announcements" not in after_down
                # Every pre-existing notification survives the whole round
                # trip untouched; only the row of the removed kind is gone.
                assert _notification_snapshot(conn) == notifications_before
                assert {
                    table: conn.execute(
                        sa.text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                    for table, _ in _PRESERVED
                } == dict(_PRESERVED)
                # The narrowed CHECK is back and is really enforced.
                try:
                    conn.execute(sa.text(
                        f"INSERT INTO notifications ({_NOTIFICATION_COLUMNS}) VALUES"
                        f" (60, 'np-60', 11, 'announcement_published', 'X', 'Y',"
                        f" '/student/x', {_MOMENT}, NULL)"
                    ))
                    raise AssertionError("the narrowed CHECK was not restored")
                except sa.exc.IntegrityError:
                    conn.rollback()
                # ...and the ordinary kinds still insert fine.
                conn.execute(sa.text(
                    f"INSERT INTO notifications ({_NOTIFICATION_COLUMNS}) VALUES"
                    f" (61, 'np-61', 11, 'lesson_published', 'X', 'Y', '/student/x',"
                    f" {_MOMENT}, NULL)"
                ))
                conn.rollback()
        finally:
            engine.dispose()


def test_models_and_migration_agree_on_the_new_table(app):
    _, source = _load_migration()
    with app.app_context():
        inspector = inspect(db.engine)
        block = source.split("op.create_table('announcements',", 1)[1].split(
            "\n    )", 1
        )[0]
        declared = dict(
            re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", block)
        )
        actual = {
            column["name"]: str(column["nullable"])
            for column in inspector.get_columns("announcements")
        }
        declared.pop("id", None)
        actual.pop("id", None)
        assert declared == actual


def test_mysql_ddl_compiles_without_a_connection():
    dialect = mysql.dialect()
    ddl = str(CreateTable(Announcement.__table__).compile(dialect=dialect))

    assert "FOREIGN KEY(author_id) REFERENCES users (id)" in ddl
    assert "FOREIGN KEY(course_id) REFERENCES courses (id)" in ddl
    assert "FOREIGN KEY(group_id) REFERENCES `groups` (id)" in ddl
    assert "UNIQUE (public_id)" in ddl
    assert "title VARCHAR(150) NOT NULL" in ddl
    assert "body VARCHAR(5000) NOT NULL" in ddl
    assert "scope VARCHAR(32) NOT NULL" in ddl
    assert "status VARCHAR(32) NOT NULL" in ddl
    for name in (
        "ck_announcements_scope_valid",
        "ck_announcements_status_valid",
        "ck_announcements_scope_target",
        "ck_announcements_status_timestamps",
        "ck_announcements_version_positive",
    ):
        assert name in ddl, name
    # No cascade, and no fractional-precision DATETIME.
    assert "ON DELETE" not in ddl
    assert "ON UPDATE" not in ddl
    assert "BIGINT" in ddl
    assert "DATETIME(" not in ddl
    # No MySQL ENUM column: the two closed sets are VARCHAR + CHECK, the
    # convention every other closed-set column in this project uses.
    assert "ENUM(" not in ddl
    # An announcement carries no number that could look like a grade.
    assert "FLOAT" not in ddl.upper()
    assert "DOUBLE" not in ddl.upper()
    assert "NUMERIC" not in ddl.upper()


def test_the_new_table_uses_the_deployment_engine_and_charset(app):
    """The engine and charset are a **deployment** property, not something
    this migration sets: no ``mysql_engine`` / ``mysql_charset`` argument
    is declared, so the table inherits the MySQL server's (or the
    database's) default exactly as every earlier table in this project
    does. Asserted here so a future silent divergence is caught, and
    verified for real against development MySQL separately -- SQLite can
    demonstrate neither."""
    assert "mysql_engine" not in Announcement.__table__.kwargs
    assert "mysql_charset" not in Announcement.__table__.kwargs
