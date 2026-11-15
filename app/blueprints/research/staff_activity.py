from datetime import date, datetime, timedelta, timezone
from functools import partial
import re

from flask import abort, current_app, render_template, request, url_for

from app.blueprints.research import research_bp
from app.security.decorators import roles_required
from app.services.schedule_occurrences import to_app_local
from app.services.staff_activity import RETENTION_DAYS, ROLES, activity


@research_bp.get("/staff-activity")
@roles_required("researcher")
def staff_activity():
    today = datetime.now(timezone.utc).date()
    oldest = today - timedelta(days=RETENTION_DAYS - 1)
    raw_day = request.args.get("day", "")
    role = request.args.get("role", "")
    search = request.args.get("staff", "").strip()[:120]
    session_id = request.args.get("session_id", "")
    page = request.args.get("page", 1, type=int)
    try:
        day = date.fromisoformat(raw_day) if raw_day else None
    except ValueError:
        abort(400)
    if ((day is not None and not oldest <= day <= today) or (role and role not in ROLES) or not 1 <= page <= 60000
            or (session_id and not re.fullmatch(r"[a-f0-9]{32}", session_id))):
        abort(400)

    enabled = current_app.config["STAFF_ACTIVITY_ENABLED"]
    failed = False
    rows, more, quota = [], False, False
    if enabled:
        try:
            rows, more, quota = activity(day, role, search.casefold(), session_id, page, today)
        except (OSError, RuntimeError):
            failed = True

    params = dict(day=day.isoformat() if day else "", role=role, staff=search, session_id=session_id)
    local_time = partial(to_app_local, current_app.config["APP_TIMEZONE"])
    for row in rows:
        row["local_time"] = local_time(datetime.fromisoformat(row["at"]))
    return render_template(
        "research/staff_activity.html", rows=rows, roles=ROLES, page=page,
        prev_url=url_for("research.staff_activity", page=page - 1, **params) if page > 1 else None,
        next_url=url_for("research.staff_activity", page=page + 1, **params) if more else None,
        today=today, oldest=oldest, day=day or "", role=role, search=search, session_id=session_id,
        enabled=enabled, failed=failed, quota=quota, retention_days=RETENTION_DAYS,
    )
