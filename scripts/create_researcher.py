"""
One-time local development helper to create a Researcher account
(Phase 6 / M01).

Run this yourself in your own terminal (it must be run interactively so the
password prompts can read your keystrokes):

    .venv\\Scripts\\python.exe scripts\\create_researcher.py

You will be asked for an email, full name, and a password (input hidden,
confirmed twice). The password is hashed with Argon2id by the project's own
``app.security.passwords.hash_password`` before it is stored, and is never
written to disk, logged, echoed or printed in plain text.

**The password is never accepted as a command-line argument.** A password on
a command line lands in the shell history, in the process list and in any
terminal recording, which is exactly what ``getpass`` exists to avoid -- so
this script takes no arguments at all and refuses to run non-interactively.

**A Researcher works inside the Researcher portal and nowhere else.** The
account created here reaches ``/research/dashboard``, reads the pseudonymous
participant pages, and (Phase 6 / M02A) writes and internally activates
experiment protocol versions in the protocol catalogue, which holds no
participant data. It cannot open any Administrator page, cannot create a
participant, cannot author or activate consent wording, and cannot consent
for anybody.

``scripts/create_admin.py`` is deliberately untouched: the two scripts create
different roles and share nothing but the password service, and editing a
working Administrator bootstrap to save a few lines here would put the first
Administrator account at risk for no benefit.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models.user import User, UserRole, UserStatus
from app.security.passwords import hash_password

MIN_PASSWORD_LENGTH = 12


def main() -> int:
    if not sys.stdin.isatty():
        print(
            "This script must be run interactively so the password can be typed "
            "without being echoed or recorded.",
            file=sys.stderr,
        )
        return 1

    app = create_app("development")
    with app.app_context():
        email = input("Researcher email: ").strip().lower()
        if not email:
            print("Email is required.", file=sys.stderr)
            return 1

        if User.query.filter_by(email=email).first() is not None:
            print(f"A user with email '{email}' already exists.", file=sys.stderr)
            return 1

        full_name = input("Researcher full name: ").strip()
        if not full_name:
            print("Full name is required.", file=sys.stderr)
            return 1

        password = getpass.getpass("Researcher password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        if len(password) < MIN_PASSWORD_LENGTH:
            print(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
                file=sys.stderr,
            )
            return 1

        user = User(
            email=email,
            full_name=full_name,
            password_hash=hash_password(password),
            role=UserRole.RESEARCHER.value,
            status=UserStatus.ACTIVE.value,
        )
        password = None
        confirm = None

        db.session.add(user)
        try:
            db.session.commit()
        except Exception:
            # Roll back completely rather than leaving a half-written
            # account behind, and say nothing about the driver or the SQL.
            db.session.rollback()
            print(
                "The researcher account could not be created. Nothing was written.",
                file=sys.stderr,
            )
            return 1

        print(f"Researcher account created for '{email}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
