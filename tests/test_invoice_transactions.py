"""Phase 5 / M04 -- tokens, the lock chains, invoice numbering, post-lock
revalidation and ``IntegrityError`` recovery for invoices.

SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so the lock tests
assert what the code *requests* -- which rows, in which order -- and the race
tests inject a competing change at the exact transaction boundary (between the
pre-lock reads and the lock chain, or at the sequence lock) to prove every
write re-decides against the locked rows. None of this proves real InnoDB
blocking.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

import app.blueprints.admin.fee_assignments as assignment_routes
import app.blueprints.admin.invoices as routes
import app.services.invoice_audit as audit
import tests.fee_assignment_fixtures as fees
import tests.fee_plan_fixtures as plans
import tests.invoice_fixtures as fx
from app.extensions import db
from app.models import (
    MAX_INVOICE_SEQUENCE_NUMBER,
    AcademicTerm,
    Enrollment,
    FeePlan,
    FeePlanItem,
    Group,
    Invoice,
    InvoiceItem,
    InvoiceNumberSequence,
    PaymentAuditEvent,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import fee_plan_tokens, student_fee_assignment_tokens
from app.services import invoice_tokens as tokens
from app.services.invoice_transactions import (
    allocate_invoice_number,
    lock_invoice_chain,
    lock_invoice_number_sequence,
    locked_active_items,
    locked_assignment_invoices,
    locked_plan_items,
)

_PREFIX = ["academic_terms", "levels", "courses", "groups", "users", "enrollments", "users"]
_MOMENT = datetime(2026, 7, 1, 12, 0, 0)

CREATE = tokens.PURPOSE_CREATE
ITEM_CREATE = tokens.PURPOSE_ITEM_CREATE
ITEM_EDIT = tokens.PURPOSE_ITEM_EDIT
ITEM_REMOVE = tokens.PURPOSE_ITEM_REMOVE
ISSUE = tokens.PURPOSE_ISSUE
CANCEL = tokens.PURPOSE_CANCEL


def _now_plus(minutes):
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=minutes)).replace(
        microsecond=0)


def _draft_world(app, client):
    """A logged-in world with one draft invoice written directly."""
    w = fx.login_world(app, client)
    with app.app_context():
        owner = fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]),
                           db.session.get(User, w["admin_id"]))
        return w, owner.public_id


# ===========================================================================
# Tokens
# ===========================================================================


def _payloads():
    actor, invoice = "a" * 36, "i" * 36
    invoice_state = {"actor_public_id": actor, "invoice_public_id": invoice, "invoice_version": 3}
    return {
        CREATE: {"actor_public_id": actor, "group_public_id": "g" * 36,
                 "enrollment_public_id": "e" * 36, "assignment_public_id": "s" * 36,
                 "plan_public_id": "p" * 36, "assignment_version": 1},
        ITEM_CREATE: invoice_state,
        ITEM_EDIT: dict(invoice_state, item_public_id="l" * 36),
        ITEM_REMOVE: dict(invoice_state, item_public_id="l" * 36),
        ISSUE: dict(invoice_state, active_items=[["l" * 36, 1], ["m" * 36, 2]]),
        CANCEL: invoice_state,
    }


def test_the_six_purposes_cannot_be_replayed_as_one_another(app):
    with app.app_context():
        payloads = _payloads()
        assert set(payloads) == set(tokens.PURPOSES)
        for purpose, payload in payloads.items():
            token = tokens.make_token(purpose, **payload)
            assert tokens.load_token(token, purpose) == dict(payload, purpose=purpose)
            for other in tokens.PURPOSES:
                if other != purpose:
                    assert tokens.load_token(token, other) is None, (purpose, other)


def test_a_token_from_another_milestone_is_not_an_invoice_token(app):
    with app.app_context():
        foreign = [
            fee_plan_tokens.make_token(fee_plan_tokens.PURPOSE_CREATE, actor_public_id="a" * 36),
            student_fee_assignment_tokens.make_token(
                student_fee_assignment_tokens.PURPOSE_CANCEL, actor_public_id="a" * 36,
                enrollment_public_id="e" * 36, assignment_public_id="s" * 36,
                assignment_version=1),
        ]
        for token in foreign:
            for purpose in tokens.PURPOSES:
                assert tokens.load_token(token, purpose) is None


def test_tampered_malformed_and_wrongly_shaped_tokens_are_refused(app):
    with app.app_context():
        payload = _payloads()[ISSUE]
        token = tokens.make_token(ISSUE, **payload)
        middle = len(token) // 3
        tampered = token[:middle] + ("x" if token[middle] != "x" else "y") + token[middle + 1:]
        serializer = tokens._serializer(ISSUE)
        body = dict(payload, purpose=ISSUE)
        for bad in (
            None, "", 42, tampered, token + "x", "x" * 5000, "not.a.token",
            serializer.dumps(dict(body, extra="1")),
            serializer.dumps({"purpose": ISSUE}),
            serializer.dumps(dict(body, purpose=CANCEL)),
            serializer.dumps(dict(body, invoice_version=True)),
            serializer.dumps(dict(body, invoice_version=0)),
            serializer.dumps(dict(body, invoice_version="3")),
            serializer.dumps(dict(body, invoice_version=2**31)),
            serializer.dumps(dict(body, invoice_public_id="")),
            serializer.dumps(dict(body, actor_public_id="a" * 65)),
            serializer.dumps(dict(body, active_items="l1")),
            serializer.dumps(dict(body, active_items=[["l1"]])),
            serializer.dumps(dict(body, active_items=[["l1", 1, 2]])),
            serializer.dumps(dict(body, active_items=[["l1", True]])),
            serializer.dumps(dict(body, active_items=[["l1", 0]])),
            serializer.dumps(dict(body, active_items=[[1, 1]])),
            serializer.dumps(dict(body, active_items=[["l1", 1], ["l1", 2]])),
            serializer.dumps(dict(body, active_items=[[f"l{n}", 1] for n in range(21)])),
            serializer.dumps(["not", "a", "dict"]),
        ):
            assert tokens.load_token(bad, ISSUE) is None, bad
            assert tokens.token_is_stale(bad, ISSUE, **payload)
        assert not tokens.token_is_stale(token, ISSUE, **payload)
        assert tokens.token_is_stale(token, ISSUE, **dict(payload, invoice_version=4))
        assert tokens.token_is_stale(token, ISSUE, **dict(payload, active_items=[["l" * 36, 1]]))
        assert tokens.token_is_stale(
            token, ISSUE, **dict(payload, active_items=[["m" * 36, 2], ["l" * 36, 1]]))


def test_an_expired_token_is_refused(app, monkeypatch):
    with app.app_context():
        token = tokens.make_token(CANCEL, **_payloads()[CANCEL])
        assert tokens.load_token(token, CANCEL) is not None
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
        assert tokens.load_token(token, CANCEL) is None


def test_a_create_token_older_than_the_latest_invoice_change_is_stale(app):
    with app.app_context():
        state = _payloads()[CREATE]
        token = tokens.make_token(CREATE, **state)
        _, issued = tokens.load_token_with_issue_time(token, CREATE)
        assert issued.tzinfo is None and issued.microsecond == 0
        second = timedelta(seconds=1)
        assert not tokens.token_is_stale(token, CREATE, **state)
        assert not tokens.token_is_stale(token, CREATE, changed_at=issued, **state)
        assert tokens.token_is_stale(token, CREATE, changed_at=issued + second, **state)


def test_an_issue_token_for_twenty_lines_stays_within_its_bound(app):
    with app.app_context():
        state = [[str(uuid.uuid4()), 2**31 - 1] for _ in range(20)]
        token = tokens.make_token(ISSUE, actor_public_id=str(uuid.uuid4()),
                                  invoice_public_id=str(uuid.uuid4()), invoice_version=2**31 - 1,
                                  active_items=state)
        assert len(token) <= tokens._MAX_TOKEN_LENGTH
        assert tokens.load_token(token, ISSUE)["active_items"] == state


def test_rendered_tokens_carry_no_internal_id_name_label_money_or_reason(app, client):
    with app.app_context():
        filler = fx.admin("filler@example.com")
        for index in range(5):
            fx.invoice(fees.assignment(fees.enrollment(),
                                       fees.active_plan(filler, name=f"Filler {index}"), filler),
                       filler)
    w = fx.login_world(app, client)
    create_token = fx.create_token(client, w)
    ip = fx.create_draft(client, w)
    lp = fx.line_ids(app, ip)[0]
    rendered = {
        CREATE: create_token,
        ITEM_CREATE: fx.add_line_token(client, w, ip),
        ITEM_EDIT: fx.edit_line_token(client, w, ip, lp),
        ITEM_REMOVE: fx.remove_line_token(client, w, ip, lp),
        ISSUE: fx.issue_token(client, w, ip),
        CANCEL: fx.cancel_token(client, w, ip),
    }
    with app.app_context():
        lines = fx.stored_lines(ip)
        row = fx.stored_invoice(ip)
        internal = {str(value) for value in (
            row.id, w["assignment_id"], w["enrollment_id"], w["group_id"], w["plan_id"],
            w["admin_id"], w["student_id"], *[line.id for line in lines])}
        state = {"actor_public_id": w["admin_public_id"], "invoice_public_id": ip,
                 "invoice_version": 1}
        expected = {
            CREATE: {"actor_public_id": w["admin_public_id"], "group_public_id": w["gp"],
                     "enrollment_public_id": w["ep"], "assignment_public_id": w["ap"],
                     "plan_public_id": w["pp"], "assignment_version": 1},
            ITEM_CREATE: state,
            ITEM_EDIT: dict(state, item_public_id=lp),
            ITEM_REMOVE: dict(state, item_public_id=lp),
            ISSUE: dict(state, active_items=[[line.public_id, 1] for line in lines]),
            CANCEL: state,
        }
        for purpose, token in rendered.items():
            assert token, purpose
            payload = tokens.load_token(token, purpose)
            assert payload == dict(expected[purpose], purpose=purpose), purpose
            text = str(payload)
            for secret in ("Student One", "Standard plan", "Registration", "Course", "1200.5",
                           "50.000", "LYD", "draft", "Duplicate"):
                assert secret not in text, (purpose, secret)
            values = [value for key, value in payload.items() if key != "active_items"]
            values += [part for pair in payload.get("active_items", []) for part in pair]
            assert not {str(value) for value in values} & internal, purpose


# ===========================================================================
# Tokens at the routes
# ===========================================================================

_COMMON = ["missing", "forged", "tampered", "wrong_purpose", "stale_version", "other_actor",
           "expired"]
_CASES = (
    [(CREATE, kind) for kind in _COMMON + ["cross_group", "cross_enrollment", "cross_assignment",
                                           "cross_plan"]]
    + [(purpose, kind) for purpose in (ITEM_CREATE, ITEM_EDIT, ITEM_REMOVE, ISSUE, CANCEL)
       for kind in _COMMON + ["cross_invoice"]]
    + [(purpose, "cross_item") for purpose in (ITEM_EDIT, ITEM_REMOVE)]
    + [(ISSUE, kind) for kind in ("items_extra", "items_version", "items_missing")]
)


def _post(client, purpose, w, ip, lp, token):
    if purpose == CREATE:
        return fx.create(client, w, token=token)
    if purpose == ITEM_CREATE:
        return fx.add_line(client, w, ip, token=token)
    if purpose == ITEM_EDIT:
        return fx.edit_line(client, w, ip, lp, token=token, kind="course", label="Changed",
                            amount="99")
    if purpose == ITEM_REMOVE:
        return fx.remove_line(client, w, ip, lp, token=token)
    if purpose == ISSUE:
        return fx.issue(client, w, ip, token=token)
    return fx.cancel(client, w, ip, token=token)


@pytest.mark.parametrize("purpose, kind", _CASES)
def test_every_mutation_refuses_every_token_but_a_current_one(
    app, client, monkeypatch, purpose, kind
):
    w = fx.login_world(app, client)
    ip = lp = None
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        plan = db.session.get(FeePlan, w["plan_id"])
        second = fx.admin("second@example.com")
        if purpose == CREATE:
            state = {"actor_public_id": w["admin_public_id"], "group_public_id": w["gp"],
                     "enrollment_public_id": w["ep"], "assignment_public_id": w["ap"],
                     "plan_public_id": w["pp"], "assignment_version": 1}
            sibling = fees.enrollment(db.session.get(Group, w["group_id"]))
            elsewhere = fees.enrollment()
            cross = {
                "cross_group": {"group_public_id": db.session.get(
                    Group, elsewhere.group_id).public_id},
                "cross_enrollment": {"enrollment_public_id": sibling.public_id},
                "cross_assignment": {"assignment_public_id": fees.assignment(
                    sibling, plan, actor).public_id},
                "cross_plan": {"plan_public_id": fees.active_plan(actor, name="Other").public_id},
            }
            stale = {"assignment_version": 2}
        else:
            other_ip = fx.invoice(assignment, actor).public_id
            owner = fx.invoice(assignment, actor)
            ip = owner.public_id
            lines = InvoiceItem.query.filter_by(invoice_id=owner.id).order_by(InvoiceItem.id).all()
            lp, lp2 = lines[0].public_id, lines[1].public_id
            active = [[line.public_id, line.version] for line in lines]
            state = {"actor_public_id": w["admin_public_id"], "invoice_public_id": ip,
                     "invoice_version": 1}
            if purpose in (ITEM_EDIT, ITEM_REMOVE):
                state["item_public_id"] = lp
            if purpose == ISSUE:
                state["active_items"] = active
            cross = {"cross_invoice": {"invoice_public_id": other_ip},
                     "cross_item": {"item_public_id": lp2},
                     "items_extra": {"active_items": active + [["x" * 36, 1]]},
                     "items_version": {"active_items": [[active[0][0], 2], active[1]]},
                     "items_missing": {"active_items": active[:1]}}
            stale = {"invoice_version": 2}
        genuine = tokens.make_token(purpose, **state)
        middle = len(genuine) // 2
        wrong = CANCEL if purpose != CANCEL else ITEM_CREATE
        variants = {
            "missing": "",
            "forged": "forged",
            "tampered": genuine[:middle] + ("A" if genuine[middle] != "A" else "B")
            + genuine[middle + 1:],
            "wrong_purpose": tokens.make_token(
                wrong, actor_public_id=w["admin_public_id"], invoice_public_id=ip or "i" * 36,
                invoice_version=1),
            "stale_version": tokens.make_token(purpose, **dict(state, **stale)),
            "other_actor": tokens.make_token(purpose, **dict(state,
                                                              actor_public_id=second.public_id)),
            "expired": genuine,
        }
        if kind in cross:
            variants[kind] = tokens.make_token(purpose, **dict(state, **cross[kind]))
        token = variants[kind]
        before = fx.financial_record()
    if kind == "expired":
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
    response = _post(client, purpose, w, ip, lp, token)
    monkeypatch.undo()
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


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


def test_draft_creation_takes_the_documented_lock_order(app, client, monkeypatch):
    w = fx.login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        plan = db.session.get(FeePlan, w["plan_id"])
        plans.item(plan, label="Old", status=plans.ITEM_REMOVED)
        earlier = fx.invoice(assignment, actor, status=fx.CANCELLED,
                             lines=(("course", "Course", "5"),))
        fx.invoice(fees.assignment(fees.enrollment(), plan, actor), actor)
        later = fx.invoice(assignment, actor, status=fx.CANCELLED,
                           lines=(("course", "Course", "5"),))
        item_ids = [row.id for row in FeePlanItem.query.filter_by(fee_plan_id=plan.id,
                                                                  status="active")]
        invoice_ids = [earlier.id, later.id]
    token = fx.create_token(client, w)

    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch, routes, ["lock_invoice_chain"]) as chain:
        response = fx.create(client, w, token=token)
    locked = requested[:]
    monkeypatch.undo()

    assert response.status_code == 302
    assert fx.CREATED_OK_TEXT in fx.followed(client, response)
    assert locked == _PREFIX + ["student_fee_assignments", "fee_plans", "fee_plan_items",
                                "fee_plan_items", "invoices", "invoices"]
    assert chain.ids("users") == [w["student_id"], w["admin_id"]]
    assert chain.ids("student_fee_assignments") == [w["assignment_id"]]
    assert chain.ids("fee_plans") == [w["plan_id"]]
    assert chain.ids("fee_plan_items") == sorted(item_ids)
    assert chain.ids("invoices") == sorted(invoice_ids)


def test_a_line_edit_takes_the_documented_lock_order(app, client, monkeypatch):
    w = fx.login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        owner = fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]), actor,
                           lines=())
        first = fx.line(owner, label="A")
        fx.line(owner, label="B", status=fx.LINE_REMOVED, removed_by=actor)
        target = fx.line(owner, label="C")
        last = fx.line(owner, label="D")
        ip, lp, invoice_id = owner.public_id, target.public_id, owner.id
        expected = sorted([first.id, target.id, last.id])
    token = fx.edit_line_token(client, w, ip, lp)

    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch, routes, ["lock_invoice_chain"]) as chain:
        response = fx.edit_line(client, w, ip, lp, token=token, kind="course", label="C2",
                                amount="7")
    locked = requested[:]
    monkeypatch.undo()

    assert fx.LINE_SAVED_TEXT in fx.followed(client, response)
    assert locked == _PREFIX + ["student_fee_assignments", "invoices"] + ["invoice_items"] * 3
    assert chain.ids("invoices") == [invoice_id]
    assert chain.ids("invoice_items") == expected


def test_issue_takes_the_documented_lock_order_with_the_sequence_last(app, client, monkeypatch):
    w, ip = _draft_world(app, client)
    with app.app_context():
        counter = fx.sequence(fx.center_year(app, _MOMENT), 0)
        counter_id = counter.id
        line_ids = [line.id for line in fx.stored_lines(ip)]
    token = fx.issue_token(client, w, ip)

    requested = _record_lock_requests(monkeypatch)
    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    with _ChainReads(monkeypatch, routes,
                     ["lock_invoice_chain", "lock_invoice_number_sequence"]) as chain:
        response = fx.issue(client, w, ip, token=token)
    locked = requested[:]
    monkeypatch.undo()

    assert fx.ISSUED_OK_TEXT in fx.followed(client, response)
    assert locked == _PREFIX + ["student_fee_assignments", "invoices", "invoice_items",
                                "invoice_items", "invoice_number_sequences"]
    assert chain.ids("invoice_items") == sorted(line_ids)
    assert chain.ids("invoice_number_sequences") == [counter_id]


def test_cancellation_takes_the_documented_lock_order(app, client, monkeypatch):
    w, ip = _draft_world(app, client)
    with app.app_context():
        invoice_id = fx.stored_invoice(ip).id
    token = fx.cancel_token(client, w, ip)
    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch, routes, ["lock_invoice_chain"]) as chain:
        response = fx.cancel(client, w, ip, token=token)
    locked = requested[:]
    monkeypatch.undo()
    assert fx.CANCELLED_OK_TEXT in fx.followed(client, response)
    assert locked == _PREFIX + ["student_fee_assignments", "invoices"]
    assert chain.ids("invoices") == [invoice_id]


def test_fee_assignment_cancellation_locks_its_invoices_after_the_assignment(
    app, client, monkeypatch
):
    w = fx.login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        assignment = db.session.get(StudentFeeAssignment, w["assignment_id"])
        plan = db.session.get(FeePlan, w["plan_id"])
        earlier = fx.invoice(assignment, actor, status=fx.CANCELLED)
        fx.invoice(fees.assignment(fees.enrollment(), plan, actor), actor)
        later = fx.invoice(assignment, actor, status=fx.CANCELLED, number="INV-2026-000001")
        invoice_ids = [earlier.id, later.id]
    token = fees.cancel_token(client, w["gp"], w["ep"], w["ap"])
    assert token

    requested = _record_lock_requests(monkeypatch)
    with _ChainReads(monkeypatch, assignment_routes,
                     ["lock_fee_assignment_chain", "lock_assignment_invoices"]) as chain:
        response = fees.cancel(client, w["gp"], w["ep"], w["ap"], token=token)
    locked = requested[:]
    monkeypatch.undo()

    assert fees.CANCELLED_OK_TEXT in fx.followed(client, response)
    assert locked == _PREFIX + ["student_fee_assignments", "invoices", "invoices"]
    assert chain.ids("student_fee_assignments") == [w["assignment_id"]]
    assert chain.ids("invoices") == sorted(invoice_ids)


def _chain(w, **links):
    return lock_invoice_chain(w["gp"], w["term_id"], w["level_id"], w["course_id"],
                              w["student_id"], w["enrollment_id"], w["admin_id"],
                              w["assignment_id"], **links)


def test_the_chain_stops_where_the_arguments_stop(app, monkeypatch):
    w = fx.world(app)
    with app.app_context():
        owner = fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]),
                           db.session.get(User, w["admin_id"]))
        invoice_id = owner.id
        requested = _record_lock_requests(monkeypatch)

        _chain(w)
        assert requested == _PREFIX + ["student_fee_assignments"]
        requested.clear()
        _chain(w, plan_id=w["plan_id"])
        assert requested == _PREFIX + ["student_fee_assignments", "fee_plans"]
        requested.clear()
        locks = _chain(w, plan_id=w["plan_id"], include_plan_items=True,
                       include_assignment_invoices=True)
        assert requested == _PREFIX + ["student_fee_assignments", "fee_plans", "fee_plan_items",
                                       "fee_plan_items", "invoices"]
        assert [item.label for item in locked_plan_items(locks)] == ["Registration", "Course"]
        assert [row.id for row in locked_assignment_invoices(locks)] == [invoice_id]
        assert locks.invoice is None and locks.items == {}
        requested.clear()
        locks = _chain(w, invoice_id=invoice_id)
        assert requested == _PREFIX + ["student_fee_assignments", "invoices"]
        assert locks.items == {} and locks.plan is None
        requested.clear()
        locks = _chain(w, invoice_id=invoice_id, include_active_items=True)
        assert requested == _PREFIX + ["student_fee_assignments", "invoices", "invoice_items",
                                       "invoice_items"]
        assert len(locked_active_items(locks)) == 2
        assert (locks.group.id, locks.student.id, locks.enrollment.id, locks.actor.id,
                locks.assignment.id) == (w["group_id"], w["student_id"], w["enrollment_id"],
                                         w["admin_id"], w["assignment_id"])

        requested.clear()
        counter = lock_invoice_number_sequence(2026, fx.CREATED_AT)
        assert requested == ["invoice_number_sequences"]
        assert (counter.calendar_year, counter.last_number) == (2026, 0)
        assert allocate_invoice_number(counter, fx.CREATED_AT) == "INV-2026-000001"
        assert counter.last_number == 1
        counter.last_number = MAX_INVOICE_SEQUENCE_NUMBER
        assert allocate_invoice_number(counter, fx.CREATED_AT) is None
        assert counter.last_number == MAX_INVOICE_SEQUENCE_NUMBER
        db.session.rollback()


def test_the_chain_resets_the_transaction_exactly_once(app):
    w = fx.world(app)
    rollbacks = []
    with app.app_context():
        def _rec(conn):
            rollbacks.append(1)

        sa_event.listen(db.engine, "rollback", _rec)
        try:
            _chain(w, plan_id=w["plan_id"], include_plan_items=True,
                   include_assignment_invoices=True)
        finally:
            sa_event.remove(db.engine, "rollback", _rec)
    assert len(rollbacks) <= 1


# ===========================================================================
# Numbering
# ===========================================================================


def _rival(app, w, **kwargs):
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        assignment = fees.assignment(fees.enrollment(), db.session.get(FeePlan, w["plan_id"]),
                                     actor)
        return fx.invoice(assignment, actor, **kwargs).public_id


def test_a_competing_issue_at_the_sequence_lock_yields_the_next_number(app, client, monkeypatch):
    w, ip = _draft_world(app, client)
    year = fx.center_year(app, _MOMENT)
    rival = _rival(app, w)
    token = fx.issue_token(client, w, ip)
    real = routes.lock_invoice_number_sequence

    def competing(calendar_year, moment):
        # Another Administrator issued the rival draft and committed first.
        db.session.execute(update(Invoice).where(Invoice.public_id == rival).values(
            status="issued", invoice_number=f"INV-{calendar_year}-000001", issued_at=moment,
            issued_by_id=w["admin_id"], version=2, updated_at=moment))
        db.session.add(InvoiceNumberSequence(calendar_year=calendar_year, last_number=1,
                                             created_at=moment, updated_at=moment))
        db.session.commit()
        return real(calendar_year, moment)

    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    monkeypatch.setattr(routes, "lock_invoice_number_sequence", competing)
    response = fx.issue(client, w, ip, token=token)
    monkeypatch.undo()

    assert fx.ISSUED_OK_TEXT in fx.followed(client, response)
    with app.app_context():
        assert fx.stored_invoice(ip).invoice_number == f"INV-{year}-000002"
        assert fx.stored_invoice(rival).invoice_number == f"INV-{year}-000001"
        assert [(r.calendar_year, r.last_number) for r in InvoiceNumberSequence.query] == [
            (year, 2)]


def test_a_number_already_taken_is_refused_without_issuing_or_consuming(app, client, monkeypatch):
    w, ip = _draft_world(app, client)
    year = fx.center_year(app, _MOMENT)
    _rival(app, w, status=fx.ISSUED, number=f"INV-{year}-000001")
    with app.app_context():
        fx.sequence(year, 0)
    token = fx.issue_token(client, w, ip)
    before = fx.record(app)
    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    response = fx.issue(client, w, ip, token=token)
    monkeypatch.undo()
    assert fx.INTEGRITY_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


def test_first_issues_racing_to_create_the_years_sequence_roll_back_safely(
    app, client, monkeypatch
):
    w, ip = _draft_world(app, client)
    year = fx.center_year(app, _MOMENT)
    token = fx.issue_token(client, w, ip)

    def racing(calendar_year, moment):
        # The other issue created the year's row first; this one's own insert
        # then meets the unique year.
        db.session.add(InvoiceNumberSequence(calendar_year=calendar_year, last_number=0,
                                             created_at=moment, updated_at=moment))
        db.session.commit()
        db.session.add(InvoiceNumberSequence(calendar_year=calendar_year, last_number=0,
                                             created_at=moment, updated_at=moment))
        db.session.flush()

    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    monkeypatch.setattr(routes, "lock_invoice_number_sequence", racing)
    response = fx.issue(client, w, ip, token=token)
    monkeypatch.undo()
    html = fx.followed(client, response)
    assert fx.INTEGRITY_TEXT in html
    assert not re.search(r"UNIQUE|constraint|sqlite", html, re.I)
    with app.app_context():
        row = fx.stored_invoice(ip)
        assert (row.status, row.invoice_number, row.version) == ("draft", None, 1)
        assert [(r.calendar_year, r.last_number) for r in InvoiceNumberSequence.query] == [
            (year, 0)]
        assert PaymentAuditEvent.query.count() == 0


def test_an_exhausted_year_refuses_issuance_without_changing_anything(app, client, monkeypatch):
    w, ip = _draft_world(app, client)
    with app.app_context():
        fx.sequence(fx.center_year(app, _MOMENT), MAX_INVOICE_SEQUENCE_NUMBER)
    token = fx.issue_token(client, w, ip)
    before = fx.record(app)
    monkeypatch.setattr(routes, "_write_moment", lambda: _MOMENT)
    response = fx.issue(client, w, ip, token=token)
    monkeypatch.undo()
    assert fx.EXHAUSTED_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


def test_the_number_year_is_the_center_local_year(app, client, monkeypatch):
    w, ip = _draft_world(app, client)
    token = fx.issue_token(client, w, ip)
    moment = datetime(2026, 12, 31, 22, 30, 0)
    monkeypatch.setitem(app.config, "APP_TIMEZONE", "UTC+03:00")
    monkeypatch.setattr(routes, "_write_moment", lambda: moment)
    response = fx.issue(client, w, ip, token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    with app.app_context():
        row = fx.stored_invoice(ip)
        assert (row.invoice_number, row.issued_at) == ("INV-2027-000001", moment)
        assert [(r.calendar_year, r.last_number) for r in InvoiceNumberSequence.query] == [
            (2027, 1)]


def test_a_year_boundary_crossed_at_the_sequence_lock_is_stale(app, client, monkeypatch):
    w, ip = _draft_world(app, client)
    token = fx.issue_token(client, w, ip)
    moments = iter([datetime(2026, 12, 31, 23, 59, 59), datetime(2027, 1, 1, 0, 0, 0)])
    before = fx.record(app)
    monkeypatch.setitem(app.config, "APP_TIMEZONE", "UTC")
    monkeypatch.setattr(routes, "_write_moment", lambda: next(moments))
    response = fx.issue(client, w, ip, token=token)
    monkeypatch.undo()
    assert fx.STALE_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


# ===========================================================================
# Post-lock revalidation -- a competing change at the lock boundary
# ===========================================================================


def _inject_before_locks(monkeypatch, change):
    real_chain = routes.lock_invoice_chain

    def chain(*args, **kwargs):
        change()
        db.session.commit()
        return real_chain(*args, **kwargs)

    monkeypatch.setattr(routes, "lock_invoice_chain", chain)


def _spare(app):
    with app.app_context():
        actor = fx.admin("spare@example.com")
        spare_enrollment = fees.enrollment()
        spare_group = db.session.get(Group, spare_enrollment.group_id)
        spare_assignment = fees.assignment(spare_enrollment, fees.active_plan(actor, name="Spare"),
                                           actor)
        spare_invoice = fx.invoice(spare_assignment, actor)
        return {"group_id": spare_group.id, "course_id": spare_group.course_id,
                "enrollment_id": spare_enrollment.id, "assignment_id": spare_assignment.id,
                "invoice_id": spare_invoice.id}


def _common_change(kind, w, spare):
    """The changes every chain meets the same way. ``True`` when handled."""
    if kind == "admin_suspended":
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif kind == "admin_demoted":
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            role=UserRole.TEACHER.value))
    elif kind == "student_role_changed":
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            role=UserRole.TEACHER.value))
    elif kind == "enrollment_moved":
        db.session.execute(update(Enrollment).where(Enrollment.id == w["enrollment_id"]).values(
            group_id=spare["group_id"]))
    elif kind == "assignment_moved":
        db.session.execute(update(StudentFeeAssignment).where(
            StudentFeeAssignment.id == w["assignment_id"]).values(
            enrollment_id=spare["enrollment_id"]))
    elif kind == "group_retargeted":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            course_id=spare["course_id"]))
    elif kind == "group_archived":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            status=fees.ARCHIVED))
    else:
        return False
    return True


def _create_change(kind, w, spare):
    if _common_change(kind, w, spare):
        return
    if kind == "student_suspended":
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif kind == "enrollment_withdrawn":
        db.session.execute(update(Enrollment).where(Enrollment.id == w["enrollment_id"]).values(
            status=fees.WITHDRAWN))
    elif kind == "term_archived":
        db.session.execute(update(AcademicTerm).where(AcademicTerm.id == w["term_id"]).values(
            status=fees.ARCHIVED))
    elif kind in ("assignment_cancelled", "assignment_cancelled_quietly"):
        db.session.execute(update(StudentFeeAssignment).where(
            StudentFeeAssignment.id == w["assignment_id"]).values(
            status=fees.CANCELLED, cancelled_at=fees.CANCELLED_AT, cancelled_by_id=w["admin_id"],
            updated_at=fees.CANCELLED_AT, version=2 if kind == "assignment_cancelled" else 1))
    elif kind == "draft_created_meanwhile":
        db.session.add(Invoice(student_fee_assignment_id=w["assignment_id"], status="draft",
                               version=1, created_at=fx.CREATED_AT, updated_at=fx.CREATED_AT))
    elif kind == "invoice_cancelled_just_now":
        later = _now_plus(2)
        db.session.add(Invoice(student_fee_assignment_id=w["assignment_id"], status="cancelled",
                               cancelled_at=later, cancelled_by_id=w["admin_id"], version=2,
                               created_at=later, updated_at=later))
    elif kind == "plan_items_broken_quietly":
        db.session.add(FeePlanItem(fee_plan_id=w["plan_id"], kind="course", label="course",
                                   amount="1", status="active", version=1,
                                   created_at=plans.CREATED, updated_at=plans.CREATED))
    elif kind == "plan_reverted_quietly":
        db.session.execute(update(FeePlan).where(FeePlan.id == w["plan_id"]).values(
            status="draft", first_activated_at=None, first_activated_by_id=None))
    else:  # pragma: no cover
        raise AssertionError(kind)


_CREATE_RACES = {
    "admin_suspended": 404,
    "admin_demoted": 404,
    "student_role_changed": 404,
    "enrollment_moved": 404,
    "assignment_moved": 404,
    "student_suspended": fx.STUDENT_INACTIVE_TEXT,
    "enrollment_withdrawn": fx.ENROLLMENT_INACTIVE_TEXT,
    "group_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "term_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "group_retargeted": fx.STALE_TEXT,
    "assignment_cancelled": fx.STALE_TEXT,
    "assignment_cancelled_quietly": fx.ASSIGNMENT_CANCELLED_TEXT,
    "draft_created_meanwhile": fx.OPEN_INVOICE_TEXT,
    "invoice_cancelled_just_now": fx.STALE_TEXT,
    "plan_items_broken_quietly": fx.PLAN_ITEMS_INVALID_TEXT,
    "plan_reverted_quietly": fx.PLAN_UNAVAILABLE_TEXT,
}


@pytest.mark.parametrize("kind", sorted(_CREATE_RACES))
def test_draft_creation_re_proves_every_rule_against_the_locked_rows(
    app, client, monkeypatch, kind
):
    w = fx.login_world(app, client)
    spare = _spare(app)
    token = fx.create_token(client, w)
    assert token
    with app.app_context():
        existing = Invoice.query.count()
    _inject_before_locks(monkeypatch, lambda: _create_change(kind, w, spare))
    response = fx.create(client, w, token=token)
    monkeypatch.undo()

    expected = _CREATE_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert expected in fx.followed(client, response)
    injected = 1 if kind in ("draft_created_meanwhile", "invoice_cancelled_just_now") else 0
    with app.app_context():
        assert Invoice.query.count() == existing + injected
        assert PaymentAuditEvent.query.count() == 0


def _line_world(app, client):
    w = fx.login_world(app, client)
    spare = _spare(app)
    with app.app_context():
        owner = fx.invoice(db.session.get(StudentFeeAssignment, w["assignment_id"]),
                           db.session.get(User, w["admin_id"]))
        registration, course = [line.public_id for line in fx.stored_lines(owner.public_id)]
        return w, spare, owner.public_id, registration, course


def _invoice_change(kind, w, spare, ip, registration, course):
    if _common_change(kind, w, spare):
        return
    invoice = Invoice.public_id == ip
    if kind == "invoice_moved":
        db.session.execute(update(Invoice).where(invoice).values(
            student_fee_assignment_id=spare["assignment_id"]))
    elif kind == "line_moved":
        db.session.execute(update(InvoiceItem).where(InvoiceItem.public_id == course).values(
            invoice_id=spare["invoice_id"]))
    elif kind == "version_moved":
        db.session.execute(update(Invoice).where(invoice).values(version=2))
    elif kind in ("cancelled_quietly", "cancelled_elsewhere"):
        db.session.execute(update(Invoice).where(invoice).values(
            status="cancelled", cancelled_at=fx.CANCELLED_AT, cancelled_by_id=w["admin_id"],
            updated_at=fx.CANCELLED_AT, version=2 if kind == "cancelled_elsewhere" else 1))
    elif kind == "issued_quietly":
        db.session.execute(update(Invoice).where(invoice).values(
            status="issued", invoice_number="INV-2026-000777", issued_at=fx.ISSUED_AT,
            issued_by_id=w["admin_id"], updated_at=fx.ISSUED_AT))
    elif kind == "line_removed_quietly":
        db.session.execute(update(InvoiceItem).where(InvoiceItem.public_id == course).values(
            status="removed", removed_at=fx.REMOVED_AT, removed_by_id=w["admin_id"],
            updated_at=fx.REMOVED_AT))
    elif kind == "label_taken_quietly":
        db.session.execute(update(InvoiceItem).where(
            InvoiceItem.public_id == registration).values(label="Changed"))
    elif kind == "labels_duplicated_quietly":
        db.session.execute(update(InvoiceItem).where(InvoiceItem.public_id == course).values(
            label="registration"))
    elif kind == "line_added":
        owner = Invoice.query.filter(invoice).one()
        db.session.add(InvoiceItem(invoice_id=owner.id, kind="course", label="Extra", amount="1",
                                   status="active", version=1, created_at=fx.CREATED_AT,
                                   updated_at=fx.CREATED_AT))
    elif kind == "line_version_moved":
        db.session.execute(update(InvoiceItem).where(InvoiceItem.public_id == course).values(
            version=2))
    else:  # pragma: no cover
        raise AssertionError(kind)


_EDIT_RACES = {
    "admin_suspended": 404,
    "invoice_moved": 404,
    "line_moved": 404,
    "assignment_moved": 404,
    "version_moved": fx.STALE_TEXT,
    "cancelled_quietly": fx.READ_ONLY_TEXT,
    "issued_quietly": fx.STALE_TEXT,
    "line_removed_quietly": fx.LINE_ALREADY_REMOVED_TEXT,
    "label_taken_quietly": fx.LABEL_TAKEN_TEXT,
    "group_retargeted": fx.STALE_TEXT,
    "group_archived": fx.LINE_SAVED_TEXT,
}


@pytest.mark.parametrize("kind", sorted(_EDIT_RACES))
def test_a_line_edit_re_proves_every_rule_against_the_locked_rows(app, client, monkeypatch, kind):
    w, spare, ip, registration, course = _line_world(app, client)
    token = fx.edit_line_token(client, w, ip, course)
    _inject_before_locks(monkeypatch,
                         lambda: _invoice_change(kind, w, spare, ip, registration, course))
    response = fx.edit_line(client, w, ip, course, token=token, kind="course", label="Changed",
                            amount="99")
    monkeypatch.undo()

    expected = _EDIT_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert expected in fx.followed(client, response)
    written = expected == fx.LINE_SAVED_TEXT
    with app.app_context():
        line = InvoiceItem.query.filter_by(public_id=course).one()
        assert (line.label == "Changed") == written
        assert PaymentAuditEvent.query.count() == (1 if written else 0)


_ISSUE_RACES = {
    "admin_suspended": 404,
    "invoice_moved": 404,
    "cancelled_quietly": fx.NOT_ISSUABLE_TEXT,
    "line_added": fx.STALE_TEXT,
    "line_version_moved": fx.STALE_TEXT,
    "labels_duplicated_quietly": fx.LINES_INVALID_TEXT,
    "group_archived": fx.ISSUED_OK_TEXT,
}


@pytest.mark.parametrize("kind", sorted(_ISSUE_RACES))
def test_issue_re_proves_every_rule_against_the_locked_rows(app, client, monkeypatch, kind):
    w, spare, ip, registration, course = _line_world(app, client)
    token = fx.issue_token(client, w, ip)
    _inject_before_locks(monkeypatch,
                         lambda: _invoice_change(kind, w, spare, ip, registration, course))
    response = fx.issue(client, w, ip, token=token)
    monkeypatch.undo()

    expected = _ISSUE_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert expected in fx.followed(client, response)
    issued = expected == fx.ISSUED_OK_TEXT
    with app.app_context():
        row = Invoice.query.filter_by(public_id=ip).one()
        assert (row.invoice_number is not None) == issued
        assert InvoiceNumberSequence.query.count() == (1 if issued else 0)
        assert PaymentAuditEvent.query.count() == (1 if issued else 0)


_CANCEL_RACES = {
    "admin_suspended": 404,
    "invoice_moved": 404,
    "assignment_moved": 404,
    "cancelled_elsewhere": fx.ALREADY_CANCELLED_TEXT,
    "version_moved": fx.STALE_TEXT,
    "group_retargeted": fx.STALE_TEXT,
    "group_archived": fx.CANCELLED_OK_TEXT,
}


@pytest.mark.parametrize("kind", sorted(_CANCEL_RACES))
def test_cancellation_re_proves_its_rules_against_the_locked_rows(app, client, monkeypatch, kind):
    w, spare, ip, registration, course = _line_world(app, client)
    token = fx.cancel_token(client, w, ip)
    _inject_before_locks(monkeypatch,
                         lambda: _invoice_change(kind, w, spare, ip, registration, course))
    response = fx.cancel(client, w, ip, token=token)
    monkeypatch.undo()

    expected = _CANCEL_RACES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert expected in fx.followed(client, response)
    written = expected == fx.CANCELLED_OK_TEXT
    with app.app_context():
        row = Invoice.query.filter_by(public_id=ip).one()
        assert (row.cancelled_by_id is not None) == (written or kind == "cancelled_elsewhere")
        if kind == "cancelled_elsewhere":
            assert row.cancelled_at == fx.CANCELLED_AT
        assert PaymentAuditEvent.query.count() == (1 if written else 0)


# ===========================================================================
# IntegrityError and audit refusals
# ===========================================================================


def _failing_commit():
    raise IntegrityError("INSERT INTO payment_audit_events ...", {},
                         Exception("Duplicate entry 'x' for key"))


def _action(action, client, w, ip, course):
    """``(token_reader, poster)`` for one mutation."""
    return {
        "create": (lambda: fx.create_token(client, w),
                   lambda token: fx.create(client, w, token=token)),
        "add": (lambda: fx.add_line_token(client, w, ip),
                lambda token: fx.add_line(client, w, ip, token=token, label="Books", amount="5")),
        "edit": (lambda: fx.edit_line_token(client, w, ip, course),
                 lambda token: fx.edit_line(client, w, ip, course, token=token, kind="course",
                                            label="Changed", amount="99")),
        "remove": (lambda: fx.remove_line_token(client, w, ip, course),
                   lambda token: fx.remove_line(client, w, ip, course, token=token)),
        "issue": (lambda: fx.issue_token(client, w, ip),
                  lambda token: fx.issue(client, w, ip, token=token)),
        "cancel": (lambda: fx.cancel_token(client, w, ip),
                   lambda token: fx.cancel(client, w, ip, token=token)),
    }[action]


_ACTIONS = ["create", "add", "edit", "remove", "issue", "cancel"]


def _world_for(action, app, client):
    if action == "create":
        return fx.login_world(app, client), None, None
    w, _spare_ids, ip, _registration, course = _line_world(app, client)
    return w, ip, course


@pytest.mark.parametrize("action", _ACTIONS)
def test_an_integrity_error_is_rolled_back_and_reported_generically(
    app, client, monkeypatch, action
):
    w, ip, course = _world_for(action, app, client)
    read_token, post = _action(action, client, w, ip, course)
    token = read_token()
    assert token
    before = fx.record(app)
    monkeypatch.setattr(routes.db.session, "commit", _failing_commit)
    response = post(token)
    monkeypatch.undo()
    assert response.status_code == 302
    html = fx.followed(client, response)
    assert fx.INTEGRITY_TEXT in html
    for leaked in ("INSERT", "Duplicate entry", "IntegrityError", "sqlite", "pymysql"):
        assert leaked not in html, leaked
    assert fx.record(app) == before


@pytest.mark.parametrize("action", _ACTIONS)
def test_a_refused_audit_event_rolls_back_the_whole_change(app, client, monkeypatch, action):
    w, ip, course = _world_for(action, app, client)
    read_token, post = _action(action, client, w, ip, course)
    token = read_token()
    before = fx.record(app)

    def refuse(**kwargs):
        raise ValueError("refused")

    monkeypatch.setattr(routes, "record_invoice_event", refuse)
    response = post(token)
    monkeypatch.undo()
    assert fx.INTEGRITY_TEXT in fx.followed(client, response)
    assert fx.record(app) == before


def test_recovery_re_authorizes_from_current_state(app, client, monkeypatch):
    w = fx.login_world(app, client)
    token = fx.create_token(client, w)
    real_commit = db.session.commit

    def commit_that_loses_access():
        db.session.rollback()
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        real_commit()
        _failing_commit()

    monkeypatch.setattr(routes.db.session, "commit", commit_that_loses_access)
    response = fx.create(client, w, token=token)
    monkeypatch.undo()
    assert response.status_code == 404
    with app.app_context():
        assert Invoice.query.count() == 0


def test_a_real_audit_constraint_failure_rolls_back_the_whole_change(app, client, monkeypatch):
    """The version-transition CHECK itself refuses the event -- the final
    defense, exercised for real -- and the line insert and invoice update of
    the same transaction go with it."""
    w, _spare_ids, ip, _registration, _course = _line_world(app, client)
    token = fx.add_line_token(client, w, ip)
    before = fx.record(app)
    real = audit.PaymentAuditEvent

    def corrupted(**kwargs):
        kwargs["invoice_version_after"] = kwargs["invoice_version_after"] + 1
        return real(**kwargs)

    monkeypatch.setattr(audit, "PaymentAuditEvent", corrupted)
    response = fx.add_line(client, w, ip, token=token, label="Books", amount="5")
    monkeypatch.undo()
    html = fx.followed(client, response)
    assert fx.INTEGRITY_TEXT in html
    assert not re.search(r"CHECK|constraint|sqlite", html, re.I)
    assert fx.record(app) == before
