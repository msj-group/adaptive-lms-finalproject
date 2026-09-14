"""Phase 5 / M02 -- the FeePlan and FeePlanItem models.

Application validators first (normalized text, the fixed currency, closed
sets, positive versions, exact money that refuses a float), then every
database CHECK / UNIQUE / foreign key driven directly with raw SQL so the
validators cannot mask a missing constraint, then the absence of any
relationship or cascade that could delete a plan or an item.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

import tests.fee_plan_fixtures as fx
from app.extensions import db
from app.models import FeePlan, FeePlanItem
from app.models.fee_plan import (
    TEXT_CONTROL,
    TEXT_MISSING,
    TEXT_TOO_LONG,
    normalize_fee_plan_description,
    normalize_fee_plan_name,
)
from app.models.fee_plan_item import (
    MAX_ACTIVE_FEE_PLAN_ITEMS,
    fee_plan_item_label_key,
    normalize_fee_plan_item_label,
)

_T0 = "'2026-05-01 09:00:00'"
_T1 = "'2026-05-02 09:00:00'"
_T2 = "'2026-05-03 09:00:00'"


# ===========================================================================
# Normalisation
# ===========================================================================


def test_names_and_labels_are_collapsed_to_one_trimmed_line():
    assert normalize_fee_plan_name("  Standard    plan ") == ("Standard plan", None)
    assert normalize_fee_plan_name("A  B") == ("A B", None)
    # A tab or a newline in a one-line field is a control character: refused,
    # exactly as every existing title normaliser in the project refuses it.
    assert normalize_fee_plan_name("Standard\tplan") == (None, TEXT_CONTROL)
    assert normalize_fee_plan_name("Standard plan\n") == (None, TEXT_CONTROL)
    assert normalize_fee_plan_item_label(" Course   fee ") == ("Course fee", None)
    assert normalize_fee_plan_name("خطة التسجيل") == ("خطة التسجيل", None)


@pytest.mark.parametrize(
    "raw, error",
    [
        (None, TEXT_MISSING),
        ("", TEXT_MISSING),
        ("   ", TEXT_MISSING),
        ("Plan\x00", TEXT_CONTROL),
        ("Plan\x1b[31m", TEXT_CONTROL),
        ("Plan\x85", TEXT_CONTROL),  # a C1 control
        ("Plan ‮flE", TEXT_CONTROL),  # right-to-left override
        ("Plan ⁦x⁩", TEXT_CONTROL),  # bidi isolates
        ("Plan two", TEXT_CONTROL),
        ("x" * 151, TEXT_TOO_LONG),
    ],
)
def test_bad_names_are_rejected_never_repaired(raw, error):
    assert normalize_fee_plan_name(raw) == (None, error)
    assert normalize_fee_plan_item_label(raw) == (None, error)


def test_descriptions_keep_lines_and_absent_is_none():
    assert normalize_fee_plan_description("One\r\n\tTwo\rThree ") == ("One\n\tTwo\nThree", None)
    assert normalize_fee_plan_description("   \r\n ") == (None, None)
    assert normalize_fee_plan_description(None) == (None, None)
    assert normalize_fee_plan_description("x\x07") == (None, TEXT_CONTROL)
    assert normalize_fee_plan_description("x" * 1001) == (None, TEXT_TOO_LONG)


def test_labels_compare_case_insensitively():
    assert fee_plan_item_label_key("Course Fee") == fee_plan_item_label_key("course fee")
    assert fee_plan_item_label_key("Course fee") != fee_plan_item_label_key("Course fees")
    assert MAX_ACTIVE_FEE_PLAN_ITEMS == 20


# ===========================================================================
# Application validators
# ===========================================================================


def test_a_plan_gets_a_public_id_the_fixed_currency_and_draft_defaults(app):
    with app.app_context():
        creator = fx.admin()
        first = fx.plan(creator)
        second = fx.plan(creator)
        assert len(first.public_id) == 36
        assert first.public_id != second.public_id
        assert first.currency_code == "LYD"
        assert first.status == fx.DRAFT and first.version == 1
        assert not first.has_been_activated and not first.can_be_reactivated


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", " Padded"),
        ("name", "Two  spaces"),
        ("name", ""),
        ("name", None),
        ("description", ""),
        ("description", " padded "),
        ("currency_code", "USD"),
        ("currency_code", "lyd"),
        ("status", "deleted"),
        ("status", "ACTIVE"),
        ("version", 0),
        ("version", True),
        ("version", "1"),
    ],
)
def test_plan_validators_refuse_bad_values(app, field, value):
    with app.app_context():
        with pytest.raises(ValueError):
            FeePlan(**{field: value})


@pytest.mark.parametrize(
    "field, value",
    [
        ("kind", "discount"),
        ("kind", "tax"),
        ("status", "deleted"),
        ("label", " padded"),
        ("label", ""),
        ("amount", 12.5),
        ("amount", 12),
        ("amount", Decimal("0")),
        ("amount", Decimal("-1")),
        ("amount", Decimal("100000")),
        ("amount", Decimal("1.00001")),
        ("amount", Decimal("NaN")),
        ("amount", "1,250"),
        ("version", 0),
    ],
)
def test_item_validators_refuse_bad_values(app, field, value):
    with app.app_context():
        with pytest.raises(ValueError):
            FeePlanItem(**{field: value})


def test_an_amount_round_trips_exactly_as_a_decimal(app):
    with app.app_context():
        owner = fx.plan(fx.admin())
        values = ["0.001", "99999.999", "12.3456", "1250.5", "0.1", "0.2"]
        public_ids = [fx.item(owner, amount=value).public_id for value in values]
        db.session.expire_all()
        stored = [fx.stored_item(ip).amount for ip in public_ids]
        assert all(isinstance(amount, Decimal) for amount in stored)
        assert stored == [Decimal(value) for value in values]
        assert [str(amount) for amount in stored] == [
            "0.0010", "99999.9990", "12.3456", "1250.5000", "0.1000", "0.2000"
        ]


# ===========================================================================
# Database constraints, driven with raw SQL
# ===========================================================================


_PLAN_COLUMNS = (
    "public_id", "name", "description", "currency_code", "status", "created_by_id",
    "first_activated_at", "first_activated_by_id", "status_changed_at",
    "status_changed_by_id", "version", "created_at", "updated_at",
)


def _plan_sql(actor_id, **overrides):
    values = {
        "public_id": f"'p-{fx._next()}'",
        "name": f"'Raw plan {fx._next()}'",
        "description": "NULL",
        "currency_code": "'LYD'",
        "status": "'draft'",
        "created_by_id": str(actor_id),
        "first_activated_at": "NULL",
        "first_activated_by_id": "NULL",
        "status_changed_at": "NULL",
        "status_changed_by_id": "NULL",
        "version": "1",
        "created_at": _T0,
        "updated_at": _T0,
    }
    values.update(overrides)
    return (
        f"INSERT INTO fee_plans ({', '.join(_PLAN_COLUMNS)}) VALUES "
        f"({', '.join(values[column] for column in _PLAN_COLUMNS)})"
    )


_ITEM_COLUMNS = (
    "public_id", "fee_plan_id", "kind", "label", "amount", "status", "removed_at",
    "removed_by_id", "version", "created_at", "updated_at",
)


def _item_sql(plan_id, **overrides):
    values = {
        "public_id": f"'i-{fx._next()}'",
        "fee_plan_id": str(plan_id),
        "kind": "'course'",
        "label": "'Raw item'",
        "amount": "10.5",
        "status": "'active'",
        "removed_at": "NULL",
        "removed_by_id": "NULL",
        "version": "1",
        "created_at": _T0,
        "updated_at": _T0,
    }
    values.update(overrides)
    return (
        f"INSERT INTO fee_plan_items ({', '.join(_ITEM_COLUMNS)}) VALUES "
        f"({', '.join(values[column] for column in _ITEM_COLUMNS)})"
    )


def _accepted(statement):
    db.session.execute(sa.text(statement))
    db.session.commit()


def _refused(statement, rule):
    try:
        db.session.execute(sa.text(statement))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise AssertionError(f"{rule} was not enforced")


def test_every_plan_check_and_unique_constraint_is_enforced(app):
    with app.app_context():
        actor_id = fx.admin().id
        active = {
            "status": "'active'",
            "first_activated_at": _T1, "first_activated_by_id": str(actor_id),
            "status_changed_at": _T1, "status_changed_by_id": str(actor_id),
            "updated_at": _T1,
        }
        _accepted(_plan_sql(actor_id, name="'Legal draft'", public_id="'legal-1'"))
        _accepted(_plan_sql(actor_id, **active))
        _accepted(_plan_sql(actor_id, status="'archived'", status_changed_at=_T2,
                            status_changed_by_id=str(actor_id), updated_at=_T2))
        _accepted(_plan_sql(actor_id, **dict(active, status="'archived'",
                                             status_changed_at=_T2, updated_at=_T2)))
        # Phase 5 / M02R: a draft restored from its archive keeps the
        # restoration in status_changed_* and was never activated.
        _accepted(_plan_sql(actor_id, status_changed_at=_T2,
                            status_changed_by_id=str(actor_id), updated_at=_T2))

        for overrides, rule in (
            ({"name": "'Legal draft'"}, "uq_fee_plans_name"),
            ({"public_id": "'legal-1'"}, "public_id uniqueness"),
            ({"currency_code": "'USD'"}, "ck_fee_plans_currency_code"),
            ({"status": "'deleted'"}, "ck_fee_plans_status_valid"),
            ({"version": "0"}, "ck_fee_plans_version_positive"),
            ({"first_activated_at": _T1}, "ck_fee_plans_first_activation_pair"),
            ({"status_changed_by_id": str(actor_id)}, "ck_fee_plans_status_change_pair"),
            (dict(active, status="'draft'"), "a draft that was activated"),
            ({"first_activated_at": _T1, "first_activated_by_id": str(actor_id),
              "updated_at": _T1}, "a draft with a first activation and no transition"),
            ({"status_changed_at": _T2, "status_changed_by_id": str(actor_id)},
             "a restored draft updated before its restoration"),
            ({"status_changed_at": _T2, "updated_at": _T2},
             "a restoration without its actor"),
            ({"status": "'active'"}, "an active plan never activated"),
            (dict(active, first_activated_at="NULL", first_activated_by_id="NULL"),
             "an active plan with no first activation"),
            ({"status": "'archived'"}, "an archived plan with no transition"),
            (dict(active, status_changed_at=_T0), "a transition before the activation"),
            ({"updated_at": "'2026-04-30 09:00:00'"}, "updated_at before created_at"),
            (dict(active, updated_at=_T0), "updated_at before the transition"),
            ({"created_by_id": "999999"}, "the created_by foreign key"),
            ({"name": "NULL"}, "name NOT NULL"),
        ):
            _refused(_plan_sql(actor_id, **overrides), rule)


def test_every_item_check_and_foreign_key_is_enforced(app):
    with app.app_context():
        creator = fx.admin()
        owner = fx.plan(creator)
        plan_id, actor_id = owner.id, creator.id
        _accepted(_item_sql(plan_id, amount="0.001", public_id="'item-legal'"))
        _accepted(_item_sql(plan_id, amount="99999.999"))
        _accepted(_item_sql(plan_id, status="'removed'", removed_at=_T1,
                            removed_by_id=str(actor_id), updated_at=_T1))
        for overrides, rule in (
            ({"public_id": "'item-legal'"}, "public_id uniqueness"),
            ({"kind": "'discount'"}, "ck_fee_plan_items_kind_valid"),
            ({"status": "'deleted'"}, "ck_fee_plan_items_status_valid"),
            ({"amount": "0"}, "ck_fee_plan_items_amount_range (zero)"),
            ({"amount": "-5"}, "ck_fee_plan_items_amount_range (negative)"),
            ({"amount": "0.0009"}, "ck_fee_plan_items_amount_range (below)"),
            ({"amount": "100000"}, "ck_fee_plan_items_amount_range (above)"),
            ({"version": "0"}, "ck_fee_plan_items_version_positive"),
            ({"status": "'removed'"}, "a removed item without attribution"),
            ({"removed_at": _T1, "removed_by_id": str(actor_id), "updated_at": _T1},
             "an active item with removal attribution"),
            ({"status": "'removed'", "removed_at": _T1, "updated_at": _T1},
             "a removal without its actor"),
            ({"updated_at": "'2026-04-30 09:00:00'"}, "updated_at before created_at"),
            ({"fee_plan_id": "999999"}, "the fee_plan foreign key"),
            ({"status": "'removed'", "removed_at": _T1, "removed_by_id": "999999",
              "updated_at": _T1}, "the removed_by foreign key"),
        ):
            _refused(_item_sql(plan_id, **overrides), rule)


def test_nothing_referenced_can_be_hard_deleted(app):
    """Plain foreign keys with no cascade: an account that created a plan,
    and a plan that owns an item, cannot be deleted out from under them."""
    with app.app_context():
        creator = fx.admin()
        owner = fx.plan(creator)
        fx.item(owner)
        _refused(f"DELETE FROM users WHERE id = {creator.id}", "no-cascade from users")
        _refused(f"DELETE FROM fee_plans WHERE id = {owner.id}", "no-cascade from fee_plans")
        assert FeePlan.query.count() == 1 and FeePlanItem.query.count() == 1


def test_no_relationship_cascade_or_ondelete_exists(app):
    with app.app_context():
        for model in (FeePlan, FeePlanItem):
            assert not sa.inspect(model).relationships, model
            for foreign_key in model.__table__.foreign_keys:
                assert foreign_key.ondelete is None and foreign_key.onupdate is None
        targets = {
            (fk.parent.name, fk.column.table.name)
            for model in (FeePlan, FeePlanItem)
            for fk in model.__table__.foreign_keys
        }
        assert targets == {
            ("created_by_id", "users"),
            ("first_activated_by_id", "users"),
            ("status_changed_by_id", "users"),
            ("fee_plan_id", "fee_plans"),
            ("removed_by_id", "users"),
        }


def test_lifecycle_properties_follow_the_stored_state(app):
    with app.app_context():
        creator = fx.admin()
        draft = fx.plan(creator)
        active = fx.plan(creator, status=fx.ACTIVE)
        archived = fx.plan(creator, status=fx.ARCHIVED_STATUS)
        archived_draft = fx.plan(creator, status=fx.ARCHIVED_STATUS, ever_activated=False)
        assert draft.is_draft and not draft.has_been_activated
        assert not draft.restores_to_draft and not draft.can_be_reactivated
        assert active.is_active and active.has_been_activated and not active.can_be_reactivated
        assert not active.restores_to_draft
        # Phase 5 / M02R: every archived plan restores, to the state its
        # history allows.
        assert archived.is_archived and archived.can_be_reactivated
        assert not archived.restores_to_draft
        assert archived_draft.is_archived and archived_draft.restores_to_draft
        assert not archived_draft.can_be_reactivated
