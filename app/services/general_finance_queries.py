"""Bounded account reads; invoice references never allocate collected money."""
from decimal import Decimal
from sqlalchemy import case, func
from app.extensions import db
from app.models import Invoice, PaymentTransaction


def balances_for_students(student_ids):
    ids = list(student_ids)
    result = {student_id: {"obligations": Decimal(0), "collections": Decimal(0), "payouts": Decimal(0)} for student_id in ids}
    if not ids:
        return result
    for student_id, amount in db.session.query(Invoice.student_id, func.sum(Invoice.charge_amount - Invoice.discount_amount)).filter(
            Invoice.student_id.in_(ids), Invoice.status == "issued", Invoice.deleted_at.is_(None)).group_by(Invoice.student_id):
        result[student_id]["obligations"] = Decimal(amount or 0)
    effective = case((PaymentTransaction.kind == "reversal", -PaymentTransaction.amount), else_=PaymentTransaction.amount)
    for student_id, direction, amount in db.session.query(PaymentTransaction.student_id, PaymentTransaction.movement_direction, func.sum(effective)).filter(
            PaymentTransaction.student_id.in_(ids), PaymentTransaction.status == "confirmed",
            PaymentTransaction.deleted_at.is_(None)).group_by(PaymentTransaction.student_id, PaymentTransaction.movement_direction):
        result[student_id]["collections" if direction == "in" else "payouts"] = Decimal(amount or 0)
    for facts in result.values():
        facts["balance"] = facts["obligations"] - facts["collections"] + facts["payouts"]
    return result
