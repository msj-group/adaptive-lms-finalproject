"""Verified provider money goes to the general Student account atomically."""
import time
import uuid
from sqlalchemy.exc import IntegrityError, OperationalError
from app.extensions import db
from app.models import Invoice, PaymentIntent, PaymentProviderEvent, PaymentTransaction, Receipt, User
from app.models.receipt import receipt_moment_text
from app.models.submission_feedback import whole_second_utc
from app.services.financial_history import record_document_revision
from app.services.payment_providers import PaymentProviderError, EVENT_PAYMENT_SUCCEEDED, EVENT_PAYMENT_FAILED
from app.services.financial_numbering import allocate_receipt_number, lock_receipt_number_sequence
from app.services.schedule_occurrences import to_app_local

ACTIVE = {"pending", "provider_succeeded"}


def process_general_webhook(provider, raw_body, headers, *, tz_name, clock=whole_second_utc, epoch=time.time):
    from app.services.payment_webhooks import WebhookRejected, WebhookRetry, WebhookResult
    received_at = clock()
    try:
        event = provider.normalize_event(provider.verify_webhook(raw_body, headers, now=int(epoch())))
    except PaymentProviderError:
        raise WebhookRejected("The event is not authentic, fresh and exact") from None
    try:
        discovery = db.session.query(PaymentIntent.id, Invoice.student_id).join(Invoice, Invoice.id == PaymentIntent.invoice_id).filter(
            PaymentIntent.provider == provider.name, PaymentIntent.provider_reference == event.provider_reference).first()
        if discovery is None:
            raise WebhookRejected("No intent holds this reference")
        intent_id, student_id = discovery
        db.session.rollback()
        student = User.query.filter_by(id=student_id, role="student").populate_existing().with_for_update().first()
        if student is None:
            raise WebhookRetry("Student account context is unavailable")
        # Every finance writer owns the Student mutex before documents.
        invoice_id = db.session.query(PaymentIntent.invoice_id).filter_by(id=intent_id).scalar()
        invoice = Invoice.query.filter_by(id=invoice_id, student_id=student_id).populate_existing().with_for_update().first()
        intent = PaymentIntent.query.filter_by(id=intent_id, invoice_id=invoice_id).populate_existing().with_for_update().first()
        if invoice is None or intent is None or intent.provider != provider.name or intent.provider_reference != event.provider_reference:
            raise WebhookRetry("Payment context changed")
        stored = PaymentProviderEvent.query.filter_by(provider=provider.name, provider_event_id=event.event_id).first()
        if stored:
            if stored.payload_digest != event.payload_digest:
                raise WebhookRejected("An event identity was reused for different bytes")
            result = WebhookResult(stored.outcome, stored.public_id, None, True)
            db.session.rollback()
            return result
        corresponds = intent.amount == event.amount and intent.currency_code == event.currency_code
        outcome = "reconciliation_required"
        if corresponds and event.event_type == EVENT_PAYMENT_FAILED:
            outcome = "failed" if intent.status in ACTIVE else "duplicate" if intent.status == "provider_failed" else "ignored_terminal" if intent.status == "cancelled" else outcome
        elif corresponds and event.event_type == EVENT_PAYMENT_SUCCEEDED:
            outcome = "confirmed" if intent.status in ACTIVE else "duplicate" if intent.status == "confirmed" else outcome
        moment = clock()
        inbox = PaymentProviderEvent(public_id=str(uuid.uuid4()), provider=provider.name, provider_event_id=event.event_id,
            payment_intent_id=intent.id, event_type=event.event_type, amount=event.amount, currency_code=event.currency_code,
            provider_occurred_at=event.occurred_at, received_at=received_at, processed_at=moment,
            created_at=moment, payload_digest=event.payload_digest, outcome=outcome)
        number = None
        if outcome == "confirmed":
            # Context only: withdrawal, a changed invoice or another collection
            # must not erase authenticated evidence of money actually received.
            payment = PaymentTransaction(student_id=student.id, invoice_id=invoice.id, movement_direction="in", kind="collection",
                method="online", amount=intent.amount, currency_code="LYD", status="confirmed", payment_intent_id=intent.id,
                recorded_at=moment, confirmed_at=moment, recorded_by_id=None, confirmed_by_id=None,
                version=1, created_at=moment, updated_at=moment)
            db.session.add(payment)
            db.session.flush()
            record_document_revision(payment, student.id, None, "create", None, service_principal="verified_payment_provider")
            inbox.payment_transaction_id = payment.id
            db.session.add(inbox)
            db.session.flush()
            year = to_app_local(tz_name, moment).year
            number = allocate_receipt_number(lock_receipt_number_sequence(year, moment), moment)
            if number is None:
                raise WebhookRetry("The annual receipt sequence is exhausted")
            receipt = Receipt(public_id=str(uuid.uuid4()), payment_transaction_id=payment.id, receipt_number=number,
                status="issued", issued_by_id=None, issued_at=moment, created_at=moment, updated_at=moment, version=1)
            receipt.snapshot = {"schema":"repair.student-receipt.online.v1", "receipt_public_id":receipt.public_id,
                "receipt_number":number, "payment_public_id":payment.public_id, "student_name":student.full_name,
                "invoice_public_id":invoice.public_id, "invoice_number":invoice.invoice_number, "method":"online",
                "amount":format(payment.amount,".4f"), "currency_code":"LYD", "movement_direction":"in",
                "confirmed_at":receipt_moment_text(moment), "confirmed_by_name":"Verified payment provider",
                "payment_intent_public_id":intent.public_id, "provider_event_public_id":inbox.public_id}
            db.session.add(receipt)
            db.session.flush()
            record_document_revision(receipt, student.id, None, "create", None, service_principal="verified_payment_provider")
        else:
            db.session.add(inbox)
        if outcome in {"confirmed", "failed"}:
            intent.status = "confirmed" if outcome == "confirmed" else "provider_failed"
            intent.terminal_at, intent.updated_at, intent.version = moment, moment, intent.version + 1
        db.session.commit()
        return WebhookResult(outcome, inbox.public_id, number, False)
    except (WebhookRejected, WebhookRetry):
        db.session.rollback()
        raise
    except (IntegrityError, OperationalError):
        db.session.rollback()
        raise WebhookRetry("A database conflict interrupted delivery") from None
    except Exception:
        db.session.rollback()
        raise
