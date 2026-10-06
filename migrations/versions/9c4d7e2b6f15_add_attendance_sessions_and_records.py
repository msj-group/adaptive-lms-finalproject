"""add attendance_sessions and attendance_records

Revision ID: 9c4d7e2b6f15
Revises: 4e2c9b7f1a83
Create Date: 2026-09-11

Additive only (Phase 4 / M07). Exactly two new tables.

Nothing existing is read, rewritten or altered. ``assignments``,
``submissions``, ``submission_feedback``, ``quizzes``,
``quiz_questions``, ``question_options``, ``quiz_attempts``,
``quiz_answers``, ``quiz_answer_selections``, ``listening_activities``,
``speaking_activities``, ``speaking_submissions``, ``speaking_feedback``,
``schedules``, ``enrollments``, ``group_teacher_assignments``,
``materials``, ``uploaded_files``, ``file_access_logs``, ``notifications``
and every academic table are untouched: there is no ``batch_alter_table``,
no ``add_column``, no ``alter_column``, no ``drop``, no data rewrite and no
seeded row. **No attendance data is seeded** -- an attendance session
exists only because a Teacher opened one for a real scheduled meeting, so
a migration that creates empty tables states nothing about any past class.

``groups``, ``schedules`` and ``users`` appear only as existing foreign-key
targets. **Every** reference is plain, with no ``ON DELETE`` and no
``ON UPDATE`` action, matching the history-preserving lifecycle of the
whole project: no Group, Schedule, account, enrollment or academic
lifecycle change may remove a recorded attendance session or one of its
records, and nothing is ever hard-deleted.

What each rule is for:

- ``uq_attendance_sessions_schedule_date`` -- one attendance session per
  scheduled occurrence, so a double-submitted confirmation, a reloaded
  POST or a genuinely concurrent second Teacher loses at the database
  instead of producing a second session for the same meeting. It is also
  the exact shape of the "does this occurrence already have a session?"
  lookup and the leftmost prefix the ``schedule_id`` foreign key needs, so
  no separate single-column index is declared for it.
- ``uq_attendance_records_session_student`` -- exactly one record per
  session and Student, the shape of every per-Student lookup inside a
  session, and the index the ``attendance_session_id`` foreign key needs.
- ``ck_attendance_records_status`` -- the four ``AttendanceStatus``
  members as a literal ``IN`` list rather than a MySQL ``ENUM`` column,
  the same convention every other status column in this project uses, so
  the SQLite test backend enforces it identically and adding a member
  stays a visible schema change.
- ``ck_attendance_sessions_version_positive`` /
  ``ck_attendance_records_version_positive`` /
  ``ck_attendance_sessions_time_order`` -- plain comparison CHECKs,
  supported by MySQL 8 and by SQLite alike. The time order mirrors
  ``ck_schedules_time_order`` on the row those values were copied from.

``ix_attendance_sessions_group_date_id`` is the Teacher's Group-scoped
list shape (equality on the Group, then the two ordering columns) and
doubles as the index the ``group_id`` foreign key requires;
``ix_attendance_sessions_date_id`` is the Administrator's center-wide
review list, ordered by exactly those two columns across every Group;
``ix_attendance_records_student_id`` exists for the ``student_id``
**foreign key**, which InnoDB requires and nothing else leads with, and is
also what the Student's own summary leads with.

Two conditions a CHECK cannot express are stated here rather than hidden:
that the referenced Schedule belongs to the same Group as the session, and
that ``student_id`` names a User who was a Student with an active account
and an active Enrollment in that Group *at capture time*. Both are
cross-table conditions enforced in the application against the locked
rows -- a foreign key proves a row exists, never its role or its
relationship.

No ``mysql_engine`` / ``mysql_charset`` argument is declared on either
table: each inherits the MySQL server's (or the database's) default
exactly as every earlier table in this project does.

The downgrade drops the two tables it created, in reverse dependency
order, and nothing else.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c4d7e2b6f15'
down_revision = '4e2c9b7f1a83'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('attendance_sessions',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('schedule_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('session_date', sa.Date(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('finalized_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('start_time < end_time', name='ck_attendance_sessions_time_order'),
    sa.CheckConstraint('version > 0', name='ck_attendance_sessions_version_positive'),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('schedule_id', 'session_date', name='uq_attendance_sessions_schedule_date')
    )
    op.create_index(
        'ix_attendance_sessions_date_id',
        'attendance_sessions',
        ['session_date', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_attendance_sessions_group_date_id',
        'attendance_sessions',
        ['group_id', 'session_date', 'id'],
        unique=False,
    )
    op.create_table('attendance_records',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('attendance_session_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('student_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "status IN ('present', 'absent', 'late', 'excused')",
        name='ck_attendance_records_status',
    ),
    sa.CheckConstraint('version > 0', name='ck_attendance_records_version_positive'),
    sa.ForeignKeyConstraint(['attendance_session_id'], ['attendance_sessions.id'], ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attendance_session_id', 'student_id', name='uq_attendance_records_session_student'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        op.f('ix_attendance_records_student_id'),
        'attendance_records',
        ['student_id'],
        unique=False,
    )


def downgrade():
    if op.get_context().dialect.name != 'mysql':
        op.drop_index(op.f('ix_attendance_records_student_id'), table_name='attendance_records')
    op.drop_table('attendance_records')
    if op.get_context().dialect.name != 'mysql':
        op.drop_index('ix_attendance_sessions_group_date_id', table_name='attendance_sessions')
        op.drop_index('ix_attendance_sessions_date_id', table_name='attendance_sessions')
    op.drop_table('attendance_sessions')
