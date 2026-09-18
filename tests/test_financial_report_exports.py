"""Phase 5 / M08: the CSV and PDF renderers of a financial report, and the
report filters, tested as plain functions.

``app/services/financial_report_exports.py`` and
``app/services/financial_reports.py`` are Flask-independent, so these tests
build a report model directly and prove the formatting and safety rules
without any route or database.
"""

import ast
import csv
import io
import pathlib
import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

import tests.financial_report_fixtures as rx
from app.services import financial_report_exports as exports
from app.services import financial_reports as reports
from app.services.financial_reports import (
    AMOUNT,
    COUNT,
    MOMENT,
    TEXT,
    Column,
    FinancialReport,
    ReportSection,
)


def _report(rows, footer=None, filters=(("Group", "All groups (center-wide)"),)):
    section = ReportSection(
        key="rows",
        title="Rows",
        description="Every row.",
        columns=(
            Column("when", "When (Africa/Tripoli)", MOMENT),
            Column("name", "Name", TEXT),
            Column("count", "Count", COUNT),
            Column("amount", "Amount (LYD)", AMOUNT),
        ),
        rows=tuple(rows),
        footer=footer,
        empty_text="No rows.",
    )
    return FinancialReport(
        key="collections",
        title="Test report",
        filters=tuple(filters),
        generated_local=datetime(2026, 9, 18, 12, 0, 5),
        tz_name="Africa/Tripoli",
        currency_code="LYD",
        notice=reports.NOTICE,
        notes=("A note.",),
        sections=(section,),
    )


def _row(name="Student", amount="10.0000", count=1):
    return {"when": datetime(2026, 9, 1, 0, 0), "name": name, "count": count,
            "amount": Decimal(amount)}


# ===========================================================================
# Cells
# ===========================================================================


@pytest.mark.parametrize(
    "text, expected",
    [
        ("=1+1", "'=1+1"),
        ("+SUM(A1)", "'+SUM(A1)"),
        ("-2+3", "'-2+3"),
        ("@cmd", "'@cmd"),
        ("\tTabbed", "'\tTabbed"),
        ("\rReturn", "'\rReturn"),
        ("Plain = text", "Plain = text"),
        (" =leading space", " =leading space"),
        ("", ""),
    ],
)
def test_csv_text_neutralizes_only_a_leading_formula_character(text, expected):
    assert exports.csv_text(text) == expected


def test_amounts_are_exact_decimal_text_and_never_floats():
    assert exports.csv_cell(Decimal("1250.5000")) == "1250.500"
    assert exports.csv_cell(Decimal("-100.0000")) == "-100.000"
    assert exports.csv_cell(Decimal("0.0001")) == "0.0001"
    assert exports.csv_cell(Decimal("123456789.1234")) == "123456789.1234"
    assert exports.csv_cell(Decimal("0")) == "0.000"
    assert exports.display_text(Decimal("-1250.5000")) == "-1,250.500"
    assert exports.csv_cell(7) == "7"
    assert exports.csv_cell(None) == ""
    assert exports.csv_cell(datetime(2026, 9, 1, 0, 5)) == "2026-09-01 00:05"
    for bad in (1.5, True):
        with pytest.raises(TypeError):
            exports.csv_cell(bad)
        with pytest.raises(TypeError):
            exports.display_text(bad)


def test_the_csv_states_the_whole_report_with_a_bom():
    body = exports.render_csv(_report([_row("=bad"), _row("Good", "-5.0000")],
                                      footer={"name": "Total", "amount": Decimal("5.0000")}))
    assert body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"), newline="")))
    assert rows[:7] == [
        ["Report", "Test report"],
        ["Generated", "2026-09-18 12:00:05 (Africa/Tripoli)"],
        ["Currency", "LYD"],
        ["Filter: Group", "All groups (center-wide)"],
        ["Notice", reports.NOTICE],
        ["Note", "A note."],
        [],
    ]
    header, data = rx.csv_section(rows, "Rows")
    assert header == ["When (Africa/Tripoli)", "Name", "Count", "Amount (LYD)"]
    assert data == [
        ["2026-09-01 00:00", "'=bad", "1", "10.000"],
        ["2026-09-01 00:00", "Good", "1", "-5.000"],
        ["", "Total", "", "5.000"],
    ]
    assert b"\r\n" in body
    empty = list(csv.reader(io.StringIO(exports.render_csv(_report([])).decode("utf-8-sig"))))
    assert rx.csv_section(empty, "Rows")[1] == [["No rows."]]


# ===========================================================================
# PDF
# ===========================================================================


def test_wrapping_keeps_every_character_and_respects_the_width():
    text = "A long   name with averyveryveryverylongword and more words"
    lines = exports._wrap(text, 10)
    assert all(len(line) <= 10 for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")
    assert exports._wrap("", 10) == [""]
    assert exports._wrap("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]


def test_pdf_text_is_only_ever_a_hex_string():
    for text in ("(a) \\ <b>) Tj ET", "€ café — naïve", "%comment /Name << >>"):
        operand = exports._hex_string(text)
        assert re.fullmatch(r"<[0-9A-F]*>", operand), operand
        assert bytes.fromhex(operand[1:-1]).decode("cp1252") == text
    assert exports._hex_string("() ") == "<282920>"
    right_to_left_mark = chr(0x200F)
    for bad in ("محمد", "中文", "a" + right_to_left_mark + "b", "bell\x07", "\x00", "del\x7f",
                "tab\t"):
        with pytest.raises(exports.PdfTextUnsupported):
            exports._hex_string(bad)


def test_a_pdf_is_a_valid_multipage_document_with_its_totals():
    rows = [_row(f"Student {n}", f"{n}.0000", n) for n in range(1, 121)]
    footer = {"name": "Total (120)", "amount": Decimal("7260.0000")}
    body = exports.render_pdf(_report(rows, footer=footer))
    assert rx.pdf_objects_valid(body) == 5 + 2 * rx.pdf_page_count(body)
    pages = rx.pdf_page_count(body)
    assert pages >= 3
    texts = rx.pdf_texts(body)
    assert texts.count("When (Africa/Tripoli)") == pages
    assert texts.count("Rows (continued)") == pages - 1
    assert "Total (120)" in texts and "7,260.000" in texts
    assert [text for text in texts if text.startswith("Student ")] == [
        f"Student {n}" for n in range(1, 121)]
    assert b"/Courier" in body and b"/FontFile" not in body
    for name in (b"/JavaScript", b"/JS", b"/OpenAction", b"/AA", b"/URI", b"/Launch",
                 b"/EmbeddedFile", b"/AcroForm", b"/Annots"):
        assert name not in body


def test_a_pdf_with_unsupported_text_is_refused_whole():
    with pytest.raises(exports.PdfTextUnsupported):
        exports.render_pdf(_report([_row("Latin"), _row("علي")]))
    with pytest.raises(exports.PdfTextUnsupported):
        exports.render_pdf(_report([_row()], filters=(("Group", "مجموعة"),)))
    assert exports.render_pdf(_report([_row("Zoë Müller-Laski")])).startswith(b"%PDF-1.4")


def test_an_empty_section_states_that_it_has_no_rows():
    texts = rx.pdf_texts(exports.render_pdf(_report([])))
    assert "No rows." in texts


def _imports_and_calls(module):
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    imported, called, attributes = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
        elif isinstance(node, ast.Call):
            function = node.func
            called.add(function.attr if isinstance(function, ast.Attribute)
                       else getattr(function, "id", None))
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)
    return imported, called, attributes


def test_the_export_module_touches_no_file_network_process_or_log():
    imported, called, _ = _imports_and_calls(exports)
    assert imported == {"codecs", "csv", "io", "re", "datetime", "decimal",
                        "app.services.financial_reports", "app.services.money"}
    assert not called & {"open", "print", "system", "Popen", "urlopen", "write_bytes"}
    imported, called, attributes = _imports_and_calls(reports)
    assert not {name for name in imported if name.split(".")[0] in (
        "os", "logging", "subprocess", "socket", "urllib", "requests", "pathlib")}
    assert not called & {"commit", "add", "add_all", "delete", "flush", "with_for_update",
                         "print", "open"}
    for column in ("provider_reference", "idempotency_key", "payload_digest",
                   "bank_transfer_reference", "bank_transfer_date", "rejection_reason",
                   "provider_event_id", "snapshot", "void_reason"):
        assert column not in attributes, column


# ===========================================================================
# Filters, without a database
# ===========================================================================

_MOMENT = datetime(2026, 9, 18, 10, 0, 0)


def _parse(key, **values):
    args = MultiDict([(name, value) for name, items in values.items()
                      for value in (items if isinstance(items, list) else [items])])
    return reports.parse_report_filters(key, args, "Africa/Tripoli", _MOMENT)


def test_explicit_dates_become_tripoli_midnight_bounds():
    filters, errors = _parse(reports.COLLECTIONS, start="2026-09-01", end="2026-09-30")
    assert errors == []
    assert (filters.start, filters.end, filters.default_range) == (
        date(2026, 9, 1), date(2026, 9, 30), False)
    assert filters.start_utc == datetime(2026, 8, 31, 22, 0, 0)
    assert filters.end_utc == datetime(2026, 9, 30, 22, 0, 0)
    assert filters.query_args() == {"start": "2026-09-01", "end": "2026-09-30"}


def test_absent_dates_are_the_current_month_and_nothing_else_is_guessed():
    filters, errors = _parse(reports.COLLECTIONS)
    assert errors == [] and filters.default_range
    assert (filters.start, filters.end) == (date(2026, 9, 1), date(2026, 9, 30))
    for values in ({"start": "2026-09-01"}, {"end": "2026-09-30"},
                   {"start": "2026-09-30", "end": "2026-09-01"},
                   {"start": ["2026-09-01", "2026-09-01"], "end": "2026-09-30"},
                   {"start": "2026-9-01", "end": "2026-09-30"}):
        filters, errors = _parse(reports.COLLECTIONS, **values)
        assert filters is None and errors, values
    for key in (reports.OUTSTANDING, reports.EXCEPTIONS):
        filters, errors = _parse(key)
        assert errors == [] and filters.start is None and filters.query_args() == {}
        assert _parse(key, start="2026-09-01", end="2026-09-30")[1] == [
            reports.DATES_NOT_ACCEPTED_MESSAGE]
