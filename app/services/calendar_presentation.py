"""UI-only filters and dates over bounded, already-authorized calendar rows.

No queries, authorization, schedule expansion or source mutations occur here.
"""
from datetime import date, timedelta

from app.services.calendar_queries import next_range, previous_range, range_args

KINDS = (
    ("all", "All types"), ("class", "Classes"),
    ("assignment", "Assignments"), ("quiz", "Quizzes"),
    ("listening", "Listening"), ("center_event", "Center events"),
)


def _kind(source):
    return source.split("_", 1)[0] if source != "center_event" else source


def _action(row, role):
    if not row.get("url"):
        return None
    kind = _kind(row["source"])
    if kind == "class":
        return "Open attendance" if role == "teacher" else "View course"
    if kind == "assignment":
        return "View assignments" if role == "teacher" else "View assignment"
    return "View listening" if kind == "listening" else "View quiz"


def prepare_calendar(rows, a_range, today, role, args):
    """Narrow a capped authorized read and build civil-day/week presentation.

    Choices come only from this fetched range. Invalid choices normalize to All.
    The caller must still report the original query's overflow indicator.
    """
    groups = {}
    for row in rows:
        public_id = row.get("group_public_id")
        if public_id and (public_id not in groups or row["source"] == "class"):
            groups[public_id] = row.get("context") or "Group"
    kind, group = args.get("kind", "all"), args.get("group", "all")
    filters_normalized = kind not in dict(KINDS) or (group != "all" and group not in groups)
    if kind not in dict(KINDS):
        kind = "all"
    if group != "all" and group not in groups:
        group = "all"
    view = args.get("view")
    if view == "list":
        view = "upcoming"
    if view not in ("month", "week", "day", "upcoming"):
        view = "week" if role == "teacher" else "month"
    default_day = min(max(today, a_range.start), a_range.end)
    if args.get("view") == "list" and not args.get("day"):
        default_day = a_range.start
    try:
        raw_day = args.get("day", "")
        selected = date.fromisoformat(raw_day)
        if selected.isoformat() != raw_day:
            selected = default_day
    except (ValueError, TypeError):
        selected = default_day
    selected = min(max(selected, a_range.start), a_range.end)
    filtered = []
    for row in rows:
        if kind != "all" and _kind(row["source"]) != kind:
            continue
        if group != "all" and row.get("group_public_id") != group:
            continue
        source = row["source"]
        tone = "class" if source == "class" else (
            "deadline" if source.endswith(("due", "deadline")) else "opening"
        )
        icon = {"class": "book", "assignment": "pen", "quiz": "quiz",
                "listening": "headphones", "center_event": "calendar"}.get(_kind(source), "calendar")
        filtered.append(dict(row, tone=tone, icon=icon, action_label=_action(row, role)))
    by_date = {}
    for row in filtered:
        by_date.setdefault(row["event_date"], []).append(row)
    base_args = dict(range_args(a_range), kind=kind, group=group, view=view)
    current = a_range.start - timedelta(days=a_range.start.weekday())
    finish = a_range.end + timedelta(days=6 - a_range.end.weekday())
    weeks, days = [], []
    while current <= finish:
        week = []
        for unused in range(7):
            in_range = a_range.start <= current <= a_range.end
            entries = by_date.get(current, []) if in_range else []
            cell = dict(date=current, iso=current.isoformat(), in_range=in_range,
                        entries=entries, count=len(entries), today=current == today,
                        selected=current == selected,
                        args=dict(base_args, day=current.isoformat()))
            week.append(cell)
            if in_range:
                days.append(cell)
            current += timedelta(days=1)
        weeks.append(week)
    before, after = previous_range(a_range, today), next_range(a_range, today)
    navigation = dict(kind=kind, group=group, view=view)
    return dict(
        days=days, weeks=weeks, selected=selected, today=today, view=view,
        group=group, kind=kind, kinds=KINDS,
        groups=[dict(public_id=key, label=value) for key, value in sorted(
            groups.items(), key=lambda item: (item[1].casefold(), item[0]))],
        filters_active=kind != "all" or group != "all",
        filters_normalized=filters_normalized, count=len(filtered),
        explicit_view=args.get("view") in ("day", "upcoming", "list"),
        range_args=base_args,
        previous_args=dict(range_args(before), **navigation, day=before.end.isoformat()),
        next_args=dict(range_args(after), **navigation, day=after.start.isoformat()),
        today_args=dict(navigation, day=today.isoformat()),
    )
