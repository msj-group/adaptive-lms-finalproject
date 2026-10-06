"""Scoped compatibility links to the general-account replacement workflows."""
from flask import abort, redirect, url_for
from app.models import Enrollment, EnrollmentMembership, Group, Invoice, PaymentTransaction, Receipt, StudentFeeAssignment, User


def enrollment_account(group_public_id, enrollment_public_id, assignment_public_id=None):
    group = Group.query.filter_by(public_id=group_public_id).first_or_404()
    episode = Enrollment.query.join(User, User.id == Enrollment.student_id).filter(
        Enrollment.public_id == enrollment_public_id, User.role == "student").first_or_404()
    if episode.group_id != group.id and not EnrollmentMembership.query.filter_by(
            enrollment_id=episode.id, group_id=group.id).first():
        abort(404)
    if assignment_public_id and not StudentFeeAssignment.query.filter_by(
            public_id=assignment_public_id, enrollment_id=episode.id).first():
        abort(404)
    return episode


def legacy_finance_link(group_public_id=None, enrollment_public_id=None, assignment_public_id=None,
                        invoice_public_id=None, payment_public_id=None, receipt_public_id=None,
                        target="record"):
    episode = enrollment_account(group_public_id, enrollment_public_id, assignment_public_id) if enrollment_public_id else None
    invoice = Invoice.query.filter_by(public_id=invoice_public_id).first_or_404() if invoice_public_id else None
    if invoice and episode and (invoice.enrollment_id != episode.id or invoice.student_id != episode.student_id):
        abort(404)
    payment = PaymentTransaction.query.filter_by(public_id=payment_public_id).first_or_404() if payment_public_id else None
    if payment and invoice and payment.invoice_id != invoice.id:
        abort(404)
    receipt = Receipt.query.filter_by(public_id=receipt_public_id).first_or_404() if receipt_public_id else None
    if receipt:
        movement = PaymentTransaction.query.filter_by(id=receipt.payment_transaction_id).first_or_404()
        if invoice and movement.invoice_id != invoice.id:
            abort(404)
        payment = movement
    student_id = payment.student_id if payment else invoice.student_id if invoice else episode.student_id if episode else None
    student = User.query.filter_by(id=student_id, role="student").first_or_404() if student_id else None
    if not student:
        return redirect(url_for("admin.student_accounts"), code=303)
    values = {"student_public_id": student.public_id}
    endpoint = "admin.student_financial_record"
    if target == "money":
        endpoint = "admin.student_money_new"
    elif target == "correct" and (payment or invoice):
        endpoint = "admin.student_document_correct"
        values.update(kind="payment" if payment else "invoice", document_public_id=(payment or invoice).public_id)
    elif receipt:
        endpoint = "admin.student_account_receipt"
        values["receipt_public_id"] = receipt.public_id
    return redirect(url_for(endpoint, **values), code=303)
