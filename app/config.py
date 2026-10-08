import os
from sqlalchemy.engine import URL

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

    # Structured URL safely handles @, :, / and % inside deployment credentials.
    SQLALCHEMY_DATABASE_URI = URL.create(
        "mysql+pymysql", username=DATABASE_USER, password=DATABASE_PASSWORD,
        host=DATABASE_HOST, port=int(DATABASE_PORT), database=DATABASE_NAME,
        query={"charset": "utf8mb4"},
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "hide_parameters": True}

    WTF_CSRF_ENABLED = True

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    TRUSTED_HOSTS = [host.strip() for host in os.environ.get("TRUSTED_HOSTS", "").split(",") if host.strip()] or None
    PROXY_TRUSTED_HOPS = os.environ.get("PROXY_TRUSTED_HOPS", "0")
    DATABASE_SSL_CA = os.environ.get("DATABASE_SSL_CA", "")

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

    # Online payment remains disabled until a real provider is integrated.
    # Startup rejects other values; collection uses ordinary cash/bank forms.
    PAYMENT_PROVIDER_MODE = os.environ.get("PAYMENT_PROVIDER_MODE", "disabled")

    # ---- Phase 6: natural-use research collection ----
    # Resolved and validated once at start-up by
    # app.services.research_settings.resolve_research_settings (fail-closed).
    # RESEARCH_RETENTION_DAYS has no default on purpose: until the deployment
    # supplies one, no collection configuration can be activated.
    RESEARCH_DATA_PROVENANCE = os.environ.get("RESEARCH_DATA_PROVENANCE", "development")
    RESEARCH_RETENTION_DAYS = os.environ.get("RESEARCH_RETENTION_DAYS")
    RESEARCHER_EMAIL_ALLOWLIST = os.environ.get("RESEARCHER_EMAIL_ALLOWLIST", "")


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    WTF_CSRF_ENABLED = False
    # Factory-only tests can build an engine, but cannot connect without an
    # owned lease guard. DB fixtures override this sentinel with a harness-
    # issued loopback MySQL URL; never inherit development credentials.
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://unused:unused@127.0.0.1:9/aelms_test_unallocated"
    TEST_MYSQL_LEASE_GUARD = None
    RATELIMIT_ENABLED = False
    # Keep the factory-only environment outside online payment integrations.
    PAYMENT_PROVIDER_MODE = "disabled"
    # Pinned for the same reason: a test opts in to "study" data or a
    # retention value explicitly.
    RESEARCH_DATA_PROVENANCE = "development"
    RESEARCH_RETENTION_DAYS = None
    RESEARCHER_EMAIL_ALLOWLIST = ""


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    # Hosting evaluation must never become study data just because it uses
    # production HTTP settings. A genuine study supplies "study" explicitly.


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
