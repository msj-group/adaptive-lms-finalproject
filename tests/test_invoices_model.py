"""Phase 5 / M04 -- the Invoice, InvoiceItem, PaymentAuditEvent and
InvoiceNumberSequence models.

Application validators and defaults first; then every database CHECK, unique
constraint and foreign key driven with raw SQL, so no validator can mask a
missing constraint; then the ORM guards that refuse rewriting or deleting
financial history; then the absence of any relationship, cascade, plan-item
reference, duplicated identity column or card / payment column.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

import tests.fee_assignment_fixtures as fees
import tests.invoice_fixtures as fx
from app.extensions import db
from app.models import (
    FeePlanItemKind,
    FinancialHistoryError,
    Invoice,
    InvoiceItem,
    InvoiceItemKind,
    InvoiceItemStatus,
    InvoiceNumberSequence,
    InvoiceStatus,
    PaymentAuditEvent,
    PaymentAuditEventKind,
)
from app.models.invoice import format_invoice_number, invoice_number_is_valid
from app.models.submission_feedback import whole_second_utc

_T0 = "'2026-06-01 09:00:00'"
_T1 = "'2026-06-02 09:00:00'"
_T2 = "'2026-06-03 09:00:00'"
_EARLIER = "'2026-05-31 09:00:00'"
_SNAPSHOT = "'{\"schema\": \"raw\"}'"


def _owners():
    actor = fees.admin()
    assignment = fees.assignment(fees.enrollment(), fees.active_plan(actor), actor)
    return actor, assignment


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
    return (
        Invoice.query.count(),
        InvoiceItem.query.count(),
        PaymentAuditEvent.query.count(),
        InvoiceNumberSequence.query.count(),
    )


# ===========================================================================
# Closed sets, defaults and validators
# ===========================================================================


def test_the_closed_sets_are_exact():
    assert [m.value for m in InvoiceStatus] == ["draft", "issued", "cancelled"]
    assert [m.value for m in InvoiceItemKind] == ["registration", "course"]
    assert [m.value for m in InvoiceItemKind] == [m.value for m in FeePlanItemKind]
    assert [m.value for m in InvoiceItemStatus] == ["active", "removed"]
    # Phase 5 / M05 extended the one financial trail with seven payment and
    # receipt kinds; the five invoice kinds are unchanged and still first.
    assert [m.value for m in PaymentAuditEventKind] == [
        "invoice_draft_created",
        "invoice_draft_edited",
        "invoice_issued",
        "invoice_issued_edited",
        "invoice_cancelled",
        "payment_cash_recorded",
        "payment_bank_transfer_recorded",
        "payment_bank_transfer_confirmed",
        "payment_bank_transfer_rejected",
        "payment_reversed",
        "receipt_issued",
        "receipt_voided",
    ]


def test_defaults_public_ids_and_timestamp_hooks(app):
    with app.app_context():
        _actor, assignment = _owners()
        rows = []
        for _ in range(2):
            row = Invoice(student_fee_assignment_id=assignment.id, created_at=fx.CREATED_AT,
                          updated_at=fx.CREATED_AT)
            db.session.add(row)
            db.session.commit()
            rows.append(row)
        first, second = rows
        assert len(first.public_id) == 36 and first.public_id != second.public_id
        assert (first.status, first.currency_code, first.version, first.invoice_number) == (
            "draft", "LYD", 1, None)
        assert first.issued_at is None and first.cancelled_at is None
        assert first.is_draft and first.is_open and not first.is_issued and not first.is_cancelled

        line = InvoiceItem(invoice_id=first.id, kind="course", label="Course", amount="10",
                           created_at=fx.CREATED_AT, updated_at=fx.CREATED_AT)
        counter = InvoiceNumberSequence(calendar_year=2026, created_at=fx.CREATED_AT,
                                        updated_at=fx.CREATED_AT)
        db.session.add_all([line, counter])
        db.session.commit()
        assert len(line.public_id) == 36 and line.status == "active" and line.version == 1
        assert line.amount == Decimal("10.0000") and line.is_active
        assert counter.last_number == 0

        for model in (Invoice, InvoiceItem, InvoiceNumberSequence):
            columns = model.__table__.c
            assert columns.created_at.default.is_callable and columns.updated_at.default.is_callable
            assert columns.created_at.onupdate is None and columns.updated_at.onupdate is None
        events = PaymentAuditEvent.__table__.c
        assert events.occurred_at.default is None
        assert "updated_at" not in events and "public_id" not in events
        assert "public_id" not in InvoiceNumberSequence.__table__.c
        assert whole_second_utc().microsecond == 0


@pytest.mark.parametrize(
    "model, field, value",
    [
        (Invoice, "status", "paid"),
        (Invoice, "status", "DRAFT"),
        (Invoice, "currency_code", "USD"),
        (Invoice, "currency_code", "lyd"),
        (Invoice, "version", 0),
        (Invoice, "version", True),
        (Invoice, "version", "1"),
        (Invoice, "invoice_number", "INV-2026-000000"),
        (Invoice, "invoice_number", "inv-2026-000001"),
        (Invoice, "invoice_number", ""),
        (InvoiceItem, "kind", "discount"),
        (InvoiceItem, "status", "deleted"),
        (InvoiceItem, "label", " padded"),
        (InvoiceItem, "label", ""),
        (InvoiceItem, "label", "a‮b"),
        (InvoiceItem, "amount", 10.5),
        (InvoiceItem, "amount", 10),
        (InvoiceItem, "amount", "1,0"),
        (InvoiceItem, "amount", "0"),
        (InvoiceItem, "amount", Decimal("100000")),
        (InvoiceItem, "amount", Decimal("1.00001")),
        (InvoiceItem, "version", 0),
        (InvoiceNumberSequence, "calendar_year", 999),
        (InvoiceNumberSequence, "calendar_year", 10000),
        (InvoiceNumberSequence, "calendar_year", True),
        (InvoiceNumberSequence, "calendar_year", "2026"),
        (InvoiceNumberSequence, "last_number", -1),
        (InvoiceNumberSequence, "last_number", 1000000),
        (InvoiceNumberSequence, "last_number", False),
        (PaymentAuditEvent, "kind", "payment_recorded"),
        (PaymentAuditEvent, "invoice_version_before", 0),
        (PaymentAuditEvent, "invoice_version_after", None),
        (PaymentAuditEvent, "invoice_version_after", True),
        (PaymentAuditEvent, "reason", " padded"),
        (PaymentAuditEvent, "reason", "bad\x00byte"),
        (PaymentAuditEvent, "reason", "x" * 501),
        (PaymentAuditEvent, "before_snapshot", {"card_number": "4111111111111111"}),
        (PaymentAuditEvent, "after_snapshot", None),
        (PaymentAuditEvent, "after_snapshot", "{}"),
    ],
)
def test_validators_refuse_bad_values(app, model, field, value):
    with app.app_context():
        with pytest.raises(ValueError):
            model(**{field: value})


def test_invoice_number_helpers():
    assert format_invoice_number(2026, 1) == "INV-2026-000001"
    assert format_invoice_number(2026, 999999) == "INV-2026-999999"
    assert format_invoice_number(1000, 42) == "INV-1000-000042"
    for year, number in ((2026, 0), (2026, 1000000), (999, 1), (10000, 1), (True, 1), (2026, True),
                         ("2026", 1)):
        with pytest.raises(ValueError):
            format_invoice_number(year, number)
    assert invoice_number_is_valid("INV-2026-000001")
    for bad in ("inv-2026-000001", "INV-2026-000000", "INV-0999-000001", "INV-2026-0000001",
                "INV-2026-00001", "INV-２０２６-000001", "INV-2026-000001\n",
                " INV-2026-000001", "INV_2026_000001", None, 5):
        assert not invoice_number_is_valid(bad), bad


# ===========================================================================
# Database constraints, driven with raw SQL
# ===========================================================================


def _invoice_sql(assignment_id, **overrides):
    values = {
        "public_id": f"'i-{fx._next()}'",
        "student_fee_assignment_id": str(assignment_id),
        "currency_code": "'LYD'",
        "status": "'draft'",
        "invoice_number": "NULL",
        "issued_at": "NULL",
        "issued_by_id": "NULL",
        "cancelled_at": "NULL",
        "cancelled_by_id": "NULL",
        "version": "1",
        "created_at": _T0,
        "updated_at": _T0,
    }
    values.update(overrides)
    return _insert("invoices", values)


def test_every_invoice_constraint_is_enforced(app):
    with app.app_context():
        actor, assignment = _owners()
        a, u = assignment.id, str(actor.id)
        issued = {"status": "'issued'", "invoice_number": "'INV-2026-000001'", "issued_at": _T1,
                  "issued_by_id": u, "updated_at": _T1, "version": "2"}
        cancelled_draft = {"status": "'cancelled'", "cancelled_at": _T2, "cancelled_by_id": u,
                           "updated_at": _T2, "version": "2"}
        cancelled_issued = dict(issued, status="'cancelled'", invoice_number="'INV-2026-000002'",
                                cancelled_at=_T2, cancelled_by_id=u, updated_at=_T2, version="3")
        _accepted(_invoice_sql(a, public_id="'legal-1'"))
        _accepted(_invoice_sql(a, **issued))
        _accepted(_invoice_sql(a, **cancelled_draft))
        _accepted(_invoice_sql(a, **cancelled_issued))
        number = "'INV-2026-000009'"

        for overrides, rule in (
            ({"public_id": "'legal-1'"}, "public_id uniqueness"),
            (dict(issued), "uq_invoices_invoice_number"),
            ({"public_id": "NULL"}, "public_id NOT NULL"),
            ({"status": "'paid'"}, "ck_invoices_status_valid"),
            ({"status": "'DRAFT'"}, "the case-sensitive closed set"),
            ({"currency_code": "'USD'"}, "ck_invoices_currency_code"),
            ({"version": "0"}, "ck_invoices_version_positive"),
            (dict(issued, invoice_number=number, issued_by_id="NULL"), "the issue pair (actor)"),
            (dict(issued, invoice_number=number, issued_at="NULL", updated_at=_T0),
             "the issue pair (moment)"),
            (dict(cancelled_draft, cancelled_by_id="NULL"), "the cancellation pair (actor)"),
            ({"cancelled_by_id": u}, "the cancellation pair (moment)"),
            ({"invoice_number": number}, "a draft holding a number"),
            (dict(issued, invoice_number="NULL"), "an issued invoice without a number"),
            (dict(issued, invoice_number="'INV-2026-00009'"), "a short number"),
            (dict(issued, invoice_number="'INV-2026-0000009'"), "a long number"),
            (dict(issued, invoice_number="'XYZ-2026-000009'"), "a foreign prefix"),
            ({"issued_at": _T1, "issued_by_id": u, "invoice_number": number, "updated_at": _T1},
             "a draft that was issued"),
            ({"cancelled_at": _T2, "cancelled_by_id": u, "updated_at": _T2},
             "a draft carrying a cancellation"),
            (dict(issued, invoice_number=number, cancelled_at=_T2, cancelled_by_id=u,
                  updated_at=_T2), "an issued invoice carrying a cancellation"),
            (dict(cancelled_draft, cancelled_at="NULL", cancelled_by_id="NULL", updated_at=_T0),
             "a cancelled invoice without its cancellation"),
            ({"updated_at": _EARLIER}, "updated_at before created_at"),
            (dict(issued, invoice_number=number, issued_at=_EARLIER), "issued before created"),
            (dict(issued, invoice_number=number, updated_at=_T0), "updated before issued"),
            (dict(cancelled_issued, invoice_number=number, cancelled_at=_T0),
             "cancelled before issued"),
            (dict(cancelled_draft, updated_at=_T1), "updated before cancelled"),
            ({"student_fee_assignment_id": "999999"}, "the assignment foreign key"),
            (dict(issued, invoice_number=number, issued_by_id="999999"), "the issuer foreign key"),
            (dict(cancelled_draft, cancelled_by_id="999999"), "the canceller foreign key"),
            ({"student_fee_assignment_id": "NULL"}, "student_fee_assignment_id NOT NULL"),
            ({"status": "NULL"}, "status NOT NULL"),
            ({"version": "NULL"}, "version NOT NULL"),
            ({"currency_code": "NULL"}, "currency_code NOT NULL"),
            ({"created_at": "NULL"}, "created_at NOT NULL"),
        ):
            _refused(_invoice_sql(a, **overrides), rule)
        assert Invoice.query.count() == 4


def _line_sql(owner_id, **overrides):
    values = {
        "public_id": f"'l-{fx._next()}'",
        "invoice_id": str(owner_id),
        "kind": "'course'",
        "label": "'Course'",
        "amount": "10",
        "status": "'active'",
        "removed_at": "NULL",
        "removed_by_id": "NULL",
        "version": "1",
        "created_at": _T0,
        "updated_at": _T0,
    }
    values.update(overrides)
    return _insert("invoice_items", values)


def test_every_invoice_item_constraint_is_enforced(app):
    with app.app_context():
        actor, assignment = _owners()
        owner = fx.invoice(assignment, actor, lines=())
        i, u = owner.id, str(actor.id)
        removed = {"status": "'removed'", "removed_at": _T1, "removed_by_id": u, "updated_at": _T1,
                   "version": "2"}
        _accepted(_line_sql(i, public_id="'legal-line'"))
        _accepted(_line_sql(i, **removed))
        _accepted(_line_sql(i, amount="0.001"))
        _accepted(_line_sql(i, amount="99999.999"))
        _accepted(_line_sql(i, amount="12.3456"))

        for overrides, rule in (
            ({"public_id": "'legal-line'"}, "public_id uniqueness"),
            ({"kind": "'discount'"}, "ck_invoice_items_kind_valid"),
            ({"kind": "'COURSE'"}, "the case-sensitive kind"),
            ({"status": "'deleted'"}, "ck_invoice_items_status_valid"),
            ({"amount": "0"}, "a zero amount"),
            ({"amount": "0.0009"}, "an amount below the minimum"),
            ({"amount": "100000"}, "an amount above the maximum"),
            ({"amount": "-5"}, "a negative amount"),
            ({"version": "0"}, "ck_invoice_items_version_positive"),
            (dict(removed, removed_at="NULL", removed_by_id="NULL", updated_at=_T0),
             "a removed line without its removal"),
            (dict(removed, removed_by_id="NULL"), "a removal without its actor"),
            ({"removed_at": _T1, "removed_by_id": u, "updated_at": _T1},
             "an active line carrying a removal"),
            ({"updated_at": _EARLIER}, "updated_at before created_at"),
            (dict(removed, removed_at=_EARLIER), "removed before created"),
            (dict(removed, updated_at=_T0), "updated before removed"),
            ({"invoice_id": "999999"}, "the invoice foreign key"),
            (dict(removed, removed_by_id="999999"), "the remover foreign key"),
            ({"invoice_id": "NULL"}, "invoice_id NOT NULL"),
            ({"label": "NULL"}, "label NOT NULL"),
            ({"amount": "NULL"}, "amount NOT NULL"),
            ({"kind": "NULL"}, "kind NOT NULL"),
        ):
            _refused(_line_sql(i, **overrides), rule)
        assert InvoiceItem.query.count() == 5


def _event_sql(owner_id, author_id, **overrides):
    values = {
        "invoice_id": str(owner_id),
        "actor_id": str(author_id),
        "kind": "'invoice_draft_created'",
        "occurred_at": _T0,
        "invoice_version_before": "NULL",
        "invoice_version_after": "1",
        "reason": "NULL",
        "before_snapshot": "NULL",
        "after_snapshot": _SNAPSHOT,
    }
    values.update(overrides)
    return _insert("payment_audit_events", values)


def test_every_audit_event_constraint_is_enforced(app):
    with app.app_context():
        actor, assignment = _owners()
        owner = fx.invoice(assignment, actor, lines=())
        i, u = owner.id, actor.id
        edited = {"kind": "'invoice_draft_edited'", "invoice_version_before": "1",
                  "invoice_version_after": "2", "before_snapshot": _SNAPSHOT}
        issued = dict(edited, kind="'invoice_issued'", invoice_version_before="2",
                      invoice_version_after="3")
        issued_edited = dict(edited, kind="'invoice_issued_edited'", reason="'Corrected'",
                             invoice_version_before="3", invoice_version_after="4")
        cancelled = dict(edited, kind="'invoice_cancelled'", reason="'Duplicate'",
                         invoice_version_before="4", invoice_version_after="5")
        for accepted in ({}, edited, issued, issued_edited, cancelled):
            _accepted(_event_sql(i, u, **accepted))

        for overrides, rule in (
            ({"kind": "'payment_recorded'"}, "ck_payment_audit_events_kind_valid"),
            ({"kind": "'INVOICE_ISSUED'"}, "the case-sensitive kind"),
            ({"invoice_version_before": "1", "invoice_version_after": "2"},
             "a creation from an earlier version"),
            ({"invoice_version_after": "2"}, "a creation at version 2"),
            (dict(edited, invoice_version_before="NULL"), "a later event without its version"),
            (dict(edited, invoice_version_after="3"), "a skipped version"),
            (dict(edited, invoice_version_before="2", invoice_version_after="1"),
             "a version moving backwards"),
            ({"invoice_version_after": "0"}, "a zero version"),
            (dict(edited, invoice_version_before="0", invoice_version_after="1"),
             "a zero earlier version"),
            ({"before_snapshot": _SNAPSHOT}, "a creation with a before snapshot"),
            (dict(edited, before_snapshot="NULL"), "a later event without a before snapshot"),
            ({"after_snapshot": "NULL"}, "an event without an after snapshot"),
            (dict(issued_edited, reason="NULL"), "a post-issue edit without a reason"),
            (dict(issued_edited, reason="''"), "a post-issue edit with an empty reason"),
            (dict(cancelled, reason="NULL"), "a cancellation without a reason"),
            (dict(edited, reason="'Why'"), "a draft edit with a reason"),
            (dict(issued, reason="'Why'"), "an issue with a reason"),
            ({"reason": "'Why'"}, "a creation with a reason"),
            ({"invoice_id": "999999"}, "the invoice foreign key"),
            ({"actor_id": "999999"}, "the actor foreign key"),
            ({"invoice_id": "NULL"}, "invoice_id NOT NULL"),
            ({"actor_id": "NULL"}, "actor_id NOT NULL"),
            ({"kind": "NULL"}, "kind NOT NULL"),
            ({"occurred_at": "NULL"}, "occurred_at NOT NULL"),
        ):
            _refused(_event_sql(i, u, **overrides), rule)
        assert PaymentAuditEvent.query.count() == 5


def _sequence_sql(**overrides):
    values = {"calendar_year": "2026", "last_number": "0", "created_at": _T0, "updated_at": _T0}
    values.update(overrides)
    return _insert("invoice_number_sequences", values)


def test_every_sequence_constraint_is_enforced(app):
    with app.app_context():
        _accepted(_sequence_sql())
        _accepted(_sequence_sql(calendar_year="2027", last_number="999999"))
        _accepted(_sequence_sql(calendar_year="1000"))
        _accepted(_sequence_sql(calendar_year="9999"))
        for overrides, rule in (
            ({}, "uq_invoice_number_sequences_calendar_year"),
            ({"calendar_year": "999"}, "a three-digit year"),
            ({"calendar_year": "10000"}, "a five-digit year"),
            ({"calendar_year": "2030", "last_number": "-1"}, "a negative number"),
            ({"calendar_year": "2030", "last_number": "1000000"}, "a seven-digit number"),
            ({"calendar_year": "2030", "updated_at": _EARLIER}, "updated_at before created_at"),
            ({"calendar_year": "NULL"}, "calendar_year NOT NULL"),
            ({"calendar_year": "2030", "last_number": "NULL"}, "last_number NOT NULL"),
        ):
            _refused(_sequence_sql(**overrides), rule)
        assert InvoiceNumberSequence.query.count() == 4


def test_one_open_invoice_per_assignment_is_an_application_rule_not_a_constraint(app):
    """MySQL has no portable partial unique index for "draft or issued rows
    only", so the database accepts a second draft; the routes prove the rule
    under the assignment lock (see the route and transaction suites)."""
    with app.app_context():
        _actor, assignment = _owners()
        _accepted(_invoice_sql(assignment.id))
        _accepted(_invoice_sql(assignment.id))
        assert Invoice.query.filter_by(status="draft").count() == 2


def test_the_database_refuses_deleting_anything_referenced(app):
    with app.app_context():
        actor, assignment = _owners()
        owner = fx.invoice(assignment, actor)
        fx.event(owner, actor, 1)
        assignment_id, invoice_id, actor_id = assignment.id, owner.id, actor.id
        _refused(f"DELETE FROM student_fee_assignments WHERE id = {assignment_id}",
                 "no cascade from an assignment")
        _refused(f"DELETE FROM invoices WHERE id = {invoice_id}", "no cascade from an invoice")
        _refused(f"DELETE FROM users WHERE id = {actor_id}", "no cascade from an account")
        assert _counts() == (1, 2, 1, 0)


@pytest.mark.parametrize(
    "model, expected",
    [
        (Invoice, {("student_fee_assignment_id", "student_fee_assignments"),
                   ("issued_by_id", "users"), ("cancelled_by_id", "users")}),
        (InvoiceItem, {("invoice_id", "invoices"), ("removed_by_id", "users")}),
        # Phase 5 / M05 links a payment or receipt event to its rows.
        (PaymentAuditEvent, {("invoice_id", "invoices"), ("actor_id", "users"),
                             ("payment_transaction_id", "payment_transactions"),
                             ("receipt_id", "receipts")}),
        (InvoiceNumberSequence, set()),
    ],
)
def test_foreign_keys_are_plain_with_no_relationship_or_cascade(app, model, expected):
    with app.app_context():
        assert not sa.inspect(model).relationships
        foreign_keys = model.__table__.foreign_keys
        for foreign_key in foreign_keys:
            assert foreign_key.ondelete is None and foreign_key.onupdate is None
        assert {(fk.parent.name, fk.column.table.name) for fk in foreign_keys} == expected


_COLUMNS = {
    Invoice: {"id", "public_id", "student_fee_assignment_id", "currency_code", "status",
              "invoice_number", "issued_at", "issued_by_id", "cancelled_at", "cancelled_by_id",
              "version", "created_at", "updated_at"},
    InvoiceItem: {"id", "public_id", "invoice_id", "kind", "label", "amount", "status",
                  "removed_at", "removed_by_id", "version", "created_at", "updated_at"},
    PaymentAuditEvent: {"id", "invoice_id", "actor_id", "kind", "occurred_at",
                        "invoice_version_before", "invoice_version_after", "reason",
                        "before_snapshot", "after_snapshot", "payment_transaction_id",
                        "receipt_id"},
    InvoiceNumberSequence: {"id", "calendar_year", "last_number", "created_at", "updated_at"},
}

#: Column-name parts that would mean card, bank, provider or payment data, a
#: stored total, or identity duplicated from the assignment chain.
_PROHIBITED_PARTS = (
    "card", "pan", "cvv", "cvc", "expiry", "iban", "bank", "account", "token", "secret",
    "password", "provider", "intent", "customer", "webhook", "receipt", "refund", "discount",
    "tax", "installment", "due", "quantity", "total", "paid", "payment", "enrollment", "group",
    "course", "level", "term", "plan", "name",
)


@pytest.mark.parametrize("model", list(_COLUMNS), ids=lambda model: model.__tablename__)
def test_no_card_payment_total_or_duplicated_identity_column_exists(model):
    columns = {column.name for column in model.__table__.columns}
    assert columns == _COLUMNS[model]
    # Phase 5 / M05's two audit links name a payment transaction and a receipt
    # by id; they store no payment data themselves.
    for name in columns - {"student_fee_assignment_id", "payment_transaction_id", "receipt_id"}:
        for part in _PROHIBITED_PARTS:
            assert part not in name.split("_"), (name, part)
    assert "fee_plan_item_id" not in columns


# ===========================================================================
# ORM guards
# ===========================================================================


def test_no_invoice_line_event_or_sequence_is_ever_deleted_through_the_orm(app):
    with app.app_context():
        actor, assignment = _owners()
        owner = fx.invoice(assignment, actor)
        first = InvoiceItem.query.filter_by(invoice_id=owner.id).first()
        entry = fx.event(owner, actor, 1)
        counter = fx.sequence(2026, 3)
        for row in (first, entry, counter, owner):
            db.session.delete(row)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        assert _counts() == (1, 2, 1, 1)


def test_an_audit_event_is_never_updated_through_the_orm(app):
    with app.app_context():
        actor, assignment = _owners()
        other = fees.admin("other@example.com")
        owner = fx.invoice(assignment, actor)
        entry = fx.event(owner, actor, 1)
        entry_id, actor_id, other_id = entry.id, actor.id, other.id
        for field, value in (("occurred_at", fx.ISSUED_AT), ("invoice_version_after", 2),
                             ("actor_id", other_id), ("kind", "invoice_draft_edited")):
            setattr(entry, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        db.session.expire_all()
        stored = db.session.get(PaymentAuditEvent, entry_id)
        assert (stored.occurred_at, stored.invoice_version_after, stored.actor_id, stored.kind) == (
            fx.CREATED_AT, 1, actor_id, "invoice_draft_created")


_BULK_STATEMENTS = {
    "an ORM UPDATE of audit events": lambda: sa.update(PaymentAuditEvent).values(reason="x"),
    "a Core UPDATE of audit events": lambda: sa.update(PaymentAuditEvent.__table__).values(
        reason="x"),
    "an ORM DELETE of audit events": lambda: sa.delete(PaymentAuditEvent),
    "an ORM DELETE of invoices": lambda: sa.delete(Invoice),
    "a Core DELETE of invoice lines": lambda: sa.delete(InvoiceItem.__table__),
    "an ORM DELETE of sequences": lambda: sa.delete(InvoiceNumberSequence),
}


@pytest.mark.parametrize("name", sorted(_BULK_STATEMENTS))
def test_bulk_rewrites_of_financial_history_are_refused(app, name):
    with app.app_context():
        actor, assignment = _owners()
        owner = fx.invoice(assignment, actor)
        fx.event(owner, actor, 1)
        fx.sequence(2026, 1)
        with pytest.raises(FinancialHistoryError):
            db.session.execute(_BULK_STATEMENTS[name]())
        db.session.rollback()
        assert _counts() == (1, 2, 1, 1)


def test_legacy_query_bulk_writes_of_audit_events_are_refused(app):
    with app.app_context():
        actor, assignment = _owners()
        fx.event(fx.invoice(assignment, actor), actor, 1)
        with pytest.raises(FinancialHistoryError):
            db.session.query(PaymentAuditEvent).update({"reason": "x"})
        db.session.rollback()
        with pytest.raises(FinancialHistoryError):
            db.session.query(PaymentAuditEvent).delete()
        db.session.rollback()
        assert PaymentAuditEvent.query.one().reason is None


@pytest.mark.parametrize("number", [None, "INV-2026-000001"])
def test_a_cancelled_invoice_is_read_only_through_the_orm(app, number):
    with app.app_context():
        actor, assignment = _owners()
        owner = fx.invoice(assignment, actor, status=fx.CANCELLED, number=number)
        version = owner.version
        for field, value in (("version", version + 1), ("status", "draft"),
                             ("updated_at", fx.CANCELLED_AT.replace(hour=10))):
            db.session.expire_all()
            setattr(owner, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        stored = fx.stored_invoice(owner.public_id)
        assert (stored.status, stored.version, stored.updated_at) == (
            "cancelled", version, fx.CANCELLED_AT)


def test_a_number_the_issue_record_and_the_owner_never_change(app):
    with app.app_context():
        actor, assignment = _owners()
        other = fees.admin("other@example.com")
        other_assignment = fees.assignment(fees.enrollment(), fees.active_plan(actor), actor)
        owner = fx.invoice(assignment, actor, status=fx.ISSUED, number="INV-2026-000001")
        for field, value in (("invoice_number", "INV-2026-000002"), ("invoice_number", None),
                             ("issued_at", fx.CANCELLED_AT), ("issued_by_id", other.id),
                             ("student_fee_assignment_id", other_assignment.id)):
            db.session.expire_all()
            setattr(owner, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        stored = fx.stored_invoice(owner.public_id)
        assert (stored.invoice_number, stored.issued_at, stored.issued_by_id,
                stored.student_fee_assignment_id) == (
            "INV-2026-000001", fx.ISSUED_AT, actor.id, assignment.id)


def test_a_removed_line_never_changes_and_no_line_moves(app):
    with app.app_context():
        actor, assignment = _owners()
        owner = fx.invoice(assignment, actor, lines=())
        other = fx.invoice(fees.assignment(fees.enrollment(), fees.active_plan(actor), actor),
                           actor, lines=())
        gone = fx.line(owner, label="Gone", status=fx.LINE_REMOVED, removed_by=actor)
        kept = fx.line(owner, label="Kept")
        for row, field, value in ((gone, "label", "Back"), (gone, "status", "active"),
                                  (kept, "invoice_id", other.id)):
            db.session.expire_all()
            setattr(row, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        assert [(r.label, r.status, r.invoice_id) for r in fx.stored_lines(owner.public_id)] == [
            ("Gone", "removed", owner.id), ("Kept", "active", owner.id)]


def test_a_sequence_never_goes_backwards_or_changes_year(app):
    with app.app_context():
        counter = fx.sequence(2026, 5)
        for field, value in (("last_number", 4), ("calendar_year", 2027)):
            db.session.expire_all()
            setattr(counter, field, value)
            with pytest.raises(FinancialHistoryError):
                db.session.flush()
            db.session.rollback()
        counter.last_number = 6
        db.session.commit()
        db.session.expire_all()
        stored = InvoiceNumberSequence.query.one()
        assert (stored.calendar_year, stored.last_number) == (2026, 6)


def test_the_guards_allow_the_permitted_changes(app):
    with app.app_context():
        actor, assignment = _owners()
        owner = fx.invoice(assignment, actor)
        actor_id = actor.id
        line = InvoiceItem.query.filter_by(invoice_id=owner.id).first()
        line.amount = "12.000"
        line.version = 2
        owner.status = "issued"
        owner.invoice_number = "INV-2026-000001"
        owner.issued_at = fx.ISSUED_AT
        owner.issued_by_id = actor_id
        owner.version = 2
        owner.updated_at = fx.ISSUED_AT
        db.session.commit()
        owner.status = "cancelled"
        owner.cancelled_at = fx.CANCELLED_AT
        owner.cancelled_by_id = actor_id
        owner.version = 3
        owner.updated_at = fx.CANCELLED_AT
        db.session.commit()
        stored = fx.stored_invoice(owner.public_id)
        assert (stored.status, stored.invoice_number, stored.version) == (
            "cancelled", "INV-2026-000001", 3)


def test_an_absent_before_snapshot_is_sql_null_not_json_null(app):
    with app.app_context():
        actor, assignment = _owners()
        entry = fx.event(fx.invoice(assignment, actor), actor, 1)
        raw = db.session.execute(
            sa.text("SELECT before_snapshot IS NULL, after_snapshot IS NULL "
                    "FROM payment_audit_events WHERE id = :id"), {"id": entry.id}).one()
        assert tuple(raw) == (1, 0)
        assert entry.after_snapshot["schema"] == "phase5-m04.invoice.v1"
