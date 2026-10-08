"""Hosting configuration boundaries; no business or authentication decisions."""
from pathlib import Path


def resolve_deployment_settings(config, environment):
    try:
        hops = int(config.get("PROXY_TRUSTED_HOPS", 0))
    except (TypeError, ValueError):
        raise ValueError("PROXY_TRUSTED_HOPS must be an integer from 0 to 2.") from None
    if not 0 <= hops <= 2:
        raise ValueError("PROXY_TRUSTED_HOPS must be an integer from 0 to 2.")
    config["PROXY_TRUSTED_HOPS"] = hops
    certificate = config.get("DATABASE_SSL_CA")
    if certificate:
        certificate = Path(certificate).resolve()
        if not certificate.is_file():
            raise ValueError("DATABASE_SSL_CA must identify an existing CA certificate.")
        config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            **config["SQLALCHEMY_ENGINE_OPTIONS"],
            "connect_args": {"ssl": {"ca": str(certificate), "check_hostname": True}},
        }
    if environment != "production":
        return
    if not config.get("DATABASE_USER") or not config.get("DATABASE_PASSWORD"):
        raise ValueError("Production requires explicit MySQL application credentials.")
    if str(config["DATABASE_USER"]).lower() == "root":
        raise ValueError("Production requires a schema-scoped MySQL account, not root.")
    options = config["SQLALCHEMY_ENGINE_OPTIONS"]
    config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        **options, "pool_size": 4, "max_overflow": 2,
        "pool_timeout": 10, "pool_recycle": 300,
        "connect_args": {**options.get("connect_args", {}),
                         "connect_timeout": 5, "read_timeout": 30, "write_timeout": 30},
    }
    key = config.get("SECRET_KEY")
    if not isinstance(key, str) or len(key) < 32 or key.startswith("replace-"):
        raise ValueError("Production requires a generated SECRET_KEY of at least 32 characters.")
    hosts = config.get("TRUSTED_HOSTS")
    if not hosts or any(host == "*" or "/" in host or "://" in host for host in hosts):
        raise ValueError("Production requires explicit TRUSTED_HOSTS hostnames.")
    if config.get("DEBUG") or config.get("TESTING") or not config.get("WTF_CSRF_ENABLED"):
        raise ValueError("Production requires debug/testing off and CSRF enabled.")
    if not config.get("SESSION_COOKIE_SECURE"):
        raise ValueError("Production requires secure session cookies and HTTPS.")
    if not Path(str(config.get("MATERIAL_STORAGE_ROOT", ""))).is_absolute():
        raise ValueError("Production MATERIAL_STORAGE_ROOT must be an absolute private persistent path.")
    if config.get("RESEARCH_RETENTION_DAYS") != 15:
        raise ValueError("This approved Version A production baseline requires 15-day research retention.")
