"""Shared resource locks and effective half-open schedule overlap checks."""
from sqlalchemy import or_
from app.extensions import db
from app.models import Group, GroupTeacherAssignment, Room, Schedule, User
from app.services.schedule_queries import effective_ranges_share_weekday


def lock_schedule_resources(group_id, room_id=None):
    # A locking discovery read avoids creating a REPEATABLE READ snapshot before
    # waiting for all Teacher locks. The Group lock protects its assignment set.
    ids = {row[0] for row in db.session.query(GroupTeacherAssignment.teacher_id).filter_by(
        group_id=group_id, status="active").with_for_update().all()}
    teachers = User.query.filter(User.id.in_(sorted(ids))).order_by(User.id).populate_existing().with_for_update().all() if ids else []
    room = Room.query.filter_by(id=room_id).populate_existing().with_for_update().first() if room_id else None
    return teachers, room


def resource_schedule_conflict(group_id, day, start, end, effective_start, effective_end, room_id=None, exclude_id=None):
    teachers = db.session.query(GroupTeacherAssignment.teacher_id).join(User, User.id == GroupTeacherAssignment.teacher_id).filter(
        GroupTeacherAssignment.group_id == group_id, GroupTeacherAssignment.status == "active",
        User.role == "teacher", User.status == "active")
    other_groups = db.session.query(GroupTeacherAssignment.group_id).filter(
        GroupTeacherAssignment.teacher_id.in_(teachers), GroupTeacherAssignment.status == "active")
    scope = or_(Schedule.group_id == group_id, Schedule.group_id.in_(other_groups))
    if room_id:
        scope = or_(scope, Schedule.room_id == room_id)
    candidates = Schedule.query.join(Group, Group.id == Schedule.group_id).filter(
        scope, Group.status == "active", Schedule.status == "active", Schedule.day_of_week == day,
        Schedule.start_time < end, Schedule.end_time > start)
    if exclude_id:
        candidates = candidates.filter(Schedule.id != exclude_id)
    for other in candidates.order_by(Schedule.id).all():
        if effective_ranges_share_weekday(day, effective_start, effective_end, other.effective_start_date, other.effective_end_date):
            return other
    return None


def teacher_assignment_conflict(teacher_id, group_id):
    other_groups = db.session.query(GroupTeacherAssignment.group_id).filter(
        GroupTeacherAssignment.teacher_id == teacher_id, GroupTeacherAssignment.status == "active",
        GroupTeacherAssignment.group_id != group_id)
    target = Schedule.query.filter_by(group_id=group_id, status="active").all()
    others = Schedule.query.join(Group, Group.id == Schedule.group_id).filter(
        Schedule.group_id.in_(other_groups), Schedule.status == "active", Group.status == "active").all()
    for slot in target:
        for other in others:
            if slot.day_of_week == other.day_of_week and slot.start_time < other.end_time and slot.end_time > other.start_time and effective_ranges_share_weekday(
                slot.day_of_week, slot.effective_start_date, slot.effective_end_date, other.effective_start_date, other.effective_end_date):
                return other
    return None


def lock_group_rooms(group_id, capacity):
    ids = {row[0] for row in db.session.query(Schedule.room_id).filter(
        Schedule.group_id == group_id, Schedule.status == "active", Schedule.room_id.isnot(None)
    ).with_for_update().all()}
    rooms = Room.query.filter(Room.id.in_(sorted(ids))).order_by(Room.id).populate_existing().with_for_update().all() if ids else []
    if any(room.status != "active" or room.capacity < capacity for room in rooms):
        return "Every assigned room must be active and accommodate the group's capacity."
    return None


def group_reactivation_schedule_error(group):
    error = lock_group_rooms(group.id, group.capacity)
    if error:
        return error
    for slot in Schedule.query.filter_by(group_id=group.id, status="active").order_by(Schedule.id).all():
        if resource_schedule_conflict(group.id, slot.day_of_week, slot.start_time, slot.end_time,
                                     slot.effective_start_date, slot.effective_end_date, slot.room_id, slot.id):
            return "Resolve the group's teacher or room schedule conflicts before reactivating it."
    return None
