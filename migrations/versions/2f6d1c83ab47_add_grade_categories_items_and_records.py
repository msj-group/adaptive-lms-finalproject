"""add grade_categories, grade_items and grade_records

Revision ID: 2f6d1c83ab47
Revises: 9c4d7e2b6f15
Create Date: 2026-09-12

Additive only (Phase 4 / M08). Exactly three new tables.

Nothing existing is read, rewritten or altered. ``assignments``,
``submissions``, ``submission_feedback``, ``quizzes``,
``quiz_questions``, ``question_options``, ``quiz_attempts``,
``quiz_answers``, ``quiz_answer_selections``, ``listening_activities``,
``speaking_activities``, ``speaking_submissions``, ``speaking_feedback``,
``attendance_sessions``, ``attendance_records``, ``schedules``,
``enrollments``, ``group_teacher_assignments``, ``materials``,
``uploaded_files``, ``file_access_logs``, ``notifications`` and every
academic table are untouched: there is no ``batch_alter_table``, no
``add_column``, no ``alter_column``, no ``drop``, no data rewrite and no
seeded row.

**No score column is added to any existing table**, and that is the
central design decision of this milestone rather than an omission. A
Teacher-entered grade lives in ``grade_records`` and nowhere else, so
``submissions``, ``quiz_attempts``, ``speaking_submissions``,
``attendance_records``, ``submission_feedback`` and ``speaking_feedback``
keep exactly the shape they had. Quiz attempts continue to compute their
own assessment result in their own columns; **no attempt result is copied,
imported or backfilled into a grade record here or anywhere else**.

**No gradebook data is seeded.** A grade category exists only because a
Teacher created it, and a grade record exists only because a Teacher
created a grade item and the Group had students at that moment -- so a
migration that creates empty tables states nothing about any past term,
and inventing a default category set would be inventing business rules
nobody approved.

``groups``, ``assignments``, ``quizzes``, ``speaking_activities`` and
``users`` appear only as existing foreign-key targets. **Every**
reference is plain, with no ``ON DELETE`` and no ``ON UPDATE`` action,
matching the history-preserving lifecycle of the whole project: no Group,
Assignment, Quiz, Speaking, account or academic lifecycle change may
remove a grade category, a grade item or a student's grade, and nothing
is ever hard-deleted. That restriction is what makes the linked-source
columns safe to declare at all -- a cascade from ``quizzes`` would
otherwise be able to delete somebody's released result.

Exact numbers, never binary floats
----------------------------------
- ``grade_categories.weight_basis_points`` is an ``INTEGER`` count of
  hundredths of a percent (10,000 = 100.00%), so "do this group's
  categories add up to exactly 100%?" is an exact integer comparison
  rather than a tolerance against a float.
- ``grade_items.max_points`` and ``grade_records.score`` are
  ``NUMERIC(7, 2)``, which MySQL treats as an exact synonym for
  ``DECIMAL(7, 2)`` -- the same storage and the same exact arithmetic.
  There is no ``FLOAT``, ``DOUBLE`` or ``REAL`` column anywhere in this
  revision. Every sum, percentage and weighted total is computed in Python
  ``Decimal`` by ``app/services/grade_calculations.py``; the database is
  never asked to do grade arithmetic, which is also why the two backends
  cannot disagree about a result.

What each rule is for
---------------------
- ``uq_grade_categories_group_title`` -- one category name per Group, so
  a double-submitted form or a genuinely concurrent second Teacher loses
  at the database instead of producing two same-named categories. It is
  also the exact shape of the duplicate-name lookup and the leftmost
  prefix the ``group_id`` foreign key needs, so no separate single-column
  index is declared for it.
- ``ck_grade_categories_weight_range`` -- ``1 <= weight <= 10000``. A
  per-row bound only: "the *sum* of this Group's categories is at most
  10,000" is a cross-row condition no CHECK can express, and is enforced
  by the application against the locked category rows.
- ``uq_grade_records_item_student`` -- exactly one record per grade item
  and Student, the shape of every per-Student lookup inside an item, and
  the index the ``grade_item_id`` foreign key needs.
- ``ck_grade_items_source_kind`` -- the five ``GradeSourceKind`` members
  as a literal ``IN`` list rather than a MySQL ``ENUM`` column, the same
  convention every other closed-set column in this project uses, so the
  SQLite test backend enforces it identically and adding a member stays a
  visible schema change.
- ``ck_grade_items_source_link`` -- the exact-one-source rule: an
  ``assignment`` item carries exactly ``assignment_id``, a ``quiz`` item
  exactly ``quiz_id``, a ``speaking`` item exactly
  ``speaking_activity_id``, and an ``activity`` or ``manual`` item
  carries none of the three. Three real typed foreign keys rather than a
  generic ``source_type`` / ``source_id`` pair, deliberately: a
  polymorphic id is a reference the database cannot check.
- ``ck_grade_records_score_non_negative`` -- ``score IS NULL OR
  score >= 0``. NULL is the legitimate "not graded yet" state.
- ``ck_grade_records_graded_consistency`` -- ``graded_by_id`` and
  ``graded_at`` are both NULL or both set, so "who last graded this and
  when" can never be half-recorded.
- ``ck_grade_items_max_points_positive`` /
  ``ck_grade_*_version_positive`` -- plain comparison CHECKs, supported
  by MySQL 8 and by SQLite alike.

Indexes, one per real query path
--------------------------------
- ``uq_grade_categories_group_title`` -- the Group-scoped category list
  and the duplicate check (see above).
- ``ix_grade_items_category_id`` (``category_id``, ``id``) -- every item
  list is a category equality (or an ``IN`` over one Group's categories)
  ordered by ``id``, and it is the index the ``category_id`` foreign key
  requires.
- ``ix_grade_items_assignment_id`` / ``ix_grade_items_quiz_id`` /
  ``ix_grade_items_speaking_activity_id`` -- required by InnoDB for those
  three foreign keys, which nothing else leads with, and the shape of the
  "is this source already linked to a grade item?" lookup.
- ``uq_grade_records_item_student`` -- the roster lookup (see above).
- ``ix_grade_records_student_item`` (``student_id``, ``grade_item_id``)
  -- the Student's own grade history, and the index the ``student_id``
  foreign key requires.
- ``ix_grade_records_graded_by_id`` -- required by InnoDB for the
  ``graded_by_id`` foreign key.

Conditions a CHECK cannot express are stated here rather than hidden:
that a linked Assignment / Quiz / SpeakingActivity belongs to the same
Group as the item's category; that a linked source is published before
the item is released; that ``score <= max_points`` (a cross-table
comparison); that a Group's category weights total at most 10,000 and
exactly 10,000 before any release; and that ``student_id`` names a User
who was a Student with an active account and an active Enrollment in that
Group *at capture time*. All of them are enforced in the application
against the locked rows -- a foreign key proves a row exists, never its
role or its relationship.

No ``mysql_engine`` / ``mysql_charset`` argument is declared on any of the
three tables: each inherits the MySQL server's (or the database's)
default exactly as every earlier table in this project does.

The downgrade drops the three tables it created, in reverse dependency
order, and nothing else. It deliberately does **not** drop the indexes
first, even though earlier revisions in this project do: every one of
these indexes leads with a column that a foreign key needs, and MySQL
refuses to drop such an index while the constraint exists --

    (1553, "Cannot drop index 'ix_grade_records_student_item': needed in
     a foreign key constraint")

-- which was observed against the authorized development MySQL database,
not assumed. ``DROP TABLE`` removes a table's own indexes and foreign
keys with it, so dropping the three tables in reverse dependency order is
both sufficient and the only order that works on MySQL and SQLite alike.
Verified in both directions against development MySQL and against an
isolated SQLite probe.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f6d1c83ab47'
down_revision = '9c4d7e2b6f15'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('grade_categories',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('weight_basis_points', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        'weight_basis_points >= 1 AND weight_basis_points <= 10000',
        name='ck_grade_categories_weight_range',
    ),
    sa.CheckConstraint('version > 0', name='ck_grade_categories_version_positive'),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'title', name='uq_grade_categories_group_title'),
    sa.UniqueConstraint('public_id')
    )
    op.create_table('grade_items',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('category_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('source_kind', sa.String(length=32), nullable=False),
    sa.Column('max_points', sa.Numeric(precision=7, scale=2), nullable=False),
    sa.Column('assignment_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('quiz_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('speaking_activity_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('released_at', sa.DateTime(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        "source_kind IN ('assignment', 'quiz', 'speaking', 'activity', 'manual')",
        name='ck_grade_items_source_kind',
    ),
    sa.CheckConstraint(
        "(source_kind = 'assignment' AND assignment_id IS NOT NULL"
        " AND quiz_id IS NULL AND speaking_activity_id IS NULL)"
        " OR (source_kind = 'quiz' AND quiz_id IS NOT NULL"
        " AND assignment_id IS NULL AND speaking_activity_id IS NULL)"
        " OR (source_kind = 'speaking' AND speaking_activity_id IS NOT NULL"
        " AND assignment_id IS NULL AND quiz_id IS NULL)"
        " OR (source_kind IN ('activity', 'manual') AND assignment_id IS NULL"
        " AND quiz_id IS NULL AND speaking_activity_id IS NULL)",
        name='ck_grade_items_source_link',
    ),
    sa.CheckConstraint('max_points > 0', name='ck_grade_items_max_points_positive'),
    sa.CheckConstraint('version > 0', name='ck_grade_items_version_positive'),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ),
    sa.ForeignKeyConstraint(['category_id'], ['grade_categories.id'], ),
    sa.ForeignKeyConstraint(['quiz_id'], ['quizzes.id'], ),
    sa.ForeignKeyConstraint(['speaking_activity_id'], ['speaking_activities.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_grade_items_assignment_id', 'grade_items', ['assignment_id'], unique=False
    )
    op.create_index(
        'ix_grade_items_category_id', 'grade_items', ['category_id', 'id'], unique=False
    )
    op.create_index('ix_grade_items_quiz_id', 'grade_items', ['quiz_id'], unique=False)
    op.create_index(
        'ix_grade_items_speaking_activity_id',
        'grade_items',
        ['speaking_activity_id'],
        unique=False,
    )
    op.create_table('grade_records',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('grade_item_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('student_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('score', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('graded_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('graded_at', sa.DateTime(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint(
        'score IS NULL OR score >= 0', name='ck_grade_records_score_non_negative'
    ),
    sa.CheckConstraint(
        '(graded_by_id IS NULL AND graded_at IS NULL)'
        ' OR (graded_by_id IS NOT NULL AND graded_at IS NOT NULL)',
        name='ck_grade_records_graded_consistency',
    ),
    sa.CheckConstraint('version > 0', name='ck_grade_records_version_positive'),
    sa.ForeignKeyConstraint(['grade_item_id'], ['grade_items.id'], ),
    sa.ForeignKeyConstraint(['graded_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('grade_item_id', 'student_id', name='uq_grade_records_item_student'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(
        'ix_grade_records_graded_by_id', 'grade_records', ['graded_by_id'], unique=False
    )
    op.create_index(
        'ix_grade_records_student_item',
        'grade_records',
        ['student_id', 'grade_item_id'],
        unique=False,
    )


def downgrade():
    # Tables only, in reverse dependency order. Dropping the indexes
    # explicitly first would fail on MySQL with errno 1553 -- each of them
    # leads with a foreign-key column -- and is unnecessary anyway, since
    # DROP TABLE removes a table's own indexes and constraints. See the
    # module docstring.
    op.drop_table('grade_records')
    op.drop_table('grade_items')
    op.drop_table('grade_categories')
