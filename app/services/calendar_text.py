"""The single normalisation boundary for center calendar-event text
(Phase 4 / M10).

Flask-independent pure functions: no ``request``, no ORM, no template,
no I/O. The one authoring surface -- the Administrator's calendar-event
form -- calls exactly these, so a future second caller cannot drift on
what an event title, its details or its location *are*.

**Calendar-event text is plain text.** Not HTML, not Markdown, not a
rich-text document, and never a link. Nothing here strips tags,
sanitises markup or "cleans" anything: if an Administrator types ``<b>``
it is stored as the five characters they typed and rendered as the five
characters they typed, because every template escapes it. There is
deliberately no sanitiser to mis-configure and no ``| safe`` anywhere in
M10.

Declared separately from ``app/services/announcement_text.py`` rather
than imported from it, for the reason every query module in this project
states about its own bounds: **each feature owns the limits its columns
have**, so widening an announcement body can never silently widen a
calendar event's details, and the two can be read beside their own
columns.

What normalisation does, then, is only what a plain-text field honestly
needs:

- **line endings become ``\\n``** in the details, so the same paragraph
  typed on Windows, macOS and Linux is the same stored string and the
  same character count;
- **control characters are rejected, never dropped** -- with the single
  deliberate exception of ``\\n`` and ``\\t`` in the details, which are
  real formatting an event description may legitimately contain.
  Silently deleting a NUL or an escape sequence would store something
  the author never wrote and never saw;
- **the title and the location are collapsed to one line** -- a heading
  and a room name are not paragraphs, and either carrying a newline
  would render as one line anyway while counting as two;
- **outer whitespace is stripped** from all three, so details of nothing
  but spaces are *absent* rather than "5,000 characters of nothing".

**Absent has exactly one spelling.** ``details`` and ``location`` are
optional, and an omitted, blank or whitespace-only value normalises to
``None`` -- never to ``""`` -- so a NULL column and an empty string can
never come to mean two different things, and a no-op edit comparison can
never be defeated by the difference between them.

Length is measured **after** normalisation, against each column's own
width, so what the Administrator is told and what the database can hold
are the same number. An error is returned as a *code*, never as a
sentence: the wording belongs beside the audience, which is the form.
"""

from app.models import (
    CALENDAR_EVENT_DETAILS_MAX_LENGTH,
    CALENDAR_EVENT_LOCATION_MAX_LENGTH,
    CALENDAR_EVENT_TITLE_MAX_LENGTH,
)

#: Every C0 control character plus DEL. ``\n`` and ``\t`` are removed
#: from the details' forbidden set below; the title and the location
#: forbid all of them, because neither has lines or columns.
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}
_DETAILS_FORBIDDEN = _CONTROL_CHARS - {"\n", "\t"}

#: Returned instead of a sentence. The caller owns the wording.
MISSING = "missing"
CONTROL = "control"
TOO_LONG = "too_long"


def normalize_title(raw):
    """``(title, error_code)`` for one calendar-event title.

    Collapses every run of whitespace to a single space and strips the
    ends, so a pasted heading with a stray newline or a double space is
    stored the way it reads. Rejects any control character outright, and
    any title longer than
    :data:`~app.models.CALENDAR_EVENT_TITLE_MAX_LENGTH` characters
    *after* that collapse. A title is required: an event with no name is
    a row nobody could act on.
    """
    text = raw or ""
    if any(ch in _CONTROL_CHARS for ch in text):
        return None, CONTROL
    text = " ".join(text.split())
    if not text:
        return None, MISSING
    if len(text) > CALENDAR_EVENT_TITLE_MAX_LENGTH:
        return None, TOO_LONG
    return text, None


def normalize_details(raw):
    """``(details_or_None, error_code)`` for one event's optional
    details.

    Normalises ``\\r\\n`` and bare ``\\r`` to ``\\n``, strips the outer
    whitespace, and keeps every interior newline and tab exactly as
    typed -- paragraphs in a description are content, not formatting to
    be thrown away. Rejects every other control character, and anything
    longer than :data:`~app.models.CALENDAR_EVENT_DETAILS_MAX_LENGTH`
    characters after normalisation.

    An omitted or blank value returns ``(None, None)``: absent details
    are ``NULL``, and never an empty string.
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    if any(ch in _DETAILS_FORBIDDEN for ch in text):
        return None, CONTROL
    text = text.strip()
    if not text:
        return None, None
    if len(text) > CALENDAR_EVENT_DETAILS_MAX_LENGTH:
        return None, TOO_LONG
    return text, None


def normalize_location(raw):
    """``(location_or_None, error_code)`` for one event's optional
    location.

    Same one-line collapse as the title, the same outright rejection of
    control characters, and the same "absent is ``None``" rule as the
    details. Bounded by
    :data:`~app.models.CALENDAR_EVENT_LOCATION_MAX_LENGTH`.
    """
    text = raw or ""
    if any(ch in _CONTROL_CHARS for ch in text):
        return None, CONTROL
    text = " ".join(text.split())
    if not text:
        return None, None
    if len(text) > CALENDAR_EVENT_LOCATION_MAX_LENGTH:
        return None, TOO_LONG
    return text, None
