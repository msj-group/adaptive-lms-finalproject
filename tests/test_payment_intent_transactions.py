"""Phase 5 / M06 -- tokens, the checkout context, the derived idempotency key,
the lock chains, post-lock revalidation, provider timing and
``IntegrityError`` recovery for payment intents.

SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so the lock tests
assert what the code *requests* -- which rows, in which order -- and the race
tests inject a competing change at the exact transaction boundary (between the
pre-lock reads and the lock chain) to prove every write re-decides against the
locked rows. None of this proves real InnoDB blocking.
"""

import re
import time
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

import app.blueprints.admin.payment_intents as routes
import tests.fee_assignment_fixtures as fees
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
from app.extensions import db
from app.models import (
    FeePlan,
    Group,
    Invoice,
    InvoiceItem,
    PaymentIntent,
    PaymentTransaction,
    User,
    UserRole,
    UserStatus,
)
from app.services import invoice_tokens, payment_tokens
from app.services import payment_intent_tokens as tokens
from app.services.mock_payment_provider import mock_reference
from app.services.payment_intent_transactions import lock_payment_intent_chain
from app.services.payment_providers import (
    PaymentProviderError,
    PaymentProviderSettings,
    ProviderPayment,
)

_PREFIX = ["academic_terms", "levels", "courses", "groups", "users", "enrollments", "users",
           "student_fee_assignments", "invoices"]

CREATE = tokens.PURPOSE_CREATE
CANCEL = tokens.PURPOSE_CANCEL
CHECKOUT = tokens.PURPOSE_CHECKOUT


@pytest.fixture
def app():
    application = ix.make_app()
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


class _Intent:
    """A stand-in carrying exactly what a checkout context binds."""

    def __init__(self, status="pending", version=1):
        self.public_id = "x" * 36
        self.version = version
        self.status = status
        self.provider_reference = "mock_pi_" + "a" * 32


# ===========================================================================
# Tokens and the checkout context
# ===========================================================================


def _payloads():
    actor, invoice, intent = "a" * 36, "i" * 36, "x" * 36
    payments = [["p" * 36, "rejected", 2]]
    intents = [["y" * 36, "cancelled", 2], [intent, "pending", 1]]
    return {
        CREATE: {"actor_public_id": actor, "invoice_public_id": invoice, "invoice_version": 2,
                 "payment_state": payments, "intent_state": intents, "provider_mode": "mock"},
        CANCEL: {"actor_public_id": actor, "invoice_public_id": invoice, "invoice_version": 2,
                 "intent_public_id": intent, "intent_version": 1, "intent_status": "pending",
                 "intent_state": intents, "provider_mode": "mock"},
    }


def _all_tokens():
    payloads = _payloads()
    minted = {purpose: tokens.make_token(purpose, **payload)
              for purpose, payload in payloads.items()}
    minted[CHECKOUT] = tokens.make_checkout_context(_Intent())
    return minted


def test_the_three_purposes_cannot_be_replayed_as_one_another(app):
    with app.app_context():
        minted = _all_tokens()
        assert set(minted) == set(tokens.PURPOSES)
        payloads = _payloads()
        for purpose, token in minted.items():
            loaded = tokens.load_token(token, purpose)
            assert loaded is not None, purpose
            if purpose in payloads:
                assert loaded == dict(payloads[purpose], purpose=purpose)
            for other in tokens.PURPOSES:
                if other != purpose:
                    assert tokens.load_token(token, other) is None, (purpose, other)


def test_a_token_from_another_milestone_is_not_an_intent_token(app):
    with app.app_context():
        foreign = [
            payment_tokens.make_token(payment_tokens.PURPOSE_CASH_RECORD,
                                      actor_public_id="a" * 36, invoice_public_id="i" * 36,
                                      invoice_version=2, payment_state=[]),
            invoice_tokens.make_token(invoice_tokens.PURPOSE_CANCEL, actor_public_id="a" * 36,
                                      invoice_public_id="i" * 36, invoice_version=2),
        ]
        for token in foreign:
            for purpose in tokens.PURPOSES:
                assert tokens.load_token(token, purpose) is None


def test_tampered_malformed_and_wrongly_shaped_tokens_are_refused(app):
    with app.app_context():
        payload = _payloads()[CANCEL]
        token = tokens.make_token(CANCEL, **payload)
        middle = len(token) // 3
        tampered = token[:middle] + ("x" if token[middle] != "x" else "y") + token[middle + 1:]
        serializer = tokens._serializer(CANCEL)
        body = dict(payload, purpose=CANCEL)
        for bad in (
            None, "", 42, tampered, token + "x", "x" * 13000, "not.a.token",
            serializer.dumps(dict(body, extra="1")),
            serializer.dumps({"purpose": CANCEL}),
            serializer.dumps(dict(body, purpose=CREATE)),
            serializer.dumps(dict(body, intent_version=True)),
            serializer.dumps(dict(body, intent_version=0)),
            serializer.dumps(dict(body, intent_version="1")),
            serializer.dumps(dict(body, intent_version=2**31)),
            serializer.dumps(dict(body, intent_public_id="")),
            serializer.dumps(dict(body, intent_public_id="has space")),
            serializer.dumps(dict(body, actor_public_id="a" * 65)),
            serializer.dumps(dict(body, intent_status="paid")),
            serializer.dumps(dict(body, provider_mode="stripe")),
            serializer.dumps(dict(body, provider_mode=None)),
            serializer.dumps(dict(body, intent_state="x")),
            serializer.dumps(dict(body, intent_state=[["x", "pending"]])),
            serializer.dumps(dict(body, intent_state=[["x", "paid", 1]])),
            serializer.dumps(dict(body, intent_state=[["x", "pending", 1], ["x", "pending", 1]])),
            serializer.dumps(dict(body, intent_state=[["x" * 36, "pending", 1]] * 26)),
        ):
            assert tokens.load_token(bad, CANCEL) is None, bad
        create = dict(_payloads()[CREATE], purpose=CREATE)
        create_serializer = tokens._serializer(CREATE)
        for bad_state in ("x", [["p", "deleted", 1]], [["p", "pending", 0]],
                          [[str(n), "pending", 1] for n in range(51)]):
            assert tokens.load_token(
                create_serializer.dumps(dict(create, payment_state=bad_state)), CREATE) is None
        checkout = tokens.load_token(tokens.make_checkout_context(_Intent()), CHECKOUT)
        checkout_serializer = tokens._serializer(CHECKOUT)
        now = int(time.time())
        for bad in (dict(checkout, expires_at=now - 1),
                    dict(checkout, expires_at=now + tokens.CHECKOUT_CONTEXT_MAX_AGE_SECONDS + 60),
                    dict(checkout, expires_at=str(now + 60)),
                    dict(checkout, expires_at=True),
                    dict(checkout, provider_reference="has space"),
                    dict(checkout, intent_status="paid"),
                    {k: v for k, v in checkout.items() if k != "expires_at"},
                    dict(checkout, amount="1250.500")):
            assert tokens.load_token(checkout_serializer.dumps(bad), CHECKOUT) is None, bad


def test_expired_tokens_and_contexts_are_refused(app, monkeypatch):
    with app.app_context():
        minted = _all_tokens()
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
        assert tokens.load_token(minted[CREATE], CREATE) is None
        assert tokens.load_token(minted[CANCEL], CANCEL) is None
        assert tokens.load_token(minted[CHECKOUT], CHECKOUT) is not None
        monkeypatch.setattr(tokens, "CHECKOUT_CONTEXT_MAX_AGE_SECONDS", -1)
        assert tokens.load_token(minted[CHECKOUT], CHECKOUT) is None


def test_the_checkout_context_is_short_lived_and_stale_once_the_intent_moves(app):
    assert tokens.CHECKOUT_CONTEXT_MAX_AGE_SECONDS == 30 * 60 < tokens.TOKEN_MAX_AGE_SECONDS
    with app.app_context():
        context = tokens.make_checkout_context(_Intent())
        current = {"intent_public_id": "x" * 36, "intent_version": 1, "intent_status": "pending",
                   "provider_reference": "mock_pi_" + "a" * 32}
        assert not tokens.token_is_stale(context, CHECKOUT, **current)
        for field, value in (("intent_version", 2), ("intent_status", "provider_succeeded"),
                             ("intent_public_id", "z" * 36),
                             ("provider_reference", "mock_pi_" + "b" * 32)):
            assert tokens.token_is_stale(context, CHECKOUT, **dict(current, **{field: value}))


def test_a_token_for_the_largest_state_stays_within_its_bound(app):
    with app.app_context():
        payments = [[str(uuid.uuid4()), "confirmed", 2**31 - 1] for _ in range(50)]
        intents = [[str(uuid.uuid4()), "provider_failed", 2**31 - 1] for _ in range(25)]
        token = tokens.make_token(CREATE, actor_public_id=str(uuid.uuid4()),
                                  invoice_public_id=str(uuid.uuid4()), invoice_version=2**31 - 1,
                                  payment_state=payments, intent_state=intents,
                                  provider_mode="mock")
        assert len(token) <= tokens._MAX_TOKEN_LENGTH
        loaded = tokens.load_token(token, CREATE)
        assert loaded["payment_state"] == payments and loaded["intent_state"] == intents


# ===========================================================================
# The derived idempotency key
# ===========================================================================


def test_the_idempotency_key_is_derived_deterministically_from_the_verified_request(app):
    with app.app_context():
        payload = dict(_payloads()[CREATE], purpose=CREATE)
        key = tokens.idempotency_key_for(payload)
        assert re.fullmatch(r"[0-9a-f]{64}", key)
        # Minted at another second, the same logical request has the same key.
        first = tokens.load_token(tokens.make_token(CREATE, **_payloads()[CREATE]), CREATE)
        time.sleep(1.05)
        second = tokens.load_token(tokens.make_token(CREATE, **_payloads()[CREATE]), CREATE)
        assert tokens.idempotency_key_for(first) == tokens.idempotency_key_for(second) == key
        for field, value in (("actor_public_id", "b" * 36), ("invoice_public_id", "j" * 36),
                             ("invoice_version", 3), ("payment_state", []),
                             ("intent_state", []), ("provider_mode", "disabled")):
            assert tokens.idempotency_key_for(dict(payload, **{field: value})) != key, field
        for fragment in ("a" * 16, "i" * 16, "mock", "pending"):
            assert fragment not in key


def test_the_idempotency_key_depends_on_the_application_secret(app, monkeypatch):
    with app.app_context():
        payload = dict(_payloads()[CREATE], purpose=CREATE)
        key = tokens.idempotency_key_for(payload)
        monkeypatch.setitem(app.config, "SECRET_KEY", "another-secret")
        assert tokens.idempotency_key_for(payload) != key


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
    """``(table, id)`` for every by-id row read issued **inside** the lock
    chain, in execution order."""

    def __init__(self, monkeypatch):
        self.reads, self._on = [], False
        real = routes.lock_payment_intent_chain

        def wrapper(*args, **kwargs):
            self._on = True
            try:
                return real(*args, **kwargs)
            finally:
                self._on = False

        monkeypatch.setattr(routes, "lock_payment_intent_chain", wrapper)

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


def _lock_world(app, client, pending=False):
    """An issued invoice holding a rejected transfer and two terminal intents
    (plus, optionally, a pending one), and another invoice's rows that must
    never be locked."""
    w = ix.login_world(app, client)
    with app.app_context():
        owner, actor = ix.invoice_and_admin(w)
        rejected = px.payment(owner, actor, method="bank_transfer", status="rejected", amount="3")
        failed = ix.intent(owner, actor, status="provider_failed")
        cancelled = ix.intent(owner, actor, status="cancelled")
        plan = db.session.get(FeePlan, w["plan_id"])
        elsewhere = px.issued_invoice(fees.assignment(fees.enrollment(), plan, actor), actor)
        px.payment(elsewhere, actor, amount="6")
        ix.intent(elsewhere, actor, status="cancelled")
        target = ix.registered_intent(app, owner, actor) if pending else None
        return w, {
            "payments": [rejected.id],
            "intents": [failed.id, cancelled.id],
            "target": None if target is None else (target.id, target.public_id),
        }


def test_creation_locks_the_invoices_payments_then_its_intents(app, client, monkeypatch):
    w, rows = _lock_world(app, client)
    token = ix.create_token(client, w)
    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch) as chain:
        response = ix.create(client, w, token=token)
    locked = requested[:]
    monkeypatch.undo()
    assert ix.CREATED_OK_TEXT in ix.followed(client, response)
    assert locked == _PREFIX + ["payment_transactions"] + ["payment_intents"] * 2
    assert chain.ids("invoices") == [w["invoice_id"]]
    assert chain.ids("payment_transactions") == rows["payments"]
    assert chain.ids("payment_intents") == rows["intents"]


@pytest.mark.parametrize("action", ["cancel", "return"])
def test_cancellation_and_the_return_lock_the_target_first_then_the_rest(
    app, client, monkeypatch, action
):
    w, rows = _lock_world(app, client, pending=True)
    target_id, xp = rows["target"]
    if action == "cancel":
        token = ix.cancel_token(client, w, xp)
    else:
        ix.simulate(client, w, xp, "success")
        token = ix.checkout_context(client, w, xp)
    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch) as chain:
        if action == "cancel":
            response = ix.cancel(client, w, xp, token=token)
        else:
            response = ix.return_to_lms(client, w, xp, context=token)
    locked = requested[:]
    monkeypatch.undo()
    assert response.status_code == 302
    with app.app_context():
        assert ix.stored_intent(xp).status in ("cancelled", "provider_succeeded")
    assert locked == (_PREFIX + ["payment_intents"] + ["payment_transactions"]
                      + ["payment_intents"] * 2)
    assert chain.ids("payment_intents") == [target_id] + rows["intents"]
    assert chain.ids("payment_transactions") == rows["payments"]


def test_checkout_simulation_takes_no_lock_and_writes_nothing(app, client, monkeypatch):
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    context = ix.checkout_context(client, w, xp)
    requested = _record_lock_requests(monkeypatch)
    before = ix.record(app)
    ix.simulate(client, w, xp, "success", context=context)
    locked = requested[:]
    monkeypatch.undo()
    assert locked == [] and ix.record(app) == before


def test_the_chain_stops_at_the_invoice_when_it_is_gone(app, monkeypatch):
    with app.app_context():
        w = ix.world(app)
        context_row = (db.session.query(Group.academic_term_id, Group.course_id)
                       .filter(Group.id == w["group_id"]).one())
        requested = _record_lock_requests(monkeypatch)
        locks = lock_payment_intent_chain(
            w["gp"], context_row.academic_term_id, w["level_id"], context_row.course_id,
            w["student_id"], w["enrollment_id"], w["admin_id"], w["assignment_id"], 999999,
            intent_id=1)
        monkeypatch.undo()
        assert locks.chain.invoice is None and locks.intent is None
        assert locks.payments == {} and locks.intents == {}
        assert requested == _PREFIX


def test_the_provider_is_called_only_after_every_lock(app, client, monkeypatch):
    w, _rows = _lock_world(app, client)
    token = ix.create_token(client, w)
    requested = _record_lock_requests(monkeypatch)
    provider = ix.provider_of(app)
    real_create = provider.create_payment_intent
    seen = {}

    def create(**kwargs):
        seen["locks"] = requested[:]
        return real_create(**kwargs)

    monkeypatch.setattr(provider, "create_payment_intent", create)
    ix.create(client, w, token=token)
    monkeypatch.undo()
    assert seen["locks"] == _PREFIX + ["payment_transactions"] + ["payment_intents"] * 2


# ===========================================================================
# Post-lock revalidation -- a competing change at the lock boundary
# ===========================================================================


def _inject_before_locks(monkeypatch, change):
    real_chain = routes.lock_payment_intent_chain

    def chain(*args, **kwargs):
        change()
        db.session.commit()
        return real_chain(*args, **kwargs)

    monkeypatch.setattr(routes, "lock_payment_intent_chain", chain)


def _spare(app):
    with app.app_context():
        actor = fees.admin("spare@example.com")
        spare_enrollment = fees.enrollment()
        spare_group = db.session.get(Group, spare_enrollment.group_id)
        spare_assignment = fees.assignment(spare_enrollment,
                                           fees.active_plan(actor, name="Spare"), actor)
        spare_invoice = px.issued_invoice(spare_assignment, actor)
        return {"course_id": spare_group.course_id, "invoice_id": spare_invoice.id,
                "assignment_id": spare_assignment.id}


def _set_mode(app, mode):
    settings = app.extensions["payment_provider"]
    app.extensions["payment_provider"] = PaymentProviderSettings(
        mode=mode, environment=settings.environment, provider=settings.provider)


def _change(app, kind, w, spare, xp=None):
    invoice = Invoice.id == w["invoice_id"]
    if kind == "admin_suspended":
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif kind == "admin_demoted":
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            role=UserRole.TEACHER.value))
    elif kind == "group_retargeted":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            course_id=spare["course_id"]))
    elif kind == "invoice_moved":
        db.session.execute(update(Invoice).where(invoice).values(
            student_fee_assignment_id=spare["assignment_id"]))
    elif kind == "invoice_version_moved":
        db.session.execute(update(Invoice).where(invoice).values(version=3))
    elif kind == "invoice_cancelled_quietly":
        db.session.execute(update(Invoice).where(invoice).values(
            status="cancelled", cancelled_at=datetime(2026, 6, 3, 9), cancelled_by_id=w["admin_id"],
            updated_at=datetime(2026, 6, 3, 9)))
    elif kind == "line_raised_quietly":
        db.session.execute(update(InvoiceItem).where(
            InvoiceItem.invoice_id == w["invoice_id"], InvoiceItem.label == "Course").values(
            amount=Decimal("99999.999")))
    elif kind == "payment_recorded_meanwhile":
        owner, actor = ix.invoice_and_admin(w)
        px.payment(owner, actor, method="bank_transfer", status="pending", amount="1")
    elif kind == "intent_created_meanwhile":
        owner, actor = ix.invoice_and_admin(w)
        ix.intent(owner, actor)
    elif kind == "mode_disabled":
        _set_mode(app, "disabled")
    elif kind == "intent_decided_elsewhere":
        row = PaymentIntent.query.filter_by(public_id=xp).one()
        row.version = 2
        row.status = "provider_succeeded"
        row.provider_result_at = row.created_at
        row.provider_result_by_id = w["admin_id"]
        row.updated_at = row.created_at
    elif kind == "intent_cancelled_elsewhere":
        row = PaymentIntent.query.filter_by(public_id=xp).one()
        row.version = 2
        row.status = "cancelled"
        row.terminal_at = row.created_at
        row.cancelled_by_id = w["admin_id"]
        row.updated_at = row.created_at
    else:  # pragma: no cover
        raise AssertionError(kind)


_CREATE_RACES = {
    "admin_suspended": 404,
    "admin_demoted": 404,
    "invoice_moved": 404,
    "group_retargeted": ix.STALE_TEXT,
    "invoice_version_moved": ix.STALE_TEXT,
    "invoice_cancelled_quietly": ix.NOT_ISSUED_TEXT,
    "line_raised_quietly": ix.ABOVE_MAXIMUM_TEXT,
    "payment_recorded_meanwhile": ix.STALE_TEXT,
    "intent_created_meanwhile": ix.STALE_TEXT,
    "mode_disabled": ix.MOCK_DISABLED_TEXT,
}


@pytest.mark.parametrize("kind", sorted(_CREATE_RACES))
def test_a_creation_re_proves_every_rule_against_the_locked_rows(app, client, monkeypatch, kind):
    w = ix.login_world(app, client)
    spare = _spare(app)
    token = ix.create_token(client, w)
    _inject_before_locks(monkeypatch, lambda: _change(app, kind, w, spare))
    response = ix.create(client, w, token=token)
    monkeypatch.undo()
    expected = _CREATE_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert expected in ix.followed(client, response)
    with app.app_context():
        assert [row.status for row in ix.stored_intents(w)] == (
            ["pending"] if kind == "intent_created_meanwhile" else [])
    assert ix.provider_of(app)._ledger == {}


def test_an_identical_request_completed_at_the_lock_boundary_is_resolved_not_repeated(
    app, client, monkeypatch
):
    w = ix.login_world(app, client)
    token = ix.create_token(client, w)

    def the_same_request_finished_first():
        key = tokens.idempotency_key_for(tokens.load_token(token, CREATE))
        owner, actor = ix.invoice_and_admin(w)
        ix.intent(owner, actor, key=key)

    _inject_before_locks(monkeypatch, the_same_request_finished_first)
    response = ix.create(client, w, token=token)
    monkeypatch.undo()
    assert ix.ALREADY_CREATED_TEXT in ix.followed(client, response)
    with app.app_context():
        (row,) = ix.stored_intents(w)
        assert response.headers["Location"].endswith(row.public_id)
    assert ix.provider_of(app)._ledger == {}


_CANCEL_RACES = {
    "admin_suspended": 404,
    "admin_demoted": 404,
    "invoice_moved": 404,
    "group_retargeted": ix.STALE_TEXT,
    "invoice_version_moved": ix.STALE_TEXT,
    "intent_decided_elsewhere": ix.STALE_TEXT,
    "mode_disabled": ix.MOCK_DISABLED_TEXT,
}


@pytest.mark.parametrize("kind", sorted(_CANCEL_RACES))
def test_a_cancellation_re_proves_every_rule_against_the_locked_rows(
    app, client, monkeypatch, kind
):
    w = ix.login_world(app, client)
    spare = _spare(app)
    xp = ix.create_intent(client, w)
    token = ix.cancel_token(client, w, xp)
    _inject_before_locks(monkeypatch, lambda: _change(app, kind, w, spare, xp))
    response = ix.cancel(client, w, xp, token=token)
    monkeypatch.undo()
    expected = _CANCEL_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert expected in ix.followed(client, response)
    with app.app_context():
        assert ix.stored_intent(xp).status != "cancelled"
    assert ix.provider_of(app).get_payment_status(mock_reference(
        _key_of(app, xp))).status == "pending"


def _key_of(app, xp):
    with app.app_context():
        return ix.stored_intent(xp).idempotency_key


_RETURN_RACES = {
    "admin_suspended": 404,
    "invoice_moved": 404,
    "group_retargeted": ix.STALE_TEXT,
    "intent_cancelled_elsewhere": ix.CHECKOUT_INVALID_TEXT,
    "mode_disabled": ix.MOCK_DISABLED_TEXT,
    "invoice_cancelled_quietly": ix.NOT_ISSUED_TEXT,
}


@pytest.mark.parametrize("kind", sorted(_RETURN_RACES))
def test_the_return_re_proves_every_rule_against_the_locked_rows(app, client, monkeypatch, kind):
    w = ix.login_world(app, client)
    spare = _spare(app)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    context = ix.checkout_context(client, w, xp)
    _inject_before_locks(monkeypatch, lambda: _change(app, kind, w, spare, xp))
    response = ix.return_to_lms(client, w, xp, context=context)
    monkeypatch.undo()
    expected = _RETURN_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert expected in ix.followed(client, response)
    with app.app_context():
        assert ix.stored_intent(xp).status != "provider_succeeded"


def test_a_second_active_intent_found_under_the_lock_blocks_the_return(app, client, monkeypatch):
    """The database cannot refuse two active intents; the return re-proves the
    one-active-intent rule under the invoice lock before recording anything."""
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    context = ix.checkout_context(client, w, xp)

    def a_second_active_intent():
        owner, actor = ix.invoice_and_admin(w)
        ix.intent(owner, actor)

    _inject_before_locks(monkeypatch, a_second_active_intent)
    response = ix.return_to_lms(client, w, xp, context=context)
    monkeypatch.undo()
    assert ix.INTEGRITY_TEXT in ix.followed(client, response)
    with app.app_context():
        assert ix.stored_intent(xp).status == "pending"


# ===========================================================================
# The provider's answer, and IntegrityError recovery
# ===========================================================================


@pytest.mark.parametrize("answer", [
    lambda **kw: ProviderPayment("mock_pi_" + "0" * 32, "succeeded", kw["amount"], "LYD"),
    lambda **kw: ProviderPayment("mock_pi_" + "0" * 32, "pending", Decimal("1"), "LYD"),
    lambda **kw: ProviderPayment("mock_pi_" + "0" * 32, "pending", kw["amount"], "USD"),
    lambda **kw: ProviderPayment("has space", "pending", kw["amount"], "LYD"),
    lambda **kw: ProviderPayment("x" * 65, "pending", kw["amount"], "LYD"),
    None,
], ids=["status", "amount", "currency", "reference", "long-reference", "error"])
def test_a_creation_the_provider_refuses_or_misreports_records_nothing(
    app, client, monkeypatch, answer
):
    w = ix.login_world(app, client)

    def create(**kwargs):
        if answer is None:
            raise PaymentProviderError("sandbox unavailable")
        return answer(**kwargs)

    monkeypatch.setattr(ix.provider_of(app), "create_payment_intent", create)
    before = ix.record(app)
    response = ix.create(client, w)
    monkeypatch.undo()
    assert ix.PROVIDER_REFUSED_TEXT in ix.followed(client, response)
    assert ix.record(app) == before


def test_a_reference_already_held_is_refused_generically(app, client, monkeypatch):
    w = ix.login_world(app, client)
    other = _spare(app)
    token = ix.create_token(client, w)

    def a_row_already_holds_the_reference():
        key = tokens.idempotency_key_for(tokens.load_token(token, CREATE))
        owner = db.session.get(Invoice, other["invoice_id"])
        ix.intent(owner, db.session.get(User, w["admin_id"]), key=ix.key_for("other"),
                  reference=mock_reference(key))

    _inject_before_locks(monkeypatch, a_row_already_holds_the_reference)
    response = ix.create(client, w, token=token)
    monkeypatch.undo()
    assert ix.INTEGRITY_TEXT in ix.followed(client, response)
    with app.app_context():
        assert ix.stored_intents(w) == []


def _failing_commit():
    raise IntegrityError("INSERT INTO payment_intents ...", {},
                         Exception("Duplicate entry 'x' for key 'uq_payment_intents_x'"))


@pytest.mark.parametrize("action", ["create", "return", "cancel"])
def test_an_integrity_error_is_rolled_back_and_reported_generically(
    app, client, monkeypatch, action
):
    w = ix.login_world(app, client)
    if action == "create":
        token = ix.create_token(client, w)
    else:
        xp = ix.create_intent(client, w)
        if action == "return":
            ix.simulate(client, w, xp, "failure")
            token = ix.checkout_context(client, w, xp)
        else:
            token = ix.cancel_token(client, w, xp)
    before = ix.record(app)
    monkeypatch.setattr(routes.db.session, "commit", _failing_commit)
    if action == "create":
        response = ix.create(client, w, token=token)
    elif action == "return":
        response = ix.return_to_lms(client, w, xp, context=token)
    else:
        response = ix.cancel(client, w, xp, token=token)
    monkeypatch.undo()
    html = ix.followed(client, response)
    assert ix.INTEGRITY_TEXT in html
    for leaked in ("INSERT", "Duplicate entry", "IntegrityError", "sqlite", "pymysql",
                   "uq_payment_intents"):
        assert leaked not in html, leaked
    assert ix.record(app) == before


def test_a_real_check_failure_rolls_the_whole_return_back(app, client, monkeypatch):
    """A result moment earlier than the intent's creation breaks
    ``ck_payment_intents_timestamps_ordered`` in the database itself."""
    w = ix.login_world(app, client)
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "success")
    context = ix.checkout_context(client, w, xp)
    before = ix.record(app)
    monkeypatch.setattr(routes, "_write_moment", lambda: datetime(2000, 1, 1, 0, 0, 0))
    response = ix.return_to_lms(client, w, xp, context=context)
    monkeypatch.undo()
    assert ix.INTEGRITY_TEXT in ix.followed(client, response)
    assert ix.record(app) == before


def test_the_payment_rows_an_intent_write_reads_are_never_written(app, client):
    w = ix.login_world(app, client)
    with app.app_context():
        owner, actor = ix.invoice_and_admin(w)
        px.payment(owner, actor, method="bank_transfer", status="rejected", amount="3")
        before = [(r.id, r.status, r.version, r.updated_at)
                  for r in PaymentTransaction.query.order_by(PaymentTransaction.id)]
    xp = ix.create_intent(client, w)
    ix.simulate(client, w, xp, "failure")
    ix.return_to_lms(client, w, xp)
    ix.cancel(client, w, ix.create_intent(client, w))
    with app.app_context():
        db.session.expire_all()
        assert [(r.id, r.status, r.version, r.updated_at)
                for r in PaymentTransaction.query.order_by(PaymentTransaction.id)] == before
