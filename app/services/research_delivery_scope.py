"""Opaque server-verified browser delivery binding.

The proof carries no identity and adds no expiry or session semantics.
Verification belongs after the existing account/configuration locks and
before subject lookup or provisioning. Nothing is persisted or exported.
"""

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import uuid

_PURPOSE = b"research.browser.delivery.v1\x00"
_TOKEN = re.compile(r"[0-9a-f]{64}\Z", flags=re.ASCII)


def is_delivery_scope(value):
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _secret_bytes(secret):
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not isinstance(secret, bytes) or not secret:
        raise ValueError("Delivery signing key is unavailable")
    return secret


def _identity(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("Delivery identity is invalid")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("Delivery identity is invalid")
    except ValueError:
        raise ValueError("Delivery identity is invalid") from None
    return value


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Delivery version is invalid")
    return value


def issue_delivery_scope(secret, user_public_id, auth_version,
                         configuration_public_id, configuration_version):
    """Bind to stable identities even if numeric database IDs are reused."""
    payload = json.dumps([
        _identity(user_public_id), _version(auth_version),
        _identity(configuration_public_id), _version(configuration_version),
    ], separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hmac.new(_secret_bytes(secret), _PURPOSE + payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class DeliveryProof:
    """The route supplies the key; a request body can supply only the token."""

    token: str = field(repr=False)
    secret: object = field(repr=False)

    def matches(self, token, user_public_id, auth_version,
                configuration_public_id, configuration_version):
        if not is_delivery_scope(token) or not is_delivery_scope(self.token):
            return False
        if not hmac.compare_digest(token, self.token):
            return False
        try:
            expected = issue_delivery_scope(
                self.secret, user_public_id, auth_version,
                configuration_public_id, configuration_version,
            )
        except (ValueError, TypeError, UnicodeError):
            return False
        return hmac.compare_digest(token, expected)
