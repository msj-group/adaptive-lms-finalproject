"""Inactive-until-configured, short cloud research quality entry point.

No Flask factory, dotenv, LMS writer fallback, purge, model or API-key call.
Only RESEARCH_QUALITY_DATABASE_* supplies the dedicated SELECT principal.
Default: aggregate JSON to stdout, Telegram/heartbeat/file storage disabled.
Optional operations directory must be private and persistent, shared by the
single scheduled instance; its OS lock is not a distributed multi-host lease.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import ssl
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

from sqlalchemy import create_engine, text
from hashlib import sha256
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool


ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "research_quality_runtime", ROOT / "app" / "services" / "research_v2_quality.py")
quality = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quality)
MAX_JSON_BYTES = 524_288
LIBYA = timezone(timedelta(hours=2))
LABEL_YIELD_DAY_MS = 86_400_000
LABEL_YIELD_ALERT_SEMANTICS = "zero-primary-labels.daily-two-populated.v1"


class RunnerError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _required(name):
    value = os.environ.get(name, "")
    if not value:
        raise RunnerError("configuration_missing")
    return value


def _integer(name, default, low, high):
    try:
        raw = os.environ.get(name, str(default))
        if not re.fullmatch(r"[0-9]+", raw):
            raise ValueError
        value = int(raw)
    except (ValueError, TypeError):
        raise RunnerError("configuration_invalid") from None
    if not low <= value <= high:
        raise RunnerError("configuration_invalid")
    return value


def _engine(prefix="RESEARCH_QUALITY_DATABASE_"):
    """Explicit least-privilege account; never use DATABASE_*/MYSQL* defaults."""
    username = _required(prefix + "USER")
    if username.lower() in ("root", "mysql", "admin"):
        raise RunnerError("select_principal_required")
    connect = {"connect_timeout": 5, "read_timeout": 8, "write_timeout": 8}
    ca = os.environ.get(prefix + "SSL_CA")
    if ca:
        context = ssl.create_default_context(cafile=ca)
        connect["ssl"] = context
    url = URL.create("mysql+pymysql", username=username,
                     password=_required(prefix + "PASSWORD"),
                     host=_required(prefix + "HOST"),
                     port=_integer(prefix + "PORT", 3306, 1, 65535),
                     database=_required(prefix + "NAME"),
                     query={"charset": "utf8mb4"})
    return create_engine(url, poolclass=NullPool, hide_parameters=True,
                         connect_args=connect, echo=False)


def _snapshot(engine, start, end, cutoff, scope="technical"):
    """MySQL explicit read-only snapshot, always rolled back and disconnected."""
    with engine.connect() as connection:
        # These are transaction controls, never application/source writes.
        # No GET_LOCK, SELECT FOR UPDATE, configuration gate or purge helper.
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        cutoff = int(time.time() * 1_000)
        try:
            return quality.collect_quality_snapshot(connection, period_start_ms=start,
                                                    period_end_ms=end, cutoff_ms=cutoff,
                                                    scope_code=scope)
        finally:
            connection.rollback()


def _publish(snapshot):
    """Optional separate principal: INSERT only into aggregate run/report tables.

    Configure grants separately; never use the LMS writer or SELECT principal.
    No event, prompt, participant, partition or configuration can be updated here.
    """
    if os.environ.get("RESEARCH_QUALITY_REPORTS_ENABLED") != "1":
        return
    prefix = "RESEARCH_QUALITY_REPORT_DATABASE_"
    if _required(prefix + "USER") == _required("RESEARCH_QUALITY_DATABASE_USER"):
        raise RunnerError("separate_report_principal_required")
    for suffix in ("HOST", "NAME"):
        if _required(prefix + suffix) != _required("RESEARCH_QUALITY_DATABASE_" + suffix):
            raise RunnerError("report_destination_mismatch")
    engine = _engine(prefix)
    scope_columns = ("configuration_id", "provenance", "protocol_version", "feature_version", "release_ref",
        "analysis_partition", "scope_code", "schema_version", "monitor_version", "aggregate_scope_key")
    period = ("period_start_ms", "period_end_ms", "cutoff_ms")
    try:
        with engine.begin() as connection:
            for group in snapshot["groups"]:
                values = {k: group["scope"][k] for k in scope_columns}
                values.update({k: snapshot[k] for k in period})
                created = int(time.time() * 1000)
                expiry = snapshot["period_start_ms"] + 45 * 86400000
                if expiry <= created or values["scope_code"] not in {"technical", "development"}:
                    raise RunnerError("report_scope_invalid")
                run_key = sha256((values["aggregate_scope_key"] + ":" + str(values["period_start_ms"])
                                  + ":" + str(values["period_end_ms"])).encode("ascii")).hexdigest()
                exists = connection.execute(text("SELECT id FROM research_quality_runs WHERE run_key=:key"), {"key": run_key}).scalar()
                if exists is not None:
                    continue
                run = dict(values, run_key=run_key, started_at_ms=created, finished_at_ms=created,
                           status="succeeded", error_code=None, expiry_at_ms=expiry)
                cols = tuple(run)
                statement = "INSERT INTO research_quality_runs (" + ",".join(cols) + ") VALUES (" + ",".join(":" + c for c in cols) + ")"
                result = connection.execute(text(statement), run)
                report = dict(values, run_id=result.lastrowid, created_at_ms=created, expiry_at_ms=expiry)
                report.update({k: group["metrics"][k] for k in quality.METRIC_NAMES})
                cols = tuple(report)
                statement = "INSERT INTO research_quality_reports (" + ",".join(cols) + ") VALUES (" + ",".join(":" + c for c in cols) + ")"
                connection.execute(text(statement), report)
    finally:
        engine.dispose()


def _private_directory():
    raw = os.environ.get("RESEARCH_QUALITY_OPERATIONS_DIR")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink():
        raise RunnerError("operations_path_invalid")
    resolved = path.resolve()
    # In a local checkout use ignored instance only. Hosted /data paths are
    # configured privately; no source/static directory can expose snapshots.
    if ROOT == resolved or ROOT in resolved.parents:
        if ROOT / "instance" not in resolved.parents and resolved != ROOT / "instance":
            raise RunnerError("operations_path_invalid")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise RunnerError("operations_permissions_invalid")
    return resolved


@contextmanager
def _lease(directory):
    if directory is None:
        # Output-only manual operation has no shared files or notifications.
        yield
        return
    lock_path = directory / "quality.lock"
    if lock_path.is_symlink():
        raise RunnerError("operations_path_invalid")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    lock_file = os.fdopen(descriptor, "r+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            if lock_path.stat().st_size == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RunnerError("lease_unavailable") from None
        else:
            import fcntl
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RunnerError("lease_unavailable") from None
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def _load_state(directory, now):
    if directory is None:
        return {"incidents": {}, "daily_date": None}
    path = directory / "quality-state.json"
    if not path.exists():
        return {"incidents": {}, "daily_date": None}
    if path.is_symlink() or path.stat().st_size > MAX_JSON_BYTES:
        raise RunnerError("operations_state_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        incidents = value["incidents"]
        if not isinstance(incidents, dict) or len(incidents) > 512:
            raise ValueError
        for key, incident in incidents.items():
            if (not re.fullmatch(r"[a-z_]+(?::[a-f0-9]{64})?", key)
                    or set(incident) != {"active", "notified_active", "first_seen_ms", "last_seen_ms", "expires_ms"}
                    or type(incident["active"]) is not bool
                    or incident["notified_active"] not in (True, False, None)
                    or any(type(incident[n]) is not int for n in ("first_seen_ms", "last_seen_ms", "expires_ms"))):
                raise ValueError
        value["incidents"] = {k: v for k, v in incidents.items() if v["expires_ms"] > now}
        evidence = value.get("rate_evidence", {})
        if not isinstance(evidence, dict) or len(evidence) > 512:
            raise ValueError
        for key, item in evidence.items():
            if (not re.fullmatch(r"[a-z_]+:[a-f0-9]{64}", key)
                    or set(item) != {"end_ms", "streak", "expiry_ms"}
                    or any(type(v) is not int for v in item.values()) or not -2 <= item["streak"] <= 2):
                raise ValueError
        value["rate_evidence"] = {k: v for k, v in evidence.items() if v["expiry_ms"] > now}
        label_evidence = value.get("label_yield_evidence", {})
        if not isinstance(label_evidence, dict) or len(label_evidence) > 512:
            raise ValueError
        for key, item in label_evidence.items():
            if (not re.fullmatch(r"primary_labels_absent:[a-f0-9]{64}", key)
                    or set(item) != {"last_start_ms", "last_end_ms", "horizon_ms", "semantics", "streak", "expiry_ms"}
                    or item["semantics"] != LABEL_YIELD_ALERT_SEMANTICS
                    or any(type(item[n]) is not int for n in
                           ("last_start_ms", "last_end_ms", "horizon_ms", "streak", "expiry_ms"))
                    or item["last_start_ms"] < 0
                    or item["horizon_ms"] != LABEL_YIELD_DAY_MS
                    or item["last_end_ms"] - item["last_start_ms"] != LABEL_YIELD_DAY_MS
                    or (item["last_start_ms"] + 7_200_000) % LABEL_YIELD_DAY_MS
                    or item["streak"] not in (-2, -1, 1, 2)):
                raise ValueError
        value["label_yield_evidence"] = {k: v for k, v in label_evidence.items() if v["expiry_ms"] > now}
        if value.get("daily_date") is not None:
            datetime.strptime(value["daily_date"], "%Y-%m-%d")
        return value
    except (ValueError, KeyError, TypeError):
        raise RunnerError("operations_state_invalid") from None


def _write_json(directory, name, value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
        raise RunnerError("aggregate_too_large")
    destination, temporary = directory / name, directory / (name + ".tmp")
    if destination.is_symlink() or temporary.is_symlink():
        raise RunnerError("operations_path_invalid")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(payload + "\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(destination)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _post(url, payload=None):
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    data = json.dumps(payload).encode("utf-8") if payload is not None else b""
    request = Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=8) as response:
            if response.status != 200:
                raise RunnerError("notification_unavailable")
            body = response.read(16_385)
            if len(body) > 16_384:
                raise RunnerError("notification_unavailable")
            return body
    except (HTTPError, URLError, TimeoutError, OSError):
        # URLs contain private bot/heartbeat tokens. Never log the exception.
        raise RunnerError("notification_unavailable") from None


def _telegram(message):
    token = _required("RESEARCH_QUALITY_TELEGRAM_BOT_TOKEN")
    chat = _required("RESEARCH_QUALITY_TELEGRAM_CHAT_ID")
    # Private user chat only. Negative group/channel destinations are refused.
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,100}", token) or not re.fullmatch(r"[1-9][0-9]{0,19}", chat):
        raise RunnerError("notification_configuration_invalid")
    try:
        value = json.loads(_post("https://api.telegram.org/bot" + token + "/sendMessage",
                                 {"chat_id": chat, "text": message,
                                  "disable_web_page_preview": True, "protect_content": True}))
        if (value.get("ok") is not True or str(value["result"]["chat"]["id"]) != chat
                or value["result"]["chat"].get("type") != "private"):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise RunnerError("notification_unavailable") from None


def _notify(state, active_keys, now, *, snapshot_succeeded):
    incidents = state["incidents"]
    # A failed query supplies no evidence that previous source incidents healed.
    if not snapshot_succeeded:
        active_keys = set(active_keys) | {key for key, v in incidents.items() if v["active"]}
    for key in set(incidents) | set(active_keys):
        incident = incidents.setdefault(key, {"active": True, "notified_active": None,
                                             "first_seen_ms": now, "last_seen_ms": now,
                                             "expires_ms": now + 45 * 86_400_000})
        was_active = incident["active"]
        incident["active"] = key in active_keys
        if incident["active"]:
            if not was_active:
                incident["first_seen_ms"] = now
            incident["last_seen_ms"] = now
            incident["expires_ms"] = now + 45 * 86_400_000
    enabled = os.environ.get("RESEARCH_QUALITY_TELEGRAM_ENABLED") == "1"
    if not enabled:
        return True
    delivered = True
    for key, incident in sorted(incidents.items()):
        if incident["notified_active"] == incident["active"]:
            continue
        if not incident["active"] and incident["notified_active"] is None:
            # An incident that never reached the recipient needs no recovery.
            incident["notified_active"] = False
            continue
        if key.startswith("primary_labels_absent:"):
            message = ("تنبيه جمع البيانات: لم تظهر عينات تستوفي شروط التدريب في فترتي مراجعة يوميتين كافيتين. "
                       "راجعوا ملف جودة البيانات اليومي؛ أعداد لوحة الباحث خام. هذا تنبيه للمراجعة، وليس حكمًا على النموذج."
                       if incident["active"] else
                       "مراقبة جمع البيانات: ظهرت عينات تستوفي شروط الجمع في فترتي مراجعة يوميتين كافيتين. "
                       "هذا لا يعني اكتمال البيانات أو تأكيد دقة النموذج.")
        else:
            message = ("تنبيه مراقبة المنصة: تعذر تأكيد سلامة الفحص أو ظهرت مشكلة تقنية مهمة. "
                       "راجعوا تقرير الباحث قبل اتخاذ إجراء. لا توجد بيانات طلاب في هذا التنبيه."
                       if incident["active"] else
                       "مراقبة المنصة: عاد الفحص للعمل أو زالت المشكلة التقنية التي أُبلغتم بها. "
                       "راجعوا التقرير؛ هذا لا يعني تأكيد دقة النموذج.")
        link = os.environ.get("RESEARCH_QUALITY_WORKSPACE_URL", "")
        parsed = urlsplit(link)
        if parsed.scheme == "https" and parsed.hostname and parsed.path == "/research/v2" and not (parsed.username or parsed.password or parsed.query or parsed.fragment):
            message += "\n" + link
        try:
            _telegram(message)
            incident["notified_active"] = incident["active"]
        except RunnerError:
            delivered = False
    return delivered


def _daily_label_yield_incidents(state, snapshot, now, active):
    """Assess two populated, nonoverlapping completed Tripoli days, not hours.

    Missing/small days provide no new evidence. Weekends do not erase a prior
    populated day's streak. This investigation rule cannot certify model quality.
    """
    if snapshot is None:
        return
    start, end, cutoff = (snapshot.get(n) for n in ("period_start_ms", "period_end_ms", "cutoff_ms"))
    if (snapshot.get("scope_code") != "technical"
            or any(type(value) is not int for value in (start, end, cutoff))
            or start < 0 or end - start != LABEL_YIELD_DAY_MS
            or not end <= cutoff <= now or (start + 7_200_000) % LABEL_YIELD_DAY_MS):
        raise RunnerError("label_yield_cohort_invalid")
    evidence = state.setdefault("label_yield_evidence", {})
    for group in snapshot["groups"]:
        counts = group.get("evidence", {}).get("label_yield")
        if counts is None:
            continue
        matured, eligible = counts.get("matured_rendered"), counts.get("primary_eligible_answers")
        if (counts.get("semantics") != quality.LABEL_YIELD_SEMANTICS
                or type(matured) is not int or type(eligible) is not int
                or not 0 <= eligible <= matured):
            raise RunnerError("label_yield_cohort_invalid")
        if matured < quality.LABEL_YIELD_MINIMUM_MATURED:
            continue
        digest = sha256((group["scope"]["aggregate_scope_key"] + ":" + quality.LABEL_YIELD_SEMANTICS
                         + ":" + LABEL_YIELD_ALERT_SEMANTICS).encode("ascii")).hexdigest()
        key = "primary_labels_absent:" + digest
        old = evidence.get(key)
        if old and (old["last_end_ms"] >= end or start < old["last_end_ms"]):
            continue
        sign = 1 if eligible == 0 else -1
        streak = sign * min(2, abs(old["streak"]) + 1) if old and old["streak"] * sign > 0 else sign
        evidence[key] = {"last_start_ms": start, "last_end_ms": end,
                         "horizon_ms": LABEL_YIELD_DAY_MS, "semantics": LABEL_YIELD_ALERT_SEMANTICS,
                         "streak": streak, "expiry_ms": now + 45 * LABEL_YIELD_DAY_MS}
        if streak == 2:
            active.add(key)
        elif streak == -2:
            active.discard(key)


def _quality_incidents(state, snapshot, now, *, daily_snapshot=None):
    """Optional pilot thresholds: hourly rates and populated-day label yield.

    Missing/small cohorts cannot certify recovery. Thresholds are engineering
    investigation triggers, never evidence of emotion or model failure.
    """
    active = {issue["rule_code"] + ":" + issue["aggregate_scope_key"] for issue in quality.critical_issues(snapshot)}
    global_key = "invalid_rate:" + sha256(b"v2-global-technical-delivery").hexdigest()
    delivery = snapshot.get("global_delivery")
    if (global_key in state["incidents"] and state["incidents"][global_key]["active"]
            and (delivery is None or delivery["attempts"] < 20
                 or delivery.get("accepted_client_attempts", 0) == 0
                 or delivery["rejected_attempts"] / delivery["attempts"] >= .95)):
        active.add(global_key)
    presence_key = "delivery_stopped:" + sha256(b"v2-global-server-presence").hexdigest()
    if (presence_key in state["incidents"] and state["incidents"][presence_key]["active"]
            and (delivery is None or delivery.get("recovery_evidence") != "clean_matured_cohort")):
        # No recent sessions, only server outcomes or an immature cohort cannot
        # turn an active client-silence alert into a false recovery message.
        active.add(presence_key)
    retention_key = "expiry_backlog:" + sha256(b"v2-global-retention").hexdigest()
    retention = snapshot.get("global_retention")
    if (retention_key in state["incidents"] and state["incidents"][retention_key]["active"]
            and (retention is None or retention.get("due_sessions") != 0)):
        # A full-scope clean SELECT can resolve a backlog incident. It cannot
        # establish that the scheduled job ran; its heartbeat is separate.
        active.add(retention_key)
    previous = {k for k, v in state["incidents"].items() if v["active"] and k != global_key and k.split(":")[0] in {
        "invalid_rate", "clock_alignment", "coverage_gap", "prompt_delivery", "response_rate", "primary_labels_absent"}}
    active.update(previous)
    # Disabling rate evaluation provides no clean cohorts and cannot certify
    # recovery of an existing optional incident. Keep it open until evaluation
    # resumes and supplies the required positive recovery evidence.
    if os.environ.get("RESEARCH_QUALITY_RATE_ALERTS_ENABLED") != "1":
        return active
    evidence = state.setdefault("rate_evidence", {})
    for group in snapshot["groups"]:
        m = group["metrics"]
        checks = (
            ("invalid_rate", m["events_received"], m["events_invalid"], 100, .05),
            ("clock_alignment", m["prompts_rendered"], m["clock_unknown_windows"] + m["clock_uncertain_windows"], 10, .20),
            ("coverage_gap", m["prompts_rendered"], m["low_coverage_windows"], 10, .30),
            ("prompt_delivery", m["prompts_reserved"], m["prompts_expired"], 10, .50),
            ("response_rate", m["prompts_matured"], m["prompts_unanswered_matured"], 20, .80),
        )
        for rule, denominator, numerator, minimum, threshold in checks:
            if denominator < minimum:
                continue
            key = rule + ":" + group["scope"]["aggregate_scope_key"]
            old = evidence.get(key)
            end = snapshot["period_end_ms"]
            if old and old["end_ms"] >= end:
                continue
            bad = numerator / denominator >= threshold
            consecutive = old is not None and old["end_ms"] == snapshot["period_start_ms"]
            sign = 1 if bad else -1
            streak = sign * min(2, abs(old["streak"]) + 1) if consecutive and old["streak"] * sign > 0 else sign
            evidence[key] = {"end_ms": end, "streak": streak, "expiry_ms": now + 45 * 86400000}
            if streak == 2:
                active.add(key)
            elif streak == -2:
                active.discard(key)
    _daily_label_yield_incidents(state, daily_snapshot, now, active)
    return active


def _heartbeat():
    raw = os.environ.get("RESEARCH_QUALITY_HEARTBEAT_URL")
    if not raw:
        return False
    parsed = urlsplit(raw)
    if (parsed.scheme != "https" or parsed.hostname != "hc-ping.com"
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", parsed.path)):
        raise RunnerError("heartbeat_configuration_invalid")
    _post(raw)
    return True


def _safe_error(error):
    if isinstance(error, (RunnerError, quality.QualitySnapshotError)):
        return error.code
    return "database_or_snapshot_unavailable"


def _run(directory):
    now = int(time.time() * 1_000)
    state = _load_state(directory, now)
    engine = None
    snapshot = None
    error_code = None
    daily = None
    daily_development = None
    completed_daily_date = None
    try:
        engine = _engine()
        end = now // 3_600_000 * 3_600_000
        snapshot = _snapshot(engine, end - 3_600_000, end, now)
        if directory is not None:
            # Daily review is a protected aggregate file, not a Telegram spam.
            local = datetime.fromtimestamp(now / 1_000, timezone.utc).astimezone(LIBYA)
            day = local.date() - timedelta(days=1)
            if local.hour >= 9 and state.get("daily_date") != day.isoformat():
                day_start = datetime.combine(day, datetime.min.time(), tzinfo=LIBYA)
                start = int(day_start.timestamp() * 1_000)
                daily = _snapshot(engine, start, start + 86_400_000, now)
                daily_development = _snapshot(engine, start, start + 86_400_000, now, "development")
                completed_daily_date = day.isoformat()
        _publish(snapshot)
        if daily is not None:
            _publish(daily)
        if daily_development is not None:
            _publish(daily_development)
    except Exception as error:
        error_code = _safe_error(error)
    finally:
        if engine is not None:
            engine.dispose()
    succeeded = error_code is None
    active = {"checker_failure"}
    if succeeded:
        try:
            # Each snapshot freezes a later cutoff than this run's start time.
            # Assess only after all snapshots and publication have completed.
            assessment_now = int(time.time() * 1_000)
            active = _quality_incidents(state, snapshot, assessment_now, daily_snapshot=daily)
        except Exception as error:
            error_code = _safe_error(error)
            succeeded = False
    if directory is not None:
        if snapshot is not None and succeeded:
            _write_json(directory, "quality-latest.json", snapshot)
        if daily is not None and succeeded:
            _write_json(directory, "quality-daily.json", daily)
        if daily_development is not None and succeeded:
            _write_json(directory, "quality-development-daily.json", daily_development)
        if succeeded and completed_daily_date is not None:
            state["daily_date"] = completed_daily_date
    notifications_ok = _notify(state, active, now, snapshot_succeeded=succeeded)
    if directory is not None:
        _write_json(directory, "quality-state.json", state)
    receipt = {"at_ms": now, "success": succeeded, "error_code": error_code,
               "notification_path": "disabled" if os.environ.get("RESEARCH_QUALITY_TELEGRAM_ENABLED") != "1"
                                    else "confirmed" if notifications_ok else "failed",
               "heartbeat_configured": bool(os.environ.get("RESEARCH_QUALITY_HEARTBEAT_URL")),
               "snapshot": snapshot if succeeded else None}
    # Only signal independent success after query, report persistence and every
    # due notification succeeded. A Telegram failure must remain externally visible.
    if succeeded and notifications_ok:
        try:
            receipt["heartbeat_sent"] = _heartbeat()
        except RunnerError as error:
            receipt.update(success=False, error_code=error.code, heartbeat_sent=False)
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return 0 if receipt["success"] and notifications_ok else 1


def main():
    # Process-wide ceiling supplements short query/connection/network timeouts.
    # In a forced exit the OS closes the DB socket and its read-only transaction.
    def deadline():
        print('{"success":false,"error_code":"runtime_limit"}', flush=True)
        os._exit(2)
    timer = threading.Timer(120, deadline)
    timer.daemon = True
    timer.start()
    try:
        directory = _private_directory()
        if directory is None and (os.environ.get("RESEARCH_QUALITY_TELEGRAM_ENABLED") == "1"
                                  or os.environ.get("RESEARCH_QUALITY_HEARTBEAT_URL")):
            raise RunnerError("persistent_operations_store_required")
        with _lease(directory):
            return _run(directory)
    except Exception as error:
        print(json.dumps({"success": False, "error_code": _safe_error(error)}, sort_keys=True))
        return 1
    finally:
        timer.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
