"""
One-time local development database bootstrap.

Run this yourself in your own terminal (it must be run interactively so the
password prompt can read your keystrokes):

    .venv\\Scripts\\python.exe scripts\\bootstrap_db.py

You will be asked for your local MySQL root password; it is not echoed to
the screen, is used only in-memory for this one connection, and is never
written to disk, logged, or printed. Safe to re-run.

It creates the adaptive_english_lms database and a dedicated least-privilege
local development user (adaptive_lms_dev) scoped only to that database, then
writes the generated application credentials into .env.
"""
import getpass
import secrets
import sys
from pathlib import Path

import pymysql

DB_NAME = "adaptive_english_lms"
APP_DB_USER = "adaptive_lms_dev"
DB_HOST = "localhost"
DB_PORT = 3306

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def update_env_file(env_path: Path, values: dict) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

    seen = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0] if "=" in stripped and not stripped.startswith("#") else None
        if key in values:
            new_lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            new_lines.append(line)

    for key, value in values.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> int:
    root_password = getpass.getpass("Enter MySQL root password: ")

    try:
        connection = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user="root",
            password=root_password,
            charset="utf8mb4",
        )
    except pymysql.err.OperationalError as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        return 1
    finally:
        root_password = None  # discard as soon as it is no longer needed

    try:
        app_password = secrets.token_urlsafe(32)
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
            )
            cursor.execute(
                "CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s;",
                (APP_DB_USER, DB_HOST, app_password),
            )
            cursor.execute(
                "ALTER USER %s@%s IDENTIFIED BY %s;",
                (APP_DB_USER, DB_HOST, app_password),
            )
            cursor.execute(
                f"GRANT ALL PRIVILEGES ON `{DB_NAME}`.* TO %s@%s;",
                (APP_DB_USER, DB_HOST),
            )
            cursor.execute("FLUSH PRIVILEGES;")
        connection.commit()
    finally:
        connection.close()

    update_env_file(
        ENV_PATH,
        {
            "DATABASE_HOST": DB_HOST,
            "DATABASE_PORT": str(DB_PORT),
            "DATABASE_NAME": DB_NAME,
            "DATABASE_USER": APP_DB_USER,
            "DATABASE_PASSWORD": app_password,
        },
    )
    app_password = None  # discard as soon as it is no longer needed

    print("Bootstrap complete.")
    print(f"Database '{DB_NAME}' is ready (utf8mb4 / utf8mb4_0900_ai_ci).")
    print(f"Application user '{APP_DB_USER}' created, scoped only to '{DB_NAME}'.*")
    print("Credentials were written to .env and were not displayed here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
