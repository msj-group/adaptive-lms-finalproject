"""Private v2 quality/partition review; never expose reserved test labels."""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError
from wtforms import HiddenField, SelectField, SubmitField
from wtforms.validators import DataRequired
from app.i18n import LocalizedFlaskForm
from app.blueprints.research import research_bp
from app.extensions import db
from app.models import ResearchSubject, now_ms
from app.models.research_v2 import ResearchV2EvaluationPartition as Partition
from app.security.decorators import roles_required
from app.services.research_v2_quality import read_latest_reports


class PartitionForm(LocalizedFlaskForm):
    state = HiddenField(validators=[DataRequired()])
    partition = SelectField("Evaluation group", choices=[("development", "Development / pilot"),
        ("locked_final_test", "Reserved final test"), ("prospective_monitor", "Future monitoring")], validators=[DataRequired()])
    submit = SubmitField("Assign group")


def _signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="research.v2.partition.v1")


@research_bp.get("/v2")
@roles_required("researcher")
def v2_review():
    enabled = current_app.config.get("RESEARCH_V2_ENABLED", False)
    reports, development_reports, participants = [], [], []
    forms = {}
    page = request.args.get("page", "1")
    if not page.isascii() or not page.isdigit() or not 1 <= int(page) <= 10000:
        abort(400)
    page = int(page)
    if enabled:
        reports = read_latest_reports(db.session.connection(), now_ms=now_ms(), limit=10)
        development_reports = read_latest_reports(db.session.connection(), now_ms=now_ms(), limit=5, scope_code="development")
        participants = db.session.query(ResearchSubject.subject_code, Partition.partition_code, Partition.version,
            Partition.development_exposed_at_ms).join(Partition, Partition.subject_id == ResearchSubject.id).order_by(
                ResearchSubject.id).offset((page - 1) * 50).limit(51).all()
        for row in participants[:50]:
            form = PartitionForm()
            form.state.data = _signer().dumps([current_user.public_id, row.subject_code, row.version])
            forms[row.subject_code] = form
    return render_template("research/v2.html", active_nav="v2", enabled=enabled, reports=reports,
        development_reports=development_reports,
        participants=participants[:50], forms=forms, page=page, has_next=len(participants) > 50,
        now=now_ms())


@research_bp.post("/v2/participants/<subject_code>/partition")
@roles_required("researcher")
def v2_partition(subject_code):
    if not current_app.config.get("RESEARCH_V2_ENABLED", False):
        abort(404)
    form = PartitionForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        token = _signer().loads(form.state.data, max_age=3600)
    except BadData:
        abort(400)
    if not isinstance(token, list) or len(token) != 3 or token[:2] != [current_user.public_id, subject_code] or type(token[2]) is not int:
        abort(400)
    db.session.rollback()
    from app.services.research_configuration_transactions import _lock_researcher
    from app.services.research_control_gate import lock_all_configurations
    actor = _lock_researcher(current_user.id)
    if actor is None:
        abort(403)
    lock_all_configurations()
    subject = db.session.query(ResearchSubject).filter_by(subject_code=subject_code).with_for_update().first()
    part = db.session.get(Partition, subject.id) if subject else None
    if part is None:
        abort(404)
    if part.version != token[2] or part.partition_code != "unassigned":
        db.session.rollback()
        flash("This participant already has a group or the form is stale. Reload the page.", "warning")
        return redirect(url_for("research.v2_review"))
    if form.partition.data == "locked_final_test" and part.development_exposed_at_ms is not None:
        abort(409)
    moment = now_ms()
    part.partition_code = form.partition.data
    part.assigned_by_id, part.assigned_at_ms = actor.id, moment
    part.locked_at_ms = moment if part.partition_code == "locked_final_test" else None
    part.assignment_reason = {"development": "development_pilot", "locked_final_test": "predeclared_split", "prospective_monitor": "prospective_review"}[part.partition_code]
    part.version += 1
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("The group could not be saved. Reload and try again.", "danger")
    else:
        flash("Evaluation group recorded. Final-test data stays quarantined.", "success")
    return redirect(url_for("research.v2_review"))
