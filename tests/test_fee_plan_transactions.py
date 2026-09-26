"""Phase 5 / M02 -- tokens, the lock chain, post-lock revalidation and
``IntegrityError`` recovery for the fee plan catalogue.

SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so the lock tests
assert what the code *requests* -- which rows, in which order -- and the race
tests inject a competing change at the exact transaction boundary (between
the pre-lock read and the lock chain) to prove the write re-decides against
the locked rows. None of this proves real InnoDB blocking.
"""

import re

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

import app.blueprints.admin.fee_plans as routes
import tests.fee_plan_fixtures as fx
import tests.structural_checks as sc
from app.extensions import db
from app.models import FeePlan, FeePlanItem, User, UserRole, UserStatus
from app.services import fee_plan_tokens as tokens
from app.services.fee_plan_transactions import (
    administrator_authz_broken,
    label_in_use,
    labels_are_unique,
    lock_fee_plan_chain,
    locked_active_items,
)


def _admin(client, app, email="admin@example.com"):
    with app.app_context():
        actor_id = fx.admin(email).id
    fx.login_as(client, email)
    return actor_id


def _draft(app, email="admin@example.com", labels=("Registration", "Course"), version=1):
    with app.app_context():
        creator = User.query.filter_by(email=email).one()
        owner = fx.plan(creator, version=version)
        rows = [fx.item(owner, label=label, amount="75.25") for label in labels]
        return owner.public_id, [row.public_id for row in rows]


def _payloads(actor, plan, item):
    plan_state = {"actor_public_id": actor, "plan_public_id": plan, "plan_version": 1,
                  "plan_status": "draft"}
    item_state = dict(plan_state, item_public_id=item, item_version=1, item_status="active")
    return {
        tokens.PURPOSE_CREATE: {"actor_public_id": actor},
        tokens.PURPOSE_EDIT: plan_state,
        tokens.PURPOSE_ITEM_CREATE: plan_state,
        tokens.PURPOSE_ITEM_EDIT: item_state,
        tokens.PURPOSE_ITEM_REMOVE: item_state,
        tokens.PURPOSE_ACTIVATE: plan_state,
        tokens.PURPOSE_ARCHIVE: plan_state,
        tokens.PURPOSE_REACTIVATE: plan_state,
    }


# ===========================================================================
# Tokens
# ===========================================================================


def test_the_eight_purposes_cannot_be_replayed_as_one_another(app):
    with app.app_context():
        payloads = _payloads("a" * 36, "p" * 36, "i" * 36)
        assert set(payloads) == set(tokens.PURPOSES)
        for purpose, payload in payloads.items():
            token = tokens.make_token(purpose, **payload)
            assert tokens.load_token(token, purpose) is not None
            for other in tokens.PURPOSES:
                if other != purpose:
                    assert tokens.load_token(token, other) is None, (purpose, other)


def test_a_token_from_another_milestone_is_not_a_fee_plan_token(app):
    from app.services.calendar_tokens import make_token as calendar_token
    from app.services.lesson_progress_tokens import make_progress_token

    with app.app_context():
        foreign = [
            calendar_token("admin", "calendar-event-create", actor_public_id="a" * 36),
            calendar_token("admin", "calendar-event-cancel", actor_public_id="a" * 36,
                           event_public_id="p" * 36, event_version=1,
                           event_status="scheduled"),
            make_progress_token("a" * 36, "g", "u", "l", "complete", 1),
        ]
        for token in foreign:
            for purpose in tokens.PURPOSES:
                assert tokens.load_token(token, purpose) is None


def test_tampered_malformed_and_wrongly_shaped_tokens_are_refused(app):
    with app.app_context():
        payload = _payloads("a" * 36, "p" * 36, "i" * 36)[tokens.PURPOSE_ACTIVATE]
        token = tokens.make_token(tokens.PURPOSE_ACTIVATE, **payload)
        middle = len(token) // 3
        tampered = token[:middle] + ("x" if token[middle] != "x" else "y") + token[middle + 1:]
        serializer = tokens._serializer(tokens.PURPOSE_ACTIVATE)
        for bad in (
            None, "", 42, tampered, token + "x", "x" * 3000, "not.a.token",
            serializer.dumps(dict(payload, purpose=tokens.PURPOSE_ACTIVATE, extra="1")),
            serializer.dumps({"purpose": tokens.PURPOSE_ACTIVATE, "actor_public_id": "a"}),
            serializer.dumps(dict(payload, purpose=tokens.PURPOSE_ARCHIVE)),
            serializer.dumps(dict(payload, purpose=tokens.PURPOSE_ACTIVATE, plan_version=True)),
            serializer.dumps(dict(payload, purpose=tokens.PURPOSE_ACTIVATE, plan_version=0)),
            serializer.dumps(dict(payload, purpose=tokens.PURPOSE_ACTIVATE, plan_version="1")),
            serializer.dumps(dict(payload, purpose=tokens.PURPOSE_ACTIVATE, plan_version=2**31)),
            serializer.dumps(dict(payload, purpose=tokens.PURPOSE_ACTIVATE, plan_status="")),
            serializer.dumps(dict(payload, purpose=tokens.PURPOSE_ACTIVATE, plan_public_id=7)),
            serializer.dumps(["not", "a", "dict"]),
        ):
            assert tokens.load_token(bad, tokens.PURPOSE_ACTIVATE) is None, bad
            assert tokens.token_is_stale(bad, tokens.PURPOSE_ACTIVATE, **payload)
        assert not tokens.token_is_stale(token, tokens.PURPOSE_ACTIVATE, **payload)
        assert tokens.token_is_stale(token, tokens.PURPOSE_ACTIVATE,
                                     **dict(payload, plan_version=2))


def test_an_expired_token_is_refused(app, monkeypatch):
    with app.app_context():
        payload = _payloads("a" * 36, "p" * 36, "i" * 36)[tokens.PURPOSE_EDIT]
        token = tokens.make_token(tokens.PURPOSE_EDIT, **payload)
        assert tokens.load_token(token, tokens.PURPOSE_EDIT) is not None
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
        assert tokens.load_token(token, tokens.PURPOSE_EDIT) is None


def test_a_rendered_token_carries_no_text_money_or_internal_id(app, client):
    _admin(client, app)
    with app.app_context():
        actor = User.query.one()
        # Filler plans with items first, so neither internal id can coincide
        # with a version number -- otherwise "the id is not in the payload"
        # would be untestable.
        for index in range(6):
            fx.item(fx.plan(actor, name=f"Filler {index}"), label=f"Filler {index}")
        owner = fx.plan(actor, name="Secret plan", description="Private words", version=3)
        row = fx.item(owner, label="Hidden label", amount="4321.5")
        pp, ip, plan_id, item_id = owner.public_id, row.public_id, owner.id, row.id
    rendered = {
        tokens.PURPOSE_EDIT: fx.token_from(client, fx.edit_url(pp)),
        tokens.PURPOSE_ITEM_CREATE: fx.token_from(client, fx.item_new_url(pp)),
        tokens.PURPOSE_ITEM_EDIT: fx.token_from(client, fx.item_edit_url(pp, ip)),
        tokens.PURPOSE_ITEM_REMOVE: fx.token_from(client, fx.detail_url(pp),
                                                  fx.item_remove_url(pp, ip)),
        tokens.PURPOSE_ACTIVATE: fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp)),
        tokens.PURPOSE_ARCHIVE: fx.token_from(client, fx.detail_url(pp), fx.archive_url(pp)),
    }
    with app.app_context():
        for purpose, token in rendered.items():
            payload = tokens.load_token(token, purpose)
            assert payload is not None, purpose
            # By key and exact value, never as one string: a public id is
            # random hexadecimal and contains "4321" about once in two
            # thousand ids. Each public id is exactly a UUID; every other
            # value is searched.
            for key, value in payload.items():
                if key.endswith("_public_id"):
                    assert sc.is_public_id(value), (purpose, key, value)
                    continue
                for secret in ("Secret plan", "Private words", "Hidden label", "4321"):
                    assert secret not in str(value), (purpose, key, secret)
            assert payload["plan_public_id"] == pp
            assert payload["plan_version"] == 3
            values = set(map(str, payload.values()))
            assert str(plan_id) not in values and str(item_id) not in values


# ===========================================================================
# Tokens at the routes: forged, foreign, stale and replayed
# ===========================================================================


def test_forged_tokens_are_refused_on_every_mutation(app, client):
    _admin(client, app)
    pp, (ip, _) = _draft(app)
    # Each attempt is checked before the next is made: a flashed message is
    # consumed by the first page that renders it.
    for attempt in (
        lambda: client.post(fx.NEW_URL, data=fx.plan_form(name="New", token="forged")),
        lambda: client.post(fx.edit_url(pp), data=fx.plan_form(name="Renamed", token="forged")),
        lambda: client.post(fx.item_new_url(pp), data=fx.item_form(label="New", token="forged")),
        lambda: client.post(fx.item_edit_url(pp, ip),
                            data=fx.item_form(label="X", token="forged")),
        lambda: fx.remove_item(client, pp, ip, token="forged"),
        lambda: fx.activate(client, pp, token="forged"),
        lambda: fx.archive(client, pp, token=""),
    ):
        response = attempt()
        assert response.status_code == 302
        assert fx.STALE_TEXT in fx.page(client, response.headers["Location"])
    with app.app_context():
        owner = fx.stored_plan(pp)
        assert owner.name != "Renamed"
        assert owner.version == 1 and owner.status == fx.DRAFT
        assert FeePlan.query.count() == 1 and FeePlanItem.query.count() == 2
        assert fx.stored_item(ip).status == fx.ITEM_ACTIVE


def test_an_invalid_form_with_a_stale_token_is_rejected_not_refreshed(app, client):
    """Re-rendering an invalid form with a freshly minted token would let the
    next submit overwrite a change made meanwhile. A stale submission is
    rejected instead, even when the form is also invalid."""
    _admin(client, app)
    pp, _ = _draft(app)
    token = fx.token_from(client, fx.edit_url(pp))
    assert fx.edit_plan(client, pp, name="Changed meanwhile").status_code == 302
    response = client.post(fx.edit_url(pp), data=fx.plan_form(name="", token=token))
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.page(client, fx.edit_url(pp))
    with app.app_context():
        assert fx.stored_plan(pp).name == "Changed meanwhile"


def test_another_administrators_token_is_refused(app, client):
    _admin(client, app)
    pp, _ = _draft(app)
    token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))
    create_token = fx.token_from(client, fx.NEW_URL)
    with app.app_context():
        fx.admin("second@example.com")
    fx.login_as(client, "second@example.com")
    fx.activate(client, pp, token=token)
    client.post(fx.NEW_URL, data=fx.plan_form(name="Other", token=create_token))
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT
        assert FeePlan.query.count() == 1


def test_activation_is_stale_once_anything_in_the_aggregate_changed(app, client):
    """An Administrator can only activate the definition they were shown."""
    _admin(client, app)
    pp, (ip, _) = _draft(app)
    token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))
    assert fx.edit_item(client, pp, ip, label="Registration", amount="999").status_code == 302
    fx.activate(client, pp, token=token)
    assert fx.STALE_TEXT in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


@pytest.mark.parametrize("change", ["add", "remove"])
def test_other_item_changes_also_stale_the_activation_token(app, client, change):
    _admin(client, app)
    pp, (ip, _) = _draft(app)
    token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))
    if change == "add":
        assert fx.add_item(client, pp, label="Books").status_code == 302
    else:
        assert fx.remove_item(client, pp, ip).status_code == 302
    fx.activate(client, pp, token=token)
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


def test_replayed_tokens_change_nothing(app, client):
    _admin(client, app)
    pp, (ip, other) = _draft(app)

    item_token = fx.token_from(client, fx.item_new_url(pp))
    data = fx.item_form(label="Books", amount="10", token=item_token)
    assert client.post(fx.item_new_url(pp), data=data).status_code == 302
    client.post(fx.item_new_url(pp), data=dict(data, label="Books again"))

    remove_token = fx.token_from(client, fx.detail_url(pp), fx.item_remove_url(pp, ip))
    assert fx.remove_item(client, pp, ip, token=remove_token).status_code == 302
    fx.remove_item(client, pp, ip, token=remove_token)

    activate_token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))
    assert fx.activate(client, pp, token=activate_token).status_code == 302
    archive_token = fx.token_from(client, fx.detail_url(pp), fx.archive_url(pp))
    assert fx.archive(client, pp, token=archive_token).status_code == 302
    assert fx.reactivate(client, pp).status_code == 302
    fx.archive(client, pp, token=archive_token)
    fx.activate(client, pp, token=activate_token)

    with app.app_context():
        owner = fx.stored_plan(pp)
        assert owner.status == fx.ACTIVE and owner.version == 6
        assert FeePlanItem.query.filter_by(label="Books again").count() == 0
        assert FeePlanItem.query.count() == 3
        assert fx.stored_item(ip).version == 2


def test_an_expired_page_is_refused_at_the_route(app, client, monkeypatch):
    _admin(client, app)
    pp, _ = _draft(app)
    token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))
    monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
    fx.activate(client, pp, token=token)
    monkeypatch.undo()
    assert fx.STALE_TEXT in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


# ===========================================================================
# The lock chain -- structural
# ===========================================================================


def _record_lock_requests(monkeypatch):
    """Every ``with_for_update`` request, as the locked table name, in order."""
    requested = []
    original = Query.with_for_update

    def spy(self, *args, **kwargs):
        requested.append(self.column_descriptions[0]["entity"].__tablename__)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy)
    return requested


def _record_item_lock_ids(app):
    ids = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        flat = " ".join(statement.split())
        if flat.startswith("SELECT") and "WHERE fee_plan_items.id = ?" in flat:
            ids.append(parameters[0])

    return ids, _rec


def test_the_chain_requests_actor_plan_then_items_in_ascending_id(app, monkeypatch):
    with app.app_context():
        actor = fx.admin()
        owner = fx.plan(actor)
        other = fx.plan(actor)
        # Interleave rows so display order, label order and id order differ.
        zed = fx.item(owner, label="Zed")
        fx.item(other, label="Elsewhere")
        alpha = fx.item(owner, label="Alpha")
        removed = fx.item(owner, label="Old", status=fx.ITEM_REMOVED)
        mid = fx.item(owner, label="Mid")
        # Every scalar is resolved before recording starts, so an attribute
        # refresh cannot be mistaken for part of the lock order.
        actor_id, plan_id = actor.id, owner.id
        zed_id, alpha_id, mid_id, removed_id = zed.id, alpha.id, mid.id, removed.id

        requested = _record_lock_requests(monkeypatch)
        ids, recorder = _record_item_lock_ids(app)
        sa_event.listen(db.engine, "before_cursor_execute", recorder)
        try:
            locks = lock_fee_plan_chain(actor_id, plan_id=plan_id, item_ids=(mid_id,),
                                        include_active_items=True)
        finally:
            sa_event.remove(db.engine, "before_cursor_execute", recorder)
        assert requested == ["users", "fee_plans"] + ["fee_plan_items"] * 3
        assert ids == sorted(ids) == [zed_id, alpha_id, mid_id]
        assert removed_id not in ids
        assert [row.label for row in locked_active_items(locks)] == ["Zed", "Alpha", "Mid"]
        for academic in ("academic_terms", "levels", "courses", "groups"):
            assert academic not in requested


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({}, ["users"]),
        ({"plan": True}, ["users", "fee_plans"]),
        ({"plan": True, "one_item": True}, ["users", "fee_plans", "fee_plan_items"]),
    ],
)
def test_the_chain_stops_where_the_arguments_stop(app, monkeypatch, kwargs, expected):
    with app.app_context():
        actor = fx.admin()
        owner = fx.plan(actor)
        row = fx.item(owner)
        fx.item(owner)
        requested = _record_lock_requests(monkeypatch)
        lock_fee_plan_chain(
            actor.id,
            plan_id=owner.id if kwargs.get("plan") else None,
            item_ids=(row.id,) if kwargs.get("one_item") else (),
        )
        assert requested == expected


def test_the_chain_resets_the_transaction_exactly_once(app):
    rollbacks = []
    with app.app_context():
        actor = fx.admin()
        owner = fx.plan(actor)
        fx.item(owner)

        def _rec(conn):
            rollbacks.append(1)

        sa_event.listen(db.engine, "rollback", _rec)
        try:
            lock_fee_plan_chain(actor.id, plan_id=owner.id, include_active_items=True)
        finally:
            sa_event.remove(db.engine, "rollback", _rec)
    assert len(rollbacks) <= 1


@pytest.mark.parametrize(
    "action, expected",
    [
        ("create", ["users"]),
        ("edit", ["users", "fee_plans"]),
        ("item_create", ["users", "fee_plans", "fee_plan_items", "fee_plan_items"]),
        ("item_edit", ["users", "fee_plans", "fee_plan_items", "fee_plan_items"]),
        ("item_remove", ["users", "fee_plans", "fee_plan_items"]),
        ("activate", ["users", "fee_plans", "fee_plan_items", "fee_plan_items"]),
        ("archive", ["users", "fee_plans"]),
    ],
)
def test_each_route_requests_its_documented_locks(app, client, monkeypatch, action, expected):
    _admin(client, app)
    pp, (ip, _) = _draft(app)
    form_url, data = {
        "create": (fx.NEW_URL, fx.plan_form(name="Fresh")),
        "edit": (fx.edit_url(pp), fx.plan_form(name="Renamed")),
        "item_create": (fx.item_new_url(pp), fx.item_form(label="Books")),
        "item_edit": (fx.item_edit_url(pp, ip), fx.item_form(label="Registration", amount="5")),
        "item_remove": (None, None),
        "activate": (None, None),
        "archive": (None, None),
    }[action]
    if form_url is not None:
        data[fx.STATE_FIELD] = fx.token_from(client, form_url)
    requested = _record_lock_requests(monkeypatch)
    if action == "item_remove":
        response = fx.remove_item(client, pp, ip)
        requested_now = requested[:]
    elif action in ("activate", "archive"):
        response = getattr(fx, action)(client, pp)
        requested_now = requested[:]
    else:
        response = client.post(form_url, data=data)
        requested_now = requested[:]
    assert response.status_code == 302
    assert requested_now == expected


def test_the_locked_actor_is_re_checked_rather_than_trusted(app):
    with app.app_context():
        actor = fx.admin()
        assert not administrator_authz_broken(lock_fee_plan_chain(actor.id))
        actor.status = UserStatus.SUSPENDED.value
        db.session.commit()
        assert administrator_authz_broken(lock_fee_plan_chain(actor.id))
        actor.status = UserStatus.ACTIVE.value
        actor.role = UserRole.TEACHER.value
        db.session.commit()
        assert administrator_authz_broken(lock_fee_plan_chain(actor.id))
        assert administrator_authz_broken(lock_fee_plan_chain(None))


def test_label_rules_compare_locked_rows_case_insensitively(app):
    with app.app_context():
        owner = fx.plan(fx.admin())
        first = fx.item(owner, label="Course fee")
        second = fx.item(owner, label="Books")
        rows = [first, second]
        assert label_in_use(rows, "COURSE FEE")
        assert not label_in_use(rows, "Course fee", exclude_item_id=first.id)
        assert labels_are_unique(rows)
        assert not labels_are_unique(rows + [fx.item(owner, label="books")])


# ===========================================================================
# Post-lock revalidation -- a competing change at the lock boundary
# ===========================================================================


def _inject_before_locks(monkeypatch, change):
    """Run `change` (and commit it) immediately before the route's lock chain,
    i.e. after every pre-lock read and friendly check has already passed."""
    real_chain = routes.lock_fee_plan_chain

    def chain(*args, **kwargs):
        change()
        db.session.commit()
        return real_chain(*args, **kwargs)

    monkeypatch.setattr(routes, "lock_fee_plan_chain", chain)


def test_an_administrator_suspended_mid_request_writes_nothing(app, client, monkeypatch):
    actor_id = _admin(client, app)
    pp, _ = _draft(app)
    token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))
    _inject_before_locks(monkeypatch, lambda: db.session.execute(
        update(User).where(User.id == actor_id).values(status=UserStatus.SUSPENDED.value)))
    response = fx.activate(client, pp, token=token)
    assert response.status_code == 404
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


def test_a_plan_activated_mid_request_refuses_the_item_being_added(app, client, monkeypatch):
    actor_id = _admin(client, app)
    pp, _ = _draft(app)
    token = fx.token_from(client, fx.item_new_url(pp))

    def activate_elsewhere():
        from datetime import datetime
        moment = datetime(2026, 5, 5, 9, 0, 0)
        db.session.execute(update(FeePlan).where(FeePlan.public_id == pp).values(
            status="active", first_activated_at=moment, first_activated_by_id=actor_id,
            status_changed_at=moment, status_changed_by_id=actor_id, version=2,
            updated_at=moment))

    _inject_before_locks(monkeypatch, activate_elsewhere)
    response = client.post(fx.item_new_url(pp), data=fx.item_form(label="Books", token=token))
    assert response.status_code == 302
    monkeypatch.undo()
    followed = client.get(response.headers["Location"], follow_redirects=True)
    assert fx.STALE_TEXT in followed.get_data(as_text=True)
    with app.app_context():
        assert FeePlanItem.query.filter_by(label="Books").count() == 0


def _raw_item(pp, label, status=fx.ITEM_ACTIVE):
    """An item written without moving the plan's version -- a state the
    application never produces, used to prove the post-lock rule itself
    rather than the token."""
    owner = FeePlan.query.filter_by(public_id=pp).one()
    db.session.add(FeePlanItem(fee_plan_id=owner.id, kind="course", label=label,
                               amount="1", status=status, version=1,
                               created_at=fx.CREATED, updated_at=fx.CREATED))


def test_the_item_limit_is_re_proved_against_locked_rows(app, client, monkeypatch):
    _admin(client, app)
    pp, _ = _draft(app, labels=[f"Item {n}" for n in range(19)])
    token = fx.token_from(client, fx.item_new_url(pp))
    _inject_before_locks(monkeypatch, lambda: _raw_item(pp, "Sneaked in"))
    response = client.post(fx.item_new_url(pp), data=fx.item_form(label="Twenty-first",
                                                                   token=token))
    assert response.status_code == 302
    monkeypatch.undo()
    assert "at most 20 items" in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert FeePlanItem.query.filter_by(label="Twenty-first").count() == 0


def test_label_uniqueness_is_re_proved_against_locked_rows(app, client, monkeypatch):
    _admin(client, app)
    pp, (ip, _) = _draft(app)
    token = fx.token_from(client, fx.item_new_url(pp))
    _inject_before_locks(monkeypatch, lambda: _raw_item(pp, "books"))
    client.post(fx.item_new_url(pp), data=fx.item_form(label="Books", token=token))
    monkeypatch.undo()
    assert "already has an item with this label" in fx.page(client, fx.item_new_url(pp))
    with app.app_context():
        assert FeePlanItem.query.filter_by(label="Books").count() == 0

    edit_token = fx.token_from(client, fx.item_edit_url(pp, ip))
    _inject_before_locks(monkeypatch, lambda: _raw_item(pp, "Tuition"))
    client.post(fx.item_edit_url(pp, ip), data=fx.item_form(label="TUITION", token=edit_token))
    monkeypatch.undo()
    with app.app_context():
        assert fx.stored_item(ip).label == "Registration"


@pytest.mark.parametrize(
    "injection, message",
    [
        ("duplicate", "share a label"),
        ("empty", "at least one item"),
        ("overfull", "at most 20 items"),
    ],
)
def test_activation_re_proves_every_item_rule_against_locked_rows(
    app, client, monkeypatch, injection, message
):
    _admin(client, app)
    labels = [f"Item {n}" for n in range(20)] if injection == "overfull" else ["Course"]
    pp, item_ids = _draft(app, labels=labels)
    token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))

    def change():
        if injection == "duplicate":
            _raw_item(pp, "COURSE")
        elif injection == "empty":
            db.session.execute(update(FeePlanItem).where(
                FeePlanItem.public_id.in_(item_ids)).values(
                status="removed", removed_at=fx.REMOVED,
                removed_by_id=FeePlan.query.filter_by(public_id=pp).one().created_by_id,
                updated_at=fx.REMOVED))
        else:
            _raw_item(pp, "Twenty-first")

    _inject_before_locks(monkeypatch, change)
    response = fx.activate(client, pp, token=token)
    assert response.status_code == 302
    monkeypatch.undo()
    assert message in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


def test_a_name_taken_mid_request_is_refused_after_the_locks(app, client, monkeypatch):
    actor_id = _admin(client, app)
    token = fx.token_from(client, fx.NEW_URL)

    def take_the_name():
        db.session.add(FeePlan(name="Contested", currency_code="LYD", status="draft",
                               created_by_id=actor_id, version=1, created_at=fx.CREATED,
                               updated_at=fx.CREATED))

    _inject_before_locks(monkeypatch, take_the_name)
    client.post(fx.NEW_URL, data=fx.plan_form(name="Contested", token=token))
    monkeypatch.undo()
    assert "already uses this name" in fx.page(client, fx.NEW_URL)
    with app.app_context():
        assert FeePlan.query.filter_by(name="Contested").count() == 1


def test_an_item_removed_mid_request_cannot_be_edited(app, client, monkeypatch):
    actor_id = _admin(client, app)
    pp, (ip, _) = _draft(app)
    token = fx.token_from(client, fx.item_edit_url(pp, ip))
    _inject_before_locks(monkeypatch, lambda: db.session.execute(
        update(FeePlanItem).where(FeePlanItem.public_id == ip).values(
            status="removed", removed_at=fx.REMOVED, removed_by_id=actor_id,
            updated_at=fx.REMOVED)))
    response = client.post(fx.item_edit_url(pp, ip),
                           data=fx.item_form(label="Registration", amount="1", token=token))
    assert response.status_code == 302
    with app.app_context():
        row = fx.stored_item(ip)
        assert row.status == fx.ITEM_REMOVED and row.amount != 1


# ===========================================================================
# Phase 5 / M02R -- restoring archived plans
# ===========================================================================


def _archived(app, name, ever_activated, version=2):
    with app.app_context():
        actor = User.query.filter_by(email="admin@example.com").one()
        row = fx.plan(actor, name=name, status=fx.ARCHIVED_STATUS,
                      ever_activated=ever_activated, version=version)
        fx.item(row, label="Course")
        return row.public_id


def _plan_snapshot(app, pp):
    with app.app_context():
        row = fx.stored_plan(pp)
        return (row.status, row.version, row.updated_at, row.status_changed_at,
                row.status_changed_by_id, row.first_activated_at, row.first_activated_by_id)


@pytest.mark.parametrize("ever_activated", [False, True])
def test_restoration_locks_only_the_actor_and_the_plan(app, client, monkeypatch, ever_activated):
    _admin(client, app)
    pp = _archived(app, "Locked", ever_activated)
    token = fx.token_from(client, fx.detail_url(pp), fx.reactivate_url(pp))
    requested = _record_lock_requests(monkeypatch)
    response = fx.reactivate(client, pp, token=token)
    locked = requested[:]
    monkeypatch.undo()
    assert response.status_code == 302
    assert locked == ["users", "fee_plans"]
    with app.app_context():
        assert fx.stored_plan(pp).status == (fx.ACTIVE if ever_activated else fx.DRAFT)


@pytest.mark.parametrize("ever_activated", [False, True])
@pytest.mark.parametrize(
    "kind",
    ["missing", "forged", "stale", "wrong_purpose", "tampered", "cross_plan", "other_actor",
     "expired"],
)
def test_restoration_refuses_every_token_but_a_current_one(
    app, client, monkeypatch, ever_activated, kind
):
    _admin(client, app)
    pp = _archived(app, "Target", ever_activated)
    other_pp = _archived(app, "Other", ever_activated)
    with app.app_context():
        actor = User.query.filter_by(email="admin@example.com").one()
        second = fx.admin("second@example.com")
        state = {"actor_public_id": actor.public_id, "plan_public_id": pp, "plan_version": 2,
                 "plan_status": fx.ARCHIVED_STATUS}
        genuine = tokens.make_token(tokens.PURPOSE_REACTIVATE, **state)
        middle = len(genuine) // 2
        token = {
            "missing": "",
            "forged": "forged",
            "stale": tokens.make_token(tokens.PURPOSE_REACTIVATE, **dict(state, plan_version=1)),
            "wrong_purpose": tokens.make_token(tokens.PURPOSE_ARCHIVE, **state),
            "tampered": genuine[:middle] + ("A" if genuine[middle] != "A" else "B")
            + genuine[middle + 1:],
            "cross_plan": tokens.make_token(tokens.PURPOSE_REACTIVATE,
                                            **dict(state, plan_public_id=other_pp)),
            "other_actor": tokens.make_token(tokens.PURPOSE_REACTIVATE,
                                             **dict(state, actor_public_id=second.public_id)),
            "expired": genuine,
        }[kind]
    before = _plan_snapshot(app, pp)
    other_before = _plan_snapshot(app, other_pp)
    if kind == "expired":
        monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
    response = fx.reactivate(client, pp, token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.page(client, fx.detail_url(pp))
    assert _plan_snapshot(app, pp) == before
    assert _plan_snapshot(app, other_pp) == other_before


def test_a_replayed_restoration_token_moves_the_version_exactly_once(app, client):
    _admin(client, app)
    pp = _archived(app, "Replay", ever_activated=False)
    token = fx.token_from(client, fx.detail_url(pp), fx.reactivate_url(pp))
    assert fx.reactivate(client, pp, token=token).status_code == 302
    assert fx.archive(client, pp).status_code == 302
    fx.reactivate(client, pp, token=token)
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.status, row.version) == (fx.ARCHIVED_STATUS, 4)


def test_a_restoration_made_elsewhere_mid_request_is_not_applied_twice(app, client, monkeypatch):
    actor_id = _admin(client, app)
    pp = _archived(app, "Contested", ever_activated=False)
    token = fx.token_from(client, fx.detail_url(pp), fx.reactivate_url(pp))

    def restored_elsewhere():
        from datetime import datetime
        moment = datetime(2026, 5, 6, 9, 0, 0)
        db.session.execute(update(FeePlan).where(FeePlan.public_id == pp).values(
            status="draft", status_changed_at=moment, status_changed_by_id=actor_id,
            version=3, updated_at=moment))

    _inject_before_locks(monkeypatch, restored_elsewhere)
    response = fx.reactivate(client, pp, token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    assert fx.STALE_TEXT in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.status, row.version) == (fx.DRAFT, 3)


# ===========================================================================
# IntegrityError recovery
# ===========================================================================


def _failing_commit():
    raise IntegrityError("INSERT INTO fee_plans ...", {}, Exception("Duplicate entry 'x' for key"))


@pytest.mark.parametrize("action", ["create", "item_create", "item_edit", "activate", "archive"])
def test_an_integrity_error_is_rolled_back_and_reported_generically(
    app, client, monkeypatch, action
):
    _admin(client, app)
    pp, (ip, _) = _draft(app)
    tokens_ready = {
        "create": fx.token_from(client, fx.NEW_URL),
        "item_create": fx.token_from(client, fx.item_new_url(pp)),
        "item_edit": fx.token_from(client, fx.item_edit_url(pp, ip)),
        "activate": fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp)),
        "archive": fx.token_from(client, fx.detail_url(pp), fx.archive_url(pp)),
    }
    monkeypatch.setattr(routes.db.session, "commit", _failing_commit)
    token = tokens_ready[action]
    response = {
        "create": lambda: client.post(fx.NEW_URL, data=fx.plan_form(name="New", token=token)),
        "item_create": lambda: client.post(fx.item_new_url(pp),
                                           data=fx.item_form(label="Books", token=token)),
        "item_edit": lambda: client.post(fx.item_edit_url(pp, ip), data=fx.item_form(
            label="Registration", amount="1", token=token)),
        "activate": lambda: fx.activate(client, pp, token=token),
        "archive": lambda: fx.archive(client, pp, token=token),
    }[action]()
    monkeypatch.undo()
    assert response.status_code == 302
    html = fx.page(client, response.headers["Location"])
    assert fx.INTEGRITY_TEXT in html
    for leaked in ("INSERT", "Duplicate entry", "IntegrityError", "sqlite", "pymysql"):
        assert leaked not in html, leaked
    with app.app_context():
        owner = fx.stored_plan(pp)
        assert (owner.status, owner.version) == (fx.DRAFT, 1)
        assert FeePlan.query.count() == 1 and FeePlanItem.query.count() == 2
        assert fx.stored_item(ip).amount != 1


def test_recovery_re_authorizes_from_current_state(app, client, monkeypatch):
    actor_id = _admin(client, app)
    pp, _ = _draft(app)
    token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))
    real_commit = db.session.commit

    def commit_that_loses_access():
        db.session.rollback()
        db.session.execute(update(User).where(User.id == actor_id).values(
            status=UserStatus.SUSPENDED.value))
        real_commit()
        _failing_commit()

    monkeypatch.setattr(routes.db.session, "commit", commit_that_loses_access)
    response = fx.activate(client, pp, token=token)
    monkeypatch.undo()
    assert response.status_code == 404
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


@pytest.mark.parametrize("ever_activated", [False, True])
def test_a_restoration_integrity_error_is_rolled_back_and_reported_generically(
    app, client, monkeypatch, ever_activated
):
    _admin(client, app)
    pp = _archived(app, "Fragile", ever_activated)
    token = fx.token_from(client, fx.detail_url(pp), fx.reactivate_url(pp))
    before = _plan_snapshot(app, pp)
    monkeypatch.setattr(routes.db.session, "commit", _failing_commit)
    response = fx.reactivate(client, pp, token=token)
    monkeypatch.undo()
    assert response.status_code == 302
    html = fx.page(client, response.headers["Location"])
    assert fx.INTEGRITY_TEXT in html
    for leaked in ("Duplicate entry", "IntegrityError", "INSERT"):
        assert leaked not in html, leaked
    assert _plan_snapshot(app, pp) == before


def test_a_real_unique_name_race_reaches_the_database_and_is_handled(app, client, monkeypatch):
    """Both friendly name checks are bypassed, so ``uq_fee_plans_name`` itself
    refuses the insert -- the final defense, exercised for real."""
    _admin(client, app)
    with app.app_context():
        fx.plan(User.query.one(), name="Contested")
    token = fx.token_from(client, fx.NEW_URL)
    monkeypatch.setattr(routes, "plan_name_taken", lambda *args, **kwargs: False)
    response = client.post(fx.NEW_URL, data=fx.plan_form(name="Contested", token=token))
    monkeypatch.undo()
    assert response.status_code == 302
    html = fx.page(client, response.headers["Location"])
    assert fx.INTEGRITY_TEXT in html
    assert not re.search(r"UNIQUE|constraint|sqlite", html, re.I)
    with app.app_context():
        assert FeePlan.query.filter_by(name="Contested").count() == 1
