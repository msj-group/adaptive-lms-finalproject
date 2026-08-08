from flask import abort, flash, redirect, render_template, url_for

from app.blueprints.admin import admin_bp
from app.blueprints.admin.forms import LevelForm
from app.blueprints.admin.utils import move_within_siblings, normalize_optional_text
from app.extensions import db
from app.models import AcademicStatus, Level, UserRole
from app.security.decorators import roles_required


def _ordered_levels():
    return Level.query.order_by(Level.display_order, Level.id).all()


@admin_bp.get("/levels")
@roles_required(UserRole.ADMINISTRATOR.value)
def levels_list():
    levels = _ordered_levels()
    return render_template("admin/levels/list.html", levels=levels)


@admin_bp.route("/levels/new", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def level_create():
    form = LevelForm()
    if form.validate_on_submit():
        max_order = db.session.query(db.func.max(Level.display_order)).scalar()
        next_order = (max_order + 1) if max_order is not None else 0
        level = Level(
            name=form.name.data.strip(),
            code=normalize_optional_text(form.code.data),
            display_order=next_order,
        )
        db.session.add(level)
        db.session.commit()
        flash(f"Level '{level.name}' created.", "success")
        return redirect(url_for("admin.levels_list"))
    return render_template("admin/levels/form.html", form=form, level=None)


@admin_bp.route("/levels/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def level_edit(public_id):
    level = Level.query.filter_by(public_id=public_id).first_or_404()
    form = LevelForm(obj=level, level_id=level.id)
    if form.validate_on_submit():
        level.name = form.name.data.strip()
        level.code = normalize_optional_text(form.code.data)
        db.session.commit()
        flash(f"Level '{level.name}' updated.", "success")
        return redirect(url_for("admin.levels_list"))
    return render_template("admin/levels/form.html", form=form, level=level)


@admin_bp.post("/levels/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def level_toggle_status(public_id):
    level = Level.query.filter_by(public_id=public_id).first_or_404()
    level.status = (
        AcademicStatus.ARCHIVED.value
        if level.status == AcademicStatus.ACTIVE.value
        else AcademicStatus.ACTIVE.value
    )
    db.session.commit()
    flash(f"Level '{level.name}' is now {level.status}.", "success")
    return redirect(url_for("admin.levels_list"))


@admin_bp.post("/levels/<public_id>/move-up")
@roles_required(UserRole.ADMINISTRATOR.value)
def level_move_up(public_id):
    level, moved = move_within_siblings(_ordered_levels(), public_id, -1)
    if level is None:
        abort(404)
    if moved:
        db.session.commit()
        flash(f"Level '{level.name}' moved up.", "success")
    else:
        flash("This level is already first.", "warning")
    return redirect(url_for("admin.levels_list"))


@admin_bp.post("/levels/<public_id>/move-down")
@roles_required(UserRole.ADMINISTRATOR.value)
def level_move_down(public_id):
    level, moved = move_within_siblings(_ordered_levels(), public_id, 1)
    if level is None:
        abort(404)
    if moved:
        db.session.commit()
        flash(f"Level '{level.name}' moved down.", "success")
    else:
        flash("This level is already last.", "warning")
    return redirect(url_for("admin.levels_list"))
