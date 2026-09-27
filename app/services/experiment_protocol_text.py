"""The single normalisation boundary for experiment protocol text
(Phase 6 / M02A).

Flask-independent pure functions: no ``request``, no ORM, no template, no
I/O. The Researcher's protocol forms call exactly these, and the activation
digest is taken over exactly what they return -- so what a Researcher
reviewed is what is stored and what is sealed.

**Protocol text is plain text**, under the same character policy as consent
text (``app/services/research_text.py``, whose character rule is reused
rather than restated): control characters, bidirectional controls and the
Unicode line separators are **rejected, never dropped**; multi-line fields
keep ``\\n`` and ``\\t`` and normalise line endings; single-line fields
collapse whitespace; outer whitespace is stripped; length is measured after
normalisation. Nothing strips markup -- every template escapes.

**Codes are upper-case ASCII.** A protocol version identifier and a task-set
code are folded to upper case and limited to ``A-Z``, ``0-9`` and a few
separators, so the case- and accent-insensitive MySQL collation
(``utf8mb4_0900_ai_ci``) and the case-sensitive SQLite test backend agree on
what "the same code" means.

**Defense in depth against obvious personal data -- not a guarantee.** A
protocol describes tasks; it is not a place for participant data. Every text
field is refused if it contains something shaped like a research participant
code (``RP-`` and ten code characters) or an email address. That catches the
obvious mistakes; it cannot recognise a name, a phone number or a sentence
describing somebody, and the catalogue therefore **does not claim** to be
free of personal information. What it guarantees is structural: no column
references a participant, and nothing here is shown to or collected from a
participant.

An error comes back as a *code*, never as a sentence: the form owns the
wording.
"""

import re

from app.models import (
    EQUIVALENCE_RATIONALE_MAX_LENGTH,
    PARTICIPANT_CODE_ALPHABET,
    PARTICIPANT_CODE_PREFIX,
    PARTICIPANT_CODE_LENGTH,
    PROTOCOL_TITLE_MAX_LENGTH,
    PROTOCOL_VERSION_MAX_LENGTH,
    TASK_GOAL_MAX_LENGTH,
    TASK_INSTRUCTIONS_MAX_LENGTH,
    TASK_SET_CODE_MAX_LENGTH,
    TASK_SET_TITLE_MAX_LENGTH,
    TASK_TITLE_MAX_LENGTH,
)
from app.services.research_text import CONTROL, MISSING, TOO_LONG, _forbidden

#: Returned instead of a sentence, alongside the three shared codes.
FORMAT = "format"
PROHIBITED = "prohibited"

__all__ = [
    "CONTROL",
    "FORMAT",
    "MISSING",
    "PROHIBITED",
    "TOO_LONG",
    "contains_prohibited_pattern",
    "normalize_equivalence_rationale",
    "normalize_expected_goal",
    "normalize_participant_instructions",
    "normalize_protocol_title",
    "normalize_protocol_version",
    "normalize_set_code",
    "normalize_set_title",
    "normalize_task_title",
]

_PARTICIPANT_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    + re.escape(PARTICIPANT_CODE_PREFIX)
    + "[" + PARTICIPANT_CODE_ALPHABET + "]"
    + "{" + str(PARTICIPANT_CODE_LENGTH - len(PARTICIPANT_CODE_PREFIX)) + "}"
    + r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

_VERSION_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]*")
_SET_CODE_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9-]*")


def contains_prohibited_pattern(text):
    """``True`` when `text` holds something shaped like a research
    participant code or an email address."""
    return bool(_PARTICIPANT_CODE_PATTERN.search(text) or _EMAIL_PATTERN.search(text))


def _single_line(raw, max_length):
    text = raw if isinstance(raw, str) else ""
    if any(_forbidden(ch) for ch in text):
        return None, CONTROL
    text = " ".join(text.split())
    if not text:
        return None, MISSING
    if len(text) > max_length:
        return None, TOO_LONG
    if contains_prohibited_pattern(text):
        return None, PROHIBITED
    return text, None


def _multi_line(raw, max_length, required=True):
    text = (raw if isinstance(raw, str) else "").replace("\r\n", "\n").replace("\r", "\n")
    if any(_forbidden(ch, allowed=frozenset("\n\t")) for ch in text):
        return None, CONTROL
    text = text.strip()
    if not text:
        return (None, MISSING) if required else (None, None)
    if len(text) > max_length:
        return None, TOO_LONG
    if contains_prohibited_pattern(text):
        return None, PROHIBITED
    return text, None


def _code(raw, max_length, pattern):
    value, error = _single_line(raw, max_length)
    if error is not None:
        return None, error
    value = value.upper()
    if not pattern.fullmatch(value):
        return None, FORMAT
    if contains_prohibited_pattern(value):
        return None, PROHIBITED
    return value, None


def normalize_protocol_version(raw):
    """``(version_identifier, error)`` -- for example ``VA-2026-01``.
    Upper-cased; ``A-Z``, ``0-9``, ``.``, ``_`` and ``-``; starts with a
    letter or digit."""
    return _code(raw, PROTOCOL_VERSION_MAX_LENGTH, _VERSION_PATTERN)


def normalize_set_code(raw):
    """``(set_code, error)`` -- for example ``SET-A``. Upper-cased; ``A-Z``,
    ``0-9`` and ``-``; starts with a letter or digit."""
    return _code(raw, TASK_SET_CODE_MAX_LENGTH, _SET_CODE_PATTERN)


def normalize_protocol_title(raw):
    return _single_line(raw, PROTOCOL_TITLE_MAX_LENGTH)


def normalize_set_title(raw):
    return _single_line(raw, TASK_SET_TITLE_MAX_LENGTH)


def normalize_task_title(raw):
    return _single_line(raw, TASK_TITLE_MAX_LENGTH)


def normalize_participant_instructions(raw):
    return _multi_line(raw, TASK_INSTRUCTIONS_MAX_LENGTH)


def normalize_expected_goal(raw):
    return _multi_line(raw, TASK_GOAL_MAX_LENGTH)


def normalize_equivalence_rationale(raw):
    """``(rationale_or_None, error)``. Optional while drafting: an empty
    value is ``None`` rather than an empty string, so the database never
    stores ``''``."""
    return _multi_line(raw, EQUIVALENCE_RATIONALE_MAX_LENGTH, required=False)
