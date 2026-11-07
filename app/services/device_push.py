"""Opt-in Web Push delivery from stored notifications, independent of visits."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from flask import current_app, request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select, update

from app.extensions import db
from app.models import Notification, User
from app.models.device_push import DevicePushSubscription as Device

COOKIE = "aelms_push_device"
ROLES = ("student", "teacher", "administrator", "researcher")


class SubscriptionConflict(ValueError):
    """The browser must renew its endpoint rather than transfer an account binding."""


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def settings():
    config = current_app.config
    enabled = config["DEVICE_PUSH_ENABLED"]
    public = config["PUSH_VAPID_PUBLIC_KEY"]
    private = config["PUSH_VAPID_PRIVATE_KEY_FILE"]
    subject = config["PUSH_VAPID_SUBJECT"]
    if enabled and (not re.fullmatch(r"[A-Za-z0-9_-]{87}", public)
                    or not private or not Path(private).is_file()
                    or not (subject.startswith("mailto:") or subject.startswith("https://"))):
        raise ValueError("Device notification keys are not configured")
    if enabled:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        try:
            key = serialization.load_pem_private_key(Path(private).read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
                raise ValueError
            encoded = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            if base64.urlsafe_b64encode(encoded).decode().rstrip("=") != public:
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise ValueError("Device notification keys are not configured") from None
    return enabled, public, private, subject


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="device-push-v1")


def device_cookie(device):
    return _serializer().dumps([device.public_id, device.user_id, device.auth_version])


def current_device(user_id):
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        public_id, owner, version = _serializer().loads(raw, max_age=90 * 86400)
        if owner != user_id:
            return None
    except (BadSignature, ValueError, TypeError):
        return None
    return db.session.scalar(select(Device).where(Device.public_id == public_id,
        Device.user_id == user_id, Device.auth_version == version))


def validate_subscription(body):
    if not isinstance(body, dict):
        raise ValueError("The device subscription is invalid.")
    endpoint = body.get("endpoint")
    if not isinstance(endpoint, str) or not 20 <= len(endpoint) <= 2048 or any(ord(c) < 33 for c in endpoint):
        raise ValueError("The device subscription is invalid.")
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        allowed = host in ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com") \
            or host.endswith(".push.services.mozilla.com") or host.endswith(".notify.windows.com")
        if url.scheme != "https" or url.port not in (None, 443) or url.username or url.password \
                or url.fragment or not allowed:
            raise ValueError
    except ValueError:
        raise ValueError("This browser's push service is not supported.") from None
    keys = body.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("The device subscription keys are invalid.")
    def key(name, size):
        value = keys.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
            raise ValueError("The device subscription keys are invalid.")
        try:
            decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        except ValueError:
            raise ValueError("The device subscription keys are invalid.") from None
        if len(decoded) != size or (name == "p256dh" and decoded[0] != 4):
            raise ValueError("The device subscription keys are invalid.")
        if name == "p256dh":
            from cryptography.hazmat.primitives.asymmetric import ec
            try:
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded)
            except ValueError:
                raise ValueError("The device subscription keys are invalid.") from None
        return value.rstrip("=")
    public, auth = key("p256dh", 65), key("auth", 16)
    expiration = body.get("expirationTime")
    expires = now() + timedelta(days=90)
    if expiration is not None:
        if isinstance(expiration, bool) or not isinstance(expiration, (int, float)):
            raise ValueError("The device subscription expiry is invalid.")
        try:
            expires = min(expires, datetime.fromtimestamp(expiration / 1000, timezone.utc).replace(tzinfo=None))
        except (ValueError, OverflowError, OSError):
            raise ValueError("The device subscription expiry is invalid.") from None
        if expires <= now():
            raise ValueError("The device subscription has expired.")
    return endpoint, public, auth, expires


def save_subscription(actor, body):
    endpoint, public, auth, expires = validate_subscription(body)
    identity = actor.id, actor.auth_version
    cookie_id = current_device(actor.id)
    owned_id = cookie_id.id if cookie_id else None
    db.session.rollback()
    user = db.session.get(User, identity[0], populate_existing=True, with_for_update=True)
    if not user or not user.is_active_account() or user.auth_version != identity[1] or user.role not in ROLES:
        raise ValueError("Your account changed. Sign in again.")
    digest = hashlib.sha256(endpoint.encode()).hexdigest()
    device = db.session.scalar(select(Device).where(Device.endpoint_hash == digest).with_for_update())
    if device and device.user_id != user.id:
        # Never move an active subscription between accounts implicitly.
        raise SubscriptionConflict("This device is linked to another account. Disable notifications in browser settings and try again.")
    active = device and device.enabled and device.auth_version == user.auth_version
    if not device:
        count = db.session.scalar(select(func.count()).select_from(Device).where(
            Device.user_id == user.id, Device.auth_version == user.auth_version,
            Device.enabled.is_(True), Device.expires_at > now()))
        if count >= 12:
            raise ValueError("Too many devices are enabled. Contact your centre administrator.")
        device = Device(user_id=user.id, auth_version=user.auth_version, endpoint_hash=digest,
                        endpoint=endpoint, p256dh=public, auth_key=auth)
        db.session.add(device)
    elif active and owned_id != device.id:
        raise SubscriptionConflict("Enable notifications again from this device's account settings.")
    device.auth_version = user.auth_version
    device.p256dh, device.auth_key, device.expires_at = public, auth, expires
    device.enabled, device.failures, device.retry_at = True, 0, None
    device.last_seen_at = now()
    if not active:
        device.last_notification_id = db.session.scalar(select(func.max(Notification.id)).where(
            Notification.recipient_id == user.id)) or 0
    device.version = (device.version or 0) + 1
    db.session.commit()
    return device


def revoke_current_device(user_id):
    if not current_app.config["DEVICE_PUSH_ENABLED"]:
        return
    try:
        device = current_device(user_id)
        if device:
            device.enabled = False
            device.version += 1
            db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.warning("Device notification revoke failed type=%s", type(error).__name__)


def _advance(identity, **values):
    device_id, version = identity
    db.session.execute(update(Device).where(Device.id == device_id, Device.version == version,
        Device.enabled.is_(True)).values(**values))
    db.session.commit()


def deliver_device(device_id):
    """Deliver the recipient's latest unread notification title and message."""
    from pywebpush import webpush, WebPushException
    import requests
    device = db.session.get(Device, device_id)
    if not device or not device.enabled:
        return
    identity = device.id, device.version
    user = db.session.get(User, device.user_id)
    if not user or not user.is_active_account() or user.auth_version != device.auth_version \
            or user.role not in ROLES or device.expires_at <= now():
        _advance(identity, enabled=False)
        return
    notices = db.session.execute(select(Notification.id, Notification.read_at,
        Notification.title, Notification.message).where(
        Notification.recipient_id == user.id, Notification.id > device.last_notification_id)
        .order_by(Notification.id).limit(50)).all()
    if not notices:
        db.session.rollback()
        return
    last_id = notices[-1].id
    unread = [row for row in notices if row.read_at is None]
    if not unread:
        _advance(identity, last_notification_id=last_id, failures=0, retry_at=None)
        return
    subscription = {"endpoint":device.endpoint, "keys":{"p256dh":device.p256dh,"auth":device.auth_key}}
    failures = device.failures
    latest = unread[-1]
    payload = json.dumps({"title":latest.title, "body":latest.message,
                         "url":"/notifications", "tag":"youth-centre-updates"}, ensure_ascii=False)
    db.session.rollback()  # No database or domain lock across network I/O.
    enabled, _, private, subject = settings()
    if not enabled:
        return
    class NoRedirectSession(requests.Session):
        def post(self, url, **kwargs):
            kwargs["allow_redirects"] = False
            return super().post(url, **kwargs)
    try:
        with NoRedirectSession() as http:
            http.trust_env = False
            webpush(subscription_info=subscription, data=payload, vapid_private_key=private,
                    vapid_claims={"sub":subject}, ttl=86400, timeout=5, requests_session=http)
        _advance(identity, last_notification_id=last_id, failures=0, retry_at=None)
    except WebPushException as error:
        status = error.response.status_code if error.response is not None else None
        if status in (404, 410):
            _advance(identity, enabled=False)
        else:
            # Keep the cursor for retry. Never log endpoints, keys or payloads.
            delay = min(3600, 15 * 2 ** min(failures, 8))
            retry = getattr(error, "retry_after", None)
            if retry is not None:
                from email.utils import parsedate_to_datetime
                try:
                    seconds = int(retry)
                except (TypeError, ValueError):
                    try:
                        seconds = int((parsedate_to_datetime(str(retry)).astimezone(timezone.utc).replace(tzinfo=None) - now()).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        seconds = 0
                delay = max(delay, min(86400, seconds))
            _advance(identity, failures=min(failures + 1, 20), retry_at=now() + timedelta(seconds=delay))
            current_app.logger.warning("Device notification delivery deferred status=%s", status)
    except Exception as error:
        db.session.rollback()
        _advance(identity, failures=min(failures + 1, 20), retry_at=now() + timedelta(minutes=5))
        current_app.logger.warning("Device notification delivery deferred type=%s", type(error).__name__)
