"""add experiment protocol catalogue

Revision ID: b86838ce23db
Revises: f2a6d1c84b37
Create Date: 2026-09-27

Phase 6 / M02A. Exactly three things happen here: the
``experiment_definitions`` table is created, then ``experiment_task_sets``,
then ``experiment_tasks`` -- each with its CHECK constraints, unique
constraints, plain foreign keys and query-driven indexes.

Nothing else is touched: no other table, column, index or constraint is
altered, no existing row is read or rewritten, and **nothing is seeded**. In
particular no protocol is created or activated. A migration that shipped a
sample protocol would be putting task wording nobody wrote into the catalogue
a Researcher activates from.

The new tables
--------------
``experiment_definitions`` -- one Researcher-authored protocol version:
``public_id``; a unique upper-case ``version_identifier``; ``title``;
``study_stage`` (only ``version_a_collection``); an optional
``equivalence_rationale``; ``status`` (``draft``, ``active``, ``superseded``
or ``discarded``); ``current_marker`` (``1`` exactly while ``active``);
``content_digest`` (the SHA-256 of the frozen content, set at activation);
``derived_from_id`` (the frozen version a draft was copied from); a positive
``version`` for the whole definition/set/task aggregate; ``created_by_id``;
``activated_*``, ``superseded_*`` and ``discarded_*`` moment/actor pairs;
whole-second UTC ``created_at`` / ``updated_at``.

``experiment_task_sets`` -- ``public_id``; ``definition_id``; a ``set_code``
unique inside its definition; ``title``; a server-owned ``display_order``;
``created_at`` / ``updated_at``.

``experiment_tasks`` -- ``public_id``; ``task_set_id``; ``display_order``;
``task_type`` (``dashboard_navigation``, ``find_lesson``, ``search``,
``quiz_completion`` or ``assignment_submission``); ``title``;
``participant_instructions``; ``expected_goal``; ``difficulty`` (``easy``,
``medium`` or ``hard``); ``recommended_duration_seconds`` (30 to 1,800);
``completion_criterion`` -- the criterion the protocol **intends**, never
verified here; ``created_at`` / ``updated_at``.

**No column references or stores a participant.** There is no participant,
Student, session, event, rating, observation, payload, address, agent or
fingerprint column in any of the three tables, and no foreign key to
``research_participants``. The only ``users`` references are the actor
columns on ``experiment_definitions``.

What each rule is for
---------------------
- The ``*_valid`` CHECKs -- the closed sets, as literal ``IN`` lists rather
  than MySQL ``ENUM`` columns, the project's convention.
- ``ck_experiment_tasks_type_criterion_pair`` -- which completion criterion
  each task type may intend.
- ``uq_experiment_definitions_current`` (``study_stage``,
  ``current_marker``) with ``ck_experiment_definitions_current_marker`` --
  **at most one active version per study stage**: the marker is ``1``
  exactly while ``active`` and NULL otherwise, and a unique index admits
  many NULLs but one ``1`` per stage. The CHECK spells out ``IS NOT NULL``
  because a CHECK that evaluates to NULL passes.
- ``ck_experiment_definitions_lifecycle_state`` -- which moments and which
  digest each status carries; the three ``*_pair`` CHECKs store a moment and
  its actor together.
- The ``*_present`` CHECKs -- no empty mandatory text. Every mandatory
  column is also declared ``NOT NULL``, because a CHECK over NULL passes.
- ``ck_experiment_tasks_duration_range``, the ``*_display_order_non_negative``
  CHECKs, the digest format and the ``*_timestamps_ordered`` CHECKs.
- ``uq_experiment_definitions_version_identifier`` and
  ``uq_experiment_task_sets_definition_code``.

Conditions a CHECK cannot express are stated here rather than hidden: that an
actor is an active Researcher, that a frozen version and its sets and tasks
never change, that a set or task never changes parent, that
``derived_from_id`` names another, frozen version (MySQL refuses a CHECK over
an ``AUTO_INCREMENT`` column), that the digest matches the content, and the
limits of four sets per version and ten tasks per set. The application
proves each against locked rows, and the ORM guards in
``app/models/experiment_*.py`` refuse the flush outright.

Indexes, one per real query path
--------------------------------
- ``ix_experiment_definitions_status_id`` (``status``, ``id``) -- the
  protocol list and the dashboard counts.
- ``ix_experiment_definitions_created_by_id``, ``_activated_by_id``,
  ``_superseded_by_id``, ``_discarded_by_id`` and ``_derived_from_id`` -- the
  foreign keys InnoDB requires an index for.
- ``ix_experiment_task_sets_definition_order_id`` (``definition_id``,
  ``display_order``, ``id``) -- one version's sets in order, and its foreign
  key.
- ``ix_experiment_tasks_set_order_id`` (``task_set_id``, ``display_order``,
  ``id``) -- one set's tasks in order, and its foreign key.

**No MySQL execution plan has been measured for these tables.**

Every foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE``
action. No ``mysql_engine`` / ``mysql_charset`` argument is declared, so all
three tables inherit the server's defaults like every earlier table.

The downgrade **refuses while any protocol row exists**, before touching
anything: the previous schema cannot represent a protocol version, and this
revision never destroys one. With the tables empty it drops them child
before parent, one statement each; ``DROP TABLE`` removes their indexes.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b86838ce23db'
down_revision = 'f2a6d1c84b37'
branch_labels = None
depends_on = None


#: Every M02A row, counted in one statement. The downgrade refuses if this is
#: anything but zero.
_M02A_ROWS_SQL = (
    "SELECT (SELECT COUNT(*) FROM experiment_tasks)"
    " + (SELECT COUNT(*) FROM experiment_task_sets)"
    " + (SELECT COUNT(*) FROM experiment_definitions)"
)


def _id_type():
    return sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade():
    op.create_table('experiment_definitions',
    sa.Column('id', _id_type(), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('version_identifier', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('study_stage', sa.String(length=32), nullable=False),
    sa.Column('equivalence_rationale', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('current_marker', sa.SmallInteger(), nullable=True),
    sa.Column('content_digest', sa.String(length=64), nullable=True),
    sa.Column('derived_from_id', _id_type(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by_id', _id_type(), nullable=False),
    sa.Column('activated_at', sa.DateTime(), nullable=True),
    sa.Column('activated_by_id', _id_type(), nullable=True),
    sa.Column('superseded_at', sa.DateTime(), nullable=True),
    sa.Column('superseded_by_id', _id_type(), nullable=True),
    sa.Column('discarded_at', sa.DateTime(), nullable=True),
    sa.Column('discarded_by_id', _id_type(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('draft', 'active', 'superseded', 'discarded')",
        name='ck_experiment_definitions_status_valid',
    ),
    sa.CheckConstraint(
        "study_stage IN ('version_a_collection')",
        name='ck_experiment_definitions_stage_valid',
    ),
    sa.CheckConstraint('version > 0', name='ck_experiment_definitions_version_positive'),
    sa.CheckConstraint(
        'LENGTH(version_identifier) > 0',
        name='ck_experiment_definitions_version_identifier_present',
    ),
    sa.CheckConstraint('LENGTH(title) > 0', name='ck_experiment_definitions_title_present'),
    sa.CheckConstraint(
        'equivalence_rationale IS NULL OR LENGTH(equivalence_rationale) > 0',
        name='ck_experiment_definitions_rationale_present',
    ),
    # ``IS NOT NULL`` is not redundant beside ``= 1``: a CHECK that
    # evaluates to NULL passes, and ``NULL = 1`` is NULL, so without it an
    # ``active`` row carrying no marker would be accepted -- and two such
    # rows would satisfy the unique index too, since it admits many NULLs.
    sa.CheckConstraint(
        "(status = 'active' AND current_marker IS NOT NULL AND current_marker = 1)"
        " OR (status <> 'active' AND current_marker IS NULL)",
        name='ck_experiment_definitions_current_marker',
    ),
    sa.CheckConstraint(
        "(activated_at IS NULL AND activated_by_id IS NULL)"
        " OR (activated_at IS NOT NULL AND activated_by_id IS NOT NULL)",
        name='ck_experiment_definitions_activation_pair',
    ),
    sa.CheckConstraint(
        "(superseded_at IS NULL AND superseded_by_id IS NULL)"
        " OR (superseded_at IS NOT NULL AND superseded_by_id IS NOT NULL)",
        name='ck_experiment_definitions_supersession_pair',
    ),
    sa.CheckConstraint(
        "(discarded_at IS NULL AND discarded_by_id IS NULL)"
        " OR (discarded_at IS NOT NULL AND discarded_by_id IS NOT NULL)",
        name='ck_experiment_definitions_discard_pair',
    ),
    sa.CheckConstraint(
        "(status = 'draft' AND activated_at IS NULL AND superseded_at IS NULL"
        " AND discarded_at IS NULL AND content_digest IS NULL)"
        " OR (status = 'active' AND activated_at IS NOT NULL AND superseded_at IS NULL"
        " AND discarded_at IS NULL AND content_digest IS NOT NULL)"
        " OR (status = 'superseded' AND activated_at IS NOT NULL"
        " AND superseded_at IS NOT NULL AND superseded_at >= activated_at"
        " AND discarded_at IS NULL AND content_digest IS NOT NULL)"
        " OR (status = 'discarded' AND activated_at IS NULL AND superseded_at IS NULL"
        " AND discarded_at IS NOT NULL AND content_digest IS NULL)",
        name='ck_experiment_definitions_lifecycle_state',
    ),
    sa.CheckConstraint(
        'content_digest IS NULL OR LENGTH(content_digest) = 64',
        name='ck_experiment_definitions_digest_format',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at"
        " AND (activated_at IS NULL OR (activated_at >= created_at AND updated_at >= activated_at))"
        " AND (superseded_at IS NULL"
        " OR (superseded_at >= created_at AND updated_at >= superseded_at))"
        " AND (discarded_at IS NULL"
        " OR (discarded_at >= created_at AND updated_at >= discarded_at))",
        name='ck_experiment_definitions_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['derived_from_id'], ['experiment_definitions.id'], ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['activated_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['superseded_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['discarded_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint(
        'version_identifier', name='uq_experiment_definitions_version_identifier'),
    sa.UniqueConstraint(
        'study_stage', 'current_marker', name='uq_experiment_definitions_current'),
    sa.UniqueConstraint('public_id')
    )
    for name, columns in (
        ('ix_experiment_definitions_status_id', ['status', 'id']),
        ('ix_experiment_definitions_created_by_id', ['created_by_id']),
        ('ix_experiment_definitions_activated_by_id', ['activated_by_id']),
        ('ix_experiment_definitions_superseded_by_id', ['superseded_by_id']),
        ('ix_experiment_definitions_discarded_by_id', ['discarded_by_id']),
        ('ix_experiment_definitions_derived_from_id', ['derived_from_id']),
    ):
        op.create_index(name, 'experiment_definitions', columns, unique=False)

    op.create_table('experiment_task_sets',
    sa.Column('id', _id_type(), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('definition_id', _id_type(), nullable=False),
    sa.Column('set_code', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('LENGTH(set_code) > 0', name='ck_experiment_task_sets_code_present'),
    sa.CheckConstraint('LENGTH(title) > 0', name='ck_experiment_task_sets_title_present'),
    sa.CheckConstraint(
        'display_order >= 0', name='ck_experiment_task_sets_display_order_non_negative'),
    sa.CheckConstraint(
        'updated_at >= created_at', name='ck_experiment_task_sets_timestamps_ordered'),
    sa.ForeignKeyConstraint(['definition_id'], ['experiment_definitions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint(
        'definition_id', 'set_code', name='uq_experiment_task_sets_definition_code'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_experiment_task_sets_definition_order_id',
        'experiment_task_sets',
        ['definition_id', 'display_order', 'id'],
        unique=False,
    )

    op.create_table('experiment_tasks',
    sa.Column('id', _id_type(), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('task_set_id', _id_type(), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('task_type', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('participant_instructions', sa.Text(), nullable=False),
    sa.Column('expected_goal', sa.Text(), nullable=False),
    sa.Column('difficulty', sa.String(length=16), nullable=False),
    sa.Column('recommended_duration_seconds', sa.Integer(), nullable=False),
    sa.Column('completion_criterion', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "task_type IN ('dashboard_navigation', 'find_lesson', 'search', 'quiz_completion',"
        " 'assignment_submission')",
        name='ck_experiment_tasks_type_valid',
    ),
    sa.CheckConstraint(
        "difficulty IN ('easy', 'medium', 'hard')",
        name='ck_experiment_tasks_difficulty_valid',
    ),
    sa.CheckConstraint(
        "completion_criterion IN ('participant_declared', 'lesson_opened',"
        " 'quiz_attempt_submitted', 'assignment_submitted')",
        name='ck_experiment_tasks_criterion_valid',
    ),
    sa.CheckConstraint(
        "(task_type = 'dashboard_navigation' AND completion_criterion IN"
        " ('participant_declared'))"
        " OR (task_type = 'find_lesson' AND completion_criterion IN"
        " ('lesson_opened', 'participant_declared'))"
        " OR (task_type = 'search' AND completion_criterion IN"
        " ('lesson_opened', 'participant_declared'))"
        " OR (task_type = 'quiz_completion' AND completion_criterion IN"
        " ('quiz_attempt_submitted'))"
        " OR (task_type = 'assignment_submission' AND completion_criterion IN"
        " ('assignment_submitted'))",
        name='ck_experiment_tasks_type_criterion_pair',
    ),
    sa.CheckConstraint(
        'recommended_duration_seconds >= 30 AND recommended_duration_seconds <= 1800',
        name='ck_experiment_tasks_duration_range',
    ),
    sa.CheckConstraint('LENGTH(title) > 0', name='ck_experiment_tasks_title_present'),
    sa.CheckConstraint(
        'LENGTH(participant_instructions) > 0',
        name='ck_experiment_tasks_instructions_present',
    ),
    sa.CheckConstraint('LENGTH(expected_goal) > 0', name='ck_experiment_tasks_goal_present'),
    sa.CheckConstraint(
        'display_order >= 0', name='ck_experiment_tasks_display_order_non_negative'),
    sa.CheckConstraint(
        'updated_at >= created_at', name='ck_experiment_tasks_timestamps_ordered'),
    sa.ForeignKeyConstraint(['task_set_id'], ['experiment_task_sets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_experiment_tasks_set_order_id',
        'experiment_tasks',
        ['task_set_id', 'display_order', 'id'],
        unique=False,
    )


def downgrade():
    bind = op.get_bind()
    if not op.get_context().as_sql:
        existing = bind.execute(sa.text(_M02A_ROWS_SQL)).scalar()
        if existing:
            raise RuntimeError(
                f"Refusing to downgrade: {existing} experiment protocol version(s), task "
                "set(s) or task(s) exist. The previous schema cannot represent a protocol "
                "version, and this revision never deletes one."
            )
    # Child before parent, one statement each. Dropping the indexes first
    # would fail on MySQL with errno 1553 for those leading with a
    # foreign-key column, and DROP TABLE removes them anyway.
    op.drop_table('experiment_tasks')
    op.drop_table('experiment_task_sets')
    op.drop_table('experiment_definitions')
