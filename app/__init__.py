import os

from flask import Flask

from app.config import config_by_name
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.errors import register_error_handlers
from app.services.material_config import resolve_material_config
from app.services.payment_providers import resolve_payment_provider


def create_app(config_name=None, **config_overrides):
    """Build the Flask application.

    ``config_overrides`` lets a caller (in practice, only the test suite)
    override individual config keys after the named config class is
    loaded -- e.g. pointing ``MATERIAL_STORAGE_ROOT`` at an isolated
    ``tmp_path`` per test, per Part M12 section 12 ("Tests must use
    isolated temporary storage, not the real development material
    directory").
    """
    app = Flask(__name__)
    config_name = config_name or os.environ.get("FLASK_ENV", "development")
    app.config.from_object(config_by_name.get(config_name, config_by_name["development"]))
    if config_overrides:
        app.config.update(config_overrides)

    # M12: resolve + validate the Material storage configuration once at
    # start-up. Fail closed -- MaterialConfigError propagates and the
    # application refuses to start rather than run with a guessed or
    # partially-valid storage configuration. Never touches the
    # filesystem beyond a read-only existence check (see
    # resolve_material_config's docstring).
    project_root = os.path.dirname(app.root_path)
    material_config = resolve_material_config(app.config, project_root)
    app.extensions["material_config"] = material_config
    app.config["MAX_CONTENT_LENGTH"] = material_config.max_content_length

    # Phase 5 / M06: resolve the online-payment provider once, for the
    # environment this application was created for. Fail closed --
    # PaymentProviderConfigError propagates, so production (or any unknown
    # environment) configured for the mock provider refuses to start.
    app.extensions["payment_provider"] = resolve_payment_provider(app.config, config_name)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = "auth.login"

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            raw_pk, raw_version = user_id.split(".", 1)
            primary_key, auth_version = int(raw_pk), int(raw_version)
        except (AttributeError, ValueError):
            return None
        user = db.session.get(User, primary_key)
        if user is None or not user.is_active_account() or user.auth_version != auth_version:
            return None
        return user

    from app.blueprints.design_system.routes import design_system_bp
    from app.blueprints.auth.routes import auth_bp
    from app.blueprints.admin.routes import admin_bp
    from app.blueprints.teacher import teacher_bp
    from app.blueprints.student import student_bp
    from app.blueprints.research import research_bp
    from app.blueprints.notifications import notifications_bp
    from app.blueprints.messages import messages_bp
    from app.blueprints.webhooks import webhooks_bp

    app.register_blueprint(design_system_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)
    # Phase 6 / M01: the Researcher portal. Read-only, and gated to an
    # active Researcher account.
    app.register_blueprint(research_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(messages_bp)
    # Phase 5 / M07: the public, CSRF-exempt, signature-verified provider
    # webhook endpoint (404 unless the Mock/Sandbox provider is enabled).
    app.register_blueprint(webhooks_bp)

    # M14: the shared Student/Teacher portal header renders a
    # Notifications link and unread badge. This injects a *callable*, not
    # a value, so a template that never calls it (every Administrator
    # page, the login page, the error pages) costs no query at all; the
    # helper itself is role-gated and memoised on the request object, so
    # a Student or Teacher request pays for at most one bounded count no
    # matter how many templates it renders. It fails open to zero -- see
    # app/services/notification_queries.py.
    @app.context_processor
    def inject_notification_header():
        from flask_login import current_user

        from app.services.notification_queries import header_badge

        return {"notification_header": lambda: header_badge(current_user)}

    # Phase 6 / M01: the shared Student/Teacher portal header renders a
    # Research consent link for a Student who has an invitation to answer or
    # an acceptance they may withdraw. Injected as a *callable*, exactly like
    # the notification badge above, so a template that never calls it (every
    # Administrator page, the Researcher portal, the login page, the error
    # pages) costs no query at all; the helper itself is role-gated and
    # returns None for every other case -- so no Student is ever shown a
    # consent prompt that is not theirs.
    @app.context_processor
    def inject_research_consent_link():
        from flask_login import current_user

        from app.services.research_queries import portal_consent_status

        return {"research_consent_status": lambda: portal_consent_status(current_user)}

    register_error_handlers(app)

    @app.get("/health")
    def health():
        return {"status": "ok"}, 200

    return app
