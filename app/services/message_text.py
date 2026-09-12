"""The single normalisation boundary for private message text
(Phase 4 / M11).

Flask-independent pure functions: no ``request``, no ORM, no template,
no I/O -- the same shape as ``app/services/announcement_text.py``, whose
rules this module follows so the two plain-text surfaces cannot drift on
what "plain text" means.

**Message text is plain text.** Nothing here strips tags or sanitises
markup: ``<script>`` is stored as the characters that were typed and
rendered escaped, because every template escapes it and no messaging
template uses ``| safe``.

- **line endings become ``\\n``** in a body, so the same message typed on
  any platform is the same stored string and the same character count;
- **control characters are rejected, never dropped** -- except ``\\n`` and
  ``\\t`` in a body, which are real formatting;
- **a subject is collapsed to one line**, and outer whitespace is stripped
  from both, so a value of nothing but spaces is empty rather than valid.

Length is measured **after** normalisation against the column width. An
error is returned as a *code*; the route owns the wording.
"""

from app.models import MESSAGE_BODY_MAX_LENGTH, MESSAGE_SUBJECT_MAX_LENGTH

_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}
_BODY_FORBIDDEN = _CONTROL_CHARS - {"\n", "\t"}

MISSING = "missing"
CONTROL = "control"
TOO_LONG = "too_long"

#: The recipient search box is a convenience filter, never an identifier:
#: it is normalised and capped rather than rejected.
RECIPIENT_QUERY_MAX_LENGTH = 64

#: How much of a body an inbox or dashboard row previews.
PREVIEW_LENGTH = 120


def normalize_subject(raw):
    """``(subject, error_code)`` for one thread subject.

    Collapses every whitespace run to one space, strips the ends, rejects
    any control character outright, and rejects a subject longer than
    :data:`~app.models.MESSAGE_SUBJECT_MAX_LENGTH` after that collapse.
    """
    text = raw if isinstance(raw, str) else ""
    if any(ch in _CONTROL_CHARS for ch in text):
        return None, CONTROL
    text = " ".join(text.split())
    if not text:
        return None, MISSING
    if len(text) > MESSAGE_SUBJECT_MAX_LENGTH:
        return None, TOO_LONG
    return text, None


def normalize_body(raw):
    """``(body, error_code)`` for one message body.

    Normalises ``\\r\\n`` and bare ``\\r`` to ``\\n``, strips the outer
    whitespace, keeps interior newlines, blank lines and tabs exactly as
    typed, rejects every other control character, and rejects a body
    longer than :data:`~app.models.MESSAGE_BODY_MAX_LENGTH` afterwards.
    """
    text = raw if isinstance(raw, str) else ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if any(ch in _BODY_FORBIDDEN for ch in text):
        return None, CONTROL
    text = text.strip()
    if not text:
        return None, MISSING
    if len(text) > MESSAGE_BODY_MAX_LENGTH:
        return None, TOO_LONG
    return text, None


def normalize_recipient_query(raw):
    """The recipient search text: control characters removed, whitespace
    collapsed, capped at :data:`RECIPIENT_QUERY_MAX_LENGTH` characters.
    Never raises; an unusable value becomes ``""`` (no filter)."""
    text = raw if isinstance(raw, str) else ""
    text = "".join(" " if ch in _CONTROL_CHARS else ch for ch in text)
    text = " ".join(text.split())
    if len(text) > RECIPIENT_QUERY_MAX_LENGTH:
        text = text[:RECIPIENT_QUERY_MAX_LENGTH].strip()
    return text


def clip(text, limit):
    """Bound `text` to `limit` characters, ending in an ellipsis when cut."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def preview_text(body, limit=PREVIEW_LENGTH):
    """A one-line preview of a stored body: every whitespace run -- line
    breaks included -- collapsed to one space, then clipped. Rendered
    escaped like every other message string."""
    return clip(" ".join((body or "").split()), limit)
