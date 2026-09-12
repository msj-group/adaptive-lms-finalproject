"""add announcements and the announcement_published notification kind

Revision ID: c7a91f4b2d68
Revises: 2f6d1c83ab47
Create Date: 2026-09-12

Phase 4 / M09. Exactly two things happen here:

1. the ``announcements`` table is created, with its CHECK constraints,
   its three plain foreign keys, its unique ``public_id`` and its five
   query-driven indexes; and
2. the existing ``ck_notifications_kind_valid`` CHECK on
   ``notifications`` is **replaced** by one that additionally allows
   ``'announcement_published'``.

Nothing else is touched. No other table, column, index or constraint is
altered, no row anywhere is rewritten, and nothing is seeded: an
announcement exists only because a human wrote one, so a migration that
invented historical notices for past terms would be inventing statements
nobody made.

Why the notification CHECK is *replaced* rather than widened
------------------------------------------------------------
A CHECK constraint has no ``ALTER`` on either backend -- there is no
"add a value" operation, because the constraint is one expression. The
only correct change is therefore "drop the old expression, add the new
one", and the two backends do that very differently:

- **MySQL 8** supports it directly and cheaply::

      ALTER TABLE notifications DROP CHECK ck_notifications_kind_valid;
      ALTER TABLE notifications ADD CONSTRAINT ck_notifications_kind_valid
          CHECK (kind IN (...));

  Both statements are ordinary DDL on an existing table. **No row is
  read, rewritten, or deleted**: the new expression is strictly wider
  than the old one, so every stored notification already satisfies it and
  MySQL's validation pass accepts the table as it stands.

- **SQLite has no such statement at all.** A CHECK constraint can only
  be changed by rebuilding the table, which is exactly what Alembic's
  batch mode does: create a new table with the new definition, copy every
  row across, drop the old table, rename the new one into place. This
  revision drives that with an explicit ``copy_from`` table rather than
  by reflecting the existing one, deliberately -- a reflected SQLite
  table would bring the **old** CHECK back with it, and the rebuild would
  silently reinstate the constraint this revision exists to replace.
  Declaring the table here means the rebuilt ``notifications`` is exactly
  the shape ``app/models/notification.py`` describes, indexes included.

  A rebuild copies rows, so it is the one step in this revision that
  touches existing data. It is a straight ``INSERT ... SELECT`` of every
  column in order; ``tests/test_announcement_migration.py`` executes it
  against a seeded database and asserts every pre-existing notification
  -- including its ``public_id``, ``read_at`` and target -- survives both
  directions unchanged.

**SQLite table-alter behaviour is not assumed to match MySQL's**, which
is the whole reason these two paths are written out separately instead of
one ``batch_alter_table`` being pointed at both. The MySQL path never
rebuilds anything and never copies a row; the SQLite path always does.

The new table
-------------
``announcements`` holds one center-, course- or group-scoped notice per
row: a UUID ``public_id`` (the only identifier that ever appears in a URL
or in HTML), an immutable ``author_id`` into ``users``, a ``scope``
constrained to the three approved values, the **at most one** target
(``course_id`` or ``group_id``) that scope allows, plain-text ``title``
(150) and ``body`` (5,000), a ``status`` constrained to the three
approved values, the two nullable publication timestamps, a positive
``version`` and the two audit timestamps.

What each rule is for
---------------------
- ``ck_announcements_scope_valid`` / ``ck_announcements_status_valid``
  -- the two closed sets as literal ``IN`` lists rather than MySQL
  ``ENUM`` columns, the convention every other closed-set column in this
  project uses, so the SQLite test backend enforces them identically and
  adding a member stays a visible schema change.
- ``ck_announcements_scope_target`` -- exactly one target per scope: a
  ``center`` row carries neither, a ``course`` row carries only
  ``course_id``, a ``group`` row carries only ``group_id``. A
  group-scoped row deliberately cannot carry a ``course_id``; its Course
  is reached through its Group, so the two can never disagree.
- ``ck_announcements_status_timestamps`` -- the complete state/timestamp
  truth table: a draft has neither timestamp, a published row has
  ``published_at`` and no ``withdrawn_at``, and a withdrawn row has both
  with ``withdrawn_at >= published_at``. A row can therefore never claim
  it was withdrawn before it was published, or that it is a draft that
  was already published.
- ``ck_announcements_version_positive`` -- ``version > 0``.

Conditions a CHECK cannot express are stated here rather than hidden:
that ``author_id`` names a Teacher or an Administrator with an active
account; that a Teacher may only target a Group they are actively
assigned to; that a published announcement's text, scope, target and
author are frozen; that a withdrawn one is permanently immutable; and
that a reader currently reaches the announcement's scope. All of them are
enforced by the application against the **locked** rows -- a foreign key
proves a row exists, never its role, its assignment, or its lifecycle.

Indexes, one per real query path
--------------------------------
- ``ix_announcements_scope_status_published``
  (``scope``, ``status``, ``published_at``, ``id``) -- the center feed,
  ordered ``published_at DESC, id DESC``.
- ``ix_announcements_course_status_published``
  (``course_id``, ``status``, ``published_at``, ``id``) -- the
  course-scoped feed, and the leftmost prefix InnoDB requires for the
  ``course_id`` foreign key.
- ``ix_announcements_group_status_published``
  (``group_id``, ``status``, ``published_at``, ``id``) -- the
  group-scoped feed and the Teacher's per-Group management list, and the
  index the ``group_id`` foreign key requires.
- ``ix_announcements_author_status_created``
  (``author_id``, ``status``, ``created_at``, ``id``) -- the author's own
  management view, and the index the ``author_id`` foreign key requires.
- ``ix_announcements_status_scope_created``
  (``status``, ``scope``, ``created_at``, ``id``) -- the Administrator's
  management list, whose two filters are exactly status and scope.

**Every** foreign key is plain, with no ``ON DELETE`` and no
``ON UPDATE`` action, matching the history-preserving lifecycle of the
whole project: no Course, Group or account lifecycle change may remove an
announcement, and nothing is ever hard-deleted.

No ``mysql_engine`` / ``mysql_charset`` argument is declared, so the table
inherits the MySQL server's (or the database's) default exactly as every
earlier table in this project does.

The downgrade reverses both steps in reverse order: the notification
CHECK is restored to exactly its M14 expression (which is *narrower*, so
it is applied **after** the announcements table is gone and is safe only
because no ``announcement_published`` row can exist without the feature
that writes them -- see the guard in :func:`downgrade`), and
``announcements`` is dropped. The table is dropped without dropping its
indexes first: each of the last three leads with a foreign-key column and
MySQL refuses to drop such an index while the constraint exists
(errno 1553), while ``DROP TABLE`` removes a table's own indexes and
foreign keys with it. Verified in both directions against the authorized
development MySQL database and against an isolated SQLite probe.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7a91f4b2d68'
down_revision = '2f6d1c83ab47'
branch_labels = None
depends_on = None


#: The notification kinds before and after this revision. Written out in
#: full rather than imported from the application: a migration must keep
#: describing the schema it produced even after the enum moves on again.
_KINDS_BEFORE = (
    'enrollment_activated',
    'enrollment_withdrawn',
    'teacher_assignment_activated',
    'teacher_assignment_removed',
    'schedule_changed',
    'lesson_published',
    'material_available',
)
_KINDS_AFTER = _KINDS_BEFORE + ('announcement_published',)

_KIND_CHECK_NAME = 'ck_notifications_kind_valid'


def _kind_check_sql(values):
    return "kind IN (" + ", ".join("'%s'" % value for value in values) + ")"


def _notifications_table(values):
    """The complete ``notifications`` definition, with the given ``kind``
    CHECK -- used only as the SQLite rebuild's ``copy_from``.

    Declared explicitly so the rebuild never reflects the live table: a
    reflected SQLite table carries the **old** CHECK, which the rebuild
    would then faithfully reinstate.
    """
    return sa.Table(
        'notifications',
        sa.MetaData(),
        sa.Column(
            'id',
            sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            nullable=False,
        ),
        sa.Column('public_id', sa.String(length=36), nullable=False),
        sa.Column(
            'recipient_id',
            sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            nullable=False,
        ),
        sa.Column('kind', sa.String(length=48), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('target_path', sa.String(length=512), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(_kind_check_sql(values), name=_KIND_CHECK_NAME),
        sa.ForeignKeyConstraint(['recipient_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('public_id'),
        sa.Index(
            'ix_notifications_recipient_created_id',
            'recipient_id', 'created_at', 'id',
        ),
        sa.Index(
            'ix_notifications_recipient_unread_created',
            'recipient_id', 'read_at', 'created_at',
        ),
    )


def _replace_notification_kind_check(values):
    """Replace ``ck_notifications_kind_valid`` with the CHECK that allows
    exactly `values`, preserving every existing row.

    Two genuinely different implementations, because the two backends are
    genuinely different here -- see the module docstring.
    """
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table(
            'notifications',
            copy_from=_notifications_table(values),
            recreate='always',
        ) as batch_op:
            # A deliberate no-change alter: batch mode needs at least one
            # operation to flush, and under ``recreate='always'`` it is
            # absorbed into the rebuild, whose shape comes entirely from
            # ``copy_from``.
            batch_op.alter_column(
                'kind',
                existing_type=sa.String(length=48),
                existing_nullable=False,
            )
    else:
        op.drop_constraint(_KIND_CHECK_NAME, 'notifications', type_='check')
        op.create_check_constraint(
            _KIND_CHECK_NAME, 'notifications', _kind_check_sql(values)
        )


def upgrade():
    op.create_table('announcements',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('author_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('scope', sa.String(length=32), nullable=False),
    sa.Column('course_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('body', sa.String(length=5000), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('withdrawn_at', sa.DateTime(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "scope IN ('center', 'course', 'group')",
        name='ck_announcements_scope_valid',
    ),
    sa.CheckConstraint(
        "status IN ('draft', 'published', 'withdrawn')",
        name='ck_announcements_status_valid',
    ),
    sa.CheckConstraint(
        "(scope = 'center' AND course_id IS NULL AND group_id IS NULL)"
        " OR (scope = 'course' AND course_id IS NOT NULL AND group_id IS NULL)"
        " OR (scope = 'group' AND group_id IS NOT NULL AND course_id IS NULL)",
        name='ck_announcements_scope_target',
    ),
    sa.CheckConstraint(
        "(status = 'draft' AND published_at IS NULL AND withdrawn_at IS NULL)"
        " OR (status = 'published' AND published_at IS NOT NULL AND withdrawn_at IS NULL)"
        " OR (status = 'withdrawn' AND published_at IS NOT NULL"
        " AND withdrawn_at IS NOT NULL AND withdrawn_at >= published_at)",
        name='ck_announcements_status_timestamps',
    ),
    sa.CheckConstraint('version > 0', name='ck_announcements_version_positive'),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_announcements_scope_status_published',
        'announcements',
        ['scope', 'status', 'published_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_announcements_course_status_published',
        'announcements',
        ['course_id', 'status', 'published_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_announcements_group_status_published',
        'announcements',
        ['group_id', 'status', 'published_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_announcements_author_status_created',
        'announcements',
        ['author_id', 'status', 'created_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_announcements_status_scope_created',
        'announcements',
        ['status', 'scope', 'created_at', 'id'],
        unique=False,
    )
    # Widening the notification CHECK comes AFTER the table exists, so an
    # interrupted upgrade can never leave a database that accepts an
    # 'announcement_published' row with nowhere for it to point.
    _replace_notification_kind_check(_KINDS_AFTER)


def downgrade():
    # Narrowing the CHECK again would be refused by MySQL if any
    # 'announcement_published' notification still existed. Those rows are
    # produced only by announcement publication, so this deletes them
    # first and says so out loud: they are personal historical rows about
    # a feature this downgrade removes, and leaving them behind would
    # leave the database unable to satisfy its own constraint. Nothing
    # else is deleted -- every notification of every other kind is
    # untouched.
    op.execute(
        "DELETE FROM notifications WHERE kind = 'announcement_published'"
    )
    _replace_notification_kind_check(_KINDS_BEFORE)
    # The table alone, in one statement. Dropping its indexes explicitly
    # first would fail on MySQL with errno 1553 -- three of them lead with
    # a foreign-key column -- and is unnecessary anyway, since DROP TABLE
    # removes a table's own indexes and constraints. See the module
    # docstring.
    op.drop_table('announcements')
