"""add private teacher-student messaging

Revision ID: a4d9e3f7c215
Revises: e5b83c7d1a49
Create Date: 2026-09-13

Phase 4 / M11. Exactly two things happen here:

1. three append-only tables are created -- ``message_threads``,
   ``message_thread_members`` and ``messages`` -- with their CHECK
   constraints, plain foreign keys, unique identifiers, unique creation
   nonces and query-driven indexes; and
2. the existing ``ck_notifications_kind_valid`` CHECK on ``notifications``
   is **replaced** by one that additionally allows ``'message_received'``.

Nothing else is touched: no other table, column, index or constraint is
altered, no existing row is rewritten, and nothing is seeded.

The notification CHECK is replaced exactly as Phase 4 / M09
(``c7a91f4b2d68``) replaced it, for the same reasons: a CHECK has no
``ALTER`` on either backend. On **MySQL 8** the old expression is dropped
and the wider one added -- ordinary DDL, no row read or rewritten, and
every stored notification already satisfies the wider expression. On
**SQLite** the table is rebuilt by batch mode from an explicitly declared
``copy_from`` table, never a reflected one, because a reflected SQLite
table would carry the old CHECK back into the rebuild.

The new tables
--------------
- ``message_threads`` -- a UUID ``public_id``, the immutable
  ``created_by_id`` into ``users``, a plain-text ``subject`` (150), a
  unique 64-character ``creation_nonce`` and a whole-second UTC
  ``created_at``.
- ``message_thread_members`` -- ``thread_id`` into ``message_threads``,
  ``user_id`` into ``users`` and a whole-second UTC ``joined_at``; one row
  per participant, unique per ``(thread_id, user_id)``.
- ``messages`` -- a UUID ``public_id``, ``thread_id``, ``sender_id``, a
  plain-text ``body`` (5,000), a unique ``creation_nonce`` and a
  whole-second UTC ``created_at``.

No row in any of them is ever updated or deleted by the application.

What each rule is for
---------------------
- ``ck_message_threads_subject_not_blank`` / ``ck_messages_body_not_blank``
  -- ``LENGTH(TRIM(...)) > 0``: the final defense against an empty or
  all-space subject or body, behind the application's own normalisation.
- ``creation_nonce`` UNIQUE on both tables -- the final duplicate-
  submission defense: a replayed or double-clicked form creates at most
  one thread and one message.
- ``uq_message_thread_members_thread_user`` -- a user appears at most once
  in a thread.

Conditions a CHECK cannot express are stated here rather than hidden:
that a thread has exactly two members, one an active Student and the
other an active Teacher; that its creator and every sender is one of those
members; and that the two currently share an operational Group whenever a
message is added. A CHECK cannot read other rows, so all of these are
proved by the application against locked rows before each insert.

Indexes, one per real query path
--------------------------------
- ``ix_message_threads_created_by_created_id`` (``created_by_id``,
  ``created_at``, ``id``) -- the leftmost prefix InnoDB requires for the
  ``created_by_id`` foreign key.
- ``uq_message_thread_members_thread_user`` (``thread_id``, ``user_id``)
  -- membership proof for one thread, and the ``thread_id`` foreign key.
- ``ix_message_thread_members_user_thread`` (``user_id``, ``thread_id``)
  -- the inbox's "threads I belong to", and the ``user_id`` foreign key.
- ``ix_messages_thread_created_id`` (``thread_id``, ``created_at``,
  ``id``) -- the conversation page's deterministic order and the inbox's
  newest message per thread; also the ``thread_id`` foreign key.
- ``ix_messages_sender_created_id`` (``sender_id``, ``created_at``,
  ``id``) -- the index the ``sender_id`` foreign key requires.

**No MySQL execution plan has been measured for these tables**; as with
every earlier Part, this is a reasoned design pending an authorized
``EXPLAIN``.

Every foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE``
action: nothing is ever hard-deleted and no account lifecycle change may
remove a conversation. No ``mysql_engine`` / ``mysql_charset`` argument is
declared, so the tables inherit the server's defaults like every earlier
table.

The downgrade reverses both steps in reverse order: ``message_received``
notifications are deleted (they are personal rows about a feature the
downgrade removes, and the narrower CHECK could not otherwise be
restored), the CHECK is restored to its M09 expression, and the three
tables are dropped children first. Their indexes are not dropped first:
several lead with a foreign-key column, which MySQL refuses to drop while
the constraint exists (errno 1553), and ``DROP TABLE`` removes them anyway.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4d9e3f7c215'
down_revision = 'e5b83c7d1a49'
branch_labels = None
depends_on = None


#: The notification kinds before and after this revision, written out
#: rather than imported so the migration keeps describing the schema it
#: produced.
_KINDS_BEFORE = (
    'enrollment_activated',
    'enrollment_withdrawn',
    'teacher_assignment_activated',
    'teacher_assignment_removed',
    'schedule_changed',
    'lesson_published',
    'material_available',
    'announcement_published',
)
_KINDS_AFTER = _KINDS_BEFORE + ('message_received',)

_KIND_CHECK_NAME = 'ck_notifications_kind_valid'


def _kind_check_sql(values):
    return "kind IN (" + ", ".join("'%s'" % value for value in values) + ")"


def _notifications_table(values):
    """The complete ``notifications`` definition with the given ``kind``
    CHECK -- used only as the SQLite rebuild's ``copy_from``."""
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
    """Replace ``ck_notifications_kind_valid`` with the CHECK allowing
    exactly `values`, preserving every existing row."""
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table(
            'notifications',
            copy_from=_notifications_table(values),
            recreate='always',
        ) as batch_op:
            # A deliberate no-change alter: batch mode needs one operation
            # to flush, and the rebuilt shape comes entirely from copy_from.
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
    op.create_table('message_threads',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('subject', sa.String(length=150), nullable=False),
    sa.Column('creation_nonce', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        'LENGTH(TRIM(subject)) > 0',
        name='ck_message_threads_subject_not_blank',
    ),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('creation_nonce')
    )
    op.create_index(
        'ix_message_threads_created_by_created_id',
        'message_threads',
        ['created_by_id', 'created_at', 'id'],
        unique=False,
    )

    op.create_table('message_thread_members',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('thread_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('joined_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['thread_id'], ['message_threads.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint(
        'thread_id', 'user_id', name='uq_message_thread_members_thread_user'
    )
    )
    op.create_index(
        'ix_message_thread_members_user_thread',
        'message_thread_members',
        ['user_id', 'thread_id'],
        unique=False,
    )

    op.create_table('messages',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('thread_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('sender_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('body', sa.String(length=5000), nullable=False),
    sa.Column('creation_nonce', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        'LENGTH(TRIM(body)) > 0',
        name='ck_messages_body_not_blank',
    ),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['thread_id'], ['message_threads.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('creation_nonce')
    )
    op.create_index(
        'ix_messages_thread_created_id',
        'messages',
        ['thread_id', 'created_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_messages_sender_created_id',
        'messages',
        ['sender_id', 'created_at', 'id'],
        unique=False,
    )

    # Widening the notification CHECK comes AFTER the tables exist, so an
    # interrupted upgrade can never accept a 'message_received' row with
    # nowhere for it to point.
    _replace_notification_kind_check(_KINDS_AFTER)


def downgrade():
    # The narrower CHECK would be refused while any 'message_received'
    # notification remains. Those rows exist only because of the feature
    # this downgrade removes, so they -- and nothing else -- are deleted.
    op.execute("DELETE FROM notifications WHERE kind = 'message_received'")
    _replace_notification_kind_check(_KINDS_BEFORE)
    # Children first. Each table alone, in one statement: dropping indexes
    # that lead with a foreign-key column first would fail on MySQL with
    # errno 1553, and DROP TABLE removes a table's indexes and constraints.
    op.drop_table('messages')
    op.drop_table('message_thread_members')
    op.drop_table('message_threads')
