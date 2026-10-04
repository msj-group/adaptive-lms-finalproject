"""add natural-use research collection

Revision ID: 69c4bae553fe
Revises: b86838ce23db
Create Date: 2026-09-29

Phase 6 replacement, first of two revisions. **Additive only**: it creates the
nine natural-use research tables and carries every legacy refusal or withdrawal
forward as a collection exclusion. It drops, alters and deletes nothing; the
superseded consent and protocol tables are removed by the next revision
(``d574ab56594f``) only after its own preflight.

The new tables, in foreign-key order
------------------------------------
- ``research_subjects`` -- pseudonymous subject code, collection status
  (``included`` / ``excluded``), its truthful basis (``population_rule`` or
  ``operator_reinstatement`` for an included subject; ``external_exclusion``
  or ``legacy_collection_exclusion`` for an excluded one), provenance.
- ``research_subject_links`` -- the only link from a subject to ``users``;
  one each way, both unique.
- ``research_configurations`` -- versioned collection configuration: event
  schema version, period, session and sampling policy, retention in force,
  the operational collecting/paused state; at most one active
  (``uq_research_configurations_current`` over a marker that is ``1`` exactly
  while active, with an explicit ``IS NOT NULL`` in the CHECK). There is no
  per-area scope: an active configuration covers the whole Student platform.
- ``research_sessions`` -- natural-use sessions with observation-run,
  sampling and delivery-quality counters.
- ``research_events`` -- validated client observations and server outcomes,
  with separate client and server clocks; ``event_uid`` unique.
- ``research_feedback_prompts`` -- offers, deferrals, the display and its
  exact window, the raw 1-5 rating and optional causes, dismissal, late flag;
  ``uq_research_feedback_prompts_session_slot`` bounds displays per session.
- ``research_exports`` -- what each export contains, the timezone its dates
  were read in, and the oldest data it holds (for retention).
- ``research_export_archives`` -- the immutable ZIP of one export (one row
  per export, ``LONGBLOB`` on MySQL), with its SHA-256 and size.
- ``research_audit_events`` -- the append-only audit (no Student content).

**No column can hold free text or content**: there is no text, URL, value,
coordinate, payload, user-agent, address or fingerprint column; the archive
holds only the ZIP built from these tables. Every foreign key is plain (no
``ON DELETE`` / ``ON UPDATE``), no ``mysql_engine`` / ``mysql_charset`` is
declared, and every mandatory column is ``NOT NULL`` because a CHECK over NULL
passes.

The legacy transition
---------------------
For every ``research_participants`` row whose status is ``declined`` or
``withdrawn``, this revision creates an ``excluded`` subject with basis
``legacy_collection_exclusion``, its account link, and a ``migration`` audit
row. That preserves the collection exclusion the superseded workflow held,
and the population rule never collects such a Student unless an operator
deliberately lifts it.

It deliberately does **not** carry over ``invited`` or ``active``
participants. An old ``active`` row recorded an acceptance of one specific
consent document of the removed workflow; it is not reinterpreted as anything
in the new method. Such Students are treated like every other Student: the
population rule collects them automatically while they are eligible. No
acceptance, inclusion, consent document or Student action is fabricated.

Online only
-----------
Both directions read rows before they act (the transfer above; the
downgrade's emptiness check), so offline ``--sql`` rendering is **refused**
with a clear error rather than emitting a script that silently skips the
data-dependent step. Run it against a live connection.

Downgrade
---------
Refuses while any row exists in the nine tables, before touching anything,
then drops them child before parent. Indexes are not dropped first: several
lead with a foreign-key column, which MySQL refuses to drop while the
constraint exists (errno 1553), and ``DROP TABLE`` removes them.

**No MySQL execution plan has been measured for these tables.**
"""
import secrets
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '69c4bae553fe'
down_revision = 'b86838ce23db'
branch_labels = None
depends_on = None


NEW_TABLES = (
    'research_subjects', 'research_subject_links', 'research_configurations',
    'research_sessions', 'research_events', 'research_feedback_prompts',
    'research_exports', 'research_export_archives', 'research_audit_events',
)

OFFLINE_REFUSAL = (
    "Revision 69c4bae553fe reads existing rows (the legacy exclusion transfer on upgrade, "
    "the emptiness check on downgrade) and cannot be rendered offline with --sql. Run it "
    "against a live database connection."
)

#: The subject-code rule, copied rather than imported so this revision keeps
#: meaning what it meant when it was written.
_CODE_PREFIX = 'RS-'
_CODE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTVWXYZ'
_CODE_RANDOM_LENGTH = 10


def _new_code(taken):
    while True:
        code = _CODE_PREFIX + ''.join(
            secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_RANDOM_LENGTH)
        )
        if code not in taken:
            taken.add(code)
            return code


def _refuse_offline():
    if op.get_context().as_sql:
        raise RuntimeError(OFFLINE_REFUSAL)


def upgrade():
    _refuse_offline()
    _create_tables()
    _transfer_legacy_exclusions()


def _create_tables():
    """The nine tables in foreign-key order. Pure DDL, kept separate so the
    statements can be reviewed (and rendered) without the data step."""
    op.create_table('research_subjects',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('subject_code', sa.String(length=13), nullable=False),
    sa.Column('collection_status', sa.String(length=16), nullable=False),
    sa.Column('status_basis', sa.String(length=32), nullable=False),
    sa.Column('provenance', sa.String(length=16), nullable=False),
    sa.Column('status_changed_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status_basis IN ('population_rule', 'operator_reinstatement',"
        " 'external_exclusion', 'legacy_collection_exclusion')",
        name='ck_research_subjects_basis_valid',
    ),
    sa.CheckConstraint(
        "LENGTH(subject_code) = 13",
        name='ck_research_subjects_code_format',
    ),
    sa.CheckConstraint(
        "provenance IN ('study', 'demo')",
        name='ck_research_subjects_provenance_valid',
    ),
    sa.CheckConstraint(
        "(collection_status = 'included' AND status_basis IN ('population_rule',"
        " 'operator_reinstatement')) OR (collection_status = 'excluded' AND"
        " status_basis IN ('external_exclusion', 'legacy_collection_exclusion'))",
        name='ck_research_subjects_status_basis_pair',
    ),
    sa.CheckConstraint(
        "collection_status IN ('included', 'excluded')",
        name='ck_research_subjects_status_valid',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at AND status_changed_at >= created_at",
        name='ck_research_subjects_timestamps_ordered',
    ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('subject_code', name='uq_research_subjects_code')
    )
    op.create_index(
        'ix_research_subjects_status_id',
        'research_subjects',
        ['collection_status', 'id'],
        unique=False,
    )

    op.create_table('research_subject_links',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('subject_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['subject_id'], ['research_subjects.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('subject_id', name='uq_research_subject_links_subject_id'),
    sa.UniqueConstraint('user_id', name='uq_research_subject_links_user_id')
    )

    op.create_table('research_configurations',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('event_schema_version', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('current_marker', sa.SmallInteger(), nullable=True),
    sa.Column('is_collecting', sa.Boolean(), nullable=False),
    sa.Column('collection_starts_at', sa.DateTime(), nullable=False),
    sa.Column('collection_ends_at', sa.DateTime(), nullable=False),
    sa.Column('session_inactivity_minutes', sa.Integer(), nullable=False),
    sa.Column('max_prompts_per_session', sa.Integer(), nullable=False),
    sa.Column('max_prompts_per_day', sa.Integer(), nullable=False),
    sa.Column('lookback_seconds', sa.Integer(), nullable=False),
    sa.Column('min_observed_seconds', sa.Integer(), nullable=False),
    sa.Column('random_prompt_permille', sa.Integer(), nullable=False),
    sa.Column('activity_end_prompt_permille', sa.Integer(), nullable=False),
    sa.Column('offer_ttl_seconds', sa.Integer(), nullable=False),
    sa.Column('response_window_seconds', sa.Integer(), nullable=False),
    sa.Column('retention_days', sa.Integer(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('activated_at', sa.DateTime(), nullable=True),
    sa.Column('activated_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('retired_at', sa.DateTime(), nullable=True),
    sa.Column('retired_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "activity_end_prompt_permille >= 0 AND activity_end_prompt_permille <= 1000",
        name='ck_research_configurations_activity_end_prompt_permille_range',
    ),
    sa.CheckConstraint(
        "LENGTH(label) > 0",
        name='ck_research_configurations_label_present',
    ),
    sa.CheckConstraint(
        "(status = 'draft' AND current_marker IS NULL AND is_collecting = 0 AND"
        " activated_at IS NULL AND activated_by_id IS NULL AND retired_at IS NULL AND"
        " retired_by_id IS NULL AND retention_days IS NULL) OR (status = 'active' AND"
        " current_marker IS NOT NULL AND current_marker = 1 AND activated_at IS NOT"
        " NULL AND activated_by_id IS NOT NULL AND retired_at IS NULL AND retired_by_id"
        " IS NULL AND retention_days IS NOT NULL) OR (status = 'retired' AND"
        " current_marker IS NULL AND is_collecting = 0 AND activated_at IS NOT NULL AND"
        " activated_by_id IS NOT NULL AND retired_at IS NOT NULL AND retired_by_id IS"
        " NOT NULL AND retention_days IS NOT NULL AND retired_at >= activated_at)",
        name='ck_research_configurations_lifecycle_state',
    ),
    sa.CheckConstraint(
        "lookback_seconds >= 30 AND lookback_seconds <= 600",
        name='ck_research_configurations_lookback_seconds_range',
    ),
    sa.CheckConstraint(
        "max_prompts_per_day >= 0 AND max_prompts_per_day <= 6",
        name='ck_research_configurations_max_prompts_per_day_range',
    ),
    sa.CheckConstraint(
        "max_prompts_per_session >= 0 AND max_prompts_per_session <= 3",
        name='ck_research_configurations_max_prompts_per_session_range',
    ),
    sa.CheckConstraint(
        "min_observed_seconds >= 15 AND min_observed_seconds <= 600",
        name='ck_research_configurations_min_observed_seconds_range',
    ),
    sa.CheckConstraint(
        "version_number > 0",
        name='ck_research_configurations_number_positive',
    ),
    sa.CheckConstraint(
        "min_observed_seconds <= lookback_seconds",
        name='ck_research_configurations_observed_within_lookback',
    ),
    sa.CheckConstraint(
        "offer_ttl_seconds >= 60 AND offer_ttl_seconds <= 3600",
        name='ck_research_configurations_offer_ttl_seconds_range',
    ),
    sa.CheckConstraint(
        "collection_ends_at > collection_starts_at",
        name='ck_research_configurations_period_ordered',
    ),
    sa.CheckConstraint(
        "random_prompt_permille >= 0 AND random_prompt_permille <= 1000",
        name='ck_research_configurations_random_prompt_permille_range',
    ),
    sa.CheckConstraint(
        "response_window_seconds >= 60 AND response_window_seconds <= 3600",
        name='ck_research_configurations_response_window_seconds_range',
    ),
    sa.CheckConstraint(
        "retention_days IS NULL OR (retention_days >= 1 AND retention_days <= 3650)",
        name='ck_research_configurations_retention_range',
    ),
    sa.CheckConstraint(
        "event_schema_version IN ('natural-use-events.v1')",
        name='ck_research_configurations_schema_valid',
    ),
    sa.CheckConstraint(
        "session_inactivity_minutes >= 5 AND session_inactivity_minutes <= 240",
        name='ck_research_configurations_session_inactivity_minutes_range',
    ),
    sa.CheckConstraint(
        "status IN ('draft', 'active', 'retired')",
        name='ck_research_configurations_status_valid',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at AND (activated_at IS NULL OR activated_at >="
        " created_at)",
        name='ck_research_configurations_timestamps_ordered',
    ),
    sa.CheckConstraint(
        "version > 0",
        name='ck_research_configurations_version_positive',
    ),
    sa.ForeignKeyConstraint(['activated_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['retired_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('current_marker', name='uq_research_configurations_current'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('version_number', name='uq_research_configurations_version_number')
    )
    op.create_index(
        'ix_research_configurations_activated_by_id',
        'research_configurations',
        ['activated_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_configurations_created_by_id',
        'research_configurations',
        ['created_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_configurations_retired_by_id',
        'research_configurations',
        ['retired_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_configurations_status_id',
        'research_configurations',
        ['status', 'id'],
        unique=False,
    )

    op.create_table('research_sessions',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('subject_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('configuration_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('provenance', sa.String(length=16), nullable=False),
    sa.Column('started_at_ms', sa.BigInteger(), nullable=False),
    sa.Column('last_seen_at_ms', sa.BigInteger(), nullable=False),
    sa.Column('ended_at_ms', sa.BigInteger(), nullable=True),
    sa.Column('end_reason', sa.String(length=32), nullable=True),
    sa.Column('observed_since_ms', sa.BigInteger(), nullable=True),
    sa.Column('last_observed_at_ms', sa.BigInteger(), nullable=True),
    sa.Column('activity_end_pending_at_ms', sa.BigInteger(), nullable=True),
    sa.Column('prompt_dismissed_at_ms', sa.BigInteger(), nullable=True),
    sa.Column('batches_received', sa.Integer(), nullable=False),
    sa.Column('events_accepted', sa.Integer(), nullable=False),
    sa.Column('events_duplicate', sa.Integer(), nullable=False),
    sa.Column('events_invalid', sa.Integer(), nullable=False),
    sa.Column('events_late', sa.Integer(), nullable=False),
    sa.Column('events_dropped_client', sa.Integer(), nullable=False),
    sa.Column('sampling_eligible_checks', sa.Integer(), nullable=False),
    sa.Column('sampling_ineligible_checks', sa.Integer(), nullable=False),
    sa.CheckConstraint(
        "batches_received >= 0 AND events_accepted >= 0 AND events_duplicate >= 0 AND"
        " events_invalid >= 0 AND events_late >= 0 AND events_dropped_client >= 0 AND"
        " sampling_eligible_checks >= 0 AND sampling_ineligible_checks >= 0",
        name='ck_research_sessions_counters_non_negative',
    ),
    sa.CheckConstraint(
        "(ended_at_ms IS NULL AND end_reason IS NULL) OR (ended_at_ms IS NOT NULL AND"
        " end_reason IS NOT NULL AND ended_at_ms >= started_at_ms)",
        name='ck_research_sessions_end_pair',
    ),
    sa.CheckConstraint(
        "end_reason IS NULL OR end_reason IN ('logout', 'inactivity',"
        " 'configuration_changed', 'collection_stopped', 'subject_ineligible')",
        name='ck_research_sessions_end_reason_valid',
    ),
    sa.CheckConstraint(
        "(observed_since_ms IS NULL AND last_observed_at_ms IS NULL) OR"
        " (observed_since_ms IS NOT NULL AND last_observed_at_ms IS NOT NULL AND"
        " last_observed_at_ms >= observed_since_ms)",
        name='ck_research_sessions_observation_run',
    ),
    sa.CheckConstraint(
        "provenance IN ('study', 'demo', 'development')",
        name='ck_research_sessions_provenance_valid',
    ),
    sa.CheckConstraint(
        "last_seen_at_ms >= started_at_ms",
        name='ck_research_sessions_times_ordered',
    ),
    sa.ForeignKeyConstraint(['configuration_id'], ['research_configurations.id'], ),
    sa.ForeignKeyConstraint(['subject_id'], ['research_subjects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_research_sessions_configuration_id_id',
        'research_sessions',
        ['configuration_id', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_research_sessions_last_seen',
        'research_sessions',
        ['last_seen_at_ms'],
        unique=False,
    )
    op.create_index(
        'ix_research_sessions_subject_id_id',
        'research_sessions',
        ['subject_id', 'id'],
        unique=False,
    )

    op.create_table('research_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('event_uid', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('event_type', sa.String(length=24), nullable=False),
    sa.Column('page_id', sa.String(length=40), nullable=True),
    sa.Column('element_id', sa.String(length=32), nullable=True),
    sa.Column('detail_code', sa.String(length=24), nullable=True),
    sa.Column('count_value', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('position_value', sa.Integer(), nullable=True),
    sa.Column('page_view_ref', sa.String(length=36), nullable=True),
    sa.Column('tab_ref', sa.String(length=36), nullable=True),
    sa.Column('sequence_number', sa.Integer(), nullable=True),
    sa.Column('client_ts_ms', sa.BigInteger(), nullable=True),
    sa.Column('clock_offset_ms', sa.BigInteger(), nullable=True),
    sa.Column('occurred_at_ms', sa.BigInteger(), nullable=False),
    sa.Column('received_at_ms', sa.BigInteger(), nullable=False),
    sa.CheckConstraint(
        "(source = 'client' AND page_id IS NOT NULL AND page_view_ref IS NOT NULL AND"
        " tab_ref IS NOT NULL AND sequence_number IS NOT NULL AND client_ts_ms IS NOT"
        " NULL AND clock_offset_ms IS NOT NULL) OR (source = 'server' AND page_view_ref"
        " IS NULL AND tab_ref IS NULL AND sequence_number IS NULL AND client_ts_ms IS"
        " NULL AND clock_offset_ms IS NULL)",
        name='ck_research_events_client_fields',
    ),
    sa.CheckConstraint(
        "(source = 'client' AND event_type IN ('page_view', 'page_leave',"
        " 'visibility_hidden', 'visibility_visible', 'heartbeat', 'control_click',"
        " 'repeated_click', 'non_interactive_click', 'input_change', 'form_submit',"
        " 'form_invalid', 'media_event', 'recorder_state', 'recorder_failure')) OR"
        " (source = 'server' AND event_type IN ('lesson_completion', 'search',"
        " 'assignment_submission', 'quiz_start', 'quiz_answer', 'quiz_submission',"
        " 'listening_start', 'listening_answer', 'listening_submission',"
        " 'speaking_submission', 'discussion_reply', 'message_send'))",
        name='ck_research_events_source_type',
    ),
    sa.CheckConstraint(
        "LENGTH(event_uid) = 36",
        name='ck_research_events_uid_format',
    ),
    sa.CheckConstraint(
        "(count_value IS NULL OR count_value >= 0) AND (duration_ms IS NULL OR"
        " duration_ms >= 0) AND (position_value IS NULL OR position_value >= 0) AND"
        " (sequence_number IS NULL OR sequence_number >= 1)",
        name='ck_research_events_values_non_negative',
    ),
    sa.ForeignKeyConstraint(['session_id'], ['research_sessions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_uid', name='uq_research_events_event_uid')
    )
    op.create_index(
        'ix_research_events_session_occurred',
        'research_events',
        ['session_id', 'occurred_at_ms', 'id'],
        unique=False,
    )
    op.create_index('ix_research_events_type', 'research_events', ['event_type'], unique=False)

    op.create_table('research_feedback_prompts',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('configuration_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('sampling_reason', sa.String(length=16), nullable=False),
    sa.Column('offered_at_ms', sa.BigInteger(), nullable=False),
    sa.Column('deferral_count', sa.Integer(), nullable=False),
    sa.Column('last_deferral_reason', sa.String(length=24), nullable=True),
    sa.Column('displayed_at_ms', sa.BigInteger(), nullable=True),
    sa.Column('display_slot', sa.SmallInteger(), nullable=True),
    sa.Column('prompt_day', sa.Date(), nullable=True),
    sa.Column('window_start_ms', sa.BigInteger(), nullable=True),
    sa.Column('window_end_ms', sa.BigInteger(), nullable=True),
    sa.Column('observed_ms', sa.Integer(), nullable=True),
    sa.Column('responded_at_ms', sa.BigInteger(), nullable=True),
    sa.Column('rating', sa.SmallInteger(), nullable=True),
    sa.Column('cause_interface', sa.Boolean(), nullable=False),
    sa.Column('cause_technical', sa.Boolean(), nullable=False),
    sa.Column('cause_content', sa.Boolean(), nullable=False),
    sa.Column('cause_other', sa.Boolean(), nullable=False),
    sa.Column('cause_unsure', sa.Boolean(), nullable=False),
    sa.Column('late_response', sa.Boolean(), nullable=False),
    sa.CheckConstraint(
        "deferral_count >= 0 AND deferral_count <= 1000 AND ((deferral_count = 0 AND"
        " last_deferral_reason IS NULL) OR (deferral_count > 0 AND last_deferral_reason"
        " IS NOT NULL))",
        name='ck_research_feedback_prompts_deferral_count',
    ),
    sa.CheckConstraint(
        "last_deferral_reason IS NULL OR last_deferral_reason IN ('timed_activity',"
        " 'recording', 'uploading', 'hidden_tab')",
        name='ck_research_feedback_prompts_deferral_valid',
    ),
    sa.CheckConstraint(
        "(status = 'offered' AND displayed_at_ms IS NULL AND display_slot IS NULL AND"
        " prompt_day IS NULL AND window_start_ms IS NULL AND window_end_ms IS NULL AND"
        " observed_ms IS NULL AND responded_at_ms IS NULL AND rating IS NULL AND"
        " cause_interface = 0 AND cause_technical = 0 AND cause_content = 0 AND"
        " cause_other = 0 AND cause_unsure = 0) OR (status = 'displayed' AND"
        " displayed_at_ms IS NOT NULL AND display_slot IS NOT NULL AND prompt_day IS"
        " NOT NULL AND window_start_ms IS NOT NULL AND window_end_ms IS NOT NULL AND"
        " observed_ms IS NOT NULL AND responded_at_ms IS NULL AND rating IS NULL AND"
        " cause_interface = 0 AND cause_technical = 0 AND cause_content = 0 AND"
        " cause_other = 0 AND cause_unsure = 0) OR (status = 'answered' AND"
        " displayed_at_ms IS NOT NULL AND display_slot IS NOT NULL AND prompt_day IS"
        " NOT NULL AND window_start_ms IS NOT NULL AND window_end_ms IS NOT NULL AND"
        " observed_ms IS NOT NULL AND responded_at_ms IS NOT NULL AND rating IS NOT"
        " NULL) OR (status = 'dismissed' AND displayed_at_ms IS NOT NULL AND"
        " display_slot IS NOT NULL AND prompt_day IS NOT NULL AND window_start_ms IS"
        " NOT NULL AND window_end_ms IS NOT NULL AND observed_ms IS NOT NULL AND"
        " responded_at_ms IS NOT NULL AND rating IS NULL AND cause_interface = 0 AND"
        " cause_technical = 0 AND cause_content = 0 AND cause_other = 0 AND"
        " cause_unsure = 0)",
        name='ck_research_feedback_prompts_lifecycle_state',
    ),
    sa.CheckConstraint(
        "rating IS NULL OR (rating >= 1 AND rating <= 5)",
        name='ck_research_feedback_prompts_rating_range',
    ),
    sa.CheckConstraint(
        "sampling_reason IN ('random', 'activity_end')",
        name='ck_research_feedback_prompts_reason_valid',
    ),
    sa.CheckConstraint(
        "display_slot IS NULL OR display_slot >= 1",
        name='ck_research_feedback_prompts_slot_positive',
    ),
    sa.CheckConstraint(
        "status IN ('offered', 'displayed', 'answered', 'dismissed')",
        name='ck_research_feedback_prompts_status_valid',
    ),
    sa.CheckConstraint(
        "displayed_at_ms IS NULL OR (window_end_ms = displayed_at_ms AND"
        " window_start_ms < window_end_ms AND displayed_at_ms >= offered_at_ms AND"
        " observed_ms >= 0 AND observed_ms <= window_end_ms - window_start_ms AND"
        " (responded_at_ms IS NULL OR responded_at_ms >= displayed_at_ms))",
        name='ck_research_feedback_prompts_window',
    ),
    sa.ForeignKeyConstraint(['configuration_id'], ['research_configurations.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['research_sessions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('session_id', 'display_slot', name='uq_research_feedback_prompts_session_slot')
    )
    op.create_index(
        'ix_research_feedback_prompts_configuration_id',
        'research_feedback_prompts',
        ['configuration_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_feedback_prompts_day',
        'research_feedback_prompts',
        ['prompt_day'],
        unique=False,
    )
    op.create_index(
        'ix_research_feedback_prompts_session_id_id',
        'research_feedback_prompts',
        ['session_id', 'id'],
        unique=False,
    )

    op.create_table('research_exports',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('export_format', sa.String(length=32), nullable=False),
    sa.Column('event_schema_version', sa.String(length=40), nullable=False),
    sa.Column('configuration_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('period_from', sa.Date(), nullable=True),
    sa.Column('period_to', sa.Date(), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('cutoff_ms', sa.BigInteger(), nullable=False),
    sa.Column('oldest_last_seen_ms', sa.BigInteger(), nullable=True),
    sa.Column('subjects_count', sa.Integer(), nullable=False),
    sa.Column('sessions_count', sa.Integer(), nullable=False),
    sa.Column('events_count', sa.Integer(), nullable=False),
    sa.Column('prompts_count', sa.Integer(), nullable=False),
    sa.Column('manifest_digest', sa.String(length=64), nullable=False),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "subjects_count >= 0 AND sessions_count >= 0 AND events_count >= 0 AND"
        " prompts_count >= 0",
        name='ck_research_exports_counts_non_negative',
    ),
    sa.CheckConstraint(
        "LENGTH(manifest_digest) = 64",
        name='ck_research_exports_digest_format',
    ),
    sa.CheckConstraint(
        "export_format = 'natural-use-export.v1'",
        name='ck_research_exports_format_valid',
    ),
    sa.CheckConstraint(
        "(sessions_count = 0 AND oldest_last_seen_ms IS NULL) OR (sessions_count > 0"
        " AND oldest_last_seen_ms IS NOT NULL)",
        name='ck_research_exports_oldest_present',
    ),
    sa.CheckConstraint(
        "period_from IS NULL OR period_to IS NULL OR period_to >= period_from",
        name='ck_research_exports_period_ordered',
    ),
    sa.CheckConstraint(
        "LENGTH(timezone) > 0",
        name='ck_research_exports_timezone_present',
    ),
    sa.ForeignKeyConstraint(['configuration_id'], ['research_configurations.id'], ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_research_exports_configuration_id',
        'research_exports',
        ['configuration_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_exports_created_by_id',
        'research_exports',
        ['created_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_exports_oldest',
        'research_exports',
        ['oldest_last_seen_ms'],
        unique=False,
    )

    op.create_table('research_export_archives',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('export_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('archive_sha256', sa.String(length=64), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('content', sa.LargeBinary().with_variant(mysql.LONGBLOB(), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "LENGTH(archive_sha256) = 64",
        name='ck_research_export_archives_digest_format',
    ),
    sa.CheckConstraint(
        "byte_size > 0 AND byte_size <= 16777216",
        name='ck_research_export_archives_size_range',
    ),
    sa.ForeignKeyConstraint(['export_id'], ['research_exports.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('export_id', name='uq_research_export_archives_export_id')
    )

    op.create_table('research_audit_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('actor_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('configuration_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('export_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('subject_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('detail_code', sa.String(length=40), nullable=True),
    sa.Column('count_value', sa.Integer(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "action IN ('configuration_created', 'configuration_updated',"
        " 'configuration_activated', 'collection_paused', 'collection_resumed',"
        " 'subject_excluded', 'subject_reinstated', 'subject_marked_demo',"
        " 'export_created', 'export_downloaded', 'retention_purged')",
        name='ck_research_audit_events_action_valid',
    ),
    sa.CheckConstraint(
        "channel IN ('workspace', 'operator', 'migration')",
        name='ck_research_audit_events_channel_valid',
    ),
    sa.CheckConstraint(
        "channel <> 'workspace' OR actor_id IS NOT NULL",
        name='ck_research_audit_events_workspace_actor',
    ),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['configuration_id'], ['research_configurations.id'], ),
    sa.ForeignKeyConstraint(['export_id'], ['research_exports.id'], ),
    sa.ForeignKeyConstraint(['subject_id'], ['research_subjects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_research_audit_events_actor_id',
        'research_audit_events',
        ['actor_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_audit_events_configuration_id',
        'research_audit_events',
        ['configuration_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_audit_events_export_id',
        'research_audit_events',
        ['export_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_audit_events_occurred',
        'research_audit_events',
        ['occurred_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_research_audit_events_subject_id',
        'research_audit_events',
        ['subject_id'],
        unique=False,
    )


def _transfer_legacy_exclusions():
    """Carry each legacy refusal or withdrawal forward as an exclusion.

    Online only (``upgrade`` refuses ``--sql`` first). Runs after every table
    exists, so a failure here leaves the new, empty schema in place and the
    legacy tables untouched.
    """
    bind = op.get_bind()
    if 'research_participants' not in sa.inspect(bind).get_table_names():
        return
    rows = bind.execute(sa.text(
        "SELECT student_id FROM research_participants"
        " WHERE status IN ('declined', 'withdrawn') ORDER BY id"
    )).fetchall()
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    # Typed binds, so each dialect converts the moment itself (a string on
    # SQLite, a native value through PyMySQL) -- never a driver default.
    moment = sa.bindparam('now', value=now, type_=sa.DateTime())
    taken = set()
    for (student_id,) in rows:
        code = _new_code(taken)
        bind.execute(sa.text(
            "INSERT INTO research_subjects (public_id, subject_code, collection_status,"
            " status_basis, provenance, status_changed_at, created_at, updated_at)"
            " VALUES (:public_id, :code, 'excluded', 'legacy_collection_exclusion', 'study',"
            " :now, :now, :now)"
        ).bindparams(moment), {'public_id': str(uuid.uuid4()), 'code': code})
        subject_id = bind.execute(sa.text(
            "SELECT id FROM research_subjects WHERE subject_code = :code"
        ), {'code': code}).scalar_one()
        bind.execute(sa.text(
            "INSERT INTO research_subject_links (subject_id, user_id, created_at)"
            " VALUES (:subject_id, :user_id, :now)"
        ).bindparams(moment), {'subject_id': subject_id, 'user_id': student_id})
        bind.execute(sa.text(
            "INSERT INTO research_audit_events (action, channel, subject_id, detail_code,"
            " occurred_at) VALUES ('subject_excluded', 'migration', :subject_id,"
            " 'legacy_collection_exclusion', :now)"
        ).bindparams(moment), {'subject_id': subject_id})


#: Every row of the nine tables, counted in one statement.
_ROWS_SQL = "SELECT " + " + ".join(f"(SELECT COUNT(*) FROM {table})" for table in NEW_TABLES)


def downgrade():
    _refuse_offline()
    existing = op.get_bind().execute(sa.text(_ROWS_SQL)).scalar()
    if existing:
        raise RuntimeError(
            f"Refusing to downgrade: {existing} natural-use research row(s) exist. The "
            "previous schema cannot represent them, and this revision never deletes them."
        )
    # Child before parent, one statement each.
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
