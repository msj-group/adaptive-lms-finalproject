"""M14 persistence: the ``Notification`` model's shape, constraints,
index, foreign key, UUID identity and UTC timestamps, plus the shape of
migration ``023a5f5814a8`` itself.

Backend note: the suite builds the schema with ``db.create_all()`` on
SQLite, so these tests prove the *models* match what the migration is
written to produce -- they are not proof that the migration runs on
MySQL. That is checked separately against the real development database
(see the M14 report).
"""

import importlib.util
import pathlib
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Notification, NotificationKind, UserRole
from tests import notification_fixtures as fx

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "023a5f5814a8"
_DOWN_REVISION = "ed1e6c7c2548"


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m14_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


# ===========================================================================
# Migration shape
# ===========================================================================


def test_migration_revision_identifiers():
    module, _ = _load_migration()
    assert module.revision == _REVISION
    assert module.down_revision == _DOWN_REVISION
    assert module.branch_labels is None
    assert module.depends_on is None


def test_migration_is_a_single_additive_table():
    _, source = _load_migration()
    created = re.findall(r"op\.create_table\('([^']+)'", source)
    assert created == ["notifications"]

    # It must not touch anything that already exists.
    for forbidden in (
        "add_column",
        "drop_column",
        "alter_column",
        "execute(",
        "op.bulk_insert",
        "create_foreign_key",
    ):
        assert forbidden not in source, forbidden
    assert set(re.findall(r"batch_alter_table\('([^']+)'", source)) == {"notifications"}

    # Symmetric downgrade: both indexes are dropped, in the reverse of
    # the order they are created, then the one table.
    assert source.count("op.drop_table('notifications')") == 1
    created_indexes = re.findall(r"create_index\('([^']+)'", source)
    dropped_indexes = re.findall(r"drop_index\('([^']+)'", source)
    assert created_indexes == [
        "ix_notifications_recipient_created_id",
        "ix_notifications_recipient_unread_created",
    ]
    assert dropped_indexes == list(reversed(created_indexes))


#: The seven kinds THIS revision created. Phase 4 / M09 later added an
#: eighth (``announcement_published``) by replacing this CHECK in its own
#: revision, which is the only way a kind may ever be added -- so this
#: migration must still declare exactly the seven it was written with, and
#: must NOT be edited to mention a later one.
_M14_KINDS = (
    "enrollment_activated",
    "enrollment_withdrawn",
    "teacher_assignment_activated",
    "teacher_assignment_removed",
    "schedule_changed",
    "lesson_published",
    "material_available",
)


def test_migration_declares_the_kind_check_and_both_composite_indexes():
    _, source = _load_migration()
    assert "ck_notifications_kind_valid" in source
    for kind in _M14_KINDS:
        assert f"'{kind}'" in source
    assert "'announcement_published'" not in source
    # Every kind the application knows today is either one of this
    # revision's seven or was added by a later revision.
    assert set(_M14_KINDS) <= set(fx.kinds())
    assert (
        "create_index('ix_notifications_recipient_unread_created', "
        "['recipient_id', 'read_at', 'created_at']" in source
    )
    assert (
        "create_index('ix_notifications_recipient_created_id', "
        "['recipient_id', 'created_at', 'id']" in source
    )
    assert "sa.ForeignKeyConstraint(['recipient_id'], ['users.id'], )" in source
    assert "sa.UniqueConstraint('public_id')" in source


# ===========================================================================
# Model / schema alignment
# ===========================================================================


def test_columns_types_and_nullability(app):
    with app.app_context():
        columns = {c["name"]: c for c in inspect(db.engine).get_columns("notifications")}
        assert set(columns) == {
            "id",
            "public_id",
            "recipient_id",
            "kind",
            "title",
            "message",
            "target_path",
            "created_at",
            "read_at",
        }
        for name in ("public_id", "recipient_id", "kind", "title", "message",
                     "target_path", "created_at"):
            assert columns[name]["nullable"] is False, name
        assert columns["read_at"]["nullable"] is True

        widths = {
            "public_id": 36,
            "kind": 48,
            "title": 150,
            "message": 500,
            "target_path": 512,
        }
        for name, width in widths.items():
            assert columns[name]["type"].length == width, name


def test_both_composite_indexes_and_unique_public_id_exist(app):
    """Two indexes, because the two inbox filters sort on different key
    suffixes: the unread filter orders within ``read_at``, while the
    "all" filter orders on ``created_at``/``id`` directly after the
    ``recipient_id`` equality -- which the unread index cannot satisfy
    without a sort step."""
    with app.app_context():
        insp = inspect(db.engine)
        indexes = {i["name"]: i for i in insp.get_indexes("notifications")}

        expected = {
            "ix_notifications_recipient_unread_created": [
                "recipient_id",
                "read_at",
                "created_at",
            ],
            "ix_notifications_recipient_created_id": [
                "recipient_id",
                "created_at",
                "id",
            ],
        }
        for name, columns in expected.items():
            assert name in indexes, name
            assert indexes[name]["column_names"] == columns, name
            assert not indexes[name]["unique"], name

        uniques = insp.get_unique_constraints("notifications")
        assert any(u["column_names"] == ["public_id"] for u in uniques)


def test_foreign_key_targets_users(app):
    with app.app_context():
        fks = inspect(db.engine).get_foreign_keys("notifications")
        assert len(fks) == 1
        assert fks[0]["constrained_columns"] == ["recipient_id"]
        assert fks[0]["referred_table"] == "users"
        assert fks[0]["referred_columns"] == ["id"]
        # No cascade: a notification is a historical record, never
        # something a parent row deletes.
        assert not fks[0].get("options", {}).get("ondelete")


def test_public_id_defaults_to_a_uuid_and_is_unique(app):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        first = fx.notification(student)
        second = fx.notification(student)
        assert uuid.UUID(first.public_id)
        assert first.public_id != second.public_id

        clash = Notification(
            recipient_id=student.id,
            public_id=first.public_id,
            kind=NotificationKind.SCHEDULE_CHANGED.value,
            title="t",
            message="m",
            target_path="/student/dashboard",
        )
        db.session.add(clash)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_created_at_defaults_to_utc_and_read_at_starts_null(app):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        row = Notification(
            recipient_id=student.id,
            kind=NotificationKind.LESSON_PUBLISHED.value,
            title="t",
            message="m",
            target_path="/student/dashboard",
        )
        db.session.add(row)
        db.session.commit()
        after = datetime.now(timezone.utc).replace(tzinfo=None)

        assert row.read_at is None
        assert row.is_unread is True
        # Stored naive; the value must be UTC, not local wall-clock.
        assert before - timedelta(seconds=5) <= row.created_at <= after + timedelta(seconds=5)


def test_recipient_id_is_required(app):
    with app.app_context():
        row = Notification(
            kind=NotificationKind.SCHEDULE_CHANGED.value,
            title="t",
            message="m",
            target_path="/student/dashboard",
        )
        db.session.add(row)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_recipient_must_reference_an_existing_user(app):
    with app.app_context():
        row = Notification(
            recipient_id=999999,
            kind=NotificationKind.SCHEDULE_CHANGED.value,
            title="t",
            message="m",
            target_path="/student/dashboard",
        )
        db.session.add(row)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ===========================================================================
# Kind: enforced in the application AND in the database
# ===========================================================================


def test_every_declared_kind_is_accepted(app):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        for kind in fx.kinds():
            fx.notification(student, kind=kind)
        assert Notification.query.count() == len(fx.kinds())


def test_application_validator_rejects_an_unknown_kind(app):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        with pytest.raises(ValueError):
            Notification(
                recipient_id=student.id,
                kind="announcement",
                title="t",
                message="m",
                target_path="/student/dashboard",
            )


def test_database_check_constraint_rejects_an_unknown_kind(app):
    """Bypass the ORM validator entirely -- the CHECK constraint is the
    final defense and must reject a hand-written row."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        from sqlalchemy import text

        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    "INSERT INTO notifications "
                    "(public_id, recipient_id, kind, title, message, target_path, created_at) "
                    "VALUES (:p, :r, 'announcement', 't', 'm', '/student/dashboard', :c)"
                ),
                # `created_at` is bound as an ISO string, not a Python
                # datetime: the raw DBAPI path would otherwise go through
                # sqlite3's deprecated default datetime adapter.
                {
                    "p": str(uuid.uuid4()),
                    "r": student.id,
                    "c": "2026-05-01 12:00:00",
                },
            )
            db.session.commit()
        db.session.rollback()


def test_kind_enum_matches_the_check_constraint_exactly(app):
    """The enum and the constraint are generated from one source, so they
    can never drift -- this is the regression that proves it."""
    from app.models.notification import _KIND_CHECK_SQL

    for kind in NotificationKind:
        assert f"'{kind.value}'" in _KIND_CHECK_SQL
    assert _KIND_CHECK_SQL.count("'") == 2 * len(list(NotificationKind))


def test_notification_carries_no_actor_or_source_columns(app):
    """M14 deliberately stores no actor identity and no polymorphic
    source pointer -- a notification names a place, not a row."""
    with app.app_context():
        names = {c["name"] for c in inspect(db.engine).get_columns("notifications")}
        for forbidden in (
            "actor_id",
            "created_by",
            "created_by_id",
            "source_type",
            "source_id",
            "payload",
            "data",
            "group_id",
            "lesson_id",
            "material_id",
        ):
            assert forbidden not in names, forbidden
