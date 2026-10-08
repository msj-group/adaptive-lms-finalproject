"""Authoritative study start, consistent with the term and first scheduled class."""
from datetime import datetime, timedelta
from flask import current_app
from app.models import Schedule
from app.services.schedule_occurrences import from_app_local, to_app_local


def first_slot_start(day, start_time, effective_start, effective_end):
    first = effective_start + timedelta(days=(day-effective_start.weekday()) % 7)
    if first > effective_end:
        return None
    return from_app_local(current_app.config["APP_TIMEZONE"], datetime.combine(first,start_time))


def study_start_error(term, moment, group_id=None):
    if moment is None:
        return "Set the group's study start."
    local = to_app_local(current_app.config["APP_TIMEZONE"],moment).date()
    if term is None or not term.start_date <= local <= term.end_date:
        return "Study start must fall inside the group's academic term."
    if group_id:
        # Group mutex prevents concurrent schedule changes. Stream its history;
        # an archived earlier class still bounds the recorded study start.
        for slot in Schedule.query.filter_by(group_id=group_id).order_by(Schedule.id).yield_per(100):
            first = first_slot_start(slot.day_of_week,slot.start_time,slot.effective_start_date,slot.effective_end_date)
            if first is not None and first < moment:
                return "Study start cannot be later than an existing scheduled class."
    return None
