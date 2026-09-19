"""Phase 5 / M08: the CSV and PDF renderers of a financial report, and the
report filters, tested as plain functions.

``app/services/financial_report_exports.py`` and
``app/services/financial_reports.py`` are Flask-independent, so these tests
build a report model directly and prove the formatting and safety rules
without any route or database.
"""

import ast
import csv
import hashlib
import io
import pathlib
import unicodedata
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

#: The bundled font: DejaVu Sans 2.37, as published by the DejaVu project.
_FONT_SHA256 = "7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954"

_ALI = "علي"
#: "علي" shaped and in visual order: YEH final, LAM medial, AIN initial.
_ALI_DRAWN = (
    "\N{ARABIC LETTER YEH FINAL FORM}"
    "\N{ARABIC LETTER LAM MEDIAL FORM}"
    "\N{ARABIC LETTER AIN INITIAL FORM}"
)
#: "محمد" likewise: DAL final, MEEM medial, HAH medial, MEEM initial.
_MUHAMMAD_DRAWN = (
    "\N{ARABIC LETTER DAL FINAL FORM}"
    "\N{ARABIC LETTER MEEM MEDIAL FORM}"
    "\N{ARABIC LETTER HAH MEDIAL FORM}"
    "\N{ARABIC LETTER MEEM INITIAL FORM}"
)


def _drawn(text):
    return exports._visual(text, exports._base_right_to_left(text))


def test_the_bundled_font_is_dejavu_sans_outside_the_public_static_folder():
    root = pathlib.Path(__file__).resolve().parents[1]
    font = exports.FONT_PATH
    assert font == root / "app" / "assets" / "fonts" / "DejaVuSans.ttf"
    assert (root / "app" / "static") not in font.parents
    assert hashlib.sha256(font.read_bytes()).hexdigest() == _FONT_SHA256
    notice = (font.parent / "DejaVuSans-LICENSE.txt").read_text(encoding="utf-8")
    assert "Bitstream Vera Fonts Copyright" in notice and "Arev Fonts Copyright" in notice
    assert "DejaVu changes are in public domain" in notice


def test_arabic_is_shaped_into_joined_forms_in_visual_order():
    assert _drawn(_ALI) == _ALI_DRAWN
    assert _drawn("لا") == "\N{ARABIC LIGATURE LAM WITH ALEF ISOLATED FORM}"
    assert _drawn("محمد علي") == f"{_ALI_DRAWN} {_MUHAMMAD_DRAWN}"
    for name in ("محمد علي", "عبدالله الطرابلسي", "لا إله", "مجموعة الصباح"):
        drawn = _drawn(name)
        # Every letter is a joined presentation form, and undoing the visual
        # order and the shaping gives the stored name back.
        assert all(character == " " or unicodedata.name(character).endswith("FORM")
                   for character in drawn), drawn
        assert rx.logical(drawn) == name
    # The font has no "Allah" ligature glyph, so the name is drawn letter by letter.
    assert "\N{ARABIC LIGATURE ALLAH ISOLATED FORM}" not in _drawn("عبدالله")


def test_mixed_direction_cells_keep_each_run_readable():
    assert _drawn("Ali علي Hassan") == f"Ali {_ALI_DRAWN} Hassan"
    assert _drawn("علي Ali") == f"Ali {_ALI_DRAWN}"
    assert _drawn("مجموعة 2 — Level A").startswith("Level A — 2 ")
    assert exports._base_right_to_left("علي Ali") is True
    assert exports._base_right_to_left("Ali علي") is False
    assert exports._base_right_to_left("2026 علي") is True


def test_harakat_are_kept_and_drawn_before_their_letter():
    drawn = _drawn("ش\N{ARABIC FATHA}د\N{ARABIC SHADDA}ة")
    assert "\N{ARABIC FATHA}" in drawn and "\N{ARABIC SHADDA}" in drawn
    # DejaVu draws a mark over the glyph that follows it: shadda, then its
    # dal; fatha, then its sheen.
    dal = drawn.index("\N{ARABIC LETTER DAL FINAL FORM}")
    sheen = drawn.index("\N{ARABIC LETTER SHEEN INITIAL FORM}")
    assert drawn.index("\N{ARABIC SHADDA}") == dal - 1
    assert drawn.index("\N{ARABIC FATHA}") == sheen - 1


def test_accented_latin_and_punctuation_are_drawn_unchanged():
    for text in ("Zoë Müller-Łaski", "José Núñez", "Ærø — 50% (a/b) & c", "Ñandú: «x» €"):
        assert _drawn(text) == text


def test_only_characters_without_a_glyph_are_refused():
    for bad in ("中文", "かな", "नमस्ते", "bell\x07"):
        with pytest.raises(exports.PdfGlyphUnavailable):
            _drawn(bad)
    assert _drawn("a" + chr(0x200F) + "b") == "ab"  # an invisible bidi mark, consumed
    with pytest.raises(exports.PdfGlyphUnavailable):
        exports.render_pdf(_report([_row("Latin"), _row("中文")]))
    body = exports.render_pdf(_report([_row("Latin"), _row(_ALI)],
                                      filters=(("Group", "مجموعة الصباح"),)))
    assert body.startswith(b"%PDF-1.4")
    texts = rx.pdf_texts(body)
    assert _ALI_DRAWN in texts
    assert any(rx.drawn_as(text, "Group: مجموعة الصباح") for text in texts)


def test_wrapping_keeps_every_character_and_respects_the_width():
    size = exports._BODY_SIZE
    for text in ("A long   name with averyveryveryverylongword and more words",
                 "محمد عبدالله الطرابلسي بن علي الفيتوري"):
        rtl = exports._base_right_to_left(text)
        lines = exports._wrap(text, 60, size, rtl)
        assert len(lines) > 1
        assert all(exports._width(line, size, rtl) <= 60 for line in lines)
        assert "".join(lines).replace(" ", "") == text.replace(" ", "")
    assert exports._wrap("", 60, size) == [""]


def test_hostile_text_is_drawn_literally_and_never_interpreted():
    hostile = [
        '<a href="javascript:alert(1)">x</a>',
        "<b>bold</b> <font color=red>red</font> &amp; &lt;",
        "(evil) Tj /JavaScript << /S /Launch >> \\ %comment",
        "<img src=x onerror=1> <para>p</para> <br/>",
    ]
    body = exports.render_pdf(_report([_row(text) for text in hostile]))
    texts = rx.pdf_texts(body)
    for text in hostile:
        assert text in texts, text
    assert rx.pdf_active_names(body) == set()


def test_a_pdf_is_a_valid_multipage_document_with_its_totals():
    rows = [_row(f"Student {n}", f"{n}.0000", n) for n in range(1, 121)]
    footer = {"name": "Total (120)", "amount": Decimal("7260.0000")}
    body = exports.render_pdf(_report(rows, footer=footer))
    assert rx.pdf_objects_valid(body) > 0
    assert rx.pdf_embeds_dejavu(body)
    assert b"Helvetica" not in body and b"/Courier" not in body
    pages = rx.pdf_page_count(body)
    assert pages >= 3
    per_page = rx.pdf_page_texts(body)
    for number, texts in enumerate(per_page, start=1):
        assert texts.count("When (Africa/Tripoli)") == 1, number  # the header, on every page
        assert texts.count("Rows") == 1, number  # the section title repeats with it
        assert f"Test report | Page {number} of {pages} | {reports.NOTICE}" in texts
        assert ("Test report (continued)" in texts) == (number > 1)
    assert {"Total (120)", "7,260.000"} <= set(per_page[-1])
    texts = rx.pdf_texts(body)
    assert [text for text in texts if text.startswith("Student ")] == [
        f"Student {n}" for n in range(1, 121)]
    assert exports.render_pdf(_report(rows, footer=footer)) == body  # deterministic


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


def test_the_export_module_touches_no_network_process_log_or_written_file():
    imported, called, _ = _imports_and_calls(exports)
    assert imported == {
        "codecs", "csv", "io", "re", "threading", "unicodedata", "datetime", "decimal",
        "pathlib", "arabic_reshaper", "arabic_reshaper.ligatures",
        "arabic_reshaper.reshaper_config", "bidi", "reportlab.lib", "reportlab.lib.pagesizes",
        "reportlab.pdfbase", "reportlab.pdfbase.ttfonts", "reportlab.pdfgen.canvas",
        "reportlab.platypus", "app.services.financial_reports", "app.services.money"}
    assert not called & {"open", "print", "system", "Popen", "urlopen", "write_bytes",
                         "write_text", "Paragraph", "XPreformatted", "linkURL", "linkAbsolute"}
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
