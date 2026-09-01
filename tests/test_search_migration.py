"""M13 migration ``ed1e6c7c2548`` -- it is additive, descends from the
M12 head, and touches only the three nullable ``search_keywords``
columns. Also checks the live model/schema alignment (the test backend
builds the schema with ``db.create_all()``, so this proves the *models*
match what the migration is written to produce)."""

import importlib.util
import pathlib
import re

from sqlalchemy import inspect

from app.extensions import db

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
_REVISION = "ed1e6c7c2548"
_DOWN_REVISION = "8319232a5609"
_TABLES = ("units", "lessons", "materials")


def _load_migration():
    path = next(p for p in _MIGRATIONS.glob("*.py") if _REVISION in p.name)
    spec = importlib.util.spec_from_file_location(f"m13_{_REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_revision_identifiers():
    module, _ = _load_migration()
    assert module.revision == _REVISION
    assert module.down_revision == _DOWN_REVISION
    assert module.branch_labels is None


def test_migration_only_adds_the_three_nullable_columns():
    _, source = _load_migration()
    add_cols = re.findall(r"add_column\(\s*sa\.Column\('([^']+)',\s*([^,]+),\s*nullable=(\w+)", source)
    assert len(add_cols) == 3
    assert {name for name, _type, _null in add_cols} == {"search_keywords"}
    assert {typ.strip() for _n, typ, _null in add_cols} == {"sa.String(length=500)"}
    assert {null for _n, _t, null in add_cols} == {"True"}

    # every batch_alter_table target is one of the three known tables
    targets = set(re.findall(r"batch_alter_table\('([^']+)'", source))
    assert targets == set(_TABLES)

    # nothing structural beyond add/drop column
    for forbidden in ("create_table", "drop_table", "create_index", "drop_index",
                      "create_foreign_key", "create_check_constraint", "create_unique_constraint",
                      "alter_column", "execute("):
        assert forbidden not in source, forbidden

    drop_cols = re.findall(r"drop_column\('([^']+)'\)", source)
    assert drop_cols == ["search_keywords"] * 3  # symmetric downgrade


def test_models_expose_nullable_search_keywords_varchar_500(app):
    with app.app_context():
        insp = inspect(db.engine)
        for table in _TABLES:
            cols = {c["name"]: c for c in insp.get_columns(table)}
            assert "search_keywords" in cols, table
            col = cols["search_keywords"]
            assert col["nullable"] is True
            assert getattr(col["type"], "length", None) == 500


def test_courses_table_unchanged(app):
    with app.app_context():
        insp = inspect(db.engine)
        assert "search_keywords" not in {c["name"] for c in insp.get_columns("courses")}
