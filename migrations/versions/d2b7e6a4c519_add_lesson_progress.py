"""add lesson progress

Revision ID: d2b7e6a4c519
Revises: f3c8a1d5e927
Create Date: 2026-09-13

Phase 4 / M13. Exactly one thing happens here: the ``lesson_progress`` table
is created, with its CHECK constraint, its UNIQUE constraint, plain foreign
keys and query-driven indexes.

Nothing else is touched: no other table, column, index or constraint is
altered, no existing row is read or rewritten, and nothing is seeded -- a
progress row exists only because a Student opened or completed a Lesson,
so a migration that invented one would state something that never
happened.

The new table
-------------
``lesson_progress`` -- ``student_id`` into ``users``, ``group_id`` into
``groups``, ``lesson_id`` into ``lessons``, a whole-second UTC
``created_at``, a nullable ``completed_at``, a nullable ``last_opened_at``
and a positive ``version``.

There is no ``public_id``: no URL, form value or signed token names a
progress row. A completion form is identified by the Group, Unit and Lesson
public identifiers and the authenticated Student.

What each rule is for
---------------------
- ``uq_lesson_progress_student_group_lesson`` -- at most one row per
  Student, Group and Lesson: the final duplicate defense.
- ``ck_lesson_progress_version_positive`` -- ``version > 0``.

Conditions a CHECK cannot express are stated here rather than hidden: that
the Student is an active Student account actively enrolled in the Group,
that the Group and its Course, Level and AcademicTerm are active, that the
Lesson is published in an active Unit of **that** Group. A CHECK cannot read
other rows, so all of these are proved by the application -- in SQL on
every read, and against locked rows before every completion write.

Indexes, one per real query path
--------------------------------
- ``uq_lesson_progress_student_group_lesson`` (``student_id``,
  ``group_id``, ``lesson_id``) -- the Student Lesson-page lookup; also the
  ``student_id`` foreign key.
- ``ix_lesson_progress_student_opened_id`` (``student_id``,
  ``last_opened_at``, ``id``) -- Recently Opened and Continue Learning.
- ``ix_lesson_progress_group_student`` (``group_id``, ``student_id``) --
  the Teacher Group view; also the ``group_id`` foreign key.
- ``ix_lesson_progress_lesson_group`` (``lesson_id``, ``group_id``) --
  Lesson/Group ownership lookups; also the ``lesson_id`` foreign key.

**No MySQL execution plan has been measured for this table**; as with every
earlier Part, this is a reasoned design pending an authorized ``EXPLAIN``.

Every foreign key is plain, with no ``ON DELETE`` and no ``ON UPDATE``
action: nothing is ever hard-deleted, and no Group, Lesson or account
lifecycle change may remove a Student's progress. No ``mysql_engine`` /
``mysql_charset`` argument is declared, so the table inherits the server's
defaults like every earlier table.

The downgrade drops the table in one statement. Its indexes are not dropped
first: each leads with a foreign-key column, which MySQL refuses to drop
while the constraint exists (errno 1553), and ``DROP TABLE`` removes them
anyway.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2b7e6a4c519'
down_revision = 'f3c8a1d5e927'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('lesson_progress',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('student_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('lesson_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('last_opened_at', sa.DateTime(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint(
        'version > 0',
        name='ck_lesson_progress_version_positive',
    ),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.ForeignKeyConstraint(['lesson_id'], ['lessons.id'], ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint(
        'student_id', 'group_id', 'lesson_id',
        name='uq_lesson_progress_student_group_lesson',
    )
    )
    op.create_index(
        'ix_lesson_progress_student_opened_id',
        'lesson_progress',
        ['student_id', 'last_opened_at', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_lesson_progress_group_student',
        'lesson_progress',
        ['group_id', 'student_id'],
        unique=False,
    )
    op.create_index(
        'ix_lesson_progress_lesson_group',
        'lesson_progress',
        ['lesson_id', 'group_id'],
        unique=False,
    )


def downgrade():
    # One statement: dropping indexes that lead with a foreign-key column
    # first would fail on MySQL with errno 1553, and DROP TABLE removes a
    # table's indexes and constraints.
    op.drop_table('lesson_progress')
