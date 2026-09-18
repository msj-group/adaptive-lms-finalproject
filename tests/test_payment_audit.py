"""Phase 5 / M05 -- the exact balance, server-built payment snapshots and
receipt documents, and the one payment / receipt audit-event writer.

The balance's exact Decimal arithmetic; the snapshot's and the receipt
document's exact canonical layout and the absence of internal ids; every
refusal of both layout validators; every rule of ``record_payment_event``; the
plain-text descriptions; and a scan proving that nothing in the application
constructs a payment or receipt outside the payment routes, or rewrites or
deletes one anywhere.
"""

import copy
import json
import pathlib
import re
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import tests.fee_assignment_fixtures as fees
import tests.payment_fixtures as px
from app.extensions import db
from app.models import InvoiceItem, PaymentAuditEvent
from app.models.payment_audit_event import PAYMENT_SNAPSHOT_SCHEMA, validate_payment_snapshot
from app.models.receipt import RECEIPT_SNAPSHOT_SCHEMA, validate_receipt_snapshot
from app.services.payment_audit import (
    BANK_CONFIRMED,
    BANK_RECORDED,
    BANK_REJECTED,
    CASH_RECORDED,
    RECEIPT_ISSUED,
    RECEIPT_VOIDED,
    REVERSED,
    build_payment_snapshot,
    build_receipt_document,
    record_payment_event,
)
from app.services.payment_queries import describe_payment_event
from app.services.payment_transactions import payment_balance

_ADMIN = SimpleNamespace(id=3, role="administrator", status="active")
_MOMENT = datetime(2026, 7, 1, 9, 0, 0)


def _invoice(**overrides):
    values = dict(id=7, public_id="i" * 36, invoice_number="INV-2026-000001", status="issued",
                  version=2, currency_code="LYD")
    values.update(overrides)
    return SimpleNamespace(**values)


def _items(*amounts):
    return [SimpleNamespace(amount=Decimal(amount)) for amount in amounts]


_ITEMS = _items("50.0000", "1200.5000")


def _pay(**overrides):
    values = dict(id=11, public_id="p" * 36, invoice_id=7, kind="collection", method="cash",
                  status="confirmed", amount=Decimal("100.0000"), currency_code="LYD",
                  reversal_of_payment_transaction_id=None, confirmed_at=_MOMENT)
    values.update(overrides)
    return SimpleNamespace(**values)


def _receipt(**overrides):
    values = dict(id=21, public_id="r" * 36, receipt_number="RCT-2026-000001", status="issued",
                  payment_transaction_id=11)
    values.update(overrides)
    return SimpleNamespace(**values)


def _values(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _values(value)
    elif isinstance(node, list):
        for value in node:
            yield None, value
            yield from _values(value)


# ===========================================================================
# The balance
# ===========================================================================


def test_the_balance_counts_only_confirmed_rows_in_exact_decimals():
    items = _items("0.1000", "0.2000")
    assert tuple(payment_balance(items, [])) == (Decimal("0.3"), Decimal(0), Decimal("0.3"))
    rows = [
        _pay(amount=Decimal("0.1000")),
        _pay(id=12, method="bank_transfer", status="pending", amount=Decimal("0.2000")),
        _pay(id=13, method="bank_transfer", status="rejected", amount=Decimal("0.2000")),
        _pay(id=14, method="bank_transfer", amount=Decimal("0.1500")),
    ]
    assert tuple(payment_balance(items, rows)) == (
        Decimal("0.3"), Decimal("0.25"), Decimal("0.05"))
    rows.append(_pay(id=15, kind="reversal", reversal_of_payment_transaction_id=11,
                     amount=Decimal("0.1000")))
    assert tuple(payment_balance(items, rows)) == (
        Decimal("0.3"), Decimal("0.15"), Decimal("0.15"))
    many = _items(*["99999.999"] * 20)
    assert payment_balance(many, [_pay(amount=Decimal("99999.999"))]).outstanding == Decimal(
        "1899999.981")


def test_a_balance_the_records_cannot_describe_is_none_and_floats_are_refused():
    assert payment_balance(_ITEMS, [_pay(amount=Decimal("1250.5001"))]) is None
    assert payment_balance(_ITEMS, [_pay(kind="reversal", reversal_of_payment_transaction_id=9)]) is None
    with pytest.raises(ValueError):
        payment_balance(_items("1"), [_pay(amount=0.1)])
    with pytest.raises(ValueError):
        payment_balance([SimpleNamespace(amount=1.5)], [])


# ===========================================================================
# Payment snapshots
# ===========================================================================


def _stored_world():
    actor = fees.admin()
    assignment = fees.assignment(fees.enrollment(), fees.active_plan(actor), actor)
    owner = px.issued_invoice(assignment, actor)
    items = InvoiceItem.query.filter_by(invoice_id=owner.id, status="active").all()
    return actor, assignment, owner, items


def test_a_payment_snapshot_is_canonical_complete_and_free_of_internal_ids(app):
    with app.app_context():
        actor, assignment, owner, items = _stored_world()
        original, voided, reversal = px.reversed_collection(owner, actor, amount="100.000")
        pending = px.payment(owner, actor, method="bank_transfer", status="pending", amount="20")
        cash = px.payment(owner, actor, amount="250.25")
        payments = [original, reversal, pending, cash]
        snapshot = build_payment_snapshot(owner, items, payments, payment=reversal)
        assert snapshot == {
            "schema": PAYMENT_SNAPSHOT_SCHEMA,
            "invoice_public_id": owner.public_id,
            "invoice_number": owner.invoice_number,
            "invoice_status": "issued",
            "currency_code": "LYD",
            "invoice_total": "1250.5000",
            "paid_amount": "250.2500",
            "outstanding_amount": "1000.2500",
            "payment": {"public_id": reversal.public_id, "kind": "reversal", "method": "cash",
                        "status": "confirmed", "amount": "100.0000",
                        "reversal_of_public_id": original.public_id},
            "receipt": None,
        }
        with_receipt = build_payment_snapshot(owner, items, payments, payment=original,
                                              receipt=voided)
        assert with_receipt["receipt"] == {"public_id": voided.public_id,
                                           "receipt_number": voided.receipt_number,
                                           "status": "voided"}
        text = json.dumps(with_receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert json.loads(text) == with_receipt

        internal = {str(value) for value in (owner.id, assignment.id, actor.id, original.id,
                                              reversal.id, pending.id, cash.id, voided.id,
                                              *[item.id for item in items])}
        for key, value in _values(with_receipt):
            assert value is None or isinstance(value, (str, dict, list)), (key, value)
            if isinstance(value, str):
                assert value not in internal, (key, value)
            if key is not None:
                assert "id" not in key.split("_") or key.endswith("public_id"), key
        for secret in ("REF-", "Not received", "Student One", "Standard plan"):
            assert secret not in text, secret


def test_a_snapshot_is_refused_when_its_records_cannot_describe_it():
    with pytest.raises(ValueError):
        build_payment_snapshot(_invoice(), _ITEMS, [_pay(amount=Decimal("99999.0000"))] * 2)
    orphan = _pay(id=12, kind="reversal", reversal_of_payment_transaction_id=99)
    with pytest.raises(ValueError):
        build_payment_snapshot(_invoice(), _ITEMS, [_pay(), orphan], payment=orphan)
    with pytest.raises(ValueError):
        build_payment_snapshot(_invoice(status="draft", invoice_number=None), _ITEMS, [])


def _valid_snapshot():
    return {
        "schema": PAYMENT_SNAPSHOT_SCHEMA,
        "invoice_public_id": "i" * 36,
        "invoice_number": "INV-2026-000001",
        "invoice_status": "issued",
        "currency_code": "LYD",
        "invoice_total": "1250.5000",
        "paid_amount": "100.0000",
        "outstanding_amount": "1150.5000",
        "payment": {"public_id": "p" * 36, "kind": "collection", "method": "cash",
                    "status": "confirmed", "amount": "100.0000", "reversal_of_public_id": None},
        "receipt": {"public_id": "r" * 36, "receipt_number": "RCT-2026-000001",
                    "status": "issued"},
    }


def _set(path, value):
    def mutate(snapshot):
        target = snapshot
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = value
    return mutate


def _delete(key):
    def mutate(snapshot):
        del snapshot[key]
    return mutate


_SNAPSHOT_MUTATIONS = {
    "extra card number": _set(["card_number"], "4111111111111111"),
    "extra account number": _set(["payment", "account_number"], "123"),
    "extra transfer reference": _set(["payment", "bank_transfer_reference"], "TRX"),
    "extra csrf token": _set(["csrf_token"], "x"),
    "internal payment id": _set(["payment", "id"], 11),
    "missing outstanding": _delete("outstanding_amount"),
    "unknown schema": _set(["schema"], "phase5-m04.invoice.v1"),
    "draft invoice": _set(["invoice_status"], "draft"),
    "cancelled invoice": _set(["invoice_status"], "cancelled"),
    "no invoice number": _set(["invoice_number"], None),
    "foreign currency": _set(["currency_code"], "USD"),
    "integer invoice id": _set(["invoice_public_id"], 7),
    "float total": _set(["invoice_total"], 1250.5),
    "short paid": _set(["paid_amount"], "100.0"),
    "negative outstanding": _set(["outstanding_amount"], "-1.0000"),
    "paid and outstanding not adding up": _set(["outstanding_amount"], "1150.4000"),
    "leading zero": _set(["paid_amount"], "0100.0000"),
    "unknown payment kind": _set(["payment", "kind"], "refund"),
    "unknown method": _set(["payment", "method"], "card"),
    "unknown payment status": _set(["payment", "status"], "paid"),
    "pending cash": _set(["payment", "status"], "pending"),
    "zero payment": _set(["payment", "amount"], "0.0000"),
    "payment over the maximum": _set(["payment", "amount"], "100000.0000"),
    "collection reversing something": _set(["payment", "reversal_of_public_id"], "x" * 36),
    "reversal naming nothing": _set(["payment", "kind"], "reversal"),
    "payment not a dict": _set(["payment"], "p" * 36),
    "receipt of a pending transfer": lambda s: s["payment"].update(method="bank_transfer",
                                                                  status="pending"),
    "receipt without a payment": _set(["payment"], None),
    "malformed receipt number": _set(["receipt", "receipt_number"], "RCT-2026-000000"),
    "unknown receipt status": _set(["receipt", "status"], "deleted"),
    "receipt with extra key": _set(["receipt", "snapshot"], {}),
}


def test_the_valid_payment_layout_is_accepted_and_copied():
    original = _valid_snapshot()
    checked = validate_payment_snapshot(original)
    assert checked == original and checked is not original


@pytest.mark.parametrize("name", sorted(_SNAPSHOT_MUTATIONS))
def test_the_payment_snapshot_validator_refuses_anything_but_the_exact_layout(name):
    snapshot = copy.deepcopy(_valid_snapshot())
    _SNAPSHOT_MUTATIONS[name](snapshot)
    with pytest.raises(ValueError):
        validate_payment_snapshot(snapshot)


# ===========================================================================
# Receipt documents
# ===========================================================================


def test_a_receipt_document_is_canonical_and_free_of_internal_ids(app):
    with app.app_context():
        actor, assignment, owner, _items_ = _stored_world()
        pay = px.payment(owner, actor, method="bank_transfer", status="confirmed",
                         amount="200.5", reference="TRX-SECRET-REF")
        document = build_receipt_document(
            receipt_public_id="r" * 36, receipt_number="RCT-2026-000042", payment=pay,
            invoice=owner, assignment_public_id=assignment.public_id,
            confirmed_by_name="Admin A", student_name="Student One", group_name="Group A",
            course_title="Course A", academic_term_name="Term A")
        assert document == {
            "schema": RECEIPT_SNAPSHOT_SCHEMA,
            "receipt_public_id": "r" * 36,
            "receipt_number": "RCT-2026-000042",
            "payment_public_id": pay.public_id,
            "invoice_public_id": owner.public_id,
            "invoice_number": owner.invoice_number,
            "student_fee_assignment_public_id": assignment.public_id,
            "method": "bank_transfer",
            "amount": "200.5000",
            "currency_code": "LYD",
            "confirmed_at": "2026-07-02T09:00:00Z",
            "confirmed_by_name": "Admin A",
            "student_name": "Student One",
            "group_name": "Group A",
            "course_title": "Course A",
            "academic_term_name": "Term A",
        }
        internal = {str(value) for value in (owner.id, assignment.id, actor.id, pay.id)}
        for key, value in document.items():
            assert value not in internal, key
            assert "id" not in key.split("_") or key.endswith("public_id"), key
        assert "TRX-SECRET-REF" not in json.dumps(document)
        for refused in (px.payment(owner, actor, method="bank_transfer", status="pending"),
                        px.payment(owner, actor, kind="reversal", reversal_of=pay)):
            with pytest.raises(ValueError):
                build_receipt_document(
                    receipt_public_id="r" * 36, receipt_number="RCT-2026-000043",
                    payment=refused, invoice=owner, assignment_public_id=assignment.public_id,
                    confirmed_by_name="A", student_name="S", group_name="G", course_title="C",
                    academic_term_name="T")


def _valid_document():
    return {
        "schema": RECEIPT_SNAPSHOT_SCHEMA, "receipt_public_id": "r" * 36,
        "receipt_number": "RCT-2026-000001", "payment_public_id": "p" * 36,
        "invoice_public_id": "i" * 36, "invoice_number": "INV-2026-000001",
        "student_fee_assignment_public_id": "a" * 36, "method": "cash", "amount": "100.0000",
        "currency_code": "LYD", "confirmed_at": "2026-07-01T09:00:00Z",
        "confirmed_by_name": "Admin", "student_name": "Student", "group_name": "Group",
        "course_title": "Course", "academic_term_name": "Term",
    }


_DOCUMENT_MUTATIONS = {
    "card number": _set(["card_number"], "4111111111111111"),
    "session value": _set(["session"], "abc"),
    "internal id": _set(["payment_id"], 11),
    "missing student": _delete("student_name"),
    "unknown schema": _set(["schema"], "v0"),
    "malformed receipt number": _set(["receipt_number"], "RCT-2026-0001"),
    "draft invoice number": _set(["invoice_number"], None),
    "unknown method": _set(["method"], "card"),
    "foreign currency": _set(["currency_code"], "EUR"),
    "rounded amount": _set(["amount"], "100.00"),
    "float amount": _set(["amount"], 100.0),
    "zero amount": _set(["amount"], "0.0000"),
    "local moment": _set(["confirmed_at"], "2026-07-01 09:00:00"),
    "impossible moment": _set(["confirmed_at"], "2026-02-30T09:00:00Z"),
    "empty name": _set(["group_name"], ""),
    "nested name": _set(["student_name"], {"first": "A"}),
    "oversized name": _set(["course_title"], "x" * 256),
    "oversized public id": _set(["invoice_public_id"], "x" * 37),
}


def test_the_valid_receipt_layout_is_accepted_and_copied():
    original = _valid_document()
    checked = validate_receipt_snapshot(original)
    assert checked == original and checked is not original


@pytest.mark.parametrize("name", sorted(_DOCUMENT_MUTATIONS))
def test_the_receipt_validator_refuses_anything_but_the_exact_layout(name):
    document = copy.deepcopy(_valid_document())
    _DOCUMENT_MUTATIONS[name](document)
    with pytest.raises(ValueError):
        validate_receipt_snapshot(document)


# ===========================================================================
# The writer
# ===========================================================================


def _change(kind):
    """The writer's keyword arguments for one legitimate event of `kind`,
    built from plain objects."""
    invoice = _invoice()
    receipt = None
    reason = None
    if kind in (CASH_RECORDED, BANK_RECORDED):
        payment = _pay() if kind == CASH_RECORDED else _pay(method="bank_transfer",
                                                            status="pending")
        before = build_payment_snapshot(invoice, _ITEMS, [])
        after = build_payment_snapshot(invoice, _ITEMS, [payment], payment=payment)
    elif kind in (BANK_CONFIRMED, BANK_REJECTED):
        payment = _pay(method="bank_transfer", status="pending")
        before = build_payment_snapshot(invoice, _ITEMS, [payment], payment=payment)
        payment.status = "confirmed" if kind == BANK_CONFIRMED else "rejected"
        after = build_payment_snapshot(invoice, _ITEMS, [payment], payment=payment)
        reason = "Not received" if kind == BANK_REJECTED else None
    elif kind == REVERSED:
        original = _pay()
        payment = _pay(id=12, public_id="v" * 36, kind="reversal",
                       reversal_of_payment_transaction_id=11)
        before = build_payment_snapshot(invoice, _ITEMS, [original])
        after = build_payment_snapshot(invoice, _ITEMS, [original, payment], payment=payment)
        reason = "Wrong amount"
    elif kind == RECEIPT_ISSUED:
        payment, receipt = _pay(), _receipt()
        before = build_payment_snapshot(invoice, _ITEMS, [payment], payment=payment)
        after = build_payment_snapshot(invoice, _ITEMS, [payment], payment=payment,
                                       receipt=receipt)
    else:
        payment, receipt = _pay(), _receipt()
        rows = [payment, _pay(id=12, public_id="v" * 36, kind="reversal",
                              reversal_of_payment_transaction_id=11)]
        before = build_payment_snapshot(invoice, _ITEMS, rows, payment=payment, receipt=receipt)
        receipt.status = "voided"
        after = build_payment_snapshot(invoice, _ITEMS, rows, payment=payment, receipt=receipt)
        reason = "Wrong amount"
    return dict(invoice=invoice, actor=_ADMIN, kind=kind, payment=payment, receipt=receipt,
                before_snapshot=before, after_snapshot=after, reason=reason, moment=_MOMENT)


_KINDS = [CASH_RECORDED, BANK_RECORDED, BANK_CONFIRMED, BANK_REJECTED, REVERSED, RECEIPT_ISSUED,
          RECEIPT_VOIDED]


def _pending_events():
    return [obj for obj in db.session.new if isinstance(obj, PaymentAuditEvent)]


@pytest.mark.parametrize("kind", _KINDS)
def test_the_writer_adds_exactly_one_event_for_each_legitimate_movement(app, kind):
    with app.app_context():
        values = _change(kind)
        entry = record_payment_event(**values)
        assert _pending_events() == [entry]
        assert (entry.invoice_id, entry.actor_id, entry.kind, entry.occurred_at,
                entry.invoice_version_before, entry.invoice_version_after, entry.reason,
                entry.payment_transaction_id, entry.receipt_id) == (
            7, 3, kind, _MOMENT, 2, 2, values["reason"], values["payment"].id,
            None if values["receipt"] is None else 21)
        assert entry.before_snapshot == values["before_snapshot"]
        assert entry.after_snapshot == values["after_snapshot"]
        db.session.expunge(entry)


def _with(change, **overrides):
    values = _change(change)
    values.update(overrides)
    return values


def _mutated(change, mutate):
    values = _change(change)
    mutate(values)
    return values


_REFUSED = {
    "no actor": lambda: _with(CASH_RECORDED, actor=None),
    "a teacher": lambda: _with(CASH_RECORDED, actor=SimpleNamespace(id=3, role="teacher",
                                                                    status="active")),
    "a suspended administrator": lambda: _with(CASH_RECORDED, actor=SimpleNamespace(
        id=3, role="administrator", status="suspended")),
    "an invoice event kind": lambda: _with(CASH_RECORDED, kind="invoice_issued"),
    "an unknown kind": lambda: _with(CASH_RECORDED, kind="payment_refunded"),
    "a draft invoice": lambda: _with(CASH_RECORDED, invoice=_invoice(status="draft")),
    "an unsaved invoice": lambda: _with(CASH_RECORDED, invoice=_invoice(id=None)),
    "a payment of another invoice": lambda: _mutated(
        CASH_RECORDED, lambda v: setattr(v["payment"], "invoice_id", 8)),
    "an unsaved payment": lambda: _mutated(CASH_RECORDED, lambda v: setattr(v["payment"], "id", None)),
    "a payment event naming a receipt": lambda: _with(CASH_RECORDED, receipt=_receipt()),
    "a receipt event without its receipt": lambda: _with(RECEIPT_ISSUED, receipt=None),
    "a receipt of another payment": lambda: _mutated(
        RECEIPT_ISSUED, lambda v: setattr(v["receipt"], "payment_transaction_id", 99)),
    "a rejection without a reason": lambda: _with(BANK_REJECTED, reason=None),
    "a reversal with a blank reason": lambda: _with(REVERSED, reason="   "),
    "a void with a padded reason": lambda: _with(RECEIPT_VOIDED, reason=" Wrong amount "),
    "a cash payment with a reason": lambda: _with(CASH_RECORDED, reason="Why"),
    "a confirmation with a reason": lambda: _with(BANK_CONFIRMED, reason="Why"),
    "no before snapshot": lambda: _with(BANK_CONFIRMED, before_snapshot=None),
    "an invoice snapshot": lambda: _with(CASH_RECORDED, after_snapshot={
        "schema": "phase5-m04.invoice.v1"}),
    "a snapshot of another invoice": lambda: _mutated(
        CASH_RECORDED, lambda v: v["after_snapshot"].update(invoice_public_id="z" * 36)),
    "a change that changed nothing": lambda: _mutated(
        CASH_RECORDED, lambda v: v.update(before_snapshot=v["after_snapshot"])),
    "a cash event whose row is no longer what the snapshot shows": lambda: _mutated(
        CASH_RECORDED, lambda v: setattr(v["payment"], "amount", Decimal("90.0000"))),
    "a cash event labelled as a transfer recording": lambda: _with(CASH_RECORDED,
                                                                   kind=BANK_RECORDED),
    "a confirmation whose before shows it confirmed": lambda: _mutated(
        BANK_CONFIRMED, lambda v: v.update(before_snapshot=build_payment_snapshot(
            _invoice(), _ITEMS, [], None))),
    "a recording whose before shows the payment": lambda: _mutated(
        BANK_RECORDED, lambda v: v.update(before_snapshot=build_payment_snapshot(
            _invoice(), _ITEMS, [v["payment"]], payment=v["payment"]))),
    "a rejection labelled as a confirmation": lambda: _with(BANK_REJECTED, kind=BANK_CONFIRMED,
                                                            reason=None),
    "a receipt issue whose after shows no receipt": lambda: _mutated(
        RECEIPT_ISSUED, lambda v: v.update(after_snapshot=dict(
            build_payment_snapshot(_invoice(), _ITEMS, [v["payment"]], payment=v["payment"]),
            paid_amount="100.0000"))),
    "a void whose receipt is still issued": lambda: _mutated(
        RECEIPT_VOIDED, lambda v: setattr(v["receipt"], "status", "issued")),
    "a snapshot naming another receipt": lambda: _mutated(
        RECEIPT_ISSUED, lambda v: v["after_snapshot"]["receipt"].update(
            receipt_number="RCT-2026-000999")),
    "a cash event that did not move the paid amount": lambda: _mutated(
        CASH_RECORDED, lambda v: v.update(before_snapshot=dict(
            v["after_snapshot"], payment=None))),
    "a reversal that moved the paid amount the wrong way": lambda: _mutated(
        REVERSED, lambda v: v.update(before_snapshot=build_payment_snapshot(
            _invoice(), _ITEMS, []))),
    "an event whose invoice total moved": lambda: _mutated(
        BANK_REJECTED, lambda v: v.update(before_snapshot=build_payment_snapshot(
            _invoice(), _items("50.0000", "1300.0000"), [v["payment"]],
            payment=_pay(method="bank_transfer", status="pending")))),
}


@pytest.mark.parametrize("name", sorted(_REFUSED))
def test_the_writer_refuses_and_adds_nothing(app, name):
    with app.app_context():
        with pytest.raises(ValueError):
            record_payment_event(**_REFUSED[name]())
        assert _pending_events() == []


def test_a_real_payment_event_is_stored_as_written(app):
    with app.app_context():
        actor, _assignment, owner, items = _stored_world()
        pay = px.payment(owner, actor, amount="10")
        before = build_payment_snapshot(owner, items, [])
        after = build_payment_snapshot(owner, items, [pay], payment=pay)
        record_payment_event(invoice=owner, actor=actor, kind=CASH_RECORDED, payment=pay,
                             receipt=None, before_snapshot=before, after_snapshot=after,
                             reason=None, moment=_MOMENT)
        db.session.commit()
        (stored,) = PaymentAuditEvent.query.all()
        assert (stored.kind, stored.payment_transaction_id, stored.receipt_id,
                stored.invoice_version_before, stored.invoice_version_after) == (
            CASH_RECORDED, pay.id, None, owner.version, owner.version)
        assert (stored.before_snapshot, stored.after_snapshot) == (before, after)
        assert stored.after_snapshot["outstanding_amount"] == "1240.5000"


# ===========================================================================
# Presentation, and nothing else writes or rewrites a payment
# ===========================================================================


def test_payment_events_are_described_from_their_snapshots(app):
    with app.app_context():
        described = {kind: describe_payment_event(kind, values["before_snapshot"],
                                                  values["after_snapshot"])
                     for kind, values in ((kind, _change(kind)) for kind in _KINDS)}
    assert described[CASH_RECORDED] == [
        "Cash payment of 100.000 LYD recorded and confirmed.",
        "Paid changed from 0.000 to 100.000 LYD; outstanding is now 1,150.500 LYD."]
    assert described[BANK_RECORDED] == ["Bank transfer of 100.000 LYD recorded as pending."]
    assert described[BANK_CONFIRMED][0] == "Bank transfer of 100.000 LYD confirmed."
    assert described[BANK_REJECTED] == ["Bank transfer of 100.000 LYD rejected."]
    assert described[REVERSED] == [
        "The cash payment of 100.000 LYD was reversed in full.",
        "Paid changed from 100.000 to 0.000 LYD; outstanding is now 1,250.500 LYD."]
    assert described[RECEIPT_ISSUED] == ["Receipt RCT-2026-000001 issued."]
    assert described[RECEIPT_VOIDED] == ["Receipt RCT-2026-000001 is now void."]


def test_only_the_payment_routes_create_payments_and_receipts_and_nothing_rewrites_them(app):
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    payments, receipts, rewriting = set(), set(), []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        name = path.relative_to(root).as_posix()
        if re.search(r"(?<!class )\bPaymentTransaction\(", text):
            payments.add(name)
        if re.search(r"(?<!class )\bReceipt\(", text):
            receipts.add(name)
        for pattern in (r"(PaymentTransaction|Receipt|ReceiptNumberSequence)\)\.(update|delete)",
                        r"(update|delete)\(\s*(PaymentTransaction|Receipt|ReceiptNumberSequence)\b",
                        r"(payment_transactions|receipts|receipt_number_sequences)\s+SET",
                        r"DELETE\s+FROM\s+(payment_transactions|receipts|receipt_number_sequences)"):
            if re.search(pattern, text, re.I):
                rewriting.append((name, pattern))
    assert payments == {"blueprints/admin/payments.py"}
    assert receipts == {"blueprints/admin/payments.py"}
    assert rewriting == []
    for template in (root / "templates").rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert not re.search(r'action="[^"]*(delete|refund|restore|reissue)', text, re.I), template
    for rule in app.url_map.iter_rules():
        if "payment" in rule.rule or "receipt" in rule.rule and rule.rule.startswith("/admin"):
            for fragment in ("delete", "edit", "refund", "restore", "reissue", "webhook"):
                assert fragment not in rule.rule, rule.rule
