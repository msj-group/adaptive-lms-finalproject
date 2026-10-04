"""Shared primitives of the natural-use research data model (Phase 6
replacement).

- :class:`ResearchDataError` -- a write that would rewrite research history
  or identity. Its own class, so a handler for financial or academic errors
  never absorbs it silently.
- :func:`now_ms` -- the server clock as integer epoch milliseconds, the unit
  every interaction timing column uses (sessions, events, prompts). Integer
  milliseconds keep sub-second ordering identical on MySQL and SQLite without
  a dialect-specific fractional ``DATETIME``.
- :func:`generate_subject_code` -- a pseudonymous research code drawn from
  ``secrets``. The generator takes no argument, so nothing about a Student
  can reach it.
"""

import secrets
import time

from sqlalchemy import BigInteger, Integer

#: The integer primary/foreign key type every table in this project uses.
ID_TYPE = BigInteger().with_variant(Integer, "sqlite")

#: The code prefix: recognisable in a log or a screenshot, uninformative.
SUBJECT_CODE_PREFIX = "RS-"
SUBJECT_CODE_RANDOM_LENGTH = 10
#: Crockford-style: no ``I``, ``L``, ``O``, ``U``, ``0`` or ``1``, so a code
#: read aloud or copied off a screen cannot become a different code.
SUBJECT_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
SUBJECT_CODE_LENGTH = len(SUBJECT_CODE_PREFIX) + SUBJECT_CODE_RANDOM_LENGTH


class ResearchDataError(RuntimeError):
    """A write that would rewrite research identity, frozen configuration or
    recorded observations."""


def now_ms():
    """Server wall clock, integer milliseconds since the Unix epoch (UTC)."""
    return time.time_ns() // 1_000_000


def generate_subject_code():
    """A fresh, unpredictable pseudonymous subject code.

    ``secrets.choice`` -- never ``random`` -- because a guessable code would
    let anyone holding one code enumerate the others.
    """
    body = "".join(
        secrets.choice(SUBJECT_CODE_ALPHABET) for _ in range(SUBJECT_CODE_RANDOM_LENGTH)
    )
    return f"{SUBJECT_CODE_PREFIX}{body}"


def subject_code_is_valid(value):
    return (
        isinstance(value, str)
        and len(value) == SUBJECT_CODE_LENGTH
        and value.startswith(SUBJECT_CODE_PREFIX)
        and all(ch in SUBJECT_CODE_ALPHABET for ch in value[len(SUBJECT_CODE_PREFIX):])
    )


def in_list_sql(column, values):
    """A literal ``IN`` list CHECK, the project's convention for closed sets."""
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def changed_columns(state, columns):
    """The sorted names in `columns` whose value this flush changes."""
    return sorted(
        attr.key
        for attr in state.mapper.column_attrs
        if attr.key in columns and state.attrs[attr.key].history.has_changes()
    )
