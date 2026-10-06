"""The runtime database policy; validation never creates an engine or connects."""

from collections.abc import Mapping

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError, OperationalError


def require_mysql_databases(configuration):
    targets = [configuration.get("SQLALCHEMY_DATABASE_URI")]
    binds = configuration.get("SQLALCHEMY_BINDS")
    if binds is None:
        binds = {}
    if not isinstance(binds, Mapping):
        raise ValueError("Database targets must use MySQL with PyMySQL.")
    targets.extend(value.get("url") if isinstance(value, Mapping) else value
                   for value in binds.values())
    for target in targets:
        try:
            valid = make_url(target).drivername == "mysql+pymysql"
        except (ArgumentError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("Database targets must use MySQL with PyMySQL.")


def _mysql_check_integrity_error(context):
    """PyMySQL classifies MySQL CHECK rejection (3819) as OperationalError.

    The application treats a violated CHECK like other enforced integrity
    constraints. Preserve the actual driver exception, statement/parameters,
    disconnect flag and redaction policy. Do not reinterpret syntax errors,
    deadlocks, timeouts, connection failures or another driver's exceptions.
    """
    dialect = context.dialect
    original = context.original_exception
    if (dialect.name != "mysql" or dialect.driver != "pymysql"
            or not isinstance(context.sqlalchemy_exception, OperationalError)
            or not original.args or original.args[0] != 3819):
        return None
    return IntegrityError(
        context.statement, context.parameters, original,
        connection_invalidated=context.is_disconnect,
        hide_parameters=context.sqlalchemy_exception.hide_parameters,
    )


def install_mysql_integrity_errors(engine):
    """Install on one application engine without opening a connection."""
    event.listen(engine, "handle_error", _mysql_check_integrity_error, retval=True)
