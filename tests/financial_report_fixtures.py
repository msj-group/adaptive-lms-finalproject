"""Shared fixtures for the Phase 5 / M08 financial report test modules.

Builds on ``tests/payment_fixtures.py`` and ``tests/payment_intent_fixtures.py``
(whose academic chain, invoices, intents and accounts it reuses). Row helpers
write payments and provider events straight into the tables with exact
timestamps, so a report test can place a movement on either side of a
center-local midnight without driving a payment route. Every row respects the
database CHECKs of Phase 5 / M05 to M07.

The reading helpers at the bottom turn a CSV download, a PDF download and an
HTML page back into plain cells, so a test can prove the three outputs state
the same facts.
"""

import csv
import hashlib
import io
import re
import secrets
from datetime import date, datetime
from html.parser import HTMLParser

import tests.fee_assignment_fixtures as fees
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
from app import create_app
from app.extensions import db
from app.models import (
    FeePlan,
    Group,
    Invoice,
    PaymentProviderEvent,
    PaymentTransaction,
    User,
)

TZ = "Africa/Tripoli"

INDEX_URL = "/admin/financial-reports"
COLLECTIONS_URL = "/admin/financial-reports/collections"
OUTSTANDING_URL = "/admin/financial-reports/outstanding-invoices"
EXCEPTIONS_URL = "/admin/financial-reports/exceptions"
REPORT_URLS = (COLLECTIONS_URL, OUTSTANDING_URL, EXCEPTIONS_URL)
ALL_URLS = (INDEX_URL,) + tuple(
    url + suffix for url in REPORT_URLS for suffix in ("", ".csv", ".pdf")
)
FILENAMES = {
    COLLECTIONS_URL: "collections-report",
    OUTSTANDING_URL: "outstanding-invoices-report",
    EXCEPTIONS_URL: "operational-exceptions-report",
}

NOTICE_TEXT = "Operational report only. It is not a tax invoice, a receipt or a legal accounting statement."
PDF_REFUSED_TEXT = "The PDF export can only show Latin-script text"
NO_REPORT_TEXT = "No report was produced."

#: A moment in September 2026, center-local 12:00.
SEPTEMBER = datetime(2026, 9, 10, 10, 0, 0)

login_as = px.login_as
admin = px.admin
user = px.user
page = px.page

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


def make_app(**overrides):
    overrides.setdefault("APP_TIMEZONE", TZ)
    return create_app("testing", **overrides)


def make_mock_app(**overrides):
    """The Mock/Sandbox provider enabled, for the real online-payment path."""
    overrides.setdefault("APP_TIMEZONE", TZ)
    return ix.make_app(mode="mock", **overrides)


def rows_of(w):
    return db.session.get(Invoice, w["invoice_id"]), db.session.get(User, w["admin_id"])


def fixed_clock(monkeypatch, moment):
    """Pin the report moment (naive UTC)."""
    import app.services.financial_reports as reports

    monkeypatch.setattr(reports, "utc_reference_now", lambda: moment)


# ---------------------------------------------------------------------------
# Rows, written directly
# ---------------------------------------------------------------------------


def transaction(owner, actor, amount="100.000", method="cash", kind="collection",
                status="confirmed", at=SEPTEMBER, recorded_at=None, reversal_of=None,
                intent=None, reference=None, reason=None):
    """One payment movement whose confirmation (or rejection) is `at`. A bank
    transfer is recorded at `recorded_at` (default `at`); cash, reversals and
    online collections are recorded and confirmed at `at`."""
    bank = kind == "collection" and method == "bank_transfer"
    online = kind == "collection" and method == "online"
    confirmed = status == "confirmed"
    rejected = status == "rejected"
    recorded = (recorded_at or at) if bank else at
    row = PaymentTransaction(
        invoice_id=owner.id,
        kind=kind,
        method=method,
        status=status,
        currency_code="LYD",
        amount=amount,
        bank_transfer_reference=(reference or f"BANKREF-{_next()}") if bank else None,
        bank_transfer_date=date(2026, 1, 1) if bank else None,
        recorded_at=recorded,
        recorded_by_id=None if online else actor.id,
        confirmed_at=at if confirmed else None,
        confirmed_by_id=None if online or not confirmed else actor.id,
        rejected_at=at if rejected else None,
        rejected_by_id=actor.id if rejected else None,
        rejection_reason=(reason or "Funds never arrived") if rejected else None,
        reversal_of_payment_transaction_id=None if reversal_of is None else reversal_of.id,
        payment_intent_id=intent.id if online else None,
        version=2 if bank and status != "pending" else 1,
        created_at=recorded,
        updated_at=at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def online_collection(owner, actor, amount="1250.5000", at=SEPTEMBER):
    """A confirmed intent and the online collection its signed webhook made."""
    settled = ix.intent(owner, actor, status="confirmed", amount=amount)
    return transaction(owner, actor, amount=amount, method="online", at=at, intent=settled), settled


def provider_event(intent, outcome="reconciliation_required", event_type="payment.succeeded",
                   amount="1250.5000", received_at=SEPTEMBER, event_id=None):
    row = PaymentProviderEvent(
        provider="mock",
        provider_event_id=event_id or "evt_mock_" + secrets.token_hex(16),
        payment_intent_id=intent.id,
        event_type=event_type,
        currency_code="LYD",
        amount=amount,
        provider_occurred_at=received_at,
        received_at=received_at,
        processed_at=received_at,
        payload_digest=hashlib.sha256(f"body-{_next()}".encode()).hexdigest(),
        outcome=outcome,
        payment_transaction_id=None,
        created_at=received_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def invoice_in_group(actor, plan, group_name=None, student_name=None, status="issued",
                     lines=fees.DEFAULT_ITEMS, owning_group=None):
    """An invoice for a new Student in `owning_group` (or a new Group)."""
    owning_group = owning_group or fees.group(name=group_name)
    enrolled = fees.student(name=student_name)
    enrollment = fees.enrollment(owning_group=owning_group, enrolled=enrolled)
    assignment = fees.assignment(enrollment, plan, actor)
    number = f"INV-2026-{_next() + 500000:06d}" if status != "draft" else None
    owner = px.fx.invoice(assignment, actor, status=status, lines=lines, number=number)
    return owner, owning_group


def plan_of(w):
    return db.session.get(FeePlan, w["plan_id"])


def group_of(w):
    return db.session.get(Group, w["group_id"])


def everything():
    """Every financial row -- intents, payments, receipts, sequences, audit
    events, invoices, lines -- and every provider event: what "a report
    changed nothing" is compared against."""
    db.session.expire_all()
    return (
        ix.intent_record(),
        [(r.id, r.public_id, r.provider_event_id, r.payment_intent_id, r.event_type, r.amount,
          r.outcome, r.payment_transaction_id, r.payload_digest, r.created_at)
         for r in PaymentProviderEvent.query.order_by(PaymentProviderEvent.id)],
    )


# ---------------------------------------------------------------------------
# Reading the three outputs back
# ---------------------------------------------------------------------------


def csv_rows(response):
    raw = response.get_data()
    assert raw.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


def csv_meta(rows):
    return {row[0]: row[1] for row in rows if len(row) == 2 and not row[0].startswith("Section")}


def csv_section(rows, title):
    """``(header, data_rows)`` of the CSV section titled `title`; the footer,
    if any, is the last data row."""
    start = rows.index(["Section", title])
    index = start + 1
    if rows[index] and rows[index][0] == "Description":
        index += 1
    header = rows[index]
    data = []
    for row in rows[index + 1:]:
        if not row:
            break
        data.append(row)
    return header, data


_TEXT_OPERAND = re.compile(rb"<([0-9A-F]*)> Tj")


def pdf_texts(body):
    """Every text operand the PDF draws, decoded, in drawing order."""
    return [bytes.fromhex(match.decode("ascii")).decode("cp1252")
            for match in _TEXT_OPERAND.findall(body)]


def pdf_objects_valid(body):
    """Whether the cross-reference table points at every object exactly."""
    assert body.startswith(b"%PDF-1.4\n")
    assert body.endswith(b"%%EOF\n")
    startxref = int(re.search(rb"startxref\n(\d+)\n%%EOF\n$", body).group(1))
    assert body[startxref:startxref + 4] == b"xref"
    header = re.match(rb"xref\n0 (\d+)\n", body[startxref:])
    count = int(header.group(1))
    entries = body[startxref + header.end():].split(b"\n")[: count]
    assert entries[0] == b"0000000000 65535 f "
    for number, entry in enumerate(entries[1:], start=1):
        offset = int(entry[:10])
        assert body[offset:].startswith(f"{number} 0 obj\n".encode()), number
    for match in re.finditer(rb"<< /Length (\d+) >>\nstream\n", body):
        length = int(match.group(1))
        assert body[match.end() + length:match.end() + length + 10] == b"\nendstream"
    return count - 1


def pdf_page_count(body):
    return int(re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", body).group(1))


class _Tables(HTMLParser):
    """Each report section's table: header, body rows and footer cells, and
    the "no rows" sentence of an empty table."""

    def __init__(self):
        super().__init__()
        self.sections = {}
        self.current = None
        self.part = None
        self.row = None
        self.cell = None
        self.empty = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h2" and (attrs.get("id") or "").startswith("section-"):
            self.current = attrs["id"][len("section-"):]
            self.sections[self.current] = {"header": [], "rows": [], "footer": [], "empty": None}
        elif self.current and tag in ("thead", "tbody", "tfoot"):
            self.part = tag
        elif self.current and tag == "tr":
            self.row = []
            self.empty = False
        elif self.row is not None and tag in ("td", "th"):
            if "colspan" in attrs:
                self.empty = True
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            target = {"thead": "header", "tbody": "rows", "tfoot": "footer"}[self.part]
            if self.empty:
                self.sections[self.current]["empty"] = self.row[0]
            elif target == "rows":
                self.sections[self.current]["rows"].append(self.row)
            else:
                self.sections[self.current][target] = self.row
            self.row = None
        elif tag == "section":
            self.current = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def html_tables(html):
    parser = _Tables()
    parser.feed(html)
    return parser.sections


def plain(text):
    """An amount as a page shows it, without its reading commas."""
    return text.replace(",", "")
