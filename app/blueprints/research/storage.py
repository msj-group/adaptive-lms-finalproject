from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from wtforms import BooleanField, DateField, HiddenField, SelectField, SubmitField
from wtforms.validators import InputRequired, Length, Optional
from app.blueprints.research import research_bp
from app.extensions import db
from app.models import ResearchAuditEvent, ResearchDataGap, ResearchExport, ResearchExportArchive, now_ms
from app.security.decorators import roles_required
from app.services.research_storage import cleanup_data, cleanup_preview, remove_archive, storage_usage


class CleanupForm(FlaskForm):
    start = DateField("Sessions started from (local date)", validators=[Optional()])
    end = DateField("Sessions started through (local date)", validators=[Optional()])
    all_data = BooleanField("All raw sessions")
    provenance = SelectField("Provenance", choices=[("all", "All provenance"), ("study", "Study"), ("development", "Development"), ("demo", "Demonstration")], default="all")
    preview_token = HiddenField(validators=[Optional(), Length(max=8000)])
    confirm = BooleanField("Permanently remove the complete sessions and listed archive bytes")
    submit = SubmitField("Review selection")


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="research.cleanup.preview.v1")


@research_bp.route("/storage", methods=["GET", "POST"])
@roles_required("researcher")
def storage():
    form = CleanupForm()
    preview = None
    if form.validate_on_submit():
        try:
            if request.form.get("execute") == "yes":
                if not form.confirm.data or not form.preview_token.data:
                    raise ValueError("Review the selection and confirm its deletion first.")
                expected = _serializer().loads(form.preview_token.data)
                cleanup_data(current_user.id, expected, form.start.data, form.end.data, form.all_data.data, form.provenance.data)
                db.session.commit()
                flash("Selected raw sessions removed. Subject links, exclusions, configuration, export descriptions and audit history are preserved.", "success")
                return redirect(url_for("research.storage"))
            preview = cleanup_preview(form.start.data, form.end.data, form.all_data.data, form.provenance.data)
            form.preview_token.data = _serializer().dumps(preview)
            db.session.add(ResearchAuditEvent(action="data_cleanup_previewed", channel="workspace", actor_id=current_user.id,
                count_value=preview["sessions"], detail_code="whole_sessions_preview"))
            db.session.commit()
        except (BadSignature, ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else "The cleanup could not be saved. Review a fresh preview.", "danger")
            form.preview_token.data = ""
    page = max(1, request.args.get("page", 1, type=int))
    gaps = ResearchDataGap.query.order_by(ResearchDataGap.id.desc()).paginate(page=page, per_page=30, error_out=False)
    exports = db.session.query(ResearchExport.public_id, ResearchExport.manifest_digest, ResearchExport.created_at,
        ResearchExportArchive.byte_size).outerjoin(ResearchExportArchive, ResearchExportArchive.export_id == ResearchExport.id).order_by(ResearchExport.id.desc()).paginate(
        page=max(1, request.args.get("export_page",1,type=int)), per_page=30, error_out=False)
    from app.services.research_workspace_queries import ms_to_local
    return render_template("research/storage.html", form=form, preview=preview, usage=storage_usage(), gaps=gaps,
        exports=exports, active_nav="storage", tz_name=current_app.config["APP_TIMEZONE"],
        measured_at=ms_to_local(current_app.config["APP_TIMEZONE"], now_ms()), retention_days=current_app.config["RESEARCH_RETENTION_DAYS"])


@research_bp.post("/exports/<export_public_id>/remove-archive")
@roles_required("researcher")
def export_remove_archive(export_public_id):
    try:
        remove_archive(current_user.id, export_public_id, request.form.get("manifest_digest", ""))
        db.session.commit()
        flash("Archive bytes removed. Its description and digest remain; this export cannot be rebuilt.", "success")
    except (ValueError, IntegrityError):
        db.session.rollback()
        flash("The archive could not be removed. Reload its record.", "danger")
    return redirect(url_for("research.storage"))
