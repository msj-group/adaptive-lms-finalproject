"""Administrator correction of finalized attendance, including preserved history."""
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError, OperationalError
from wtforms import HiddenField, SelectField, SubmitField, TextAreaField
from wtforms.validators import InputRequired, Length, Optional
from app.blueprints.admin import admin_bp
from app.blueprints.admin.financial_http import _financial_response
from app.blueprints.admin.finance_registers import local_moment
from app.extensions import db
from app.models import AcademicRevision, AttendanceRecord, AttendanceSession, Group, User
from app.security.decorators import roles_required
from app.services.attendance_corrections import attendance_snapshot, correct_finalized_attendance


class AttendanceCorrectionForm(FlaskForm):
    snapshot = HiddenField(validators=[InputRequired(), Length(max=10000)])
    status = SelectField("Attendance mark", choices=[("present", "Present"), ("absent", "Absent"),
                        ("late", "Late"), ("excused", "Excused")], validators=[InputRequired()])
    note = TextAreaField("Private staff note", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Save correction")


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="repair.attendance.correction.v1")


@admin_bp.route("/groups/<group_public_id>/attendance/<session_public_id>/records/<record_public_id>/correct", methods=["GET", "POST"])
@_financial_response
@roles_required("administrator")
def attendance_record_correct(group_public_id, session_public_id, record_public_id):
    group = Group.query.filter_by(public_id=group_public_id).first_or_404()
    session = AttendanceSession.query.filter_by(public_id=session_public_id, group_id=group.id).first_or_404()
    record = AttendanceRecord.query.join(User, User.id == AttendanceRecord.student_id).filter(
        AttendanceRecord.public_id == record_public_id, AttendanceRecord.attendance_session_id == session.id,
        User.role == "student").first_or_404()
    student = db.session.get(User, record.student_id)
    back = url_for("admin.attendance_session_detail", group_public_id=group.public_id, session_public_id=session.public_id)
    if not session.is_finalized():
        flash("Draft attendance must be recorded by the group's teacher.", "warning")
        return redirect(back)
    form = AttendanceCorrectionForm()
    if request.method == "GET":
        form.snapshot.data = _serializer().dumps(attendance_snapshot(record))
        form.status.data, form.note.data = record.status, record.note
    if form.validate_on_submit():
        try:
            expected = _serializer().loads(form.snapshot.data)
            changed = correct_finalized_attendance(group_public_id, session_public_id, record_public_id,
                                                   current_user.id, expected, form.status.data, form.note.data or "")
            db.session.commit()
        except (BadSignature, ValueError, IntegrityError, OperationalError):
            db.session.rollback()
            flash("The correction could not be saved. Reload and review the current mark.", "danger")
            return redirect(url_for("admin.attendance_record_correct", group_public_id=group_public_id,
                                   session_public_id=session_public_id, record_public_id=record_public_id))
        flash("Attendance corrected; its previous values are preserved." if changed else "No attendance values changed.", "success")
        return redirect(back)
    history = db.session.query(AcademicRevision, User.full_name).join(User, User.id == AcademicRevision.actor_id).filter(
        AcademicRevision.attendance_record_id == record.id).order_by(AcademicRevision.id.desc()).paginate(
        page=max(1, request.args.get("page", 1, type=int)), per_page=20, error_out=False)
    return render_template("admin/attendance/correct.html", group=group, session=session, student=student,
                           form=form, history=history, local=local_moment, back=back)
