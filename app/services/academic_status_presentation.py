"""Validate an explicit directory status selection after the route's own locks."""
from flask import flash, request

from app.extensions import db


def accept_status_selection(current_status):
    # Existing native toggle callers remain supported. The new selector submits
    # both its intended destination and the state shown when the row was read.
    if "status" not in request.form:
        return True
    targets = request.form.getlist("status")
    originals = request.form.getlist("current_status")
    allowed = {"active", "archived"}
    if len(targets) != 1 or len(originals) != 1 or targets[0] not in allowed or originals[0] not in allowed:
        db.session.rollback()
        flash("Invalid status selection. Reload the records and try again.", "danger")
        return False
    if targets[0] == current_status:
        db.session.rollback()
        flash("The record already has the selected status.", "info")
        return False
    if originals[0] != current_status:
        db.session.rollback()
        flash("This record changed. Reload before changing its status.", "danger")
        return False
    # With exactly two allowed states, the original guarded toggle now performs
    # precisely the requested transition. All lifecycle blockers stay in its route.
    return True
