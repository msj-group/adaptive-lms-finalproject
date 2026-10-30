"""Daily retention entry point. Deletes only expired research data.

The scheduler supplies no credentials: settings come from the deployment's
environment. A count-only audit log is written under ignored instance/.
An optional independent success heartbeat is sent only after the real purge
service completes and its local receipt has been durably written. An empty
expiry scan is successful even though the service correctly reports no delete.
"""
import json
import os
from pathlib import Path
import re
import ssl
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import create_app  # noqa: E402
from app.services.research_operator import retention_report  # noqa: E402


class RetentionRunnerError(ValueError):
    """Closed error code, without URLs, credentials or driver details."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _heartbeat_url():
    raw = os.environ.get('RESEARCH_RETENTION_HEARTBEAT_URL')
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme != 'https' or parsed.hostname != 'hc-ping.com'
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or parsed.query or parsed.fragment
                or not re.fullmatch(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', parsed.path)):
            raise ValueError
    except ValueError:
        raise RetentionRunnerError('heartbeat_configuration_invalid') from None
    return raw


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _heartbeat(url):
    # Never follow a redirect or expose a private heartbeat URL in an error.
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(Request(url, data=b'', method='POST'), timeout=8) as response:
            if response.status != 200 or len(response.read(1025)) > 1024:
                raise RetentionRunnerError('heartbeat_unavailable')
    except (HTTPError, URLError, TimeoutError, OSError):
        raise RetentionRunnerError('heartbeat_unavailable') from None


def _write_receipt(log_path, record):
    if log_path.is_symlink():
        raise RetentionRunnerError('receipt_path_invalid')
    if log_path.exists() and log_path.stat().st_size > 1_048_576:
        previous = log_path.with_suffix('.previous.jsonl')
        if previous.is_symlink():
            raise RetentionRunnerError('receipt_path_invalid')
        log_path.replace(previous)
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, 'a', encoding='utf-8') as log:
        log.write(json.dumps(record, sort_keys=True) + '\n')
        log.flush()
        os.fsync(log.fileno())
    log_path.chmod(0o600)


def main():
    record = {'at': datetime.now(timezone.utc).isoformat()}
    result = 0
    log_path = None
    try:
        # Reject bad configuration before the first database/purge operation.
        heartbeat = _heartbeat_url()
        record.update(heartbeat_configured=heartbeat is not None, heartbeat_sent=False)
        log_dir = Path(os.environ.get('RESEARCH_RETENTION_LOG_DIR', str(ROOT / 'instance')))
        if log_dir.is_symlink():
            raise RetentionRunnerError('receipt_path_invalid')
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = log_dir / 'research-retention.jsonl'
        app = create_app(os.environ.get('FLASK_ENV', 'development'))
        with app.app_context():
            days = app.config.get('RESEARCH_RETENTION_DAYS')
            if days is None:
                raise ValueError('Retention is not configured')
            report = retention_report(days, execute=True, service_principal="daily_retention")
            if not report.executed and any((report.sessions, report.events, report.prompts, report.archives)):
                raise RetentionRunnerError('purge_not_completed')
            record.update(retention_days=days, sessions=report.sessions, events=report.events,
                          prompts=report.prompts, archives=report.archives,
                          executed=report.executed, retention_success=True, success=True)
        # A ping must never get ahead of either the actual operation or the
        # durable local receipt. It is distinct from the quality-reader ping.
        _write_receipt(log_path, record)
        if heartbeat is not None:
            _heartbeat(heartbeat)
            record['heartbeat_sent'] = True
            _write_receipt(log_path, record)
    except Exception as error:
        # Driver exception messages can include SQL and data: log only the
        # exception class. Scheduler failure remains visible through exit=1.
        record.update(success=False, error_type=type(error).__name__)
        if isinstance(error, RetentionRunnerError):
            record['error_code'] = error.code
        result = 1
        if log_path is not None:
            try:
                _write_receipt(log_path, record)
            except Exception:
                # Receipt failure also withholds success; stdout stays redacted.
                record['receipt_error_code'] = 'receipt_unavailable'
    print(json.dumps(record, sort_keys=True))
    return result


if __name__ == '__main__':
    raise SystemExit(main())
