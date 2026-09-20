"""add research participants and consent

Revision ID: f2a6d1c84b37
Revises: b3d8f1a6c472
Create Date: 2026-09-20

Phase 6 / M01. Exactly three things happen here: the
``research_consent_documents`` table is created, then
``research_participants``, then ``research_consent_events`` -- each with its
CHECK constraints, unique constraints, plain foreign keys and query-driven
indexes.

Nothing else is touched: no other table, column, index or constraint is
altered, no existing row is read or rewritten, and **nothing is seeded**. In
particular no consent document is created. A migration that invented consent
wording would be stating, in the database, that people had been told
something nobody wrote and nobody approved.

The new tables
--------------
``research_consent_documents`` -- ``public_id``; a unique
``version_identifier``; an English ``title``; the ``body`` wording as
``TEXT``; ``body_digest`` (a 64-character SHA-256 hex digest of the version,
title and body); ``status`` (``draft``, ``active`` or ``superseded``);
``current_marker`` (``1`` exactly while ``active``, otherwise NULL);
``created_by_id``; ``activated_at`` / ``activated_by_id``; ``superseded_at``
/ ``superseded_by_id``; whole-second UTC ``created_at`` / ``updated_at``.

``research_participants`` -- ``public_id``; ``student_id`` (unique: one
participant per Student); a unique, server-generated ``participant_code``;
``status`` (``invited``, ``active``, ``declined`` or ``withdrawn``); a
nullable ``consent_document_id``; ``decided_at``; ``withdrawn_at``;
``invited_by_id``; a positive ``version``; whole-second UTC ``created_at`` /
``updated_at``.

``research_consent_events`` -- the append-only consent history:
``participant_id``; ``consent_document_id``; ``action`` (``accepted``,
``declined`` or ``withdrawn``); ``actor_id``; ``consent_version`` and
``consent_digest`` copied at the moment of the decision; ``occurred_at``.

**No column stores, or is shaped to store, tracking data.** There is no IP
address, user-agent string, device or browser fingerprint, keystroke,
screen recording, audio, free-form comment, survey answer, frustration
rating, experiment, session, task or generic event payload -- not here and
not anywhere M01 touches. The consent history records consent decisions and
nothing else.

What each rule is for
---------------------
- ``ck_research_consent_documents_status_valid`` /
  ``ck_research_participants_status_valid`` /
  ``ck_research_consent_events_action_valid`` -- the closed sets, as literal
  ``IN`` lists rather than MySQL ``ENUM`` columns, the project's convention.
- ``uq_research_consent_documents_current`` with
  ``ck_research_consent_documents_current_marker`` -- **the cross-row
  invariant that at most one document is current**, expressed as a database
  constraint rather than promised by the application: the marker is ``1``
  exactly while ``active`` and NULL otherwise, and both MySQL and SQLite
  allow many NULLs in a unique index but only one ``1``. The write path
  still takes the documented lock order and re-proves everything after its
  locks (``app/services/research_transactions.py``); the constraint is the
  final defense, not the workflow.
- ``ck_research_consent_documents_lifecycle_state`` -- a draft was never
  activated or superseded; an active document was activated and not
  superseded; a superseded one was activated and then superseded, no
  earlier.
- ``ck_research_consent_documents_activation_pair`` /
  ``_supersession_pair`` -- an attribution moment and its actor are stored
  together or not at all.
- ``ck_research_consent_documents_digest_format`` /
  ``ck_research_consent_events_digest_format`` -- a 64-character digest.
- ``ck_research_consent_documents_body_present`` /
  ``ck_research_consent_events_version_present`` -- never empty.
- ``ck_research_participants_lifecycle_state`` -- ``invited`` carries no
  decision at all; ``active`` and ``declined`` carry a document and a
  decision moment and no withdrawal; ``withdrawn`` carries all three, with
  the withdrawal moment equal to the decision moment.
- ``ck_research_participants_code_format`` -- the code's exact stored
  length, so a truncated or empty code cannot exist.
- ``ck_research_participants_version_positive``.
- ``uq_research_participants_student_id`` -- one participant per Student.
- ``uq_research_participants_code`` -- codes are unique.
- The two ``*_timestamps_ordered`` CHECKs.

Conditions a CHECK cannot express are stated here rather than hidden: that
``student_id`` names an **active Student** (a foreign key into ``users``
proves the row exists, never its role or status), that an attributed account
is an active Administrator, that an activated document's wording never
changes, that a participant's stored status matches its latest consent
event, and that only the linked Student may record a decision. The
application proves each against locked rows, and the ORM guards in
``app/models/research_*.py`` refuse the flush outright.

Indexes, one per real query path
--------------------------------
- ``ix_research_consent_documents_status_id`` (``status``, ``id``) -- the
  Administrator document list and the "which document is current" read.
- ``ix_research_consent_documents_created_by_id``,
  ``_activated_by_id``, ``_superseded_by_id`` -- the three ``users``
  foreign keys.
- ``ix_research_participants_status_id`` (``status``, ``id``) -- both
  participant lists and the dashboard counts.
- ``ix_research_participants_invited_by_id`` and
  ``ix_research_participants_consent_document_id`` -- the two foreign keys
  no unique constraint already covers.
- ``ix_research_consent_events_participant_id_id`` (``participant_id``,
  ``id``) -- one participant's history, and the ``participant_id`` foreign
  key.
- ``ix_research_consent_events_consent_document_id`` and
  ``ix_research_consent_events_actor_id`` -- the other two foreign keys.

**No MySQL execution plan has been measured for these tables.**

Every foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE``
action: no research row is ever hard-deleted, and no account or document
change may cascade away a participant or a consent record. No
``mysql_engine`` / ``mysql_charset`` argument is declared, so all three
tables inherit the server's defaults like every earlier table.

The downgrade **refuses while any research row exists**, before touching
anything: the previous schema cannot represent a consent decision, and this
revision never destroys one. With the tables empty it drops them child
before parent, one statement each. Indexes are not dropped first: several
lead with a foreign-key column, which MySQL refuses to drop while the
constraint exists (errno 1553), and ``DROP TABLE`` removes them anyway.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2a6d1c84b37'
down_revision = 'b3d8f1a6c472'
branch_labels = None
depends_on = None


#: Every M01 row, counted in one statement. The downgrade refuses if this is
#: anything but zero.
_M01_ROWS_SQL = (
    "SELECT (SELECT COUNT(*) FROM research_consent_events)"
    " + (SELECT COUNT(*) FROM research_participants)"
    " + (SELECT COUNT(*) FROM research_consent_documents)"
)


def upgrade():
    op.create_table('research_consent_documents',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('version_identifier', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('body_digest', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('current_marker', sa.SmallInteger(), nullable=True),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('activated_at', sa.DateTime(), nullable=True),
    sa.Column('activated_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('superseded_at', sa.DateTime(), nullable=True),
    sa.Column('superseded_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('draft', 'active', 'superseded')",
        name='ck_research_consent_documents_status_valid',
    ),
    # ``IS NOT NULL`` is not redundant beside ``= 1``: a CHECK that
    # evaluates to NULL passes, and ``NULL = 1`` is NULL, so without it an
    # ``active`` row carrying no marker would be accepted -- and two such
    # rows would satisfy the unique index too, since it admits many NULLs.
    sa.CheckConstraint(
        "(status = 'active' AND current_marker IS NOT NULL AND current_marker = 1)"
        " OR (status <> 'active' AND current_marker IS NULL)",
        name='ck_research_consent_documents_current_marker',
    ),
    sa.CheckConstraint(
        "(activated_at IS NULL AND activated_by_id IS NULL)"
        " OR (activated_at IS NOT NULL AND activated_by_id IS NOT NULL)",
        name='ck_research_consent_documents_activation_pair',
    ),
    sa.CheckConstraint(
        "(superseded_at IS NULL AND superseded_by_id IS NULL)"
        " OR (superseded_at IS NOT NULL AND superseded_by_id IS NOT NULL)",
        name='ck_research_consent_documents_supersession_pair',
    ),
    sa.CheckConstraint(
        "(status = 'draft' AND activated_at IS NULL AND superseded_at IS NULL)"
        " OR (status = 'active' AND activated_at IS NOT NULL AND superseded_at IS NULL)"
        " OR (status = 'superseded' AND activated_at IS NOT NULL"
        " AND superseded_at IS NOT NULL AND superseded_at >= activated_at)",
        name='ck_research_consent_documents_lifecycle_state',
    ),
    sa.CheckConstraint(
        'LENGTH(body_digest) = 64',
        name='ck_research_consent_documents_digest_format',
    ),
    sa.CheckConstraint(
        'LENGTH(body) > 0',
        name='ck_research_consent_documents_body_present',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at"
        " AND (activated_at IS NULL OR (activated_at >= created_at AND updated_at >= activated_at))"
        " AND (superseded_at IS NULL"
        " OR (superseded_at >= created_at AND updated_at >= superseded_at))",
        name='ck_research_consent_documents_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['activated_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['superseded_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_identifier', name='uq_research_consent_documents_version'),
    sa.UniqueConstraint('current_marker', name='uq_research_consent_documents_current'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_research_consent_documents_status_id',
        'research_consent_documents',
        ['status', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_research_consent_documents_created_by_id',
        'research_consent_documents',
        ['created_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_consent_documents_activated_by_id',
        'research_consent_documents',
        ['activated_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_consent_documents_superseded_by_id',
        'research_consent_documents',
        ['superseded_by_id'],
        unique=False,
    )

    op.create_table('research_participants',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('student_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('participant_code', sa.String(length=13), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('consent_document_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('withdrawn_at', sa.DateTime(), nullable=True),
    sa.Column('invited_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('invited', 'active', 'declined', 'withdrawn')",
        name='ck_research_participants_status_valid',
    ),
    sa.CheckConstraint(
        'LENGTH(participant_code) = 13',
        name='ck_research_participants_code_format',
    ),
    sa.CheckConstraint('version > 0', name='ck_research_participants_version_positive'),
    sa.CheckConstraint(
        "(status = 'invited' AND consent_document_id IS NULL AND decided_at IS NULL"
        " AND withdrawn_at IS NULL)"
        " OR (status IN ('active', 'declined') AND consent_document_id IS NOT NULL"
        " AND decided_at IS NOT NULL AND withdrawn_at IS NULL)"
        " OR (status = 'withdrawn' AND consent_document_id IS NOT NULL"
        " AND decided_at IS NOT NULL AND withdrawn_at IS NOT NULL"
        " AND withdrawn_at = decided_at)",
        name='ck_research_participants_lifecycle_state',
    ),
    sa.CheckConstraint(
        "updated_at >= created_at"
        " AND (decided_at IS NULL OR (decided_at >= created_at AND updated_at >= decided_at))",
        name='ck_research_participants_timestamps_ordered',
    ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(
        ['consent_document_id'], ['research_consent_documents.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('student_id', name='uq_research_participants_student_id'),
    sa.UniqueConstraint('participant_code', name='uq_research_participants_code'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_research_participants_status_id',
        'research_participants',
        ['status', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_research_participants_invited_by_id',
        'research_participants',
        ['invited_by_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_participants_consent_document_id',
        'research_participants',
        ['consent_document_id'],
        unique=False,
    )

    op.create_table('research_consent_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('participant_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('consent_document_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('consent_version', sa.String(length=40), nullable=False),
    sa.Column('consent_digest', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "action IN ('accepted', 'declined', 'withdrawn')",
        name='ck_research_consent_events_action_valid',
    ),
    sa.CheckConstraint(
        'LENGTH(consent_digest) = 64',
        name='ck_research_consent_events_digest_format',
    ),
    sa.CheckConstraint(
        'LENGTH(consent_version) > 0',
        name='ck_research_consent_events_version_present',
    ),
    sa.ForeignKeyConstraint(['participant_id'], ['research_participants.id'], ),
    sa.ForeignKeyConstraint(
        ['consent_document_id'], ['research_consent_documents.id'], ),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_research_consent_events_participant_id_id',
        'research_consent_events',
        ['participant_id', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_research_consent_events_consent_document_id',
        'research_consent_events',
        ['consent_document_id'],
        unique=False,
    )
    op.create_index(
        'ix_research_consent_events_actor_id',
        'research_consent_events',
        ['actor_id'],
        unique=False,
    )


def downgrade():
    bind = op.get_bind()
    if not op.get_context().as_sql:
        existing = bind.execute(sa.text(_M01_ROWS_SQL)).scalar()
        if existing:
            raise RuntimeError(
                f"Refusing to downgrade: {existing} research consent document(s), "
                "participant(s) or consent event(s) exist. The previous schema cannot "
                "represent a consent decision, and this revision never deletes one."
            )
    # Child before parent, one statement each. Dropping the indexes first
    # would fail on MySQL with errno 1553 for those leading with a
    # foreign-key column, and DROP TABLE removes them anyway.
    op.drop_table('research_consent_events')
    op.drop_table('research_participants')
    op.drop_table('research_consent_documents')
