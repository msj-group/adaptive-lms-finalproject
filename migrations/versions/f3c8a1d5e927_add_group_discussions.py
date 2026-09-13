"""add group discussions

Revision ID: f3c8a1d5e927
Revises: a4d9e3f7c215
Create Date: 2026-09-13

Phase 4 / M12. Exactly two things happen here:

1. two tables are created -- ``discussion_topics`` and
   ``discussion_replies`` -- with their CHECK constraints, plain foreign
   keys, unique identifiers, unique creation nonces and query-driven
   indexes; and
2. the existing ``ck_notifications_kind_valid`` CHECK on ``notifications``
   is **replaced** by one that additionally allows
   ``'discussion_topic_created'``, keeping every earlier kind -- including
   Phase 4 / M11's ``'message_received'``.

Nothing else is touched: no other table, column, index or constraint is
altered, no existing row is rewritten, and nothing is seeded.

The notification CHECK is replaced exactly as Phase 4 / M09
(``c7a91f4b2d68``) and M11 (``a4d9e3f7c215``) replaced it, for the same
reasons: a CHECK has no ``ALTER`` on either backend. On **MySQL 8** the old
expression is dropped and the wider one added -- ordinary DDL, no row read
or rewritten, and every stored notification already satisfies the wider
expression. On **SQLite** the table is rebuilt by batch mode from an
explicitly declared ``copy_from`` table, never a reflected one, because a
reflected SQLite table would carry the old CHECK back into the rebuild.

The new tables
--------------
- ``discussion_topics`` -- a UUID ``public_id``, the owning ``group_id``
  into ``groups``, the ``author_id`` into ``users``, a plain-text ``title``
  (150) and ``body`` (5,000), a ``status`` of ``open`` or ``locked``, a
  positive ``version``, a unique 64-character ``creation_nonce``, and
  whole-second UTC ``created_at`` / ``updated_at``.
- ``discussion_replies`` -- a UUID ``public_id``, ``topic_id`` into
  ``discussion_topics``, ``author_id`` into ``users``, a plain-text ``body``
  (5,000), a unique ``creation_nonce`` and a whole-second UTC
  ``created_at``.

Topic and reply content is never updated or deleted by the application;
only a topic's ``status``, ``version`` and ``updated_at`` change, together,
when a Teacher locks or reopens it.

What each rule is for
---------------------
- ``ck_discussion_topics_title_not_blank`` /
  ``ck_discussion_topics_body_not_blank`` /
  ``ck_discussion_replies_body_not_blank`` -- ``LENGTH(TRIM(...)) > 0``:
  the final defense against empty or all-space text, behind the
  application's own normalisation.
- ``ck_discussion_topics_status_valid`` -- the closed status set.
- ``ck_discussion_topics_version_positive`` -- ``version > 0``.
- ``creation_nonce`` UNIQUE on both tables -- the final duplicate-
  submission defense.

Conditions a CHECK cannot express are stated here rather than hidden: that
a topic's author is an active Teacher actively assigned to the topic's
operational Group; that a reply's author is an active Student actively
enrolled in, or an active Teacher actively assigned to, that Group; and
that the topic is ``open`` when a reply is added. A CHECK cannot read
other rows, so all of these are proved by the application against locked
rows before each write.

Indexes, one per real query path
--------------------------------
- ``ix_discussion_topics_group_created_id`` (``group_id``, ``created_at``,
  ``id``) -- the Group topic list's deterministic order; also the
  ``group_id`` foreign key.
- ``ix_discussion_topics_author_created_id`` (``author_id``,
  ``created_at``, ``id``) -- the index the ``author_id`` foreign key
  requires.
- ``ix_discussion_replies_topic_created_id`` (``topic_id``, ``created_at``,
  ``id``) -- the topic timeline's deterministic order; also the
  ``topic_id`` foreign key.
- ``ix_discussion_replies_author_created_id`` (``author_id``,
  ``created_at``, ``id``) -- the index the ``author_id`` foreign key
  requires.

**No MySQL execution plan has been measured for these tables**; as with
every earlier Part, this is a reasoned design pending an authorized
``EXPLAIN``.

Every foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE``
action: nothing is ever hard-deleted, and no Group or account lifecycle
change may remove a discussion. No ``mysql_engine`` / ``mysql_charset``
argument is declared, so the tables inherit the server's defaults like
every earlier table.

The downgrade reverses both steps in reverse order: only
``discussion_topic_created`` notifications are deleted (they are personal
rows about a feature the downgrade removes, and the narrower CHECK could
not otherwise be restored), the CHECK is restored to its exact M11
expression -- ``message_received`` included -- and the two tables are
dropped, replies before topics. Their indexes are not dropped first: each
leads with a foreign-key column, which MySQL refuses to drop while the
constraint exists (errno 1553), and ``DROP TABLE`` removes them anyway.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3c8a1d5e927'
down_revision = 'a4d9e3f7c215'
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
    'message_received',
)
_KINDS_AFTER = _KINDS_BEFORE + ('discussion_topic_created',)

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
    op.create_table('discussion_topics',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('author_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('body', sa.String(length=5000), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('creation_nonce', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        'LENGTH(TRIM(title)) > 0',
        name='ck_discussion_topics_title_not_blank',
    ),
    sa.CheckConstraint(
        'LENGTH(TRIM(body)) > 0',
        name='ck_discussion_topics_body_not_blank',
    ),
    sa.CheckConstraint(
        "status IN ('open', 'locked')",
        name='ck_discussion_topics_status_valid',
    ),
    sa.CheckConstraint(
        'version > 0',
        name='ck_discussion_topics_version_positive',
    ),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('creation_nonce')
    )
    op.create_index(
        'ix_discussion_topics_group_created_id',
        'discussion_topics',
        ['group_id', 'created_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_discussion_topics_author_created_id',
        'discussion_topics',
        ['author_id', 'created_at', 'id'],
        unique=False,
    )

    op.create_table('discussion_replies',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('topic_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('author_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('body', sa.String(length=5000), nullable=False),
    sa.Column('creation_nonce', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        'LENGTH(TRIM(body)) > 0',
        name='ck_discussion_replies_body_not_blank',
    ),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['topic_id'], ['discussion_topics.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('creation_nonce')
    )
    op.create_index(
        'ix_discussion_replies_topic_created_id',
        'discussion_replies',
        ['topic_id', 'created_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_discussion_replies_author_created_id',
        'discussion_replies',
        ['author_id', 'created_at', 'id'],
        unique=False,
    )

    # Widening the notification CHECK comes AFTER the tables exist, so an
    # interrupted upgrade can never accept a 'discussion_topic_created' row
    # with nowhere for it to point.
    _replace_notification_kind_check(_KINDS_AFTER)


def downgrade():
    # The narrower CHECK would be refused while any
    # 'discussion_topic_created' notification remains. Those rows exist only
    # because of the feature this downgrade removes, so they -- and nothing
    # else, 'message_received' included -- are deleted.
    op.execute("DELETE FROM notifications WHERE kind = 'discussion_topic_created'")
    _replace_notification_kind_check(_KINDS_BEFORE)
    # Replies before topics. Each table alone, in one statement: dropping
    # indexes that lead with a foreign-key column first would fail on MySQL
    # with errno 1553, and DROP TABLE removes a table's indexes and
    # constraints.
    op.drop_table('discussion_replies')
    op.drop_table('discussion_topics')
