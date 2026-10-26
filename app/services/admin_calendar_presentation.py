"""Administrator Focus planner over the existing authorized calendar read.

No queries, writes, recurrence expansion or additional authorization lives here.
"""
from calendar import monthrange
from datetime import timedelta

from app.services.calendar_presentation import prepare_calendar


def prepare_focus_calendar(rows, a_range, today, args):
    """Keep native range bounds/filters and build compact civil-month selectors."""
    display_args = dict(args)
    display_args["view"] = args.get("view") if args.get("view") in ("list", "upcoming") else "day"
    calendar = prepare_calendar(rows, a_range, today, "admin", display_args)
    by_date = {day["date"]: day for day in calendar["days"]}
    for day in calendar["days"]:
        for row in day["entries"]:
            row["action_label"] = (
                "Open group schedules" if row["source"] == "class" else
                "Open event details" if row["source"] == "center_event" else None
            ) if row.get("url") else None

    months = []
    current = a_range.start.replace(day=1)
    while current <= a_range.end:
        last = current.replace(day=monthrange(current.year, current.month)[1])
        cell_date = current - timedelta(days=current.weekday())
        finish = last + timedelta(days=6 - last.weekday())
        weeks = []
        while cell_date <= finish:
            week = []
            for unused in range(7):
                cell = by_date.get(cell_date) if cell_date.month == current.month else None
                week.append(dict(cell) if cell else dict(
                    date=cell_date, iso=cell_date.isoformat(), in_range=False,
                    count=0, entries=[], selected=False, today=cell_date == today,
                ))
                cell_date += timedelta(days=1)
            weeks.append(week)
        months.append(dict(date=current, weeks=weeks))
        current = last + timedelta(days=1)
    calendar["months"] = months
    return calendar
