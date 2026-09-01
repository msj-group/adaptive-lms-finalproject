import os

from flask import Flask

from app.config import config_by_name
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.errors import register_error_handlers


def create_app(config_name=None):
    app = Flask(__name__)
    config_name = config_name or os.environ.get("FLASK_ENV", "development")
    app.config.from_object(config_by_name.get(config_name, config_by_name["development"]))

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

    app.register_blueprint(design_system_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)

    register_error_handlers(app)

    @app.get("/health")
    def health():
        return {"status": "ok"}, 200

    return app
