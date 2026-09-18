"""Administrator financial reports (Phase 5 / M08).

Flask-independent: validated filters, explicit column-projected queries and a
plain report model, and no ``request``, ``abort``, template or response.
``app/blueprints/admin/financial_reports.py`` turns a report into an HTML
page, and ``app/services/financial_report_exports.py`` into CSV and PDF bytes.

**One service, three outputs.** Every route builds its report here, from one
set of validated filters and one report moment, so the HTML page, the CSV and
the PDF of the same request state the same rows, order, totals, filters and
generation time. Nothing here is rendered differently per output.

**Read-only.** Every function reads; none adds, changes, flushes or commits a
row, and nothing is logged. A report never writes an export history, a file or
an audit event.

**Three reports.**

- *Collections*: every **confirmed** ``collection`` (positive) and ``reversal``
  (negative) whose center-local confirmation date lies in the requested range,
  with subtotals by method -- cash, bank transfer, online. A reversal keeps its
  collection's method (Phase 5 / M05), so it subtracts from that method.
  Pending and rejected bank transfers are never counted.
- *Outstanding invoices*: the current state -- every ``issued`` invoice whose
  exact balance (M05's :func:`~app.services.payment_transactions.payment_balance`
  over its active lines and confirmed movements) is strictly positive. Drafts
  and cancelled invoices are excluded.
- *Operational exceptions*: pending and rejected bank transfers, active online
  payment intents (``pending``, ``provider_succeeded``) and provider events
  whose outcome is ``reconciliation_required`` -- shown, never changed.

**Every matching row is returned.** There is no row limit: the totals are
exact only over every row, and the CSV and PDF exports must include them all.
The HTML page may show one page of a long table, but always with the totals of
all rows. A group's reports are one Group's; without a Group they are
center-wide.

**Nothing internal survives.** Internal ids are read only to join and to break
ordering ties (``ORDER BY <moment>, id``); a report row holds names, numbers,
labels, exact ``Decimal`` amounts and naive center-local moments. No bank
transfer reference, rejection reason, provider reference, idempotency key,
provider event id, payload digest, signature or secret is ever selected.

**Money is exact.** Amounts are ``Decimal`` from ``DECIMAL(19, 4)`` columns,
added and subtracted in Python with :class:`decimal.Inexact` trapped, never in
SQL and never through ``float``.
"""

import re
from calendar import monthrange
from collections import defaultdict, namedtuple
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, Inexact, localcontext

from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    ACTIVE_PAYMENT_INTENT_STATUSES,
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    Group,
    Invoice,
    InvoiceItem,
    InvoiceItemStatus,
    InvoiceStatus,
    PaymentIntent,
    PaymentMethod,
    PaymentProviderEvent,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentTransactionStatus,
    ProviderEventOutcome,
    StudentFeeAssignment,
    User,
)
from app.services.money import CURRENCY_CODE, sum_amounts
from app.services.payment_intent_queries import EVENT_TYPE_LABELS
from app.services.payment_intent_queries import STATUS_LABELS as INTENT_STATUS_LABELS
from app.services.payment_queries import KIND_LABELS, METHOD_LABELS
from app.services.payment_transactions import payment_balance
from app.services.schedule_occurrences import (
    LocalTimeError,
    from_app_local,
    to_app_local,
    utc_reference_now,
)

_COLLECTION = PaymentTransactionKind.COLLECTION.value
_REVERSAL = PaymentTransactionKind.REVERSAL.value
_PENDING = PaymentTransactionStatus.PENDING.value
_CONFIRMED = PaymentTransactionStatus.CONFIRMED.value
_REJECTED = PaymentTransactionStatus.REJECTED.value
_ISSUED = InvoiceStatus.ISSUED.value
_ITEM_ACTIVE = InvoiceItemStatus.ACTIVE.value
_RECONCILIATION = ProviderEventOutcome.RECONCILIATION_REQUIRED.value
_GROUP_ACTIVE = AcademicStatus.ACTIVE.value
_ZERO = Decimal(0)

#: The three methods, in the order every report states them.
_METHOD_ORDER = (
    PaymentMethod.CASH.value,
    PaymentMethod.BANK_TRANSFER.value,
    PaymentMethod.ONLINE.value,
)

# ---------------------------------------------------------------------------
# The three reports
# ---------------------------------------------------------------------------

COLLECTIONS = "collections"
OUTSTANDING = "outstanding-invoices"
EXCEPTIONS = "exceptions"

ReportSpec = namedtuple("ReportSpec", "key title summary takes_dates")

REPORT_SPECS = {
    COLLECTIONS: ReportSpec(
        COLLECTIONS,
        "Collections report",
        "Confirmed collections and reversals by their local confirmation date, totaled by "
        "cash, bank transfer and online.",
        True,
    ),
    OUTSTANDING: ReportSpec(
        OUTSTANDING,
        "Outstanding invoices report",
        "Issued invoices that still have an outstanding balance, as they stand now.",
        False,
    ),
    EXCEPTIONS: ReportSpec(
        EXCEPTIONS,
        "Operational exceptions report",
        "Pending and rejected bank transfers, active online payment intents and provider "
        "events awaiting reconciliation, as they stand now.",
        False,
    ),
}

#: Stated on every report, in every output.
NOTICE = (
    "Operational report only. It is not a tax invoice, a receipt or a legal accounting "
    "statement."
)
ALL_GROUPS_TEXT = "All groups (center-wide)"
NO_INVOICE_NUMBER = "—"

#: How many rows of a long table one HTML page shows. Exports show every row.
HTML_PAGE_SIZE = 50

# ---------------------------------------------------------------------------
# The report model
# ---------------------------------------------------------------------------

#: How a column's values read: ``text`` wraps, the others never do.
TEXT = "text"
AMOUNT = "amount"
COUNT = "count"
MOMENT = "moment"

Column = namedtuple("Column", "key label kind")


@dataclass(frozen=True)
class ReportSection:
    """One table of a report. ``rows`` and ``footer`` map a column key to a
    ``str``, an ``int``, an exact ``Decimal`` or a naive center-local
    ``datetime`` -- never to an internal id."""

    key: str
    title: str
    description: str
    columns: tuple
    rows: tuple
    footer: dict
    empty_text: str
    paginated: bool = False


@dataclass(frozen=True)
class FinancialReport:
    key: str
    title: str
    filters: tuple
    generated_local: datetime
    tz_name: str
    currency_code: str
    notice: str
    notes: tuple
    sections: tuple


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

GROUP_PARAM = "group"
START_PARAM = "start"
END_PARAM = "end"

#: A technical bound on a report date: the bank-transfer lower bound of
#: Phase 5 / M05, and an upper bound that keeps the next local midnight a
#: representable moment.
EARLIEST_REPORT_DATE = date(2000, 1, 1)
LATEST_REPORT_DATE = date(9998, 12, 31)

GROUP_INVALID_MESSAGE = (
    "That Group filter is not valid. Choose a Group from the list, or All groups for the "
    "whole center."
)
REPEATED_MESSAGE = "Each filter can be given only once."
DATE_FORMAT_MESSAGE = (
    "Enter the {which} date as YYYY-MM-DD: a real calendar date from "
    f"{EARLIEST_REPORT_DATE.isoformat()} to {LATEST_REPORT_DATE.isoformat()}."
)
DATE_PAIR_MESSAGE = (
    "Enter both a start date and an end date, or leave both empty for the current month."
)
DATE_REVERSED_MESSAGE = "The start date is after the end date. Nothing was reported."
DATE_MIDNIGHT_MESSAGE = (
    "Midnight of the {which} date does not exist in the center timezone. Choose another date."
)
DATES_NOT_ACCEPTED_MESSAGE = (
    "This report shows the current state and takes no date range. Remove the dates."
)

_PUBLIC_ID_SHAPE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DATE_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

#: A Group a report may be limited to. ``id`` is internal and never leaves
#: this module's queries.
GroupChoice = namedtuple("GroupChoice", "id public_id label")


@dataclass(frozen=True)
class ReportFilters:
    """Validated filters. ``start_utc`` / ``end_utc`` are the naive-UTC
    half-open bounds ``[start local midnight, local midnight after end)``."""

    group: GroupChoice
    start: date
    end: date
    default_range: bool
    start_utc: datetime
    end_utc: datetime

    def query_args(self):
        """The canonical, public-only query string of these filters."""
        args = {}
        if self.group is not None:
            args[GROUP_PARAM] = self.group.public_id
        if self.start is not None:
            args[START_PARAM] = self.start.isoformat()
            args[END_PARAM] = self.end.isoformat()
        return args


def report_moment():
    """The one naive-UTC, whole-second moment a report is generated at."""
    return utc_reference_now().replace(microsecond=0)


def _group_label(name, course_title, term_name, status):
    label = f"{name} — {course_title} — {term_name}"
    return label if status == _GROUP_ACTIVE else label + " (archived)"


def _group_rows():
    return (
        db.session.query(
            Group.id,
            Group.public_id,
            Group.name,
            Group.status,
            Course.title.label("course_title"),
            AcademicTerm.name.label("term_name"),
        )
        .select_from(Group)
        .join(Course, Course.id == Group.course_id)
        .join(AcademicTerm, AcademicTerm.id == Group.academic_term_id)
    )


def group_choices():
    """Every Group -- archived ones too, whose financial history stays
    reportable -- as ``(public_id, label)`` pairs ordered by name. One query."""
    rows = _group_rows().order_by(Group.name.asc(), Group.id.asc()).all()
    return [
        (row.public_id, _group_label(row.name, row.course_title, row.term_name, row.status))
        for row in rows
    ]


def _group_choice(public_id):
    """The Group whose ``public_id`` is exactly `public_id`, or ``None``. A
    numeric database id is never looked up."""
    if _PUBLIC_ID_SHAPE.fullmatch(public_id) is None:
        return None
    row = _group_rows().filter(Group.public_id == public_id).first()
    if row is None:
        return None
    return GroupChoice(
        row.id, row.public_id, _group_label(row.name, row.course_title, row.term_name, row.status)
    )


def _single(args, name, errors):
    """The one submitted value of `name` (``""`` when absent). A repeated
    parameter is an error, never resolved by picking one."""
    values = args.getlist(name)
    if len(values) > 1:
        if REPEATED_MESSAGE not in errors:
            errors.append(REPEATED_MESSAGE)
        return None
    return values[0] if values else ""


def _parse_date(text):
    """A strict ``YYYY-MM-DD`` date within the technical bounds, or ``None``.
    Nothing is stripped, padded or repaired."""
    if _DATE_SHAPE.fullmatch(text) is None:
        return None
    try:
        value = date.fromisoformat(text)
    except ValueError:
        return None
    if not EARLIEST_REPORT_DATE <= value <= LATEST_REPORT_DATE:
        return None
    return value


def _local_midnight_utc(tz_name, day):
    return from_app_local(tz_name, datetime.combine(day, time.min))


def parse_report_filters(report_key, args, tz_name, moment):
    """``(ReportFilters, [])`` for valid query arguments, else
    ``(None, [messages])``.

    `args` is the request's ``MultiDict``; `moment` the report moment. Only the
    ``group`` (a Group public id, or empty for the whole center) and -- for the
    collections report only -- the ``start`` / ``end`` dates are read. Both
    dates absent or empty means the current center-local calendar month. A
    malformed, out-of-range, single, repeated or reversed date, dates on a
    current-state report, and a Group that is malformed, numeric or unknown
    are all refused: nothing is normalized into a different report.
    """
    errors = []
    spec = REPORT_SPECS[report_key]
    group = None
    raw_group = _single(args, GROUP_PARAM, errors)
    if raw_group:
        group = _group_choice(raw_group)
        if group is None:
            errors.append(GROUP_INVALID_MESSAGE)
    raw_start = _single(args, START_PARAM, errors)
    raw_end = _single(args, END_PARAM, errors)
    start = end = start_utc = end_utc = None
    default_range = False
    if not spec.takes_dates:
        if raw_start or raw_end:
            errors.append(DATES_NOT_ACCEPTED_MESSAGE)
    elif raw_start is None or raw_end is None:
        pass  # a repeated date parameter, already reported
    elif not raw_start and not raw_end:
        today = to_app_local(tz_name, moment).date()
        start = today.replace(day=1)
        end = today.replace(day=monthrange(today.year, today.month)[1])
        default_range = True
    elif not raw_start or not raw_end:
        errors.append(DATE_PAIR_MESSAGE)
    else:
        start, end = _parse_date(raw_start), _parse_date(raw_end)
        if start is None:
            errors.append(DATE_FORMAT_MESSAGE.format(which="start"))
        if end is None:
            errors.append(DATE_FORMAT_MESSAGE.format(which="end"))
        if start is not None and end is not None and start > end:
            errors.append(DATE_REVERSED_MESSAGE)
    if start is not None and end is not None and not errors:
        try:
            start_utc = _local_midnight_utc(tz_name, start)
        except LocalTimeError:
            errors.append(DATE_MIDNIGHT_MESSAGE.format(which="start"))
        try:
            end_utc = _local_midnight_utc(tz_name, end + timedelta(days=1))
        except LocalTimeError:
            errors.append(DATE_MIDNIGHT_MESSAGE.format(which="day after the end"))
    if errors:
        return None, errors
    return ReportFilters(group, start, end, default_range, start_utc, end_utc), []


# ---------------------------------------------------------------------------
# Shared query shape
# ---------------------------------------------------------------------------


def _with_invoice_context(query, student, group_id):
    """Join the invoice's assignment, Enrollment, Group and Student onto a
    query that already selects from (or joined) ``invoices``; limit it to one
    Group when a Group filter is set."""
    query = (
        query.join(StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id)
        .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
        .join(Group, Group.id == Enrollment.group_id)
        .join(student, student.id == Enrollment.student_id)
    )
    if group_id is not None:
        query = query.filter(Group.id == group_id)
    return query


def _group_id(filters):
    return None if filters.group is None else filters.group.id


def _context_columns(student):
    return (
        Invoice.invoice_number,
        student.full_name.label("student_name"),
        Group.name.label("group_name"),
    )


def _context_cells(row):
    return {
        "invoice": row.invoice_number or NO_INVOICE_NUMBER,
        "student": row.student_name,
        "group": row.group_name,
    }


def _local(tz_name, moment):
    return to_app_local(tz_name, moment)


def _minus(minuend, subtrahend):
    """``minuend - subtrahend`` exactly, or :class:`decimal.Inexact`."""
    with localcontext() as context:
        context.traps[Inexact] = True
        return minuend - subtrahend


def _filter_texts(filters, tz_name):
    texts = []
    if filters.start is not None:
        suffix = " (the current month, by default)" if filters.default_range else ""
        texts.append(
            (
                f"Confirmation date ({tz_name})",
                f"{filters.start.isoformat()} to {filters.end.isoformat()}, inclusive{suffix}",
            )
        )
    texts.append(("Group", ALL_GROUPS_TEXT if filters.group is None else filters.group.label))
    return tuple(texts)


def _report(key, filters, tz_name, generated_local, notes, sections, extra_filters=()):
    return FinancialReport(
        key=key,
        title=REPORT_SPECS[key].title,
        filters=_filter_texts(filters, tz_name) + tuple(extra_filters),
        generated_local=generated_local,
        tz_name=tz_name,
        currency_code=CURRENCY_CODE,
        notice=NOTICE,
        notes=tuple(notes),
        sections=tuple(sections),
    )


def _amount_label(label):
    return f"{label} ({CURRENCY_CODE})"


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


def confirmed_movement_rows(filters):
    """Every confirmed collection and reversal confirmed inside the filters'
    UTC bounds, in confirmation order (``confirmed_at``, then ``id``). One
    query."""
    student = aliased(User)
    query = _with_invoice_context(
        db.session.query(
            PaymentTransaction.kind,
            PaymentTransaction.method,
            PaymentTransaction.amount,
            PaymentTransaction.confirmed_at,
            *_context_columns(student),
        )
        .select_from(PaymentTransaction)
        .join(Invoice, Invoice.id == PaymentTransaction.invoice_id),
        student,
        _group_id(filters),
    ).filter(
        PaymentTransaction.status == _CONFIRMED,
        PaymentTransaction.kind.in_((_COLLECTION, _REVERSAL)),
        PaymentTransaction.method.in_(_METHOD_ORDER),
        PaymentTransaction.confirmed_at >= filters.start_utc,
        PaymentTransaction.confirmed_at < filters.end_utc,
    )
    return query.order_by(
        PaymentTransaction.confirmed_at.asc(), PaymentTransaction.id.asc()
    ).all()


def _signed(row):
    """A collection adds its amount; a reversal subtracts it."""
    return row.amount if row.kind == _COLLECTION else _minus(_ZERO, row.amount)


def _collections_report(filters, tz_name, generated_local):
    rows = confirmed_movement_rows(filters)
    collected = {method: [] for method in _METHOD_ORDER}
    reversed_ = {method: [] for method in _METHOD_ORDER}
    movements = []
    for row in rows:
        (collected if row.kind == _COLLECTION else reversed_)[row.method].append(row.amount)
        movements.append(
            {
                "confirmed": _local(tz_name, row.confirmed_at),
                "type": KIND_LABELS[row.kind],
                "method": METHOD_LABELS[row.method],
                "amount": _signed(row),
                **_context_cells(row),
            }
        )

    def method_totals(label, collections, reversals):
        gross, back = sum_amounts(collections), sum_amounts(reversals)
        return {
            "method": label,
            "collections": len(collections),
            "collected": gross,
            "reversals": len(reversals),
            "reversed": _minus(_ZERO, back),
            "net": _minus(gross, back),
        }

    by_method = tuple(
        method_totals(METHOD_LABELS[method], collected[method], reversed_[method])
        for method in _METHOD_ORDER
    )
    everything = method_totals(
        "All methods",
        [amount for method in _METHOD_ORDER for amount in collected[method]],
        [amount for method in _METHOD_ORDER for amount in reversed_[method]],
    )
    count = len(movements)
    totals = ReportSection(
        key="totals",
        title="Totals by method",
        description="Net collections by method: confirmed collections less confirmed reversals.",
        columns=(
            Column("method", "Method", TEXT),
            Column("collections", "Collections", COUNT),
            Column("collected", _amount_label("Collected"), AMOUNT),
            Column("reversals", "Reversals", COUNT),
            Column("reversed", _amount_label("Reversed"), AMOUNT),
            Column("net", _amount_label("Net"), AMOUNT),
        ),
        rows=by_method,
        footer=everything,
        empty_text="",
    )
    detail = ReportSection(
        key="movements",
        title="Confirmed movements",
        description=(
            "Every confirmed collection (positive) and reversal (negative), oldest "
            "confirmation first."
        ),
        columns=(
            Column("confirmed", f"Confirmed ({tz_name})", MOMENT),
            Column("type", "Type", TEXT),
            Column("method", "Method", TEXT),
            Column("amount", _amount_label("Amount"), AMOUNT),
            Column("invoice", "Invoice", TEXT),
            Column("student", "Student", TEXT),
            Column("group", "Group", TEXT),
        ),
        rows=tuple(movements),
        footer={
            "confirmed": f"Net total ({count} movement{'' if count == 1 else 's'})",
            "amount": everything["net"],
        },
        empty_text="No confirmed collection or reversal in this range.",
        paginated=True,
    )
    notes = (
        "Only confirmed movements are included, by the center-local date and time they were "
        "confirmed. A reversal subtracts its full amount from the method of the collection "
        "it reverses; it is an internal correction, not a refund.",
        "Pending and rejected bank transfers are never counted in these totals.",
    )
    return _report(COLLECTIONS, filters, tz_name, generated_local, notes, (totals, detail))


# ---------------------------------------------------------------------------
# Outstanding invoices
# ---------------------------------------------------------------------------


def issued_invoice_rows(filters):
    """Every issued invoice in scope, oldest issue first (``issued_at``, then
    ``id``). One query."""
    student = aliased(User)
    query = _with_invoice_context(
        db.session.query(Invoice.id, Invoice.issued_at, *_context_columns(student)).select_from(
            Invoice
        ),
        student,
        _group_id(filters),
    ).filter(Invoice.status == _ISSUED)
    return query.order_by(Invoice.issued_at.asc(), Invoice.id.asc()).all()


def _issued_scope(query, filters):
    """Limit a query joined to ``invoices`` to issued invoices in scope."""
    query = query.filter(Invoice.status == _ISSUED)
    group_id = _group_id(filters)
    if group_id is not None:
        query = (
            query.join(
                StudentFeeAssignment, StudentFeeAssignment.id == Invoice.student_fee_assignment_id
            )
            .join(Enrollment, Enrollment.id == StudentFeeAssignment.enrollment_id)
            .filter(Enrollment.group_id == group_id)
        )
    return query


def issued_invoice_line_rows(filters):
    """The active lines of every issued invoice in scope. One query."""
    query = (
        db.session.query(InvoiceItem.invoice_id, InvoiceItem.amount)
        .select_from(InvoiceItem)
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .filter(InvoiceItem.status == _ITEM_ACTIVE)
    )
    return _issued_scope(query, filters).order_by(
        InvoiceItem.invoice_id.asc(), InvoiceItem.id.asc()
    ).all()


def issued_invoice_movement_rows(filters):
    """The confirmed collections and reversals of every issued invoice in
    scope -- the only rows a balance counts. One query."""
    query = (
        db.session.query(
            PaymentTransaction.invoice_id,
            PaymentTransaction.kind,
            PaymentTransaction.status,
            PaymentTransaction.amount,
        )
        .select_from(PaymentTransaction)
        .join(Invoice, Invoice.id == PaymentTransaction.invoice_id)
        .filter(PaymentTransaction.status == _CONFIRMED)
    )
    return _issued_scope(query, filters).order_by(
        PaymentTransaction.invoice_id.asc(), PaymentTransaction.id.asc()
    ).all()


def _outstanding_report(filters, tz_name, generated_local):
    invoices = issued_invoice_rows(filters)
    lines, movements = defaultdict(list), defaultdict(list)
    for row in issued_invoice_line_rows(filters):
        lines[row.invoice_id].append(row)
    for row in issued_invoice_movement_rows(filters):
        movements[row.invoice_id].append(row)
    entries, unavailable = [], []
    for row in invoices:
        balance = payment_balance(lines[row.id], movements[row.id])
        if balance is None:
            unavailable.append({**_context_cells(row), "issued": _local(tz_name, row.issued_at)})
            continue
        if balance.outstanding <= 0:
            continue
        entries.append(
            {
                **_context_cells(row),
                "issued": _local(tz_name, row.issued_at),
                "total": balance.total,
                "paid": balance.paid,
                "outstanding": balance.outstanding,
            }
        )
    count = len(entries)
    context_columns = (
        Column("invoice", "Invoice", TEXT),
        Column("student", "Student", TEXT),
        Column("group", "Group", TEXT),
        Column("issued", f"Issued ({tz_name})", MOMENT),
    )
    sections = [
        ReportSection(
            key="invoices",
            title="Outstanding invoices",
            description="Issued invoices with an outstanding balance above zero, oldest first.",
            columns=context_columns
            + (
                Column("total", _amount_label("Total"), AMOUNT),
                Column("paid", _amount_label("Paid"), AMOUNT),
                Column("outstanding", _amount_label("Outstanding"), AMOUNT),
            ),
            rows=tuple(entries),
            footer={
                "invoice": f"Total ({count} invoice{'' if count == 1 else 's'})",
                "total": sum_amounts([entry["total"] for entry in entries]),
                "paid": sum_amounts([entry["paid"] for entry in entries]),
                "outstanding": sum_amounts([entry["outstanding"] for entry in entries]),
            },
            empty_text="No issued invoice has an outstanding balance.",
            paginated=True,
        )
    ]
    notes = [
        "Current state at the generation time, not a historical statement. Outstanding = "
        "active invoice lines - confirmed collections + confirmed reversals.",
        "Pending and rejected bank transfers do not change a balance. Draft, cancelled and "
        "settled invoices are not listed.",
    ]
    if unavailable:
        notes.append(
            f"{len(unavailable)} issued invoice(s) have payment records that do not describe a "
            "valid balance. They are listed separately and are not in the totals."
        )
        sections.append(
            ReportSection(
                key="unavailable",
                title="Invoices whose balance is unavailable",
                description=(
                    "Their payment records give a negative paid or outstanding amount. Review "
                    "each invoice's payments page."
                ),
                columns=context_columns,
                rows=tuple(unavailable),
                footer={"invoice": f"Total ({len(unavailable)})"},
                empty_text="",
            )
        )
    return _report(
        OUTSTANDING,
        filters,
        tz_name,
        generated_local,
        notes,
        sections,
        extra_filters=(("As of", "The current state when this report was generated"),),
    )


# ---------------------------------------------------------------------------
# Operational exceptions
# ---------------------------------------------------------------------------


def transfer_rows(filters, status, moment_column):
    """Bank-transfer collections with `status`, oldest first by
    `moment_column`, then ``id``. Neither the reference, its date nor any
    rejection reason is selected. One query."""
    student = aliased(User)
    query = _with_invoice_context(
        db.session.query(
            moment_column.label("moment"), PaymentTransaction.amount, *_context_columns(student)
        )
        .select_from(PaymentTransaction)
        .join(Invoice, Invoice.id == PaymentTransaction.invoice_id),
        student,
        _group_id(filters),
    ).filter(
        PaymentTransaction.status == status,
        PaymentTransaction.kind == _COLLECTION,
        PaymentTransaction.method == PaymentMethod.BANK_TRANSFER.value,
    )
    return query.order_by(moment_column.asc(), PaymentTransaction.id.asc()).all()


def active_intent_rows(filters):
    """Every ``pending`` or ``provider_succeeded`` payment intent, oldest
    first. No provider reference or idempotency key is selected. One query."""
    student = aliased(User)
    query = _with_invoice_context(
        db.session.query(
            PaymentIntent.created_at.label("moment"),
            PaymentIntent.status,
            PaymentIntent.amount,
            *_context_columns(student),
        )
        .select_from(PaymentIntent)
        .join(Invoice, Invoice.id == PaymentIntent.invoice_id),
        student,
        _group_id(filters),
    ).filter(PaymentIntent.status.in_(ACTIVE_PAYMENT_INTENT_STATUSES))
    return query.order_by(PaymentIntent.created_at.asc(), PaymentIntent.id.asc()).all()


def reconciliation_event_rows(filters):
    """Every provider event whose outcome is ``reconciliation_required``,
    oldest receipt first. No provider event id, payload digest or provider
    reference is selected. One query."""
    student = aliased(User)
    query = _with_invoice_context(
        db.session.query(
            PaymentProviderEvent.received_at.label("moment"),
            PaymentProviderEvent.event_type,
            PaymentProviderEvent.amount,
            PaymentIntent.status.label("intent_status"),
            *_context_columns(student),
        )
        .select_from(PaymentProviderEvent)
        .join(PaymentIntent, PaymentIntent.id == PaymentProviderEvent.payment_intent_id)
        .join(Invoice, Invoice.id == PaymentIntent.invoice_id),
        student,
        _group_id(filters),
    ).filter(PaymentProviderEvent.outcome == _RECONCILIATION)
    return query.order_by(
        PaymentProviderEvent.received_at.asc(), PaymentProviderEvent.id.asc()
    ).all()


_EXCEPTION_CONTEXT = (
    Column("amount", _amount_label("Amount"), AMOUNT),
    Column("invoice", "Invoice", TEXT),
    Column("student", "Student", TEXT),
    Column("group", "Group", TEXT),
)


def _exception_section(key, title, description, first_columns, entries, empty_text):
    count = len(entries)
    return ReportSection(
        key=key,
        title=title,
        description=description,
        columns=tuple(first_columns) + _EXCEPTION_CONTEXT,
        rows=tuple(entries),
        footer={
            first_columns[0].key: f"Total ({count})",
            "amount": sum_amounts([entry["amount"] for entry in entries]),
        },
        empty_text=empty_text,
    )


def _exceptions_report(filters, tz_name, generated_local):
    def entry(row, **cells):
        return {
            "moment": _local(tz_name, row.moment),
            **cells,
            "amount": row.amount,
            **_context_cells(row),
        }

    pending = [entry(row) for row in transfer_rows(filters, _PENDING, PaymentTransaction.recorded_at)]
    rejected = [
        entry(row) for row in transfer_rows(filters, _REJECTED, PaymentTransaction.rejected_at)
    ]
    intents = [
        entry(row, status=INTENT_STATUS_LABELS.get(row.status, row.status))
        for row in active_intent_rows(filters)
    ]
    events = [
        entry(
            row,
            event=EVENT_TYPE_LABELS.get(row.event_type, row.event_type),
            intent_status=INTENT_STATUS_LABELS.get(row.intent_status, row.intent_status),
        )
        for row in reconciliation_event_rows(filters)
    ]
    categories = (
        ("Pending bank transfers", pending),
        ("Rejected bank transfers", rejected),
        ("Active online payment intents", intents),
        ("Provider events requiring reconciliation", events),
    )
    summary = ReportSection(
        key="summary",
        title="Summary",
        description="How many rows each category holds, and their amounts.",
        columns=(
            Column("category", "Category", TEXT),
            Column("rows", "Rows", COUNT),
            Column("amount", _amount_label("Amount"), AMOUNT),
        ),
        rows=tuple(
            {
                "category": label,
                "rows": len(entries),
                "amount": sum_amounts([item["amount"] for item in entries]),
            }
            for label, entries in categories
        ),
        footer=None,
        empty_text="",
    )
    sections = (
        summary,
        _exception_section(
            "pending",
            "Pending bank transfers",
            "Recorded and awaiting an Administrator's confirmation or rejection.",
            (Column("moment", f"Recorded ({tz_name})", MOMENT),),
            pending,
            "No bank transfer is pending.",
        ),
        _exception_section(
            "rejected",
            "Rejected bank transfers",
            "Rejected by an Administrator. They have no financial effect.",
            (Column("moment", f"Rejected ({tz_name})", MOMENT),),
            rejected,
            "No bank transfer has been rejected.",
        ),
        _exception_section(
            "intents",
            "Active online payment intents",
            "Pending, or with a browser-observed success still awaiting its signed webhook.",
            (
                Column("moment", f"Created ({tz_name})", MOMENT),
                Column("status", "Status", TEXT),
            ),
            intents,
            "No online payment intent is active.",
        ),
        _exception_section(
            "reconciliation",
            "Provider events requiring reconciliation",
            "Verified provider events that conflicted with the recorded financial state. "
            "They had no financial effect.",
            (
                Column("moment", f"Received ({tz_name})", MOMENT),
                Column("event", "Event", TEXT),
                Column("intent_status", "Intent status", TEXT),
            ),
            events,
            "No provider event requires reconciliation.",
        ),
    )
    notes = (
        "This report only shows these states; it changes nothing. Each item is handled from "
        "its invoice's payments or payment intents page.",
        "Amounts here are not collections: pending and rejected transfers, intents and "
        "events requiring reconciliation do not change any balance.",
    )
    return _report(
        EXCEPTIONS,
        filters,
        tz_name,
        generated_local,
        notes,
        sections,
        extra_filters=(("As of", "The current state when this report was generated"),),
    )


_BUILDERS = {
    COLLECTIONS: _collections_report,
    OUTSTANDING: _outstanding_report,
    EXCEPTIONS: _exceptions_report,
}


def build_report(report_key, filters, tz_name, moment):
    """The :class:`FinancialReport` `report_key` for validated `filters`,
    generated at the naive-UTC `moment`."""
    return _BUILDERS[report_key](filters, tz_name, to_app_local(tz_name, moment))
