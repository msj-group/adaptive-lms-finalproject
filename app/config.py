import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "UTC")

    DATABASE_HOST = os.environ.get("DATABASE_HOST", "localhost")
    DATABASE_PORT = os.environ.get("DATABASE_PORT", "3306")
    DATABASE_NAME = os.environ.get("DATABASE_NAME", "adaptive_english_lms")
    DATABASE_USER = os.environ.get("DATABASE_USER", "")
    DATABASE_PASSWORD = os.environ.get("DATABASE_PASSWORD", "")

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DATABASE_USER}:{DATABASE_PASSWORD}"
        f"@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    WTF_CSRF_ENABLED = True

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    RATELIMIT_STORAGE_URI = "memory://"

    # ---- M12: Lesson Materials -- secure file storage ----
    # Resolved and validated once at start-up by
    # app.services.material_config.resolve_material_config (fail-closed:
    # an invalid value refuses to start the application rather than
    # silently narrowing/repairing it). See docs/DECISIONS.md, Part M12.
    MATERIAL_STORAGE_ROOT = os.environ.get("MATERIAL_STORAGE_ROOT", "storage/materials")
    MATERIAL_ALLOWED_EXTENSIONS = os.environ.get(
        "MATERIAL_ALLOWED_EXTENSIONS", "pdf,docx,png,jpg,jpeg,gif,webp,mp3,wav,mp4,webm"
    )
    MATERIAL_MAX_DOCUMENT_BYTES = os.environ.get("MATERIAL_MAX_DOCUMENT_BYTES", "26214400")
    MATERIAL_MAX_IMAGE_BYTES = os.environ.get("MATERIAL_MAX_IMAGE_BYTES", "10485760")
    MATERIAL_MAX_AUDIO_BYTES = os.environ.get("MATERIAL_MAX_AUDIO_BYTES", "52428800")
    MATERIAL_MAX_VIDEO_BYTES = os.environ.get("MATERIAL_MAX_VIDEO_BYTES", "104857600")

    # ---- Phase 5 / M06: online payment provider boundary ----
    # ``disabled`` (the default) or ``mock``. Resolved and validated once at
    # start-up by app.services.payment_providers.resolve_payment_provider
    # (fail-closed: ``mock`` outside development/testing, or any unknown
    # value, refuses to start the application). Not a secret; no provider
    # credential exists. See docs/DECISIONS.md, Part M06.
    PAYMENT_PROVIDER_MODE = os.environ.get("PAYMENT_PROVIDER_MODE", "disabled")


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    RATELIMIT_ENABLED = False
    # Pinned, like the database URI, so a developer's own environment can
    # never switch the suite into a provider mode; a test opts in explicitly.
    PAYMENT_PROVIDER_MODE = "disabled"


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
