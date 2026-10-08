"""Own-account mutations with current authentication, version and lock checks."""
import uuid

from flask import current_app
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.extensions import db
from app.models import AccountRevision, UploadedFile
from app.models.submission_feedback import whole_second_utc
from app.security.passwords import (
    ACCOUNT_PASSWORD_MAX_LENGTH, ACCOUNT_PASSWORD_MIN_LENGTH, verify_password,
)
from app.services.account_operations import AccountConflict, _lock_account, account_snapshot
from app.services.actor_authorization import require_current_actor
from app.services.profile_photos import (
    PHOTO_ACTION, PHOTO_FILE_KEY, PROFILE_PHOTO_MAX_BYTES, latest_photo_reference,
)

ACCOUNT_ROLES = ("student", "teacher", "administrator", "researcher")
_PURPOSES = {"password", PHOTO_ACTION}


def self_service_snapshot(account):
    return {"public_id": account.public_id, "role": account.role,
            "version": account.version, "auth_version": account.auth_version}


def _serializer(purpose):
    if purpose not in _PURPOSES:
        raise ValueError("Unsupported account form purpose")
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"],
                                  salt=f"self-service.account.{purpose}.v1")


def self_service_token(account, purpose):
    return _serializer(purpose).dumps(self_service_snapshot(account))


def read_self_service_token(token, purpose):
    try:
        value = _serializer(purpose).loads(token or "", max_age=3600)
        return value if isinstance(value, dict) else None
    except BadSignature:
        return None


def _lock_own_account(public_id, role, actor_id, expected):
    if role not in ACCOUNT_ROLES:
        raise AccountConflict("This account is unavailable.")
    account, _groups = _lock_account(public_id, role)
    actor = require_current_actor(actor_id, role)
    if account.id != actor.id or expected != self_service_snapshot(account):
        raise AccountConflict("Your account changed or this form expired. Review the page and try again.")
    return account


def _record(account, action, before, after_extra=None):
    account.version += 1
    account.updated_at = whole_second_utc()
    after = account_snapshot(account)
    if after_extra:
        after.update(after_extra)
    db.session.add(AccountRevision(user_id=account.id, actor_id=account.id,
        version=account.version, action=action, before_snapshot=before, after_snapshot=after))


def change_own_password(public_id, role, actor_id, expected, current_password,
                        new_password, password_hash):
    account = _lock_own_account(public_id, role, actor_id, expected)
    # Verify against the locked, current credential, never the form's preview.
    if not isinstance(current_password, str) or not 1 <= len(current_password) <= 1024 \
            or not verify_password(account.password_hash, current_password):
        raise AccountConflict("The current password is incorrect.")
    if not isinstance(new_password, str) \
            or not ACCOUNT_PASSWORD_MIN_LENGTH <= len(new_password) <= ACCOUNT_PASSWORD_MAX_LENGTH:
        raise AccountConflict("Choose a new password between 15 and 128 characters.")
    if current_password == new_password:
        raise AccountConflict("Choose a password different from your current password.")
    if not password_hash:
        raise AccountConflict("A new password is required.")
    before = account_snapshot(account)
    account.password_hash = password_hash
    account.bump_auth_version()
    _record(account, "password", before)
    return account


def change_own_photo(public_id, role, actor_id, expected, stored):
    account = _lock_own_account(public_id, role, actor_id, expected)
    if (stored.category, stored.extension, stored.content_type) != ("image", "png", "image/png") \
            or not 0 < stored.byte_size <= PROFILE_PHOTO_MAX_BYTES:
        raise AccountConflict("This photo could not be saved. Choose another image.")
    before = account_snapshot(account)
    before[PHOTO_FILE_KEY] = latest_photo_reference(account.id)
    photo = UploadedFile(public_id=str(uuid.uuid4()), uploaded_by_id=account.id,
        storage_key=stored.storage_key, original_filename=stored.original_filename,
        extension=stored.extension, category=stored.category, content_type=stored.content_type,
        byte_size=stored.byte_size, sha256=stored.sha256)
    db.session.add(photo)
    _record(account, PHOTO_ACTION, before, {PHOTO_FILE_KEY: photo.public_id})
    return account
