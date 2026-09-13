"""The single normalisation boundary for Group discussion text
(Phase 4 / M12).

Flask-independent pure functions: no ``request``, no ORM, no template,
no I/O -- the same shape and the same rules as
``app/services/message_text.py``, so the project's plain-text surfaces
cannot drift on what "plain text" means.

**Discussion text is plain text.** Nothing here strips tags or sanitises
markup: ``<script>`` is stored as the characters that were typed and is
rendered escaped, because every discussion template escapes it and none
uses ``| safe``.

- **line endings become ``\\n``** in a body, so the same text typed on any
  platform is the same stored string and the same character count;
- **control characters are rejected, never dropped** -- except ``\\n`` and
  ``\\t`` in a body, which are real formatting;
- **a title is collapsed to one line**, and outer whitespace is stripped
  from both, so a value of nothing but spaces is empty rather than valid.

Length is measured **after** normalisation against the column width. An
error is returned as a *code*; the route owns the wording.
"""

from app.models import DISCUSSION_BODY_MAX_LENGTH, DISCUSSION_TITLE_MAX_LENGTH

_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}
_BODY_FORBIDDEN = _CONTROL_CHARS - {"\n", "\t"}

MISSING = "missing"
CONTROL = "control"
TOO_LONG = "too_long"

#: How much of a topic body a topic list previews.
PREVIEW_LENGTH = 160


def normalize_title(raw):
    """``(title, error_code)`` for one topic title.

    Rejects any control character outright, collapses every whitespace run
    to one space, strips the ends, and rejects a title longer than
    :data:`~app.models.DISCUSSION_TITLE_MAX_LENGTH` after that collapse.
    """
    text = raw if isinstance(raw, str) else ""
    if any(ch in _CONTROL_CHARS for ch in text):
        return None, CONTROL
    text = " ".join(text.split())
    if not text:
        return None, MISSING
    if len(text) > DISCUSSION_TITLE_MAX_LENGTH:
        return None, TOO_LONG
    return text, None


def normalize_body(raw):
    """``(body, error_code)`` for one topic body or reply body.

    Normalises ``\\r\\n`` and bare ``\\r`` to ``\\n``, rejects every control
    character other than ``\\n`` and ``\\t``, strips the outer whitespace,
    keeps interior newlines, blank lines and tabs exactly as typed, and
    rejects a body longer than :data:`~app.models.DISCUSSION_BODY_MAX_LENGTH`
    afterwards.
    """
    text = raw if isinstance(raw, str) else ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if any(ch in _BODY_FORBIDDEN for ch in text):
        return None, CONTROL
    text = text.strip()
    if not text:
        return None, MISSING
    if len(text) > DISCUSSION_BODY_MAX_LENGTH:
        return None, TOO_LONG
    return text, None


def clip(text, limit):
    """Bound `text` to `limit` characters, ending in an ellipsis when cut."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def preview_text(body, limit=PREVIEW_LENGTH):
    """A one-line preview of a stored body: every whitespace run -- line
    breaks included -- collapsed to one space, then clipped. Rendered
    escaped like every other discussion string."""
    return clip(" ".join((body or "").split()), limit)
