"""Room management with signed snapshots and locked scheduling guards."""
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from app.i18n import LocalizedFlaskForm as FlaskForm
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.exc import IntegrityError
from wtforms import IntegerField, StringField, SubmitField
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange
from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models import Group, Room, Schedule, UserRole
from app.models.submission_feedback import whole_second_utc
from app.security.decorators import roles_required
from app.services.scheduling_history import record_scheduling_change, room_snapshot


class RoomForm(FlaskForm):
    name = StringField("Room name", validators=[DataRequired(), Length(max=100)])
    capacity = IntegerField("Capacity", validators=[InputRequired(), NumberRange(min=1, max=100000)])
    submit = SubmitField("Save room")


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="admin.room.snapshot.v1")


def _token(room):
    return _serializer().dumps(room_snapshot(room))


def _matches(room):
    try:
        return _serializer().loads(request.form.get("snapshot", "")) == room_snapshot(room)
    except BadSignature:
        return False


def _active_room_slots(room_id):
    return Schedule.query.join(Group, Group.id == Schedule.group_id).filter(
        Schedule.room_id == room_id, Schedule.status == "active", Group.status == "active")


@admin_bp.get("/rooms")
@roles_required(UserRole.ADMINISTRATOR.value)
def rooms_list():
    page = Room.query.order_by(Room.name, Room.id).paginate(page=max(1, request.args.get("page", 1, type=int)), per_page=50, error_out=False)
    return render_template("admin/rooms/list.html", page=page, room_token=_token)


@admin_bp.route("/rooms/new", methods=["GET", "POST"])
@admin_bp.route("/rooms/<public_id>/edit", methods=["GET", "POST"])
@roles_required(UserRole.ADMINISTRATOR.value)
def room_edit(public_id=None):
    room = Room.query.filter_by(public_id=public_id).first_or_404() if public_id else None
    form = RoomForm(obj=room if request.method == "GET" else None)
    token = request.form.get("snapshot", "") if request.method == "POST" else _token(room) if room else ""
    if form.validate_on_submit():
        actor_id = current_user.id
        db.session.rollback()
        if public_id:
            room = Room.query.filter_by(public_id=public_id).populate_existing().with_for_update().first_or_404()
            if not _matches(room):
                db.session.rollback()
                flash("This room changed since this form was opened. Please review and try again.", "danger")
                return redirect(url_for("admin.room_edit", public_id=public_id))
            if _active_room_slots(room.id).filter(Group.capacity > form.capacity.data).first() is not None:
                db.session.rollback()
                flash("The room must accommodate the capacity of every active group using it.", "danger")
                return redirect(url_for("admin.room_edit", public_id=public_id))
            before = room_snapshot(room)
            room.version += 1
        else:
            room = Room(status="active", version=1)
            before = None
            db.session.add(room)
        room.name, room.capacity = form.name.data.strip(), form.capacity.data
        room.updated_at = whole_second_utc()
        try:
            record_scheduling_change(room, actor_id, "edit" if public_id else "create", before)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("This room could not be saved. A room with that name may already exist.", "danger")
            return redirect(url_for("admin.room_edit", **({"public_id": public_id} if public_id else {})))
        flash("Room saved.", "success")
        return redirect(url_for("admin.rooms_list"))
    return render_template("admin/rooms/form.html", room=room, form=form, snapshot=token)


@admin_bp.post("/rooms/<public_id>/toggle-status")
@roles_required(UserRole.ADMINISTRATOR.value)
def room_toggle_status(public_id):
    actor_id = current_user.id
    db.session.rollback()
    room = Room.query.filter_by(public_id=public_id).populate_existing().with_for_update().first_or_404()
    if not _matches(room):
        db.session.rollback()
        flash("This room changed. Reload before changing its status.", "danger")
    elif room.status == "active" and _active_room_slots(room.id).first() is not None:
        db.session.rollback()
        flash("Reassign or archive the room's active schedules before archiving it.", "danger")
    else:
        before = room_snapshot(room)
        room.status = "archived" if room.status == "active" else "active"
        room.version += 1
        room.updated_at = whole_second_utc()
        record_scheduling_change(room, actor_id, "status", before)
        db.session.commit()
        flash("Room status updated.", "success")
    return redirect(url_for("admin.rooms_list"))
