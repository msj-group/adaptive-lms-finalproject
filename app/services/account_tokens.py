"""Signed account snapshots shared by edit, password and status forms."""
from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer
from app.services.account_operations import account_snapshot


def account_token(account):
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="admin.account.snapshot.v1").dumps(account_snapshot(account))


def read_account_token(token):
    try:
        value = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="admin.account.snapshot.v1").loads(token or "")
        return value if isinstance(value, dict) else None
    except BadSignature:
        return None
