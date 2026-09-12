"""The single normalisation boundary for announcement text
(Phase 4 / M09).

Flask-independent pure functions: no ``request``, no ORM, no template,
no I/O. Both authoring surfaces -- the Teacher's group-nested form and
the Administrator's three-scope form -- call exactly these, so the two
can never drift on what an announcement title or body *is*, and neither
can a future third caller.

**Announcement text is plain text.** Not HTML, not Markdown, not a rich
text document. Nothing here strips tags, sanitises markup or "cleans"
anything: if an author types ``<b>`` it is stored as the five characters
they typed and rendered as the five characters they typed, because every
template escapes it. There is deliberately no sanitiser to mis-configure
and no ``| safe`` anywhere in M09.

What normalisation does, then, is only what a plain-text field honestly
needs:

- **line endings become ``\\n``** in the body, so the same paragraph
  typed on Windows, macOS and Linux is the same stored string and the
  same character count;
- **control characters are rejected, never dropped** -- with the single
  deliberate exception of ``\\n`` and ``\\t`` in the body, which are real
  formatting a notice may legitimately contain. Silently deleting a NUL
  or an escape sequence would store something the author never wrote and
  never saw;
- **the title is collapsed to one line** -- a heading is not a paragraph,
  and a title carrying a newline would render as one line anyway while
  counting as two;
- **outer whitespace is stripped** from both, so a body of nothing but
  spaces is empty rather than "5,000 characters of nothing".

Length is measured **after** normalisation, against the column's own
width, so what the author is told and what the database can hold are the
same number. An error is returned as a *code*, never as a sentence: the
Teacher form and the Administrator form phrase the same rule for
different audiences, and the wording belongs beside the audience.
"""

from app.models import ANNOUNCEMENT_BODY_MAX_LENGTH, ANNOUNCEMENT_TITLE_MAX_LENGTH

#: Every C0 control character plus DEL. ``\n`` and ``\t`` are removed
#: from the body's forbidden set below; the title forbids all of them,
#: because a heading has no lines and no columns.
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}
_BODY_FORBIDDEN = _CONTROL_CHARS - {"\n", "\t"}

#: Returned instead of a sentence. The caller owns the wording.
MISSING = "missing"
CONTROL = "control"
TOO_LONG = "too_long"


def normalize_title(raw):
    """``(title, error_code)`` for one announcement title.

    Collapses every run of whitespace to a single space and strips the
    ends, so a pasted heading with a stray newline or a double space is
    stored the way it reads. Rejects any control character outright, and
    any title longer than
    :data:`~app.models.ANNOUNCEMENT_TITLE_MAX_LENGTH` characters *after*
    that collapse.
    """
    text = raw or ""
    if any(ch in _CONTROL_CHARS for ch in text):
        return None, CONTROL
    text = " ".join(text.split())
    if not text:
        return None, MISSING
    if len(text) > ANNOUNCEMENT_TITLE_MAX_LENGTH:
        return None, TOO_LONG
    return text, None


def normalize_body(raw):
    """``(body, error_code)`` for one announcement body.

    Normalises ``\\r\\n`` and bare ``\\r`` to ``\\n``, strips the outer
    whitespace, and keeps every interior newline and tab exactly as
    typed -- paragraphs in a notice are content, not formatting to be
    thrown away. Rejects every other control character, and any body
    longer than :data:`~app.models.ANNOUNCEMENT_BODY_MAX_LENGTH`
    characters after normalisation.

    Interior blank lines, interior spacing and interior indentation are
    all preserved; the rendering surfaces wrap the value in a
    ``white-space: pre-wrap`` element, so what is shown is what was
    typed, escaped.
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    if any(ch in _BODY_FORBIDDEN for ch in text):
        return None, CONTROL
    text = text.strip()
    if not text:
        return None, MISSING
    if len(text) > ANNOUNCEMENT_BODY_MAX_LENGTH:
        return None, TOO_LONG
    return text, None
