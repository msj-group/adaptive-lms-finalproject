import uuid
from flask import current_app, jsonify, render_template
from flask_login import current_user
from werkzeug.exceptions import SecurityError


def register_error_handlers(app):
    @app.errorhandler(SecurityError)
    def invalid_host(_error):
        # Trusted-host rejection occurs before a URL adapter exists. The
        # normal workspace page uses url_for and cannot render at this stage.
        response = jsonify(error="Invalid request host")
        response.status_code = 400
        response.headers["Cache-Control"] = "no-store"
        return response

    def error_page(code):
        reference = uuid.uuid4().hex[:12] if code == 500 else None
        if reference:
            current_app.logger.error("Error page reference: %s", reference)
        return render_template("errors/workspace.html", code=code, reference=reference), code

    @app.errorhandler(404)
    def not_found(_error):
        return error_page(404)

    @app.errorhandler(413)
    def request_entity_too_large(_error):
        # M12: raised by Flask/Werkzeug when a request body exceeds
        # MAX_CONTENT_LENGTH (the largest enabled Material category limit
        # plus a small multipart overhead) -- a friendly page, never a
        # stack trace or driver/framework text.
        return error_page(413)

    @app.errorhandler(500)
    def server_error(_error):
        return error_page(500)

    for code in (400, 403, 410, 429):
        app.register_error_handler(code, lambda _error, status=code: error_page(status))
