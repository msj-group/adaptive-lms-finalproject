from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

_hasher = PasswordHasher()

# Shared by administrator resets and authenticated self-service changes.
# Length-only policy; no composition rules or compromised-password blocklist.
ACCOUNT_PASSWORD_MIN_LENGTH = 15
ACCOUNT_PASSWORD_MAX_LENGTH = 128


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False
