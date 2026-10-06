"""Fail-closed database boundary for the named testing environment.

Pure factory checks can construct an app without connecting. A DB-backed
test must supply a guard from its owned disposable MySQL lease. There is
no development URI or SQLite fallback and no connection during validation.
"""

import re
from collections.abc import Mapping

from sqlalchemy import event
from sqlalchemy.engine import make_url


class TestDatabaseConfigError(RuntimeError):
    __test__ = False


def validate_test_database(config):
    if config.get("SQLALCHEMY_BINDS"):
        raise TestDatabaseConfigError("Secondary test database binds are not permitted.")
    options = config.get("SQLALCHEMY_ENGINE_OPTIONS") or {}
    allowed_options = {
        "pool_pre_ping", "pool_size", "max_overflow", "pool_timeout",
        "pool_recycle", "pool_use_lifo", "isolation_level", "connect_args",
        "echo", "echo_pool", "hide_parameters", "future", "query_cache_size",
        "pool_reset_on_return",
    }
    if not isinstance(options, Mapping) or set(options) - allowed_options:
        raise TestDatabaseConfigError("Custom test database connection providers are not permitted.")
    connect_args = options.get("connect_args") or {}
    if not isinstance(connect_args, Mapping) or any(
        key not in {"connect_timeout", "read_timeout", "write_timeout"}
        or not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for key, value in connect_args.items()
    ):
        raise TestDatabaseConfigError("Test engine arguments cannot change the connection target.")
    try:
        url = make_url(config.get("SQLALCHEMY_DATABASE_URI"))
    except Exception:
        raise TestDatabaseConfigError("Testing requires a valid isolated MySQL target.") from None
    if (
        url.drivername != "mysql+pymysql"
        or url.host != "127.0.0.1"
        or not isinstance(url.port, int)
        or not (0 < url.port < 65536)
        or not re.fullmatch(r"aelms_test_[a-f0-9]{32}|aelms_test_unallocated", url.database or "")
        or dict(url.query) not in ({}, {"charset": "utf8mb4"})
    ):
        raise TestDatabaseConfigError("Testing requires an owned loopback MySQL schema.")
    guard = config.get("TEST_MYSQL_LEASE_GUARD")
    if guard is not None and not callable(guard):
        raise TestDatabaseConfigError("The MySQL test lease guard must be callable.")
    return guard


def protect_test_connections(engine, guard):
    def check_ownership(dialect, connection_record, parameters, options):
        if guard is None:
            raise TestDatabaseConfigError(
                "DB-backed tests require an owned MySQL lease; use the isolated test harness."
            )
        guard(engine.url)

    event.listen(engine, "do_connect", check_ownership)
