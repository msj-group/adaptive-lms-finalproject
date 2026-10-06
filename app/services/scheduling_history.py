from app.extensions import db
from app.models import SchedulingRevision


def room_snapshot(room):
    return {"public_id": room.public_id, "name": room.name, "capacity": room.capacity,
            "status": room.status, "version": room.version}


def schedule_snapshot(slot):
    return {"public_id": slot.public_id, "group_id": slot.group_id, "room_id": slot.room_id,
            "day_of_week": slot.day_of_week, "start_time": slot.start_time.isoformat(),
            "end_time": slot.end_time.isoformat(), "effective_start_date": slot.effective_start_date.isoformat(),
            "effective_end_date": slot.effective_end_date.isoformat(), "location": slot.location, "status": slot.status}


def record_scheduling_change(target, actor_id, action, before):
    from app.models import Room
    from app.services.actor_authorization import require_current_actor
    require_current_actor(actor_id, "administrator")
    db.session.flush()
    is_room = isinstance(target, Room)
    db.session.add(SchedulingRevision(room_id=target.id if is_room else None,
        schedule_id=None if is_room else target.id, actor_id=actor_id, action=action,
        before_snapshot=before, after_snapshot=room_snapshot(target) if is_room else schedule_snapshot(target)))
