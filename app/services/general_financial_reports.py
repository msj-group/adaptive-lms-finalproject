"""Current general accounts and chronological revisions, read in one snapshot."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from flask import current_app
from sqlalchemy import exists, select, union_all, literal
from app.extensions import db
from app.models import FinancialRevision, Invoice, PaymentIntent, PaymentProviderEvent, PaymentTransaction, User
from app.services.general_finance_queries import balances_for_students
from app.services.financial_report_types import Column, FinancialReport, ReportSection, AMOUNT, TEXT, MOMENT, NOTICE
from app.services.schedule_occurrences import from_app_local, to_app_local, utc_reference_now

PAGE_SIZE = 50
EXPORT_LIMIT = 5000


def _bounds(args):
    start, end = args.get("start", ""), args.get("end", "")
    if bool(start) != bool(end):
        raise ValueError("Enter both dates or leave both empty.")
    if not start:
        return None, None
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last or first.year < 2000 or last.year > 9998:
            raise ValueError
        zone = current_app.config["APP_TIMEZONE"]
        return (from_app_local(zone, datetime.combine(first, time.min)),
                from_app_local(zone, datetime.combine(last + timedelta(days=1), time.min)))
    except (ValueError, OverflowError):
        raise ValueError("Enter valid dates in order (YYYY-MM-DD).") from None


def revision_view(revision, actor_name):
    before, after = revision.before_snapshot or {}, revision.after_snapshot
    kind = "Invoice" if revision.invoice_id else "Receipt" if revision.receipt_id else "Money movement"
    def net(values):
        if not values:
            return None
        if kind == "Invoice":
            return Decimal(values.get("charge_amount") or "0") - Decimal(values.get("discount_amount") or "0")
        if kind == "Receipt":
            return Decimal((values.get("snapshot") or {}).get("amount") or "0")
        return Decimal(values.get("amount") or "0")
    def state(values):
        return "Deleted" if values.get("deleted_at") else str(values.get("status") or "").replace("_", " ").title()
    number = after.get("invoice_number") or after.get("receipt_number") or "Money movement"
    return {"moment": to_app_local(current_app.config["APP_TIMEZONE"], revision.created_at),
        "document": number, "kind": kind, "action": revision.action.replace("_", " ").title(),
        "before_amount": net(before), "after_amount": net(after), "before_state": state(before), "after_state": state(after),
        "actor": actor_name or "Verified payment provider", "reason": revision.reason or "",
        "student_id": revision.student_id}


def build_general_report(key, args, *, export=False):
    zone, moment = current_app.config["APP_TIMEZONE"], utc_reference_now()
    page = max(1, args.get("page", 1, type=int))
    limit, offset = (EXPORT_LIMIT + 1, 0) if export else (PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
    rows, columns, title = [], (), ""
    if key == "accounts":
        if args.get("start") or args.get("end"):
            raise ValueError("Current accounts show today's corrected state. Use history for dates.")
        query = User.query.filter_by(role="student")
        search = args.get("q", "").strip()[:100]
        if search:
            query = query.filter(User.full_name.ilike(f"%{search}%"))
        students = query.order_by(User.full_name, User.id).offset(offset).limit(limit).all()
        balances = balances_for_students(row.id for row in students)
        rows = [{"student": row.full_name, "balance": balances[row.id]["balance"]} for row in students]
        columns = (Column("student", "Student", TEXT), Column("balance", "Signed balance (LYD)", AMOUNT))
        title = "Current Student accounts"
        notes = ("Positive: student owes the center. Negative: center owes the student. Zero: balanced.",
                 "Collections are general-account money; an invoice reference does not allocate it.")
    elif key == "exceptions":
        if args.get("start") or args.get("end"):
            raise ValueError("Exceptions show current unresolved records and take no date range.")
        manual = select(User.full_name.label("student"),literal("Bank transfer").label("kind"),PaymentTransaction.amount.label("amount"),
            PaymentTransaction.status.label("state"),PaymentTransaction.recorded_at.label("moment"),literal(1).label("source"),PaymentTransaction.id.label("row_id")).join(User,User.id==PaymentTransaction.student_id).where(
                User.role=="student",PaymentTransaction.deleted_at.is_(None),PaymentTransaction.status.in_(["pending","rejected"]))
        intents = select(User.full_name,literal("Sandbox intent"),PaymentIntent.amount,PaymentIntent.status,PaymentIntent.created_at,literal(2),PaymentIntent.id).join(
            Invoice,Invoice.id==PaymentIntent.invoice_id).join(User,User.id==Invoice.student_id).where(User.role=="student",PaymentIntent.status.in_(["pending","provider_succeeded"]))
        events = select(User.full_name,literal("Provider reconciliation"),PaymentProviderEvent.amount,PaymentProviderEvent.outcome,PaymentProviderEvent.received_at,literal(3),PaymentProviderEvent.id).join(
            PaymentIntent,PaymentIntent.id==PaymentProviderEvent.payment_intent_id).join(Invoice,Invoice.id==PaymentIntent.invoice_id).join(User,User.id==Invoice.student_id).where(
                User.role=="student",PaymentProviderEvent.outcome=="reconciliation_required")
        table = union_all(manual,intents,events).subquery()
        rows = [dict(row, moment=to_app_local(zone,row["moment"])) for row in db.session.execute(select(table).order_by(
            table.c.moment.desc(),table.c.source,table.c.row_id.desc()).offset(offset).limit(limit)).mappings().all()]
        columns = tuple(Column(*value) for value in (("student","Student",TEXT),("kind","Record",TEXT),("amount","Amount (LYD)",AMOUNT),("state","State",TEXT),("moment","Recorded",MOMENT)))
        title,notes = "Current financial exceptions", ("Pending and rejected transfers do not count as confirmed money. Provider exceptions retain their verified evidence.",)
    else:
        lower, upper = _bounds(args)
        query = db.session.query(FinancialRevision, User.full_name).outerjoin(User, User.id == FinancialRevision.actor_id)
        if lower:
            query = query.filter(FinancialRevision.created_at >= lower, FinancialRevision.created_at < upper)
        student = args.get("student", "")
        if student:
            owner = User.query.filter_by(public_id=student, role="student").first()
            if owner is None:
                raise ValueError("Select an existing Student account.")
            query = query.filter(FinancialRevision.student_id == owner.id)
        revisions = query.order_by(FinancialRevision.created_at.desc(), FinancialRevision.id.desc()).offset(offset).limit(limit).all()
        student_names = dict(db.session.query(User.id, User.full_name).filter(User.id.in_(
            {revision.student_id for revision, _actor in revisions})).all())
        rows = [dict(revision_view(revision, actor), student=student_names.get(revision.student_id, "Student")) for revision, actor in revisions]
        columns = tuple(Column(*value) for value in (("moment","Recorded",MOMENT),("student","Student",TEXT),
            ("document","Document",TEXT),("action","Action",TEXT),("before_amount","Before (LYD)",AMOUNT),
            ("after_amount","After (LYD)",AMOUNT),("before_state","Previous state",TEXT),("after_state","Current state",TEXT),
            ("actor","Operator",TEXT),("reason","Reason",TEXT)))
        title = "Financial movements and correction history"
        notes = ("Dates select the time each operation or correction was recorded; old entries remain after corrections.",
                 "This history begins with the repair's audited operations. Earlier records retain their original provider/payment audit.",
                 "Deleting a recorded collection is a correction; it does not claim cash was returned. A payout records a real return.")
    if export and len(rows) > EXPORT_LIMIT:
        raise ValueError("This export exceeds 5,000 rows. Narrow the dates or Student selection; no partial export was produced.")
    has_next = not export and len(rows) > PAGE_SIZE
    if not export:
        rows = rows[:PAGE_SIZE]
    report = FinancialReport(key=key, title=title,
        filters=tuple((label, args.get(field)) for field, label in (("start","From"),("end","Through"),("q","Student name")) if args.get(field)),
        generated_local=to_app_local(zone, moment), tz_name=zone, currency_code="LYD", notice=NOTICE, notes=notes,
        sections=(ReportSection(key, title, "", columns, tuple(rows), {}, "No matching records.", False),))
    return report, page, has_next
