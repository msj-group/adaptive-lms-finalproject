"""Append-only corrections for existing financial documents."""
from sqlalchemy import event
from app.models.code_types import CODE_COLLATION
from app.extensions import db
from app.models.submission_feedback import whole_second_utc


class FinancialRevision(db.Model):
    __tablename__ = "financial_revisions"
    __table_args__ = (
        db.UniqueConstraint("invoice_id", "version", name="uq_financial_revisions_invoice_version"),
        db.UniqueConstraint("payment_id", "version", name="uq_financial_revisions_payment_version"),
        db.UniqueConstraint("receipt_id", "version", name="uq_financial_revisions_receipt_version"),
        db.CheckConstraint("version > 0", name="ck_financial_revisions_version"),
        db.CheckConstraint("(actor_id IS NOT NULL AND service_principal IS NULL) OR (actor_id IS NULL AND service_principal IS NOT NULL AND service_principal = 'verified_payment_provider')", name="ck_financial_revisions_actor"),
        db.CheckConstraint("(invoice_id IS NOT NULL) + (payment_id IS NOT NULL) + (receipt_id IS NOT NULL) = 1", name="ck_financial_revisions_one_target"),
        db.Index("ix_financial_revisions_student_time", "student_id", "created_at", "id"),
        db.Index("ix_financial_revisions_invoice", "invoice_id", "id"),
        db.Index("ix_financial_revisions_payment", "payment_id", "id"),
        db.Index("ix_financial_revisions_receipt", "receipt_id", "id"),
    )
    id = db.Column(db.BigInteger, primary_key=True)
    student_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    actor_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=True)
    service_principal = db.Column(db.String(32, collation=CODE_COLLATION), nullable=True)
    invoice_id = db.Column(db.BigInteger, db.ForeignKey("invoices.id"), nullable=True)
    payment_id = db.Column(db.BigInteger, db.ForeignKey("payment_transactions.id"), nullable=True)
    receipt_id = db.Column(db.BigInteger, db.ForeignKey("receipts.id"), nullable=True)
    action = db.Column(db.String(32), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(500), nullable=True)
    before_snapshot = db.Column(db.JSON, nullable=True)
    after_snapshot = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)


@event.listens_for(FinancialRevision, "before_update")
@event.listens_for(FinancialRevision, "before_delete")
def _preserve_financial_revision(_mapper, _connection, _target):
    raise ValueError("Financial revisions are append-only")
