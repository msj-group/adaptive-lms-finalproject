"""Phase 4 / M01 Assignment model, database invariants, and the additive
``4f7c1d9b2e30`` migration.

The test backend is SQLite in memory built with ``db.create_all()``, so
the schema checks here prove the **models** match what the migration is
written to produce -- never that the migration runs on MySQL. SQLite does
enforce CHECK constraints, so the integrity assertions below are real,
but MySQL/InnoDB behaviour (collation, engine, index plans) is out of
reach and is not claimed.
"""

import importlib.util
import os
import pathlib
import re
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
    Group,
    Level,
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "4f7c1d9b2e30"
_DOWN_REVISION = "023a5f5814a8"

OPENS = datetime(2026, 5, 1, 8, 0)
DUE = datetime(2026, 5, 8, 23, 59)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _group(name="Group A", course_title="English"):
    term = AcademicTerm(
        name=f"Term {name}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
    )
    level = Level(name=f"Level {name}", display_order=0)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=course_title, level_id=level.id, display_order=0)
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=name, capacity=20
    )
    db.session.add(group)
    db.session.commit()
    return group


def _assignment(group, title="Task 1", opens_at=OPENS, due_at=DUE, status=None, published_at=None):
    row = Assignment(
        group_id=group.id,
        title=title,
        instructions="Do the work.",
        opens_at=opens_at,
        due_at=due_at,
        status=status or AssignmentStatus.DRAFT.value,
        published_at=published_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# defaults, relationship, and the deliberate absence of columns
# ---------------------------------------------------------------------------


def test_defaults_are_draft_with_no_publication_time(app):
    with app.app_context():
        group = _group()
        row = Assignment(
            group_id=group.id, title="T", instructions="I", opens_at=OPENS, due_at=DUE
        )
        db.session.add(row)
        db.session.commit()
        assert row.status == AssignmentStatus.DRAFT.value
        assert row.published_at is None
        assert row.created_at is not None and row.updated_at is not None
        # public_id is a server-generated UUID, never supplied by a caller.
        assert uuid.UUID(row.public_id)


def test_group_relationship_both_directions(app):
    with app.app_context():
        group = _group()
        row = _assignment(group)
        assert row.group.id == group.id
        assert [a.id for a in group.assignments] == [row.id]


def test_no_duplicated_hierarchy_or_teacher_owner_columns(app):
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns("assignments")}
        for forbidden in (
            "course_id",
            "level_id",
            "academic_term_id",
            "unit_id",
            "lesson_id",
            "teacher_id",
            "created_by_id",
            "created_by",
            "display_order",
        ):
            assert forbidden not in columns, forbidden
        assert columns == {
            "id",
            "public_id",
            "group_id",
            "title",
            "instructions",
            "opens_at",
            "due_at",
            "status",
            "published_at",
            "created_at",
            "updated_at",
        }


def test_assignment_status_is_its_own_closed_set(app):
    assert [s.value for s in AssignmentStatus] == ["draft", "published"]


# ---------------------------------------------------------------------------
# database invariants
# ---------------------------------------------------------------------------


def test_title_unique_within_one_group_including_drafts(app):
    with app.app_context():
        group = _group()
        _assignment(group, title="Shared")  # a DRAFT still occupies the title
        with pytest.raises(IntegrityError):
            _assignment(group, title="Shared", status=AssignmentStatus.PUBLISHED.value,
                        published_at=datetime(2026, 4, 1, 9, 0))
        db.session.rollback()


def test_same_title_allowed_in_another_group(app):
    with app.app_context():
        first = _group(name="G1", course_title="C1")
        second = _group(name="G2", course_title="C2")
        _assignment(first, title="Shared")
        _assignment(second, title="Shared")
        assert Assignment.query.count() == 2


def test_valid_window_is_accepted(app):
    with app.app_context():
        group = _group()
        row = _assignment(group, opens_at=OPENS, due_at=OPENS + timedelta(minutes=1))
        assert row.opens_at < row.due_at


@pytest.mark.parametrize(
    "opens_at, due_at",
    [
        (OPENS, OPENS),                      # equal -- rejected
        (DUE, OPENS),                        # reversed -- rejected
    ],
)
def test_equal_or_reversed_window_rejected_by_check(app, opens_at, due_at):
    with app.app_context():
        group = _group()
        with pytest.raises(IntegrityError):
            _assignment(group, opens_at=opens_at, due_at=due_at)
        db.session.rollback()


def test_status_closed_set_is_validated_in_the_application(app):
    with app.app_context():
        group = _group()
        with pytest.raises(ValueError):
            Assignment(
                group_id=group.id, title="T", instructions="I",
                opens_at=OPENS, due_at=DUE, status="archived",
            )


def test_status_closed_set_is_also_a_database_check(app):
    """The DB CHECK is the final defense -- proven by writing straight
    through the ORM validator with a raw INSERT."""
    with app.app_context():
        group = _group()
        with pytest.raises(IntegrityError):
            db.session.execute(
                sa.text(
                    "INSERT INTO assignments (public_id, group_id, title, instructions, "
                    "opens_at, due_at, status, published_at, created_at, updated_at) "
                    "VALUES (:pid, :gid, 'T', 'I', :opens, :due, 'archived', NULL, "
                    ":now, :now)"
                ),
                {
                    # Bound as ISO strings, not datetime objects: a raw
                    # textual INSERT would otherwise go through the
                    # sqlite3 default datetime adapter, deprecated since
                    # Python 3.12 and an error under the strict run.
                    "pid": str(uuid.uuid4()), "gid": group.id,
                    "opens": OPENS.isoformat(sep=" "),
                    "due": DUE.isoformat(sep=" "),
                    "now": OPENS.isoformat(sep=" "),
                },
            )
            db.session.commit()
        db.session.rollback()


def test_draft_with_a_publication_time_is_rejected(app):
    with app.app_context():
        group = _group()
        with pytest.raises(IntegrityError):
            _assignment(group, status=AssignmentStatus.DRAFT.value,
                        published_at=datetime(2026, 4, 1, 9, 0))
        db.session.rollback()


def test_published_without_a_publication_time_is_rejected(app):
    with app.app_context():
        group = _group()
        with pytest.raises(IntegrityError):
            _assignment(group, status=AssignmentStatus.PUBLISHED.value, published_at=None)
        db.session.rollback()


def test_public_id_is_unique(app):
    with app.app_context():
        group = _group()
        first = _assignment(group, title="A")
        duplicate = Assignment(
            group_id=group.id, title="B", instructions="I",
            opens_at=OPENS, due_at=DUE, public_id=first.public_id,
        )
        db.session.add(duplicate)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# ---------------------------------------------------------------------------
# indexes -- exactly the two query-driven ones, and no redundant FK index
# ---------------------------------------------------------------------------


def test_only_the_two_query_driven_indexes_exist(app):
    with app.app_context():
        indexes = {
            i["name"]: list(i["column_names"])
            for i in inspect(db.engine).get_indexes("assignments")
        }
        assert indexes == {
            "ix_assignments_group_due_id": ["group_id", "due_at", "id"],
            "ix_assignments_group_status_opens_due": [
                "group_id", "status", "opens_at", "due_at"
            ],
        }


def test_no_redundant_single_column_group_id_index(app):
    """``uq_assignments_group_title`` and both composite indexes already
    start with ``group_id``, so the foreign key has a usable leftmost
    prefix and must not carry a duplicate of its own."""
    with app.app_context():
        insp = inspect(db.engine)
        for index in insp.get_indexes("assignments"):
            assert list(index["column_names"]) != ["group_id"], index["name"]
        uniques = {
            u["name"]: list(u["column_names"]) for u in insp.get_unique_constraints("assignments")
        }
        assert uniques["uq_assignments_group_title"] == ["group_id", "title"]


# ---------------------------------------------------------------------------
# migration 4f7c1d9b2e30
# ---------------------------------------------------------------------------


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m01_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_revision_identifiers():
    module, _ = _load_migration()
    assert module.revision == _REVISION
    assert module.down_revision == _DOWN_REVISION
    assert module.branch_labels is None


def test_migration_creates_only_assignments_and_alters_nothing(app):
    _, source = _load_migration()
    created = re.findall(r"op\.create_table\('([^']+)'", source)
    assert created == ["assignments"]
    batch_targets = set(re.findall(r"batch_alter_table\('([^']+)'", source))
    assert batch_targets == {"assignments"}
    # No existing table is touched, and there is no data backfill.
    for forbidden in ("add_column", "drop_column", "alter_column", "execute(",
                      "create_foreign_key", "'groups'", '"groups"'):
        assert forbidden not in source, forbidden


def test_migration_downgrade_is_symmetric():
    _, source = _load_migration()
    upgrade_src, downgrade_src = source.split("def downgrade():")
    created = re.findall(r"create_index\('([^']+)'", upgrade_src)
    dropped = re.findall(r"drop_index\('([^']+)'", downgrade_src)
    assert created == [
        "ix_assignments_group_due_id",
        "ix_assignments_group_status_opens_due",
    ]
    assert dropped == list(reversed(created))
    assert "op.drop_table('assignments')" in downgrade_src


def test_migration_applies_and_reverses_on_an_isolated_temporary_database():
    """Runs the real ``upgrade()`` / ``downgrade()`` against a throwaway
    SQLite file -- never the development MySQL database, which this Part
    is not authorized to touch. This proves the operations are internally
    consistent and that the downgrade is genuinely reversible; it proves
    nothing about MySQL/InnoDB DDL.
    """
    module, _ = _load_migration()
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + os.path.join(tmp, "probe.db").replace(os.sep, "/")
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                # The FK target must exist; nothing else from the schema is needed.
                conn.execute(sa.text("CREATE TABLE groups (id INTEGER PRIMARY KEY)"))
                conn.commit()

                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                conn.commit()

                insp = sa.inspect(conn)
                assert sorted(insp.get_table_names()) == ["assignments", "groups"]
                assert sorted(i["name"] for i in insp.get_indexes("assignments")) == [
                    "ix_assignments_group_due_id",
                    "ix_assignments_group_status_opens_due",
                ]
                assert [c["name"] for c in insp.get_columns("assignments")] == [
                    "id", "public_id", "group_id", "title", "instructions",
                    "opens_at", "due_at", "status", "published_at",
                    "created_at", "updated_at",
                ]

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                conn.commit()
                assert sa.inspect(conn).get_table_names() == ["groups"]
        finally:
            engine.dispose()


def test_models_and_migration_agree_on_columns_and_nullability(app):
    """The migration's column list/nullability matches what the models
    actually build (the test backend runs ``create_all()``)."""
    _, source = _load_migration()
    declared = dict(
        re.findall(r"sa\.Column\('([^']+)',.*?nullable=(True|False)", source)
    )
    with app.app_context():
        actual = {
            c["name"]: str(c["nullable"])
            for c in inspect(db.engine).get_columns("assignments")
        }
    # The primary key is NOT NULL in both, but SQLite reports it differently;
    # compare every other column exactly.
    declared.pop("id", None)
    actual.pop("id", None)
    assert declared == actual


def test_groups_table_is_not_altered_by_this_milestone(app):
    """``Group.assignments`` is the ORM inverse of the FK created on
    ``assignments`` -- the parent table needs no new column."""
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns("groups")}
        assert columns == {
            "id", "public_id", "academic_term_id", "course_id", "name", "code",
            "capacity", "status", "created_at", "updated_at",
        }


def test_academic_status_is_not_valid_for_an_assignment(app):
    """An Assignment is never ``archived`` -- the academic lifecycle enum
    has no meaning here."""
    with app.app_context():
        group = _group()
        with pytest.raises(ValueError):
            Assignment(
                group_id=group.id, title="T", instructions="I",
                opens_at=OPENS, due_at=DUE, status=AcademicStatus.ARCHIVED.value,
            )


def test_utc_publication_time_round_trips(app):
    with app.app_context():
        group = _group()
        stamped = datetime.now(timezone.utc)
        row = _assignment(
            group, status=AssignmentStatus.PUBLISHED.value, published_at=stamped
        )
        db.session.expire(row)
        assert row.published_at is not None
