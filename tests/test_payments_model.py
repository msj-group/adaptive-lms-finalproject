"""Phase 5 / M05 -- the PaymentTransaction, Receipt and ReceiptNumberSequence
models, and the payment extension of PaymentAuditEvent.

Application validators, defaults and the two bank-transfer input parsers
first; then every database CHECK, unique constraint and foreign key driven
with raw SQL, so no validator can mask a missing constraint; then the ORM
guards that refuse editing or deleting a payment, a receipt, a sequence or an
event; then the absence of any relationship, cascade, card, credential or
stored-balance column; and finally the rules the database deliberately leaves
to the application.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

import tests.fee_assignment_fixtures as fees
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
from app.extensions import db
from app.models import (
    FinancialHistoryError,
    InvoiceItem,
    PaymentAuditEvent,
    PaymentMethod,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    Receipt,
    ReceiptNumberSequence,
    ReceiptStatus,
)
from app.models.fee_plan import TEXT_CONTROL, TEXT_MISSING, TEXT_TOO_LONG
from app.models.payment_transaction import (
    DATE_FORMAT,
    DATE_IN_FUTURE,
    DATE_TOO_EARLY,
    REFERENCE_CARD_LIKE,
    normalize_bank_transfer_reference,
    parse_bank_transfer_date,
)
from app.models.receipt_number_sequence import format_receipt_number, receipt_number_is_valid
from app.services.payment_audit import build_payment_snapshot

_T0 = "'2026-07-01 09:00:00'"
_T1 = "'2026-07-02 09:00:00'"
_T2 = "'2026-07-03 09:00:00'"
_EARLIER = "'2026-06-30 09:00:00'"
_SNAPSHOT = "'{\"schema\": \"raw\"}'"


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


def _insert(table, values):
    return f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(values.values())})"


def _counts():
    return (PaymentTransaction.query.count(), Receipt.query.count(),
            ReceiptNumberSequence.query.count(), PaymentAuditEvent.query.count())


# ===========================================================================
# Closed sets, defaults, validators and parsers
# ===========================================================================


def test_the_closed_sets_are_exact():
    assert [m.value for m in PaymentTransactionKind] == ["collection", "reversal"]
    # Phase 5 / M07 added the generic ``online`` method.
    assert [m.value for m in PaymentMethod] == ["cash", "bank_transfer", "online"]
    assert [m.value for m in PaymentTransactionStatus] == ["pending", "confirmed", "rejected"]
    assert [m.value for m in ReceiptStatus] == ["issued", "voided"]


def test_defaults_public_ids_and_timestamp_hooks(app):
    with app.app_context():
        actor, owner = _owners()
        first, second = px.payment(owner, actor), px.payment(owner, actor)
        assert len(first.public_id) == 36 and first.public_id != second.public_id
        assert (first.currency_code, first.version, first.amount) == ("LYD", 1, Decimal("100.0000"))
        assert first.is_collection and first.is_confirmed
        assert not (first.is_pending or first.is_rejected or first.is_reversal)
        issued = px.receipt(first, actor)
        assert (issued.status, issued.version, issued.is_issued, issued.is_voided) == (
            "issued", 1, True, False)
        counter = ReceiptNumberSequence(calendar_year=2026, created_at=fx.CREATED_AT,
                                        updated_at=fx.CREATED_AT)
        db.session.add(counter)
        db.session.commit()
        assert counter.last_number == 0
        for model in (PaymentTransaction, Receipt, ReceiptNumberSequence):
            columns = model.__table__.c
            assert columns.created_at.default.is_callable and columns.updated_at.default.is_callable
            assert columns.created_at.onupdate is None and columns.updated_at.onupdate is None
        assert PaymentTransaction.__table__.c.public_id.default.is_callable
        assert Receipt.__table__.c.public_id.default.is_callable
        assert "public_id" not in ReceiptNumberSequence.__table__.c
        assert PaymentTransaction.__table__.c.recorded_at.default is None


@pytest.mark.parametrize(
    "model, field, value",
    [
        (PaymentTransaction, "kind", "refund"),
        (PaymentTransaction, "kind", "COLLECTION"),
        (PaymentTransaction, "method", "card"),
        (PaymentTransaction, "method", "gateway"),
        (PaymentTransaction, "status", "paid"),
        (PaymentTransaction, "status", "refunded"),
        (PaymentTransaction, "currency_code", "USD"),
        (PaymentTransaction, "amount", 10.5),
        (PaymentTransaction, "amount", 10),
        (PaymentTransaction, "amount", "0"),
        (PaymentTransaction, "amount", "-5"),
        (PaymentTransaction, "amount", "1,0"),
        (PaymentTransaction, "amount", "1.00001"),
        (PaymentTransaction, "amount", Decimal("100000")),
        (PaymentTransaction, "bank_transfer_reference", " padded"),
        (PaymentTransaction, "bank_transfer_reference", ""),
        (PaymentTransaction, "bank_transfer_reference", "x" * 65),
        (PaymentTransaction, "bank_transfer_reference", "a\x00b"),
        (PaymentTransaction, "bank_transfer_reference", "4111 1111 1111 1111"),
        (PaymentTransaction, "bank_transfer_date", datetime(2026, 6, 30, 9, 0)),
        (PaymentTransaction, "bank_transfer_date", "2026-06-30"),
        (PaymentTransaction, "bank_transfer_date", date(1999, 12, 31)),
        (PaymentTransaction, "rejection_reason", " padded"),
        (PaymentTransaction, "rejection_reason", "bad\x00byte"),
        (PaymentTransaction, "rejection_reason", "x" * 501),
        (PaymentTransaction, "version", 0),
        (PaymentTransaction, "version", True),
        (Receipt, "status", "void"),
        (Receipt, "receipt_number", "RCT-2026-000000"),
        (Receipt, "receipt_number", "INV-2026-000001"),
        (Receipt, "receipt_number", None),
        (Receipt, "void_reason", " padded"),
        (Receipt, "void_reason", "x" * 501),
        (Receipt, "snapshot", None),
        (Receipt, "snapshot", {"card_number": "4111111111111111"}),
        (Receipt, "version", 0),
        (ReceiptNumberSequence, "calendar_year", 999),
        (ReceiptNumberSequence, "calendar_year", "2026"),
        (ReceiptNumberSequence, "last_number", -1),
        (ReceiptNumberSequence, "last_number", 1000000),
        (PaymentAuditEvent, "kind", "payment_refunded"),
        (PaymentAuditEvent, "after_snapshot", {"schema": "phase5-m05.payment.v1"}),
    ],
)
def test_validators_refuse_bad_values(app, model, field, value):
    with app.app_context():
        with pytest.raises(ValueError):
            model(**{field: value})


def test_receipt_number_helpers():
    assert format_receipt_number(2026, 1) == "RCT-2026-000001"
    assert format_receipt_number(2026, 999999) == "RCT-2026-999999"
    for year, number in ((2026, 0), (2026, 1000000), (999, 1), (True, 1), ("2026", 1)):
        with pytest.raises(ValueError):
            format_receipt_number(year, number)
    assert receipt_number_is_valid("RCT-2026-000001")
    for bad in ("rct-2026-000001", "RCT-2026-000000", "RCT-0999-000001", "RCT-2026-0000001",
                "INV-2026-000001", "RCT-２０２６-000001", " RCT-2026-000001", None, 5):
        assert not receipt_number_is_valid(bad), bad


def test_bank_transfer_references_are_normalized_and_never_card_numbers():
    assert normalize_bank_transfer_reference("  TRX   2026/15 ") == ("TRX 2026/15", None)
    assert normalize_bank_transfer_reference("") == (None, TEXT_MISSING)
    assert normalize_bank_transfer_reference(None) == (None, TEXT_MISSING)
    assert normalize_bank_transfer_reference("a\x07b") == (None, TEXT_CONTROL)
    assert normalize_bank_transfer_reference("a‮b") == (None, TEXT_CONTROL)
    assert normalize_bank_transfer_reference("x" * 65) == (None, TEXT_TOO_LONG)
    assert normalize_bank_transfer_reference("x" * 64) == ("x" * 64, None)
    for card in ("4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111",
                 "REF 4111111111111111", "5500000000000004", "340000000000009"):
        assert normalize_bank_transfer_reference(card) == (None, REFERENCE_CARD_LIKE), card
    for reference in ("TRX-0001", "4111111111111112", "123456789012", "12345678901234567890",
                      "BANK/2026/0915"):
        assert normalize_bank_transfer_reference(reference) == (reference, None), reference


def test_bank_transfer_dates_are_real_civil_dates_no_later_than_today():
    latest = date(2026, 9, 15)
    assert parse_bank_transfer_date("2026-09-15", latest) == (date(2026, 9, 15), None)
    assert parse_bank_transfer_date(" 2000-01-01 ", latest) == (date(2000, 1, 1), None)
    for raw, code in (("", TEXT_MISSING), (None, TEXT_MISSING), ("15/09/2026", DATE_FORMAT),
                      ("2026-02-30", DATE_FORMAT), ("٢٠٢٦-٠٩-١٥", DATE_FORMAT),
                      ("2026-9-15", DATE_FORMAT), ("1999-12-31", DATE_TOO_EARLY),
                      ("2026-09-16", DATE_IN_FUTURE)):
        assert parse_bank_transfer_date(raw, latest) == (None, code), raw


# ===========================================================================
# Database constraints, driven with raw SQL
# ===========================================================================


def _payment_sql(owner_id, author_id, **overrides):
    values = {
        "public_id": f"'p-{fx._next()}'",
        "invoice_id": str(owner_id),
        "kind": "'collection'",
        "method": "'cash'",
        "status": "'confirmed'",
        "currency_code": "'LYD'",
        "amount": "100",
        "bank_transfer_reference": "NULL",
        "bank_transfer_date": "NULL",
        "recorded_at": _T0,
        "recorded_by_id": str(author_id),
        "confirmed_at": _T0,
        "confirmed_by_id": str(author_id),
        "rejected_at": "NULL",
        "rejected_by_id": "NULL",
        "rejection_reason": "NULL",
        "reversal_of_payment_transaction_id": "NULL",
        "version": "1",
        "created_at": _T0,
        "updated_at": _T0,
    }
    values.update(overrides)
    return _insert("payment_transactions", values)


def test_every_payment_transaction_constraint_is_enforced(app):
    with app.app_context():
        actor, owner = _owners()
        other = fees.admin("other@example.com")
        i, u, o = owner.id, str(actor.id), str(other.id)
        bank = {"method": "'bank_transfer'", "bank_transfer_reference": "'TRX-1'",
                "bank_transfer_date": "'2026-06-30'"}
        pending = dict(bank, status="'pending'", confirmed_at="NULL", confirmed_by_id="NULL")
        confirmed_bank = dict(bank, confirmed_at=_T1, version="2", updated_at=_T1)
        rejected = dict(pending, status="'rejected'", rejected_at=_T1, rejected_by_id=u,
                        rejection_reason="'Not received'", version="2", updated_at=_T1)
        _accepted(_payment_sql(i, u, public_id="'legal-cash'"))
        original = db.session.execute(sa.text(
            "SELECT id FROM payment_transactions WHERE public_id = 'legal-cash'")).scalar()
        reversal = {"kind": "'reversal'", "reversal_of_payment_transaction_id": str(original),
                    "recorded_at": _T2, "confirmed_at": _T2, "created_at": _T2, "updated_at": _T2}
        for accepted in (pending, confirmed_bank, rejected, reversal, {"amount": "0.001"},
                         {"amount": "99999.999"}, {"amount": "12.3456"}):
            _accepted(_payment_sql(i, u, **accepted))

        for overrides, rule in (
            ({"public_id": "'legal-cash'"}, "public_id uniqueness"),
            ({"kind": "'refund'"}, "ck_payment_transactions_kind_valid"),
            ({"method": "'card'"}, "ck_payment_transactions_method_valid"),
            ({"status": "'paid'"}, "ck_payment_transactions_status_valid"),
            ({"currency_code": "'USD'"}, "ck_payment_transactions_currency_code"),
            ({"amount": "0"}, "a zero amount"),
            ({"amount": "0.0009"}, "an amount below the minimum"),
            ({"amount": "100000"}, "an amount above the maximum"),
            ({"amount": "-5"}, "a negative amount"),
            ({"version": "0"}, "ck_payment_transactions_version_positive"),
            ({"bank_transfer_reference": "'TRX'"}, "a cash payment with a reference"),
            ({"bank_transfer_date": "'2026-06-30'"}, "a cash payment with a transfer date"),
            (dict(pending, bank_transfer_reference="NULL"), "a transfer without its reference"),
            (dict(pending, bank_transfer_reference="''"), "a transfer with an empty reference"),
            (dict(pending, bank_transfer_date="NULL"), "a transfer without its date"),
            (dict(reversal, **bank), "a reversal with transfer details"),
            ({"confirmed_by_id": "NULL"}, "the confirmation pair (actor)"),
            (dict(pending, confirmed_by_id=u), "the confirmation pair (moment)"),
            (dict(rejected, rejection_reason="NULL"), "a rejection without its reason"),
            (dict(rejected, rejection_reason="''"), "a rejection with an empty reason"),
            (dict(rejected, rejected_by_id="NULL"), "a rejection without its actor"),
            ({"rejection_reason": "'Why'"}, "a reason without a rejection"),
            ({"status": "'pending'", "confirmed_at": "NULL", "confirmed_by_id": "NULL"},
             "a pending cash payment"),
            (dict(reversal, status="'pending'", confirmed_at="NULL", confirmed_by_id="NULL"),
             "a pending reversal"),
            (dict(rejected, method="'cash'", bank_transfer_reference="NULL",
                  bank_transfer_date="NULL"), "a rejected cash payment"),
            ({"confirmed_at": "NULL", "confirmed_by_id": "NULL"},
             "a confirmed payment without its confirmation"),
            (dict(pending, confirmed_at=_T1, confirmed_by_id=u, updated_at=_T1),
             "a pending transfer carrying a confirmation"),
            (dict(rejected, confirmed_at=_T1, confirmed_by_id=u),
             "a rejected transfer carrying a confirmation"),
            (dict(confirmed_bank, rejected_at=_T1, rejected_by_id=u, rejection_reason="'x'"),
             "a confirmed transfer carrying a rejection"),
            ({"confirmed_at": _T1, "updated_at": _T1}, "cash confirmed later than it was recorded"),
            ({"confirmed_by_id": o}, "cash confirmed by somebody else"),
            (dict(reversal, confirmed_by_id=o), "a reversal confirmed by somebody else"),
            ({"reversal_of_payment_transaction_id": str(original)},
             "a collection naming a reversed row"),
            (dict(reversal, reversal_of_payment_transaction_id="NULL"), "a reversal naming nothing"),
            (reversal, "uq_payment_transactions_reversal_of"),
            ({"recorded_at": _EARLIER, "confirmed_at": _EARLIER}, "recorded before created"),
            ({"updated_at": _EARLIER}, "updated before recorded"),
            (dict(confirmed_bank, confirmed_at=_EARLIER), "confirmed before recorded"),
            (dict(confirmed_bank, updated_at=_T0), "updated before confirmed"),
            (dict(rejected, rejected_at=_EARLIER), "rejected before recorded"),
            ({"invoice_id": "999999"}, "the invoice foreign key"),
            ({"recorded_by_id": "999999", "confirmed_by_id": "999999"}, "the recorder foreign key"),
            (dict(reversal, reversal_of_payment_transaction_id="999999"),
             "the reversal foreign key"),
            ({"invoice_id": "NULL"}, "invoice_id NOT NULL"),
            ({"amount": "NULL"}, "amount NOT NULL"),
            ({"kind": "NULL"}, "kind NOT NULL"),
            ({"recorded_at": "NULL"}, "recorded_at NOT NULL"),
        ):
            _refused(_payment_sql(i, u, **overrides), rule)
        assert PaymentTransaction.query.count() == 8


def _receipt_sql(payment_id, actor_id, **overrides):
    values = {
        "public_id": f"'r-{fx._next()}'",
        "payment_transaction_id": str(payment_id),
        "receipt_number": "'RCT-2026-000009'",
        "status": "'issued'",
        "issued_at": _T0,
        "issued_by_id": str(actor_id),
        "voided_at": "NULL",
        "voided_by_id": "NULL",
        "void_reason": "NULL",
        "snapshot": _SNAPSHOT,
        "version": "1",
        "created_at": _T0,
        "updated_at": _T0,
    }
    values.update(overrides)
    return _insert("receipts", values)


def test_every_receipt_constraint_is_enforced(app):
    with app.app_context():
        actor, owner = _owners()
        u = str(actor.id)
        first, second, target = (px.payment(owner, actor).id for _ in range(3))
        voided = {"status": "'voided'", "voided_at": _T1, "voided_by_id": u,
                  "void_reason": "'Wrong amount'", "version": "2", "updated_at": _T1}
        _accepted(_receipt_sql(first, u, public_id="'legal-r'", receipt_number="'RCT-2026-000001'"))
        _accepted(_receipt_sql(second, u, receipt_number="'RCT-2026-000002'", **voided))

        for overrides, rule in (
            ({"public_id": "'legal-r'"}, "public_id uniqueness"),
            ({"payment_transaction_id": str(first)}, "uq_receipts_payment_transaction_id"),
            ({"receipt_number": "'RCT-2026-000001'"}, "uq_receipts_receipt_number"),
            ({"status": "'void'"}, "ck_receipts_status_valid"),
            ({"version": "0"}, "ck_receipts_version_positive"),
            ({"receipt_number": "'INV-2026-000009'"}, "a foreign prefix"),
            ({"receipt_number": "'RCT-2026-00009'"}, "a short number"),
            ({"receipt_number": "'RCT-2026-0000009'"}, "a long number"),
            (dict(voided, void_reason="NULL"), "a void without its reason"),
            (dict(voided, void_reason="''"), "a void with an empty reason"),
            (dict(voided, voided_by_id="NULL"), "a void without its actor"),
            ({"voided_at": _T1, "voided_by_id": u, "updated_at": _T1},
             "an issued receipt carrying a void"),
            ({"issued_at": _EARLIER}, "issued before created"),
            ({"updated_at": _EARLIER}, "updated before issued"),
            (dict(voided, voided_at=_EARLIER), "voided before issued"),
            ({"payment_transaction_id": "999999"}, "the payment foreign key"),
            ({"issued_by_id": "999999"}, "the issuer foreign key"),
            (dict(voided, voided_by_id="999999"), "the voider foreign key"),
            ({"snapshot": "NULL"}, "snapshot NOT NULL"),
            ({"receipt_number": "NULL"}, "receipt_number NOT NULL"),
            ({"payment_transaction_id": "NULL"}, "payment_transaction_id NOT NULL"),
        ):
            _refused(_receipt_sql(target, u, **overrides), rule)
        assert Receipt.query.count() == 2


def _sequence_sql(**overrides):
    values = {"calendar_year": "2026", "last_number": "0", "created_at": _T0, "updated_at": _T0}
    values.update(overrides)
    return _insert("receipt_number_sequences", values)


def test_every_receipt_sequence_constraint_is_enforced(app):
    with app.app_context():
        _accepted(_sequence_sql())
        _accepted(_sequence_sql(calendar_year="2027", last_number="999999"))
        for overrides, rule in (
            ({}, "uq_receipt_number_sequences_calendar_year"),
            ({"calendar_year": "999"}, "a three-digit year"),
            ({"calendar_year": "10000"}, "a five-digit year"),
            ({"calendar_year": "2030", "last_number": "-1"}, "a negative number"),
            ({"calendar_year": "2030", "last_number": "1000000"}, "a seven-digit number"),
            ({"calendar_year": "2030", "updated_at": _EARLIER}, "updated_at before created_at"),
            ({"calendar_year": "NULL"}, "calendar_year NOT NULL"),
        ):
            _refused(_sequence_sql(**overrides), rule)
        assert ReceiptNumberSequence.query.count() == 2


def _event_sql(invoice_id, actor_id, payment_id, **overrides):
    values = {
        "invoice_id": str(invoice_id),
        "actor_id": str(actor_id),
        "kind": "'payment_cash_recorded'",
        "occurred_at": _T0,
        "invoice_version_before": "2",
        "invoice_version_after": "2",
        "reason": "NULL",
        "before_snapshot": _SNAPSHOT,
        "after_snapshot": _SNAPSHOT,
        "payment_transaction_id": str(payment_id),
        "receipt_id": "NULL",
    }
    values.update(overrides)
    return _insert("payment_audit_events", values)


def test_the_extended_audit_event_constraints_are_enforced(app):
    with app.app_context():
        actor, owner = _owners()
        pay, issued = px.cash_with_receipt(owner, actor)
        i, u, r = owner.id, actor.id, str(issued.id)
        m04_edit = {"kind": "'invoice_issued_edited'", "invoice_version_before": "1",
                    "invoice_version_after": "2", "reason": "'Corrected'",
                    "payment_transaction_id": "NULL"}
        for accepted in (
            {},
            {"kind": "'payment_bank_transfer_recorded'"},
            {"kind": "'payment_bank_transfer_confirmed'"},
            {"kind": "'payment_bank_transfer_rejected'", "reason": "'Not received'"},
            {"kind": "'payment_reversed'", "reason": "'Wrong amount'"},
            {"kind": "'receipt_issued'", "receipt_id": r},
            {"kind": "'receipt_voided'", "receipt_id": r, "reason": "'Wrong amount'"},
            {"kind": "'invoice_draft_created'", "invoice_version_before": "NULL",
             "invoice_version_after": "1", "before_snapshot": "NULL",
             "payment_transaction_id": "NULL"},
            m04_edit,
        ):
            _accepted(_event_sql(i, u, pay.id, **accepted))

        for overrides, rule in (
            ({"kind": "'payment_refunded'"}, "ck_payment_audit_events_kind_valid"),
            ({"invoice_version_after": "3"}, "a payment event moving the invoice version"),
            ({"invoice_version_before": "NULL"}, "a payment event without its observed version"),
            ({"invoice_version_before": "0", "invoice_version_after": "0"}, "a zero version"),
            ({"payment_transaction_id": "NULL"}, "a payment event without its transaction"),
            ({"receipt_id": r}, "a payment event naming a receipt"),
            ({"kind": "'receipt_issued'"}, "a receipt event without its receipt"),
            ({"kind": "'receipt_issued'", "receipt_id": r, "payment_transaction_id": "NULL"},
             "a receipt event without its collection"),
            (dict(m04_edit, payment_transaction_id=str(pay.id)),
             "an invoice event naming a transaction"),
            (dict(m04_edit, receipt_id=r), "an invoice event naming a receipt"),
            (dict(m04_edit, invoice_version_after="1"), "an invoice edit not moving the version"),
            ({"kind": "'payment_bank_transfer_rejected'"}, "a rejection without its reason"),
            ({"kind": "'payment_reversed'", "reason": "''"}, "a reversal with an empty reason"),
            ({"kind": "'receipt_voided'", "receipt_id": r}, "a void without its reason"),
            ({"reason": "'Why'"}, "a cash payment event with a reason"),
            ({"kind": "'receipt_issued'", "receipt_id": r, "reason": "'Why'"},
             "a receipt issue with a reason"),
            ({"before_snapshot": "NULL"}, "a payment event without a before snapshot"),
            ({"payment_transaction_id": "999999"}, "the payment foreign key"),
            ({"kind": "'receipt_issued'", "receipt_id": "999999"}, "the receipt foreign key"),
        ):
            _refused(_event_sql(i, u, pay.id, **overrides), rule)
        assert PaymentAuditEvent.query.count() == 9


def test_the_database_refuses_deleting_anything_referenced(app):
    with app.app_context():
        actor, owner = _owners()
        original, voided, reversal = px.reversed_collection(owner, actor)
        invoice_id, original_id, actor_id = owner.id, original.id, actor.id
        _refused(f"DELETE FROM invoices WHERE id = {invoice_id}", "no cascade from an invoice")
        _refused(f"DELETE FROM payment_transactions WHERE id = {original_id}",
                 "no cascade from a reversed, receipted payment")
        _refused(f"DELETE FROM users WHERE id = {actor_id}", "no cascade from an account")
        assert _counts()[:2] == (2, 1)


@pytest.mark.parametrize(
    "model, expected",
    [
        # Phase 5 / M10 adds the deleting account to both.
        (PaymentTransaction, {("invoice_id", "invoices"), ("recorded_by_id", "users"),
                              ("confirmed_by_id", "users"), ("rejected_by_id", "users"),
                              ("reversal_of_payment_transaction_id", "payment_transactions"),
                              ("payment_intent_id", "payment_intents"),
                              ("deleted_by_id", "users")}),
        (Receipt, {("payment_transaction_id", "payment_transactions"), ("issued_by_id", "users"),
                   ("voided_by_id", "users"), ("deleted_by_id", "users")}),
        (ReceiptNumberSequence, set()),
        (PaymentAuditEvent, {("invoice_id", "invoices"), ("actor_id", "users"),
                             ("payment_transaction_id", "payment_transactions"),
                             ("receipt_id", "receipts")}),
    ],
)
def test_foreign_keys_are_plain_with_no_relationship_or_cascade(app, model, expected):
    with app.app_context():
        assert not sa.inspect(model).relationships
        for foreign_key in model.__table__.foreign_keys:
            assert foreign_key.ondelete is None and foreign_key.onupdate is None
        assert {(fk.parent.name, fk.column.table.name)
                for fk in model.__table__.foreign_keys} == expected


_COLUMNS = {
    PaymentTransaction: {"id", "public_id", "invoice_id", "kind", "method", "status",
                         "currency_code", "amount", "bank_transfer_reference",
                         "bank_transfer_date", "recorded_at", "recorded_by_id", "confirmed_at",
                         "confirmed_by_id", "rejected_at", "rejected_by_id", "rejection_reason",
                         "reversal_of_payment_transaction_id", "payment_intent_id", "version",
                         "created_at", "updated_at",
                         # Phase 5 / M10: the visible-deletion tombstone.
                         "deleted_at", "deleted_by_id", "deletion_reason"},
    Receipt: {"id", "public_id", "payment_transaction_id", "receipt_number", "status",
              "issued_at", "issued_by_id", "voided_at", "voided_by_id", "void_reason", "snapshot",
              "version", "created_at", "updated_at",
              "deleted_at", "deleted_by_id", "deletion_reason"},
    ReceiptNumberSequence: {"id", "calendar_year", "last_number", "created_at", "updated_at"},
}

#: Column-name parts that would mean card, account or credential data, a
#: provider, a stored balance, or identity duplicated from the invoice chain.
_PROHIBITED_PARTS = (
    "card", "pan", "cvv", "cvc", "pin", "iban", "swift", "account", "token", "secret",
    "password", "credential", "provider", "intent", "customer", "webhook", "proof", "upload",
    "image", "file", "refund", "discount", "tax", "installment", "due", "total", "paid",
    "outstanding", "balance", "enrollment", "group", "student", "course", "term", "plan", "name",
)


#: Phase 5 / M07's one approved exception: an online collection names the
#: payment intent it settles.
_ALLOWED_COLUMNS = {PaymentTransaction: {"payment_intent_id"}}


@pytest.mark.parametrize("model", list(_COLUMNS), ids=lambda model: model.__tablename__)
def test_no_card_credential_balance_or_duplicated_identity_column_exists(model):
    columns = {column.name for column in model.__table__.columns}
    assert columns == _COLUMNS[model]
    for name in columns - _ALLOWED_COLUMNS.get(model, set()):
        for part in _PROHIBITED_PARTS:
            assert part not in name.split("_"), (name, part)
    assert model.__table__.kwargs == {}


# ===========================================================================
# ORM guards
# ===========================================================================


def test_no_payment_receipt_or_receipt_sequence_is_ever_deleted_through_the_orm(app):
    with app.app_context():
        actor, owner = _owners()
        pay, issued = px.cash_with_receipt(owner, actor)
        counter = px.sequence(2026, 1)
        before = _counts()
        for row in (issued, pay, counter):
            db.session.delete(row)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        assert _counts() == before


def test_a_confirmed_or_rejected_payment_never_changes(app):
    with app.app_context():
        actor, owner = _owners()
        cash = px.payment(owner, actor)
        rejected = px.payment(owner, actor, method="bank_transfer", status="rejected")
        for row, changes in (
            (cash, {"amount": "5"}),
            (cash, {"status": "rejected", "version": 2}),
            (cash, {"version": 2, "updated_at": fx.CANCELLED_AT}),
            (rejected, {"status": "confirmed", "version": 3}),
            (rejected, {"rejection_reason": "Other", "version": 3}),
        ):
            db.session.expire_all()
            for field, value in changes.items():
                setattr(row, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        db.session.expire_all()
        assert (px.stored_payment(cash.public_id).amount, px.stored_payment(cash.public_id).version) == (
            Decimal("100.0000"), 1)
        assert px.stored_payment(rejected.public_id).rejection_reason == "Not received"


def test_a_pending_transfer_changes_only_by_one_decision(app):
    with app.app_context():
        actor, owner = _owners()
        other = fees.admin("other@example.com")
        cash = px.payment(owner, actor)
        pending = px.payment(owner, actor, method="bank_transfer", status="pending")
        for changes in (
            {"amount": "5", "version": 2},
            {"bank_transfer_reference": "OTHER", "version": 2},
            {"bank_transfer_date": date(2026, 6, 1), "version": 2},
            {"recorded_by_id": other.id, "version": 2},
            {"recorded_at": fx.CREATED_AT, "version": 2},
            {"method": "cash", "version": 2},
            {"reversal_of_payment_transaction_id": cash.id, "version": 2},
            {"status": "confirmed", "confirmed_at": px.DECIDED_AT, "confirmed_by_id": actor.id},
            {"status": "confirmed", "confirmed_at": px.DECIDED_AT, "confirmed_by_id": actor.id,
             "version": 3},
        ):
            db.session.expire_all()
            for field, value in changes.items():
                setattr(pending, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        # A scalar id captured first: reading an expired row mid-assignment
        # would autoflush a half-made change into the guard.
        actor_id = actor.id
        db.session.expire_all()
        pending.status = "confirmed"
        pending.confirmed_at = px.DECIDED_AT
        pending.confirmed_by_id = actor_id
        pending.version = 2
        pending.updated_at = px.DECIDED_AT
        db.session.commit()
        assert px.stored_payment(pending.public_id).status == "confirmed"


def test_a_receipt_changes_only_by_being_voided_once(app):
    with app.app_context():
        actor, owner = _owners()
        other = fees.admin("other@example.com")
        pay, issued = px.cash_with_receipt(owner, actor)
        spare = px.payment(owner, actor)
        snapshot = dict(issued.snapshot)
        for changes in (
            {"receipt_number": "RCT-2026-999999"},
            {"payment_transaction_id": spare.id},
            {"issued_at": px.DECIDED_AT},
            {"issued_by_id": other.id},
            {"snapshot": dict(snapshot, student_name="Somebody else")},
            {"status": "voided", "voided_at": px.REVERSED_AT, "voided_by_id": actor.id,
             "void_reason": "Wrong"},
            {"version": 2, "updated_at": px.REVERSED_AT},
        ):
            db.session.expire_all()
            for field, value in changes.items():
                setattr(issued, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        actor_id = actor.id
        db.session.expire_all()
        issued.status = "voided"
        issued.voided_at = px.REVERSED_AT
        issued.voided_by_id = actor_id
        issued.void_reason = "Wrong amount"
        issued.version = 2
        issued.updated_at = px.REVERSED_AT
        db.session.commit()
        for changes in ({"void_reason": "Another reason"}, {"version": 3},
                        {"status": "issued", "version": 3}):
            db.session.expire_all()
            for field, value in changes.items():
                setattr(issued, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        db.session.expire_all()
        stored = Receipt.query.one()
        assert (stored.status, stored.version, stored.void_reason, stored.snapshot) == (
            "voided", 2, "Wrong amount", snapshot)


def test_a_receipt_sequence_never_goes_backwards_or_changes_year(app):
    with app.app_context():
        counter = px.sequence(2026, 5)
        for field, value in (("last_number", 4), ("calendar_year", 2027)):
            db.session.expire_all()
            setattr(counter, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        counter.last_number = 6
        db.session.commit()
        assert (ReceiptNumberSequence.query.one().calendar_year,
                ReceiptNumberSequence.query.one().last_number) == (2026, 6)


_BULK_STATEMENTS = {
    "an ORM UPDATE of payments": lambda: sa.update(PaymentTransaction).values(amount="1"),
    "a Core UPDATE of payments": lambda: sa.update(PaymentTransaction.__table__).values(
        status="rejected"),
    "an ORM DELETE of payments": lambda: sa.delete(PaymentTransaction),
    "an ORM UPDATE of receipts": lambda: sa.update(Receipt).values(status="voided"),
    "a Core DELETE of receipts": lambda: sa.delete(Receipt.__table__),
    "an ORM DELETE of receipt sequences": lambda: sa.delete(ReceiptNumberSequence),
}


@pytest.mark.parametrize("name", sorted(_BULK_STATEMENTS))
def test_bulk_rewrites_of_payments_and_receipts_are_refused(app, name):
    with app.app_context():
        actor, owner = _owners()
        px.cash_with_receipt(owner, actor)
        px.sequence(2026, 1)
        before = _counts()
        with pytest.raises(FinancialHistoryError):
            db.session.execute(_BULK_STATEMENTS[name]())
        db.session.rollback()
        assert _counts() == before


def test_legacy_query_bulk_writes_of_payments_and_receipts_are_refused(app):
    with app.app_context():
        actor, owner = _owners()
        px.cash_with_receipt(owner, actor)
        for model, values in ((PaymentTransaction, {"amount": "1"}), (Receipt, {"version": 9})):
            with pytest.raises(FinancialHistoryError):
                db.session.query(model).update(values)
            db.session.rollback()
            with pytest.raises(FinancialHistoryError):
                db.session.query(model).delete()
            db.session.rollback()
        assert PaymentTransaction.query.one().amount == Decimal("100.0000")


def _payment_event(owner, actor, pay, kind, receipt=None, **overrides):
    items = InvoiceItem.query.filter_by(invoice_id=owner.id, status="active").all()
    after = build_payment_snapshot(owner, items, [pay], payment=pay, receipt=receipt)
    before = build_payment_snapshot(owner, items, [])
    values = dict(invoice_id=owner.id, actor_id=actor.id, kind=kind, occurred_at=px.RECORDED_AT,
                  invoice_version_before=owner.version, invoice_version_after=owner.version,
                  reason=None, before_snapshot=before, after_snapshot=after,
                  payment_transaction_id=pay.id,
                  receipt_id=None if receipt is None else receipt.id)
    values.update(overrides)
    return PaymentAuditEvent(**values)


def test_an_audit_event_must_match_its_kind_before_it_reaches_the_database(app):
    with app.app_context():
        actor, owner = _owners()
        pay, issued = px.cash_with_receipt(owner, actor)
        invoice_snapshot = fx.event(fx.invoice(fees.assignment(
            fees.enrollment(), fees.active_plan(actor), actor), actor), actor, 1).after_snapshot
        for build in (
            lambda: _payment_event(owner, actor, pay, "payment_cash_recorded",
                                   after_snapshot=invoice_snapshot),
            lambda: _payment_event(owner, actor, pay, "payment_cash_recorded",
                                   payment_transaction_id=None),
            lambda: _payment_event(owner, actor, pay, "receipt_issued"),
            lambda: _payment_event(owner, actor, pay, "payment_cash_recorded", receipt=issued),
            lambda: _payment_event(owner, actor, pay, "invoice_draft_edited",
                                   invoice_version_after=owner.version + 1),
        ):
            db.session.add(build())
            with pytest.raises(ValueError):
                db.session.flush()
            db.session.rollback()
        assert PaymentAuditEvent.query.count() == 1


def test_the_database_accepts_what_only_the_application_refuses(app):
    """No CHECK can read another row or add money up, so an overpayment, a
    reversal naming a pending transfer, and a receipt of a pending transfer are
    accepted by the database; the routes refuse them under the invoice lock
    (see tests/test_admin_payments.py and tests/test_payment_transactions.py)."""
    with app.app_context():
        actor, owner = _owners()
        pending = px.payment(owner, actor, method="bank_transfer", status="pending")
        overpaid = px.payment(owner, actor, amount="99999.999")
        px.payment(owner, actor, kind="reversal", reversal_of=pending)
        _accepted(_receipt_sql(pending.id, actor.id, receipt_number="'RCT-2026-000077'"))
        assert overpaid.amount > Decimal("1250.5")
        assert PaymentTransaction.query.count() == 3 and Receipt.query.count() == 1
