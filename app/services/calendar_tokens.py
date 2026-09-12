"""Signed exact-shape stale-state tokens for the Administrator calendar
surface (Phase 4 / M10).

Three purposes -- create, edit, cancel -- each minted under its **own**
salt, and each salt additionally namespaced by the surface that minted
it. A token minted under any other salt in this project (every M01
assignment snapshot, M02 submission context, M03 feedback state, M04
quiz token, M05 listening token, M06 speaking token, M07 attendance
token, all five M08 gradebook tokens and all three M09 announcement
tokens) fails signature verification here even though all of them are
signed with the same application ``SECRET_KEY``; and a token minted for
one of these three purposes fails the purpose check on either of the
other two. The tests prove both directions.

**A token is authenticated, not encrypted.** Anyone holding one can read
its payload, so nothing is placed in one that a holder should not
already know: only the acting account's public id, the event's public
id, its version and its lifecycle state. **No title, no details, no
location, no date, no time, no creator, no count and no internal
database id ever goes in** -- an event's text is exactly what a stale
form must not be able to carry back, and an internal id is exactly what
never leaves the server.

**Locks and tokens solve different problems, and M10 keeps both.** The
lock chain (``app/services/calendar_transactions.py``) decides against
the rows as they are *now*; a token decides against the state the form
was *opened* on. Neither replaces the other: the locks stop two
Administrators interleaving a write, and the token turns the loser of
that race into an explicit "reload and review" rejection instead of a
silent overwrite of somebody's text.

What each purpose binds, and why
--------------------------------
- **create** -- the actor, and nothing else. There is no row yet, so
  there is no version and no lifecycle to bind, and binding the *values
  the form happens to show* would be binding an unmade choice rather
  than any persisted state. What the Administrator does type is
  validated by the form and then written under the locks, which is where
  that decision belongs. The shape is therefore deliberately different
  from the other two, so even a forged cross-purpose payload cannot be
  the right shape.
- **edit** -- the actor, the event's public id, its ``version`` and its
  ``status``. The version alone would be enough today (cancelling
  increments it), but binding the status as well makes the one rejection
  that matters explicit rather than incidental: a form opened on a
  scheduled event must never be able to save into a cancelled one, which
  is permanently immutable.
- **cancel** -- the same three facts. Cancellation needs no other state:
  it is allowed precisely when the row is still ``scheduled``, and it
  must stay possible even for an event whose date has already passed.

Every version and status is compared against the **locked** row, never
against a pre-lock preview -- that is what closes the window between the
form's GET and the write.
"""

from itsdangerous import BadSignature, URLSafeSerializer

from flask import current_app

#: The three purposes. The milestone suffix is part of the salt on
#: purpose: changing a payload shape in a future milestone must
#: invalidate every token in flight rather than silently reinterpret one.
PURPOSES = ("calendar-event-create", "calendar-event-edit", "calendar-event-cancel")

#: The surfaces that may mint one. Only the Administrator surface writes
#: a CalendarEvent -- there is no Student or Teacher endpoint for one to
#: be aimed at -- but the surface stays part of the salt so a future
#: second surface cannot replay into this one.
SURFACES = ("admin",)

_SALTS = {
    (surface, purpose): f"{surface}.{purpose}.phase4-m10.v1"
    for surface in SURFACES
    for purpose in PURPOSES
}

#: The exact key set each payload must carry -- no more, no fewer. The
#: check below is exact and typed rather than merely "is a dict".
_FIELDS = {
    "calendar-event-create": ("purpose", "actor_public_id"),
    "calendar-event-edit": (
        "purpose",
        "actor_public_id",
        "event_public_id",
        "event_version",
        "event_status",
    ),
    "calendar-event-cancel": (
        "purpose",
        "actor_public_id",
        "event_public_id",
        "event_version",
        "event_status",
    ),
}

_VERSION_FIELDS = ("event_version",)


def _serializer(surface, purpose):
    return URLSafeSerializer(
        current_app.config["SECRET_KEY"], salt=_SALTS[(surface, purpose)]
    )


def _positive_int(value):
    """``True`` for a genuine positive ``int``.

    ``bool`` is excluded explicitly: it is an ``int`` subclass in Python,
    and ``True`` must never be accepted as version 1. A real version is
    always at least 1 (``ck_calendar_events_version_positive``), and it
    is compared against the locked row, so a forged value matches
    nothing.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def make_token(surface, purpose, **payload):
    """Sign one exact-shape M10 token.

    Only ever called with freshly read persisted state, never with
    attempted form values: a fresh token may only pair with fresh state,
    which is precisely the bypass the stale rejection exists to close.
    """
    body = dict(payload, purpose=purpose)
    if set(body) != set(_FIELDS[purpose]):  # pragma: no cover -- programming error
        raise ValueError(f"token payload does not match the {purpose} shape")
    return _serializer(surface, purpose).dumps(body)


def load_token(surface, token, purpose):
    """The token's payload, or ``None`` for a missing, malformed,
    invalidly signed, wrong-surface, wrong-salt, wrong-purpose or
    wrong-shaped one.

    Every ``None`` is treated exactly like an outdated token: rejected,
    never trusted, and never silently upgraded into a claim about a row.
    """
    if not token:
        return None
    try:
        payload = _serializer(surface, purpose).loads(token)
    except BadSignature:
        return None
    fields = _FIELDS[purpose]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return None
    if payload["purpose"] != purpose:
        return None
    for field in fields:
        value = payload[field]
        if field in _VERSION_FIELDS:
            if not _positive_int(value):
                return None
        elif not isinstance(value, str):
            return None
    return payload


def token_is_stale(surface, token, purpose, **expected):
    """``True`` when `token` does not exactly describe `expected`.

    The expected values must come from the row this request **locked**,
    never from a pre-lock preview.
    """
    payload = load_token(surface, token, purpose)
    if payload is None:
        return True
    for field, value in expected.items():
        if payload[field] != value:
            return True
    return False
