"""Redact production application diagnostics without changing error handling."""
import logging


class PrivateDiagnosticFilter(logging.Filter):
    def filter(self, record):
        # Caught driver/OS failures can embed SQL, values and private paths.
        # Keep an actionable module/function/type, never a formatted traceback.
        if record.exc_info:
            record.msg = "Operation failed at %s.%s type=%s"
            record.args = (record.module, record.funcName, record.exc_info[0].__name__)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        elif record.args and record.funcName not in {"log_safe_exception", "health_ready", "error_page"}:
            # Existing diagnostics interpolate recipient IDs and storage keys.
            # Omit these values while retaining the emitting operation/level.
            record.msg = "Diagnostic event at %s.%s"
            record.args = (record.module, record.funcName)
        return True


def install_private_diagnostics(app):
    diagnostic_filter = PrivateDiagnosticFilter()
    if not app.logger.handlers:
        from flask.logging import default_handler
        app.logger.addHandler(default_handler)
    # App and descendant module loggers share these handlers. Handler filters
    # also apply to propagated app.services.file_storage exceptions.
    for handler in app.logger.handlers:
        if not any(isinstance(item, PrivateDiagnosticFilter) for item in handler.filters):
            handler.addFilter(diagnostic_filter)
    # Avoid a second unfiltered copy through an externally configured root.
    app.logger.propagate = False
