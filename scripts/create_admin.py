"""
One-time local development helper to create the first Administrator account.

Run this yourself in your own terminal (it must be run interactively so the
password prompts can read your keystrokes):

    .venv\\Scripts\\python.exe scripts\\create_admin.py

You will be asked for an email, full name, and a password (input hidden,
confirmed twice). The password is hashed with Argon2id before it is stored
and is never written to disk or printed in plain text.
"""
import getpass
import sys

from app import create_app
from app.extensions import db
from app.models.user import User, UserRole, UserStatus
from app.security.passwords import hash_password


def main() -> int:
    app = create_app("development")
    with app.app_context():
        email = input("Administrator email: ").strip().lower()
        if not email:
            print("Email is required.", file=sys.stderr)
            return 1

        if User.query.filter_by(email=email).first() is not None:
            print(f"A user with email '{email}' already exists.", file=sys.stderr)
            return 1

        full_name = input("Administrator full name: ").strip()
        if not full_name:
            print("Full name is required.", file=sys.stderr)
            return 1

        password = getpass.getpass("Administrator password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        if len(password) < 12:
            print("Password must be at least 12 characters.", file=sys.stderr)
            return 1

        user = User(
            email=email,
            full_name=full_name,
            password_hash=hash_password(password),
            role=UserRole.ADMINISTRATOR.value,
            status=UserStatus.ACTIVE.value,
        )
        password = None
        confirm = None

        db.session.add(user)
        db.session.commit()

        print(f"Administrator account created for '{email}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
