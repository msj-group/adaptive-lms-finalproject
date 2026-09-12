"""The one "does this announcement match these words?" predicate
(Phase 4 / M09).

Flask-independent, and deliberately tiny. Two surfaces search
announcements -- the Student's global search
(``app/services/search_queries.py``) and the Teacher's own announcement
feed filter (``app/services/announcement_queries.py``) -- and both call
this, so neither can quietly match on a field the other does not.

The matching rules are M13's, unchanged and re-used rather than
re-invented:

- the query is normalised by
  :func:`app.services.search_terms.normalize_query` (whitespace
  collapsed, truncated, lower-cased, capped at 8 tokens);
- a token matches when ``lower(column) LIKE '%token%'``, with both sides
  lower-cased so SQLite and MySQL agree;
- ``%``, ``_`` and the escape character are neutralised by
  :func:`app.services.search_terms.escape_like`, so a query containing
  them matches them **literally** instead of becoming a wildcard;
- tokens are ANDed (every word must appear somewhere) and fields are
  ORed (a word may appear in the title or in the body).

**Matching is not authorization.** Nothing here mentions a Student, a
Teacher, a status or a scope. Every caller applies
``status == 'published'`` and the reader's visibility clause *as well*,
and the tests prove each one does -- a match clause that quietly carried
its own idea of who may read something would be a second, competing
answer to the only question that matters.
"""

from sqlalchemy import and_, func, or_

from app.models import Announcement
from app.services.search_terms import escape_like

#: The LIKE escape character, matching the one M13 already uses.
ESCAPE = "\\"

#: The two columns an announcement is searched on. The title is what a
#: reader remembers; the body is where the detail they half-remember
#: actually is. Nothing else is searchable: the author's name, the
#: internal ids, the timestamps and the lifecycle columns are not content.
SEARCH_COLUMNS = (Announcement.title, Announcement.body)


def contains(column, token):
    """``lower(column) LIKE '%<token>%'`` with literal ``%`` / ``_`` /
    escape-character handling. `token` is already lower-cased by
    ``normalize_query``."""
    return func.lower(column).like(
        f"%{escape_like(token, ESCAPE)}%", escape=ESCAPE
    )


def match_clause(tokens):
    """AND across query tokens, OR across :data:`SEARCH_COLUMNS`."""
    return and_(
        *[or_(*[contains(column, token) for column in SEARCH_COLUMNS])
          for token in tokens]
    )
