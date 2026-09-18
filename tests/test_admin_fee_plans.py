"""Phase 5 / M02 -- the Administrator fee plan catalogue routes.

Authorization and disclosure resistance, POST-only and CSRF, the plan and
item forms with exact money, the draft / active / archived lifecycle and its
permanent freeze, removal as history, the controls each state offers, the
absence of every out-of-scope control, escaping, internal-id hygiene,
pagination and filtering, query bounds, response headers and navigation.

Token tampering, replay and staleness, the lock chain and post-lock
revalidation live in ``tests/test_fee_plan_transactions.py``.
"""

import re
from decimal import Decimal

import pytest
from sqlalchemy import event as sa_event

import tests.fee_plan_fixtures as fx
from app import create_app
from app.extensions import db
from app.models import FeePlan, FeePlanItem, User, UserRole, UserStatus


def _admin(client, app, email="admin@example.com", name=None):
    with app.app_context():
        actor_id = fx.admin(email, name=name).id
    fx.login_as(client, email)
    return actor_id


def _draft_with_items(app, creator_email="boss@example.com", labels=("Registration", "Course")):
    with app.app_context():
        creator = User.query.filter_by(email=creator_email).first() or fx.admin(creator_email)
        owner = fx.plan(creator)
        items = [fx.item(owner, label=label, amount="100.5") for label in labels]
        return owner.public_id, [row.public_id for row in items]


def _all_urls(pp, ip):
    gets = [fx.LIST_URL, fx.NEW_URL, fx.detail_url(pp), fx.edit_url(pp),
            fx.item_new_url(pp), fx.item_edit_url(pp, ip)]
    posts = [fx.NEW_URL, fx.edit_url(pp), fx.item_new_url(pp), fx.item_edit_url(pp, ip),
             fx.item_remove_url(pp, ip), fx.activate_url(pp), fx.archive_url(pp),
             fx.reactivate_url(pp)]
    return gets, posts


# ===========================================================================
# Authorization and disclosure resistance
# ===========================================================================


def test_anonymous_visitors_are_sent_to_login_everywhere(app, client):
    pp, (ip, _) = _draft_with_items(app)
    gets, posts = _all_urls(pp, ip)
    for url in gets:
        response = client.get(url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url
    for url in posts:
        response = client.post(url, data={"confirm": "yes"})
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.TEACHER.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_forbidden_on_every_route(app, client, role):
    pp, (ip, _) = _draft_with_items(app)
    with app.app_context():
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    gets, posts = _all_urls(pp, ip)
    for url in gets:
        assert client.get(url).status_code == 403, url
    for url in posts:
        assert client.post(url, data={"confirm": "yes"}).status_code == 403, url
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT
        assert fx.stored_plan(pp).version == 1


def test_a_suspended_administrator_is_signed_out_and_changes_nothing(app, client):
    pp, _ = _draft_with_items(app)
    _admin(client, app)
    token = fx.token_from(client, fx.detail_url(pp), fx.activate_url(pp))
    with app.app_context():
        actor = User.query.filter_by(email="admin@example.com").one()
        actor.status = UserStatus.SUSPENDED.value
        db.session.commit()
    fx.fresh_identity()
    assert client.get(fx.LIST_URL).status_code == 302
    response = fx.activate(client, pp, token=token)
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


def test_unknown_foreign_and_internal_identifiers_are_a_plain_404(app, client):
    pp, (ip, _) = _draft_with_items(app)
    other_pp, (other_ip, _) = _draft_with_items(app, labels=("A", "B"))
    _admin(client, app)
    with app.app_context():
        internal = fx.stored_plan(pp).id
        internal_item = fx.stored_item(ip).id
    for bad_pp in ("no-such-plan", str(internal), "x" * 37):
        for url in (fx.detail_url(bad_pp), fx.edit_url(bad_pp), fx.item_new_url(bad_pp)):
            assert client.get(url).status_code == 404, url
        for url in (fx.activate_url(bad_pp), fx.archive_url(bad_pp), fx.reactivate_url(bad_pp)):
            assert client.post(url, data={"confirm": "yes"}).status_code == 404, url
    # An item is only ever found through its own plan.
    for bad_ip in (other_ip, str(internal_item), "no-such-item"):
        assert client.get(fx.item_edit_url(pp, bad_ip)).status_code == 404, bad_ip
        assert client.post(fx.item_edit_url(pp, bad_ip)).status_code == 404, bad_ip
        assert client.post(fx.item_remove_url(pp, bad_ip)).status_code == 404, bad_ip
    with app.app_context():
        assert fx.stored_item(other_ip).status == fx.ITEM_ACTIVE


# ===========================================================================
# POST-only, and CSRF
# ===========================================================================


def test_every_mutation_is_post_only(app, client):
    pp, (ip, _) = _draft_with_items(app)
    _admin(client, app)
    for url in (fx.item_remove_url(pp, ip), fx.activate_url(pp), fx.archive_url(pp),
                fx.reactivate_url(pp)):
        assert client.get(url).status_code == 405, url
    for url in (fx.detail_url(pp), fx.LIST_URL):
        assert client.put(url).status_code == 405, url
        assert client.delete(url).status_code == 405, url
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT
        assert fx.stored_item(ip).status == fx.ITEM_ACTIVE


@pytest.fixture
def csrf_app():
    """The testing app with CSRF protection **enabled**, so the tests below
    prove it is enforced rather than merely declared."""
    csrf_enabled = create_app("testing", WTF_CSRF_ENABLED=True)
    with csrf_enabled.app_context():
        db.create_all()
        yield csrf_enabled
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _csrf(html):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def test_csrf_is_enforced_on_create_and_on_every_lifecycle_post(csrf_app):
    client = csrf_app.test_client()
    with csrf_app.app_context():
        creator = fx.admin()
        owner = fx.plan(creator)
        fx.item(owner, label="Course")
        pp = owner.public_id
    login_page = client.get("/auth/login").get_data(as_text=True)
    client.post("/auth/login", data={"email": "admin@example.com", "password": fx.PW,
                                     "csrf_token": _csrf(login_page)})

    new_page = client.get(fx.NEW_URL).get_data(as_text=True)
    token = fx.state_in(new_page)
    assert client.post(fx.NEW_URL, data=fx.plan_form(token=token)).status_code == 400
    with csrf_app.app_context():
        assert FeePlan.query.count() == 1

    detail = client.get(fx.detail_url(pp)).get_data(as_text=True)
    activate_token = fx.state_in(detail, fx.activate_url(pp))
    response = client.post(fx.activate_url(pp), data={"state_token": activate_token,
                                                      "confirm": "yes"})
    assert response.status_code == 400
    with csrf_app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT

    response = client.post(fx.activate_url(pp), data={"state_token": activate_token,
                                                      "confirm": "yes",
                                                      "csrf_token": _csrf(detail)})
    assert response.status_code == 302
    with csrf_app.app_context():
        assert fx.stored_plan(pp).status == fx.ACTIVE

    response = client.post(fx.NEW_URL, data=dict(fx.plan_form(token=token),
                                                 csrf_token=_csrf(new_page)))
    assert response.status_code == 302
    with csrf_app.app_context():
        assert FeePlan.query.count() == 2


def test_csrf_is_enforced_on_restoring_an_archived_draft(csrf_app):
    client = csrf_app.test_client()
    with csrf_app.app_context():
        creator = fx.admin()
        pp = fx.plan(creator, status=fx.ARCHIVED_STATUS, ever_activated=False,
                     version=2).public_id
    login_page = client.get("/auth/login").get_data(as_text=True)
    client.post("/auth/login", data={"email": "admin@example.com", "password": fx.PW,
                                     "csrf_token": _csrf(login_page)})
    detail = client.get(fx.detail_url(pp)).get_data(as_text=True)
    token = fx.state_in(detail, fx.reactivate_url(pp))
    assert token
    response = client.post(fx.reactivate_url(pp), data={fx.STATE_FIELD: token, "confirm": "yes"})
    assert response.status_code == 400
    with csrf_app.app_context():
        assert (fx.stored_plan(pp).status, fx.stored_plan(pp).version) == (fx.ARCHIVED_STATUS, 2)
    response = client.post(fx.reactivate_url(pp), data={fx.STATE_FIELD: token, "confirm": "yes",
                                                        "csrf_token": _csrf(detail)})
    assert response.status_code == 302
    with csrf_app.app_context():
        assert (fx.stored_plan(pp).status, fx.stored_plan(pp).version) == (fx.DRAFT, 3)


# ===========================================================================
# Creating and editing a plan
# ===========================================================================


def test_an_administrator_creates_a_normalized_draft(app, client):
    actor_id = _admin(client, app)
    pp = fx.create_plan(client, name="  Standard    plan ", description="Line one\r\nLine two")
    assert pp is not None
    with app.app_context():
        row = fx.stored_plan(pp)
        assert row.name == "Standard plan"
        assert row.description == "Line one\nLine two"
        assert (row.currency_code, row.status, row.version) == ("LYD", fx.DRAFT, 1)
        assert row.created_by_id == actor_id
        assert row.created_at == row.updated_at and row.created_at.microsecond == 0
        assert row.first_activated_at is None and row.status_changed_at is None
        assert FeePlanItem.query.count() == 0
    assert "Fee plan created as a draft" in fx.page(client, fx.detail_url(pp))


def test_a_blank_description_is_stored_as_null(app, client):
    _admin(client, app)
    pp = fx.create_plan(client, description="   ")
    with app.app_context():
        assert fx.stored_plan(pp).description is None


@pytest.mark.parametrize(
    "name, message",
    [
        ("", "Give this fee plan a name."),
        ("x" * 151, "at most 150 characters"),
        ("Bad\x00name", "ordinary text"),
        ("Bad ‮name", "ordinary text"),
    ],
)
def test_an_invalid_name_re_renders_and_writes_nothing(app, client, name, message):
    _admin(client, app)
    token = fx.token_from(client, fx.NEW_URL)
    response = client.post(fx.NEW_URL, data=fx.plan_form(name=name, token=token))
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    with app.app_context():
        assert FeePlan.query.count() == 0


@pytest.mark.parametrize("status", [fx.DRAFT, fx.ACTIVE, fx.ARCHIVED_STATUS])
def test_a_name_is_unique_across_every_status(app, client, status):
    _admin(client, app)
    with app.app_context():
        fx.plan(User.query.one(), name="Standard plan", status=status)
    token = fx.token_from(client, fx.NEW_URL)
    response = client.post(fx.NEW_URL, data=fx.plan_form(name=" Standard  plan", token=token))
    assert response.status_code == 200
    assert "Another fee plan already uses this name" in response.get_data(as_text=True)
    with app.app_context():
        assert FeePlan.query.count() == 1


def test_submitted_fields_the_form_does_not_declare_are_ignored(app, client):
    _admin(client, app)
    token = fx.token_from(client, fx.NEW_URL)
    data = fx.plan_form(token=token)
    data.update({"status": "active", "currency_code": "USD", "version": "9",
                 "created_by_id": "1", "public_id": "forged", "first_activated_at": "2026-01-01"})
    response = client.post(fx.NEW_URL, data=data)
    assert response.status_code == 302
    pp = fx.public_id_from_redirect(response)
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.status, row.currency_code, row.version) == (fx.DRAFT, "LYD", 1)
        assert row.public_id != "forged" and row.first_activated_at is None


def test_a_draft_can_be_renamed_and_the_version_moves(app, client):
    _admin(client, app)
    pp = fx.create_plan(client)
    response = fx.edit_plan(client, pp, name="Evening plan", description="")
    assert response.status_code == 302
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.name, row.description, row.version) == ("Evening plan", None, 2)


def test_a_no_op_edit_moves_neither_the_version_nor_a_timestamp(app, client):
    _admin(client, app)
    with app.app_context():
        row = fx.plan(User.query.one(), name="Standard plan", description="Text")
        pp, updated_before = row.public_id, row.updated_at
    response = fx.edit_plan(client, pp, name="  Standard   plan ", description=" Text \r\n")
    assert response.status_code == 302
    assert "Nothing was changed" in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        row = fx.stored_plan(pp)
        assert row.version == 1 and row.updated_at == updated_before


def test_renaming_onto_another_plans_name_is_refused(app, client):
    _admin(client, app)
    with app.app_context():
        fx.plan(User.query.one(), name="Taken", status=fx.ARCHIVED_STATUS)
    pp = fx.create_plan(client, name="Mine")
    response = fx.edit_plan(client, pp, name="Taken")
    assert response.status_code == 200
    assert "Another fee plan already uses this name" in response.get_data(as_text=True)
    with app.app_context():
        assert fx.stored_plan(pp).name == "Mine"


@pytest.mark.parametrize("status", [fx.ACTIVE, fx.ARCHIVED_STATUS])
def test_an_active_or_archived_plan_cannot_be_edited(app, client, status):
    _admin(client, app)
    with app.app_context():
        row = fx.plan(User.query.one(), name="Frozen", status=status, version=3)
        fx.item(row, label="Course")
        pp = row.public_id
    response = client.get(fx.edit_url(pp))
    assert response.status_code == 302 and response.headers["Location"].endswith(fx.detail_url(pp))
    assert "cannot be changed" in fx.page(client, fx.detail_url(pp))
    response = client.post(fx.edit_url(pp), data=fx.plan_form(name="Changed"))
    assert response.status_code == 302
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.name, row.version, row.status) == ("Frozen", 3, status)


# ===========================================================================
# Items
# ===========================================================================


def test_an_item_is_added_with_its_exact_amount(app, client):
    _admin(client, app)
    pp = fx.create_plan(client)
    response = fx.add_item(client, pp, kind=fx.REGISTRATION, label=" Registration  fee ",
                           amount="1250.1234")
    assert response.status_code == 302
    with app.app_context():
        row = FeePlanItem.query.one()
        assert (row.kind, row.label, row.status, row.version) == (
            fx.REGISTRATION, "Registration fee", fx.ITEM_ACTIVE, 1)
        assert row.amount == Decimal("1250.1234")
        assert row.created_at == row.updated_at and row.created_at.microsecond == 0
        assert fx.stored_plan(pp).version == 2
    html = fx.page(client, fx.detail_url(pp))
    assert "1,250.1234" in html and "Registration fee" in html


@pytest.mark.parametrize(
    "amount, message",
    [
        ("1,250", "Do not use commas"),
        ("-5", "Do not use commas"),
        ("1e3", "Do not use commas"),
        ("NaN", "Do not use commas"),
        ("١٢", "Do not use commas"),
        ("", "Enter the amount in LYD."),
        ("0", "greater than zero"),
        ("0.0009", "at least 0.001 LYD"),
        ("100000", "at most 99,999.999 LYD"),
        ("12.34567", "never rounded"),
    ],
)
def test_a_bad_amount_is_explained_and_nothing_is_written(app, client, amount, message):
    _admin(client, app)
    pp = fx.create_plan(client)
    response = fx.add_item(client, pp, amount=amount)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert message in html
    with app.app_context():
        assert FeePlanItem.query.count() == 0
        assert fx.stored_plan(pp).version == 1


def test_an_unknown_kind_is_refused(app, client):
    _admin(client, app)
    pp = fx.create_plan(client)
    response = fx.add_item(client, pp, kind="discount")
    assert response.status_code == 200
    assert "registration fee or a course fee" in response.get_data(as_text=True)
    with app.app_context():
        assert FeePlanItem.query.count() == 0


def test_two_active_items_cannot_share_a_label_ignoring_case(app, client):
    _admin(client, app)
    pp = fx.create_plan(client)
    assert fx.add_item(client, pp, label="Course fee").status_code == 302
    response = fx.add_item(client, pp, label="COURSE   FEE")
    assert response.status_code == 200
    assert "already has an item with this label" in response.get_data(as_text=True)
    with app.app_context():
        assert FeePlanItem.query.count() == 1


def test_a_removed_items_label_may_be_used_again(app, client):
    _admin(client, app)
    with app.app_context():
        owner = fx.plan(User.query.one())
        fx.item(owner, label="Course fee", status=fx.ITEM_REMOVED)
        pp = owner.public_id
    assert fx.add_item(client, pp, label="Course fee").status_code == 302
    with app.app_context():
        assert FeePlanItem.query.filter_by(status=fx.ITEM_ACTIVE).count() == 1


def test_a_plan_holds_at_most_twenty_active_items(app, client):
    _admin(client, app)
    with app.app_context():
        owner = fx.plan(User.query.one())
        for index in range(19):
            fx.item(owner, label=f"Item {index}")
        fx.item(owner, label="Gone", status=fx.ITEM_REMOVED)
        pp = owner.public_id
    assert fx.add_item(client, pp, label="Twentieth").status_code == 302
    detail = fx.page(client, fx.detail_url(pp))
    assert "maximum of 20 items" in detail
    assert f'href="{fx.item_new_url(pp)}"' not in detail
    response = client.get(fx.item_new_url(pp))
    assert response.status_code == 302
    assert "at most 20 items" in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert FeePlanItem.query.filter_by(status=fx.ITEM_ACTIVE).count() == 20


def test_an_item_is_edited_and_both_versions_move(app, client):
    _admin(client, app)
    pp, (ip, _) = _draft_with_items(app, creator_email="admin@example.com")
    response = fx.edit_item(client, pp, ip, kind=fx.REGISTRATION, label="Registration",
                            amount="99.999")
    assert response.status_code == 302
    with app.app_context():
        row = fx.stored_item(ip)
        assert (row.kind, row.amount, row.version) == (fx.REGISTRATION, Decimal("99.999"), 2)
        assert fx.stored_plan(pp).version == 2


def test_the_edit_form_is_prefilled_with_text_the_parser_accepts(app, client):
    _admin(client, app)
    pp, (ip, _) = _draft_with_items(app, creator_email="admin@example.com")
    html = fx.page(client, fx.item_edit_url(pp, ip))
    assert 'value="100.500"' in html
    response = fx.edit_item(client, pp, ip, kind=fx.COURSE, label="Registration", amount="100.500")
    assert response.status_code == 302
    assert "Nothing was changed" in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert fx.stored_item(ip).version == 1
        assert fx.stored_plan(pp).version == 1


def test_an_item_cannot_be_renamed_onto_a_siblings_label(app, client):
    _admin(client, app)
    pp, (ip, _) = _draft_with_items(app, creator_email="admin@example.com")
    response = fx.edit_item(client, pp, ip, label="course")
    assert response.status_code == 200
    assert "already has an item with this label" in response.get_data(as_text=True)
    with app.app_context():
        assert fx.stored_item(ip).label == "Registration"


def test_removing_an_item_keeps_it_as_history(app, client):
    actor_id = _admin(client, app)
    pp, (ip, keep) = _draft_with_items(app, creator_email="admin@example.com")
    response = fx.remove_item(client, pp, ip)
    assert response.status_code == 302
    with app.app_context():
        row = fx.stored_item(ip)
        assert row.status == fx.ITEM_REMOVED
        assert row.removed_by_id == actor_id and row.removed_at is not None
        assert row.version == 2
        assert FeePlanItem.query.count() == 2
        assert fx.stored_plan(pp).version == 2
    html = fx.page(client, fx.detail_url(pp))
    assert "Removed items" in html
    assert "Items (1 of at most 20)" in html
    # The total counts the active item only.
    assert re.search(r"Total</th>\s*<th[^>]*>100.500</th>", html)
    # A removed item cannot be edited or removed again.
    assert client.get(fx.item_edit_url(pp, ip)).status_code == 302
    response = fx.remove_item(client, pp, ip, token="anything")
    assert response.status_code == 302
    with app.app_context():
        assert fx.stored_item(ip).version == 2


@pytest.mark.parametrize("status", [fx.ACTIVE, fx.ARCHIVED_STATUS])
def test_items_of_a_frozen_plan_cannot_be_touched(app, client, status):
    _admin(client, app)
    with app.app_context():
        owner = fx.plan(User.query.one(), status=status, version=4)
        row = fx.item(owner, label="Course", amount="500")
        pp, ip = owner.public_id, row.public_id
    for url in (fx.item_new_url(pp), fx.item_edit_url(pp, ip)):
        response = client.get(url)
        assert response.status_code == 302, url
    for url, data in (
        (fx.item_new_url(pp), fx.item_form(label="New")),
        (fx.item_edit_url(pp, ip), fx.item_form(label="Changed", amount="1")),
        (fx.item_remove_url(pp, ip), {}),
    ):
        assert client.post(url, data=data).status_code == 302, url
    with app.app_context():
        row = fx.stored_item(ip)
        assert (row.label, row.amount, row.status, row.version) == (
            "Course", Decimal("500"), fx.ITEM_ACTIVE, 1)
        assert FeePlanItem.query.count() == 1
        assert fx.stored_plan(pp).version == 4
    html = fx.page(client, fx.detail_url(pp))
    assert "Remove</button>" not in html and "Add item" not in html


# ===========================================================================
# Lifecycle
# ===========================================================================


def test_activation_needs_the_confirmation_box(app, client):
    _admin(client, app)
    pp, _ = _draft_with_items(app, creator_email="admin@example.com")
    response = fx.activate(client, pp, confirm=False)
    assert response.status_code == 302
    assert "tick the confirmation box" in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


def test_activation_needs_at_least_one_active_item(app, client):
    _admin(client, app)
    with app.app_context():
        owner = fx.plan(User.query.one())
        fx.item(owner, status=fx.ITEM_REMOVED)
        pp = owner.public_id
    fx.activate(client, pp)
    assert "at least one item" in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT
        assert fx.stored_plan(pp).version == 1


def test_activation_freezes_the_plan_and_leaves_every_item_untouched(app, client):
    actor_id = _admin(client, app)
    pp, item_ids = _draft_with_items(app, creator_email="admin@example.com")
    with app.app_context():
        before = [(r.label, r.amount, r.version, r.updated_at)
                  for r in map(fx.stored_item, item_ids)]
    assert fx.activate(client, pp).status_code == 302
    with app.app_context():
        row = fx.stored_plan(pp)
        assert row.status == fx.ACTIVE and row.version == 2
        assert row.first_activated_by_id == actor_id == row.status_changed_by_id
        assert row.first_activated_at == row.status_changed_at == row.updated_at
        assert row.first_activated_at.microsecond == 0
        assert [(r.label, r.amount, r.version, r.updated_at)
                for r in map(fx.stored_item, item_ids)] == before
    html = fx.page(client, fx.detail_url(pp))
    assert "Definition frozen" in html and "Fee plan activated" in html
    assert "Archive plan" in html
    for absent in ("Activate plan", "Reactivate plan", "Add item", "Edit name", "Remove</button>"):
        assert absent not in html, absent


def test_archiving_an_active_plan_changes_availability_only(app, client):
    _admin(client, app)
    pp, item_ids = _draft_with_items(app, creator_email="admin@example.com")
    fx.activate(client, pp)
    with app.app_context():
        first = fx.stored_plan(pp).first_activated_at
        items_before = [(r.amount, r.version) for r in map(fx.stored_item, item_ids)]
    assert fx.archive(client, pp).status_code == 302
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.status, row.version, row.first_activated_at) == (fx.ARCHIVED_STATUS, 3, first)
        assert [(r.amount, r.version) for r in map(fx.stored_item, item_ids)] == items_before
    html = fx.page(client, fx.detail_url(pp))
    assert "Reactivate plan" in html and "Archive plan" not in html


def test_reactivation_restores_active_and_keeps_the_plan_frozen(app, client):
    actor_id = _admin(client, app)
    pp, (ip, _) = _draft_with_items(app, creator_email="admin@example.com")
    fx.activate(client, pp)
    fx.archive(client, pp)
    with app.app_context():
        archived = fx.stored_plan(pp)
        first = (archived.first_activated_at, archived.first_activated_by_id)
        archived_version, archived_at = archived.version, archived.status_changed_at
    html = fx.page(client, fx.detail_url(pp))
    assert "Reactivate this plan" in html and "Reactivate plan" in html
    assert "Restore draft" not in html
    response = fx.reactivate(client, pp)
    assert response.status_code == 302
    assert response.headers["Cache-Control"] == "private, no-store"
    with app.app_context():
        row = fx.stored_plan(pp)
        assert row.status == fx.ACTIVE
        assert (row.first_activated_at, row.first_activated_by_id) == first
        assert row.version == archived_version + 1 == 4
        assert row.status_changed_by_id == actor_id
        assert row.status_changed_at >= archived_at and row.status_changed_at.microsecond == 0
        assert row.updated_at == row.status_changed_at
    assert client.get(fx.edit_url(pp)).status_code == 302
    assert client.get(fx.item_edit_url(pp, ip)).status_code == 302
    page = fx.page(client, fx.detail_url(pp))
    assert "Fee plan reactivated" in page and "Draft restored" not in page
    assert "Definition frozen" in page


def test_an_ever_activated_plan_stays_immutable_after_archive_and_reactivation(app, client):
    """Genuine, current tokens for every write still change nothing: the
    locked row's frozen lifecycle refuses them."""
    from app.services import fee_plan_tokens as tokens

    _admin(client, app)
    pp, (ip, _) = _draft_with_items(app, creator_email="admin@example.com")
    fx.activate(client, pp)
    fx.archive(client, pp)
    fx.reactivate(client, pp)
    with app.app_context():
        actor = User.query.one()
        row = fx.stored_plan(pp)
        item = fx.stored_item(ip)
        snapshot = (row.name, row.description, row.status, row.version, row.updated_at)
        items_before = [(r.label, r.amount, r.status, r.version)
                        for r in FeePlanItem.query.order_by(FeePlanItem.id)]
        state = {"actor_public_id": actor.public_id, "plan_public_id": pp,
                 "plan_version": row.version, "plan_status": row.status}
        item_state = dict(state, item_public_id=ip, item_version=item.version,
                          item_status=item.status)
        genuine = {
            purpose: tokens.make_token(
                purpose,
                **(item_state if purpose in (tokens.PURPOSE_ITEM_EDIT,
                                             tokens.PURPOSE_ITEM_REMOVE) else state),
            )
            for purpose in (tokens.PURPOSE_EDIT, tokens.PURPOSE_ITEM_CREATE,
                            tokens.PURPOSE_ITEM_EDIT, tokens.PURPOSE_ITEM_REMOVE,
                            tokens.PURPOSE_ACTIVATE, tokens.PURPOSE_REACTIVATE)
        }
    for url, data in (
        (fx.edit_url(pp), fx.plan_form(name="Changed", token=genuine[tokens.PURPOSE_EDIT])),
        (fx.item_new_url(pp), fx.item_form(label="New", token=genuine[tokens.PURPOSE_ITEM_CREATE])),
        (fx.item_edit_url(pp, ip), fx.item_form(label="Changed", amount="1",
                                                token=genuine[tokens.PURPOSE_ITEM_EDIT])),
        (fx.item_remove_url(pp, ip), {fx.STATE_FIELD: genuine[tokens.PURPOSE_ITEM_REMOVE]}),
        (fx.activate_url(pp), {fx.STATE_FIELD: genuine[tokens.PURPOSE_ACTIVATE], "confirm": "yes"}),
        (fx.reactivate_url(pp), {fx.STATE_FIELD: genuine[tokens.PURPOSE_REACTIVATE],
                                 "confirm": "yes"}),
    ):
        assert client.post(url, data=data).status_code == 302, url
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.name, row.description, row.status, row.version, row.updated_at) == snapshot
        assert [(r.label, r.amount, r.status, r.version)
                for r in FeePlanItem.query.order_by(FeePlanItem.id)] == items_before


def test_a_draft_can_be_archived_and_then_offers_only_restoration(app, client):
    _admin(client, app)
    pp, _ = _draft_with_items(app, creator_email="admin@example.com")
    assert fx.archive(client, pp).status_code == 302
    with app.app_context():
        row = fx.stored_plan(pp)
        assert row.status == fx.ARCHIVED_STATUS and row.first_activated_at is None
    html = fx.page(client, fx.detail_url(pp))
    assert "archived before it was ever activated" in html
    assert "Restore this plan to a draft" in html and "Restore draft" in html
    for absent in ("Activate plan", "Reactivate plan", "Reactivate this plan", "Archive plan",
                   "Add item", "Edit name", "Remove</button>", "Definition frozen"):
        assert absent not in html, absent
    assert html.count('name="state_token"') == 1
    assert fx.state_in(html, fx.reactivate_url(pp))


def test_an_archived_draft_restores_to_an_editable_draft(app, client):
    actor_id = _admin(client, app)
    with app.app_context():
        row = fx.plan(User.query.one(), name="Paused", status=fx.ARCHIVED_STATUS,
                      ever_activated=False, version=2)
        pp, archived_at = row.public_id, row.status_changed_at
    # No active item is needed to restore a draft.
    response = fx.reactivate(client, pp)
    assert response.status_code == 302
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.headers.get("Vary", "")
    with app.app_context():
        row = fx.stored_plan(pp)
        assert row.status == fx.DRAFT
        assert row.first_activated_at is None and row.first_activated_by_id is None
        assert row.status_changed_by_id == actor_id
        assert row.status_changed_at > archived_at and row.status_changed_at.microsecond == 0
        assert row.updated_at == row.status_changed_at
        assert row.version == 3
        assert FeePlanItem.query.count() == 0
    html = fx.page(client, fx.detail_url(pp))
    assert "Draft restored" in html
    assert "Fee plan reactivated" not in html and "Definition frozen" not in html
    for present in ("Edit name", "Add item", "Activate plan", "Archive plan"):
        assert present in html, present

    # Ordinary draft work, then a normal first activation.
    assert fx.edit_plan(client, pp, name="Resumed").status_code == 302
    assert fx.add_item(client, pp, kind=fx.REGISTRATION, label="Registration",
                       amount="50").status_code == 302
    assert fx.add_item(client, pp, label="Books", amount="20").status_code == 302
    with app.app_context():
        registration = FeePlanItem.query.filter_by(label="Registration").one().public_id
        books = FeePlanItem.query.filter_by(label="Books").one().public_id
    assert fx.edit_item(client, pp, registration, kind=fx.REGISTRATION, label="Registration",
                        amount="55.5").status_code == 302
    assert fx.remove_item(client, pp, books).status_code == 302
    assert fx.activate(client, pp).status_code == 302
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.status, row.name) == (fx.ACTIVE, "Resumed")
        assert row.first_activated_by_id == actor_id
        assert row.first_activated_at == row.status_changed_at == row.updated_at
        assert row.version == 9
        assert fx.stored_item(registration).amount == Decimal("55.5")
        assert fx.stored_item(books).status == fx.ITEM_REMOVED


def test_a_draft_can_be_archived_and_restored_repeatedly_keeping_its_history(app, client):
    actor_id = _admin(client, app)
    pp = fx.create_plan(client, name="Cycle")
    versions = []
    for _ in range(2):
        assert fx.archive(client, pp).status_code == 302
        with app.app_context():
            assert fx.stored_plan(pp).status == fx.ARCHIVED_STATUS
            versions.append(fx.stored_plan(pp).version)
        assert fx.reactivate(client, pp).status_code == 302
        with app.app_context():
            row = fx.stored_plan(pp)
            assert (row.status, row.first_activated_at) == (fx.DRAFT, None)
            assert row.status_changed_at is not None and row.status_changed_by_id == actor_id
            versions.append(row.version)
    assert versions == [2, 3, 4, 5]


def test_restoration_needs_the_confirmation_box_and_names_its_outcome(app, client):
    _admin(client, app)
    with app.app_context():
        actor = User.query.one()
        draft = fx.plan(actor, status=fx.ARCHIVED_STATUS, ever_activated=False, version=2)
        frozen = fx.plan(actor, status=fx.ARCHIVED_STATUS, version=3)
        sentences = {draft.public_id: "before restoring this plan to a draft",
                     frozen.public_id: "before reactivating"}
    for pp, sentence in sentences.items():
        assert fx.reactivate(client, pp, confirm=False).status_code == 302
        assert sentence in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        assert [(fx.stored_plan(pp).status, fx.stored_plan(pp).version) for pp in sentences] == [
            (fx.ARCHIVED_STATUS, 2), (fx.ARCHIVED_STATUS, 3)]


@pytest.mark.parametrize("status", [fx.DRAFT, fx.ACTIVE])
def test_restoration_is_refused_for_a_plan_that_is_not_archived(app, client, status):
    """A genuine, current reactivation token for a draft or active plan still
    changes nothing: the locked row decides."""
    from app.services import fee_plan_tokens as tokens

    _admin(client, app)
    with app.app_context():
        actor = User.query.one()
        row = fx.plan(actor, status=status, version=2)
        pp = row.public_id
        before = (row.status, row.version, row.updated_at, row.status_changed_at)
        token = tokens.make_token(tokens.PURPOSE_REACTIVATE, actor_public_id=actor.public_id,
                                  plan_public_id=pp, plan_version=2, plan_status=status)
    assert fx.reactivate(client, pp, token=token).status_code == 302
    assert "Only an archived fee plan can be restored" in fx.page(client, fx.detail_url(pp))
    with app.app_context():
        row = fx.stored_plan(pp)
        assert (row.status, row.version, row.updated_at, row.status_changed_at) == before


def _archived_draft(app, email="boss@example.com"):
    with app.app_context():
        creator = User.query.filter_by(email=email).first() or fx.admin(email)
        row = fx.plan(creator, status=fx.ARCHIVED_STATUS, ever_activated=False, version=2)
        return row.public_id


def _still_archived_draft(app, pp):
    with app.app_context():
        row = fx.stored_plan(pp)
        return (row.status, row.version, row.first_activated_at) == (fx.ARCHIVED_STATUS, 2, None)


def test_only_an_active_administrator_can_restore_an_archived_draft(app, client):
    pp = _archived_draft(app)
    response = client.post(fx.reactivate_url(pp), data={"confirm": "yes"})
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    for role in (UserRole.STUDENT.value, UserRole.TEACHER.value, UserRole.RESEARCHER.value):
        email = f"{role}@example.com"
        with app.app_context():
            fx.user(email, role)
        fx.login_as(client, email)
        assert client.post(fx.reactivate_url(pp), data={"confirm": "yes"}).status_code == 403
        assert client.get(fx.detail_url(pp)).status_code == 403
    _admin(client, app, "late@example.com")
    token = fx.token_from(client, fx.detail_url(pp), fx.reactivate_url(pp))
    assert token
    with app.app_context():
        late = User.query.filter_by(email="late@example.com").one()
        late.status = UserStatus.SUSPENDED.value
        db.session.commit()
    fx.fresh_identity()
    response = fx.reactivate(client, pp, token=token)
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    assert _still_archived_draft(app, pp)


def test_restoration_is_post_only_and_nested_to_a_real_plan(app, client):
    pp = _archived_draft(app)
    other_pp = _archived_draft(app)
    _admin(client, app)
    token = fx.token_from(client, fx.detail_url(pp), fx.reactivate_url(pp))
    assert client.get(fx.reactivate_url(pp)).status_code == 405
    with app.app_context():
        internal = fx.stored_plan(pp).id
    for bad in ("no-such-plan", str(internal), pp + "x"):
        response = client.post(fx.reactivate_url(bad), data={fx.STATE_FIELD: token,
                                                             "confirm": "yes"})
        assert response.status_code == 404, bad
    # This plan's token, aimed at another plan's URL, is stale there.
    fx.reactivate(client, other_pp, token=token)
    assert _still_archived_draft(app, pp) and _still_archived_draft(app, other_pp)


def test_each_state_offers_exactly_its_own_controls(app, client):
    _admin(client, app)
    with app.app_context():
        actor = User.query.one()
        plans = {}
        for key, status, ever in (("draft", fx.DRAFT, False), ("active", fx.ACTIVE, True),
                                  ("archived", fx.ARCHIVED_STATUS, True),
                                  ("archived_draft", fx.ARCHIVED_STATUS, False)):
            row = fx.plan(actor, status=status, ever_activated=ever)
            fx.item(row, label="Course")
            plans[key] = row.public_id
    expected = {
        "draft": {"Activate plan", "Archive plan", "Add item", "Edit name", "Remove</button>"},
        "active": {"Archive plan"},
        "archived": {"Reactivate plan"},
        "archived_draft": {"Restore draft"},
    }
    controls = {"Activate plan", "Archive plan", "Reactivate plan", "Restore draft", "Add item",
                "Edit name", "Remove</button>"}
    for key, pp in plans.items():
        html = fx.page(client, fx.detail_url(pp))
        assert {c for c in controls if c in html} == expected[key], key


@pytest.mark.parametrize(
    "suffix",
    ["/delete", "/destroy", "/duplicate", "/copy", "/restore", "/unarchive", "/draft",
     "/assign", "/invoice", "/pay", "/payments", "/export", "/discount"],
)
def test_no_out_of_scope_endpoint_exists(app, client, suffix):
    pp, _ = _draft_with_items(app)
    _admin(client, app)
    url = fx.detail_url(pp) + suffix
    assert client.get(url).status_code == 404
    assert client.post(url, data={"confirm": "yes"}).status_code in (404, 405)
    with app.app_context():
        assert fx.stored_plan(pp).status == fx.DRAFT


def test_no_page_offers_an_out_of_scope_control_or_field(app, client):
    _admin(client, app)
    pp, (ip, _) = _draft_with_items(app, creator_email="admin@example.com")
    for url in (fx.LIST_URL, fx.NEW_URL, fx.detail_url(pp), fx.edit_url(pp),
                fx.item_new_url(pp), fx.item_edit_url(pp, ip)):
        html = fx.page(client, url)
        body = html.split('class="admin-main"', 1)[1]
        for forbidden in ("Delete", "Duplicate", "Invoice", "Receipt", "Refund", "Discount",
                          "Installment", "Scholarship", "Due date", "Quantity", "Pay now",
                          'name="currency', 'name="status"', 'name="quantity"',
                          'name="discount', 'name="due_'):
            if url == fx.LIST_URL and forbidden == 'name="status"':
                continue  # the list's GET lifecycle filter, not a form field
            assert forbidden not in body, (url, forbidden)


# ===========================================================================
# Escaping, internal ids and response headers
# ===========================================================================


def test_markup_is_escaped_on_every_page(app, client):
    _admin(client, app)
    pp = fx.create_plan(client, name="<script>alert(1)</script>",
                        description="<img src=x onerror=alert(2)>")
    fx.add_item(client, pp, label="<b>Bold</b>")
    with app.app_context():
        ip = FeePlanItem.query.one().public_id
    for url in (fx.LIST_URL, fx.detail_url(pp), fx.edit_url(pp), fx.item_edit_url(pp, ip)):
        html = fx.page(client, url)
        assert "<script>alert(1)</script>" not in html, url
        assert "<img src=x" not in html, url
        assert "<b>Bold</b>" not in html, url
    detail = fx.page(client, fx.detail_url(pp))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail
    assert "&lt;b&gt;Bold&lt;/b&gt;" in detail


def test_no_internal_identifier_reaches_a_url_a_field_or_the_page(app, client):
    _admin(client, app)
    with app.app_context():
        actor = User.query.one()
        for index in range(7):
            fx.plan(actor, name=f"Filler {index}")
    pp = fx.create_plan(client)
    fx.add_item(client, pp, label="Course")
    fx.add_item(client, pp, label="Registration")
    with app.app_context():
        plan_id = fx.stored_plan(pp).id
        item_ids = [row.id for row in FeePlanItem.query.all()]
        ip = FeePlanItem.query.first().public_id
    for url in (fx.LIST_URL, fx.detail_url(pp), fx.edit_url(pp), fx.item_new_url(pp),
                fx.item_edit_url(pp, ip)):
        html = fx.page(client, url)
        assert not re.search(r"/fee-plans/\d+[/\"?]", html), url
        assert not re.search(r"/items/\d+[/\"?]", html), url
        for name, value in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html):
            assert name not in ("id", "plan_id", "item_id", "fee_plan_id"), (url, name)
            if name not in ("csrf_token", "state_token", "amount", "label", "name"):
                assert value not in {str(plan_id), *map(str, item_ids)}, (url, name, value)
        assert "fee_plan_id" not in html and "created_by_id" not in html


def test_every_response_is_private_and_no_store(app, client):
    _admin(client, app)
    pp, (ip, _) = _draft_with_items(app, creator_email="admin@example.com")
    responses = [client.get(url) for url in (
        fx.LIST_URL, fx.NEW_URL, fx.detail_url(pp), fx.edit_url(pp), fx.item_new_url(pp),
        fx.item_edit_url(pp, ip), fx.LIST_URL + "?status=draft&page=2")]
    responses += [
        fx.edit_plan(client, pp, name="Renamed"),
        fx.add_item(client, pp, amount="1,0"),
        fx.remove_item(client, pp, ip),
        fx.activate(client, pp, confirm=False),
        fx.activate(client, pp),
        fx.archive(client, pp),
        fx.reactivate(client, pp),
        client.post(fx.NEW_URL, data=fx.plan_form(token="forged")),
    ]
    for response in responses:
        assert response.headers.get("Cache-Control") == "private, no-store"
        assert "Cookie" in response.headers.get("Vary", "")


# ===========================================================================
# The list: pagination, filtering, totals and query bounds
# ===========================================================================


def _names_in(html):
    return re.findall(r'<td style="padding: var\(--space-3\)"><strong>([^<]+)</strong>', html)


def test_the_list_is_paginated_newest_first_without_a_count(app, client):
    _admin(client, app)
    with app.app_context():
        actor = User.query.one()
        for index in range(45):
            fx.plan(actor, name=f"Plan {index:02d}")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    with app.app_context():
        sa_event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        first = fx.page(client, fx.LIST_URL)
    finally:
        with app.app_context():
            sa_event.remove(db.engine, "before_cursor_execute", _rec)
    assert _names_in(first) == [f"Plan {index:02d}" for index in range(44, 24, -1)]
    assert "page=2" in first and "Previous" not in first
    assert not any("count(" in s.lower() for s in statements)
    assert any("FROM fee_plans" in s and "LIMIT" in s for s in statements)

    second = fx.page(client, fx.LIST_URL + "?page=2")
    assert _names_in(second) == [f"Plan {index:02d}" for index in range(24, 4, -1)]
    third = fx.page(client, fx.LIST_URL + "?page=3")
    assert _names_in(third) == [f"Plan {index:02d}" for index in range(4, -1, -1)]
    assert "page=4" not in third and "Previous" in third
    for bad in ("?page=99", "?page=abc", "?page=-1", "?page=0"):
        assert _names_in(fx.page(client, fx.LIST_URL + bad))[0] == "Plan 44", bad


def test_the_status_filter_is_validated(app, client):
    _admin(client, app)
    with app.app_context():
        actor = User.query.one()
        fx.plan(actor, name="Draft one")
        fx.plan(actor, name="Active one", status=fx.ACTIVE)
        fx.plan(actor, name="Archived one", status=fx.ARCHIVED_STATUS)
    assert _names_in(fx.page(client, fx.LIST_URL + "?status=active")) == ["Active one"]
    assert _names_in(fx.page(client, fx.LIST_URL + "?status=archived")) == ["Archived one"]
    assert _names_in(fx.page(client, fx.LIST_URL + "?status=draft")) == ["Draft one"]
    for bogus in ("?status=deleted", "?status=' OR 1=1 --", "?status=ACTIVE"):
        assert len(_names_in(fx.page(client, fx.LIST_URL + bogus))) == 3, bogus


def test_list_totals_are_exact_and_count_active_items_only(app, client):
    _admin(client, app)
    with app.app_context():
        owner = fx.plan(User.query.one(), name="Exact")
        fx.item(owner, label="A", amount="0.1")
        fx.item(owner, label="B", amount="0.2")
        fx.item(owner, label="C", amount="99999.999", status=fx.ITEM_REMOVED)
    html = fx.page(client, fx.LIST_URL)
    assert re.search(r">2</td>\s*<td[^>]*>0.300</td>", html)


def _select_count(app, client, url):
    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append(statement)

    with app.app_context():
        sa_event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(url).status_code == 200
    finally:
        with app.app_context():
            sa_event.remove(db.engine, "before_cursor_execute", _rec)
    return len([s for s in recorded if s.strip().upper().startswith("SELECT")])


def test_the_list_and_detail_cost_do_not_grow_with_the_catalogue(app, client):
    def build(plans, items, removed):
        with app.app_context():
            db.drop_all()
            db.create_all()
            actor = fx.admin(name="Boss")
            other = fx.admin("second@example.com", name="Second")
            last = None
            for index in range(plans):
                last = fx.plan(actor if index % 2 else other, name=f"Plan {index}")
                for position in range(items):
                    fx.item(last, label=f"Item {position}", amount="10.5")
                for position in range(removed):
                    fx.item(last, label=f"Old {position}", status=fx.ITEM_REMOVED,
                            removed_by=other)
            pp = last.public_id
        fx.login_as(client, "admin@example.com")
        return pp

    small = build(1, 1, 0)
    small_list = _select_count(app, client, fx.LIST_URL)
    small_detail = _select_count(app, client, fx.detail_url(small))
    large = build(20, 20, 12)
    assert _select_count(app, client, fx.LIST_URL) == small_list
    assert _select_count(app, client, fx.detail_url(large)) == small_detail


def test_removed_history_is_capped(app, client):
    _admin(client, app)
    with app.app_context():
        owner = fx.plan(User.query.one())
        for index in range(55):
            fx.item(owner, label=f"Old {index}", status=fx.ITEM_REMOVED)
        pp = owner.public_id
    html = fx.page(client, fx.detail_url(pp))
    assert html.count("Old ") == 50
    assert "Showing the 50 most recent removals" in html


# ===========================================================================
# Navigation
# ===========================================================================


def test_the_navigation_links_fee_plans_and_payments(app, client):
    _admin(client, app)
    dashboard = fx.page(client, "/admin/dashboard")
    assert 'href="/admin/fee-plans"' in dashboard
    # Phase 5 / M05 replaced the disabled Payments placeholder with a real link.
    assert not re.search(r"Payments <span class=\"badge badge--neutral\">Soon</span>", dashboard)
    assert 'href="/admin/payments"' in dashboard
    assert client.get("/admin/payments").status_code == 200
    own_page = fx.page(client, fx.LIST_URL)
    assert re.search(
        r'<a class="admin-nav__link admin-nav__link--active"[^>]*href="/admin/fee-plans"', own_page
    )
