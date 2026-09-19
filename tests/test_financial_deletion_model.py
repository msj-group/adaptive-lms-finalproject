"""Phase 5 / M10: the visible-deletion tombstone, its guards and its events.

An invoice, a payment transaction and a receipt may be deleted exactly once --
the three tombstone columns set together, the version moved by one, nothing
else changed -- and never change again. A cancelled invoice is never deleted.
The deletion events are written only by their strict writers, from snapshots
in the deletion layouts. Deleted rows count for nothing in a balance, a floor
or the one-open-invoice rule.
"""

from datetime import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

import tests.financial_report_fixtures as rx
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
from app.extensions import db
from app.models import PaymentAuditEvent, PaymentTransaction, Receipt, User
from app.models.invoice import FinancialHistoryError
from app.models.payment_audit_event import (
    DELETION_EVENT_KINDS,
    INVOICE_EVENT_KINDS,
    PAYMENT_EVENT_KINDS,
    REASON_REQUIRED_KINDS,
    RECEIPT_EVENT_KINDS,
    validate_audit_snapshot,
)
from app.services import financial_deletions as deletions
from app.services.invoice_audit import build_invoice_deletion_snapshot, record_invoice_deletion_event
from app.services.invoice_transactions import open_invoices
from app.services.payment_audit import (
    PAYMENT_DELETED,
    PAYMENT_REPLACED,
    RECEIPT_DELETED,
    build_payment_deletion_snapshot,
    record_payment_deletion_event,
)
from app.services.payment_transactions import payment_balance, payment_floor

MOMENT = datetime(2026, 12, 1, 9, 0, 0)
REASON = "Entered twice"
MOMENT_TEXT = "2026-12-01 09:00:00"


def _world(app):
    w = px.world(app)
    owner, actor = rx.rows_of(w)
    return w, owner, actor


def _items(invoice):
    return fx.InvoiceItem.query.filter_by(invoice_id=invoice.id).order_by(fx.InvoiceItem.id).all()


# ===========================================================================
# The closed sets
# ===========================================================================


def test_the_four_deletion_kinds_and_their_families():
    assert DELETION_EVENT_KINDS == {"invoice_deleted", "payment_deleted", "payment_replaced",
                                    "receipt_deleted"}
    assert "invoice_deleted" in INVOICE_EVENT_KINDS
    assert {"payment_deleted", "payment_replaced"} <= PAYMENT_EVENT_KINDS
    assert "receipt_deleted" in RECEIPT_EVENT_KINDS
    assert DELETION_EVENT_KINDS <= REASON_REQUIRED_KINDS


# ===========================================================================
# The guards
# ===========================================================================


@pytest.mark.parametrize("kind", ["invoice", "payment", "receipt"])
def test_a_row_is_deleted_once_and_never_changes_again(app, kind):
    with app.app_context():
        _w, owner, actor = _world(app)
        pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        row = {"invoice": owner, "payment": pay, "receipt": rc}[kind]
        version = row.version
        recorded = {key: getattr(row, key) for key in ("public_id", "status")}
        deletions.tombstone(row, actor.id, REASON, MOMENT)
        db.session.commit()
        db.session.expire_all()
        assert (row.deleted_at, row.deleted_by_id, row.deletion_reason, row.version) == (
            MOMENT, actor.id, REASON, version + 1)
        assert {key: getattr(row, key) for key in recorded} == recorded
        for change in ({"deletion_reason": "Another reason"}, {"deleted_at": None},
                       {"version": row.version + 1}, {"updated_at": MOMENT.replace(day=2)}):
            for key, value in change.items():
                setattr(row, key, value)
            with pytest.raises(FinancialHistoryError, match="deleted .* never changes"):
                db.session.flush()
            db.session.rollback()


@pytest.mark.parametrize("kind", ["invoice", "payment", "receipt"])
def test_a_partial_or_mixed_deletion_is_refused(app, kind):
    with app.app_context():
        _w, owner, actor = _world(app)
        pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        row = {"invoice": owner, "payment": pay, "receipt": rc}[kind]
        other = {"invoice": ("status", "cancelled"), "payment": ("amount", Decimal("1")),
                 "receipt": ("status", "voided")}[kind]
        attempts = [
            {"deleted_at": MOMENT},
            {"deleted_at": MOMENT, "deleted_by_id": actor.id},
            {"deleted_at": MOMENT, "deleted_by_id": actor.id, "deletion_reason": REASON},
            {"deleted_at": MOMENT, "deleted_by_id": actor.id, "deletion_reason": REASON,
             "version": row.version + 2},
            {"deleted_at": MOMENT, "deleted_by_id": actor.id, "deletion_reason": REASON,
             "version": row.version + 1, other[0]: other[1]},
        ]
        for values in attempts:
            version = row.version
            for key, value in values.items():
                setattr(row, key, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
            assert row.version == version


def test_a_cancelled_invoice_is_never_deleted(app):
    with app.app_context():
        w, _owner, actor = _world(app)
        cancelled = rx.invoice_in_group(actor, rx.plan_of(w), status="cancelled",
                                        owning_group=rx.group_of(w))[0]
        with pytest.raises(FinancialHistoryError, match="cancelled invoice is read-only"):
            deletions.tombstone(cancelled, actor.id, REASON, MOMENT)
            db.session.flush()
        db.session.rollback()
        # The database refuses it too, whatever writes it.
        with pytest.raises(sa.exc.IntegrityError, match="ck_invoices_deletion_state"):
            db.session.execute(sa.text(
                "UPDATE invoices SET deleted_at = :at, deleted_by_id = :by, "
                "deletion_reason = 'x' WHERE id = :id"),
                {"at": MOMENT_TEXT, "by": actor.id, "id": cancelled.id})
        db.session.rollback()


def test_the_tombstone_checks_hold_in_the_database(app):
    with app.app_context():
        _w, owner, actor = _world(app)
        pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        for table, row_id in (("invoices", owner.id), ("payment_transactions", pay.id),
                              ("receipts", rc.id)):
            for assignment in ("deleted_at = :at", "deleted_at = :at, deleted_by_id = :by",
                               "deleted_at = :at, deleted_by_id = :by, deletion_reason = ''",
                               "deleted_at = '2000-01-01', deleted_by_id = :by, "
                               "deletion_reason = 'x'"):
                with pytest.raises(sa.exc.IntegrityError, match=f"ck_{table}_deletion_state"):
                    db.session.execute(sa.text(
                        f"UPDATE {table} SET {assignment}, updated_at = :at WHERE id = :id"),
                        {"at": MOMENT_TEXT, "by": actor.id, "id": row_id})
                db.session.rollback()


def test_a_deletion_reason_is_normalized_text(app):
    with app.app_context():
        _w, owner, _actor = _world(app)
        for bad in ("", "  padded  ", "bad\x00control", "x" * 501):
            with pytest.raises(ValueError):
                owner.deletion_reason = bad
        owner.deletion_reason = None
        db.session.rollback()


def test_bulk_rewrites_of_payments_and_receipts_are_still_refused(app):
    with app.app_context():
        for model in (PaymentTransaction, Receipt):
            with pytest.raises(FinancialHistoryError):
                db.session.query(model).update({"deletion_reason": "x"})
            db.session.rollback()


# ===========================================================================
# Live-only reads
# ===========================================================================


def test_deleted_rows_count_for_nothing(app):
    with app.app_context():
        w, owner, actor = _world(app)
        kept = px.payment(owner, actor, "100.000")
        gone = px.payment(owner, actor, "300.000")
        pending = px.payment(owner, actor, "50.000", method="bank_transfer", status="pending")
        gone_pending = px.payment(owner, actor, "60.000", method="bank_transfer",
                                  status="pending")
        for row in (gone, gone_pending):
            deletions.tombstone(row, actor.id, REASON, MOMENT)
        db.session.commit()
        rows = [kept, gone, pending, gone_pending]
        active = [item for item in _items(owner) if item.status == "active"]
        balance = payment_balance(active, rows)
        assert (balance.paid, balance.outstanding) == (Decimal("100.0000"), Decimal("1150.5000"))
        assert payment_floor(rows) == Decimal("150.0000")
        second = rx.invoice_in_group(actor, rx.plan_of(w), owning_group=rx.group_of(w))[0]
        assert open_invoices([owner, second]) == [owner, second]
        deletions.tombstone(second, actor.id, REASON, MOMENT)
        db.session.commit()
        assert open_invoices([owner, second]) == [owner]
        assert not second.is_open and second.is_deleted


# ===========================================================================
# The writers
# ===========================================================================


def test_an_invoice_deletion_event_records_the_deletion_and_nothing_else(app):
    with app.app_context():
        w, owner, actor = _world(app)
        items = _items(owner)
        before = build_invoice_deletion_snapshot(owner, w["ap"], items)
        version = owner.version
        deletions.tombstone(owner, actor.id, REASON, MOMENT)
        db.session.flush()
        after = build_invoice_deletion_snapshot(owner, w["ap"], items)
        assert (before["deleted"], after["deleted"]) == (False, True)
        refusals = [
            dict(before_snapshot=after),  # not deleted before
            dict(after_snapshot=before),  # not deleted after
            dict(after_snapshot=dict(after, total="0.0000")),
            dict(reason=None),
            dict(reason="  padded "),
            dict(version_before=version - 1),
        ]
        for override in refusals:
            arguments = dict(invoice=owner, actor=actor, version_before=version,
                             before_snapshot=before, after_snapshot=after, reason=REASON,
                             moment=MOMENT)
            arguments.update(override)
            with pytest.raises(ValueError):
                record_invoice_deletion_event(**arguments)
        suspended = User(email="gone@example.com", full_name="Gone", role="administrator",
                         status="suspended", password_hash="x")
        with pytest.raises(ValueError):
            record_invoice_deletion_event(invoice=owner, actor=suspended, version_before=version,
                                          before_snapshot=before, after_snapshot=after,
                                          reason=REASON, moment=MOMENT)
        event = record_invoice_deletion_event(invoice=owner, actor=actor, version_before=version,
                                              before_snapshot=before, after_snapshot=after,
                                              reason=REASON, moment=MOMENT)
        db.session.commit()
        stored = db.session.get(PaymentAuditEvent, event.id)
        assert (stored.kind, stored.invoice_version_before, stored.invoice_version_after,
                stored.reason) == ("invoice_deleted", version, version + 1, REASON)


def test_payment_and_receipt_deletion_events_are_strict(app):
    with app.app_context():
        _w, owner, actor = _world(app)
        pay, rc = px.cash_with_receipt(owner, actor, "100.000")
        rows = [pay]
        active = [item for item in _items(owner) if item.status == "active"]
        pay_before = build_payment_deletion_snapshot(owner, active, rows, pay)
        receipt_before = build_payment_deletion_snapshot(owner, active, rows, pay, rc)
        deletions.tombstone(rc, actor.id, REASON, MOMENT)
        deletions.tombstone(pay, actor.id, REASON, MOMENT)
        db.session.flush()
        pay_after = build_payment_deletion_snapshot(owner, active, rows, pay)
        receipt_after = build_payment_deletion_snapshot(owner, active, rows, pay, rc)
        assert (pay_before["paid_amount"], pay_after["paid_amount"]) == ("100.0000", "0.0000")
        assert receipt_after["receipt"]["deleted"] and pay_after["payment"]["deleted"]

        def attempt(**override):
            arguments = dict(invoice=owner, actor=actor, kind=PAYMENT_DELETED, payment=pay,
                             receipt=None, before_snapshot=pay_before, after_snapshot=pay_after,
                             reason=REASON, moment=MOMENT)
            arguments.update(override)
            return record_payment_deletion_event(**arguments)

        for override in (
            dict(kind="payment_reversed"),
            dict(reason=None),
            dict(receipt=rc),
            dict(kind=RECEIPT_DELETED),
            dict(kind=PAYMENT_REPLACED),
            dict(before_snapshot=pay_after),
            dict(after_snapshot=pay_before),
            dict(after_snapshot=dict(pay_after, invoice_total="1.0000", outstanding_amount="1.0000",
                                     paid_amount="0.0000")),
            dict(kind=RECEIPT_DELETED, receipt=rc, before_snapshot=receipt_after,
                 after_snapshot=receipt_after),
        ):
            with pytest.raises(ValueError):
                attempt(**override)
        attempt(kind=RECEIPT_DELETED, receipt=rc, before_snapshot=receipt_before,
                after_snapshot=receipt_after)
        attempt()
        db.session.commit()
        kinds = [e.kind for e in PaymentAuditEvent.query.order_by(PaymentAuditEvent.id)]
        assert kinds == ["receipt_deleted", "payment_deleted"]


def test_the_deletion_layouts_are_exact(app):
    with app.app_context():
        _w, owner, actor = _world(app)
        pay, _rc = px.cash_with_receipt(owner, actor, "100.000")
        active = [item for item in _items(owner) if item.status == "active"]
        snapshot = build_payment_deletion_snapshot(owner, active, [pay], pay)
        assert validate_audit_snapshot(snapshot) == snapshot
        for broken in (
            dict(snapshot, payment=dict(snapshot["payment"], deleted="no")),
            dict(snapshot, payment=dict(snapshot["payment"], replaced_by_public_id="x" * 37)),
            # Only a deleted collection names a replacement.
            dict(snapshot, payment=dict(snapshot["payment"], replaced_by_public_id="other")),
            dict(snapshot, payment={key: value for key, value in snapshot["payment"].items()
                                    if key != "deleted"}),
            dict(snapshot, extra=True),
        ):
            with pytest.raises(ValueError):
                validate_audit_snapshot(broken)
