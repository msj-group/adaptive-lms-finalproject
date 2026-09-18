"""Phase 5 / M06 -- the PaymentIntent model (with Phase 5 / M07's ``confirmed``
status and webhook-decided states).

Closed sets, defaults and application validators first; then every database
CHECK, unique constraint and foreign key driven with raw SQL, so no validator
can mask a missing constraint; then the ORM guards that refuse deleting an
intent, rewriting what was asked, changing a decided intent, or any transition
that is not one decision moving the version by exactly one; then the absence of
any relationship, cascade or credential column; and finally the one rule the
database deliberately leaves to the application.
"""

from datetime import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

import tests.fee_assignment_fixtures as fees
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
from app.extensions import db
from app.models import (
    ACTIVE_PAYMENT_INTENT_STATUSES,
    MAX_INVOICE_PAYMENT_INTENTS,
    TERMINAL_PAYMENT_INTENT_STATUSES,
    FinancialHistoryError,
    PaymentIntent,
    PaymentIntentStatus,
)
from app.services.mock_payment_provider import mock_reference

_T0 = "'2026-07-05 09:00:00'"
_T1 = "'2026-07-05 10:00:00'"
_T2 = "'2026-07-05 11:00:00'"
_EARLIER = "'2026-07-04 09:00:00'"


def _owners():
    actor = fees.admin()
    assignment = fees.assignment(fees.enrollment(), fees.active_plan(actor), actor)
    return actor, px.issued_invoice(assignment, actor)


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


_SEQUENCE = {"n": 0}


def _intent_sql(owner_id, actor_id, /, **overrides):
    _SEQUENCE["n"] += 1
    n = _SEQUENCE["n"]
    key = ix.key_for(f"sql-{n}")
    values = {
        "public_id": f"'intent-{n}'",
        "invoice_id": str(owner_id),
        "provider": "'mock'",
        "provider_reference": f"'{mock_reference(key)}'",
        "idempotency_key": f"'{key}'",
        "status": "'pending'",
        "currency_code": "'LYD'",
        "amount": "1250.5",
        "created_by_id": str(actor_id),
        "provider_result_at": "NULL",
        "provider_result_by_id": "NULL",
        "terminal_at": "NULL",
        "cancelled_by_id": "NULL",
        "version": "1",
        "created_at": _T0,
        "updated_at": _T0,
    }
    values.update(overrides)
    return (f"INSERT INTO payment_intents ({', '.join(values)}) "
            f"VALUES ({', '.join(values.values())})")


# ===========================================================================
# Closed sets, defaults and validators
# ===========================================================================


def test_the_closed_sets_and_bounds_are_exact():
    assert [m.value for m in PaymentIntentStatus] == [
        "pending", "provider_succeeded", "provider_failed", "cancelled", "confirmed"]
    assert ACTIVE_PAYMENT_INTENT_STATUSES == ("pending", "provider_succeeded")
    assert TERMINAL_PAYMENT_INTENT_STATUSES == ("provider_failed", "cancelled", "confirmed")
    assert set(ACTIVE_PAYMENT_INTENT_STATUSES) | set(TERMINAL_PAYMENT_INTENT_STATUSES) == {
        m.value for m in PaymentIntentStatus}
    assert MAX_INVOICE_PAYMENT_INTENTS == 25


def test_defaults_public_ids_and_state_properties(app):
    with app.app_context():
        actor, owner = _owners()
        first = ix.intent(owner, actor)
        second = ix.intent(owner, actor, status="cancelled")
        assert len(first.public_id) == 36 and first.public_id != second.public_id
        assert (first.currency_code, first.version, first.amount, first.provider) == (
            "LYD", 1, Decimal("1250.5000"), "mock")
        assert first.is_pending and first.is_active and not first.is_terminal
        assert second.is_terminal and not second.is_active and not second.is_pending
        columns = PaymentIntent.__table__.c
        assert columns.public_id.default.is_callable
        assert columns.created_at.default.is_callable and columns.updated_at.default.is_callable
        assert columns.created_at.onupdate is None and columns.updated_at.onupdate is None
        assert columns.provider_result_at.default is None and columns.terminal_at.default is None


@pytest.mark.parametrize(
    "field, value",
    [
        ("provider", "stripe"), ("provider", "Mock"), ("provider", None),
        ("provider_reference", ""), ("provider_reference", "has space"),
        ("provider_reference", "x" * 65), ("provider_reference", "refé"), ("provider_reference", 7),
        ("idempotency_key", "A" * 64), ("idempotency_key", "a" * 63), ("idempotency_key", "a" * 65),
        ("idempotency_key", "z" * 64), ("idempotency_key", None),
        ("status", "paid"), ("status", "succeeded"), ("status", None),
        ("currency_code", "USD"),
        ("amount", 12.5), ("amount", Decimal("0")), ("amount", Decimal("100000")),
        ("amount", Decimal("1.00001")), ("amount", 5),
        ("version", 0), ("version", True), ("version", "1"),
    ],
)
def test_validators_refuse_bad_values(app, field, value):
    with app.app_context():
        with pytest.raises(ValueError):
            PaymentIntent(**{field: value})


# ===========================================================================
# Database constraints, driven with raw SQL
# ===========================================================================


def test_every_payment_intent_constraint_is_enforced(app):
    with app.app_context():
        actor, owner = _owners()
        other = fees.admin("other@example.com")
        i, u, o = owner.id, str(actor.id), str(other.id)
        succeeded = {"status": "'provider_succeeded'", "provider_result_at": _T1,
                     "provider_result_by_id": u, "version": "2", "updated_at": _T1}
        failed = dict(succeeded, status="'provider_failed'", terminal_at=_T1)
        cancelled = {"status": "'cancelled'", "terminal_at": _T2, "cancelled_by_id": o,
                     "version": "2", "updated_at": _T2}
        _accepted(_intent_sql(i, u, public_id="'legal'", provider_reference="'legal-ref'",
                              idempotency_key=f"'{'e' * 64}'"))
        # Phase 5 / M07: a verified webhook fails or confirms an intent with or
        # without an earlier browser-observed result, never before it.
        webhook_failed = {"status": "'provider_failed'", "terminal_at": _T2, "version": "2",
                          "updated_at": _T2}
        confirmed = dict(webhook_failed, status="'confirmed'")
        confirmed_after_browser = dict(succeeded, status="'confirmed'", terminal_at=_T2,
                                       version="3", updated_at=_T2)
        failed_after_browser = dict(confirmed_after_browser, status="'provider_failed'")
        for accepted in ({}, succeeded, failed, cancelled, {"amount": "0.001"},
                         {"amount": "99999.999"}, {"amount": "12.3456"}, {"version": "7"},
                         webhook_failed, confirmed, confirmed_after_browser,
                         failed_after_browser):
            _accepted(_intent_sql(i, u, **accepted))

        for overrides, rule in (
            ({"public_id": "'legal'"}, "public_id uniqueness"),
            ({"provider_reference": "'legal-ref'"}, "uq_payment_intents_provider_reference"),
            ({"idempotency_key": f"'{'e' * 64}'"}, "uq_payment_intents_idempotency_key"),
            ({"status": "'paid'"}, "ck_payment_intents_status_valid"),
            ({"status": "'succeeded'"}, "ck_payment_intents_status_valid (succeeded)"),
            ({"status": "'confirmed'"}, "a confirmed intent without its terminal moment"),
            (dict(confirmed, cancelled_by_id=u), "a confirmed intent naming a canceller"),
            (dict(confirmed_after_browser, terminal_at=_EARLIER, updated_at=_T2),
             "a confirmation before its browser result"),
            ({"provider": "'stripe'"}, "ck_payment_intents_provider_valid"),
            ({"currency_code": "'USD'"}, "ck_payment_intents_currency_code"),
            ({"amount": "0"}, "a zero amount"),
            ({"amount": "0.0009"}, "an amount below the minimum"),
            ({"amount": "100000"}, "an amount above the maximum"),
            ({"amount": "-5"}, "a negative amount"),
            ({"version": "0"}, "ck_payment_intents_version_positive"),
            ({"provider_reference": "''"}, "ck_payment_intents_provider_reference_present"),
            ({"idempotency_key": "'short'"}, "ck_payment_intents_idempotency_key_length"),
            ({"idempotency_key": f"'{'f' * 65}'"}, "a 65-character idempotency key"),
            ({"provider_result_at": _T1}, "the provider-result pair (actor)"),
            (dict(succeeded, provider_result_by_id="NULL"), "the provider-result pair (actor)"),
            (dict(succeeded, provider_result_at="NULL"), "the provider-result pair (moment)"),
            ({"terminal_at": _T1}, "a pending intent with a terminal moment"),
            (dict(succeeded, terminal_at=_T1), "a provider success with a terminal moment"),
            (dict(failed, terminal_at="NULL"), "a failure without its terminal moment"),
            (dict(cancelled, terminal_at="NULL"), "a cancellation without its terminal moment"),
            (dict(failed, provider_result_at=_T2, terminal_at=_T1, updated_at=_T2),
             "a failure that became terminal before its browser result"),
            (dict(cancelled, cancelled_by_id="NULL"), "a cancellation without its actor"),
            ({"cancelled_by_id": u}, "a pending intent naming a canceller"),
            (dict(succeeded, cancelled_by_id=u), "a provider success naming a canceller"),
            (dict(cancelled, provider_result_at=_T1, provider_result_by_id=u),
             "a cancellation carrying a provider result"),
            (dict(failed, cancelled_by_id=u), "a failure naming a canceller"),
            ({"updated_at": _EARLIER}, "updated before it was created"),
            (dict(succeeded, provider_result_at=_EARLIER), "a result before creation"),
            (dict(succeeded, updated_at=_T0), "a result after the last update"),
            (dict(cancelled, terminal_at=_EARLIER), "terminal before creation"),
            (dict(cancelled, updated_at=_T1), "terminal after the last update"),
            ({"invoice_id": "999"}, "the invoice foreign key"),
            ({"created_by_id": "999"}, "the creator foreign key"),
            (dict(succeeded, provider_result_by_id="999"), "the result actor foreign key"),
            (dict(cancelled, cancelled_by_id="999"), "the canceller foreign key"),
            ({"provider_reference": "NULL"}, "a missing reference"),
            ({"idempotency_key": "NULL"}, "a missing idempotency key"),
            ({"amount": "NULL"}, "a missing amount"),
        ):
            _refused(_intent_sql(i, u, **overrides), rule)
        assert PaymentIntent.query.count() == 13


def test_the_database_refuses_deleting_anything_an_intent_references(app):
    with app.app_context():
        actor, owner = _owners()
        row = ix.intent(owner, actor)
        invoice_id, actor_id = owner.id, actor.id
        _refused(f"DELETE FROM invoices WHERE id = {invoice_id}", "no cascade from an invoice")
        _refused(f"DELETE FROM users WHERE id = {actor_id}", "no cascade from an account")
        assert PaymentIntent.query.one().id == row.id


def test_foreign_keys_are_plain_with_no_relationship_or_cascade(app):
    with app.app_context():
        assert not sa.inspect(PaymentIntent).relationships
        for foreign_key in PaymentIntent.__table__.foreign_keys:
            assert foreign_key.ondelete is None and foreign_key.onupdate is None
        assert {(fk.parent.name, fk.column.table.name)
                for fk in PaymentIntent.__table__.foreign_keys} == {
            ("invoice_id", "invoices"), ("created_by_id", "users"),
            ("provider_result_by_id", "users"), ("cancelled_by_id", "users")}


_COLUMNS = {"id", "public_id", "invoice_id", "provider", "provider_reference", "idempotency_key",
            "status", "currency_code", "amount", "created_by_id", "provider_result_at",
            "provider_result_by_id", "terminal_at", "cancelled_by_id", "version", "created_at",
            "updated_at"}

#: Column-name parts that would mean card, bank or credential data, a customer,
#: a webhook or refund, a stored balance, or identity duplicated from the
#: invoice chain.
_PROHIBITED_PARTS = (
    "card", "pan", "cvv", "cvc", "pin", "iban", "swift", "account", "token", "secret",
    "password", "credential", "customer", "webhook", "proof", "upload", "image", "file",
    "refund", "signature", "event", "payload", "total", "paid", "outstanding", "balance",
    "receipt", "transaction", "enrollment", "group", "student", "course", "term", "plan",
    "name", "email", "phone",
)


def test_no_credential_balance_or_duplicated_identity_column_exists():
    columns = {column.name for column in PaymentIntent.__table__.columns}
    assert columns == _COLUMNS
    for name in columns:
        for part in _PROHIBITED_PARTS:
            assert part not in name.split("_"), (name, part)
    assert PaymentIntent.__table__.kwargs == {}


# ===========================================================================
# ORM guards
# ===========================================================================


def test_an_intent_is_never_deleted_through_the_orm(app):
    with app.app_context():
        actor, owner = _owners()
        for status in ("pending", "provider_succeeded", "provider_failed", "cancelled"):
            row = ix.intent(owner, actor, status=status)
            db.session.delete(row)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        assert PaymentIntent.query.count() == 4


_BULK_STATEMENTS = {
    "an ORM UPDATE": lambda: sa.update(PaymentIntent).values(status="cancelled"),
    "an ORM DELETE": lambda: sa.delete(PaymentIntent),
}


@pytest.mark.parametrize("name", sorted(_BULK_STATEMENTS))
def test_bulk_rewrites_of_intents_are_refused(app, name):
    with app.app_context():
        actor, owner = _owners()
        ix.intent(owner, actor)
        with pytest.raises(FinancialHistoryError):
            db.session.execute(_BULK_STATEMENTS[name]())
        db.session.rollback()
        with pytest.raises(FinancialHistoryError):
            db.session.query(PaymentIntent).update({"version": 9})
        db.session.rollback()
        with pytest.raises(FinancialHistoryError):
            db.session.query(PaymentIntent).delete()
        db.session.rollback()
        assert PaymentIntent.query.one().status == "pending"


def _decide(row, status, moment=datetime(2026, 7, 6, 9, 0, 0), actor_id=None):
    # The version is read first: reading an expired attribute refreshes the
    # row, and that refresh autoflushes whatever was already set.
    row.version = row.version + 1
    row.status = status
    row.updated_at = moment
    if status in ("provider_succeeded", "provider_failed"):
        row.provider_result_at = moment
        row.provider_result_by_id = actor_id
    if status in ("provider_failed", "cancelled"):
        row.terminal_at = moment
    if status == "cancelled":
        row.cancelled_by_id = actor_id


@pytest.mark.parametrize("status", ["provider_succeeded", "provider_failed", "cancelled"])
def test_a_pending_intent_changes_only_by_one_decision(app, status):
    with app.app_context():
        actor, owner = _owners()
        row = ix.intent(owner, actor)
        _decide(row, status, actor_id=actor.id)
        db.session.commit()
        db.session.expire_all()
        stored = PaymentIntent.query.one()
        assert (stored.status, stored.version) == (status, 2)


@pytest.mark.parametrize("status", ["provider_succeeded", "provider_failed", "cancelled"])
def test_a_decided_intent_never_changes(app, status):
    with app.app_context():
        actor, owner = _owners()
        row = ix.intent(owner, actor, status=status)
        for change in (
            lambda r: _decide(r, "cancelled", actor_id=actor.id),
            lambda r: setattr(r, "updated_at", datetime(2026, 7, 9, 9, 0, 0)),
            lambda r: (setattr(r, "status", "pending"), setattr(r, "version", r.version + 1)),
        ):
            change(row)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
            row = PaymentIntent.query.one()
        assert (row.status, row.version) == (status, 2)


@pytest.mark.parametrize(
    "field, value",
    [
        ("public_id", "another-public-id"),
        ("provider_reference", "mock_pi_" + "0" * 32),
        ("idempotency_key", "0" * 64),
        ("amount", Decimal("1.000")),
        ("created_at", datetime(2026, 7, 4, 9, 0, 0)),
    ],
)
def test_what_was_asked_never_changes(app, field, value):
    with app.app_context():
        actor, owner = _owners()
        row = ix.intent(owner, actor)
        _decide(row, "cancelled", actor_id=actor.id)
        setattr(row, field, value)
        with pytest.raises(FinancialHistoryError, match=field):
            db.session.flush()
        db.session.rollback()
        assert PaymentIntent.query.one().status == "pending"


def test_a_decision_moves_the_version_by_exactly_one_and_names_a_decision(app):
    with app.app_context():
        actor, owner = _owners()
        row = ix.intent(owner, actor)
        _decide(row, "cancelled", actor_id=actor.id)
        row.version = 3
        with pytest.raises(FinancialHistoryError, match="exactly one"):
            db.session.flush()
        db.session.rollback()
        row = PaymentIntent.query.one()
        row.version = 2
        row.updated_at = datetime(2026, 7, 6, 9, 0, 0)
        with pytest.raises(FinancialHistoryError, match="only along its lifecycle"):
            db.session.flush()
        db.session.rollback()
        assert PaymentIntent.query.one().version == 1


# ===========================================================================
# What the database deliberately leaves to the application
# ===========================================================================


def test_the_database_accepts_two_active_intents_so_the_application_must_refuse(app):
    """No portable partial unique index exists; "at most one active intent per
    invoice" is proved by the application under the invoice lock."""
    with app.app_context():
        actor, owner = _owners()
        ix.intent(owner, actor)
        ix.intent(owner, actor, status="provider_succeeded")
        assert PaymentIntent.query.filter_by(invoice_id=owner.id).count() == 2
