"""Phase 5 / M04 -- server-built invoice snapshots and the one audit-event
writer.

The snapshot's exact canonical layout, exact Decimal totals and the absence
of internal ids; every refusal of the layout validator; every rule of
``record_invoice_event``; the plain-text change descriptions; reason
normalization; and a scan proving that nothing in the application constructs
an event outside the writer or rewrites one anywhere.
"""

import copy
import json
import pathlib
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest

import tests.fee_assignment_fixtures as fees
import tests.invoice_fixtures as fx
from app.extensions import db
from app.models import PaymentAuditEvent
from app.models.fee_plan import TEXT_CONTROL, TEXT_MISSING, TEXT_TOO_LONG
from app.models.payment_audit_event import (
    INVOICE_SNAPSHOT_SCHEMA,
    canonical_snapshot_json,
    normalize_audit_reason,
    snapshot_amount_text,
    validate_invoice_snapshot,
)
from app.services.invoice_audit import build_invoice_snapshot, edit_event_kind, record_invoice_event
from app.services.invoice_queries import describe_snapshot_changes

_AP = "a" * 36
_ADMIN = SimpleNamespace(id=3, role="administrator", status="active")


def _line(row_id, public_id, amount, kind="course", label=None, status="active"):
    return SimpleNamespace(id=row_id, public_id=public_id, kind=kind, label=label or f"Line {row_id}",
                           status=status, amount=Decimal(amount))


def _invoice(status="draft", number=None, version=1, row_id=7):
    return SimpleNamespace(id=row_id, public_id="i" * 36, status=status, invoice_number=number,
                           version=version, currency_code="LYD")


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
# Snapshots
# ===========================================================================


def test_a_snapshot_is_canonical_complete_and_free_of_internal_ids(app):
    with app.app_context():
        actor = fees.admin()
        assignment = fees.assignment(fees.enrollment(), fees.active_plan(actor), actor)
        owner = fx.invoice(assignment, actor, lines=())
        registration = fx.line(owner, label="Registration", amount="50.000", kind="registration")
        gone = fx.line(owner, label="Old books", amount="15.000", status=fx.LINE_REMOVED,
                       removed_by=actor)
        course = fx.line(owner, label="Course", amount="1200.500")
        snapshot = build_invoice_snapshot(owner, assignment.public_id, [course, gone, registration])
        assert snapshot == {
            "schema": INVOICE_SNAPSHOT_SCHEMA,
            "invoice_public_id": owner.public_id,
            "status": "draft",
            "invoice_number": None,
            "student_fee_assignment_public_id": assignment.public_id,
            "currency_code": "LYD",
            "total": "1250.5000",
            "items": [
                {"public_id": registration.public_id, "kind": "registration",
                 "label": "Registration", "status": "active", "amount": "50.0000"},
                {"public_id": gone.public_id, "kind": "course", "label": "Old books",
                 "status": "removed", "amount": "15.0000"},
                {"public_id": course.public_id, "kind": "course", "label": "Course",
                 "status": "active", "amount": "1200.5000"},
            ],
        }
        text = canonical_snapshot_json(snapshot)
        assert text == json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False)
        assert json.loads(text) == snapshot

        internal = {str(value) for value in (owner.id, assignment.id, registration.id, gone.id,
                                             course.id, actor.id, assignment.enrollment_id,
                                             assignment.fee_plan_id)}
        for key, value in _values(snapshot):
            assert value is None or isinstance(value, (str, dict, list)), (key, value)
            if isinstance(value, str):
                assert value not in internal, (key, value)
            if key is not None:
                assert "id" not in key.split("_") or key.endswith("public_id"), key


def test_snapshot_lines_follow_internal_id_order_and_totals_are_exact_decimals(app):
    with app.app_context():
        invoice = _invoice()
        lines = [_line(3, "l3", "0.2000"), _line(1, "l1", "0.1000"), _line(2, "l2", "5", status="removed")]
        snapshot = build_invoice_snapshot(invoice, _AP, lines)
        assert [line["public_id"] for line in snapshot["items"]] == ["l1", "l2", "l3"]
        assert snapshot["total"] == "0.3000"
        many = [_line(index, f"m{index}", "99999.999", label=f"Big {index}") for index in range(20)]
        assert build_invoice_snapshot(invoice, _AP, many)["total"] == "1999999.9800"
    for bad in (0.3, 3, Decimal("NaN"), Decimal("-1"), Decimal("1.00001"), "1.0000"):
        with pytest.raises(ValueError):
            snapshot_amount_text(bad)


def _valid():
    return {
        "schema": INVOICE_SNAPSHOT_SCHEMA,
        "invoice_public_id": "i" * 36,
        "status": "issued",
        "invoice_number": "INV-2026-000001",
        "student_fee_assignment_public_id": _AP,
        "currency_code": "LYD",
        "total": "1250.5000",
        "items": [
            {"public_id": "l1", "kind": "registration", "label": "Registration",
             "status": "active", "amount": "50.0000"},
            {"public_id": "l2", "kind": "course", "label": "Course", "status": "active",
             "amount": "1200.5000"},
            {"public_id": "l3", "kind": "course", "label": "Old", "status": "removed",
             "amount": "15.0000"},
        ],
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


_MUTATIONS = {
    "extra card data": _set(["card_number"], "4111111111111111"),
    "extra csrf token": _set(["csrf_token"], "x"),
    "missing total": _delete("total"),
    "unknown schema": _set(["schema"], "v0"),
    "unknown status": _set(["status"], "paid"),
    "draft with a number": _set(["status"], "draft"),
    "issued without a number": _set(["invoice_number"], None),
    "malformed number": _set(["invoice_number"], "INV-2026-000000"),
    "foreign currency": _set(["currency_code"], "USD"),
    "integer invoice id": _set(["invoice_public_id"], 42),
    "empty invoice id": _set(["invoice_public_id"], ""),
    "oversized assignment id": _set(["student_fee_assignment_public_id"], "x" * 37),
    "items not a list": _set(["items"], {"l1": {}}),
    "item internal id": _set(["items", 0, "id"], 7),
    "item plan reference": _set(["items", 0, "fee_plan_item_id"], "p"),
    "integer item public id": _set(["items", 0, "public_id"], 1),
    "unknown item kind": _set(["items", 0, "kind"], "discount"),
    "unknown item status": _set(["items", 0, "status"], "deleted"),
    "nested label": _set(["items", 0, "label"], {"text": "Registration"}),
    "padded label": _set(["items", 0, "label"], " Registration"),
    "control in label": _set(["items", 0, "label"], "Regis\x07tration"),
    "amount without places": _set(["items", 0, "amount"], "50"),
    "amount with five places": _set(["items", 0, "amount"], "50.00000"),
    "amount with a leading zero": _set(["items", 0, "amount"], "050.0000"),
    "negative amount": _set(["items", 0, "amount"], "-50.0000"),
    "float amount": _set(["items", 0, "amount"], 50.0),
    "zero amount": _set(["items", 0, "amount"], "0.0000"),
    "amount above the maximum": _set(["items", 0, "amount"], "100000.0000"),
    "repeated item": _set(["items", 1, "public_id"], "l1"),
    "short total": _set(["total"], "1250.5"),
    "total counting a removed line": _set(["total"], "1265.5000"),
    "float total": _set(["total"], 1250.5),
    "too many items": _set(["items"], [
        {"public_id": f"x{index}", "kind": "course", "label": f"L {index}", "status": "removed",
         "amount": "1.0000"} for index in range(101)]),
}


def test_the_valid_layout_is_accepted_and_copied():
    original = _valid()
    checked = validate_invoice_snapshot(original)
    assert checked == original and checked is not original


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_the_snapshot_validator_refuses_anything_but_the_exact_layout(name):
    snapshot = copy.deepcopy(_valid())
    _MUTATIONS[name](snapshot)
    with pytest.raises(ValueError):
        validate_invoice_snapshot(snapshot)


# ===========================================================================
# The writer
# ===========================================================================


def _change(kind):
    """``(invoice, before, after, version_before)`` for one legitimate change
    of `kind`, built from plain objects."""
    lines = [_line(1, "l1", "10.0000"), _line(2, "l2", "20.0000")]
    if kind == "invoice_draft_created":
        invoice = _invoice()
        return invoice, None, build_invoice_snapshot(invoice, _AP, lines), None
    start = {"invoice_draft_edited": ("draft", None), "invoice_issued": ("draft", None),
             "invoice_issued_edited": ("issued", "INV-2026-000001"),
             "invoice_cancelled": ("issued", "INV-2026-000001")}[kind]
    invoice = _invoice(status=start[0], number=start[1], version=3)
    before = build_invoice_snapshot(invoice, _AP, lines)
    if kind in ("invoice_draft_edited", "invoice_issued_edited"):
        lines[0].amount = Decimal("12.0000")
    elif kind == "invoice_issued":
        invoice.status, invoice.invoice_number = "issued", "INV-2026-000001"
    else:
        invoice.status = "cancelled"
    invoice.version = 4
    return invoice, before, build_invoice_snapshot(invoice, _AP, lines), 3


def _kwargs(change, reason=None, **overrides):
    invoice, before, after, version_before = _change(change)
    values = dict(invoice=invoice, actor=_ADMIN, kind=change, version_before=version_before,
                  before_snapshot=before, after_snapshot=after, reason=reason,
                  moment=fx.CREATED_AT)
    values.update(overrides)
    return values


_ACCEPTED = {
    "invoice_draft_created": None,
    "invoice_draft_edited": None,
    "invoice_issued": None,
    "invoice_issued_edited": "Corrected the course fee",
    "invoice_cancelled": "Duplicate charge",
}


def _pending_events():
    return [obj for obj in db.session.new if isinstance(obj, PaymentAuditEvent)]


@pytest.mark.parametrize("kind", sorted(_ACCEPTED))
def test_the_writer_adds_exactly_one_event_for_each_legitimate_change(app, kind):
    with app.app_context():
        values = _kwargs(kind, reason=_ACCEPTED[kind])
        entry = record_invoice_event(**values)
        assert _pending_events() == [entry]
        assert (entry.invoice_id, entry.actor_id, entry.kind, entry.occurred_at,
                entry.invoice_version_before, entry.invoice_version_after, entry.reason) == (
            7, 3, kind, fx.CREATED_AT, values["version_before"], values["invoice"].version,
            _ACCEPTED[kind])
        assert entry.before_snapshot == values["before_snapshot"]
        assert entry.after_snapshot == values["after_snapshot"]
        db.session.expunge(entry)


def test_a_real_creation_event_is_stored_as_written(app):
    with app.app_context():
        actor = fees.admin()
        assignment = fees.assignment(fees.enrollment(), fees.active_plan(actor), actor)
        owner = fx.invoice(assignment, actor)
        lines = fx.stored_lines(owner.public_id)
        owner = fx.stored_invoice(owner.public_id)
        after = build_invoice_snapshot(owner, assignment.public_id, lines)
        record_invoice_event(invoice=owner, actor=actor, kind="invoice_draft_created",
                             version_before=None, before_snapshot=None, after_snapshot=after,
                             reason=None, moment=fx.CREATED_AT)
        db.session.commit()
        (stored,) = PaymentAuditEvent.query.all()
        assert (stored.invoice_id, stored.actor_id, stored.kind, stored.invoice_version_before,
                stored.invoice_version_after, stored.reason, stored.before_snapshot) == (
            owner.id, actor.id, "invoice_draft_created", None, 1, None, None)
        assert stored.after_snapshot == after


_REFUSED = {
    "no actor": lambda: _kwargs("invoice_draft_edited", actor=None),
    "a teacher": lambda: _kwargs("invoice_draft_edited",
                                 actor=SimpleNamespace(id=3, role="teacher", status="active")),
    "a suspended administrator": lambda: _kwargs(
        "invoice_draft_edited", actor=SimpleNamespace(id=3, role="administrator",
                                                      status="suspended")),
    "an unknown kind": lambda: _kwargs("invoice_draft_edited", kind="payment_recorded"),
    "an unsaved invoice": lambda: _kwargs(
        "invoice_draft_edited", invoice=SimpleNamespace(**dict(vars(_change(
            "invoice_draft_edited")[0]), id=None))),
    "a creation from a version": lambda: _kwargs("invoice_draft_created", version_before=1),
    "a creation with a before snapshot": lambda: _kwargs(
        "invoice_draft_created", before_snapshot=_change("invoice_draft_created")[2]),
    "a creation past version 1": lambda: _kwargs(
        "invoice_draft_created", invoice=_invoice(version=2)),
    "a skipped version": lambda: _kwargs("invoice_draft_edited", version_before=2),
    "a boolean version": lambda: _kwargs("invoice_draft_edited", version_before=True),
    "a later event without a version": lambda: _kwargs("invoice_draft_edited",
                                                        version_before=None),
    "a later event without a before snapshot": lambda: _kwargs("invoice_draft_edited",
                                                                before_snapshot=None),
    "a draft edit with a reason": lambda: _kwargs("invoice_draft_edited", reason="Why"),
    "an issue with a reason": lambda: _kwargs("invoice_issued", reason="Why"),
    "a post-issue edit without a reason": lambda: _kwargs("invoice_issued_edited"),
    "a post-issue edit with a blank reason": lambda: _kwargs("invoice_issued_edited",
                                                              reason="   "),
    "a post-issue edit with a padded reason": lambda: _kwargs("invoice_issued_edited",
                                                               reason=" Why "),
    "a cancellation without a reason": lambda: _kwargs("invoice_cancelled"),
    "a draft edit labelled as a post-issue edit": lambda: _kwargs(
        "invoice_draft_edited", kind="invoice_issued_edited", reason="Why"),
    "an issue labelled as a draft edit": lambda: _kwargs("invoice_issued",
                                                         kind="invoice_draft_edited"),
    "an after snapshot of another state": lambda: _kwargs(
        "invoice_draft_edited", after_snapshot=_change("invoice_issued")[2]),
    "an after snapshot of another invoice": lambda: _kwargs(
        "invoice_draft_edited",
        after_snapshot=dict(_change("invoice_draft_edited")[2], invoice_public_id="z" * 36)),
    "a change that changed nothing": lambda: _kwargs(
        "invoice_draft_edited", before_snapshot=_change("invoice_draft_edited")[2]),
    "an uncontrolled after snapshot": lambda: _kwargs(
        "invoice_draft_edited",
        after_snapshot=dict(_change("invoice_draft_edited")[2], session="abc")),
}


@pytest.mark.parametrize("name", sorted(_REFUSED))
def test_the_writer_refuses_and_adds_nothing(app, name):
    with app.app_context():
        with pytest.raises(ValueError):
            record_invoice_event(**_REFUSED[name]())
        assert _pending_events() == []


def test_edit_event_kind():
    assert edit_event_kind("draft") == "invoice_draft_edited"
    assert edit_event_kind("issued") == "invoice_issued_edited"
    with pytest.raises(ValueError):
        edit_event_kind("cancelled")


# ===========================================================================
# Presentation and reasons
# ===========================================================================


def test_changes_are_described_from_the_two_snapshots(app):
    with app.app_context():
        invoice = _invoice()
        lines = [_line(1, "l1", "50", kind="registration", label="Registration"),
                 _line(2, "l2", "1200.5", label="Course"), _line(3, "l3", "10", label="Kept")]
        created = build_invoice_snapshot(invoice, _AP, lines)
        assert describe_snapshot_changes(None, created) == [
            "Draft created with 3 lines copied from the fee plan, totalling 1,260.500 LYD."]

        lines[0].status = "removed"
        lines[1].kind, lines[1].label, lines[1].amount = "registration", "Fees", Decimal("1300")
        lines.append(_line(4, "l4", "25.5", label="Books"))
        invoice.status, invoice.invoice_number = "issued", "INV-2026-000001"
        after = build_invoice_snapshot(invoice, _AP, lines)
        assert describe_snapshot_changes(created, after) == [
            "Status changed from Draft to Issued.",
            "Invoice number INV-2026-000001 allocated.",
            "Removed Registration line “Registration” of 50.000 LYD.",
            "Changed Course line “Course”: kind from Course to Registration; label from "
            "“Course” to “Fees”; amount from 1,200.500 to 1,300.000 LYD.",
            "Added Course line “Books” of 25.500 LYD.",
            "Total changed from 1,260.500 to 1,335.500 LYD.",
        ]


def test_reasons_are_normalized_plain_text():
    assert normalize_audit_reason("  Corrected\r\nthe\tfee \r ") == ("Corrected\nthe\tfee", None)
    assert normalize_audit_reason("") == (None, TEXT_MISSING)
    assert normalize_audit_reason("  \n ") == (None, TEXT_MISSING)
    assert normalize_audit_reason(None) == (None, TEXT_MISSING)
    assert normalize_audit_reason("", required=False) == (None, None)
    assert normalize_audit_reason("bad\x00") == (None, TEXT_CONTROL)
    assert normalize_audit_reason("a‮b") == (None, TEXT_CONTROL)
    assert normalize_audit_reason("x" * 501) == (None, TEXT_TOO_LONG)
    assert normalize_audit_reason("x" * 500) == ("x" * 500, None)


def test_only_the_audit_writer_constructs_an_event_and_nothing_rewrites_one(app):
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    constructing, rewriting = set(), []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?<!class )\bPaymentAuditEvent\(", text):
            constructing.add(path.relative_to(root).as_posix())
        for pattern in (r"PaymentAuditEvent\)\.(update|delete)", r"update\(\s*PaymentAuditEvent",
                        r"delete\(\s*PaymentAuditEvent", r"payment_audit_events\s+SET",
                        r"DELETE\s+FROM\s+payment_audit_events"):
            if re.search(pattern, text, re.I):
                rewriting.append((path.name, pattern))
    assert constructing == {"services/invoice_audit.py"}
    assert rewriting == []
    for template in (root / "templates").rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert not re.search(r'action="[^"]*audit', text, re.I), template
    for rule in app.url_map.iter_rules():
        assert "audit" not in rule.rule and "sequence" not in rule.rule, rule.rule
