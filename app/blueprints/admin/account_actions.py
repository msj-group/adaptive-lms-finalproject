"""HTTP adapters for Student/Teacher account mutations."""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import User
from app.security.passwords import hash_password
from app.services.account_operations import AccountConflict, change_account
from app.services.account_tokens import account_token, read_account_token


def _get(public_id, role):
    return User.query.filter_by(public_id=public_id, role=role).first_or_404()


def _destination(role, public_id, action="detail"):
    return redirect(url_for("admin." + role + "_" + action, public_id=public_id))


def edit_account(public_id, role, form_type):
    account = _get(public_id, role)
    form = form_type(obj=account if request.method == "GET" else None, **{role + "_id": account.id})
    token = request.form.get("account_snapshot", "") if request.method == "POST" else account_token(account)
    if form.validate_on_submit():
        actor_id = current_user.id
        try:
            change_account(public_id, role, actor_id, read_account_token(token), "profile",
                           full_name=form.full_name.data.strip(), email=form.email.data.strip().lower())
            db.session.commit()
        except AccountConflict as error:
            db.session.rollback()
            flash(str(error), "danger")
            return _destination(role, public_id, "edit")
        except IntegrityError:
            db.session.rollback()
            flash("This account could not be saved. The email may already be in use. Please review and try again.", "danger")
            return _destination(role, public_id, "edit")
        flash("Account updated.", "success")
        return _destination(role, public_id)
    return render_template("admin/" + role + "s/form.html", form=form, snapshot_token=token, **{role: account})


def toggle_account(public_id, role, redirect_after):
    account = _get(public_id, role)
    actor_id = current_user.id
    try:
        account = change_account(public_id, role, actor_id, read_account_token(request.form.get("account_snapshot")), "status")
        db.session.commit()
        flash("Account status updated.", "success")
    except (AccountConflict, IntegrityError) as error:
        db.session.rollback()
        flash(str(error) if isinstance(error, AccountConflict) else "This account could not be changed. Please reload and try again.", "danger")
        account = _get(public_id, role)
    return redirect_after(account)


def reset_account_password(public_id, role, form_type):
    account = _get(public_id, role)
    form = form_type()
    token = request.form.get("account_snapshot", "") if request.method == "POST" else account_token(account)
    if form.validate_on_submit():
        actor_id = current_user.id
        try:
            change_account(public_id, role, actor_id, read_account_token(token), "password", password_hash=hash_password(form.password.data))
            db.session.commit()
        except (AccountConflict, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, AccountConflict) else "This password could not be changed. Please reload and try again.", "danger")
            return _destination(role, public_id, "reset_password")
        flash("Password reset.", "success")
        return _destination(role, public_id)
    return render_template("admin/" + role + "s/reset_password.html", form=form, snapshot_token=token, **{role: account})
