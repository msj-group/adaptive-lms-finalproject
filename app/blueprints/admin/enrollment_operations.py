"""Reviewable enrollment operations with signed academic/financial previews."""
import uuid
from decimal import Decimal
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from wtforms import BooleanField, DateField, HiddenField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import InputRequired, Length, Optional
from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models import Course, Enrollment, EnrollmentEvent, EnrollmentMembership, Group, Invoice, User
from app.security.decorators import roles_required
from app.services.enrollment_operations import change_enrollment, enrollment_preview
from app.services.general_finance import account_totals
from app.services.money import format_amount
from app.blueprints.admin.finance_registers import local_moment
from app.blueprints.admin.financial_http import _financial_response


class EnrollmentOperationForm(FlaskForm):
    operation_key = HiddenField(validators=[InputRequired(), Length(min=36, max=36)])
    preview = HiddenField(validators=[InputRequired(), Length(max=24000)])
    student_public_id = HiddenField(validators=[InputRequired(), Length(min=36, max=36)])
    target_public_id = HiddenField(validators=[Optional(), Length(max=36)])
    episode_public_id = HiddenField(validators=[Optional(), Length(max=36)])
    discount_kind = SelectField("Discount", default="none", choices=[("none", "No discount"), ("amount", "LYD amount"), ("percentage", "Percentage")])
    discount_value = StringField("Discount value", default="0", validators=[InputRequired(), Length(max=32)])
    discount_reason = TextAreaField("Discount reason (optional)", validators=[Optional(), Length(max=500)])
    initial_amount = StringField("Initial collection (optional, LYD)", validators=[Optional(), Length(max=32)])
    initial_method = SelectField("Initial collection method", default="cash", choices=[("cash", "Cash"), ("bank_transfer", "Bank transfer")])
    initial_bank_reference = StringField("Bank reference", validators=[Optional(), Length(max=64)])
    initial_bank_date = DateField("Transfer date", validators=[Optional()])
    initial_confirmed = BooleanField("Initial bank transfer has been verified")
    cancel_obligation = BooleanField("Cancel this enrollment's full net obligation after discount (does not return cash)")
    submit = SubmitField("Save enrollment operation")


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="repair.enrollment.preview.v1")


@admin_bp.route("/groups/<group_public_id>/enrollment-operations/<action>", methods=["GET", "POST"])
@_financial_response
@roles_required("administrator")
def enrollment_operation(group_public_id, action):
    if action not in {"enroll", "withdraw", "transfer", "correct_course"}:
        abort(404)
    source = Group.query.filter_by(public_id=group_public_id).first_or_404()
    form = EnrollmentOperationForm()
    if action in {"withdraw", "transfer"}:
        form.discount_value.validators = [Optional()]
    episode_public_id = form.episode_public_id.data if request.method == "POST" else request.args.get("episode", "")
    episode = None
    if action != "enroll":
        episode = Enrollment.query.join(User, Enrollment.student_id == User.id).filter(
            Enrollment.public_id == episode_public_id, User.role == "student").first_or_404()
        if episode.group_id != source.id:
            # A transferred episode has moved. Only a replay of its original
            # signed POST can use the old nested URL; never a fresh old-group GET.
            replay = EnrollmentEvent.query.filter_by(operation_key=form.operation_key.data).first() if request.method == "POST" else None
            if (replay is None or replay.enrollment_id != episode.id or replay.action != action
                    or not EnrollmentMembership.query.filter_by(enrollment_id=episode.id, group_id=source.id).first()):
                abort(404)
    student_public_id = episode.student.public_id if episode else (
        form.student_public_id.data if request.method == "POST" else request.args.get("student", ""))
    student = User.query.filter_by(public_id=student_public_id, role="student").first() if student_public_id else None
    if student_public_id and student is None:
        abort(404)
    target_public_id = source.public_id if action == "enroll" else (
        form.target_public_id.data if request.method == "POST" else request.args.get("target", ""))
    target = Group.query.filter_by(public_id=target_public_id).first() if target_public_id else None
    if target_public_id and target is None:
        abort(404)
    invoice = Invoice.query.filter_by(enrollment_id=episode.id, status="issued").filter(Invoice.deleted_at.is_(None)).order_by(Invoice.id).first() if episode else None
    ready = student is not None and (action == "withdraw" or target is not None)
    if request.method == "GET" and ready:
        form.operation_key.data = str(uuid.uuid4())
        form.student_public_id.data, form.target_public_id.data, form.episode_public_id.data = student.public_id, target.public_id if target else "", episode_public_id
        form.preview.data = _serializer().dumps({"action": action, "student": student.public_id,
            "source": enrollment_preview(source, source.course, episode, invoice) if episode else None,
            "target": enrollment_preview(target, target.course) if target else None})
    if form.validate_on_submit():
        try:
            payload = _serializer().loads(form.preview.data)
            if not isinstance(payload, dict) or payload.get("action") != action or payload.get("student") != student_public_id:
                raise ValueError("Reload and review the enrollment preview.")
            episode = change_enrollment(action=action, actor_id=current_user.id, student_public_id=student_public_id,
                source_public_id=source.public_id if action != "enroll" else None,
                target_public_id=target_public_id or None, episode_public_id=episode_public_id or None,
                expected_source=payload.get("source"), expected_target=payload.get("target"), operation_key=form.operation_key.data,
                discount_kind=form.discount_kind.data, discount_value=form.discount_value.data,
                discount_reason=form.discount_reason.data or None, initial_amount=form.initial_amount.data or None,
                initial_method=form.initial_method.data, initial_bank_reference=form.initial_bank_reference.data or None,
                initial_bank_date=form.initial_bank_date.data, initial_confirmed=form.initial_confirmed.data,
                cancel_obligation=form.cancel_obligation.data)
            db.session.commit()
        except (BadSignature, ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else "This operation could not be saved. Reload its preview and try again.", "danger")
            return redirect(url_for("admin.enrollment_operation", group_public_id=group_public_id, action=action,
                episode=episode_public_id, student=student_public_id, target=target_public_id))
        flash("Enrollment operation saved with its academic and financial history.", "success")
        return redirect(url_for("admin.group_members", group_public_id=episode.group.public_id))
    search = request.args.get("q", "").strip()[:100]
    student_query = User.query.filter_by(role="student", status="active")
    if search:
        student_query = student_query.filter(or_(User.full_name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%")))
    targets = Group.query.filter(Group.status == "active", Group.id != source.id)
    if action == "transfer":
        targets = targets.filter(Group.course_id == source.course_id)
    elif action == "correct_course":
        targets = targets.filter(Group.course_id != source.course_id)
    if search and action != "enroll":
        targets = targets.filter(Group.name.ilike(f"%{search}%"))
    return render_template("admin/groups/enrollment_operation.html", form=form, action=action, source=source,
        student=student, target=target, episode=episode, invoice=invoice, ready=ready, search=search,
        students=student_query.order_by(User.full_name, User.id).limit(50).all() if action == "enroll" and not ready else [],
        targets=targets.order_by(Group.name, Group.id).limit(50).all() if action in {"transfer", "correct_course"} and not ready else [],
        facts=account_totals(student.id) if student else None, money=format_amount)


@admin_bp.get("/student-accounts/<student_public_id>/enrollment-history")
@_financial_response
@roles_required("administrator")
def student_enrollment_history(student_public_id):
    student = User.query.filter_by(public_id=student_public_id, role="student").first_or_404()
    events = db.session.query(EnrollmentEvent, User.full_name).join(Enrollment, Enrollment.id == EnrollmentEvent.enrollment_id).join(
        User, User.id == EnrollmentEvent.actor_id).filter(Enrollment.student_id == student.id).order_by(EnrollmentEvent.id.desc()).paginate(
        page=max(1, request.args.get("page", 1, type=int)), per_page=50, error_out=False)
    entries = []
    for event, actor in events.items:
        before, after = event.before_snapshot or {}, event.after_snapshot
        details = [("Group", (before.get("group") or {}).get("name"), (after.get("group") or {}).get("name")),
            ("Course", (before.get("course") or {}).get("title"), (after.get("course") or {}).get("title")),
            ("Enrollment", (before.get("episode") or before).get("status"), (after.get("episode") or {}).get("status"))]
        old_invoice, new_invoice = before.get("invoice") or {}, after.get("invoice") or {}
        def obligation(snapshot):
            if not snapshot:
                return "No invoice"
            net = Decimal(snapshot.get("charge_amount") or "0") - Decimal(snapshot.get("discount_amount") or "0")
            return f"{snapshot.get('invoice_number')} · {format_amount(net)} LYD · {snapshot.get('status')}"
        details.append(("Invoice", obligation(old_invoice), obligation(new_invoice)))
        previous = after.get("previous_invoice")
        if previous:
            details.append(("Previous obligation", obligation(old_invoice), obligation(previous)))
        collection = after.get("initial_collection")
        entries.append({"at": local_moment(event.created_at), "actor": actor, "action": event.action.replace("_", " ").title(),
            "details": details, "collection": f"{format_amount(Decimal(collection['amount']))} LYD · {collection['status']}" if collection else None})
    return render_template("admin/groups/enrollment_history.html", student=student, events=events, entries=entries)
