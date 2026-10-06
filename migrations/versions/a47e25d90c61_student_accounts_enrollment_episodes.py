"""Course prices, episode membership and general Student financial accounts.

Revision ID: a47e25d90c61
Revises: e31b8c4d902a
Source only. No live database execution is authorized by implementation.
Existing course prices remain unset; enrollment fails closed until configured.
Legacy study starts use the earliest scheduled meeting, or the term's start.
No actors, cash movements, discounts or retrospective revisions are invented.
"""
from datetime import datetime, time, timedelta
import uuid
from alembic import op
import sqlalchemy as sa
from flask import current_app
from app.services.schedule_occurrences import from_app_local

revision = "a47e25d90c61"
down_revision = "e31b8c4d902a"
branch_labels = None
depends_on = None
CODE = "utf8mb4_0900_bin"


def _preflight(connection):
    checks = [
        "SELECT e.id FROM enrollments e JOIN users u ON u.id=e.student_id WHERE u.role <> 'student' LIMIT 1",
        "SELECT i.id FROM invoices i LEFT JOIN student_fee_assignments a ON a.id=i.student_fee_assignment_id LEFT JOIN enrollments e ON e.id=a.enrollment_id WHERE e.id IS NULL LIMIT 1",
        "SELECT id FROM payment_transactions WHERE MOD(amount,0.001) <> 0 LIMIT 1",
        "SELECT id FROM invoice_items WHERE MOD(amount,0.001) <> 0 LIMIT 1",
        "SELECT p.id FROM payment_transactions p LEFT JOIN invoices i ON i.id=p.invoice_id WHERE i.id IS NULL LIMIT 1",
    ]
    for query in checks:
        if connection.execute(sa.text(query)).first():
            raise RuntimeError("Repair preflight refused inconsistent identity or significant fourth-decimal money. No DDL was started.")
    # Validate the configured timezone before the first MySQL DDL implicit commit.
    from_app_local(current_app.config["APP_TIMEZONE"], datetime(2020, 1, 1))


def _backfill_starts(connection, dry_run=False):
    last_id = 0
    while True:
        rows = connection.execute(sa.text("SELECT g.id,t.start_date FROM `groups` g JOIN academic_terms t ON t.id=g.academic_term_id WHERE g.id>:last ORDER BY g.id LIMIT 200"), {"last": last_id}).mappings().all()
        if not rows:
            return
        for row in rows:
            slots = connection.execute(sa.text("SELECT day_of_week,start_time,effective_start_date,effective_end_date FROM schedules WHERE group_id=:id ORDER BY id"), {"id": row["id"]}).mappings().all()
            meetings = []
            for slot in slots:
                date = slot["effective_start_date"] + timedelta(days=(slot["day_of_week"] - slot["effective_start_date"].weekday()) % 7)
                if date <= slot["effective_end_date"]:
                    clock = slot["start_time"]
                    local = datetime.combine(date, time()) + clock if isinstance(clock, timedelta) else datetime.combine(date, clock)
                    meetings.append(local)
            local_start = min(meetings) if meetings else datetime.combine(row["start_date"], time())
            utc_start = from_app_local(current_app.config["APP_TIMEZONE"], local_start)
            if not dry_run:
                connection.execute(sa.text("UPDATE `groups` SET study_starts_at=:start WHERE id=:id"), {"start": utc_start, "id": row["id"]})
        last_id = rows[-1]["id"]


def upgrade():
    connection = op.get_bind()
    _preflight(connection)
    _backfill_starts(connection, dry_run=True)
    op.add_column("courses", sa.Column("price", sa.Numeric(19, 4), nullable=True))
    op.add_column("courses", sa.Column("currency_code", sa.String(3, collation=CODE), nullable=False, server_default="LYD"))
    op.add_column("courses", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_courses_price", "courses", "price IS NULL OR (price>=0 AND price<=99999.999 AND MOD(price,0.001)=0)")
    op.create_check_constraint("ck_courses_currency", "courses", "currency_code='LYD'")
    op.create_check_constraint("ck_courses_version", "courses", "version>0")
    for column, kind in [("currency_code", sa.String(3, collation=CODE)), ("version", sa.Integer())]:
        op.alter_column("courses", column, existing_type=kind, server_default=None)
    op.add_column("groups", sa.Column("study_starts_at", sa.DateTime(), nullable=True))
    _backfill_starts(connection)
    op.add_column("enrollments", sa.Column("active_marker", sa.Integer(), nullable=True))
    op.add_column("enrollments", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("enrollments", sa.Column("withdrawn_at", sa.DateTime(), nullable=True))
    connection.execute(sa.text("UPDATE enrollments SET active_marker=CASE WHEN status='active' THEN 1 ELSE NULL END, withdrawn_at=CASE WHEN status='withdrawn' THEN updated_at ELSE NULL END"))
    op.create_unique_constraint("uq_enrollments_active_student_group", "enrollments", ["student_id", "group_id", "active_marker"])
    op.create_unique_constraint("uq_enrollments_id_student", "enrollments", ["id", "student_id"])
    op.drop_constraint("uq_enrollments_student_group", "enrollments", type_="unique")
    op.create_check_constraint("ck_enrollments_active_marker", "enrollments", "(status='active' AND active_marker IS NOT NULL AND active_marker=1) OR (status='withdrawn' AND active_marker IS NULL)")
    op.create_check_constraint("ck_enrollments_version", "enrollments", "version>0")
    op.alter_column("enrollments", "version", existing_type=sa.Integer(), server_default=None)
    op.create_table("enrollment_memberships",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("public_id", sa.String(36), nullable=False, unique=True),
        sa.Column("enrollment_id", sa.BigInteger(), sa.ForeignKey("enrollments.id"), nullable=False),
        sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("active_marker", sa.Integer(), nullable=True), sa.Column("joined_at", sa.DateTime(), nullable=False), sa.Column("left_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("enrollment_id", "active_marker", name="uq_enrollment_memberships_current"),
        sa.CheckConstraint("(active_marker IS NOT NULL AND active_marker=1 AND left_at IS NULL) OR (active_marker IS NULL AND left_at IS NOT NULL)", name="ck_enrollment_memberships_state"),
        sa.CheckConstraint("left_at IS NULL OR left_at>=joined_at", name="ck_enrollment_memberships_dates"))
    op.create_index("ix_enrollment_memberships_group_episode", "enrollment_memberships", ["group_id", "enrollment_id"])
    # UUID() is used only for newly introduced membership identities, never
    # replacing an existing Student, Enrollment or document identifier.
    connection.execute(sa.text("INSERT INTO enrollment_memberships (public_id,enrollment_id,group_id,active_marker,joined_at,left_at) SELECT UUID(),id,group_id,active_marker,created_at,withdrawn_at FROM enrollments"))
    op.create_table("enrollment_events",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("enrollment_id", sa.BigInteger(), sa.ForeignKey("enrollments.id"), nullable=False),
        sa.Column("previous_enrollment_id", sa.BigInteger(), sa.ForeignKey("enrollments.id"), nullable=True),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation_key", sa.String(36), nullable=False), sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=True), sa.Column("after_snapshot", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("operation_key", name="uq_enrollment_events_operation_key"))
    op.create_index("ix_enrollment_events_episode", "enrollment_events", ["enrollment_id", "id"])
    op.create_index("ix_enrollment_events_previous_enrollment_id", "enrollment_events", ["previous_enrollment_id"])
    invoice_columns = [sa.Column("student_id", sa.BigInteger(), nullable=True), sa.Column("enrollment_id", sa.BigInteger(), nullable=True),
        sa.Column("charge_amount", sa.Numeric(19, 4), nullable=True), sa.Column("discount_amount", sa.Numeric(19, 4), nullable=False, server_default="0"),
        sa.Column("discount_kind", sa.String(16), nullable=False, server_default="none"), sa.Column("discount_value", sa.String(32), nullable=False, server_default="0"),
        sa.Column("discount_actor_id", sa.BigInteger(), nullable=True), sa.Column("discount_reason", sa.String(500), nullable=True), sa.Column("course_snapshot", sa.JSON(), nullable=True)]
    for column in invoice_columns:
        op.add_column("invoices", column)
    connection.execute(sa.text("UPDATE invoices i JOIN student_fee_assignments a ON a.id=i.student_fee_assignment_id JOIN enrollments e ON e.id=a.enrollment_id SET i.student_id=e.student_id,i.enrollment_id=e.id"))
    connection.execute(sa.text("UPDATE invoices i LEFT JOIN (SELECT invoice_id,SUM(amount) AS total FROM invoice_items WHERE status='active' GROUP BY invoice_id) x ON x.invoice_id=i.id SET i.charge_amount=COALESCE(x.total,0)"))
    op.alter_column("invoices", "student_id", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("invoices", "student_fee_assignment_id", existing_type=sa.BigInteger(), nullable=True)
    for column in invoice_columns:
        if column.server_default is not None:
            op.alter_column("invoices", column.name, existing_type=column.type, server_default=None)
    op.create_unique_constraint("uq_invoices_id_student", "invoices", ["id", "student_id"])
    op.create_index("ix_invoices_student_status", "invoices", ["student_id", "status", "id"])
    op.create_index("ix_invoices_enrollment_id", "invoices", ["enrollment_id"])
    for name, parent, columns, references in [
        ("fk_invoices_student_id", "users", ["student_id"], ["id"]),
        ("fk_invoices_enrollment_id", "enrollments", ["enrollment_id"], ["id"]),
        ("fk_invoices_episode_student", "enrollments", ["enrollment_id", "student_id"], ["id", "student_id"]),
        ("fk_invoices_discount_actor_id", "users", ["discount_actor_id"], ["id"])]:
        op.create_foreign_key(name, "invoices", parent, columns, references)
    op.create_check_constraint("ck_invoices_charge_amount", "invoices", "charge_amount IS NULL OR (charge_amount>=0 AND MOD(charge_amount,0.001)=0)")
    op.create_check_constraint("ck_invoices_discount_amount", "invoices", "discount_amount>=0 AND (charge_amount IS NULL OR discount_amount<=charge_amount) AND MOD(discount_amount,0.001)=0")
    op.add_column("payment_transactions", sa.Column("student_id", sa.BigInteger(), nullable=True))
    op.add_column("payment_transactions", sa.Column("movement_direction", sa.String(16, collation=CODE), nullable=False, server_default="in"))
    op.add_column("payment_transactions", sa.Column("operation_key", sa.String(36), nullable=True))
    connection.execute(sa.text("UPDATE payment_transactions p JOIN invoices i ON i.id=p.invoice_id SET p.student_id=i.student_id"))
    op.alter_column("payment_transactions", "student_id", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("payment_transactions", "invoice_id", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column("payment_transactions", "movement_direction", existing_type=sa.String(16, collation=CODE), server_default=None)
    op.create_foreign_key("fk_payment_transactions_student_id", "payment_transactions", "users", ["student_id"], ["id"])
    op.create_foreign_key("fk_payment_transactions_invoice_student", "payment_transactions", "invoices", ["invoice_id", "student_id"], ["id", "student_id"])
    op.create_index("ix_payment_transactions_student_id", "payment_transactions", ["student_id"])
    op.create_unique_constraint("operation_key", "payment_transactions", ["operation_key"])
    op.create_check_constraint("ck_payment_transactions_direction", "payment_transactions", "movement_direction IN ('in','out')")
    op.create_check_constraint("ck_payment_transactions_quantum", "payment_transactions", "MOD(amount,0.001)=0")
    op.create_table("financial_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("student_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), sa.ForeignKey("invoices.id"), nullable=True),
        sa.Column("payment_id", sa.BigInteger(), sa.ForeignKey("payment_transactions.id"), nullable=True),
        sa.Column("receipt_id", sa.BigInteger(), sa.ForeignKey("receipts.id"), nullable=True),
        sa.Column("action", sa.String(32), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("before_snapshot", sa.JSON(), nullable=True), sa.Column("after_snapshot", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("(invoice_id IS NOT NULL)+(payment_id IS NOT NULL)+(receipt_id IS NOT NULL)=1", name="ck_financial_revisions_one_target"),
        sa.CheckConstraint("version>0", name="ck_financial_revisions_version"),
        *[sa.UniqueConstraint(column, "version", name="uq_financial_revisions_" + kind + "_version") for kind, column in [("invoice", "invoice_id"), ("payment", "payment_id"), ("receipt", "receipt_id")]])
    op.create_index("ix_financial_revisions_student_time", "financial_revisions", ["student_id", "created_at", "id"])
    for kind in ("invoice", "payment", "receipt"):
        op.create_index("ix_financial_revisions_" + kind, "financial_revisions", [kind + "_id", "id"])


def downgrade():
    # This change cannot be reversed safely on a populated account database:
    # repeated episodes and general money have no faithful legacy representation.
    connection = op.get_bind()
    for table in ("financial_revisions", "enrollment_events", "enrollments", "invoices", "payment_transactions", "groups"):
        if connection.execute(sa.text("SELECT 1 FROM `" + table + "` LIMIT 1")).first():
            raise RuntimeError("Downgrade refused: preserve enrollment and financial history; restore a compatible snapshot instead.")
    if connection.execute(sa.text("SELECT 1 FROM courses WHERE price IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Downgrade refused: preserve configured course prices.")
    op.drop_table("financial_revisions")
    op.drop_table("enrollment_events")
    op.drop_table("enrollment_memberships")
    for name in ("fk_payment_transactions_invoice_student", "fk_payment_transactions_student_id"):
        op.drop_constraint(name, "payment_transactions", type_="foreignkey")
    for name in ("ck_payment_transactions_direction", "ck_payment_transactions_quantum"):
        op.drop_constraint(name, "payment_transactions", type_="check")
    op.drop_constraint("operation_key", "payment_transactions", type_="unique")
    op.drop_index("ix_payment_transactions_student_id", table_name="payment_transactions")
    for column in ("operation_key", "movement_direction", "student_id"):
        op.drop_column("payment_transactions", column)
    op.alter_column("payment_transactions", "invoice_id", existing_type=sa.BigInteger(), nullable=False)
    for name in ("fk_invoices_episode_student", "fk_invoices_enrollment_id", "fk_invoices_student_id", "fk_invoices_discount_actor_id"):
        op.drop_constraint(name, "invoices", type_="foreignkey")
    for name in ("ck_invoices_charge_amount", "ck_invoices_discount_amount"):
        op.drop_constraint(name, "invoices", type_="check")
    op.drop_constraint("uq_invoices_id_student", "invoices", type_="unique")
    op.drop_index("ix_invoices_student_status", table_name="invoices")
    op.drop_index("ix_invoices_enrollment_id", table_name="invoices")
    for column in ("student_id", "enrollment_id", "charge_amount", "discount_amount", "discount_kind", "discount_value", "discount_actor_id", "discount_reason", "course_snapshot"):
        op.drop_column("invoices", column)
    op.alter_column("invoices", "student_fee_assignment_id", existing_type=sa.BigInteger(), nullable=False)
    for name in ("ck_enrollments_active_marker", "ck_enrollments_version"):
        op.drop_constraint(name, "enrollments", type_="check")
    op.create_unique_constraint("uq_enrollments_student_group", "enrollments", ["student_id", "group_id"])
    for name in ("uq_enrollments_active_student_group", "uq_enrollments_id_student"):
        op.drop_constraint(name, "enrollments", type_="unique")
    for column in ("active_marker", "version", "withdrawn_at"):
        op.drop_column("enrollments", column)
    op.drop_column("groups", "study_starts_at")
    for name in ("ck_courses_price", "ck_courses_currency", "ck_courses_version"):
        op.drop_constraint(name, "courses", type_="check")
    for column in ("price", "currency_code", "version"):
        op.drop_column("courses", column)
