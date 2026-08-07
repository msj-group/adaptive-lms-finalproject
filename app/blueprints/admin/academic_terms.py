from flask import flash, redirect, render_template, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import AcademicTermForm
from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, UserRole
from app.security.decorators import roles_required


@admin_bp.get("/academic-terms")
@roles_required(UserRole.ADMINISTRATOR.value)
def academic_terms_list():
    terms = AcademicTerm.query.order_by(AcademicTerm.start_date.desc()).all()
    return render_template("admin/academic_terms/list.html", terms=terms)


@admin_bp.route("/academic-terms/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def academic_term_create():
    form = AcademicTermForm()
    if form.validate_on_submit():
        term = AcademicTerm(
            name=form.name.data.strip(),
            start_date=form.start_date.data,
            end_date=form.end_date.data,
        )
        db.session.add(term)
        db.session.commit()
        flash(f"Academic term '{term.name}' created.", "success")
        return redirect(url_for("admin.academic_terms_list"))
    return render_template("admin/academic_terms/form.html", form=form, term=None)


@admin_bp.route("/academic-terms/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def academic_term_edit(public_id):
    term = AcademicTerm.query.filter_by(public_id=public_id).first_or_404()
    form = AcademicTermForm(obj=term, term_id=term.id)
    if form.validate_on_submit():
        term.name = form.name.data.strip()
        term.start_date = form.start_date.data
        term.end_date = form.end_date.data
        db.session.commit()
        flash(f"Academic term '{term.name}' updated.", "success")
        return redirect(url_for("admin.academic_terms_list"))
    return render_template("admin/academic_terms/form.html", form=form, term=term)


@admin_bp.post("/academic-terms/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def academic_term_toggle_status(public_id):
    term = AcademicTerm.query.filter_by(public_id=public_id).first_or_404()
    term.status = (
        AcademicStatus.ARCHIVED.value
        if term.status == AcademicStatus.ACTIVE.value
        else AcademicStatus.ACTIVE.value
    )
    db.session.commit()
    flash(f"Academic term '{term.name}' is now {term.status}.", "success")
    return redirect(url_for("admin.academic_terms_list"))
