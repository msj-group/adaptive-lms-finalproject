"""Read-only operator diagnostics. Never prints secrets, identities or stored content."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app import create_app
from app.extensions import db
from app.services.research_event_dictionary import EVENT_SCHEMA_VERSION, EVENT_DICTIONARY_REVISION


def main():
    app = create_app()
    result = {"schema": EVENT_SCHEMA_VERSION, "dictionary": EVENT_DICTIONARY_REVISION,
              "provenance": app.config["RESEARCH_DATA_PROVENANCE"],
              "retention_days": app.config["RESEARCH_RETENTION_DAYS"],
              "csrf_enabled": app.config["WTF_CSRF_ENABLED"],
              "secure_cookie": app.config["SESSION_COOKIE_SECURE"],
              "proxy_hops": app.config["PROXY_TRUSTED_HOPS"]}
    with app.app_context():
        try:
            with db.engine.connect() as connection:
                result["migration"] = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                row = connection.execute(text("SELECT version_number,is_collecting,collection_starts_at,collection_ends_at FROM research_configurations WHERE current_marker=1")).first()
                result["active_configuration"] = None if not row else {
                    "version": row[0], "enabled": bool(row[1]), "period_start": row[2].isoformat(), "period_end": row[3].isoformat()}
                result["database_ready"] = result["migration"] == "7d4e2a9c6013"
        except Exception:
            result["database_ready"] = False
    storage = app.extensions["material_config"].storage_root
    result["private_storage_exists"] = storage.is_dir()
    result["external_checks"] = ["HTTPS and certificate", "daily retention job last success",
                                 "backup/restore last verified", "private storage write permissions",
                                 "governance arrangements", "production smoke acceptance"]
    print(json.dumps(result, indent=2))
    return 0 if result["database_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
