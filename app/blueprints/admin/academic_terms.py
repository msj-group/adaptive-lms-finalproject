from flask import abort, flash, redirect, render_template, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import AcademicTermForm
from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, UserRole
from app.security.decorators import roles_required
from app.services.academic_lifecycle import academic_term_has_active_group
from app.services.academic_term_transactions import lock_academic_term_for_write


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
    # Part M07C3 -- guarded hierarchy. AcademicTerm is the first (root)
    # entity in the global lock order, so its own `FOR UPDATE` lock,
    # taken as the first query of a freshly reset transaction, is all the
    # serialization this route needs: a concurrent Group create/edit/
    # reactivation that would put an active Group under this term locks
    # this same term row (via `lock_academic_hierarchy`) and therefore
    # serializes against this toggle.
    term = lock_academic_term_for_write(public_id)
    if term is None:
        abort(404)

    if term.status == AcademicStatus.ACTIVE.value:
        if academic_term_has_active_group(term.id):
            db.session.rollback()
            flash(
                "This academic term cannot be archived while an active group still uses it. "
                "Archive those groups first.",
                "danger",
            )
            return redirect(url_for("admin.academic_terms_list"))
        term.status = AcademicStatus.ARCHIVED.value
    else:
        # Reactivating a term: AcademicTerm is the root of the hierarchy,
        # so it has no ancestor to be blocked by.
        term.status = AcademicStatus.ACTIVE.value

    db.session.commit()
    flash(f"Academic term '{term.name}' is now {term.status}.", "success")
    return redirect(url_for("admin.academic_terms_list"))
