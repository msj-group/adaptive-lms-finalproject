"""Phase 5 / M07 -- the verified-webhook data model.

The provider-event inbox (its exact normalized columns, validators, every
database constraint and its immutability); the online collection's CHECKs,
uniqueness and ORM guard; the receipt issuer pairing; the audit actor-origin
CHECK and guard; the online snapshot layout; the payment intent's M07
transitions; and the audit writer's refusals. Database constraints are proved
by mutating real rows with raw SQL, which no ORM guard sees.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
import tests.webhook_fixtures as wx
from app.extensions import db
from app.models import (
    FinancialHistoryError,
    PaymentAuditEvent,
    PaymentIntent,
    PaymentProviderEvent,
    PaymentTransaction,
    ProviderEventOutcome,
    ProviderEventType,
    Receipt,
    User,
)
from app.models.payment_audit_event import (
    ONLINE_PAYMENT_SNAPSHOT_SCHEMA,
    PAYMENT_SNAPSHOT_SCHEMA,
    SYSTEM_EVENT_KINDS,
    validate_audit_snapshot,
    validate_online_payment_snapshot,
    validate_payment_snapshot,
)
from app.models.receipt import (
    ONLINE_RECEIPT_SNAPSHOT_SCHEMA,
    RECEIPT_SNAPSHOT_SCHEMA,
    validate_receipt_snapshot,
)
from app.services.payment_audit import (
    CASH_RECORDED,
    ONLINE_CONFIRMED,
    RECEIPT_ONLINE_ISSUED,
    REVERSED,
    online_context,
    record_payment_event,
)


@pytest.fixture
def app():
    application = ix.make_app()
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def online(app, client):
    """One verified online collection: its intent, payment, receipt, two
    system-origin audit events and confirmed provider event."""
    w = wx.login_world(app, client)
    xp = ix.create_intent(client, w)
    response, raw = wx.deliver(client, wx.reference_of(xp))
    assert response.status_code == 200
    (payment,) = wx.online_payments()
    (stored,) = wx.stored_events(xp)
    return {
        "w": w, "xp": xp, "raw": raw,
        "intent_id": ix.stored_intent(xp).id,
        "payment_id": payment.id,
        "receipt_id": Receipt.query.filter_by(payment_transaction_id=payment.id).one().id,
        "event_id": stored.id,
        "audit_ids": [e.id for e in PaymentAuditEvent.query.filter(
            PaymentAuditEvent.kind.in_(SYSTEM_EVENT_KINDS)).order_by(PaymentAuditEvent.id)],
    }


def _stored(table, row_id):
    return dict(db.session.execute(
        sa.text(f"SELECT * FROM {table} WHERE id = :id"), {"id": row_id}).mappings().one())


def _clone(table, row_id, **overrides):
    """INSERT a copy of a stored row (a fresh ``id``) with `overrides`."""
    values = _stored(table, row_id)
    values.pop("id")
    values.update(overrides)
    columns = ", ".join(values)
    params = ", ".join(f":{key}" for key in values)
    db.session.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), values)
    db.session.commit()


def _set(table, row_id, **values):
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    db.session.execute(sa.text(f"UPDATE {table} SET {assignments} WHERE id = :row_id"),
                       dict(values, row_id=row_id))
    db.session.commit()


def _refused(action, rule, constraint=None):
    """`action` fails with an IntegrityError -- from `constraint` (or `rule`
    when it names one) when SQLite names it."""
    constraint = constraint or (rule if rule.startswith("ck_") else None)
    try:
        action()
    except IntegrityError as error:
        db.session.rollback()
        if constraint is not None:
            assert f"CHECK constraint failed: {constraint}" in str(error.orig), (rule, error.orig)
        return
    raise AssertionError(f"{rule} was not enforced")


def _new_uuid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


# ===========================================================================
# Closed sets
# ===========================================================================


def test_the_m07_closed_sets_are_exact():
    assert [m.value for m in ProviderEventType] == ["payment.succeeded", "payment.failed"]
    assert [m.value for m in ProviderEventOutcome] == [
        "confirmed", "failed", "duplicate", "ignored_terminal", "reconciliation_required"]
    assert SYSTEM_EVENT_KINDS == {"payment_online_confirmed", "receipt_online_issued"}
    assert ONLINE_PAYMENT_SNAPSHOT_SCHEMA == "phase5-m07.online-payment.v1"
    assert ONLINE_RECEIPT_SNAPSHOT_SCHEMA == "phase5-m07.online-receipt.v1"


# ===========================================================================
# The provider-event inbox
# ===========================================================================

_EVENT_COLUMNS = {
    "id", "public_id", "provider", "provider_event_id", "payment_intent_id", "event_type",
    "currency_code", "amount", "provider_occurred_at", "received_at", "processed_at",
    "payload_digest", "outcome", "payment_transaction_id", "created_at",
}
_PROHIBITED_PARTS = ("raw", "body", "payload", "signature", "secret", "header", "headers",
                     "reference", "card", "pan", "cvv", "iban", "bank", "account", "token",
                     "credential", "password", "key")


def test_the_inbox_holds_only_the_normalized_record():
    table = PaymentProviderEvent.__table__
    assert {c.name for c in table.columns} == _EVENT_COLUMNS
    assert {c.name for c in table.columns if c.nullable} == {"payment_transaction_id"}
    for column in _EVENT_COLUMNS - {"payload_digest"}:
        for part in _PROHIBITED_PARTS:
            assert part not in column.split("_"), (column, part)
    assert {c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)} == {
        f"ck_payment_provider_events_{name}" for name in (
            "provider_valid", "event_type_valid", "outcome_valid", "currency_code",
            "amount_range", "event_id_present", "payload_digest_length", "outcome_link",
            "outcome_type", "timestamps_ordered")}
    assert table.c.public_id.unique
    assert {(c.name, tuple(col.name for col in c.columns)) for c in table.constraints
            if isinstance(c, sa.UniqueConstraint) and c.name} == {
        ("uq_payment_provider_events_provider_event", ("provider", "provider_event_id")),
        ("uq_payment_provider_events_payment_transaction_id", ("payment_transaction_id",)),
    }
    assert {(i.name, tuple(col.name for col in i.columns)) for i in table.indexes} == {
        ("ix_payment_provider_events_intent_id_id", ("payment_intent_id", "id")),
        ("ix_payment_provider_events_outcome_id", ("outcome", "id")),
    }
    assert {(fk.parent.name, fk.column.table.name) for fk in table.foreign_keys} == {
        ("payment_intent_id", "payment_intents"),
        ("payment_transaction_id", "payment_transactions"),
    }
    assert not any(fk.ondelete or fk.onupdate for fk in table.foreign_keys)
    assert PaymentProviderEvent.__mapper__.relationships.keys() == []


@pytest.mark.parametrize("key, value", [
    ("provider", "stripe"), ("provider", None),
    ("provider_event_id", ""), ("provider_event_id", "x" * 65),
    ("provider_event_id", "has space"), ("provider_event_id", "tab\tid"),
    ("provider_event_id", None), ("provider_event_id", 7),
    ("event_type", "payment.refunded"), ("event_type", "PAYMENT.SUCCEEDED"),
    ("outcome", "refunded"), ("outcome", None),
    ("currency_code", "USD"), ("currency_code", "lyd"),
    ("amount", Decimal("0")), ("amount", Decimal("-1")), ("amount", Decimal("100000")),
    ("amount", 1.5), ("amount", 10), ("amount", "1,000"),
    ("payload_digest", "A" * 64), ("payload_digest", "a" * 63), ("payload_digest", "g" * 64),
    ("payload_digest", None),
    ("received_at", datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)),
    ("processed_at", datetime(2026, 9, 18, 10, 0, 0, 5)),
    ("provider_occurred_at", date(2026, 9, 18)),
])
def test_the_inbox_validators_refuse_what_the_webhook_never_stores(key, value):
    with pytest.raises(ValueError):
        PaymentProviderEvent(**{key: value})


def test_the_inbox_accepts_every_legal_outcome(app, online):
    source = online["event_id"]
    for n, (event_type, outcome) in enumerate((
        ("payment.succeeded", "duplicate"), ("payment.succeeded", "reconciliation_required"),
        ("payment.failed", "failed"), ("payment.failed", "duplicate"),
        ("payment.failed", "ignored_terminal"), ("payment.failed", "reconciliation_required"),
    )):
        _clone("payment_provider_events", source, public_id=_new_uuid(n),
               provider_event_id=f"evt_mock_{n:032d}", event_type=event_type, outcome=outcome,
               payment_transaction_id=None)
    assert PaymentProviderEvent.query.count() == 7


_OUTCOME_LINK = "ck_payment_provider_events_outcome_link"
_TIMESTAMPS = "ck_payment_provider_events_timestamps_ordered"


def test_every_inbox_constraint_is_enforced(app, online):
    source, payment_id = online["event_id"], online["payment_id"]
    stored = _stored("payment_provider_events", source)
    fresh = {"public_id": _new_uuid(99), "provider_event_id": "evt_mock_" + "9" * 32,
             "outcome": "duplicate", "payment_transaction_id": None}

    def clone(**overrides):
        return lambda: _clone("payment_provider_events", source, **dict(fresh, **overrides))

    for action, rule, *constraint in (
        (clone(public_id=stored["public_id"]), "public_id uniqueness"),
        (clone(provider_event_id=stored["provider_event_id"]),
         "uq_payment_provider_events_provider_event"),
        (clone(outcome="confirmed", payment_transaction_id=payment_id),
         "uq_payment_provider_events_payment_transaction_id"),
        (clone(provider="stripe"), "ck_payment_provider_events_provider_valid"),
        (clone(event_type="payment.refunded"), "ck_payment_provider_events_event_type_valid"),
        (clone(outcome="refunded"), "ck_payment_provider_events_outcome_valid"),
        (clone(currency_code="USD"), "ck_payment_provider_events_currency_code"),
        (clone(amount="0"), "a zero amount", "ck_payment_provider_events_amount_range"),
        (clone(amount="0.0009"), "an amount below the minimum"),
        (clone(amount="100000"), "an amount above the maximum",
         "ck_payment_provider_events_amount_range"),
        (clone(provider_event_id=""), "ck_payment_provider_events_event_id_present"),
        (clone(payload_digest="abc"), "ck_payment_provider_events_payload_digest_length"),
        (clone(outcome="confirmed"), "a confirmation naming no collection", _OUTCOME_LINK),
        (clone(payment_transaction_id=payment_id), "a duplicate naming a collection",
         _OUTCOME_LINK),
        (clone(outcome="confirmed", event_type="payment.failed",
               payment_transaction_id=payment_id), "a failure event confirming",
         _OUTCOME_LINK),
        (clone(outcome="failed", event_type="payment.succeeded"),
         "ck_payment_provider_events_outcome_type"),
        (clone(processed_at="2000-01-01 00:00:00"), "processing before receipt",
         _TIMESTAMPS),
        (clone(created_at="2000-01-01 00:00:00"), "creation before receipt", _TIMESTAMPS),
        (clone(payment_intent_id=987654), "the intent foreign key"),
        (clone(payment_intent_id=None), "a required intent"),
        (clone(outcome="confirmed", payment_transaction_id=987654),
         "the collection foreign key"),
        (clone(received_at=None), "a required receipt moment"),
    ):
        _refused(action, rule, *constraint)
    assert PaymentProviderEvent.query.count() == 1


def test_a_provider_event_never_changes_or_disappears(app, online):
    event = db.session.get(PaymentProviderEvent, online["event_id"])
    for attribute, value in (("outcome", "duplicate"), ("amount", Decimal("1")),
                             ("processed_at", datetime(2030, 1, 1))):
        setattr(event, attribute, value)
        with pytest.raises(FinancialHistoryError):
            db.session.commit()
        db.session.rollback()
    db.session.delete(db.session.get(PaymentProviderEvent, online["event_id"]))
    with pytest.raises(FinancialHistoryError):
        db.session.commit()
    db.session.rollback()
    for statement in (update(PaymentProviderEvent).values(outcome="duplicate"),
                      delete(PaymentProviderEvent)):
        with pytest.raises(FinancialHistoryError):
            db.session.execute(statement)
        db.session.rollback()
    assert wx.stored_events()[0].outcome == "confirmed"


# ===========================================================================
# The online collection
# ===========================================================================


def test_every_online_collection_constraint_is_enforced(app, online):
    payment_id, w = online["payment_id"], online["w"]
    admin = str(w["admin_id"])
    owner, actor = ix.invoice_and_admin(w)
    other_intent = ix.intent(owner, actor, status="cancelled").id
    cash, _receipt = px.cash_with_receipt(owner, actor, amount="1")
    for n, action_rule in enumerate((
        (lambda: _clone("payment_transactions", payment_id, public_id=_new_uuid(1)),
         "uq_payment_transactions_payment_intent_id"),
        (lambda: _clone("payment_transactions", payment_id, public_id=_new_uuid(2),
                        payment_intent_id=None), "an online collection without its intent",
         "ck_payment_transactions_online_origin"),
        (lambda: _clone("payment_transactions", payment_id, public_id=_new_uuid(3),
                        payment_intent_id=987654), "the intent foreign key"),
        (lambda: _set("payment_transactions", payment_id, recorded_by_id=admin),
         "an online collection with a human recorder", "ck_payment_transactions_"),
        (lambda: _set("payment_transactions", payment_id, confirmed_by_id=admin),
         "an online collection with a human confirmer", "ck_payment_transactions_"),
        (lambda: _set("payment_transactions", payment_id, confirmed_at="2030-01-01 00:00:00"),
         "an online collection confirmed after it was recorded"),
        (lambda: _set("payment_transactions", payment_id, status="pending", confirmed_at=None),
         "a pending online collection"),
        (lambda: _set("payment_transactions", payment_id, bank_transfer_reference="TRX-1",
                      bank_transfer_date="2026-06-30"), "an online collection with bank details"),
        (lambda: _set("payment_transactions", cash.id, payment_intent_id=other_intent),
         "a cash collection naming an intent", "ck_payment_transactions_online_origin"),
        (lambda: _set("payment_transactions", cash.id, recorded_by_id=None,
                      confirmed_by_id=None), "a cash collection without its recorder"),
        (lambda: _set("payment_transactions", cash.id, method="online",
                      payment_intent_id=other_intent), "a human-recorded online collection",
         "ck_payment_transactions_"),
    )):
        _refused(*action_rule)
    # The same shape on another intent is legal for the database.
    _clone("payment_transactions", payment_id, public_id=_new_uuid(9),
           payment_intent_id=other_intent)
    assert PaymentTransaction.query.filter_by(method="online").count() == 2


def test_an_online_collection_never_changes(app, online):
    payment = db.session.get(PaymentTransaction, online["payment_id"])
    assert payment.is_online and payment.is_confirmed and payment.recorded_by_id is None
    for attribute, value in (("payment_intent_id", None), ("status", "rejected"),
                             ("amount", Decimal("1"))):
        setattr(payment, attribute, value)
        with pytest.raises(FinancialHistoryError):
            db.session.commit()
        db.session.rollback()
        payment = db.session.get(PaymentTransaction, online["payment_id"])


def test_a_pending_manual_row_can_never_become_online(app, client):
    w = wx.login_world(app, client)
    owner, actor = ix.invoice_and_admin(w)
    pending = px.payment(owner, actor, method="bank_transfer", status="pending", amount="5")
    intent = ix.intent(owner, actor, status="cancelled")
    pending.payment_intent_id = intent.id
    pending.status, pending.version = "confirmed", 2
    with pytest.raises(FinancialHistoryError):
        db.session.commit()
    db.session.rollback()


# ===========================================================================
# The receipt's issuer
# ===========================================================================


def _receipt_like(source, **overrides):
    values = {key: getattr(source, key) for key in (
        "payment_transaction_id", "receipt_number", "issued_by_id", "issued_at", "snapshot",
        "status", "version", "created_at", "updated_at")}
    values.update(overrides)
    return Receipt(**values)


def test_the_receipt_issuer_is_null_exactly_for_an_online_document(app, online):
    source = db.session.get(Receipt, online["receipt_id"])
    assert source.is_online and source.issued_by_id is None
    assert source.snapshot["schema"] == ONLINE_RECEIPT_SNAPSHOT_SCHEMA
    owner, actor = ix.invoice_and_admin(online["w"])
    cash, manual = px.cash_with_receipt(owner, actor, amount="1")
    assert not manual.is_online and manual.issued_by_id == actor.id
    for receipt in (
        _receipt_like(source, issued_by_id=actor.id, receipt_number="RCT-2026-900001"),
        _receipt_like(manual, issued_by_id=None, receipt_number="RCT-2026-900002",
                      payment_transaction_id=cash.id),
    ):
        db.session.add(receipt)
        with pytest.raises(ValueError, match="no issuing Administrator"):
            db.session.flush()
        db.session.rollback()


def test_an_online_receipt_document_is_exact(app, online):
    document = dict(db.session.get(Receipt, online["receipt_id"]).snapshot)
    assert validate_receipt_snapshot(document) == document
    assert "confirmed_by_name" not in document
    for broken in (
        dict(document, method="cash"),
        dict(document, confirmed_by_name="Admin"),
        {k: v for k, v in document.items() if k != "provider_event_public_id"},
        dict(document, payment_intent_public_id=""),
        dict(document, provider_reference="mock_pi_" + "a" * 32),
        dict(document, schema=RECEIPT_SNAPSHOT_SCHEMA),
    ):
        with pytest.raises(ValueError):
            validate_receipt_snapshot(broken)


# ===========================================================================
# The audit trail's actor origin
# ===========================================================================


def test_the_actor_origin_check_is_enforced(app, online):
    system_id = online["audit_ids"][0]
    admin = online["w"]["admin_id"]
    _refused(lambda: _set("payment_audit_events", system_id, actor_id=admin),
             "a system-origin event with an actor", "ck_payment_audit_events_actor_origin")
    _refused(lambda: _set("payment_audit_events", system_id, kind=CASH_RECORDED),
             "a human event without an actor", "ck_payment_audit_events_actor_origin")
    _refused(lambda: _clone("payment_audit_events", system_id, actor_id=admin),
             "a copied system event with an actor", "ck_payment_audit_events_actor_origin")


def test_the_audit_guard_pairs_the_actor_with_the_kind(app, online):
    system = db.session.get(PaymentAuditEvent, online["audit_ids"][0])
    for kind, actor_id in ((ONLINE_CONFIRMED, online["w"]["admin_id"]), (REVERSED, None)):
        copy = PaymentAuditEvent(
            invoice_id=system.invoice_id, actor_id=actor_id, kind=kind,
            payment_transaction_id=system.payment_transaction_id, receipt_id=None,
            before_snapshot=system.before_snapshot, after_snapshot=system.after_snapshot,
            invoice_version_before=system.invoice_version_before,
            invoice_version_after=system.invoice_version_after, occurred_at=system.occurred_at,
            reason="Wrong amount" if kind == REVERSED else None)
        db.session.add(copy)
        with pytest.raises(ValueError):
            db.session.flush()
        db.session.rollback()


def test_the_online_snapshot_layout_is_exact(app, online):
    snapshot = dict(db.session.get(PaymentAuditEvent, online["audit_ids"][1]).after_snapshot)
    assert validate_online_payment_snapshot(snapshot) == snapshot
    assert validate_audit_snapshot(snapshot) == snapshot
    assert set(snapshot["online"]) == {"payment_intent_public_id", "provider_event_public_id"}
    for broken in (
        {k: v for k, v in snapshot.items() if k != "online"},
        dict(snapshot, online=None),
        dict(snapshot, online="evt"),
        dict(snapshot, online=dict(snapshot["online"], provider_reference="mock_pi_x")),
        dict(snapshot, online={"payment_intent_public_id": snapshot["online"][
            "payment_intent_public_id"]}),
        dict(snapshot, online=dict(snapshot["online"], provider_event_public_id=7)),
        dict(snapshot, online=dict(snapshot["online"], provider_event_public_id="")),
        dict(snapshot, schema=PAYMENT_SNAPSHOT_SCHEMA),
    ):
        with pytest.raises(ValueError):
            validate_online_payment_snapshot(broken)
    with pytest.raises(ValueError):
        validate_payment_snapshot(snapshot)
    m05 = {k: v for k, v in snapshot.items() if k != "online"}
    with pytest.raises(ValueError):
        validate_online_payment_snapshot(dict(m05, schema=ONLINE_PAYMENT_SNAPSHOT_SCHEMA))


# ===========================================================================
# The payment intent's M07 transitions
# ===========================================================================

_LATER = datetime(2026, 7, 6, 9, 0, 0)


def _move(row, status, version, **values):
    row.status, row.version, row.updated_at = status, version, _LATER
    for key, value in values.items():
        setattr(row, key, value)
    db.session.commit()


@pytest.mark.parametrize("start, status, version", [
    ("pending", "confirmed", 2),
    ("provider_succeeded", "confirmed", 3),
    ("provider_succeeded", "provider_failed", 3),
])
def test_an_active_intent_is_confirmed_or_failed_by_its_webhook(app, client, start, status,
                                                               version):
    w = wx.login_world(app, client)
    owner, actor = ix.invoice_and_admin(w)
    row = ix.intent(owner, actor, status=start)
    _move(row, status, version, terminal_at=_LATER)
    stored = db.session.get(PaymentIntent, row.id)
    assert (stored.status, stored.version, stored.terminal_at) == (status, version, _LATER)
    assert stored.is_confirmed == (status == "confirmed")


@pytest.mark.parametrize("start, status", [
    ("provider_succeeded", "cancelled"), ("provider_succeeded", "pending"),
    ("confirmed", "pending"), ("confirmed", "provider_failed"), ("confirmed", "cancelled"),
    ("provider_failed", "confirmed"), ("cancelled", "confirmed"),
])
def test_no_other_transition_touches_a_confirmation(app, client, start, status):
    w = wx.login_world(app, client)
    owner, actor = ix.invoice_and_admin(w)
    row = ix.intent(owner, actor, status=start)
    with pytest.raises(FinancialHistoryError):
        _move(row, status, row.version + 1, terminal_at=_LATER)
    db.session.rollback()


def test_a_confirmation_moves_the_version_by_exactly_one(app, client):
    w = wx.login_world(app, client)
    owner, actor = ix.invoice_and_admin(w)
    row = ix.intent(owner, actor)
    with pytest.raises(FinancialHistoryError):
        _move(row, "confirmed", 3, terminal_at=_LATER)
    db.session.rollback()


# ===========================================================================
# The audit writer's refusals
# ===========================================================================


def _writer_inputs(online):
    payment = db.session.get(PaymentTransaction, online["payment_id"])
    first = db.session.get(PaymentAuditEvent, online["audit_ids"][0])
    owner, actor = ix.invoice_and_admin(online["w"])
    context = first.after_snapshot["online"]
    return {
        "invoice": owner, "payment": payment, "receipt": None, "reason": None,
        "before_snapshot": first.before_snapshot, "after_snapshot": first.after_snapshot,
        "moment": first.occurred_at,
    }, actor, online_context(context["payment_intent_public_id"],
                             context["provider_event_public_id"])


def test_the_writer_refuses_every_mismatched_origin(app, online):
    inputs, actor, context = _writer_inputs(online)
    other = online_context(context["payment_intent_public_id"], _new_uuid(3))
    before = wx.counts()
    for kind, who, named in (
        (ONLINE_CONFIRMED, actor, context),   # a person never confirms online
        (ONLINE_CONFIRMED, None, None),       # the system names its context
        (ONLINE_CONFIRMED, None, other),      # ... exactly as the snapshots do
        (RECEIPT_ONLINE_ISSUED, actor, context),
        (CASH_RECORDED, actor, context),      # a person's event names no context
        (CASH_RECORDED, actor, None),         # nor records an online payment
        (CASH_RECORDED, None, None),
    ):
        with pytest.raises(ValueError):
            record_payment_event(kind=kind, actor=who, online=named, **inputs)
        assert not db.session.new, kind
    db.session.rollback()
    assert wx.counts() == before


def test_a_person_may_only_reverse_an_online_collection(app, online):
    inputs, actor, _context = _writer_inputs(online)
    assert actor.role == "administrator" and isinstance(actor, User)
    with pytest.raises(ValueError, match="only reverse it"):
        record_payment_event(kind=CASH_RECORDED, actor=actor, **inputs)
    db.session.rollback()
