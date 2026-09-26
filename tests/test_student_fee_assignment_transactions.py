"""Phase 5 / M03 -- tokens, the lock chain, post-lock revalidation and
``IntegrityError`` recovery for fee assignments by Enrollment.

SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so the lock tests
assert what the code *requests* -- which rows, in which order -- and the race
tests inject a competing change at the exact transaction boundary (between the
pre-lock reads and the lock chain) to prove every write re-decides against the
locked rows. None of this proves real InnoDB blocking.
"""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

import app.blueprints.admin.fee_assignments as routes
import tests.fee_assignment_fixtures as fx
import tests.fee_plan_fixtures as plans
import tests.structural_checks as sc
from app.extensions import db
from app.models import (
    AcademicTerm,
    Course,
    Enrollment,
    FeePlan,
    FeePlanItem,
    Group,
    Level,
    StudentFeeAssignment,
    User,
    UserRole,
    UserStatus,
)
from app.services import fee_plan_tokens
from app.services import student_fee_assignment_tokens as tokens
from app.services.fee_plan_transactions import locked_active_items
from app.services.student_fee_assignment_transactions import (
    assigned_rows,
    fee_plan_assignable,
    fee_plan_items_valid,
    latest_change,
    lock_fee_assignment_chain,
)

_PREFIX = ["academic_terms", "levels", "courses", "groups", "users", "enrollments", "users"]


def _login_world(app, client):
    w = fx.world(app)
    fx.login_as(client, "admin@example.com")
    return w


def _assign_state(w, **overrides):
    return dict({"actor_public_id": w["admin_public_id"], "enrollment_public_id": w["ep"],
                 "plan_public_id": w["pp"], "plan_version": 2}, **overrides)


def _now_plus(minutes):
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=minutes)).replace(
        microsecond=0)


# ===========================================================================
# Tokens
# ===========================================================================


def test_the_two_purposes_cannot_be_replayed_as_one_another(app):
    with app.app_context():
        payloads = {
            tokens.PURPOSE_ASSIGN: {"actor_public_id": "a" * 36, "enrollment_public_id": "e" * 36,
                                    "plan_public_id": "p" * 36, "plan_version": 2},
            tokens.PURPOSE_CANCEL: {"actor_public_id": "a" * 36, "enrollment_public_id": "e" * 36,
                                    "assignment_public_id": "s" * 36, "assignment_version": 1},
        }
        assert set(payloads) == set(tokens.PURPOSES)
        for purpose, payload in payloads.items():
            token = tokens.make_token(purpose, **payload)
            assert tokens.load_token(token, purpose) == dict(payload, purpose=purpose)
            for other in tokens.PURPOSES:
                if other != purpose:
                    assert tokens.load_token(token, other) is None


def test_a_token_from_another_milestone_is_not_a_fee_assignment_token(app):
    from app.services.calendar_tokens import make_token as calendar_token

    with app.app_context():
        plan_state = {"actor_public_id": "a" * 36, "plan_public_id": "p" * 36,
                      "plan_version": 2, "plan_status": "active"}
        foreign = [
            fee_plan_tokens.make_token(fee_plan_tokens.PURPOSE_ARCHIVE, **plan_state),
            fee_plan_tokens.make_token(fee_plan_tokens.PURPOSE_CREATE, actor_public_id="a" * 36),
            calendar_token("admin", "calendar-event-create", actor_public_id="a" * 36),
        ]
        for token in foreign:
            for purpose in tokens.PURPOSES:
                assert tokens.load_token(token, purpose) is None


def test_tampered_malformed_and_wrongly_shaped_tokens_are_refused(app):
    with app.app_context():
        payload = {"actor_public_id": "a" * 36, "enrollment_public_id": "e" * 36,
                   "assignment_public_id": "s" * 36, "assignment_version": 1}
        purpose = tokens.PURPOSE_CANCEL
        token = tokens.make_token(purpose, **payload)
        middle = len(token) // 3
        tampered = token[:middle] + ("x" if token[middle] != "x" else "y") + token[middle + 1:]
        serializer = tokens._serializer(purpose)
        body = dict(payload, purpose=purpose)
        for bad in (
            None, "", 42, tampered, token + "x", "x" * 3000, "not.a.token",
            serializer.dumps(dict(body, extra="1")),
            serializer.dumps({"purpose": purpose, "actor_public_id": "a"}),
            serializer.dumps(dict(body, purpose=tokens.PURPOSE_ASSIGN)),
            serializer.dumps(dict(body, assignment_version=True)),
            serializer.dumps(dict(body, assignment_version=0)),
            serializer.dumps(dict(body, assignment_version="1")),
            serializer.dumps(dict(body, assignment_version=2**31)),
            serializer.dumps(dict(body, assignment_public_id="")),
            serializer.dumps(dict(body, enrollment_public_id=7)),
            serializer.dumps(dict(body, actor_public_id="a" * 65)),
            serializer.dumps(["not", "a", "dict"]),
        ):
            assert tokens.load_token(bad, purpose) is None, bad
            assert tokens.token_is_stale(bad, purpose, **payload)
        assert not tokens.token_is_stale(token, purpose, **payload)
        assert tokens.token_is_stale(token, purpose, **dict(payload, assignment_version=2))


def test_an_expired_token_is_refused(app, monkeypatch):
    with app.app_context():
        state = {"actor_public_id": "a" * 36, "enrollment_public_id": "e" * 36,
                 "plan_public_id": "p" * 36, "plan_version": 2}
        token = tokens.make_token(tokens.PURPOSE_ASSIGN, **state)
        assert tokens.load_token(token, tokens.PURPOSE_ASSIGN) is not None
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
        assert tokens.load_token(token, tokens.PURPOSE_ASSIGN) is None


def test_a_create_token_older_than_the_latest_fee_history_change_is_stale(app):
    with app.app_context():
        state = {"actor_public_id": "a" * 36, "enrollment_public_id": "e" * 36,
                 "plan_public_id": "p" * 36, "plan_version": 2}
        token = tokens.make_token(tokens.PURPOSE_ASSIGN, **state)
        _, issued = tokens.load_token_with_issue_time(token, tokens.PURPOSE_ASSIGN)
        assert issued.tzinfo is None and issued.microsecond == 0
        second = timedelta(seconds=1)
        assert not tokens.token_is_stale(token, tokens.PURPOSE_ASSIGN, **state)
        assert not tokens.token_is_stale(token, tokens.PURPOSE_ASSIGN, changed_at=issued - second,
                                         **state)
        assert not tokens.token_is_stale(token, tokens.PURPOSE_ASSIGN, changed_at=issued, **state)
        assert tokens.token_is_stale(token, tokens.PURPOSE_ASSIGN, changed_at=issued + second,
                                     **state)


def test_rendered_tokens_carry_no_internal_id_name_or_money(app, client):
    with app.app_context():
        # Filler rows first, so no internal id coincides with a version.
        fillers = [fx.enrollment() for _ in range(6)]
        actor = fx.admin()
        for index, filler in enumerate(fillers):
            fx.assignment(filler, fx.active_plan(actor, name=f"Filler {index}"), actor,
                          status=fx.CANCELLED)
        hidden = fx.active_plan(actor, name="Hidden plan",
                                items=(("course", "Hidden label", "4321.5"),), version=3)
        fresh = fx.enrollment(enrolled=fx.student(name="Secret Student"))
        charged = fx.enrollment(enrolled=fx.student(name="Charged Student"))
        row = fx.assignment(charged, hidden, actor)
        first = (db.session.get(Group, fresh.group_id).public_id, fresh.public_id)
        second = (db.session.get(Group, charged.group_id).public_id, charged.public_id)
        ap, pp = row.public_id, hidden.public_id
        internal = {str(value) for value in (
            actor.id, hidden.id, fresh.id, charged.id, row.id, fresh.student_id,
            charged.student_id, fresh.group_id, charged.group_id)}
        actor_public_id = actor.public_id
    fx.login_as(client, "admin@example.com")
    assign_token = fx.assign_token(client, *first, pp)
    cancel_token = fx.cancel_token(client, *second, ap)
    with app.app_context():
        assert tokens.load_token(assign_token, tokens.PURPOSE_ASSIGN) == {
            "purpose": tokens.PURPOSE_ASSIGN, "actor_public_id": actor_public_id,
            "enrollment_public_id": first[1], "plan_public_id": pp, "plan_version": 3,
        }
        assert tokens.load_token(cancel_token, tokens.PURPOSE_CANCEL) == {
            "purpose": tokens.PURPOSE_CANCEL, "actor_public_id": actor_public_id,
            "enrollment_public_id": second[1], "assignment_public_id": ap,
            "assignment_version": 1,
        }
        for purpose, token in ((tokens.PURPOSE_ASSIGN, assign_token),
                               (tokens.PURPOSE_CANCEL, cancel_token)):
            payload = tokens.load_token(token, purpose)
            # By key and exact value, never as one string: a public id is
            # random hexadecimal and contains "4321" about once in two
            # thousand ids, which discloses nothing about the 4321.5 amount.
            for key, value in payload.items():
                parts = key.split("_")
                assert "id" not in parts or key.endswith("_public_id"), (purpose, key)
                assert not set(parts) & {"name", "label", "amount", "total", "currency",
                                         "status"}, (purpose, key)
                if key.endswith("_public_id"):
                    assert sc.is_public_id(value), (purpose, key, value)
                elif key.endswith("_version"):
                    assert type(value) is int, (purpose, key, value)
                else:
                    assert (key, value) == ("purpose", purpose)
            carried = set(map(str, payload.values()))
            assert not carried & {"Secret Student", "Charged Student", "Hidden plan",
                                  "Hidden label", "4321.5", "LYD", "active", "assigned"}, purpose
            assert not carried & internal, purpose


# ===========================================================================
# Tokens at the routes
# ===========================================================================


@pytest.mark.parametrize(
    "kind",
    ["missing", "forged", "tampered", "wrong_purpose", "stale_version", "cross_enrollment",
     "cross_group_enrollment", "cross_plan", "other_actor", "expired"],
)
def test_assignment_refuses_every_token_but_a_current_one(app, client, monkeypatch, kind):
    w = _login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        same_group = fx.enrollment(db.session.get(Group, w["group_id"]))
        other_group = fx.enrollment()
        other_pp = fx.active_plan(actor, name="Other plan").public_id
        second = fx.admin("second@example.com")
        genuine = tokens.make_token(tokens.PURPOSE_ASSIGN, **_assign_state(w))
        middle = len(genuine) // 2
        token = {
            "missing": "",
            "forged": "forged",
            "tampered": genuine[:middle] + ("A" if genuine[middle] != "A" else "B")
            + genuine[middle + 1:],
            "wrong_purpose": tokens.make_token(
                tokens.PURPOSE_CANCEL, actor_public_id=w["admin_public_id"],
                enrollment_public_id=w["ep"], assignment_public_id=w["pp"],
                assignment_version=2),
            "stale_version": tokens.make_token(tokens.PURPOSE_ASSIGN,
                                               **_assign_state(w, plan_version=1)),
            "cross_enrollment": tokens.make_token(
                tokens.PURPOSE_ASSIGN, **_assign_state(w, enrollment_public_id=same_group.public_id)),
            "cross_group_enrollment": tokens.make_token(
                tokens.PURPOSE_ASSIGN,
                **_assign_state(w, enrollment_public_id=other_group.public_id)),
            "cross_plan": tokens.make_token(tokens.PURPOSE_ASSIGN,
                                            **_assign_state(w, plan_public_id=other_pp)),
            "other_actor": tokens.make_token(
                tokens.PURPOSE_ASSIGN, **_assign_state(w, actor_public_id=second.public_id)),
            "expired": genuine,
        }[kind]
    if kind == "expired":
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
    response = fx.assign(client, w["gp"], w["ep"], w["pp"], token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.followed(client, response)
    with app.app_context():
        assert StudentFeeAssignment.query.count() == 0


@pytest.mark.parametrize(
    "kind",
    ["missing", "forged", "tampered", "wrong_purpose", "stale_version", "cross_enrollment",
     "cross_assignment", "other_actor", "expired"],
)
def test_cancellation_refuses_every_token_but_a_current_one(app, client, monkeypatch, kind):
    w = _login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        world_plan = db.session.get(FeePlan, w["plan_id"])
        ap = fx.assignment(db.session.get(Enrollment, w["enrollment_id"]), world_plan,
                           actor).public_id
        other = fx.enrollment()
        other_ap = fx.assignment(other, world_plan, actor).public_id
        second = fx.admin("second@example.com")
        state = {"actor_public_id": w["admin_public_id"], "enrollment_public_id": w["ep"],
                 "assignment_public_id": ap, "assignment_version": 1}
        genuine = tokens.make_token(tokens.PURPOSE_CANCEL, **state)
        middle = len(genuine) // 2
        token = {
            "missing": "",
            "forged": "forged",
            "tampered": genuine[:middle] + ("A" if genuine[middle] != "A" else "B")
            + genuine[middle + 1:],
            "wrong_purpose": tokens.make_token(tokens.PURPOSE_ASSIGN, **_assign_state(w)),
            "stale_version": tokens.make_token(tokens.PURPOSE_CANCEL,
                                               **dict(state, assignment_version=2)),
            "cross_enrollment": tokens.make_token(
                tokens.PURPOSE_CANCEL, **dict(state, enrollment_public_id=other.public_id)),
            "cross_assignment": tokens.make_token(
                tokens.PURPOSE_CANCEL, **dict(state, assignment_public_id=other_ap)),
            "other_actor": tokens.make_token(
                tokens.PURPOSE_CANCEL, **dict(state, actor_public_id=second.public_id)),
            "expired": genuine,
        }[kind]
        everything = fx.snapshot(StudentFeeAssignment.query.order_by(StudentFeeAssignment.id).all())
    if kind == "expired":
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
    response = fx.cancel(client, w["gp"], w["ep"], ap, token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.followed(client, response)
    with app.app_context():
        db.session.expire_all()
        assert fx.snapshot(
            StudentFeeAssignment.query.order_by(StudentFeeAssignment.id).all()) == everything


def test_a_confirmation_form_cannot_be_replayed_once_the_fee_history_moved_on(
    app, client, monkeypatch
):
    w = _login_world(app, client)
    token = fx.assign_token(client, w["gp"], w["ep"], w["pp"])
    assert fx.assign(client, w["gp"], w["ep"], w["pp"], token=token).status_code == 302
    # A double submission while the plan is assigned writes nothing.
    assert fx.assign(client, w["gp"], w["ep"], w["pp"], token=token).status_code == 302
    with app.app_context():
        (row,) = fx.stored_assignments(w["ep"])
        ap = row.public_id
    # Cancelled at a later second than the form was rendered in.
    later = _now_plus(2)
    monkeypatch.setattr(routes, "_write_moment", lambda: later)
    assert fx.cancel(client, w["gp"], w["ep"], ap).status_code == 302
    monkeypatch.undo()
    before = fx.assignments_snapshot(app, w["ep"])
    assert [entry[3] for entry in before] == [fx.CANCELLED]

    response = fx.assign(client, w["gp"], w["ep"], w["pp"], token=token)
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.followed(client, response)
    assert fx.assignments_snapshot(app, w["ep"]) == before


# ===========================================================================
# The lock chain -- structural
# ===========================================================================


def _record_lock_requests(monkeypatch):
    requested = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        requested.append(self.column_descriptions[0]["entity"].__tablename__)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy)
    return requested


def _record_row_reads(rows):
    """``(table, id)`` for every by-id row read of the tables whose lock
    order matters, in execution order."""

    def _rec(conn, cursor, statement, parameters, context, executemany):
        flat = " ".join(statement.split())
        for table in ("users", "fee_plan_items", "student_fee_assignments"):
            if flat.startswith("SELECT") and f"WHERE {table}.id = ?" in flat:
                rows.append((table, parameters[0]))

    return _rec


def test_assignment_takes_the_documented_lock_order(app, client, monkeypatch):
    w = _login_world(app, client)
    with app.app_context():
        actor = db.session.get(User, w["admin_id"])
        owner = db.session.get(Enrollment, w["enrollment_id"])
        target = plans.plan(actor, name="Target", status=plans.ACTIVE, version=2)
        elsewhere = plans.plan(actor, name="Elsewhere", status=plans.ACTIVE, version=2)
        # Interleave rows so display order, label order and id order differ.
        zed = plans.item(target, label="Zed", kind="course")
        plans.item(elsewhere, label="Elsewhere")
        alpha = plans.item(target, label="Alpha", kind="registration")
        plans.item(target, label="Old", status=plans.ITEM_REMOVED)
        earlier = fx.assignment(owner, elsewhere, actor, status=fx.CANCELLED)
        fx.assignment(fx.enrollment(), elsewhere, actor)
        later = fx.assignment(owner, target, actor, status=fx.CANCELLED)
        tpp = target.public_id
        item_ids, assignment_ids = [zed.id, alpha.id], [earlier.id, later.id]
    token = fx.assign_token(client, w["gp"], w["ep"], tpp)

    requested = _record_lock_requests(monkeypatch)
    rows = []
    recorder = _record_row_reads(rows)
    sa_event.listen(db.engine, "before_cursor_execute", recorder)
    try:
        response = fx.assign(client, w["gp"], w["ep"], tpp, token=token)
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", recorder)
    locked = requested[:]
    monkeypatch.undo()

    assert response.status_code == 302
    assert fx.ASSIGNED_OK_TEXT in fx.followed(client, response)
    assert locked == _PREFIX + ["fee_plans", "fee_plan_items", "fee_plan_items",
                                "student_fee_assignments", "student_fee_assignments"]
    assert [row_id for table, row_id in rows if table == "users"][-2:] == [
        w["student_id"], w["admin_id"]]
    assert [row_id for table, row_id in rows if table == "fee_plan_items"] == sorted(item_ids)
    assert [row_id for table, row_id in rows
            if table == "student_fee_assignments"] == sorted(assignment_ids)


def test_cancellation_takes_the_documented_lock_order(app, client, monkeypatch):
    w = _login_world(app, client)
    with app.app_context():
        row = fx.assignment(db.session.get(Enrollment, w["enrollment_id"]),
                            db.session.get(FeePlan, w["plan_id"]),
                            db.session.get(User, w["admin_id"]))
        ap, assignment_id = row.public_id, row.id
    token = fx.cancel_token(client, w["gp"], w["ep"], ap)

    requested = _record_lock_requests(monkeypatch)
    rows = []
    recorder = _record_row_reads(rows)
    sa_event.listen(db.engine, "before_cursor_execute", recorder)
    try:
        response = fx.cancel(client, w["gp"], w["ep"], ap, token=token)
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", recorder)
    locked = requested[:]
    monkeypatch.undo()

    assert response.status_code == 302
    assert fx.CANCELLED_OK_TEXT in fx.followed(client, response)
    assert locked == _PREFIX + ["student_fee_assignments"]
    assert [row_id for table, row_id in rows if table == "users"][-2:] == [
        w["student_id"], w["admin_id"]]
    assert [row_id for table, row_id in rows if table == "student_fee_assignments"] == [
        assignment_id]


def _chain(w, **links):
    return lock_fee_assignment_chain(w["gp"], w["term_id"], w["level_id"], w["course_id"],
                                     w["student_id"], w["enrollment_id"], w["admin_id"], **links)


def test_the_chain_stops_where_the_arguments_stop(app, monkeypatch):
    w = fx.world(app)
    with app.app_context():
        requested = _record_lock_requests(monkeypatch)
        _chain(w)
        assert requested == _PREFIX
        requested.clear()
        _chain(w, plan_id=w["plan_id"])
        assert requested == _PREFIX + ["fee_plans"]
        requested.clear()
        locks = _chain(w, plan_id=w["plan_id"], include_plan_items=True,
                       include_enrollment_assignments=True)
        assert requested == _PREFIX + ["fee_plans", "fee_plan_items", "fee_plan_items"]
        assert [item.label for item in locked_active_items(locks)] == ["Registration", "Course"]
        assert locks.assignments == {}
        assert (locks.group.id, locks.student.id, locks.enrollment.id, locks.actor.id) == (
            w["group_id"], w["student_id"], w["enrollment_id"], w["admin_id"])


def test_the_chain_resets_the_transaction_exactly_once(app):
    w = fx.world(app)
    rollbacks = []
    with app.app_context():
        def _rec(conn):
            rollbacks.append(1)

        sa_event.listen(db.engine, "rollback", _rec)
        try:
            _chain(w, plan_id=w["plan_id"], include_plan_items=True,
                   include_enrollment_assignments=True)
        finally:
            sa_event.remove(db.engine, "rollback", _rec)
    assert len(rollbacks) <= 1


def _item(label="Course", kind="course", amount=Decimal("10.0000"), status="active"):
    return SimpleNamespace(label=label, kind=kind, amount=amount, status=status)


def test_the_item_rule_accepts_only_a_complete_valid_definition():
    assert fee_plan_items_valid([_item()])
    assert fee_plan_items_valid([_item(label=f"Line {index}") for index in range(20)])
    assert not fee_plan_items_valid([])
    assert not fee_plan_items_valid([_item(label=f"Line {index}") for index in range(21)])
    assert not fee_plan_items_valid([_item(label="Fee"), _item(label="fee")])
    assert not fee_plan_items_valid([_item(status="removed")])
    assert not fee_plan_items_valid([_item(kind="discount")])
    assert not fee_plan_items_valid([_item(label=" padded")])
    assert not fee_plan_items_valid([_item(label="")])
    for amount in (Decimal("0"), Decimal("100000"), Decimal("1.00001"), 10.5, None):
        assert not fee_plan_items_valid([_item(amount=amount)]), amount


def test_a_plan_is_assignable_only_while_active_and_frozen():
    moment = datetime(2026, 5, 2, 9, 0, 0)

    def plan(status, first_at=moment, first_by=1):
        return SimpleNamespace(status=status, first_activated_at=first_at,
                               first_activated_by_id=first_by)

    assert fee_plan_assignable(plan("active"))
    assert not fee_plan_assignable(None)
    assert not fee_plan_assignable(plan("draft", None, None))
    assert not fee_plan_assignable(plan("archived"))
    assert not fee_plan_assignable(plan("active", None, None))
    assert not fee_plan_assignable(plan("active", moment, None))


def test_history_helpers():
    old, new = datetime(2026, 5, 1), datetime(2026, 5, 3)
    rows = [SimpleNamespace(status="cancelled", updated_at=new),
            SimpleNamespace(status="assigned", updated_at=old)]
    assert latest_change(rows) == new and latest_change([]) is None
    assert assigned_rows(rows) == [rows[1]]


# ===========================================================================
# Post-lock revalidation -- a competing change at the lock boundary
# ===========================================================================


def _inject_before_locks(monkeypatch, change):
    real_chain = routes.lock_fee_assignment_chain

    def chain(*args, **kwargs):
        change()
        db.session.commit()
        return real_chain(*args, **kwargs)

    monkeypatch.setattr(routes, "lock_fee_assignment_chain", chain)


def _insert_assignment(w, moment):
    db.session.add(StudentFeeAssignment(
        enrollment_id=w["enrollment_id"], fee_plan_id=w["plan_id"], status=fx.ASSIGNED,
        assigned_at=moment, assigned_by_id=w["admin_id"], version=1, created_at=moment,
        updated_at=moment))


def _archive_plan(w, version):
    moment = datetime(2026, 5, 4, 9, 0, 0)
    db.session.execute(update(FeePlan).where(FeePlan.id == w["plan_id"]).values(
        status="archived", status_changed_at=moment, status_changed_by_id=w["admin_id"],
        updated_at=moment, version=version))


def _create_change(kind, w, spare):
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
    elif kind == "student_suspended":
        db.session.execute(update(User).where(User.id == w["student_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif kind == "enrollment_withdrawn":
        db.session.execute(update(Enrollment).where(Enrollment.id == w["enrollment_id"]).values(
            status=fx.WITHDRAWN))
    elif kind in ("group_archived", "course_archived", "level_archived", "term_archived"):
        model, key = {"group_archived": (Group, "group_id"),
                      "course_archived": (Course, "course_id"),
                      "level_archived": (Level, "level_id"),
                      "term_archived": (AcademicTerm, "term_id")}[kind]
        db.session.execute(update(model).where(model.id == w[key]).values(status=fx.ARCHIVED))
    elif kind == "group_retargeted":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            course_id=spare["course_id"]))
    elif kind == "plan_archived":
        _archive_plan(w, version=3)
    elif kind == "plan_archived_quietly":
        _archive_plan(w, version=2)
    elif kind == "items_removed_quietly":
        db.session.execute(update(FeePlanItem).where(FeePlanItem.fee_plan_id == w["plan_id"]).values(
            status="removed", removed_at=plans.REMOVED, removed_by_id=w["admin_id"],
            updated_at=plans.REMOVED))
    elif kind == "duplicate_label_quietly":
        db.session.add(FeePlanItem(fee_plan_id=w["plan_id"], kind="course", label="course",
                                   amount="1", status="active", version=1,
                                   created_at=plans.CREATED, updated_at=plans.CREATED))
    elif kind == "assigned_meanwhile":
        _insert_assignment(w, fx.ASSIGNED_AT)
    elif kind == "assigned_just_now":
        _insert_assignment(w, _now_plus(2))
    else:  # pragma: no cover
        raise AssertionError(kind)


_CREATE_OUTCOMES = {
    "admin_suspended": 404,
    "admin_demoted": 404,
    "student_role_changed": 404,
    "enrollment_moved": 404,
    "student_suspended": fx.STUDENT_INACTIVE_TEXT,
    "enrollment_withdrawn": fx.ENROLLMENT_INACTIVE_TEXT,
    "group_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "course_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "level_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "term_archived": fx.ACADEMIC_INACTIVE_TEXT,
    "group_retargeted": fx.STALE_TEXT,
    "plan_archived": fx.STALE_TEXT,
    "plan_archived_quietly": fx.PLAN_UNAVAILABLE_TEXT,
    "items_removed_quietly": fx.ITEMS_INVALID_TEXT,
    "duplicate_label_quietly": fx.ITEMS_INVALID_TEXT,
    "assigned_meanwhile": fx.ALREADY_ASSIGNED_TEXT,
    "assigned_just_now": fx.STALE_TEXT,
}


@pytest.mark.parametrize("kind", sorted(_CREATE_OUTCOMES))
def test_assignment_re_proves_every_rule_against_the_locked_rows(app, client, monkeypatch, kind):
    w = _login_world(app, client)
    with app.app_context():
        spare_group = fx.group()
        spare = {"group_id": spare_group.id, "course_id": spare_group.course_id}
    token = fx.assign_token(client, w["gp"], w["ep"], w["pp"])
    assert token
    _inject_before_locks(monkeypatch, lambda: _create_change(kind, w, spare))
    response = fx.assign(client, w["gp"], w["ep"], w["pp"], token=token)
    monkeypatch.undo()

    expected = _CREATE_OUTCOMES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        if kind not in ("admin_suspended", "admin_demoted"):
            assert expected in fx.followed(client, response)
    with app.app_context():
        rows = StudentFeeAssignment.query.all()
        injected = 1 if kind.startswith("assigned_") else 0
        assert len(rows) == injected
        assert all(row.assigned_by_id == w["admin_id"] for row in rows)


def _cancel_change(kind, w, ap, spare):
    if kind == "admin_suspended":
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
    elif kind == "enrollment_moved":
        db.session.execute(update(Enrollment).where(Enrollment.id == w["enrollment_id"]).values(
            group_id=spare["group_id"]))
    elif kind == "assignment_moved":
        db.session.execute(update(StudentFeeAssignment).where(
            StudentFeeAssignment.public_id == ap).values(enrollment_id=spare["enrollment_id"]))
    elif kind == "cancelled_elsewhere":
        db.session.execute(update(StudentFeeAssignment).where(
            StudentFeeAssignment.public_id == ap).values(
            status=fx.CANCELLED, cancelled_at=fx.CANCELLED_AT, cancelled_by_id=w["admin_id"],
            updated_at=fx.CANCELLED_AT, version=2))
    elif kind == "version_moved":
        db.session.execute(update(StudentFeeAssignment).where(
            StudentFeeAssignment.public_id == ap).values(version=2))
    elif kind == "group_archived":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            status=fx.ARCHIVED))
    elif kind == "group_retargeted":
        db.session.execute(update(Group).where(Group.id == w["group_id"]).values(
            course_id=spare["course_id"]))
    else:  # pragma: no cover
        raise AssertionError(kind)


_CANCEL_OUTCOMES = {
    "admin_suspended": (404, (fx.ASSIGNED, 1)),
    "enrollment_moved": (404, (fx.ASSIGNED, 1)),
    "assignment_moved": (404, (fx.ASSIGNED, 1)),
    "cancelled_elsewhere": (fx.ALREADY_CANCELLED_TEXT, (fx.CANCELLED, 2)),
    "version_moved": (fx.STALE_TEXT, (fx.ASSIGNED, 2)),
    "group_retargeted": (fx.STALE_TEXT, (fx.ASSIGNED, 1)),
    "group_archived": (fx.CANCELLED_OK_TEXT, (fx.CANCELLED, 2)),
}


@pytest.mark.parametrize("kind", sorted(_CANCEL_OUTCOMES))
def test_cancellation_re_proves_its_rules_against_the_locked_rows(app, client, monkeypatch, kind):
    w = _login_world(app, client)
    with app.app_context():
        row = fx.assignment(db.session.get(Enrollment, w["enrollment_id"]),
                            db.session.get(FeePlan, w["plan_id"]),
                            db.session.get(User, w["admin_id"]))
        ap = row.public_id
        spare_enrollment = fx.enrollment()
        spare_group = db.session.get(Group, spare_enrollment.group_id)
        spare = {"group_id": spare_group.id, "course_id": spare_group.course_id,
                 "enrollment_id": spare_enrollment.id}
    token = fx.cancel_token(client, w["gp"], w["ep"], ap)
    assert token
    _inject_before_locks(monkeypatch, lambda: _cancel_change(kind, w, ap, spare))
    response = fx.cancel(client, w["gp"], w["ep"], ap, token=token)
    monkeypatch.undo()

    expected, final_state = _CANCEL_OUTCOMES[kind]
    if expected == 404:
        assert response.status_code == 404
    else:
        assert response.status_code == 302
        assert expected in fx.followed(client, response)
    with app.app_context():
        db.session.expire_all()
        stored = StudentFeeAssignment.query.filter_by(public_id=ap).one()
        assert (stored.status, stored.version) == final_state
        if kind == "cancelled_elsewhere":
            assert stored.cancelled_at == fx.CANCELLED_AT


# ===========================================================================
# IntegrityError recovery
# ===========================================================================


def _failing_commit():
    raise IntegrityError("INSERT INTO student_fee_assignments ...", {},
                         Exception("Duplicate entry 'x' for key"))


@pytest.mark.parametrize("action", ["assign", "cancel"])
def test_an_integrity_error_is_rolled_back_and_reported_generically(
    app, client, monkeypatch, action
):
    w = _login_world(app, client)
    ap = None
    if action == "cancel":
        with app.app_context():
            ap = fx.assignment(db.session.get(Enrollment, w["enrollment_id"]),
                               db.session.get(FeePlan, w["plan_id"]),
                               db.session.get(User, w["admin_id"])).public_id
        token = fx.cancel_token(client, w["gp"], w["ep"], ap)
    else:
        token = fx.assign_token(client, w["gp"], w["ep"], w["pp"])
    before = fx.assignments_snapshot(app, w["ep"])
    monkeypatch.setattr(routes.db.session, "commit", _failing_commit)
    if action == "cancel":
        response = fx.cancel(client, w["gp"], w["ep"], ap, token=token)
    else:
        response = fx.assign(client, w["gp"], w["ep"], w["pp"], token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    html = fx.followed(client, response)
    assert fx.INTEGRITY_TEXT in html
    for leaked in ("INSERT", "Duplicate entry", "IntegrityError", "sqlite", "pymysql"):
        assert leaked not in html, leaked
    assert fx.assignments_snapshot(app, w["ep"]) == before


def test_recovery_re_authorizes_from_current_state(app, client, monkeypatch):
    w = _login_world(app, client)
    token = fx.assign_token(client, w["gp"], w["ep"], w["pp"])
    real_commit = db.session.commit

    def commit_that_loses_access():
        db.session.rollback()
        db.session.execute(update(User).where(User.id == w["admin_id"]).values(
            status=UserStatus.SUSPENDED.value))
        real_commit()
        _failing_commit()

    monkeypatch.setattr(routes.db.session, "commit", commit_that_loses_access)
    response = fx.assign(client, w["gp"], w["ep"], w["pp"], token=token)
    monkeypatch.undo()
    assert response.status_code == 404
    with app.app_context():
        assert StudentFeeAssignment.query.count() == 0


def test_a_real_constraint_failure_reaches_the_database_and_is_handled(app, client, monkeypatch):
    """The lifecycle CHECK itself refuses the insert -- the final defense,
    exercised for real rather than simulated."""
    w = _login_world(app, client)
    token = fx.assign_token(client, w["gp"], w["ep"], w["pp"])
    real = routes.StudentFeeAssignment

    def corrupted(**kwargs):
        kwargs["cancelled_at"] = kwargs["assigned_at"]
        return real(**kwargs)

    monkeypatch.setattr(routes, "StudentFeeAssignment", corrupted)
    response = fx.assign(client, w["gp"], w["ep"], w["pp"], token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    html = fx.followed(client, response)
    assert fx.INTEGRITY_TEXT in html
    assert not re.search(r"CHECK|constraint|sqlite", html, re.I)
    with app.app_context():
        assert StudentFeeAssignment.query.count() == 0
