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

import base64
import csv
import hashlib
import io
import re
import secrets
import unicodedata
import zlib
from datetime import date, datetime
from html.parser import HTMLParser

from bidi import get_display

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
from app.services import financial_report_exports as exports

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
PDF_REFUSED_TEXT = "The PDF font has no glyph for some characters"
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


# ---------------------------------------------------------------------------
# Reading a report PDF back
# ---------------------------------------------------------------------------
#
# ReportLab writes text in subsets of the embedded font, so the drawn bytes
# are glyph codes, not characters. Each subset carries a ToUnicode map -- what
# a PDF viewer uses to copy or search text -- and these helpers read text back
# through it: objects are found through the cross-reference table, streams
# are sliced by their /Length and decoded, and every BT ... ET block becomes
# one drawn string. No PDF library is used.

_TOKEN = re.compile(rb"/[^\s/\[\]()<>]+|[^\s/\[\]()<>]+")
_NUMBER = re.compile(rb"[-+.0-9]+")
_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _decoded(dictionary, data):
    for name in re.findall(rb"/(ASCII85Decode|FlateDecode)", dictionary):
        if name == b"ASCII85Decode":
            data = base64.a85decode(re.sub(rb"\s", b"", data).removesuffix(b"~>"))
        else:
            data = zlib.decompress(data)
    return data


def pdf_objects(body):
    """``{number: (dictionary, decoded stream or None)}`` for every object,
    located through the cross-reference table -- which also proves every
    offset and every stream length exact."""
    assert body.startswith(b"%PDF-1.")
    assert body.endswith(b"%%EOF\n")
    startxref = int(re.search(rb"startxref\r?\n(\d+)\r?\n%%EOF\r?\n$", body).group(1))
    header = re.match(rb"xref\r?\n0 (\d+)\r?\n", body[startxref:])
    count = int(header.group(1))
    table = startxref + header.end()
    assert body[table:table + 20] == b"0000000000 65535 f \n"
    objects = {}
    for number in range(1, count):
        entry = body[table + 20 * number:table + 20 * number + 20]
        assert re.fullmatch(rb"\d{10} 00000 n \n", entry), entry
        offset = int(entry[:10])
        opening = re.match(rb"%d 0 obj\r?\n" % number, body[offset:])
        assert opening, number
        start = offset + opening.end()
        stream_at, end_at = body.find(b"stream", start), body.find(b"endobj", start)
        if stream_at == -1 or stream_at > end_at:
            objects[number] = (body[start:end_at], None)
            continue
        dictionary = body[start:stream_at]
        length = int(re.search(rb"/Length (\d+)", dictionary).group(1))
        newline = 2 if body[stream_at + 6:stream_at + 8] == b"\r\n" else 1
        data_start = stream_at + len(b"stream") + newline
        assert body[data_start + length:].lstrip(b"\r\n").startswith(b"endstream"), number
        objects[number] = (dictionary, _decoded(dictionary, body[data_start:data_start + length]))
    return objects


def pdf_objects_valid(body):
    """The number of objects, once the cross-reference table and every
    stream length have been proved exact."""
    return len(pdf_objects(body))


def _reference(dictionary, key):
    match = re.search(rb"/" + key + rb" (\d+) 0 R", dictionary)
    return None if match is None else int(match.group(1))


def _utf16(hex_text):
    return bytes.fromhex(hex_text.decode("ascii")).decode("utf-16-be")


def _unicode_map(cmap):
    """``{one-byte code: text}`` of a ToUnicode CMap."""
    mapping = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for source, target in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            mapping[int(source, 16)] = _utf16(target)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        ranges = re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block)
        for low, high, target in ranges:
            low, high = int(low, 16), int(high, 16)
            if target.startswith(b"<"):
                first = int(target[1:-1], 16)
                for offset in range(high - low + 1):
                    mapping[low + offset] = chr(first + offset)
            else:
                for offset, item in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", target)):
                    mapping[low + offset] = _utf16(item)
    return mapping


def _literal(content, index):
    """``(bytes, next index)`` of the PDF literal string opening at `index`."""
    out, depth, index = bytearray(), 1, index + 1
    while True:
        char = content[index:index + 1]
        if char == b"\\":
            following = content[index + 1:index + 2]
            octal = re.match(rb"[0-7]{1,3}", content[index + 1:index + 4])
            if following in _ESCAPES:
                out += _ESCAPES[following]
                index += 2
            elif octal:
                out.append(int(octal.group(0), 8))
                index += 1 + len(octal.group(0))
            elif following in (b"\n", b"\r"):
                index += 2
            else:
                out += following
                index += 2
            continue
        if char == b"(":
            depth += 1
        elif char == b")":
            depth -= 1
            if depth == 0:
                return bytes(out), index + 1
        out += char
        index += 1


def _shown(codes, font):
    return "".join(font[code] for code in codes)


def _drawn_strings(content, fonts):
    """Each BT ... ET block of a content stream, as the characters it shows."""
    strings, current, font, operands, index = [], None, None, [], 0
    while index < len(content):
        char = content[index:index + 1]
        if char.isspace():
            index += 1
        elif char == b"(":
            value, index = _literal(content, index)
            operands.append(value)
        elif char == b"<" and content[index:index + 2] != b"<<":
            end = content.index(b">", index)
            operands.append(bytes.fromhex(re.sub(rb"\s", b"", content[index + 1:end]).decode()))
            index = end + 1
        elif char in (b"[", b"]"):
            operands.append(char)
            index += 1
        else:
            match = _TOKEN.match(content, index)
            if match is None:
                index += 1
                continue
            word, index = match.group(0), match.end()
            if word.startswith(b"/") or _NUMBER.fullmatch(word):
                operands.append(word)
                continue
            if word == b"BT":
                current = []
            elif word == b"ET":
                strings.append("".join(current))
                current = None
            elif word == b"Tf":
                font = fonts[operands[-2]]
            elif word in (b"Tj", b"'", b'"'):
                current.append(_shown(operands[-1], font))
            elif word == b"TJ":
                current += [_shown(item, font) for item in operands
                            if isinstance(item, bytes) and item not in (b"[", b"]")]
            operands = []
    return strings


def pdf_page_texts(body):
    """Every string each page draws, page by page, in drawing order."""
    objects = pdf_objects(body)
    maps = {}
    for number, (dictionary, _stream) in objects.items():
        if b"/Type /Font" not in dictionary:
            continue
        unicode_reference = _reference(dictionary, b"ToUnicode")
        maps[number] = (
            _unicode_map(objects[unicode_reference][1])
            if unicode_reference is not None
            else {code: bytes([code]).decode("cp1252", "replace") for code in range(256)}
        )
    tree = next(dictionary for dictionary, _s in objects.values() if b"/Type /Pages" in dictionary)
    kids = re.search(rb"/Kids \[([^\]]*)\]", tree).group(1)
    pages = []
    for page_number in (int(number) for number in re.findall(rb"(\d+) 0 R", kids)):
        page = objects[page_number][0]
        resources = objects[_reference(page, b"Font")][0]
        fonts = {b"/" + name: maps[int(reference)]
                 for name, reference in re.findall(rb"/(\S+) (\d+) 0 R", resources)
                 if int(reference) in maps}
        pages.append(_drawn_strings(objects[_reference(page, b"Contents")][1], fonts))
    return pages


def pdf_texts(body):
    """Every string the PDF draws, in drawing order across pages."""
    return [text for page in pdf_page_texts(body) for text in page]


def pdf_page_count(body):
    return len(pdf_page_texts(body))


def pdf_embeds_dejavu(body):
    """Whether the PDF embeds a DejaVu Sans subset, and no other font file."""
    embedded = [dictionary for dictionary, _s in pdf_objects(body).values()
                if b"/FontFile2" in dictionary]
    return bool(embedded) and all(
        re.search(rb"/FontName /[A-Z]{6}\+DejaVuSans\s", dictionary) for dictionary in embedded)


#: PDF names that would make a document act: scripts, actions, links, forms,
#: attachments and rich media.
_ACTIVE_NAMES = (b"JavaScript", b"JS", b"OpenAction", b"AA", b"URI", b"Launch", b"Link",
                 b"Annots", b"AcroForm", b"XFA", b"SubmitForm", b"GoToR", b"GoToE",
                 b"EmbeddedFile", b"EmbeddedFiles", b"RichMedia")
_STRING_OPERAND = re.compile(rb"\((?:\\.|[^\\)])*\)", re.S)


def pdf_active_names(body):
    """Every active-content name found in any object dictionary or decoded
    stream -- the embedded font program aside, and with drawn string operands
    blanked, since glyph codes are arbitrary bytes."""
    found = set()
    for dictionary, stream in pdf_objects(body).values():
        searched = dictionary
        if stream is not None and b"/Length1" not in dictionary:
            searched += _STRING_OPERAND.sub(b"()", stream)
        found |= {name.decode() for name in _ACTIVE_NAMES
                  if re.search(rb"/" + name + rb"(?![A-Za-z0-9])", searched)}
    return found


def base_right_to_left(text):
    """Whether `text`'s first strong character is right to left."""
    for character in text:
        direction = unicodedata.bidirectional(character)
        if direction == "L":
            return False
        if direction in ("R", "AL"):
            return True
    return False


def logical(drawn):
    """A drawn line of **Arabic-only** text back in logical order with plain
    letters: the visual reversal undone, then presentation forms folded back
    to their letters (NFKC). An independent check of shaping and order; for
    text mixing digits or Latin, the bidirectional algorithm has no exact
    inverse, so :func:`drawn_as` compares forwards instead."""
    return unicodedata.normalize("NFKC", get_display(drawn, base_dir="R"))


def drawn_as(drawn, expected):
    """Whether one drawn string is `expected` as the PDF draws it: shaped and
    put in visual order for its base direction."""
    return drawn == exports._visual(expected, exports._base_right_to_left(expected))


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
