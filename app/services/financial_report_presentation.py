"""Read-only Ledger Studio links and summaries over the returned report page.

Presentation metadata never defines balances, export columns or permissions.
Every linked native GET rechecks the Student and nested document ownership.
"""
from decimal import Decimal
from uuid import UUID

from flask import request, url_for

from app.models import User


def public_identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def document_link(student_public_id, kind, document_public_id):
    student = public_identifier(student_public_id)
    document = public_identifier(document_public_id)
    if not student or not document or kind not in {"invoice", "payment", "receipt"}:
        return ""
    if kind == "receipt":
        return url_for("admin.student_account_receipt", student_public_id=student, receipt_public_id=document)
    return url_for("admin.student_document_detail", student_public_id=student, kind=kind, document_public_id=document)


def revision_document_link(revision, student_public_id):
    kind = "invoice" if revision.invoice_id else "receipt" if revision.receipt_id else "payment"
    return document_link(student_public_id, kind, (revision.after_snapshot or {}).get("public_id"))


def is_report_reader():
    return request.method == "GET" and request.headers.get("X-Ledger-Reader") == "1"


def report_view(report):
    """Only this page's returned rows contribute to the three summary cards."""
    rows = [dict(row) for section in report.sections for row in section.rows]
    student_ids = {row["student_id"] for row in rows
                   if row.get("student_id") is not None and not row.get("student_public_id")}
    identities = dict(User.query.with_entities(User.id, User.public_id).filter(
        User.id.in_(student_ids), User.role == "student").all()) if student_ids else {}
    for row in rows:
        student = public_identifier(row.get("student_public_id") or identities.get(row.get("student_id")))
        row["account_url"] = url_for("admin.student_financial_record", student_public_id=student) if student else ""
        row["history_url"] = url_for("admin.student_financial_history", student_public_id=student) if student else ""
        row["document_url"] = document_link(student, row.get("document_kind"), row.get("document_public_id"))
        row["initials"] = "".join(word[0] for word in str(row.get("student", "")).split()[:2]).upper() or "?"
        if row.get("state") is not None:
            row["state_label"] = str(row["state"]).replace("_", " ").title()
        if report.key == "accounts":
            row["position"] = "Student owes" if row["balance"] > 0 else "Student credit" if row["balance"] < 0 else "Balanced"
    if report.key == "accounts":
        owed = [row for row in rows if row["balance"] > 0]
        credits = [row for row in rows if row["balance"] < 0]
        metrics = (
            {"label": "Owed to the centre", "value": sum((row["balance"] for row in owed), Decimal(0)), "count": len(owed), "money": True, "icon": "arrow-up"},
            {"label": "Student credit", "value": -sum((row["balance"] for row in credits), Decimal(0)), "count": len(credits), "money": True, "icon": "wallet"},
            {"label": "Balanced accounts", "value": sum(row["balance"] == 0 for row in rows), "money": False, "icon": "check"},
        )
    elif report.key == "exceptions":
        metrics = tuple({"label": label, "value": value, "money": False, "icon": icon} for label, value, icon in (
            ("Records shown", len(rows), "file"),
            ("Pending records", sum(row["state"] == "pending" for row in rows), "clock"),
            ("Rejected records", sum(row["state"] == "rejected" for row in rows), "info")))
    else:
        metrics = tuple({"label": label, "value": value, "money": False, "icon": icon} for label, value, icon in (
            ("Records shown", len(rows), "file"),
            ("Students shown", len({row.get("student_id") for row in rows}), "users"),
            ("Reasons recorded", sum(bool(row.get("reason")) for row in rows), "layers")))
    return {"rows": rows, "metrics": metrics}
