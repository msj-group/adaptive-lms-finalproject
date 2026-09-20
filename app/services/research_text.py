"""The single normalisation boundary for research consent text
(Phase 6 / M01).

Flask-independent pure functions: no ``request``, no ORM, no template, no
I/O. The Administrator's consent-document form is the only authoring
surface in M01, and it calls exactly these -- so if a second surface is ever
added, the two cannot drift on what a consent version, title or body *is*.

**Consent text is plain text.** Not HTML, not Markdown, not a rich text
document. Nothing here strips tags, sanitises markup or "cleans" anything:
what an Administrator types is what is stored, what is hashed into the
document's digest, and what every template escapes and renders. There is
deliberately no sanitiser to mis-configure and no ``| safe`` anywhere in
M01 -- which matters more here than anywhere else in the project, because
the digest is supposed to prove that the words a Student read are the words
that were written.

Normalisation therefore does only what a plain-text ethics field honestly
needs:

- **line endings become ``\\n``** in the body, so the same paragraph typed
  on Windows, macOS and Linux hashes to the same digest;
- **control characters are rejected, never dropped** -- except ``\\n`` and
  ``\\t`` in the body, which are real formatting an information sheet
  legitimately contains. Silently deleting a character would store, and
  hash, something nobody wrote;
- **bidirectional controls are rejected** in all three fields. They are
  invisible and can make stored text *display* as something other than what
  it says, which consent text must never do;
- **the version identifier and the title are collapsed to one line**;
- **outer whitespace is stripped**, so a body of nothing but spaces is
  empty rather than "20,000 characters of nothing".

Length is measured **after** normalisation, against each column's own
width. An error comes back as a *code*, never as a sentence: the form owns
the wording.
"""

import unicodedata

from app.models import (
    CONSENT_BODY_MAX_LENGTH,
    CONSENT_TITLE_MAX_LENGTH,
    CONSENT_VERSION_MAX_LENGTH,
)

#: Returned instead of a sentence. The caller owns the wording.
MISSING = "missing"
CONTROL = "control"
TOO_LONG = "too_long"

#: Bidirectional embedding, override and isolate controls, plus the two
#: Unicode line separators. Invisible, and able to make consent text display
#: as something other than what is stored.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
_LINE_SEPARATORS = frozenset("  ")


def _forbidden(ch, allowed=frozenset()):
    """``True`` for a character consent text must not contain: every Unicode
    ``Cc`` control (C0, DEL and C1), every bidi control and the two Unicode
    line separators -- except those in `allowed`."""
    if ch in allowed:
        return False
    return (
        unicodedata.category(ch) == "Cc"
        or ch in _BIDI_CONTROLS
        or ch in _LINE_SEPARATORS
    )


def _single_line(raw, max_length):
    text = raw if isinstance(raw, str) else ""
    if any(_forbidden(ch) for ch in text):
        return None, CONTROL
    text = " ".join(text.split())
    if not text:
        return None, MISSING
    if len(text) > max_length:
        return None, TOO_LONG
    return text, None


def normalize_consent_version(raw):
    """``(version_identifier, error_code)`` for one consent version label.

    A free-form single-line label -- ``v1.0``, ``2026-09 ethics draft`` --
    and deliberately not a parsed number: M01 does not decide what a
    version scheme means, it only requires that two documents never share
    one (``uq_research_consent_documents_version``).
    """
    return _single_line(raw, CONSENT_VERSION_MAX_LENGTH)


def normalize_consent_title(raw):
    """``(title, error_code)`` for one consent document title. Required."""
    return _single_line(raw, CONSENT_TITLE_MAX_LENGTH)


def normalize_consent_body(raw):
    """``(body, error_code)`` for the consent wording itself. Required.

    Line endings become ``\\n``; interior newlines and tabs are kept as
    typed; every other forbidden character is rejected; the outer whitespace
    is stripped. There is no optional-empty case: a consent document with no
    wording is not a consent document.
    """
    text = (raw if isinstance(raw, str) else "").replace("\r\n", "\n").replace("\r", "\n")
    if any(_forbidden(ch, allowed=frozenset("\n\t")) for ch in text):
        return None, CONTROL
    text = text.strip()
    if not text:
        return None, MISSING
    if len(text) > CONSENT_BODY_MAX_LENGTH:
        return None, TOO_LONG
    return text, None
