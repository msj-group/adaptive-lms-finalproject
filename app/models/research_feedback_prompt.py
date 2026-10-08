"""One sampled, optional frustration feedback prompt (Phase 6 replacement).

**Lifecycle** (stored): ``offered -> displayed -> answered | dismissed``.

- ``offered`` -- the server sampled a moment (``sampling_reason``: a random
  eligible moment or a natural activity ending). Nothing has been shown. The
  browser may defer showing it (timed activity, recording, upload, hidden
  tab); each deferral increments ``deferral_count`` and records the reason.
- ``displayed`` -- the server granted the display **immediately before** the
  browser showed it. The grant moment is ``displayed_at_ms`` and ends the
  observation window: ``window_end_ms = displayed_at_ms`` and
  ``window_start_ms = displayed_at_ms - lookback``. ``observed_ms`` is how much
  of that window the session actually observed continuously -- never more
  than the window, and never pretended. ``display_slot`` numbers the display
  inside its session; ``uq_research_feedback_prompts_session_slot`` is the
  final defense of the per-session budget. ``prompt_day`` is the local day
  (``APP_TIMEZONE``) the per-day budget counts.
- ``answered`` -- the raw 1-5 ``rating`` and any optional causes, at
  ``responded_at_ms``.
- ``dismissed`` -- the Student chose Skip, at ``responded_at_ms``. No rating
  and no cause is stored, and no label is ever inferred.

An offer never shown within the configuration's ``offer_ttl_seconds`` and a
display never answered within ``response_window_seconds`` are **derived**
states, computed from the stored moments; they are not stored as guesses. A
response arriving after the window sets ``late_response`` and stores nothing
else.

The questionnaire happens after ``window_end_ms``, so its own interaction is
outside the window it labels.
"""
from app.models.code_types import CODE_COLLATION

import uuid

from sqlalchemy import event, inspect, text

from app.extensions import db
from app.models.enums import ResearchDeferralReason, ResearchPromptStatus, ResearchSamplingReason
from app.models.research_common import ID_TYPE, ResearchDataError, changed_columns, in_list_sql

PROMPT_STATUSES = tuple(s.value for s in ResearchPromptStatus)
SAMPLING_REASONS = tuple(r.value for r in ResearchSamplingReason)
DEFERRAL_REASONS = tuple(r.value for r in ResearchDeferralReason)

#: The optional causes, in the order the prompt lists them.
CAUSE_COLUMNS = (
    ("interface", "cause_interface", "The interface was hard to use"),
    ("technical", "cause_technical", "A technical delay or problem"),
    ("content", "cause_content", "The learning content was difficult"),
    ("other", "cause_other", "Something else"),
    ("unsure", "cause_unsure", "I'm not sure"),
)
CAUSE_CODES = tuple(code for code, _column, _label in CAUSE_COLUMNS)

#: The full, unchanged scale anchors. The raw value is always stored.
RATING_ANCHORS = (
    (1, "Not frustrated at all"),
    (2, "Slightly frustrated"),
    (3, "Moderately frustrated"),
    (4, "Very frustrated"),
    (5, "Extremely frustrated"),
)

MAX_DEFERRALS_RECORDED = 1000

_NO_CAUSES = " AND ".join(f"{column} = 0" for _code, column, _label in CAUSE_COLUMNS)
_DISPLAY_FIELDS = (
    "displayed_at_ms IS NOT NULL AND display_slot IS NOT NULL AND prompt_day IS NOT NULL"
    " AND window_start_ms IS NOT NULL AND window_end_ms IS NOT NULL AND observed_ms IS NOT NULL"
)
_LIFECYCLE_SQL = (
    "(status = 'offered' AND displayed_at_ms IS NULL AND display_slot IS NULL"
    " AND prompt_day IS NULL AND window_start_ms IS NULL AND window_end_ms IS NULL"
    f" AND observed_ms IS NULL AND responded_at_ms IS NULL AND rating IS NULL AND {_NO_CAUSES})"
    f" OR (status = 'displayed' AND {_DISPLAY_FIELDS} AND responded_at_ms IS NULL"
    f" AND rating IS NULL AND {_NO_CAUSES})"
    f" OR (status = 'answered' AND {_DISPLAY_FIELDS} AND responded_at_ms IS NOT NULL"
    " AND rating IS NOT NULL)"
    f" OR (status = 'dismissed' AND {_DISPLAY_FIELDS} AND responded_at_ms IS NOT NULL"
    f" AND rating IS NULL AND {_NO_CAUSES})"
)
_WINDOW_SQL = (
    "displayed_at_ms IS NULL OR (window_end_ms = displayed_at_ms"
    " AND window_start_ms < window_end_ms AND displayed_at_ms >= offered_at_ms"
    " AND observed_ms >= 0 AND observed_ms <= window_end_ms - window_start_ms"
    " AND (responded_at_ms IS NULL OR responded_at_ms >= displayed_at_ms))"
)


class ResearchFeedbackPrompt(db.Model):
    __tablename__ = "research_feedback_prompts"
    __table_args__ = (
        db.UniqueConstraint(
            "session_id", "display_slot", name="uq_research_feedback_prompts_session_slot"
        ),
        db.CheckConstraint(
            in_list_sql("status", PROMPT_STATUSES), name="ck_research_feedback_prompts_status_valid"
        ),
        db.CheckConstraint(
            in_list_sql("sampling_reason", SAMPLING_REASONS),
            name="ck_research_feedback_prompts_reason_valid",
        ),
        db.CheckConstraint(
            "last_deferral_reason IS NULL OR "
            + in_list_sql("last_deferral_reason", DEFERRAL_REASONS),
            name="ck_research_feedback_prompts_deferral_valid",
        ),
        db.CheckConstraint(
            f"deferral_count >= 0 AND deferral_count <= {MAX_DEFERRALS_RECORDED}"
            " AND ((deferral_count = 0 AND last_deferral_reason IS NULL)"
            " OR (deferral_count > 0 AND last_deferral_reason IS NOT NULL))",
            name="ck_research_feedback_prompts_deferral_count",
        ),
        db.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_research_feedback_prompts_rating_range",
        ),
        db.CheckConstraint(
            "display_slot IS NULL OR display_slot >= 1",
            name="ck_research_feedback_prompts_slot_positive",
        ),
        db.CheckConstraint(_LIFECYCLE_SQL, name="ck_research_feedback_prompts_lifecycle_state"),
        db.CheckConstraint(_WINDOW_SQL, name="ck_research_feedback_prompts_window"),
        db.Index("ix_research_feedback_prompts_session_id_id", "session_id", "id"),
        db.Index("ix_research_feedback_prompts_configuration_id", "configuration_id"),
        db.Index("ix_research_feedback_prompts_day", "prompt_day"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    session_id = db.Column(ID_TYPE, db.ForeignKey("research_sessions.id"), nullable=False)
    configuration_id = db.Column(
        ID_TYPE, db.ForeignKey("research_configurations.id"), nullable=False
    )
    status = db.Column(db.String(16, collation=CODE_COLLATION), nullable=False, default=ResearchPromptStatus.OFFERED.value)
    sampling_reason = db.Column(db.String(16, collation=CODE_COLLATION), nullable=False)
    offered_at_ms = db.Column(db.BigInteger, nullable=False)
    deferral_count = db.Column(db.Integer, nullable=False, default=0)
    last_deferral_reason = db.Column(db.String(24, collation=CODE_COLLATION), nullable=True)
    displayed_at_ms = db.Column(db.BigInteger, nullable=True)
    display_slot = db.Column(db.SmallInteger, nullable=True)
    prompt_day = db.Column(db.Date, nullable=True)
    window_start_ms = db.Column(db.BigInteger, nullable=True)
    window_end_ms = db.Column(db.BigInteger, nullable=True)
    observed_ms = db.Column(db.Integer, nullable=True)
    responded_at_ms = db.Column(db.BigInteger, nullable=True)
    rating = db.Column(db.SmallInteger, nullable=True)
    cause_interface = db.Column(db.Boolean, nullable=False, default=False)
    cause_technical = db.Column(db.Boolean, nullable=False, default=False)
    cause_content = db.Column(db.Boolean, nullable=False, default=False)
    cause_other = db.Column(db.Boolean, nullable=False, default=False)
    cause_unsure = db.Column(db.Boolean, nullable=False, default=False)
    late_response = db.Column(db.Boolean, nullable=False, default=False)


@event.listens_for(ResearchFeedbackPrompt, "before_update")
def _answered_prompts_are_final(_mapper, connection, target):
    """An answered or dismissed prompt keeps its answer: only the
    late-response flag may still be set on it. Read from the stored row, not
    the in-memory attribute history."""
    stored = connection.execute(
        text("SELECT status FROM research_feedback_prompts WHERE id = :id"), {"id": target.id}
    ).scalar()
    if stored not in (ResearchPromptStatus.ANSWERED.value, ResearchPromptStatus.DISMISSED.value):
        return
    state = inspect(target)
    all_columns = frozenset(attr.key for attr in state.mapper.column_attrs)
    if changed_columns(state, all_columns - {"late_response"}):
        raise ResearchDataError("An answered or dismissed prompt is final")
