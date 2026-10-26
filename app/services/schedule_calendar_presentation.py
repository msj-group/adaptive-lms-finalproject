"""Civil-date presentation of the Admin overview's already-filtered schedules.

No SQL, authorization or business writes. Archived slots remain administrative
records and are explicitly marked, never treated as operational classes.
"""
from heapq import nsmallest

from app.services.calendar_presentation import prepare_calendar
from app.services.calendar_queries import MAX_CALENDAR_ROWS, calendar_row
from app.services.schedule_occurrences import iter_occurrences_between, slot_spec


def prepare_schedule_calendar(schedules, a_range, today, args, filters):
    """Reuse the Student/Teacher date UI without changing Admin query scope.

    Bound the occurrence projection to the earliest 600 entries plus one
    overflow sentinel. The original complete filtered register stays readable.
    All date links retain the original normalized administrative filters.
    """
    occurrences = (
        occurrence
        for schedule in schedules
        for occurrence in iter_occurrences_between(
            slot_spec(schedule), a_range.start, a_range.end
        )
    )
    selected = nsmallest(
        MAX_CALENDAR_ROWS + 1, occurrences,
        key=lambda item: (item.start, item.end, item.ref.public_id),
    )
    rows = []
    for occurrence in selected[:MAX_CALENDAR_ROWS]:
        schedule = occurrence.ref
        group = schedule.group
        parent_archived = any(parent.status != "active" for parent in (
            group, group.academic_term, group.course, group.course.level,
        ))
        row = calendar_row(
            "class", occurrence.start.date(), group.course.title,
            start_time=occurrence.start.time(), end_time=occurrence.end.time(),
            context=group.name, location=schedule.location,
            group_public_id=group.public_id, source_public_id=schedule.public_id,
        )
        row.update(schedule_status=schedule.status, parent_archived=parent_archived,
                   term_name=group.academic_term.name, level_name=group.course.level.name,
                   effective_start=schedule.effective_start_date,
                   effective_end=schedule.effective_end_date)
        rows.append(row)

    presentation_args = {key: args[key] for key in ("day", "view") if key in args}
    calendar = prepare_calendar(rows, a_range, today, "admin", presentation_args)
    for key in ("range_args", "previous_args", "next_args", "today_args"):
        calendar[key] = {name: value for name, value in calendar[key].items()
                         if name not in ("kind", "group")}
        calendar[key].update(filters)
    for day in (cell for week in calendar["weeks"] for cell in week):
        day["args"] = dict(calendar["range_args"], day=day["iso"])
    calendar.update(schedule_admin=True, filters_active=bool(filters),
                    truncated=len(selected) > MAX_CALENDAR_ROWS)
    return calendar
