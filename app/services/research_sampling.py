"""Hybrid feedback sampling and the optional frustration prompt
(Phase 6 replacement).

**When a prompt is offered.** Only at a sampling check -- one per accepted
client batch -- and only when the session is eligible:

- the configuration allows prompts at all;
- the Student has not chosen Skip in this session;
- fewer displays than ``max_prompts_per_session`` in this session and fewer
  than ``max_prompts_per_day`` for this subject on the local day
  (``APP_TIMEZONE``);
- the session has been **continuously observed** for at least
  ``min_observed_seconds`` and is still being observed now.

An eligible check offers a prompt at a **natural activity ending** (an
armed server outcome, with ``activity_end_prompt_permille``) or at a
**random eligible moment** (``random_prompt_permille``). Nothing about errors,
clicks or inactivity enters the decision, so smooth and troubled use are both
sampled; every check increments an eligible or ineligible counter so the
selection is measurable.

**When it is shown.** The browser asks immediately before showing it
(:func:`grant_display`). Eligibility and both budgets are re-proved under the
subject lock -- which is what makes two tabs, or two requests, unable to
spend one budget twice -- and the grant moment is the display moment that
ends the lookback window. Only a granted display consumes the budget.

**What is stored** is the raw 1-5 rating and any optional causes, or a
dismissal, or -- for an answer after the response window -- only a
late-response flag. No label is ever inferred from a dismissal, a
nonresponse or anything else.

**A stored response is final and is never silently replaced.** A second
response to the same prompt is compared with the stored one: the same
choice (the same rating and the same set of causes, or a second Skip) is
:data:`ALREADY` -- a retry whose first answer was lost on the way back --
and any other choice is :data:`DIFFERENT`: nothing changes, and the browser
must say that the earlier response was kept, never that the new one was
recorded. Only the acting Student's own prompt is ever compared.
"""

import secrets
from collections import namedtuple
from datetime import datetime, timezone

from sqlalchemy import func

from app.extensions import db
from app.models import (
    CAUSE_CODES,
    CAUSE_COLUMNS,
    ResearchDeferralReason,
    ResearchFeedbackPrompt,
    ResearchPromptStatus,
    ResearchSamplingReason,
    ResearchSession,
    now_ms,
)
from app.models.research_feedback_prompt import MAX_DEFERRALS_RECORDED
from app.services.schedule_occurrences import to_app_local

#: An armed natural activity ending is considered by the next check only if
#: that check comes within this long; otherwise the moment has passed.
ACTIVITY_END_WINDOW_MS = 5 * 60 * 1000

_OFFERED = ResearchPromptStatus.OFFERED.value
_DISPLAYED = ResearchPromptStatus.DISPLAYED.value
_ANSWERED = ResearchPromptStatus.ANSWERED.value
_DISMISSED = ResearchPromptStatus.DISMISSED.value
DEFERRAL_REASONS = tuple(reason.value for reason in ResearchDeferralReason)

# Outcome codes.
GRANTED = "granted"
DEFERRED = "deferred"
ANSWERED = "answered"
DISMISSED = "dismissed"
ALREADY = "already"
DIFFERENT = "different"
LATE = "late"
NOT_FOUND = "not_found"
UNAVAILABLE = "unavailable"
NOT_COLLECTING = "not_collecting"

Answer = namedtuple("Answer", "dismissed rating causes")


def _draw():
    """A uniform integer in ``[0, 1000)`` from the system CSPRNG. Isolated so
    tests can make sampling deterministic."""
    return secrets.randbelow(1000)


def local_day(moment_ms, tz_name):
    moment = datetime.fromtimestamp(moment_ms / 1000, tz=timezone.utc)
    return to_app_local(tz_name, moment).date()


def live_prompt(session, configuration, moment):
    """The session's offered, unexpired prompt, if any."""
    ttl_ms = configuration.offer_ttl_seconds * 1000
    return (
        db.session.query(ResearchFeedbackPrompt)
        .filter(
            ResearchFeedbackPrompt.session_id == session.id,
            ResearchFeedbackPrompt.status == _OFFERED,
            ResearchFeedbackPrompt.offered_at_ms >= moment - ttl_ms,
        )
        .order_by(ResearchFeedbackPrompt.id.desc())
        .first()
    )


def displays_in_session(session_id):
    return int(
        db.session.query(func.count(ResearchFeedbackPrompt.id))
        .filter(
            ResearchFeedbackPrompt.session_id == session_id,
            ResearchFeedbackPrompt.displayed_at_ms.isnot(None),
        )
        .scalar()
        or 0
    )


def displays_on_day(subject_id, day):
    return int(
        db.session.query(func.count(ResearchFeedbackPrompt.id))
        .join(ResearchSession, ResearchSession.id == ResearchFeedbackPrompt.session_id)
        .filter(
            ResearchSession.subject_id == subject_id,
            ResearchFeedbackPrompt.prompt_day == day,
            ResearchFeedbackPrompt.displayed_at_ms.isnot(None),
        )
        .scalar()
        or 0
    )


def observation_ok(session, configuration, moment):
    from app.services.research_collection import OBSERVATION_GAP_MS

    since, last = session.observed_since_ms, session.last_observed_at_ms
    return (
        since is not None
        and since <= moment - configuration.min_observed_seconds * 1000
        and moment - last <= OBSERVATION_GAP_MS
    )


def ineligibility(chain, session, moment, tz_name):
    """``None`` when the session may receive (or display) a prompt now,
    otherwise a short reason code."""
    configuration = chain.configuration
    if configuration.max_prompts_per_session < 1 or configuration.max_prompts_per_day < 1:
        return "disabled"
    if session.prompt_dismissed_at_ms is not None:
        return "dismissed"
    if displays_in_session(session.id) >= configuration.max_prompts_per_session:
        return "session_budget"
    if displays_on_day(chain.subject.id, local_day(moment, tz_name)) >= \
            configuration.max_prompts_per_day:
        return "daily_budget"
    if not observation_ok(session, configuration, moment):
        return "observation"
    return None


def consider_offer(chain, session, moment, tz_name):
    """One sampling check inside the ingestion transaction. Returns the live
    offered prompt (existing or new) or ``None``. The caller commits."""
    configuration = chain.configuration
    pending = session.activity_end_pending_at_ms
    session.activity_end_pending_at_ms = None

    live = live_prompt(session, configuration, moment)
    if live is not None:
        return live
    if ineligibility(chain, session, moment, tz_name) is not None:
        session.sampling_ineligible_checks += 1
        return None
    session.sampling_eligible_checks += 1

    reason = None
    if pending is not None and moment - pending <= ACTIVITY_END_WINDOW_MS \
            and _draw() < configuration.activity_end_prompt_permille:
        reason = ResearchSamplingReason.ACTIVITY_END.value
    elif _draw() < configuration.random_prompt_permille:
        reason = ResearchSamplingReason.RANDOM.value
    if reason is None:
        return None
    prompt = ResearchFeedbackPrompt(
        session_id=session.id,
        configuration_id=configuration.id,
        status=_OFFERED,
        sampling_reason=reason,
        offered_at_ms=moment,
    )
    db.session.add(prompt)
    db.session.flush()
    return prompt


# ---------------------------------------------------------------------------
# Browser actions on one prompt
# ---------------------------------------------------------------------------


def _lock_prompt(public_id):
    return (
        db.session.query(ResearchFeedbackPrompt)
        .filter(ResearchFeedbackPrompt.public_id == public_id)
        .with_for_update()
        .first()
    )


def _owned(chain, session_ref, prompt_public_id):
    """``(session, prompt)`` when both belong to the acting subject and to
    each other, else ``(None, None)``. Locked session before prompt."""
    from app.services.research_collection import _lock_session
    from app.services.research_event_validation import is_uuid

    if not (is_uuid(session_ref) and is_uuid(prompt_public_id)):
        return None, None
    session = _lock_session(session_ref)
    if session is None or session.subject_id != chain.subject.id:
        return None, None
    prompt = _lock_prompt(prompt_public_id)
    if prompt is None or prompt.session_id != session.id:
        return None, None
    return session, prompt


def _with_chain(user_id, moment):
    from app.services.research_collection import lock_collection_chain

    return lock_collection_chain(user_id, moment, provision=False)


def grant_display(user_id, session_ref, prompt_public_id, tz_name, moment=None):
    """Grant the display of an offered prompt, or refuse. ``(status, reason)``."""
    moment = now_ms() if moment is None else moment
    chain, refused = _with_chain(user_id, moment)
    if refused:
        return NOT_COLLECTING, None
    session, prompt = _owned(chain, session_ref, prompt_public_id)
    if prompt is None:
        db.session.rollback()
        return NOT_FOUND, None
    configuration = chain.configuration
    if prompt.status != _OFFERED or not session.is_open:
        db.session.rollback()
        return UNAVAILABLE, "not_offered"
    if prompt.configuration_id != configuration.id or \
            moment - prompt.offered_at_ms > configuration.offer_ttl_seconds * 1000:
        db.session.rollback()
        return UNAVAILABLE, "expired"
    reason = ineligibility(chain, session, moment, tz_name)
    if reason is not None:
        db.session.rollback()
        return UNAVAILABLE, reason

    # Every value is computed first: a query issued between two assignments
    # would autoflush a half-displayed prompt, which the lifecycle CHECK
    # (rightly) refuses.
    window_start = moment - configuration.lookback_seconds * 1000
    slot = displays_in_session(session.id) + 1
    day = local_day(moment, tz_name)
    observed = moment - max(window_start, session.observed_since_ms)
    with db.session.no_autoflush:
        prompt.status = _DISPLAYED
        prompt.displayed_at_ms = moment
        prompt.display_slot = slot
        prompt.prompt_day = day
        prompt.window_start_ms = window_start
        prompt.window_end_ms = moment
        prompt.observed_ms = observed
    db.session.commit()
    return GRANTED, None


def defer_display(user_id, session_ref, prompt_public_id, reason, moment=None):
    """Record that the browser deferred an offered prompt, and why."""
    if reason not in DEFERRAL_REASONS:
        return NOT_FOUND
    moment = now_ms() if moment is None else moment
    chain, refused = _with_chain(user_id, moment)
    if refused:
        return NOT_COLLECTING
    _session, prompt = _owned(chain, session_ref, prompt_public_id)
    if prompt is None:
        db.session.rollback()
        return NOT_FOUND
    if prompt.status != _OFFERED:
        db.session.rollback()
        return UNAVAILABLE
    prompt.deferral_count = min(prompt.deferral_count + 1, MAX_DEFERRALS_RECORDED)
    prompt.last_deferral_reason = reason
    db.session.commit()
    return DEFERRED


def parse_answer(body):
    """An :class:`Answer` from a response body, or ``None``.

    Exactly ``{"dismissed": true}``, or ``{"rating": 1..5}`` with an
    optional ``"causes"`` list of distinct declared cause codes. Nothing
    else -- no free text field exists.
    """
    if not isinstance(body, dict):
        return None
    if set(body) == {"dismissed"}:
        return Answer(True, None, ()) if body["dismissed"] is True else None
    if not {"rating"} <= set(body) <= {"rating", "causes"}:
        return None
    rating = body["rating"]
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        return None
    causes = body.get("causes", [])
    if not isinstance(causes, list) or len(causes) > len(CAUSE_CODES):
        return None
    if not all(isinstance(c, str) and c in CAUSE_CODES for c in causes):
        return None
    if len(set(causes)) != len(causes):
        return None
    return Answer(False, rating, tuple(causes))


def same_response(prompt, answer):
    """Whether `answer` is exactly the response `prompt` already stores."""
    if prompt.status == _DISMISSED:
        return answer.dismissed
    if prompt.status != _ANSWERED or answer.dismissed:
        return False
    stored = {code for code, column, _label in CAUSE_COLUMNS if getattr(prompt, column)}
    return prompt.rating == answer.rating and stored == set(answer.causes)


def respond(user_id, session_ref, prompt_public_id, answer, moment=None):
    """Store an answer or a dismissal for a displayed prompt. A prompt that
    already has a response keeps it: :data:`ALREADY` when `answer` is the same
    choice, :data:`DIFFERENT` otherwise."""
    moment = now_ms() if moment is None else moment
    chain, refused = _with_chain(user_id, moment)
    if refused:
        return NOT_COLLECTING
    session, prompt = _owned(chain, session_ref, prompt_public_id)
    if prompt is None:
        db.session.rollback()
        return NOT_FOUND
    if prompt.status in (_ANSWERED, _DISMISSED):
        same = same_response(prompt, answer)
        db.session.rollback()
        return ALREADY if same else DIFFERENT
    if prompt.status != _DISPLAYED:
        db.session.rollback()
        return UNAVAILABLE
    window_ms = chain.configuration.response_window_seconds * 1000
    if moment - prompt.displayed_at_ms > window_ms:
        prompt.late_response = True
        db.session.commit()
        return LATE

    prompt.responded_at_ms = moment
    if answer.dismissed:
        prompt.status = _DISMISSED
        session.prompt_dismissed_at_ms = moment
        db.session.commit()
        return DISMISSED
    prompt.status = _ANSWERED
    prompt.rating = answer.rating
    for code, column, _label in CAUSE_COLUMNS:
        setattr(prompt, column, code in answer.causes)
    db.session.commit()
    return ANSWERED
