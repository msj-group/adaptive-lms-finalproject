"""Readable, bounded account and schedule revision history."""
from flask import abort, render_template, request
from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models import AccountRevision, Room, Schedule, SchedulingRevision, User
from app.security.decorators import roles_required
from app.blueprints.admin.finance_registers import local_moment
from app.blueprints.admin.financial_http import _financial_response


@admin_bp.get("/history/<kind>/<public_id>")
@_financial_response
@roles_required("administrator")
def operational_history(kind, public_id):
    fields = {"full_name":"Name","email":"Email","status":"Status","name":"Name","capacity":"Capacity",
        "day_of_week":"Weekday (Monday = 0)","start_time":"Start time","end_time":"End time",
        "effective_start_date":"Effective from","effective_end_date":"Effective through","location":"Location"}
    if kind in {"student","teacher"}:
        target = User.query.filter_by(public_id=public_id,role=kind).first_or_404()
        query = db.session.query(AccountRevision,User.full_name).join(User,User.id==AccountRevision.actor_id).filter(AccountRevision.user_id==target.id)
        title, column = target.full_name + " — Account changes", AccountRevision.id
    elif kind in {"room","schedule"}:
        target = (Room if kind == "room" else Schedule).query.filter_by(public_id=public_id).first_or_404()
        query = db.session.query(SchedulingRevision,User.full_name).join(User,User.id==SchedulingRevision.actor_id).filter(
            (SchedulingRevision.room_id if kind=="room" else SchedulingRevision.schedule_id)==target.id)
        title, column = (target.name if kind == "room" else "Schedule") + " — Changes", SchedulingRevision.id
    else:
        abort(404)
    page = query.order_by(column.desc()).paginate(page=max(1,request.args.get("page",1,type=int)),per_page=30,error_out=False)
    entries = []
    room_ids = {value for row, _actor in page.items for snapshot in [row.before_snapshot or {},row.after_snapshot]
                for value in [snapshot.get("room_id")] if value is not None} if kind == "schedule" else set()
    labels = {room.id:room.name for room in Room.query.filter(Room.id.in_(room_ids)).all()} if room_ids else {}
    for row, actor in page.items:
        before, after = row.before_snapshot or {}, row.after_snapshot
        changes = [(label,before.get(key),after.get(key)) for key,label in fields.items() if before.get(key)!=after.get(key)]
        if kind=="schedule" and before.get("room_id")!=after.get("room_id"):
            changes.append(("Room (current name)", labels.get(before.get("room_id"),"No room"),labels.get(after.get("room_id"),"No room")))
        entries.append({"at":local_moment(row.created_at),"actor":actor,"action":row.action.replace('_',' ').title(),"changes":changes})
    return render_template("admin/history.html",title=title,entries=entries,page=page,kind=kind,public_id=public_id)
