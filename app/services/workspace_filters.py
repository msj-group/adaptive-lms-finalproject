"""Bounded local-date filters for read-only Researcher lists."""
from datetime import date, datetime, time, timedelta, timezone
from app.services.schedule_occurrences import from_app_local


def research_filters(args, tz_name):
    values = {key: args.get(key, "").strip()[:80] for key in ("subject", "version", "state", "from", "to", "action")}
    result = dict(values)
    if values["version"]:
        version = int(values["version"])
        if version < 1 or version > 1000000:
            raise ValueError("Invalid version")
        result["version"] = version
    for key, end in (("from", False), ("to", True)):
        if values[key]:
            local_day = date.fromisoformat(values[key]) + (timedelta(days=1) if end else timedelta())
            moment = from_app_local(tz_name, datetime.combine(local_day, time.min))
            result["upper_date" if end else "lower_date"] = moment
            result["upper" if end else "lower"] = int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)
    if values["from"] and values["to"] and values["from"] > values["to"]:
        raise ValueError("Invalid date range")
    return result, values
