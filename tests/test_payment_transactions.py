"""Phase 5 / M05 -- tokens, the lock chains, receipt numbering, post-lock
revalidation and ``IntegrityError`` recovery for manual payments.

SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so the lock tests
assert what the code *requests* -- which rows, in which order -- and the race
tests inject a competing change at the exact transaction boundary (between the
pre-lock reads and the lock chain, or at the receipt sequence lock) to prove
every write re-decides against the locked rows. None of this proves real
InnoDB blocking.
"""

import re
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

import app.blueprints.admin.invoices as invoice_routes
import app.blueprints.admin.payments as routes
import app.services.payment_audit as audit
import tests.fee_assignment_fixtures as fees
import tests.invoice_fixtures as fx
import tests.payment_fixtures as px
import tests.structural_checks as sc
from app.extensions import db
from app.models import (
    MAX_RECEIPT_SEQUENCE_NUMBER,
    Enrollment,
    FeePlan,
    Group,
    Invoice,
    InvoiceItem,
    PaymentAuditEvent,
    PaymentTransaction,
    ReceiptNumberSequence,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import fee_plan_tokens, invoice_tokens
from app.services import payment_tokens as tokens
from app.services.payment_transactions import (
    allocate_receipt_number,
    lock_payment_chain,
    lock_receipt_number_sequence,
    locked_invoice_payments,
)

_PREFIX = ["academic_terms", "levels", "courses", "groups", "users", "enrollments", "users",
           "student_fee_assignments", "invoices"]
_MOMENT = datetime(2026, 7, 10, 12, 0, 0)

def _sql_moment(moment):
    """A moment as the text raw SQL binds, so no deprecated sqlite3 adapter runs."""
    return moment.strftime("%Y-%m-%d %H:%M:%S")


CASH = tokens.PURPOSE_CASH_RECORD
BANK = tokens.PURPOSE_BANK_RECORD
CONFIRM = tokens.PURPOSE_BANK_CONFIRM
REJECT = tokens.PURPOSE_BANK_REJECT
REVERSE = tokens.PURPOSE_REVERSE


# ===========================================================================
# Tokens
# ===========================================================================


def _payloads():
    actor, invoice, payment = "a" * 36, "i" * 36, "p" * 36
    state = [["x" * 36, "pending", 1], ["y" * 36, "confirmed", 2]]
    record = {"actor_public_id": actor, "invoice_public_id": invoice, "invoice_version": 2,
              "payment_state": state}
    decision = {"actor_public_id": actor, "invoice_public_id": invoice,
                "payment_public_id": payment, "payment_version": 1, "payment_state": state}
    return {
        CASH: record,
        BANK: record,
        CONFIRM: decision,
        REJECT: decision,
        REVERSE: {"actor_public_id": actor, "invoice_public_id": invoice, "invoice_version": 2,
                  "payment_public_id": payment, "payment_version": 1,
                  "receipt_state": ["r" * 36, "issued", 1]},
    }


def test_the_five_purposes_cannot_be_replayed_as_one_another(app):
    with app.app_context():
        payloads = _payloads()
        assert set(payloads) == set(tokens.PURPOSES)
        for purpose, payload in payloads.items():
            token = tokens.make_token(purpose, **payload)
            assert tokens.load_token(token, purpose) == dict(payload, purpose=purpose)
            for other in tokens.PURPOSES:
                if other != purpose:
                    assert tokens.load_token(token, other) is None, (purpose, other)
        receipted = dict(payloads[REVERSE], receipt_state=None)
        assert tokens.load_token(tokens.make_token(REVERSE, **receipted), REVERSE) is not None


def test_a_token_from_another_milestone_is_not_a_payment_token(app):
    with app.app_context():
        foreign = [
            fee_plan_tokens.make_token(fee_plan_tokens.PURPOSE_CREATE, actor_public_id="a" * 36),
            invoice_tokens.make_token(invoice_tokens.PURPOSE_CANCEL, actor_public_id="a" * 36,
                                      invoice_public_id="i" * 36, invoice_version=2),
        ]
        for token in foreign:
            for purpose in tokens.PURPOSES:
                assert tokens.load_token(token, purpose) is None


def test_tampered_malformed_and_wrongly_shaped_tokens_are_refused(app):
    with app.app_context():
        payload = _payloads()[CONFIRM]
        token = tokens.make_token(CONFIRM, **payload)
        middle = len(token) // 3
        tampered = token[:middle] + ("x" if token[middle] != "x" else "y") + token[middle + 1:]
        serializer = tokens._serializer(CONFIRM)
        body = dict(payload, purpose=CONFIRM)
        for bad in (
            None, "", 42, tampered, token + "x", "x" * 9000, "not.a.token",
            serializer.dumps(dict(body, extra="1")),
            serializer.dumps({"purpose": CONFIRM}),
            serializer.dumps(dict(body, purpose=REJECT)),
            serializer.dumps(dict(body, payment_version=True)),
            serializer.dumps(dict(body, payment_version=0)),
            serializer.dumps(dict(body, payment_version="1")),
            serializer.dumps(dict(body, payment_version=2**31)),
            serializer.dumps(dict(body, payment_public_id="")),
            serializer.dumps(dict(body, actor_public_id="a" * 65)),
            serializer.dumps(dict(body, payment_state="x")),
            serializer.dumps(dict(body, payment_state=[["x", "pending"]])),
            serializer.dumps(dict(body, payment_state=[["x", "pending", 1, 2]])),
            serializer.dumps(dict(body, payment_state=[["x", "paid", 1]])),
            serializer.dumps(dict(body, payment_state=[["x", "pending", 0]])),
            serializer.dumps(dict(body, payment_state=[["x", "pending", True]])),
            serializer.dumps(dict(body, payment_state=[[1, "pending", 1]])),
            serializer.dumps(dict(body, payment_state=[["x", "pending", 1],
                                                       ["x", "confirmed", 2]])),
            serializer.dumps(dict(body, payment_state=[[f"x{n}", "pending", 1]
                                                       for n in range(51)])),
            serializer.dumps(["not", "a", "dict"]),
        ):
            assert tokens.load_token(bad, CONFIRM) is None, bad
            assert tokens.token_is_stale(bad, CONFIRM, **payload)
        assert not tokens.token_is_stale(token, CONFIRM, **payload)
        for changed in ({"payment_version": 2}, {"payment_public_id": "q" * 36},
                        {"payment_state": payload["payment_state"][:1]},
                        {"payment_state": list(reversed(payload["payment_state"]))},
                        {"payment_state": [["x" * 36, "rejected", 2], ["y" * 36, "confirmed", 2]]}):
            assert tokens.token_is_stale(token, CONFIRM, **dict(payload, **changed)), changed

        reverse = dict(_payloads()[REVERSE], purpose=REVERSE)
        reverse_serializer = tokens._serializer(REVERSE)
        for bad_state in ("x", ["r", "issued"], ["r", "deleted", 1], ["r", "issued", 0]):
            assert tokens.load_token(
                reverse_serializer.dumps(dict(reverse, receipt_state=bad_state)), REVERSE) is None


def test_an_expired_token_is_refused(app, monkeypatch):
    with app.app_context():
        token = tokens.make_token(REJECT, **_payloads()[REJECT])
        assert tokens.load_token(token, REJECT) is not None
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
        assert tokens.load_token(token, REJECT) is None


def test_a_token_for_the_largest_payment_state_stays_within_its_bound(app):
    with app.app_context():
        state = [[str(uuid.uuid4()), "confirmed", 2**31 - 1] for _ in range(50)]
        token = tokens.make_token(CASH, actor_public_id=str(uuid.uuid4()),
                                  invoice_public_id=str(uuid.uuid4()), invoice_version=2**31 - 1,
                                  payment_state=state)
        assert len(token) <= tokens._MAX_TOKEN_LENGTH
        assert tokens.load_token(token, CASH)["payment_state"] == state


def _token_world(app, client):
    w = px.login_world(app, client)
    with app.app_context():
        owner, actor = px.rows_of(app, w)
        cash, issued = px.cash_with_receipt(owner, actor, amount="100")
        pending = px.payment(owner, actor, method="bank_transfer", status="pending", amount="10",
                             reference="TRX-SECRET")
        other_pending = px.payment(owner, actor, method="bank_transfer", status="pending",
                                   amount="11")
        other_cash, _other_receipt = px.cash_with_receipt(owner, actor, amount="12")
        second = fx.admin("second@example.com")
        plan = db.session.get(FeePlan, w["plan_id"])
        elsewhere = px.issued_invoice(fees.assignment(fees.enrollment(), plan, actor), actor)
        ids = {"cash": cash.public_id, "receipt": issued.public_id, "pending": pending.public_id,
               "other_pending": other_pending.public_id, "other_cash": other_cash.public_id,
               "second": second.public_id, "elsewhere": elsewhere.public_id}
        state = tokens.payment_state(PaymentTransaction.query.filter_by(invoice_id=owner.id)
                                     .order_by(PaymentTransaction.id).all())
    return w, ids, state


def test_rendered_tokens_carry_no_internal_id_name_amount_reference_or_reason(app, client):
    w, ids, state = _token_world(app, client)
    rendered = {
        CASH: px.cash_token(client, w),
        BANK: px.bank_token(client, w),
        CONFIRM: px.confirm_token(client, w, ids["pending"]),
        REJECT: px.reject_token(client, w, ids["pending"]),
        REVERSE: px.reverse_token(client, w, ids["cash"]),
    }
    with app.app_context():
        rows = px.stored_payments(w)
        internal = {str(value) for value in (
            w["invoice_id"], w["assignment_id"], w["enrollment_id"], w["group_id"], w["plan_id"],
            w["admin_id"], w["student_id"], *[row.id for row in rows],
            *[r.id for r in px.stored_receipts(w)])}
    record = {"actor_public_id": w["admin_public_id"], "invoice_public_id": w["ip"],
              "invoice_version": 2, "payment_state": state}
    decision = {"actor_public_id": w["admin_public_id"], "invoice_public_id": w["ip"],
                "payment_public_id": ids["pending"], "payment_version": 1, "payment_state": state}
    expected = {
        CASH: record, BANK: record, CONFIRM: decision, REJECT: decision,
        REVERSE: {"actor_public_id": w["admin_public_id"], "invoice_public_id": w["ip"],
                  "invoice_version": 2, "payment_public_id": ids["cash"], "payment_version": 1,
                  "receipt_state": [ids["receipt"], "issued", 1]},
    }
    with app.app_context():
        for purpose, token in rendered.items():
            assert token, purpose
            payload = tokens.load_token(token, purpose)
            assert payload == dict(expected[purpose], purpose=purpose), purpose
            # Value by value, never as one string: a public id is random
            # hexadecimal and contains "1250" by chance. A value that is
            # exactly a public id carries nothing else; every other value is
            # searched.
            for leaf in sc.leaves(payload):
                if sc.is_public_id(leaf):
                    continue
                for secret in ("Student One", "Standard plan", "100.000", "1250", "LYD",
                               "TRX-SECRET", "Not received", "RCT-", "INV-"):
                    assert secret not in str(leaf), (purpose, leaf, secret)
            # Every identifier a token carries is a public id; versions are
            # small integers and are compared as such above.
            values = [str(v) for k, v in payload.items() if k.endswith("public_id")]
            for key in ("payment_state", "receipt_state"):
                entries = payload.get(key) or []
                entries = [entries] if key == "receipt_state" and entries else entries
                values += [str(entry[0]) for entry in entries]
            assert not set(values) & internal, purpose
            assert all(len(value) == 36 for value in values), purpose


# ===========================================================================
# Tokens at the routes
# ===========================================================================

_COMMON = ["missing", "forged", "tampered", "wrong_purpose", "stale", "other_actor", "expired",
           "cross_invoice"]
_CASES = (
    [(purpose, kind) for purpose in tokens.PURPOSES for kind in _COMMON]
    + [(purpose, "cross_payment") for purpose in (CONFIRM, REJECT, REVERSE)]
    + [(purpose, "stale_version") for purpose in (CONFIRM, REJECT, REVERSE)]
)


def _post(client, purpose, w, ids, token):
    if purpose == CASH:
        return px.record_cash(client, w, amount="5", token=token)
    if purpose == BANK:
        return px.record_bank(client, w, amount="5", token=token)
    if purpose == CONFIRM:
        return px.confirm(client, w, ids["pending"], token=token)
    if purpose == REJECT:
        return px.reject(client, w, ids["pending"], token=token)
    return px.reverse(client, w, ids["cash"], token=token)


@pytest.mark.parametrize("purpose, kind", _CASES)
def test_every_mutation_refuses_every_token_but_a_current_one(
    app, client, monkeypatch, purpose, kind
):
    w, ids, state = _token_world(app, client)
    base = {"actor_public_id": w["admin_public_id"], "invoice_public_id": w["ip"]}
    if purpose in (CASH, BANK):
        payload = dict(base, invoice_version=2, payment_state=state)
        stale = {"payment_state": state[:-1]}
        cross = {}
    elif purpose in (CONFIRM, REJECT):
        payload = dict(base, payment_public_id=ids["pending"], payment_version=1,
                       payment_state=state)
        stale = {"payment_state": [[pid, "rejected", 2] if pid == ids["pending"] else
                                   [pid, status, version] for pid, status, version in state]}
        cross = {"cross_payment": {"payment_public_id": ids["other_pending"]},
                 "stale_version": {"payment_version": 2}}
    else:
        payload = dict(base, invoice_version=2, payment_public_id=ids["cash"], payment_version=1,
                       receipt_state=[ids["receipt"], "issued", 1])
        stale = {"receipt_state": [ids["receipt"], "voided", 2]}
        cross = {"cross_payment": {"payment_public_id": ids["other_cash"]},
                 "stale_version": {"invoice_version": 3}}
    with app.app_context():
        genuine = tokens.make_token(purpose, **payload)
        middle = len(genuine) // 2
        wrong = REVERSE if purpose != REVERSE else CONFIRM
        variants = {
            "missing": "",
            "forged": "forged",
            "tampered": genuine[:middle] + ("A" if genuine[middle] != "A" else "B")
            + genuine[middle + 1:],
            "wrong_purpose": tokens.make_token(wrong, **_payloads()[wrong]),
            "stale": tokens.make_token(purpose, **dict(payload, **stale)),
            "other_actor": tokens.make_token(purpose, **dict(payload,
                                                              actor_public_id=ids["second"])),
            "expired": genuine,
            "cross_invoice": tokens.make_token(purpose, **dict(payload,
                                                                invoice_public_id=ids["elsewhere"])),
        }
        if kind in cross:
            variants[kind] = tokens.make_token(purpose, **dict(payload, **cross[kind]))
        token = variants[kind]
    before = px.record(app)
    if kind == "expired":
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
    response = _post(client, purpose, w, ids, token)
    monkeypatch.undo()
    assert response.status_code == 302
    assert px.STALE_TEXT in px.followed(client, response)
    assert px.record(app) == before
    # And the genuine token still works, proving the refusal was the token's.
    assert _post(client, purpose, w, ids, genuine).status_code == 302
    assert px.record(app) != before


# ===========================================================================
# The lock chains -- structural
# ===========================================================================


def _record_lock_requests(monkeypatch):
    requested = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        requested.append(self.column_descriptions[0]["entity"].__tablename__)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy)
    return requested


class _ChainReads:
    """``(table, id)`` for every by-id row read issued **inside** the named
    lock functions of `module`, in execution order."""

    def __init__(self, monkeypatch, module, names):
        self.reads, self._on = [], False
        for name in names:
            real = getattr(module, name)

            def wrapper(*args, _real=real, **kwargs):
                self._on = True
                try:
                    return _real(*args, **kwargs)
                finally:
                    self._on = False

            monkeypatch.setattr(module, name, wrapper)

    def _listener(self, conn, cursor, statement, parameters, context, executemany):
        flat = " ".join(statement.split())
        match = re.search(r"FROM (\w+) WHERE \1\.id = \?", flat)
        if self._on and flat.startswith("SELECT") and match:
            self.reads.append((match.group(1), parameters[0]))

    def __enter__(self):
        sa_event.listen(db.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        sa_event.remove(db.engine, "before_cursor_execute", self._listener)

    def ids(self, table):
        return [row_id for name, row_id in self.reads if name == table]


def _lock_world(app, client):
    """An issued invoice holding a rejected, a pending and a confirmed row,
    and another invoice's row that must never be locked."""
    w = px.login_world(app, client)
    with app.app_context():
        owner, actor = px.rows_of(app, w)
        rejected = px.payment(owner, actor, method="bank_transfer", status="rejected", amount="3")
        pending = px.payment(owner, actor, method="bank_transfer", status="pending", amount="4")
        cash, issued = px.cash_with_receipt(owner, actor, amount="5")
        plan = db.session.get(FeePlan, w["plan_id"])
        elsewhere = px.issued_invoice(fees.assignment(fees.enrollment(), plan, actor), actor)
        px.payment(elsewhere, actor, amount="6")
        px.sequence(fx.center_year(app, _MOMENT), 1)
        rows = {"rejected": rejected, "pending": pending, "cash": cash}
        return w, {k: (v.id, v.public_id) for k, v in rows.items()}, (issued.id, issued.public_id)


def test_cash_takes_the_documented_lock_order_with_the_sequence_last(app, client, monkeypatch):
    w, rows, _receipt = _lock_world(app, client)
    token = px.cash_token(client, w)
    requested = _record_lock_requests(monkeypatch)
    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    with _ChainReads(monkeypatch, routes, ["lock_payment_chain",
                                           "lock_receipt_number_sequence"]) as chain:
        response = px.record_cash(client, w, amount="7", token=token)
    locked = requested[:]
    monkeypatch.undo()
    assert px.CASH_OK_TEXT in px.followed(client, response)
    assert locked == _PREFIX + ["payment_transactions"] * 3 + ["receipt_number_sequences"]
    assert chain.ids("invoices") == [w["invoice_id"]]
    assert chain.ids("payment_transactions") == sorted(row_id for row_id, _ in rows.values())


def test_a_bank_transfer_recording_locks_the_invoices_payments_and_no_sequence(
    app, client, monkeypatch
):
    w, rows, _receipt = _lock_world(app, client)
    token = px.bank_token(client, w)
    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch, routes, ["lock_payment_chain"]) as chain:
        response = px.record_bank(client, w, amount="7", token=token)
    locked = requested[:]
    monkeypatch.undo()
    assert px.BANK_RECORDED_OK_TEXT in px.followed(client, response)
    assert locked == _PREFIX + ["payment_transactions"] * 3
    assert chain.ids("payment_transactions") == sorted(row_id for row_id, _ in rows.values())


def test_confirmation_locks_the_target_first_then_the_rest_then_the_sequence(
    app, client, monkeypatch
):
    w, rows, _receipt = _lock_world(app, client)
    target_id, target = rows["pending"]
    token = px.confirm_token(client, w, target)
    requested = _record_lock_requests(monkeypatch)
    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    with _ChainReads(monkeypatch, routes, ["lock_payment_chain",
                                           "lock_receipt_number_sequence"]) as chain:
        response = px.confirm(client, w, target, token=token)
    locked = requested[:]
    monkeypatch.undo()
    assert px.BANK_CONFIRMED_OK_TEXT in px.followed(client, response)
    assert locked == _PREFIX + ["payment_transactions"] * 3 + ["receipt_number_sequences"]
    others = sorted(row_id for row_id, _ in rows.values() if row_id != target_id)
    assert chain.ids("payment_transactions") == [target_id] + others


def test_rejection_locks_only_its_target_after_the_invoice(app, client, monkeypatch):
    w, rows, _receipt = _lock_world(app, client)
    target_id, target = rows["pending"]
    token = px.reject_token(client, w, target)
    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch, routes, ["lock_payment_chain"]) as chain:
        response = px.reject(client, w, target, token=token)
    locked = requested[:]
    monkeypatch.undo()
    assert px.BANK_REJECTED_OK_TEXT in px.followed(client, response)
    assert locked == _PREFIX + ["payment_transactions"]
    assert chain.ids("payment_transactions") == [target_id]


def test_reversal_locks_the_original_then_the_rest_then_its_receipt(app, client, monkeypatch):
    w, rows, (receipt_id, _rp) = _lock_world(app, client)
    target_id, target = rows["cash"]
    token = px.reverse_token(client, w, target)
    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch, routes, ["lock_payment_chain"]) as chain:
        response = px.reverse(client, w, target, token=token)
    locked = requested[:]
    monkeypatch.undo()
    assert px.REVERSED_OK_TEXT in px.followed(client, response)
    assert locked == _PREFIX + ["payment_transactions"] * 3 + ["receipts"]
    others = sorted(row_id for row_id, _ in rows.values() if row_id != target_id)
    assert chain.ids("payment_transactions") == [target_id] + others
    assert chain.ids("receipts") == [receipt_id]


def _chain(w, **links):
    return lock_payment_chain(w["gp"], w["term_id"], w["level_id"], w["course_id"],
                              w["student_id"], w["enrollment_id"], w["admin_id"],
                              w["assignment_id"], **links)


def test_the_chain_stops_where_the_arguments_stop_and_resets_once(app, monkeypatch):
    w = px.world(app)
    with app.app_context():
        owner, actor = px.rows_of(app, w)
        cash, issued = px.cash_with_receipt(owner, actor)
        pending = px.payment(owner, actor, method="bank_transfer", status="pending", amount="2")
        cash_id, pending_id, receipt_id = cash.id, pending.id, issued.id
        requested = _record_lock_requests(monkeypatch)

        locks = _chain(w, invoice_id=None)
        assert requested == _PREFIX[:-1] and locks.chain.invoice is None
        requested.clear()
        locks = _chain(w, invoice_id=w["invoice_id"])
        assert requested == _PREFIX and locks.payment is None and locks.payments == {}
        requested.clear()
        locks = _chain(w, invoice_id=w["invoice_id"], payment_id=pending_id)
        assert requested == _PREFIX + ["payment_transactions"]
        assert [row.id for row in locked_invoice_payments(locks)] == [pending_id]
        requested.clear()
        locks = _chain(w, invoice_id=w["invoice_id"], payment_id=cash_id,
                       include_invoice_payments=True, include_receipt=True)
        assert requested == _PREFIX + ["payment_transactions"] * 2 + ["receipts"]
        assert [row.id for row in locked_invoice_payments(locks)] == [cash_id, pending_id]
        assert locks.receipt.id == receipt_id

        requested.clear()
        counter = lock_receipt_number_sequence(2031, fx.CREATED_AT)
        assert requested == ["receipt_number_sequences"]
        assert allocate_receipt_number(counter, fx.CREATED_AT) == "RCT-2031-000001"
        counter.last_number = MAX_RECEIPT_SEQUENCE_NUMBER
        assert allocate_receipt_number(counter, fx.CREATED_AT) is None
        db.session.rollback()

        rollbacks = []

        def _rec(conn):
            rollbacks.append(1)

        sa_event.listen(db.engine, "rollback", _rec)
        try:
            _chain(w, invoice_id=w["invoice_id"], payment_id=cash_id,
                   include_invoice_payments=True, include_receipt=True)
        finally:
            sa_event.remove(db.engine, "rollback", _rec)
        assert len(rollbacks) <= 1


# ===========================================================================
# Receipt numbering
# ===========================================================================


def _other_confirmed_payment(app, w):
    with app.app_context():
        owner, actor = px.rows_of(app, w)
        plan = db.session.get(FeePlan, w["plan_id"])
        elsewhere = px.issued_invoice(fees.assignment(fees.enrollment(), plan, actor), actor)
        return px.payment(elsewhere, actor, amount="1").id


def test_a_competing_receipt_at_the_sequence_lock_yields_the_next_number(app, client, monkeypatch):
    w = px.login_world(app, client)
    rival = _other_confirmed_payment(app, w)
    year = fx.center_year(app, _MOMENT)
    token = px.cash_token(client, w)
    real = routes.lock_receipt_number_sequence

    def competing(calendar_year, moment):
        # Another Administrator confirmed a payment and committed first.
        db.session.add(ReceiptNumberSequence(calendar_year=calendar_year, last_number=1,
                                             created_at=moment, updated_at=moment))
        db.session.execute(text(
            "INSERT INTO receipts (public_id, payment_transaction_id, receipt_number, status,"
            " issued_at, issued_by_id, snapshot, version, created_at, updated_at) VALUES"
            " ('rival', :p, :n, 'issued', :m, :a, '{}', 1, :m, :m)"),
            {"p": rival, "n": f"RCT-{calendar_year}-000001", "m": _sql_moment(moment),
             "a": w["admin_id"]})
        db.session.commit()
        return real(calendar_year, moment)

    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    monkeypatch.setattr(routes, "lock_receipt_number_sequence", competing)
    response = px.record_cash(client, w, token=token)
    monkeypatch.undo()
    assert px.CASH_OK_TEXT in px.followed(client, response)
    with app.app_context():
        assert px.stored_receipts(w)[0].receipt_number == f"RCT-{year}-000002"
        assert [(r.calendar_year, r.last_number) for r in ReceiptNumberSequence.query] == [
            (year, 2)]


def test_a_number_already_taken_is_refused_without_recording_or_consuming(app, client, monkeypatch):
    w = px.login_world(app, client)
    rival = _other_confirmed_payment(app, w)
    year = fx.center_year(app, _MOMENT)
    with app.app_context():
        px.sequence(year, 0)
        db.session.execute(text(
            "INSERT INTO receipts (public_id, payment_transaction_id, receipt_number, status,"
            " issued_at, issued_by_id, snapshot, version, created_at, updated_at) VALUES"
            " ('rival', :p, :n, 'issued', :m, :a, '{}', 1, :m, :m)"),
            {"p": rival, "n": f"RCT-{year}-000001", "m": _sql_moment(px.RECORDED_AT),
             "a": w["admin_id"]})
        db.session.commit()
    token = px.cash_token(client, w)
    before = px.record(app)
    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    response = px.record_cash(client, w, token=token)
    monkeypatch.undo()
    assert px.INTEGRITY_TEXT in px.followed(client, response)
    assert px.record(app) == before


def test_first_receipts_racing_to_create_the_years_sequence_roll_back_safely(
    app, client, monkeypatch
):
    w = px.login_world(app, client)
    pp = px.pending_transfer(client, w, amount="9")
    year = fx.center_year(app, _MOMENT)
    token = px.confirm_token(client, w, pp)

    def racing(calendar_year, moment):
        db.session.add(ReceiptNumberSequence(calendar_year=calendar_year, last_number=0,
                                             created_at=moment, updated_at=moment))
        db.session.commit()
        db.session.add(ReceiptNumberSequence(calendar_year=calendar_year, last_number=0,
                                             created_at=moment, updated_at=moment))
        db.session.flush()

    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    monkeypatch.setattr(routes, "lock_receipt_number_sequence", racing)
    response = px.confirm(client, w, pp, token=token)
    monkeypatch.undo()
    html = px.followed(client, response)
    assert px.INTEGRITY_TEXT in html and not re.search(r"UNIQUE|constraint|sqlite", html, re.I)
    with app.app_context():
        assert px.stored_payment(pp).status == "pending"
        assert [(r.calendar_year, r.last_number) for r in ReceiptNumberSequence.query] == [
            (year, 0)]
        assert px.stored_receipts(w) == []


@pytest.mark.parametrize("action", ["cash", "confirm"])
def test_an_exhausted_year_refuses_without_changing_anything(app, client, monkeypatch, action):
    w = px.login_world(app, client)
    pp = px.pending_transfer(client, w, amount="9")
    with app.app_context():
        px.sequence(fx.center_year(app, _MOMENT), MAX_RECEIPT_SEQUENCE_NUMBER)
    token = px.cash_token(client, w) if action == "cash" else px.confirm_token(client, w, pp)
    before = px.record(app)
    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    response = (px.record_cash(client, w, token=token) if action == "cash"
                else px.confirm(client, w, pp, token=token))
    monkeypatch.undo()
    assert px.EXHAUSTED_TEXT in px.followed(client, response)
    assert px.record(app) == before


def test_the_receipt_year_is_the_center_local_year(app, client, monkeypatch):
    w = px.login_world(app, client)
    token = px.cash_token(client, w)
    moment = datetime(2026, 12, 31, 22, 30, 0)
    monkeypatch.setitem(app.config, "APP_TIMEZONE", "UTC+03:00")
    monkeypatch.setattr(routes, "_write_moment", lambda: moment)
    response = px.record_cash(client, w, token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    with app.app_context():
        (receipt,) = px.stored_receipts(w)
        assert (receipt.receipt_number, receipt.issued_at) == ("RCT-2027-000001", moment)
        assert [(r.calendar_year, r.last_number) for r in ReceiptNumberSequence.query] == [
            (2027, 1)]


def test_a_year_boundary_crossed_at_the_sequence_lock_is_stale(app, client, monkeypatch):
    w = px.login_world(app, client)
    token = px.cash_token(client, w)
    moments = iter([datetime(2026, 12, 31, 23, 59, 59), datetime(2027, 1, 1, 0, 0, 0)])
    before = px.record(app)
    monkeypatch.setitem(app.config, "APP_TIMEZONE", "UTC")
    monkeypatch.setattr(routes, "_write_moment", lambda: next(moments))
    response = px.record_cash(client, w, token=token)
    monkeypatch.undo()
    assert px.STALE_TEXT in px.followed(client, response)
    assert px.record(app) == before


def test_a_failed_write_consumes_no_number(app, client, monkeypatch):
    w = px.login_world(app, client)
    token = px.cash_token(client, w)
    monkeypatch.setattr(routes.db.session, "commit", _failing_commit)
    assert px.INTEGRITY_TEXT in px.followed(client, px.record_cash(client, w, token=token))
    monkeypatch.undo()
    rp = px.rp_from(px.record_cash(client, w))
    with app.app_context():
        (receipt,) = px.stored_receipts(w)
        assert receipt.public_id == rp and receipt.receipt_number.endswith("-000001")


# ===========================================================================
# Post-lock revalidation -- a competing change at the lock boundary
# ===========================================================================


def _inject_before_locks(monkeypatch, module, name, change):
    real_chain = getattr(module, name)

    def chain(*args, **kwargs):
        change()
        db.session.commit()
        return real_chain(*args, **kwargs)

    monkeypatch.setattr(module, name, chain)


def _spare(app):
    with app.app_context():
        actor = fx.admin("spare@example.com")
        spare_enrollment = fees.enrollment()
        spare_group = db.session.get(Group, spare_enrollment.group_id)
        spare_assignment = fees.assignment(spare_enrollment, fees.active_plan(actor, name="Spare"),
                                           actor)
        spare_invoice = px.issued_invoice(spare_assignment, actor)
        return {"course_id": spare_group.course_id, "enrollment_id": spare_enrollment.id,
                "assignment_id": spare_assignment.id, "invoice_id": spare_invoice.id}


def _change(kind, w, spare, target=None):
    invoice = Invoice.id == w["invoice_id"]
    if kind == "admin_suspended":
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif kind == "admin_demoted":
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            role=UserRole.TEACHER.value))
    elif kind == "student_suspended":
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif kind == "enrollment_withdrawn":
        db.session.execute(update(Enrollment).where(Enrollment.id == w["enrollment_id"]).values(
            status=fees.WITHDRAWN))
    elif kind == "group_archived":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            status=fees.ARCHIVED))
    elif kind == "group_retargeted":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            course_id=spare["course_id"]))
    elif kind == "assignment_moved":
        db.session.execute(update(StudentFeeAssignment).where(
            StudentFeeAssignment.id == w["assignment_id"]).values(
            enrollment_id=spare["enrollment_id"]))
    elif kind == "invoice_moved":
        db.session.execute(update(Invoice).where(invoice).values(
            student_fee_assignment_id=spare["assignment_id"]))
    elif kind == "invoice_version_moved":
        db.session.execute(update(Invoice).where(invoice).values(version=3))
    elif kind == "invoice_cancelled_quietly":
        db.session.execute(update(Invoice).where(invoice).values(
            status="cancelled", cancelled_at=fx.CANCELLED_AT, cancelled_by_id=w["admin_id"],
            updated_at=fx.CANCELLED_AT))
    elif kind == "lines_duplicated_quietly":
        db.session.execute(update(InvoiceItem).where(
            InvoiceItem.invoice_id == w["invoice_id"]).values(label="Same"))
    elif kind == "line_cut_quietly":
        db.session.execute(update(InvoiceItem).where(
            InvoiceItem.invoice_id == w["invoice_id"], InvoiceItem.label == "Course").values(
            amount=Decimal("10")))
    elif kind == "payment_recorded_meanwhile":
        db.session.add(PaymentTransaction(
            invoice_id=w["invoice_id"], kind="collection", method="bank_transfer",
            status="pending", amount="1", bank_transfer_reference="MEANWHILE",
            bank_transfer_date=px.TRANSFER_DATE, recorded_at=px.RECORDED_AT,
            recorded_by_id=w["admin_id"], version=1, created_at=px.RECORDED_AT,
            updated_at=px.RECORDED_AT))
    elif kind == "target_moved":
        db.session.execute(text("UPDATE payment_transactions SET invoice_id = :i "
                                "WHERE public_id = :p"), {"i": spare["invoice_id"], "p": target})
    elif kind == "target_decided_elsewhere":
        db.session.execute(text(
            "UPDATE payment_transactions SET status = 'rejected', rejected_at = recorded_at,"
            " rejected_by_id = :a, rejection_reason = 'Elsewhere', version = 2,"
            " updated_at = recorded_at WHERE public_id = :p"), {"a": w["admin_id"], "p": target})
    elif kind == "reversal_recorded_quietly":
        original = PaymentTransaction.query.filter_by(public_id=target).one()
        db.session.add(PaymentTransaction(
            invoice_id=w["invoice_id"], kind="reversal", method="cash", status="confirmed",
            amount=original.amount, recorded_at=px.REVERSED_AT, recorded_by_id=w["admin_id"],
            confirmed_at=px.REVERSED_AT, confirmed_by_id=w["admin_id"],
            reversal_of_payment_transaction_id=original.id, version=1,
            created_at=px.REVERSED_AT, updated_at=px.REVERSED_AT))
    else:  # pragma: no cover
        raise AssertionError(kind)


_OK = "ok"

_CASH_RACES = {
    "admin_suspended": 404,
    "admin_demoted": 404,
    "invoice_moved": 404,
    "assignment_moved": 404,
    "group_retargeted": px.STALE_TEXT,
    "invoice_version_moved": px.STALE_TEXT,
    "payment_recorded_meanwhile": px.STALE_TEXT,
    "invoice_cancelled_quietly": px.NOT_ISSUED_TEXT,
    "lines_duplicated_quietly": px.ITEMS_INVALID_TEXT,
    "line_cut_quietly": px.OVERPAYMENT_TEXT,
    "group_archived": _OK,
    "student_suspended": _OK,
}


@pytest.mark.parametrize("kind", sorted(_CASH_RACES))
def test_a_cash_payment_re_proves_every_rule_against_the_locked_rows(
    app, client, monkeypatch, kind
):
    w = px.login_world(app, client)
    spare = _spare(app)
    token = px.cash_token(client, w)
    _inject_before_locks(monkeypatch, routes, "lock_payment_chain",
                         lambda: _change(kind, w, spare))
    response = px.record_cash(client, w, amount="100", token=token)
    monkeypatch.undo()
    expected = _CASH_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert (px.CASH_OK_TEXT if expected == _OK else expected) in px.followed(client, response)
    with app.app_context():
        cash = PaymentTransaction.query.filter_by(method="cash").count()
        assert cash == (1 if expected == _OK else 0)
        assert PaymentAuditEvent.query.count() == (2 if expected == _OK else 0)
        assert ReceiptNumberSequence.query.count() == (1 if expected == _OK else 0)


_CONFIRM_RACES = {
    "admin_suspended": 404,
    "target_moved": 404,
    "invoice_moved": 404,
    "target_decided_elsewhere": px.STALE_TEXT,
    "payment_recorded_meanwhile": px.STALE_TEXT,
    "group_retargeted": px.STALE_TEXT,
    "line_cut_quietly": px.OVERPAYMENT_TEXT,
    "invoice_cancelled_quietly": px.NOT_ISSUED_TEXT,
    "group_archived": _OK,
    "enrollment_withdrawn": _OK,
}


@pytest.mark.parametrize("kind", sorted(_CONFIRM_RACES))
def test_a_confirmation_re_proves_every_rule_against_the_locked_rows(
    app, client, monkeypatch, kind
):
    w = px.login_world(app, client)
    spare = _spare(app)
    pp = px.pending_transfer(client, w, amount="100")
    token = px.confirm_token(client, w, pp)
    _inject_before_locks(monkeypatch, routes, "lock_payment_chain",
                         lambda: _change(kind, w, spare, pp))
    response = px.confirm(client, w, pp, token=token)
    monkeypatch.undo()
    expected = _CONFIRM_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert (px.BANK_CONFIRMED_OK_TEXT if expected == _OK else expected) in px.followed(
            client, response)
    with app.app_context():
        confirmed = PaymentTransaction.query.filter_by(public_id=pp, status="confirmed").count()
        assert confirmed == (1 if expected == _OK else 0)
        assert (px.stored_receipts(dict(w)) != []) == (expected == _OK)


_REJECT_RACES = {
    "admin_suspended": 404,
    "target_moved": 404,
    "target_decided_elsewhere": px.STALE_TEXT,
    "payment_recorded_meanwhile": px.STALE_TEXT,
    "invoice_cancelled_quietly": px.NOT_ISSUED_TEXT,
    "student_suspended": _OK,
    "group_archived": _OK,
}


@pytest.mark.parametrize("kind", sorted(_REJECT_RACES))
def test_a_rejection_re_proves_its_rules_against_the_locked_rows(app, client, monkeypatch, kind):
    w = px.login_world(app, client)
    spare = _spare(app)
    pp = px.pending_transfer(client, w, amount="10")
    token = px.reject_token(client, w, pp)
    _inject_before_locks(monkeypatch, routes, "lock_payment_chain",
                         lambda: _change(kind, w, spare, pp))
    response = px.reject(client, w, pp, token=token)
    monkeypatch.undo()
    expected = _REJECT_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert (px.BANK_REJECTED_OK_TEXT if expected == _OK else expected) in px.followed(
            client, response)
    with app.app_context():
        mine = PaymentTransaction.query.filter_by(public_id=pp).one()
        assert (mine.rejection_reason == "Not received") == (expected == _OK)


_REVERSE_RACES = {
    "admin_demoted": 404,
    "target_moved": 404,
    "invoice_moved": 404,
    "invoice_version_moved": px.STALE_TEXT,
    "reversal_recorded_quietly": px.ALREADY_REVERSED_TEXT,
    "invoice_cancelled_quietly": px.NOT_ISSUED_TEXT,
    "enrollment_withdrawn": _OK,
    "group_archived": _OK,
}


@pytest.mark.parametrize("kind", sorted(_REVERSE_RACES))
def test_a_reversal_re_proves_every_rule_against_the_locked_rows(app, client, monkeypatch, kind):
    w = px.login_world(app, client)
    spare = _spare(app)
    px.record_cash(client, w, amount="100")
    with app.app_context():
        pp = px.stored_payments(w)[0].public_id
    token = px.reverse_token(client, w, pp)
    _inject_before_locks(monkeypatch, routes, "lock_payment_chain",
                         lambda: _change(kind, w, spare, pp))
    response = px.reverse(client, w, pp, token=token)
    monkeypatch.undo()
    expected = _REVERSE_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert (px.REVERSED_OK_TEXT if expected == _OK else expected) in px.followed(
            client, response)
    with app.app_context():
        voided = [r for r in px.stored_receipts(w) if r.status == "voided"]
        assert bool(voided) == (expected == _OK)
        assert PaymentAuditEvent.query.filter_by(kind="payment_reversed").count() == (
            1 if expected == _OK else 0)


@pytest.mark.parametrize("action", ["add_line", "cancel"])
def test_a_payment_recorded_at_the_invoice_lock_freezes_a_waiting_invoice_change(
    app, client, monkeypatch, action
):
    w = px.login_world(app, client)
    spare = _spare(app)
    ip = w["ip"]
    if action == "add_line":
        token = fx.add_line_token(client, w, ip)
        post = lambda: fx.add_line(client, w, ip, token=token, label="Books", amount="5",  # noqa: E731
                                   reason="Books were missing")
    else:
        token = fx.cancel_token(client, w, ip)
        post = lambda: fx.cancel(client, w, ip, token=token)  # noqa: E731
    _inject_before_locks(monkeypatch, invoice_routes, "lock_invoice_chain",
                         lambda: _change("payment_recorded_meanwhile", w, spare))
    response = post()
    monkeypatch.undo()
    assert response.status_code == 302
    if action == "add_line":
        # Phase 5 / M10: a payment no longer freezes the lines. The waiting
        # change re-proves the payment floor against the transfer it now sees,
        # and its higher total clears it.
        assert fx.LINE_ADDED_TEXT in px.followed(client, response)
        with app.app_context():
            owner = fx.stored_invoice(ip)
            assert (owner.status, owner.version) == ("issued", 3)
            assert len(fx.stored_lines(ip)) == 3
            assert [e.kind for e in PaymentAuditEvent.query] == ["invoice_issued_edited"]
        return
    assert px.FROZEN_TEXT in px.followed(client, response)
    with app.app_context():
        owner = fx.stored_invoice(ip)
        assert (owner.status, owner.version) == ("issued", 2)
        assert len(fx.stored_lines(ip)) == 2
        assert PaymentAuditEvent.query.count() == 0


# ===========================================================================
# IntegrityError and audit refusals
# ===========================================================================


def _failing_commit():
    raise IntegrityError("INSERT INTO receipts ...", {}, Exception("Duplicate entry 'x' for key"))


_ACTIONS = ["cash", "bank", "confirm", "reject", "reverse"]


def _action(action, app, client, w):
    """``(token, poster)`` for one mutation, its preconditions written first."""
    if action == "cash":
        return px.cash_token(client, w), lambda token: px.record_cash(client, w, token=token)
    if action == "bank":
        return px.bank_token(client, w), lambda token: px.record_bank(client, w, token=token)
    if action in ("confirm", "reject"):
        pp = px.pending_transfer(client, w, amount="9")
        if action == "confirm":
            return (px.confirm_token(client, w, pp),
                    lambda token: px.confirm(client, w, pp, token=token))
        return px.reject_token(client, w, pp), lambda token: px.reject(client, w, pp, token=token)
    px.record_cash(client, w, amount="9")
    with app.app_context():
        pp = px.stored_payments(w)[0].public_id
    return px.reverse_token(client, w, pp), lambda token: px.reverse(client, w, pp, token=token)


@pytest.mark.parametrize("action", _ACTIONS)
def test_an_integrity_error_is_rolled_back_and_reported_generically(
    app, client, monkeypatch, action
):
    w = px.login_world(app, client)
    token, post = _action(action, app, client, w)
    assert token
    before = px.record(app)
    monkeypatch.setattr(routes.db.session, "commit", _failing_commit)
    response = post(token)
    monkeypatch.undo()
    assert response.status_code == 302
    html = px.followed(client, response)
    assert px.INTEGRITY_TEXT in html
    for leaked in ("INSERT", "Duplicate entry", "IntegrityError", "sqlite", "pymysql"):
        assert leaked not in html, leaked
    assert px.record(app) == before


@pytest.mark.parametrize("action", _ACTIONS)
def test_a_refused_audit_event_rolls_back_the_whole_change(app, client, monkeypatch, action):
    w = px.login_world(app, client)
    token, post = _action(action, app, client, w)
    before = px.record(app)

    def refuse(**kwargs):
        raise ValueError("refused")

    monkeypatch.setattr(routes, "record_payment_event", refuse)
    response = post(token)
    monkeypatch.undo()
    assert px.INTEGRITY_TEXT in px.followed(client, response)
    assert px.record(app) == before


def test_recovery_re_authorizes_from_current_state(app, client, monkeypatch):
    w = px.login_world(app, client)
    token = px.cash_token(client, w)
    real_commit = db.session.commit

    def commit_that_loses_access():
        db.session.rollback()
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        real_commit()
        _failing_commit()

    monkeypatch.setattr(routes.db.session, "commit", commit_that_loses_access)
    response = px.record_cash(client, w, token=token)
    monkeypatch.undo()
    assert response.status_code == 404
    with app.app_context():
        assert PaymentTransaction.query.count() == 0


@pytest.mark.parametrize("action", ["cash", "reverse"])
def test_a_real_audit_constraint_failure_rolls_back_the_whole_change(
    app, client, monkeypatch, action
):
    """The version-transition CHECK itself refuses the event -- the final
    defense, exercised for real -- and every payment, receipt and sequence
    write of the same transaction goes with it."""
    w = px.login_world(app, client)
    token, post = _action(action, app, client, w)
    before = px.record(app)
    real = audit.PaymentAuditEvent

    def corrupted(**kwargs):
        kwargs["invoice_version_after"] = kwargs["invoice_version_after"] + 1
        return real(**kwargs)

    monkeypatch.setattr(audit, "PaymentAuditEvent", corrupted)
    response = post(token)
    monkeypatch.undo()
    html = px.followed(client, response)
    assert px.INTEGRITY_TEXT in html
    assert not re.search(r"CHECK|constraint|sqlite", html, re.I)
    assert px.record(app) == before
