"""add calendar_events table for center-wide calendar events

Revision ID: e5b83c7d1a49
Revises: c7a91f4b2d68
Create Date: 2026-09-12

Phase 4 / M10. Exactly **one** thing happens here: the
``calendar_events`` table is created, with its CHECK constraints, its one
plain foreign key, its unique ``public_id`` and its three query-driven
indexes.

Nothing else is touched. No other table, column, index or constraint is
altered, no existing row anywhere is read or rewritten, and nothing is
seeded: a center event exists only because an Administrator created one,
so a migration that invented holidays, meetings or exam days for past
terms would be inventing statements nobody made.

**The role-specific calendar itself needs no schema at all.** That is the
central M10 decision, and this revision is what it looks like in the
database: recurring class occurrences stay computed from ``schedules`` by
``app/services/schedule_occurrences.py``, and Assignment, Quiz and
Listening dates stay in those objects' own columns. There is no
materialized-occurrence table, no "calendar row" table, no denormalized
date column added to any existing table and no placeholder for one. A
second copy of a deadline and the deadline itself could disagree, and
then two places would answer the same question differently.

The new table
-------------
``calendar_events`` holds one center-wide event per row: a UUID
``public_id`` (the only identifier that ever appears in a URL or in
HTML), an immutable ``created_by_id`` into ``users``, plain-text
``title`` (150), optional plain-text ``details`` (5,000) and ``location``
(255), a **local civil** ``event_date`` with an optional **local civil**
``start_time`` / ``end_time`` pair, a ``status`` constrained to the two
approved values, a nullable ``cancelled_at``, a positive ``version`` and
the two audit timestamps.

``event_date`` / ``start_time`` / ``end_time`` are ``DATE`` and ``TIME``
columns interpreted through ``APP_TIMEZONE``, exactly as ``schedules``'
weekly wall-clock values already are (the M08 decision). There is
deliberately **no** timezone column and no UTC duplicate of them: an
event at 09:00 is at 09:00 in the center. Only ``cancelled_at``,
``created_at`` and ``updated_at`` are UTC, following the project
convention, and none of them carries fractional-second precision.

What each rule is for
---------------------
- ``ck_calendar_events_status_valid`` -- the closed set as a literal
  ``IN`` list rather than a MySQL ``ENUM`` column, the convention every
  other closed-set column in this project uses, so the SQLite test
  backend enforces it identically and adding a member stays a visible
  schema change. The two values are written out here rather than
  imported from the application enum: a migration must keep describing
  the schema it produced even after that enum moves on again.
- ``ck_calendar_events_time_shape`` -- an event is **either** all-day
  (both times NULL) **or** timed (both NOT NULL, ``start_time <
  end_time``). A half-configured window -- a start with no end -- is the
  one shape that would render while leaving "until when?" undecided, and
  an equal or reversed pair is refused outright. Overnight events are out
  of scope, exactly as overnight Schedule slots are.
- ``ck_calendar_events_cancelled_state`` -- ``cancelled_at`` is NULL
  exactly while the event is ``scheduled``, and NOT NULL exactly while it
  is ``cancelled``. A row can therefore never claim to be standing while
  carrying a cancellation moment, or to be cancelled without one.
- ``ck_calendar_events_version_positive`` -- ``version > 0``.

Conditions a CHECK cannot express are stated here rather than hidden:
that ``created_by_id`` names an Administrator with an active account;
that a ``cancelled`` row is never written again by any code path; and
that a reader is an active Student or Teacher (a Researcher and an
anonymous visitor reach no surface that returns one of these rows). All
of them are enforced by the application against the **locked** rows -- a
foreign key proves a row exists, never its role, its status or its
lifecycle.

Indexes, one per real query path
--------------------------------
- ``ix_calendar_events_status_date_id``
  (``status``, ``event_date``, ``id``) -- the Student and Teacher read,
  which is an equality on ``status`` followed by the ``event_date
  BETWEEN`` range and the deterministic tie-break.
- ``ix_calendar_events_date_id`` (``event_date``, ``id``) -- the
  Administrator calendar, which spans the same range but deliberately
  includes cancelled rows, so ``status`` must not lead.
- ``ix_calendar_events_created_by_date_id``
  (``created_by_id``, ``event_date``, ``id``) -- the leftmost prefix
  InnoDB requires for the ``created_by_id`` foreign key. Declared rather
  than left to InnoDB's implicit index so the schema states what exists;
  no route filters on the creator, and none is planned.

**No MySQL execution plan has been measured for this table.** As with
every earlier Part's indexes, this is a reasoned design pending an
authorized real ``EXPLAIN``.

The one foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE``
action, matching the history-preserving lifecycle of the whole project:
no account lifecycle change may remove a calendar event, and nothing is
ever hard-deleted.

No ``mysql_engine`` / ``mysql_charset`` argument is declared, so the
table inherits the MySQL server's (or the database's) default exactly as
every earlier table in this project does.

The downgrade drops the table, and only the table. Its indexes are not
dropped first: ``ix_calendar_events_created_by_date_id`` leads with a
foreign-key column and MySQL refuses to drop such an index while the
constraint exists (errno 1553), while ``DROP TABLE`` removes a table's
own indexes and foreign keys with it -- the same reasoning M09's
downgrade records. Nothing else is reversed, because nothing else was
changed: no other table, no existing row and no other constraint.
Verified in both directions against the authorized development MySQL
database and against an isolated SQLite probe.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b83c7d1a49'
down_revision = 'c7a91f4b2d68'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('calendar_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('details', sa.String(length=5000), nullable=True),
    sa.Column('event_date', sa.Date(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=True),
    sa.Column('end_time', sa.Time(), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('scheduled', 'cancelled')",
        name='ck_calendar_events_status_valid',
    ),
    sa.CheckConstraint(
        "(start_time IS NULL AND end_time IS NULL)"
        " OR (start_time IS NOT NULL AND end_time IS NOT NULL AND start_time < end_time)",
        name='ck_calendar_events_time_shape',
    ),
    sa.CheckConstraint(
        "(status = 'scheduled' AND cancelled_at IS NULL)"
        " OR (status = 'cancelled' AND cancelled_at IS NOT NULL)",
        name='ck_calendar_events_cancelled_state',
    ),
    sa.CheckConstraint('version > 0', name='ck_calendar_events_version_positive'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_calendar_events_status_date_id',
        'calendar_events',
        ['status', 'event_date', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_calendar_events_date_id',
        'calendar_events',
        ['event_date', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_calendar_events_created_by_date_id',
        'calendar_events',
        ['created_by_id', 'event_date', 'id'],
        unique=False,
    )


def downgrade():
    # The table alone, in one statement. Dropping its indexes explicitly
    # first would fail on MySQL with errno 1553 --
    # ix_calendar_events_created_by_date_id leads with a foreign-key
    # column -- and is unnecessary anyway, since DROP TABLE removes a
    # table's own indexes and constraints. See the module docstring.
    op.drop_table('calendar_events')
