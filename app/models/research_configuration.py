"""One versioned natural-use collection configuration (Phase 6 replacement).

A configuration version fixes **how** collection runs: the event schema
version, the collection period, the session inactivity rule, the feedback
sampling policy and the retention in force. It always covers **every**
Student page and outcome in the event dictionary: there is deliberately no
per-area switch that could silently narrow collection. It is **not** a consent document and **not** an ethics approval, and no
page presents it as either.

**Lifecycle.** ``draft -> active -> retired``. A Researcher edits a draft;
activation freezes every policy column forever and retires whatever was
active. At most one version is active: ``current_marker`` is ``1`` exactly
while ``active`` and NULL otherwise, and ``uq_research_configurations_current``
is unique over it (both backends admit many NULLs and one ``1``). The CHECK
spells out ``current_marker IS NOT NULL`` because a CHECK that evaluates to
NULL passes.

**Operational state.** ``is_collecting`` is the one thing an active version
may still change: a Researcher pauses and resumes collection without creating
a new version, and every change is audited. A draft or retired version never
collects.

**Retention.** ``retention_days`` records the deployment's
``RESEARCH_RETENTION_DAYS`` at activation; a version cannot be activated while
the deployment supplies none.

Guards read the **stored** status through the flushing connection, so an
expired-then-assigned attribute cannot slip a frozen column past them.
"""
from app.models.code_types import CODE_COLLATION

import uuid

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import ResearchConfigurationStatus
from app.models.research_common import ID_TYPE, ResearchDataError, changed_columns, in_list_sql
from app.models.submission_feedback import whole_second_utc
from app.services.research_event_dictionary import SUPPORTED_EVENT_SCHEMA_VERSIONS

CONFIGURATION_LABEL_MAX_LENGTH = 80
CONFIGURATION_CURRENT_MARKER = 1

_DRAFT = ResearchConfigurationStatus.DRAFT.value
_ACTIVE = ResearchConfigurationStatus.ACTIVE.value
_RETIRED = ResearchConfigurationStatus.RETIRED.value
CONFIGURATION_STATUSES = (_DRAFT, _ACTIVE, _RETIRED)

#: ``(column, minimum, maximum, pilot default)`` for every bounded policy
#: integer. The pilot defaults are starting values to be versioned and
#: revisited, not validated scientific constants.
POLICY_BOUNDS = {
    "session_inactivity_minutes": (5, 240, 30),
    "max_prompts_per_session": (0, 3, 1),
    "max_prompts_per_day": (0, 6, 2),
    "lookback_seconds": (30, 600, 120),
    "min_observed_seconds": (15, 600, 120),
    "random_prompt_permille": (0, 1000, 50),
    "activity_end_prompt_permille": (0, 1000, 500),
    "offer_ttl_seconds": (60, 3600, 600),
    "response_window_seconds": (60, 3600, 600),
}

#: The only columns an ``active`` version may change: what activation of a
#: successor, a pause or a resume moves.
ACTIVE_MUTABLE_COLUMNS = frozenset(
    {"status", "current_marker", "is_collecting", "retired_at", "retired_by_id", "version",
     "updated_at"}
)
IMMUTABLE_COLUMNS = frozenset({"public_id", "version_number", "created_by_id", "created_at"})

_LIFECYCLE_SQL = (
    "(status = 'draft' AND current_marker IS NULL AND is_collecting = 0"
    " AND activated_at IS NULL AND activated_by_id IS NULL AND retired_at IS NULL"
    " AND retired_by_id IS NULL AND retention_days IS NULL)"
    " OR (status = 'active' AND current_marker IS NOT NULL AND current_marker = 1"
    " AND activated_at IS NOT NULL AND activated_by_id IS NOT NULL AND retired_at IS NULL"
    " AND retired_by_id IS NULL AND retention_days IS NOT NULL)"
    " OR (status = 'retired' AND current_marker IS NULL AND is_collecting = 0"
    " AND activated_at IS NOT NULL AND activated_by_id IS NOT NULL"
    " AND retired_at IS NOT NULL AND retired_by_id IS NOT NULL"
    " AND retention_days IS NOT NULL AND retired_at >= activated_at)"
)


def _policy_checks():
    checks = []
    for column, (low, high, _default) in POLICY_BOUNDS.items():
        checks.append(
            db.CheckConstraint(
                f"{column} >= {low} AND {column} <= {high}",
                name=f"ck_research_configurations_{column}_range",
            )
        )
    return checks


class ResearchConfiguration(db.Model):
    __tablename__ = "research_configurations"
    __table_args__ = (
        db.UniqueConstraint("version_number", name="uq_research_configurations_version_number"),
        db.UniqueConstraint("current_marker", name="uq_research_configurations_current"),
        db.CheckConstraint(
            in_list_sql("status", CONFIGURATION_STATUSES),
            name="ck_research_configurations_status_valid",
        ),
        db.CheckConstraint(
            in_list_sql("event_schema_version", SUPPORTED_EVENT_SCHEMA_VERSIONS),
            name="ck_research_configurations_schema_valid",
        ),
        db.CheckConstraint("version_number > 0", name="ck_research_configurations_number_positive"),
        db.CheckConstraint("version > 0", name="ck_research_configurations_version_positive"),
        db.CheckConstraint("LENGTH(label) > 0", name="ck_research_configurations_label_present"),
        db.CheckConstraint(
            "collection_ends_at > collection_starts_at",
            name="ck_research_configurations_period_ordered",
        ),
        db.CheckConstraint(
            "min_observed_seconds <= lookback_seconds",
            name="ck_research_configurations_observed_within_lookback",
        ),
        db.CheckConstraint(
            "retention_days IS NULL OR (retention_days >= 1 AND retention_days <= 3650)",
            name="ck_research_configurations_retention_range",
        ),
        db.CheckConstraint(_LIFECYCLE_SQL, name="ck_research_configurations_lifecycle_state"),
        db.CheckConstraint(
            "updated_at >= created_at"
            " AND (activated_at IS NULL OR activated_at >= created_at)",
            name="ck_research_configurations_timestamps_ordered",
        ),
        *_policy_checks(),
        db.Index("ix_research_configurations_status_id", "status", "id"),
        db.Index("ix_research_configurations_created_by_id", "created_by_id"),
        db.Index("ix_research_configurations_activated_by_id", "activated_by_id"),
        db.Index("ix_research_configurations_retired_by_id", "retired_by_id"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    version_number = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(CONFIGURATION_LABEL_MAX_LENGTH), nullable=False)
    event_schema_version = db.Column(db.String(40, collation=CODE_COLLATION), nullable=False)
    status = db.Column(db.String(16, collation=CODE_COLLATION), nullable=False, default=_DRAFT)
    current_marker = db.Column(db.SmallInteger, nullable=True)
    is_collecting = db.Column(db.Boolean, nullable=False, default=False)
    collection_starts_at = db.Column(db.DateTime, nullable=False)
    collection_ends_at = db.Column(db.DateTime, nullable=False)

    session_inactivity_minutes = db.Column(db.Integer, nullable=False)
    max_prompts_per_session = db.Column(db.Integer, nullable=False)
    max_prompts_per_day = db.Column(db.Integer, nullable=False)
    lookback_seconds = db.Column(db.Integer, nullable=False)
    min_observed_seconds = db.Column(db.Integer, nullable=False)
    random_prompt_permille = db.Column(db.Integer, nullable=False)
    activity_end_prompt_permille = db.Column(db.Integer, nullable=False)
    offer_ttl_seconds = db.Column(db.Integer, nullable=False)
    response_window_seconds = db.Column(db.Integer, nullable=False)
    retention_days = db.Column(db.Integer, nullable=True)

    version = db.Column(db.Integer, nullable=False, default=1)
    created_by_id = db.Column(ID_TYPE, db.ForeignKey("users.id"), nullable=False)
    activated_at = db.Column(db.DateTime, nullable=True)
    activated_by_id = db.Column(ID_TYPE, db.ForeignKey("users.id"), nullable=True)
    retired_at = db.Column(db.DateTime, nullable=True)
    retired_by_id = db.Column(ID_TYPE, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in CONFIGURATION_STATUSES:
            raise ValueError(f"Invalid configuration status: {value}")
        return value

    @validates("event_schema_version")
    def validate_schema(self, _key, value):
        if value not in SUPPORTED_EVENT_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported event schema version: {value}")
        return value

    @property
    def is_draft(self):
        return self.status == _DRAFT

    @property
    def is_active(self):
        return self.status == _ACTIVE


def _stored_status(connection, target):
    return connection.execute(
        text("SELECT status FROM research_configurations WHERE id = :id"), {"id": target.id}
    ).scalar()


@event.listens_for(ResearchConfiguration, "before_update")
def _frozen_configuration(_mapper, connection, target):
    state = inspect(target)
    fixed = changed_columns(state, IMMUTABLE_COLUMNS)
    if fixed:
        raise ResearchDataError("A configuration's " + ", ".join(fixed) + " never changes.")
    stored = _stored_status(connection, target)
    if stored == _DRAFT:
        return
    all_columns = frozenset(attr.key for attr in state.mapper.column_attrs)
    if stored == _ACTIVE:
        frozen = changed_columns(state, all_columns - ACTIVE_MUTABLE_COLUMNS)
    else:
        frozen = changed_columns(state, all_columns)
    if frozen:
        raise ResearchDataError(
            f"A {stored} configuration is frozen: " + ", ".join(frozen) + " cannot change."
        )


@event.listens_for(ResearchConfiguration, "before_delete")
def _refuse_deleting_a_configuration(_mapper, _connection, _target):
    raise ResearchDataError("A collection configuration is never deleted")
