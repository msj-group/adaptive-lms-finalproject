"""Operational activity for authenticated staff, outside Study collection."""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask import current_app, request, session
from flask_login import current_user

from app.services.file_storage import resolve_within_root
from app.services.material_config import current_material_config

ROLES = {"teacher": "Teacher", "administrator": "Administrator"}
RETENTION_DAYS = 30
DAILY_LIMIT = 2 * 1024 * 1024
SESSION_KEY = "staff_activity_session"
AREAS = {"admin": "Administration", "teacher": "Teaching", "messages": "Messages",
         "notifications": "Notifications", "account": "Account", "workspace": "Workspace"}
BACKGROUND = {"notifications.preview", "account.photo", "messages.read_thread_notifications"}


def _directory():
    return resolve_within_root(current_material_config(), "staff-activity")


@contextmanager
def _writer_lock(directory):
    # Serialize append, quota checks and expiry across web workers.
    with os.fdopen(os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600), "r+b") as lock:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _append(moment, events):
    data = "".join(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                   for event in events).encode("utf-8")
    directory = _directory()
    # A Windows 0700 mkdir installs a DACL that excludes sandboxed runners.
    directory.mkdir(mode=0o777 if os.name == "nt" else 0o700, parents=True, exist_ok=True)
    path = directory / f"{moment.date().isoformat()}.jsonl"
    oldest = (moment.date() - timedelta(days=RETENTION_DAYS - 1)).isoformat()
    with _writer_lock(directory):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "ab") as file:
            saved = os.fstat(file.fileno()).st_size + len(data) <= DAILY_LIMIT
            if saved:
                file.write(data)
        for expired in directory.glob("????-??-??.jsonl"):
            if expired.stem < oldest:
                expired.unlink(missing_ok=True)
    return saved


def track(user, kind, details=None):
    if not current_app.config["STAFF_ACTIVITY_ENABLED"] or user.role not in ROLES:
        return
    browser = session.get(SESSION_KEY)
    new_session = kind == "login" or not browser or browser["account"] != user.public_id
    if new_session:
        browser = {"account": user.public_id, "id": uuid4().hex}
    moment = datetime.now(timezone.utc)
    identity = dict(at=moment.isoformat(timespec="milliseconds"), role=user.role,
                    name=user.full_name, account=user.email, session=browser["id"])
    events = [dict(identity, kind="resumed")] if new_session and kind != "login" else []
    events.append(dict(identity, kind=kind, **(details or {})))
    try:
        saved = _append(moment, events)
    except (OSError, RuntimeError) as error:
        current_app.logger.warning("Staff activity storage unavailable (%s)", type(error).__name__)
        saved = False
    if saved and new_session and kind != "logout":
        session[SESSION_KEY] = browser
    if kind == "logout":
        session.pop(SESSION_KEY, None)


def record_staff_response(response):
    if (not current_app.config["STAFF_ACTIVITY_ENABLED"] or request.blueprint not in AREAS
            or request.endpoint in BACKGROUND or request.headers.get("X-AELMS-Communication")
            or response.status_code in {401, 403, 404, 405}):
        return response
    if request.method == "GET":
        if "prefetch" in request.headers.get("Sec-Purpose", "") or request.headers.get("Purpose") == "prefetch":
            return response
        attachment = response.headers.get("Content-Disposition", "").startswith("attachment")
        if attachment and response.status_code == 200 and not request.headers.get("Range"):
            kind = "download"
        elif response.status_code == 200 and response.mimetype == "text/html":
            kind = "view"
        else:
            return response
    elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        # Private reader marks auto-sync. Their content and cadence are not usage events.
        if request.endpoint.endswith("_page") and request.args.get("notes") == "1":
            return response
        kind = "submit"
    else:
        return response
    if current_user.is_authenticated and current_user.role in ROLES:
        track(current_user, kind, dict(area=AREAS[request.blueprint],
                                      action=request.endpoint.rsplit(".", 1)[-1],
                                      method=request.method, status=response.status_code))
    return response


def activity(day, role, search, session_id, page, today):
    """Filter retained log lines; a selected session can span UTC dates."""
    directory = _directory()
    days = (today - timedelta(days=i) for i in range(RETENTION_DAYS)) if session_id or day is None else (day,)
    offset, limit = (page - 1) * 40, 40
    rows, matched, quota = [], 0, False
    session_bytes = session_id.encode("ascii") if session_id else None
    for date in days:
        try:
            with (directory / f"{date.isoformat()}.jsonl").open("rb") as file:
                data = file.read(DAILY_LIMIT)
        except FileNotFoundError:
            continue
        quota |= len(data) > DAILY_LIMIT - 8192
        for line in reversed(data.splitlines()):
            if session_bytes and session_bytes not in line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if (role and row["role"] != role) or (session_id and row["session"] != session_id):
                continue
            if search and search not in (row["name"] + " " + row["account"]).casefold():
                continue
            if matched >= offset:
                rows.append(row)
            matched += 1
            if len(rows) > limit:
                return rows[:limit], True, quota
    return rows, False, quota
