import os

from flask import Flask, redirect, url_for
from flask_login import current_user

from app.config import config_by_name
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.errors import register_error_handlers
from app.services.material_config import resolve_material_config
from app.services.payment_providers import resolve_payment_provider
from app.services.research_settings import resolve_research_settings


def create_app(config_name=None, **config_overrides):
    """Build the Flask application.

    ``config_overrides`` lets a caller (in practice, only the test suite)
    override individual config keys after the named config class is
    loaded -- e.g. pointing ``MATERIAL_STORAGE_ROOT`` at an isolated
    ``tmp_path`` per test, per Part M12 section 12 ("Tests must use
    isolated temporary storage, not the real development material
    directory").
    """
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")
    if not isinstance(config_name, str) or config_name not in config_by_name:
        raise ValueError(
            "Unknown application environment. Use development, testing, or production."
        )

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])
    if config_overrides:
        app.config.update(config_overrides)

    # The dedicated test runner supplies no deployment dotenv/credentials.
    # Even a pure factory check selecting production/development must not
    # accidentally connect there: without an owned explicit target it gets
    # the same unallocated, non-connectable testing sentinel.
    isolated_test_run = os.environ.get("AELMS_ISOLATED_TEST_RUN") == "1"
    if isolated_test_run and "SQLALCHEMY_DATABASE_URI" not in config_overrides:
        app.config["SQLALCHEMY_DATABASE_URI"] = config_by_name["testing"].SQLALCHEMY_DATABASE_URI

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

    # Phase 6: research deployment settings (provenance, retention, the
    # Researcher provisioning allowlist). Fail closed on an invalid value.
    resolve_research_settings(app.config)

    is_owned_test_app = (
        config_name == "testing" or isolated_test_run
        or app.config.get("TEST_MYSQL_LEASE_GUARD") is not None
    )
    if is_owned_test_app:
        from app.testing import validate_test_database

        test_database_guard = validate_test_database(app.config)
    else:
        from app.database import require_mysql_databases

        require_mysql_databases(app.config)

    db.init_app(app)
    from app.database import install_mysql_integrity_errors

    with app.app_context():
        for engine in db.engines.values():
            install_mysql_integrity_errors(engine)
    if is_owned_test_app:
        from app.testing import protect_test_connections

        with app.app_context():
            protect_test_connections(db.engine, test_database_guard)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = "auth.login"
    # All roles use the shared login; workspace authorization stays separate.
    login_manager.blueprint_login_views = {}

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
    from app.blueprints.auth.routes import auth_bp, home_endpoint_for
    from app.blueprints.admin.routes import admin_bp
    from app.blueprints.teacher import teacher_bp
    from app.blueprints.student import student_bp
    from app.blueprints.research import research_bp
    from app.blueprints.notifications import notifications_bp
    from app.blueprints.messages import messages_bp
    from app.blueprints.webhooks import webhooks_bp
    from app.blueprints.collector import collector_bp

    app.register_blueprint(design_system_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)
    # Phase 6: the separate Researcher workspace, gated to an active
    # Researcher account on every workspace rule.
    app.register_blueprint(research_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(messages_bp)
    # Phase 5 / M07: the public, CSRF-exempt, signature-verified provider
    # webhook endpoint (404 unless the Mock/Sandbox provider is enabled).
    app.register_blueprint(webhooks_bp)
    # Phase 6: the Student-side research collector (authenticated, CSRF-
    # protected JSON endpoints; inert for anyone outside collection scope).
    app.register_blueprint(collector_bp)

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

    # Phase 6: the shared portal layout renders the research collector and
    # the optional feedback dialog only for a Student inside the collection
    # scope. Injected as a *callable*, like the notification badge: one
    # memoised, bounded query per request, failing closed to "render
    # nothing" -- the same query budget the removed consent link used.
    @app.context_processor
    def inject_research_collector():
        from app.blueprints.collector.hooks import collector_view

        return {"research_collector": collector_view}

    # Phase 6: server-confirmed outcomes that Student routes noted are
    # recorded after the route committed or rolled back. Best-effort and
    # isolated: it never raises and never changes the response.
    from app.blueprints.collector.hooks import flush_outcomes

    app.after_request(flush_outcomes)

    register_error_handlers(app)

    @app.get("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        return redirect(url_for(home_endpoint_for(current_user)))

    @app.get("/health")
    def health():
        return {"status": "ok"}, 200

    from app.services.request_arrival import RequestArrivalMiddleware

    app.wsgi_app = RequestArrivalMiddleware(app.wsgi_app, app.config["MAX_CONTENT_LENGTH"])
    return app
