from flask import abort, flash, redirect, render_template, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import AcademicTermForm
from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, UserRole
from app.security.decorators import roles_required
from app.services.academic_lifecycle import academic_term_has_active_group
from app.services.academic_term_transactions import lock_academic_term_for_write
from app.services.schedule_queries import term_schedule_range_outside


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
    # Ordinary, non-locking lookup -- 404 handling and form population.
    # The authoritative date-containment decision is made below, only
    # after the fresh AcademicTerm lock.
    preview_term = AcademicTerm.query.filter_by(public_id=public_id).first_or_404()
    form = AcademicTermForm(obj=preview_term, term_id=preview_term.id)
    if form.validate_on_submit():
        name = form.name.data.strip()
        new_start = form.start_date.data
        new_end = form.end_date.data

        # M08 -- an AcademicTerm date edit must not orphan any existing
        # Schedule effective range in this Term (including archived
        # schedules and schedules under archived Groups). Lock the Term
        # first so a concurrent Schedule create/edit -- which locks this
        # same Term row via `lock_academic_hierarchy` -- serializes
        # against this edit, then re-check containment against the
        # locked, current rows. A metadata-only edit that does not move
        # either date skips the check (legacy rows are not auto-repaired).
        term = lock_academic_term_for_write(public_id)
        if term is None:
            abort(404)

        if new_start != term.start_date or new_end != term.end_date:
            offending = term_schedule_range_outside(term.id, new_start, new_end)
            if offending is not None:
                # Archiving that schedule would NOT help: the guard
                # (`term_schedule_range_outside`) deliberately counts
                # archived schedules and schedules under archived Groups
                # too, so the only remedies are to shorten the schedule's
                # effective range or to choose term dates that still
                # contain it.
                db.session.rollback()
                form.start_date.errors.append(
                    "These dates would leave an existing group schedule's effective range "
                    f"({offending.effective_start_date.isoformat()} to "
                    f"{offending.effective_end_date.isoformat()}) outside the term. Adjust that "
                    "schedule's effective range to fit, or choose term dates that still contain "
                    "it. Archived schedules still count."
                )
                return render_template(
                    "admin/academic_terms/form.html", form=form, term=preview_term
                )

        term.name = name
        term.start_date = new_start
        term.end_date = new_end
        db.session.commit()
        flash(f"Academic term '{term.name}' updated.", "success")
        return redirect(url_for("admin.academic_terms_list"))
    return render_template("admin/academic_terms/form.html", form=form, term=preview_term)


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
