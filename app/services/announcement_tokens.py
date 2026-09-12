"""Signed exact-shape stale-state tokens for the Announcement surfaces
(Phase 4 / M09).

Three purposes -- draft write, publish, withdraw -- each minted under its
own salt, and each salt additionally namespaced by the **surface** that
minted it (``teacher`` or ``admin``). A token minted under any other salt
in this project (every M01 assignment snapshot, M02 submission context,
M03 feedback state, M04 quiz token, M05 listening token, M06 speaking
token, M07 attendance token and all five M08 gradebook tokens) fails
signature verification here even though all of them are signed with the
same application ``SECRET_KEY``; a token minted under one of these salts
but for another purpose fails the purpose check; and a token minted on
the Administrator surface cannot be replayed on the Teacher surface, or
the reverse. The tests prove all three directions.

**A token is authenticated, not encrypted.** Anyone holding one can read
its payload, so nothing is placed in one that a holder should not
already know: only public identifiers, a version, a scope, and a short
canonical description of the target's lifecycle. No title, no body, no
author, no recipient, no internal database id and no count ever goes in.

**Locks and tokens solve different problems, and M09 keeps both.** The
lock chain (``app/services/announcement_transactions.py``) decides
against the rows as they are *now*; a token decides against the state the
form was *opened* on. Neither replaces the other: the locks stop two
co-teachers interleaving a write, and the token turns the loser of that
race into an explicit "reload and review" rejection instead of a silent
overwrite of somebody's text.

What each purpose binds, and why
--------------------------------
- **draft** -- the actor, the announcement's public id, its version, and
  its **scope and target**. The version alone would not be enough: an
  Administrator retargeting a draft from one Course to another changes
  what the form is about, not merely its text, and a form opened against
  the old target must not be able to save into the new one.
- **publish** -- everything the draft token binds, plus the *target's own
  lifecycle state*. Publishing is the one transition whose legality
  depends on something outside the announcement: a Course or a Group (and
  its academic chain) that was operational when the page was rendered may
  have been archived since, and a publish form must not survive that.
- **withdraw** -- the actor, the public id and the version. Withdrawal
  needs no target state at all: it is allowed precisely when the row is
  still ``published``, and a withdrawal must stay possible under an
  archived chain so a notice that should no longer be standing can always
  be taken down.

Every version is compared against the **locked** row, never against a
pre-lock preview -- that is what closes the window between the form's GET
and the write.
"""

from itsdangerous import BadSignature, URLSafeSerializer

from flask import current_app

#: The purposes, and the per-surface salts. The version suffix is part of
#: the salt on purpose: changing the payload shape in a future milestone
#: must invalidate every token in flight rather than silently reinterpret
#: one.
PURPOSES = ("announcement-draft", "announcement-publish", "announcement-withdraw")

SURFACES = ("teacher", "admin")

_SALTS = {
    (surface, purpose): f"{surface}.{purpose}.phase4-m09.v1"
    for surface in SURFACES
    for purpose in PURPOSES
}

#: The exact key set each payload must carry -- no more, no fewer. The
#: check below is exact and typed rather than merely "is a dict".
_FIELDS = {
    "announcement-draft": (
        "purpose",
        "actor_public_id",
        "announcement_public_id",
        "announcement_version",
        "scope",
        "target_public_id",
    ),
    "announcement-publish": (
        "purpose",
        "actor_public_id",
        "announcement_public_id",
        "announcement_version",
        "scope",
        "target_public_id",
        "target_state",
    ),
    "announcement-withdraw": (
        "purpose",
        "actor_public_id",
        "announcement_public_id",
        "announcement_version",
    ),
}

_VERSION_FIELDS = ("announcement_version",)

#: The canonical "this target is fine" lifecycle description for a
#: center-scoped announcement, which has no target to describe.
CENTER_TARGET_STATE = "center"

#: What a group- or course-scoped target's state string is built from,
#: outermost first, so the string reads the way the chain does.
_CHAIN_SEPARATOR = "|"


def target_lifecycle_state(scope, statuses=()):
    """The canonical short string describing a target's current
    lifecycle, for a publish token.

    `statuses` is the chain outside-in -- ``(term, level, course, group)``
    for a group-scoped announcement, ``(level, course)`` for a
    course-scoped one, nothing for a center-scoped one. Joined rather than
    reduced to a single "operational" boolean deliberately: a chain that
    went from *term archived* to *group archived* between the GET and the
    POST is a different situation, and collapsing both to ``False`` would
    let a stale form through unnoticed.
    """
    if scope == "center":
        return CENTER_TARGET_STATE
    return _CHAIN_SEPARATOR.join(str(status) for status in statuses)


def _serializer(surface, purpose):
    return URLSafeSerializer(
        current_app.config["SECRET_KEY"], salt=_SALTS[(surface, purpose)]
    )


def _non_negative_int(value):
    """``True`` for a genuine non-negative ``int``.

    ``bool`` is excluded explicitly: it is an ``int`` subclass in Python,
    and ``True`` must never be accepted as version 1. Zero is allowed only
    because a *create* token carries version 0 to mean "there is no row
    yet"; a real version is always at least 1 and is compared against the
    locked row, so a forged 0 matches nothing.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def make_token(surface, purpose, **payload):
    """Sign one exact-shape M09 token.

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
            if not _non_negative_int(value):
                return None
        elif not isinstance(value, str):
            return None
    return payload


def token_is_stale(surface, token, purpose, **expected):
    """``True`` when `token` does not exactly describe `expected`.

    The expected values must come from the rows this request **locked**,
    never from a pre-lock preview.
    """
    payload = load_token(surface, token, purpose)
    if payload is None:
        return True
    for field, value in expected.items():
        if payload[field] != value:
            return True
    return False
