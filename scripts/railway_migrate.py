"""Serialized, forward-only Railway pre-deploy migration. Never seeds accounts.

Runs without the application volume, using separate schema-scoped credentials.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
PARENT_HEAD = "7d4e2a9c6013"
EXPECTED_HEAD = "8e5f3b0d7124"


def main():
    try:
        if os.environ.get("FLASK_ENV") != "production":
            raise ValueError("FLASK_ENV must be production")
        for source, target in (("MIGRATION_DATABASE_USER", "DATABASE_USER"),
                               ("MIGRATION_DATABASE_PASSWORD", "DATABASE_PASSWORD")):
            if not os.environ.get(source):
                raise ValueError("Separate migration credentials are required")
            os.environ[target] = os.environ[source]
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        from wsgi import application
        from app.extensions import db
        from alembic import command
        from sqlalchemy import text
        from alembic.runtime.migration import MigrationContext
        with application.app_context(), db.engine.connect() as lock_connection:
            acquired = lock_connection.execute(text("SELECT GET_LOCK('aelms_schema_migration', 30)")).scalar()
            if acquired != 1:
                raise ValueError("The schema migration lock is unavailable")
            try:
                migration_config = application.extensions["migrate"].migrate.get_config(str(ROOT / "migrations"))
                from alembic.script import ScriptDirectory
                if ScriptDirectory.from_config(migration_config).get_heads() != [EXPECTED_HEAD]:
                    raise ValueError("Unexpected source migration head")
                with db.engine.connect() as connection:
                    current_heads = MigrationContext.configure(connection).get_current_heads()
                    if current_heads not in ((PARENT_HEAD,), (EXPECTED_HEAD,)):
                        raise ValueError("Unexpected parent migration head")
                    before_count = connection.execute(text("SELECT COUNT(*) FROM listening_activities")).scalar_one()
                print("Applying approved forward migration: " + current_heads[0] + " -> " + EXPECTED_HEAD, flush=True)
                # The Flask-Migrate CLI decorator prints raw driver errors.
                # Use its same Alembic configuration with our redacted boundary.
                command.upgrade(migration_config, EXPECTED_HEAD)
                with db.engine.connect() as connection:
                    if MigrationContext.configure(connection).get_current_heads() != (EXPECTED_HEAD,):
                        raise ValueError("Unexpected migration head")
                    after_count = connection.execute(text("SELECT COUNT(*) FROM listening_activities")).scalar_one()
                    if after_count < before_count:
                        raise ValueError("Listening record count decreased")
                    if current_heads == (PARENT_HEAD,):
                        changed_rates = connection.execute(text("SELECT COUNT(*) FROM listening_activities WHERE playback_speed_mask <> 15")).scalar_one()
                        if changed_rates:
                            raise ValueError("Existing playback choices were not preserved")
                print("Listening records preserved; playback schema verified", flush=True)
                command.check(migration_config)
            finally:
                lock_connection.execute(text("SELECT RELEASE_LOCK('aelms_schema_migration')"))
        print("Migration head and model schema verified: " + EXPECTED_HEAD, flush=True)
        return 0
    except (Exception, SystemExit) as error:
        # Do not print driver messages, SQL/parameters or a connection URI.
        print("Railway migration failed type=" + type(error).__name__, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
