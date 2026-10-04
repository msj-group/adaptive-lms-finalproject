"""Daily retention entry point. Deletes only expired research data.

The scheduler supplies no credentials: settings come from the deployment's
environment. A count-only audit log is written under ignored instance/.
"""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import create_app  # noqa: E402
from app.services.research_operator import retention_report  # noqa: E402


def main():
    log_dir = ROOT / 'instance'
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / 'research-retention.jsonl'
    record = {'at': datetime.now(timezone.utc).isoformat()}
    result = 0
    try:
        app = create_app(os.environ.get('FLASK_ENV', 'development'))
        with app.app_context():
            days = app.config.get('RESEARCH_RETENTION_DAYS')
            if days is None:
                raise ValueError('Retention is not configured')
            report = retention_report(days, execute=True)
            record.update(retention_days=days, sessions=report.sessions, events=report.events,
                          prompts=report.prompts, archives=report.archives,
                          executed=report.executed, success=True)
    except Exception as error:
        # Driver exception messages can include SQL and data: log only the
        # exception class. Scheduler failure remains visible through exit=1.
        record.update(success=False, error_type=type(error).__name__)
        result = 1
    if log_path.exists() and log_path.stat().st_size > 1_048_576:
        previous = log_path.with_suffix('.previous.jsonl')
        log_path.replace(previous)
    with log_path.open('a', encoding='utf-8') as log:
        log.write(json.dumps(record, sort_keys=True) + '\n')
    print(json.dumps(record, sort_keys=True))
    return result


if __name__ == '__main__':
    raise SystemExit(main())
