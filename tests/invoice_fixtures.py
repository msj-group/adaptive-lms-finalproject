"""Shared fixtures for the Phase 5 / M04 invoice test modules.

Kept in one module, like ``tests/fee_assignment_fixtures.py`` (whose academic
chain, Enrollment, plan and assignment helpers it reuses), so the model, audit,
route, transaction and migration suites build the same invoices.

Row helpers write straight into the tables, so a test about (say) a cancelled
invoice refusing an edit is not also a test of the cancellation route. The
route helpers at the bottom drive the application's own write path and read
every signed token out of the page the server rendered.

Every stored moment is a whole-second 2026 UTC instant earlier than the real
clock, so the timestamp CHECKs hold when a route later writes "now" on top of
a fixture row, and a fixture row never looks newer than a freshly rendered
form.
"""

from datetime import datetime

import tests.fee_assignment_fixtures as fees
import tests.fee_plan_fixtures as plans
from app.extensions import db
from app.models import (
    Enrollment,
    FeePlan,
    Invoice,
    InvoiceItem,
    InvoiceNumberSequence,
    PaymentAuditEvent,
    StudentFeeAssignment,
    User,
)
from app.services.invoice_audit import build_invoice_snapshot
from app.services.schedule_occurrences import to_app_local

PW = plans.PW
STATE_FIELD = plans.STATE_FIELD
STALE_TEXT = plans.STALE_TEXT
INTEGRITY_TEXT = plans.INTEGRITY_TEXT
MISSING = fees.MISSING

DRAFT = "draft"
ISSUED = "issued"
CANCELLED = "cancelled"
LINE_ACTIVE = "active"
LINE_REMOVED = "removed"

CREATED_AT = datetime(2026, 6, 1, 9, 0, 0)
REMOVED_AT = datetime(2026, 6, 1, 12, 0, 0)
ISSUED_AT = datetime(2026, 6, 2, 9, 0, 0)
CANCELLED_AT = datetime(2026, 6, 3, 9, 0, 0)

CREATED_OK_TEXT = "Draft invoice created from the assigned fee plan"
LINE_ADDED_TEXT = "Line added."
LINE_SAVED_TEXT = "Line saved."
LINE_REMOVED_TEXT = "Line removed. It is kept as history."
ISSUED_OK_TEXT = "Invoice issued as"
CANCELLED_OK_TEXT = "Invoice cancelled. It is kept as read-only history."
ALREADY_CANCELLED_TEXT = "This invoice is already cancelled"
READ_ONLY_TEXT = "A cancelled invoice and its lines are read-only"
OPEN_INVOICE_TEXT = "already has a draft or issued invoice"
ASSIGNMENT_CANCELLED_TEXT = "This fee assignment is cancelled, so no new invoice"
ENROLLMENT_INACTIVE_TEXT = "This enrollment is withdrawn, so a new invoice"
STUDENT_INACTIVE_TEXT = "account is not active, so a new invoice"
ACADEMIC_INACTIVE_TEXT = "is archived, so a new invoice"
PLAN_UNAVAILABLE_TEXT = "has no recorded activation"
PLAN_ITEMS_INVALID_TEXT = "does not have a valid set of items"
LABEL_TAKEN_TEXT = "already has a line with this label"
LINE_LIMIT_TEXT = "at most 20 active lines"
ROW_LIMIT_TEXT = "removed lines included"
LAST_LINE_TEXT = "must keep at least one line"
LINE_ALREADY_REMOVED_TEXT = "This line has already been removed"
NOT_ISSUABLE_TEXT = "Only a draft invoice can be issued"
LINES_INVALID_TEXT = "This draft cannot be issued"
EXHAUSTED_TEXT = "No invoice number is left for this year"
ISSUE_CONFIRM_TEXT = "tick the confirmation box before issuing"
CANCEL_CONFIRM_TEXT = "tick the confirmation box before cancelling"
REASON_REQUIRED_TEXT = "Give the reason for this change"
CANCEL_REASON_TEXT = "Give the reason for cancelling this invoice"
NO_CHANGES_TEXT = "Nothing was changed, so nothing was saved"
ASSIGNMENT_CANCEL_BLOCKED_TEXT = "Cancel that invoice explicitly"
ASSIGNMENT_CANCEL_NOTE_TEXT = "Cancel its draft or issued invoice first."

login_as = plans.login_as
fresh_identity = plans.fresh_identity
admin = plans.admin
user = plans.user
state_in = plans.state_in
page = plans.page
followed = fees.followed

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def base_url(gp, ep, ap):
    return f"/admin/groups/{gp}/enrollments/{ep}/fee-assignments/{ap}/invoices"


def invoices_url(w):
    return base_url(w["gp"], w["ep"], w["ap"])


def new_url(w):
    return invoices_url(w) + "/new"


def detail_url(w, ip):
    return f"{invoices_url(w)}/{ip}"


def edit_url(w, ip):
    return detail_url(w, ip) + "/edit"


def line_new_url(w, ip):
    return detail_url(w, ip) + "/items/new"


def line_edit_url(w, ip, lp):
    return f"{detail_url(w, ip)}/items/{lp}/edit"


def line_remove_url(w, ip, lp):
    return f"{detail_url(w, ip)}/items/{lp}/remove"


def issue_url(w, ip):
    return detail_url(w, ip) + "/issue"


def cancel_url(w, ip):
    return detail_url(w, ip) + "/cancel"


# ---------------------------------------------------------------------------
# Rows, written directly
# ---------------------------------------------------------------------------

DEFAULT_LINES = fees.DEFAULT_ITEMS


def invoice(assignment, actor, status=DRAFT, lines=DEFAULT_LINES, number=None, version=None,
            created_at=CREATED_AT):
    """One invoice in a consistent lifecycle state, with active lines.

    An issued invoice needs `number`. A cancelled invoice given a `number` was
    issued before it was cancelled; without one it is a cancelled draft.
    """
    issued = status == ISSUED or (status == CANCELLED and number is not None)
    cancelled = status == CANCELLED
    row = Invoice(
        student_fee_assignment_id=assignment.id,
        currency_code="LYD",
        status=status,
        invoice_number=number if issued else None,
        issued_at=ISSUED_AT if issued else None,
        issued_by_id=actor.id if issued else None,
        cancelled_at=CANCELLED_AT if cancelled else None,
        cancelled_by_id=actor.id if cancelled else None,
        version=version or (1 + int(issued) + int(cancelled)),
        created_at=created_at,
        updated_at=CANCELLED_AT if cancelled else ISSUED_AT if issued else created_at,
    )
    db.session.add(row)
    db.session.commit()
    for kind, label, amount in lines:
        line(row, label=label, amount=amount, kind=kind)
    return row


def line(owner, label=None, amount="100.000", kind="course", status=LINE_ACTIVE, removed_by=None,
         version=1):
    removed = status == LINE_REMOVED
    assert removed_by is not None or not removed, "a removed line needs its remover"
    row = InvoiceItem(
        invoice_id=owner.id,
        kind=kind,
        label=label or f"Line {_next()}",
        amount=amount,
        status=status,
        removed_at=REMOVED_AT if removed else None,
        removed_by_id=removed_by.id if removed else None,
        version=version,
        created_at=CREATED_AT,
        updated_at=REMOVED_AT if removed else CREATED_AT,
    )
    db.session.add(row)
    db.session.commit()
    return row


def sequence(calendar_year, last_number, moment=CREATED_AT):
    row = InvoiceNumberSequence(
        calendar_year=calendar_year, last_number=last_number, created_at=moment, updated_at=moment
    )
    db.session.add(row)
    db.session.commit()
    return row


def event(owner, actor, version_after, kind=None, reason=None, occurred_at=CREATED_AT):
    """One valid audit event describing `owner` as it is now, written
    directly. Version 1 is a creation; anything later a draft edit unless
    `kind` says otherwise."""
    rows = InvoiceItem.query.filter_by(invoice_id=owner.id).order_by(InvoiceItem.id).all()
    assignment = db.session.get(StudentFeeAssignment, owner.student_fee_assignment_id)
    snapshot = build_invoice_snapshot(owner, assignment.public_id, rows)
    created = version_after == 1
    row = PaymentAuditEvent(
        invoice_id=owner.id,
        actor_id=actor.id,
        kind=kind or ("invoice_draft_created" if created else "invoice_draft_edited"),
        occurred_at=occurred_at,
        invoice_version_before=None if created else version_after - 1,
        invoice_version_after=version_after,
        reason=reason,
        before_snapshot=None if created else snapshot,
        after_snapshot=snapshot,
    )
    db.session.add(row)
    db.session.commit()
    return row


def world(app, admin_email="admin@example.com"):
    """M03's world -- an active Administrator, one active Enrollment in a
    fully active chain and one active two-item plan -- plus an ``assigned``
    fee assignment of that plan. Plain scalars only."""
    w = fees.world(app, admin_email)
    with app.app_context():
        row = fees.assignment(
            db.session.get(Enrollment, w["enrollment_id"]),
            db.session.get(FeePlan, w["plan_id"]),
            db.session.get(User, w["admin_id"]),
        )
        w.update(ap=row.public_id, assignment_id=row.id)
    return w


def login_world(app, client):
    w = world(app)
    login_as(client, "admin@example.com")
    return w


def stored_invoices(w):
    db.session.expire_all()
    return (
        Invoice.query.filter_by(student_fee_assignment_id=w["assignment_id"])
        .order_by(Invoice.id)
        .all()
    )


def stored_invoice(ip):
    db.session.expire_all()
    return Invoice.query.filter_by(public_id=ip).one()


def stored_lines(ip):
    owner = stored_invoice(ip)
    return InvoiceItem.query.filter_by(invoice_id=owner.id).order_by(InvoiceItem.id).all()


def stored_events(ip):
    owner = stored_invoice(ip)
    return (
        PaymentAuditEvent.query.filter_by(invoice_id=owner.id)
        .order_by(PaymentAuditEvent.id)
        .all()
    )


def financial_record():
    """Every invoice, line, event and sequence row -- what "nothing was
    written" is compared against."""
    db.session.expire_all()
    return (
        [(r.id, r.public_id, r.student_fee_assignment_id, r.status, r.invoice_number, r.issued_at,
          r.issued_by_id, r.cancelled_at, r.cancelled_by_id, r.version, r.created_at, r.updated_at)
         for r in Invoice.query.order_by(Invoice.id)],
        [(r.id, r.public_id, r.invoice_id, r.kind, r.label, r.amount, r.status, r.removed_at,
          r.removed_by_id, r.version, r.updated_at)
         for r in InvoiceItem.query.order_by(InvoiceItem.id)],
        [(r.id, r.invoice_id, r.kind, r.invoice_version_before, r.invoice_version_after, r.reason)
         for r in PaymentAuditEvent.query.order_by(PaymentAuditEvent.id)],
        [(r.calendar_year, r.last_number, r.updated_at)
         for r in InvoiceNumberSequence.query.order_by(InvoiceNumberSequence.id)],
    )


def record(app):
    with app.app_context():
        return financial_record()


def center_year(app, moment):
    return to_app_local(app.config.get("APP_TIMEZONE", "UTC"), moment).year


# ---------------------------------------------------------------------------
# Route-driven helpers
# ---------------------------------------------------------------------------


def ip_from(response):
    location = response.headers["Location"]
    assert "/invoices/" in location, location
    return location.rstrip("/").split("/")[-1]


def create_token(client, w):
    return state_in(page(client, new_url(w)), new_url(w))


def create(client, w, token=None):
    if token is None:
        token = create_token(client, w)
    return client.post(new_url(w), data={STATE_FIELD: token})


def create_draft(client, w):
    """Drive draft creation; the new invoice's public id."""
    response = create(client, w)
    assert response.status_code == 302, response.status_code
    return ip_from(response)


def line_form(kind="course", label="Books", amount="25.500", reason=None, token=""):
    data = {"kind": kind, "label": label, "amount": amount, STATE_FIELD: token}
    if reason is not None:
        data["reason"] = reason
    return data


def add_line_token(client, w, ip):
    return state_in(page(client, line_new_url(w, ip)), line_new_url(w, ip))


def add_line(client, w, ip, token=None, **fields):
    if token is None:
        token = add_line_token(client, w, ip)
    return client.post(line_new_url(w, ip), data=line_form(token=token, **fields))


def edit_line_token(client, w, ip, lp):
    return state_in(page(client, line_edit_url(w, ip, lp)), line_edit_url(w, ip, lp))


def edit_line(client, w, ip, lp, token=None, **fields):
    if token is None:
        token = edit_line_token(client, w, ip, lp)
    return client.post(line_edit_url(w, ip, lp), data=line_form(token=token, **fields))


def remove_line_token(client, w, ip, lp):
    return state_in(page(client, line_remove_url(w, ip, lp)), line_remove_url(w, ip, lp))


def remove_line(client, w, ip, lp, reason=None, token=None):
    if token is None:
        token = remove_line_token(client, w, ip, lp)
    data = {STATE_FIELD: token}
    if reason is not None:
        data["reason"] = reason
    return client.post(line_remove_url(w, ip, lp), data=data)


def issue_token(client, w, ip):
    return state_in(page(client, detail_url(w, ip)), issue_url(w, ip))


def issue(client, w, ip, confirm=True, token=None):
    if token is None:
        token = issue_token(client, w, ip)
    data = {STATE_FIELD: token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(issue_url(w, ip), data=data)


def cancel_token(client, w, ip):
    return state_in(page(client, cancel_url(w, ip)), cancel_url(w, ip))


def cancel(client, w, ip, reason="Duplicate charge", confirm=True, token=None):
    if token is None:
        token = cancel_token(client, w, ip)
    data = {STATE_FIELD: token}
    if reason is not None:
        data["reason"] = reason
    if confirm:
        data["confirm"] = "yes"
    return client.post(cancel_url(w, ip), data=data)


def line_ids(app, ip, status=LINE_ACTIVE):
    with app.app_context():
        return [row.public_id for row in stored_lines(ip) if status is None or row.status == status]
