"""Expiring own-Unit completion snapshots; no titles, files or internal ids."""
from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from app.services.lesson_progress_tokens import TOKEN_MAX_AGE_SECONDS

_SALT = 'unit-progress.completion.v1'
_FIELDS = {'purpose', 'actor', 'group', 'unit', 'book', 'versions'}


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=_SALT)


def make_unit_progress_token(actor, group, unit, book, versions):
    return _serializer().dumps(dict(purpose='unit-complete', actor=actor,
        group=group, unit=unit, book=book, versions=versions))


def load_unit_progress_token(token, actor, group, unit, book):
    if not isinstance(token, str) or not token or len(token) > 131072:
        return None
    try:
        value = _serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except BadData:
        return None
    if not isinstance(value, dict) or set(value) != _FIELDS:
        return None
    if (value['purpose'], value['actor'], value['group'], value['unit'], value['book']) != (
            'unit-complete', actor, group, unit, book):
        return None
    versions = value['versions']
    if not isinstance(versions, dict) or not versions:
        return None
    if any(not isinstance(key, str) or not key or len(key) > 64 or
           isinstance(version, bool) or not isinstance(version, int) or
           not 1 <= version <= 2**31 - 1 for key, version in versions.items()):
        return None
    return versions
