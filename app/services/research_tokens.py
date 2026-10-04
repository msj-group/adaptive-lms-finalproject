"""Signed, expiring stale-form tokens for the Researcher workspace
(Phase 6 replacement).

One purpose per form, each under its own salt. A token binds the acting
Researcher's public id, the configuration's public id and its optimistic
``version`` (and, for pause/resume, the intended state), so a form rendered
before *any* change to that configuration refuses to write. Every failure --
another salt or purpose, a wrong shape, an expired token -- is the same
``None``: a probe learns nothing from how it failed.

A token is authenticated, not encrypted; it carries public identifiers, a
version number and a purpose -- never an internal id, a name or an email.
"""

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

PURPOSE_EDIT = "research-configuration-edit"
PURPOSE_ACTIVATE = "research-configuration-activate"
PURPOSE_COLLECTING = "research-configuration-collecting"
PURPOSES = (PURPOSE_EDIT, PURPOSE_ACTIVATE, PURPOSE_COLLECTING)

_SALTS = {purpose: f"research.{purpose}.phase6-natural-use.v1" for purpose in PURPOSES}
_FIELDS = {
    PURPOSE_EDIT: ("purpose", "actor", "configuration", "version"),
    PURPOSE_ACTIVATE: ("purpose", "actor", "configuration", "version"),
    PURPOSE_COLLECTING: ("purpose", "actor", "configuration", "version", "collecting"),
}
MAX_AGE_SECONDS = 12 * 60 * 60


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALTS[purpose])


def make_token(purpose, actor_public_id, configuration_public_id, version, collecting=None):
    payload = {
        "purpose": purpose,
        "actor": actor_public_id,
        "configuration": configuration_public_id,
        "version": version,
    }
    if purpose == PURPOSE_COLLECTING:
        payload["collecting"] = bool(collecting)
    return _serializer(purpose).dumps(payload)


def load_token(purpose, token, actor_public_id, configuration_public_id):
    """The payload when the token is valid for exactly this actor and
    configuration, else ``None``."""
    if purpose not in PURPOSES or not isinstance(token, str) or not token:
        return None
    try:
        payload = _serializer(purpose).loads(token, max_age=MAX_AGE_SECONDS)
    except BadData:
        return None
    if not isinstance(payload, dict) or tuple(sorted(payload)) != tuple(sorted(_FIELDS[purpose])):
        return None
    version = payload.get("version")
    if payload.get("purpose") != purpose or payload.get("actor") != actor_public_id \
            or payload.get("configuration") != configuration_public_id:
        return None
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return None
    if purpose == PURPOSE_COLLECTING and not isinstance(payload.get("collecting"), bool):
        return None
    return payload
