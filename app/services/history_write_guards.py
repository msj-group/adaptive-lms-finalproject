"""ORM bulk mutation must not bypass append-only history or episode guards."""
from sqlalchemy import event
from sqlalchemy.orm import Session

PROTECTED = frozenset({"attempt_submission_receipts", "account_revisions", "scheduling_revisions", "financial_revisions", "academic_revisions",
    "enrollment_events", "enrollment_memberships", "enrollments", "invoices", "payment_transactions", "receipts",
    "grade_records", "attendance_records", "submission_feedback", "speaking_feedback", "lesson_progress", "quiz_attempts", "submissions", "speaking_submissions",
    "research_audit_events", "research_export_sessions", "research_data_gaps", "message_changes", "message_thread_clears"})


@event.listens_for(Session, "do_orm_execute")
def reject_bulk_history_mutation(execution):
    if not (execution.is_update or execution.is_delete):
        return
    table = getattr(execution.statement, "table", None)
    if table is not None and table.name in PROTECTED:
        raise ValueError("Use the authorized record operation; bulk history mutation is forbidden")
