from flask import render_template


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def request_entity_too_large(_error):
        # M12: raised by Flask/Werkzeug when a request body exceeds
        # MAX_CONTENT_LENGTH (the largest enabled Material category limit
        # plus a small multipart overhead) -- a friendly page, never a
        # stack trace or driver/framework text.
        return render_template("errors/413.html"), 413

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("errors/500.html"), 500
