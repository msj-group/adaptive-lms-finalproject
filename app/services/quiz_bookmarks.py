"""Session-lifetime review flags, independent of academic answer storage.

Routes must first authorize the current Student, Quiz, attempt and question.
This signed HttpOnly cookie carries only bounded attempt identifiers/bitmaps;
no prompts, selections, scores or answer keys. It is bound to account/auth
version. A separate cookie avoids answer/collector session responses replacing
bookmark state. It is not a cross-device or permanent academic record.
"""

from uuid import UUID

from flask import current_app, request
from itsdangerous import BadSignature, URLSafeSerializer

from app.models import MAX_QUIZ_QUESTIONS

_COOKIE = "aelms.quiz.review"
_LIMIT = 16


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="quiz-review-v1")


def read_bookmarks(actor_public_id, auth_version):
    actor = [actor_public_id, auth_version]
    empty = {"actor": actor, "attempts": {}}
    try:
        value = _serializer().loads(request.cookies.get(_COOKIE, ""))
        attempts = value["attempts"]
        if value["actor"] != actor or not isinstance(attempts, dict) or len(attempts) > _LIMIT:
            return empty
        for attempt_id, bitmap in attempts.items():
            if str(UUID(attempt_id)) != attempt_id or not isinstance(bitmap, str):
                return empty
            if not bitmap or len(bitmap) > (MAX_QUIZ_QUESTIONS + 3) // 4:
                return empty
            if int(bitmap, 16) < 0 or int(bitmap, 16).bit_length() > MAX_QUIZ_QUESTIONS:
                return empty
        return {"actor": actor, "attempts": dict(attempts)}
    except (BadSignature, KeyError, TypeError, ValueError, AttributeError):
        return empty


def is_bookmarked(state, attempt_public_id, position):
    return bool(int(state["attempts"].get(attempt_public_id, "0"), 16) & (1 << (position - 1)))


def write_bookmark(response, state, attempt_public_id, position, marked):
    """Write only after route authorization; authored positions are frozen in use."""
    if not 1 <= position <= MAX_QUIZ_QUESTIONS:
        raise ValueError("Question position outside the review flag bound")
    attempts = state["attempts"]
    bitmap = int(attempts.pop(attempt_public_id, "0"), 16)
    mask = 1 << (position - 1)
    bitmap = bitmap | mask if marked else bitmap & ~mask
    if bitmap:
        attempts[attempt_public_id] = format(bitmap, "x")
    while len(attempts) > _LIMIT:
        attempts.pop(next(iter(attempts)))
    response.set_cookie(
        _COOKIE, _serializer().dumps(state), path="/student",
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
        httponly=True, samesite="Lax",
    )
    return response
