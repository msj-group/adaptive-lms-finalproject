"""Pure normalisation / validation helpers for M13 learning-content search.

Flask-independent: no ``request`` / ``flash`` / ORM / template concerns and
no network I/O -- just deterministic string processing shared by

- the Teacher keyword forms (``normalize_search_keywords``), and
- the Student search route + query service (``normalize_query``,
  ``escape_like``, ``normalize_content_type``, ``normalize_material_kind``).

Keeping every boundary rule here means the Teacher write path and the
Student read path can never drift on what a keyword or a query "is".
"""

from dataclasses import dataclass

# --- Teacher keyword policy -------------------------------------------------

MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 50
MAX_CANONICAL_KEYWORDS_LENGTH = 500

#: A keyword entry may not contain any C0 control character or DEL. Comma
#: and newline are separators (consumed before this check), never content.
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}
_KEYWORD_SEPARATORS = (",", "\n", "\r")


class SearchKeywordsError(ValueError):
    """The submitted keyword text failed the M13 keyword policy."""


def normalize_search_keywords(raw):
    """Normalise a raw Teacher keyword submission to the canonical stored
    form -- a ``", "``-separated string -- or ``None`` when it carries no
    usable keyword.

    Accepts comma- or newline-separated input. Each entry is trimmed and
    its internal whitespace collapsed; empty entries are dropped; entries
    are de-duplicated case-insensitively, keeping the first spelling seen.

    Raises :class:`SearchKeywordsError` (with a safe, human-readable
    message) if any entry contains a control character, any entry exceeds
    :data:`MAX_KEYWORD_LENGTH` characters, more than :data:`MAX_KEYWORDS`
    distinct keywords remain, or the canonical string would exceed
    :data:`MAX_CANONICAL_KEYWORDS_LENGTH` characters.
    """
    if raw is None:
        return None

    entries = [raw]
    for sep in _KEYWORD_SEPARATORS:
        entries = [part for chunk in entries for part in chunk.split(sep)]

    seen = set()
    keywords = []
    for entry in entries:
        if any(ch in _CONTROL_CHARS for ch in entry):
            raise SearchKeywordsError("Keywords must not contain control characters.")
        keyword = " ".join(entry.split())
        if not keyword:
            continue
        if len(keyword) > MAX_KEYWORD_LENGTH:
            raise SearchKeywordsError(
                f"Each keyword must be at most {MAX_KEYWORD_LENGTH} characters."
            )
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(keyword)

    if not keywords:
        return None
    if len(keywords) > MAX_KEYWORDS:
        raise SearchKeywordsError(f"Use at most {MAX_KEYWORDS} keywords.")

    canonical = ", ".join(keywords)
    if len(canonical) > MAX_CANONICAL_KEYWORDS_LENGTH:
        raise SearchKeywordsError(
            f"The combined keyword list must be at most "
            f"{MAX_CANONICAL_KEYWORDS_LENGTH} characters."
        )
    return canonical


# --- Student query policy -------------------------------------------------

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 100
MAX_QUERY_TOKENS = 8

#: Phase 4 / M09 adds ``announcement``. It is deliberately last: the
#: first four are learning *content*, and an announcement is a
#: communication about it, so it reads as an addition rather than as a
#: peer of "lesson".
CONTENT_TYPES = ("all", "course", "unit", "lesson", "material", "announcement")
MATERIAL_KIND_FILTERS = ("all", "rich_text", "external_link", "file")


@dataclass(frozen=True)
class QueryNorm:
    """The deterministic result of normalising the raw ``q`` parameter.

    ``text`` is the stripped/collapsed/truncated query string;
    ``tokens`` is the lower-cased, whitespace-split token tuple capped at
    :data:`MAX_QUERY_TOKENS`; ``too_short`` is ``True`` when ``text`` has
    fewer than :data:`MIN_QUERY_LENGTH` characters (the route then shows
    the too-short guidance and executes no search).
    """

    text: str
    tokens: tuple
    too_short: bool

    @property
    def is_searchable(self):
        return bool(self.tokens) and not self.too_short


def normalize_query(raw):
    """Normalise the raw ``q`` search parameter into a :class:`QueryNorm`.

    Strips and collapses all whitespace, truncates to
    :data:`MAX_QUERY_LENGTH` characters, then splits into at most
    :data:`MAX_QUERY_TOKENS` lower-cased tokens. ``%``, ``_`` and the
    escape character are left untouched here -- :func:`escape_like`
    neutralises them at the SQL boundary so they match literally.
    """
    text = " ".join((raw or "").split())
    if len(text) > MAX_QUERY_LENGTH:
        text = text[:MAX_QUERY_LENGTH].strip()
    tokens = tuple(t.lower() for t in text.split()[:MAX_QUERY_TOKENS])
    return QueryNorm(text=text, tokens=tokens, too_short=len(text) < MIN_QUERY_LENGTH)


def escape_like(value, escape_char="\\"):
    """Escape ``value`` for use inside a ``LIKE`` / ``ILIKE`` pattern so
    that ``%``, ``_`` and the escape character itself are matched
    literally. The caller wraps the result in ``%...%`` and passes
    ``escape=escape_char`` to SQLAlchemy's ``like`` / ``ilike``.
    """
    return (
        value.replace(escape_char, escape_char + escape_char)
        .replace("%", escape_char + "%")
        .replace("_", escape_char + "_")
    )


def normalize_content_type(raw):
    """Map the raw ``type`` filter to one of :data:`CONTENT_TYPES`;
    anything unrecognised falls back to ``"all"`` (never an error)."""
    value = (raw or "").strip().lower()
    return value if value in CONTENT_TYPES else "all"


def normalize_material_kind(raw):
    """Map the raw ``kind`` filter to one of :data:`MATERIAL_KIND_FILTERS`;
    anything unrecognised falls back to ``"all"`` (never an error)."""
    value = (raw or "").strip().lower()
    return value if value in MATERIAL_KIND_FILTERS else "all"
