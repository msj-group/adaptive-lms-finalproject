"""CSV and PDF renderings of one financial report (Phase 5 / M08).

Flask-independent pure functions over a
:class:`~app.services.financial_reports.FinancialReport`: no ``request``, no
ORM, no network, no subprocess and no logging, and the only file read is the
bundled font. Both return the whole document as ``bytes``; nothing is written
to disk and nothing is kept.

Both outputs state exactly what the HTML page states -- the title, the
applied filters, the generation moment and its timezone, the currency, the
operational notice, the notes, every row of every section in the report's own
order and every total. Neither drops, truncates or reorders a row.

**A cell is formatted by its value's type**, identically everywhere: a
``Decimal`` is an exact amount, an ``int`` a count, a ``datetime`` a
center-local moment (``YYYY-MM-DD HH:MM``), a ``str`` text. Amounts on a page
are grouped for reading (``1,250.500``); in a CSV they are exact, ungrouped
decimal text (``1250.500``) -- never a ``float`` -- so a spreadsheet reads the
same number.

**CSV** is UTF-8 with a byte-order mark, for spreadsheet compatibility. Every
text cell that begins with ``=``, ``+``, ``-`` or ``@`` -- or a tab or carriage
return, which spreadsheets also treat as formula triggers -- is prefixed with
``'`` so it is shown as text and never evaluated. Amount and count cells are
produced here from ``Decimal`` / ``int`` and proved to match a strict numeric
shape, so a negative amount stays a number.

**PDF** is built in memory with ReportLab, A4 landscape, in the one bundled
Unicode font -- DejaVu Sans, loaded from a fixed application-owned path and
embedded (subset) in every file -- so Arabic, Latin, accented Latin and mixed
text all print. Arabic is shaped into its joined letter forms
(``arabic-reshaper``) and each line is put in visual order by the Unicode
bidirectional algorithm (``python-bidi``) with its cell's base direction,
after the text has been wrapped in logical order; a right-to-left cell is
right-aligned. Every string is drawn as plain table-cell or canvas text:
nothing a record holds reaches a ReportLab markup parser, and the document has
no link, JavaScript, action, form, annotation or attachment. No browser,
shell, network, file write or user-supplied path is involved. Tables span as
many pages as they need, repeat their title and column headers on every page
they continue on, and end with their totals; every page states the title,
"Page n of m" and the notice. The output is deterministic for a given report.

A character the bundled font has no glyph for -- a Chinese, Japanese or
Devanagari character, for example -- is never dropped or replaced:
:func:`render_pdf` raises :class:`PdfGlyphUnavailable` and the caller offers
the CSV and HTML report.
"""

import codecs
import csv
import io
import re
import threading
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from arabic_reshaper import ArabicReshaper
from arabic_reshaper.ligatures import LIGATURES
from arabic_reshaper.reshaper_config import default_config
from bidi import get_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.financial_report_types import AMOUNT, COUNT, TEXT
from app.services.money import amount_input_text, format_amount

CSV_CONTENT_TYPE = "text/csv; charset=utf-8"
PDF_CONTENT_TYPE = "application/pdf"

MOMENT_FORMAT = "%Y-%m-%d %H:%M"
GENERATED_FORMAT = "%Y-%m-%d %H:%M:%S"

_NUMERIC_KINDS = (AMOUNT, COUNT)


def generated_text(report):
    """``2026-09-18 14:05:09 (Africa/Tripoli)``."""
    return f"{report.generated_local.strftime(GENERATED_FORMAT)} ({report.tz_name})"


def display_text(value):
    """One cell as a page shows it: grouped exact amounts, counts, center-local
    moments and text. ``None`` is an empty cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        raise TypeError("A report cell is never a boolean")
    if isinstance(value, Decimal):
        return format_amount(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return value.strftime(MOMENT_FORMAT)
    if isinstance(value, str):
        return value
    raise TypeError(f"Unsupported report cell: {type(value).__name__}")


def is_numeric(column):
    return column.kind in _NUMERIC_KINDS


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

#: The characters a spreadsheet may treat as the start of a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_CSV_AMOUNT = re.compile(r"-?[0-9]+(?:\.[0-9]{1,4})?")
_CSV_COUNT = re.compile(r"[0-9]+")


def csv_text(text):
    """`text` safe for a spreadsheet: a leading formula character is
    neutralized with ``'``; nothing else is changed."""
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def csv_cell(value):
    """One cell as the CSV writes it. Amounts are exact decimal text; every
    text cell is neutralized."""
    if value is None:
        return ""
    if isinstance(value, bool):
        raise TypeError("A report cell is never a boolean")
    if isinstance(value, Decimal):
        text = amount_input_text(value)
        if _CSV_AMOUNT.fullmatch(text) is None:
            raise ValueError("An amount did not render as exact decimal text")
        return text
    if isinstance(value, int):
        text = str(value)
        if _CSV_COUNT.fullmatch(text) is None:
            raise ValueError("A count did not render as digits")
        return text
    return csv_text(display_text(value))


def render_csv(report):
    """The whole report as UTF-8 CSV bytes with a byte-order mark."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")

    def text_row(*cells):
        writer.writerow([csv_text(cell) for cell in cells])

    text_row("Report", report.title)
    text_row("Generated", generated_text(report))
    text_row("Currency", report.currency_code)
    for label, value in report.filters:
        text_row(f"Filter: {label}", value)
    text_row("Notice", report.notice)
    for note in report.notes:
        text_row("Note", note)
    for section in report.sections:
        writer.writerow([])
        text_row("Section", section.title)
        if section.description:
            text_row("Description", section.description)
        text_row(*(column.label for column in section.columns))
        for row in section.rows:
            writer.writerow([csv_cell(row.get(column.key)) for column in section.columns])
        if not section.rows and section.empty_text:
            text_row(section.empty_text)
        if section.footer:
            writer.writerow(
                [csv_cell(section.footer.get(column.key)) for column in section.columns]
            )
    return codecs.BOM_UTF8 + buffer.getvalue().encode("utf-8")




# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


class PdfGlyphUnavailable(ValueError):
    """Some text of the report holds a character the bundled font has no
    glyph for (for example a Chinese, Japanese or Devanagari character).
    Nothing was rendered. Arabic, Latin and accented Latin text never raise
    this."""


#: The one bundled font: DejaVu Sans 2.37, which covers Latin, accented Latin
#: and Arabic -- including the presentation forms Arabic shaping produces.
#: It is read only from this fixed, application-owned path (never from the
#: host's fonts and never from a request), outside the public static folder,
#: and embedded (subset) in every report PDF. Its license is beside it.
FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "DejaVuSans.ttf"
FONT_NAME = "LmsReportDejaVuSans"

_PAGE_SIZE = landscape(A4)
_MARGIN = 36
_TOP_MARGIN = _MARGIN + 18  # room for the "(continued)" line above the frame
_BOTTOM_MARGIN = _MARGIN + 12  # room for the page footer below the frame
_BODY_SIZE = 8
_TITLE_SIZE = 14
_HEADING_SIZE = 10
_FOOTER_SIZE = 7
_PADDING = 3
_MIN_TEXT_WIDTH = 48
_HEADER_SHADE = colors.Color(0.9, 0.9, 0.9)
_FOOTER_SHADE = colors.Color(0.96, 0.96, 0.96)
_RULE = colors.Color(0.75, 0.75, 0.75)
_PRODUCER = "Adaptive English LMS"

_font_lock = threading.Lock()
_font_state = {}


def _leading(size):
    return size * 1.25


def _frame_width():
    return _PAGE_SIZE[0] - 2 * _MARGIN


def _loaded_font():
    """``(font, reshaper)``: the bundled font, registered once, and an Arabic
    reshaper that produces only glyphs the font has.

    Of the reshaper's default ligatures, one is enabled only when the font has
    its glyph: DejaVu Sans has the lam-alef ligatures but not the "Allah"
    ligature, so a common name such as "عبدالله" is drawn letter by letter
    rather than as a missing glyph. Harakat are kept (never silently deleted)
    and, as DejaVu's marks are drawn over the glyph that follows them, left
    unshifted: the visual reversal already puts each mark before its letter.
    """
    with _font_lock:
        if not _font_state:
            font = TTFont(FONT_NAME, str(FONT_PATH))
            pdfmetrics.registerFont(font)
            glyphs = font.face.charToGlyph
            configuration = {"delete_harakat": False, "shift_harakat_position": False}
            for name, (_match, forms) in LIGATURES:
                if default_config.get(name):
                    configuration[name] = all(
                        ord(character) in glyphs for form in forms for character in form
                    )
            _font_state["font"] = font
            _font_state["reshaper"] = ArabicReshaper(configuration=configuration)
        return _font_state["font"], _font_state["reshaper"]


def _is_right_to_left(character):
    return unicodedata.bidirectional(character) in ("R", "AL")


def _base_right_to_left(text):
    """Whether `text` reads right to left: its first strong character is
    Arabic (or another right-to-left script)."""
    for character in text:
        direction = unicodedata.bidirectional(character)
        if direction == "L":
            return False
        if direction in ("R", "AL"):
            return True
    return False


def _visual(line, right_to_left):
    """One line of logical text as it is drawn left to right: normalized,
    Arabic shaped into joined letter forms, put in visual order by the
    Unicode bidirectional algorithm with the cell's base direction, and
    stripped of the invisible formatting marks that algorithm consumed. Every
    visible character must have a glyph in the bundled font."""
    font, reshaper = _loaded_font()
    line = unicodedata.normalize("NFC", line)
    if any(_is_right_to_left(character) for character in line):
        line = get_display(reshaper.reshape(line), base_dir="R" if right_to_left else "L")
    line = "".join(character for character in line if unicodedata.category(character) != "Cf")
    glyphs = font.face.charToGlyph
    if any(ord(character) not in glyphs for character in line if character != " "):
        raise PdfGlyphUnavailable("The report holds a character the PDF font cannot show")
    return line


def _width(line, size, right_to_left=False):
    return pdfmetrics.stringWidth(_visual(line, right_to_left), FONT_NAME, size)


def _longest_prefix(word, width, size, right_to_left):
    """How many leading characters of `word` fit `width` (at least one)."""
    fitting = 1
    for end in range(2, len(word) + 1):
        if _width(word[:end], size, right_to_left) > width:
            break
        fitting = end
    return fitting


def _wrap(text, width, size, right_to_left=False):
    """`text` as logical lines that each fit `width` points once shaped:
    broken at spaces where possible, inside a word only when the word alone
    is wider than the column. Runs of whitespace read as one space; no other
    character is lost."""
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if _width(candidate, size, right_to_left) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(word) > 1 and _width(word, size, right_to_left) > width:
            cut = _longest_prefix(word, width, size, right_to_left)
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current or not lines:
        lines.append(current)
    return lines


def _cell(text, width, size=_BODY_SIZE):
    """``(drawn text, right_to_left)`` of one table cell: wrapped logically,
    each line then shaped and ordered for drawing, joined by newlines. The
    string is drawn as plain text -- ReportLab never parses it as markup."""
    right_to_left = _base_right_to_left(text)
    lines = _wrap(text, width, size, right_to_left)
    return "\n".join(_visual(line, right_to_left) for line in lines), right_to_left


def _column_widths(section, texts):
    """Point widths of the section's columns. Numbers and moments never wrap
    -- their header too, while the page has room -- and text columns share
    what is left, none narrower than :data:`_MIN_TEXT_WIDTH`."""
    columns = section.columns
    available = _frame_width()

    def natural(index, whole_label=True):
        values = [_width(text, _BODY_SIZE, _base_right_to_left(text)) for text in texts[index]]
        label = columns[index].label
        labels = [label] if whole_label else label.split()
        return max(values + [_width(part, _BODY_SIZE) for part in labels]) + 2 * _PADDING

    fixed_indexes = [index for index, column in enumerate(columns) if column.kind != TEXT]
    flexible = sorted(
        (natural(index), index) for index, column in enumerate(columns) if column.kind == TEXT
    )
    fixed = {index: natural(index) for index in fixed_indexes}
    if sum(fixed.values()) + _MIN_TEXT_WIDTH * len(flexible) > available:
        fixed = {index: natural(index, whole_label=False) for index in fixed_indexes}
    widths = [fixed.get(index, 0) for index in range(len(columns))]
    remaining = available - sum(widths)
    for position, (wanted, index) in enumerate(flexible):
        fair = remaining / (len(flexible) - position)
        widths[index] = max(min(wanted, fair), _MIN_TEXT_WIDTH)
        remaining -= widths[index]
    if sum(widths) > available + 0.01:
        raise ValueError("The report's columns do not fit the page width")
    return widths


def _lines_table(lines):
    """A borderless one-column block of ``(text, size)`` lines -- the report
    heading, filters, notice and notes -- wrapped to the page width."""
    rows, style = [], [("VALIGN", (0, 0), (-1, -1), "TOP")]
    for text, size in lines:
        drawn, right_to_left = _cell(text, _frame_width() - 2 * _PADDING, size)
        row = len(rows)
        rows.append([drawn])
        style.append(("FONT", (0, row), (0, row), FONT_NAME, size, _leading(size)))
        style.append(("ALIGN", (0, row), (0, row), "RIGHT" if right_to_left else "LEFT"))
        style.append(("BOTTOMPADDING", (0, row), (0, row), 1))
        style.append(("TOPPADDING", (0, row), (0, row), 1))
    table = Table(rows, colWidths=[_frame_width()], hAlign="LEFT")
    table.setStyle(TableStyle(style))
    return table


def _section_table(section):
    """One section as a table: its title, description and column header --
    repeated at the top of every page the table continues on -- then its rows,
    its "no rows" sentence when empty, and its totals row."""
    columns = section.columns
    count = len(columns)
    body = [[display_text(row.get(column.key)) for column in columns] for row in section.rows]
    footer = (
        [display_text(section.footer.get(column.key)) for column in columns]
        if section.footer
        else None
    )
    texts = [
        [row[index] for row in body + ([footer] if footer else [])] for index in range(count)
    ]
    widths = _column_widths(section, texts)
    rows, style = [], [
        ("FONT", (0, 0), (-1, -1), FONT_NAME, _BODY_SIZE, _leading(_BODY_SIZE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), _PADDING),
        ("RIGHTPADDING", (0, 0), (-1, -1), _PADDING),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]

    def spanning(text, size):
        row = len(rows)
        drawn, right_to_left = _cell(text, sum(widths) - 2 * _PADDING, size)
        rows.append([drawn] + [""] * (count - 1))
        style.append(("SPAN", (0, row), (-1, row)))
        style.append(("FONT", (0, row), (-1, row), FONT_NAME, size, _leading(size)))
        style.append(("ALIGN", (0, row), (-1, row), "RIGHT" if right_to_left else "LEFT"))
        return row

    def cells(values):
        row = len(rows)
        drawn = []
        for index, (text, column) in enumerate(zip(values, columns)):
            content, right_to_left = _cell(text, widths[index] - 2 * _PADDING)
            drawn.append(content)
            if is_numeric(column) or right_to_left:
                style.append(("ALIGN", (index, row), (index, row), "RIGHT"))
        rows.append(drawn)
        return row

    title_row = spanning(section.title, _HEADING_SIZE)
    style.append(("TOPPADDING", (0, title_row), (-1, title_row), 8))
    if section.description:
        spanning(section.description, _BODY_SIZE)
    if not body and not footer:
        if section.empty_text:
            spanning(section.empty_text, _BODY_SIZE)
        table = Table(rows, colWidths=widths, hAlign="LEFT")
        table.setStyle(TableStyle(style))
        return table
    header_row = cells([column.label for column in columns])
    style.append(("BACKGROUND", (0, header_row), (-1, header_row), _HEADER_SHADE))
    repeat = len(rows)
    for values in body:
        row = cells(values)
        style.append(("LINEBELOW", (0, row), (-1, row), 0.3, _RULE))
    if not body and section.empty_text:
        spanning(section.empty_text, _BODY_SIZE)
    if footer:
        row = cells(footer)
        style.append(("LINEABOVE", (0, row), (-1, row), 0.9, colors.black))
        style.append(("BACKGROUND", (0, row), (-1, row), _FOOTER_SHADE))
    table = Table(rows, colWidths=widths, repeatRows=repeat, hAlign="LEFT")
    table.setStyle(TableStyle(style))
    return table


def _canvas_class(report):
    """A canvas that, once every page is laid out, gives each page its footer
    (title, "Page n of m", notice) and each later page a "(continued)" line."""

    footer = f"{report.title} | Page {{number}} of {{total}} | {report.notice}"
    continued = f"{report.title} (continued)"

    class _ReportCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._page_states = []

        def showPage(self):
            self._page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._page_states)
            for number, state in enumerate(self._page_states, start=1):
                self.__dict__.update(state)
                self.setFont(FONT_NAME, _FOOTER_SIZE)
                text = footer.format(number=number, total=total)
                for index, line in enumerate(_wrap(text, _frame_width(), _FOOTER_SIZE)):
                    self.drawString(
                        _MARGIN,
                        _MARGIN - 8 - index * _leading(_FOOTER_SIZE),
                        _visual(line, False),
                    )
                if number > 1:
                    self.setFont(FONT_NAME, _HEADING_SIZE)
                    self.drawString(
                        _MARGIN, _PAGE_SIZE[1] - _MARGIN - _HEADING_SIZE, _visual(continued, False)
                    )
                super().showPage()
            super().save()

    return _ReportCanvas


def render_pdf(report):
    """The whole report as PDF bytes, built in memory with the bundled font
    embedded. Raises :class:`PdfGlyphUnavailable` only for a character the
    font has no glyph for."""
    _loaded_font()
    heading = [(report.title, _TITLE_SIZE), (f"Generated: {generated_text(report)}", _BODY_SIZE),
               (f"Currency: {report.currency_code}", _BODY_SIZE), ("Applied filters:", _BODY_SIZE)]
    heading += [(f"{label}: {value}", _BODY_SIZE) for label, value in report.filters]
    heading += [(report.notice, _BODY_SIZE)] + [(note, _BODY_SIZE) for note in report.notes]
    story = [_lines_table(heading)]
    for section in report.sections:
        story += [Spacer(1, 6), _section_table(section)]
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=_PAGE_SIZE,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_TOP_MARGIN,
        bottomMargin=_BOTTOM_MARGIN,
        initialFontName=FONT_NAME,
        initialFontSize=_BODY_SIZE,
        initialLeading=_leading(_BODY_SIZE),
        title=report.title,
        subject=report.notice,
        author=_PRODUCER,
        creator=_PRODUCER,
        producer=_PRODUCER,
        invariant=1,
        pageCompression=1,
    )
    document.build(story, canvasmaker=_canvas_class(report))
    return buffer.getvalue()
