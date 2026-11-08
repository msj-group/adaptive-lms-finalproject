"""UTC range validation for collection evidence (at most 90 days)."""
from datetime import date, datetime, time, timedelta, timezone

def dashboard_filters(args, as_of_ms):
    today = datetime.fromtimestamp(as_of_ms / 1000, timezone.utc).date()
    end = date.fromisoformat(args.get("to") or today.isoformat())
    start = date.fromisoformat(args.get("from") or (end - timedelta(days=14)).isoformat())
    if not 0 <= (end - start).days <= 89 or end > today:
        raise ValueError("Use an ordered range of at most 90 days, through today.")
    raw_version = (args.get("version") or "").strip()
    version = int(raw_version) if raw_version else None
    if version is not None and not 1 <= version <= 1_000_000:
        raise ValueError("Invalid configuration version.")
    epoch = lambda d: int(datetime.combine(d, time.min, timezone.utc).timestamp() * 1000)
    return {"from": start.isoformat(), "to": end.isoformat(), "version": raw_version,
            "start": start, "end": end, "lower": epoch(start),
            "upper": min(epoch(end + timedelta(days=1)), as_of_ms + 1), "version_number": version}
