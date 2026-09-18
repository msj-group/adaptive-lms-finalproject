"""CSV and PDF renderings of one financial report (Phase 5 / M08).

Flask-independent pure functions over a
:class:`~app.services.financial_reports.FinancialReport`: no ``request``, no
ORM, no file, no network, no subprocess and no logging. Both return the whole
document as ``bytes``; nothing is written to disk and nothing is kept.

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

**PDF** is written directly, with no library, browser, shell or external
resource: PDF 1.4, A4 landscape, the standard Courier and Courier-Bold fonts
(no embedded file) in ``WinAnsiEncoding``. Every piece of text -- the
report's own wording and every stored name alike -- is written as a
hexadecimal string operand of ``Tj``, so no character of it can close the
string or be read as a PDF operator, name or markup. The document has no
JavaScript, action, link, form, annotation, attachment or external reference.
Tables span as many pages as they need, repeat their column headers on every
page they continue on, and end with their totals. Courier is monospaced, so
every column width and line wrap is computed exactly.

A standard font can only show the characters of ``WinAnsiEncoding`` (Latin
script). When any text of the report holds another character -- an Arabic
name, for example -- :func:`render_pdf` raises :class:`PdfTextUnsupported`
instead of dropping or replacing it; the caller offers the CSV and HTML
report, which show every character.
"""

import codecs
import csv
import io
import re
from datetime import datetime
from decimal import Decimal

from app.services.financial_reports import AMOUNT, COUNT, TEXT
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

_CSV_AMOUNT = re.compile(r"-?[0-9]+\.[0-9]{3,4}")
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


class PdfTextUnsupported(ValueError):
    """Some text of the report holds a character the PDF's standard font
    cannot show. Nothing was rendered."""


_PAGE_WIDTH = 842  # A4 landscape, in points
_PAGE_HEIGHT = 595
_MARGIN = 36
_CONTENT_BOTTOM = _MARGIN + 18  # the page footer sits below this line
_BODY_SIZE = 8
_TITLE_SIZE = 14
_HEADING_SIZE = 10
_FOOTER_SIZE = 7
_CHAR_ADVANCE = 0.6  # every Courier glyph is 600/1000 of the font size wide
_CELL_GAP = 2  # blank characters between two columns
_CELL_PADDING = 2.5  # points above and below a row's text
_MIN_TEXT_COLUMN = 8  # characters

_REGULAR = "F1"
_BOLD = "F2"


def _line_height(size):
    return size * 1.25


def _chars_per_line(size, width=_PAGE_WIDTH - 2 * _MARGIN):
    return int(width // (size * _CHAR_ADVANCE))


def _encode(text):
    """`text` in ``WinAnsiEncoding``, or :class:`PdfTextUnsupported`. A
    control character is refused as well: it has no glyph to show."""
    try:
        encoded = text.encode("cp1252")
    except UnicodeEncodeError:
        raise PdfTextUnsupported("The report holds text the PDF font cannot show") from None
    if any(byte < 0x20 or byte == 0x7F for byte in encoded):
        raise PdfTextUnsupported("The report holds a control character")
    return encoded


def _hex_string(text):
    """A PDF hexadecimal string operand: only ``0-9A-F`` between ``<`` and
    ``>``, whatever `text` holds."""
    return "<" + _encode(text).hex().upper() + ">"


def _wrap(text, width):
    """`text` broken into lines of at most `width` characters, at spaces
    where possible. Runs of whitespace read as one space; every other
    character is kept, and nothing is cut off."""
    lines, current = [], ""
    for word in text.split():
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not word:
            continue
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current or not lines:
        lines.append(current)
    return lines


class _Document:
    """Pages of content-stream operators, laid out top to bottom."""

    def __init__(self, title):
        self.title = title
        self.pages = []
        self.ops = None
        self.y = 0
        self.new_page()

    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        self.y = _PAGE_HEIGHT - _MARGIN
        if len(self.pages) > 1:
            self.paragraph(f"{self.title} (continued)", _HEADING_SIZE, bold=True)
            self.y -= 4

    def room(self):
        return self.y - _CONTENT_BOTTOM

    def text(self, x, y, text, size, bold=False):
        if text:
            self.ops.append(
                f"BT /{_BOLD if bold else _REGULAR} {size} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm "
                f"{_hex_string(text)} Tj ET"
            )

    def rule(self, y, width=0.4, gray=0.6):
        self.ops.append(
            f"{gray:.2f} G {width:.2f} w {_MARGIN:.2f} {y:.2f} m "
            f"{_PAGE_WIDTH - _MARGIN:.2f} {y:.2f} l S 0 G"
        )

    def shade(self, y_top, height, gray=0.9):
        self.ops.append(
            f"{gray:.2f} g {_MARGIN:.2f} {y_top - height:.2f} "
            f"{_PAGE_WIDTH - 2 * _MARGIN:.2f} {height:.2f} re f 0 g"
        )

    def paragraph(self, text, size, bold=False):
        height = _line_height(size)
        for line in _wrap(text, _chars_per_line(size)):
            if self.room() < height:
                self.new_page()
            self.y -= height
            self.text(_MARGIN, self.y + (height - size) / 2 + 1, line, size, bold)

    def gap(self, points):
        self.y -= points


def _column_widths(section, texts):
    """Character widths of the section's columns. Numbers and moments never
    wrap -- their header too, while the page has room -- and text columns
    share what is left, none narrower than :data:`_MIN_TEXT_COLUMN`."""
    columns = section.columns
    available = _chars_per_line(_BODY_SIZE) - _CELL_GAP * (len(columns) - 1)
    longest = [max((len(text) for text in texts[index]), default=0) for index in range(len(columns))]
    flexible = sorted(
        (max(longest[index], len(column.label), 1), index)
        for index, column in enumerate(columns)
        if column.kind == TEXT
    )

    def fixed(whole_label):
        return {
            index: max(
                longest[index],
                len(column.label) if whole_label else max(map(len, column.label.split())),
                1,
            )
            for index, column in enumerate(columns)
            if column.kind != TEXT
        }

    widths_of_fixed = fixed(True)
    if sum(widths_of_fixed.values()) + _MIN_TEXT_COLUMN * len(flexible) > available:
        widths_of_fixed = fixed(False)
    widths = [widths_of_fixed.get(index, 0) for index in range(len(columns))]
    remaining = available - sum(widths)
    for position, (natural, index) in enumerate(flexible):
        fair = remaining // (len(flexible) - position)
        widths[index] = max(min(natural, fair), _MIN_TEXT_COLUMN)
        remaining -= widths[index]
    if sum(widths) > available:
        raise ValueError("The report's columns do not fit the page width")
    return widths


def _draw_row(document, columns, widths, cells, bold=False, shade=False):
    """Draw one table row whose `cells` are already wrapped, on the current
    page. Numbers are right-aligned."""
    size = _BODY_SIZE
    leading = _line_height(size)
    height = max(len(lines) for lines in cells) * leading + 2 * _CELL_PADDING
    if shade:
        document.shade(document.y, height)
    char_width = size * _CHAR_ADVANCE
    offset = 0
    for column, width, lines in zip(columns, widths, cells):
        left = _MARGIN + offset * char_width
        for number, line in enumerate(lines):
            baseline = document.y - _CELL_PADDING - (number + 1) * leading + (leading - size) / 2 + 1
            x = left + (width - len(line)) * char_width if is_numeric(column) else left
            document.text(x, baseline, line, size, bold)
        offset += width + _CELL_GAP
    document.y -= height
    return height


def _row_height(cells):
    return max(len(lines) for lines in cells) * _line_height(_BODY_SIZE) + 2 * _CELL_PADDING


def _paragraph_height(text, size):
    return len(_wrap(text, _chars_per_line(size))) * _line_height(size)


def _draw_section(document, section):
    """One section: its heading and description, then its table -- header,
    rows, "no rows" sentence, totals. A section starts on a new page unless
    its heading, header and first row (or, when it has no rows, the whole
    table) fit; a table that continues repeats its header."""
    columns = section.columns
    body = [[display_text(row.get(column.key)) for column in columns] for row in section.rows]
    footer = (
        [display_text(section.footer.get(column.key)) for column in columns]
        if section.footer
        else None
    )
    texts = [
        [row[index] for row in body + ([footer] if footer else [])] for index in range(len(columns))
    ]
    widths = _column_widths(section, texts)

    def cells_of(row_texts):
        return [_wrap(text, width) for text, width in zip(row_texts, widths)]

    header = cells_of(column.label for column in columns)
    rows = [cells_of(row_texts) for row_texts in body]
    footer_cells = cells_of(footer) if footer else None
    empty_height = (
        _paragraph_height(section.empty_text, _BODY_SIZE) + 2 * _CELL_PADDING
        if not rows and section.empty_text
        else 0
    )
    lead = 8 + _line_height(_HEADING_SIZE) + 2
    if section.description:
        lead += _paragraph_height(section.description, _BODY_SIZE)
    if rows:
        table = _row_height(header) + _row_height(rows[0])
    elif footer_cells:
        table = _row_height(header) + empty_height + _row_height(footer_cells)
    else:
        table = empty_height
    if document.room() < lead + table:
        document.new_page()
    document.gap(8)
    document.paragraph(section.title, _HEADING_SIZE, bold=True)
    if section.description:
        document.paragraph(section.description, _BODY_SIZE)
    document.gap(2)

    def draw_header():
        _draw_row(document, columns, widths, header, bold=True, shade=True)

    def continue_if_needed(height):
        if document.room() < height:
            document.new_page()
            document.paragraph(f"{section.title} (continued)", _BODY_SIZE, bold=True)
            draw_header()

    if not rows and not footer_cells:
        if section.empty_text:
            document.paragraph(section.empty_text, _BODY_SIZE)
        return
    draw_header()
    for cells in rows:
        continue_if_needed(_row_height(cells))
        _draw_row(document, columns, widths, cells)
        document.rule(document.y, width=0.3, gray=0.8)
    if not rows and section.empty_text:
        document.gap(_CELL_PADDING)
        document.paragraph(section.empty_text, _BODY_SIZE)
        document.gap(_CELL_PADDING)
    if footer_cells:
        continue_if_needed(_row_height(footer_cells))
        document.rule(document.y, width=0.9, gray=0.0)
        _draw_row(document, columns, widths, footer_cells, bold=True)


def _page_footers(document, report):
    total = len(document.pages)
    for number, ops in enumerate(document.pages, start=1):
        text = f"{report.title} | Page {number} of {total} | {report.notice}"
        document.ops = ops
        for index, line in enumerate(_wrap(text, _chars_per_line(_FOOTER_SIZE))):
            document.text(_MARGIN, _MARGIN - 12 - index * _line_height(_FOOTER_SIZE), line, _FOOTER_SIZE)


def _assemble(pages, title):
    """The PDF file: catalog, page tree, two standard fonts, the document
    information, and one page plus one content stream per page."""
    first_page = 6
    page_numbers = [first_page + 2 * index for index in range(len(pages))]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{number} 0 R" for number in page_numbers)
            + f"] /Count {len(pages)} >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>",
        f"<< /Title {_hex_string(title)} /Producer {_hex_string('Adaptive English LMS')} >>".encode(
            "ascii"
        ),
    ]
    for number, ops in zip(page_numbers, pages):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
                f"/Resources << /Font << /{_REGULAR} 3 0 R /{_BOLD} 4 0 R >> >> "
                f"/Contents {number + 1} 0 R >>"
            ).encode("ascii")
        )
        stream = "\n".join(ops).encode("ascii")
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode("ascii")
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 5 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(output)


def render_pdf(report):
    """The whole report as PDF bytes, or :class:`PdfTextUnsupported` when
    some text cannot be shown in the standard font."""
    document = _Document(report.title)
    document.paragraph(report.title, _TITLE_SIZE, bold=True)
    document.gap(4)
    document.paragraph(f"Generated: {generated_text(report)}", _BODY_SIZE)
    document.paragraph(f"Currency: {report.currency_code}", _BODY_SIZE)
    document.paragraph("Applied filters:", _BODY_SIZE, bold=True)
    for label, value in report.filters:
        document.paragraph(f"{label}: {value}", _BODY_SIZE)
    document.gap(2)
    document.paragraph(report.notice, _BODY_SIZE, bold=True)
    for note in report.notes:
        document.paragraph(note, _BODY_SIZE)
    for section in report.sections:
        _draw_section(document, section)
    _page_footers(document, report)
    return _assemble(document.pages, report.title)
