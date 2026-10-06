"""Full document snapshots and narrowly authorized, append-only revisions."""
from datetime import date, datetime
from decimal import Decimal
import copy
from sqlalchemy import inspect
from app.extensions import db
from app.models.financial_revision import FinancialRevision


def _value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return copy.deepcopy(value)


def document_snapshot(target):
    return {column.key: _value(getattr(target, column.key)) for column in target.__table__.columns}


def record_document_revision(target, student_id, actor_id, action, before, reason=None, *, service_principal=None):
    keys = {"invoices": "invoice_id", "payment_transactions": "payment_id", "receipts": "receipt_id"}
    if (actor_id is None) != (service_principal == "verified_payment_provider"):
        raise ValueError("A financial revision must identify its actual operator or verified provider")
    revision = FinancialRevision(student_id=student_id, actor_id=actor_id, service_principal=service_principal, action=action,
        version=target.version, reason=reason, before_snapshot=before,
        after_snapshot=document_snapshot(target), **{keys[target.__tablename__]: target.id})
    db.session.add(revision)
    return revision


def revision_authorizes_update(connection, target):
    """Only a matching pending revision may supersede the legacy edit guard."""
    state = inspect(target)
    session = state.session
    if session is None:
        return False
    key = {"invoices": "invoice_id", "payment_transactions": "payment_id", "receipts": "receipt_id"}[target.__tablename__]
    revisions = [row for row in session.new if isinstance(row, FinancialRevision) and getattr(row, key) == target.id]
    if not revisions:
        return False
    if len(revisions) != 1:
        raise ValueError("Each document mutation needs exactly one revision")
    row = connection.execute(target.__table__.select().where(target.__table__.c.id == target.id)).mappings().one()
    revision = revisions[0]
    before = {key: _value(value) for key, value in row.items()}
    if before.get("deleted_at") is not None or revision.before_snapshot != before or revision.after_snapshot != document_snapshot(target):
        raise ValueError("A document revision must preserve exact current old/new facts")
    if revision.version != target.version or target.version != row["version"] + 1:
        raise ValueError("A document correction advances its version exactly once")
    immutable = {"id", "public_id", "created_at"}
    if key == "invoice_id":
        immutable |= {"student_id", "enrollment_id", "invoice_number", "issued_at", "issued_by_id", "student_fee_assignment_id", "course_snapshot", "currency_code"}
    elif key == "payment_id":
        immutable |= {"student_id", "invoice_id", "kind", "movement_direction", "operation_key", "payment_intent_id",
                      "recorded_at", "recorded_by_id", "reversal_of_payment_transaction_id"}
    else:
        immutable |= {"payment_transaction_id", "receipt_number", "issued_at", "issued_by_id"}
    if any(before.get(field) != revision.after_snapshot.get(field) for field in immutable):
        raise ValueError("A correction cannot change financial document identity")
    return True
