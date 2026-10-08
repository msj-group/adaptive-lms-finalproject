"""Railway private-volume initialization, then one production Gunicorn process.

No migrations, accounts, example data, collection activation or maintenance.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def prepare_runtime():
    if sys.platform != "linux":
        raise ValueError("Railway WSGI startup requires Linux")
    if sys.version_info[:3] != (3, 14, 6):
        raise ValueError("Python 3.14.6 is required")
    if os.environ.get("FLASK_ENV") != "production":
        raise ValueError("FLASK_ENV must be production")
    provenance = os.environ.get("RESEARCH_DATA_PROVENANCE", "development")
    if provenance != "development" and os.environ.get("RAILWAY_ALLOW_STUDY", "0") != "1":
        raise ValueError("Candidate startup requires development research provenance")
    try:
        port = int(os.environ["PORT"])
    except (KeyError, ValueError):
        raise ValueError("Railway PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("Railway PORT is out of range")
    mount = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ""))
    if str(mount) != "/data" or not mount.is_mount():
        raise ValueError("A persistent Railway volume must be mounted at /data")
    storage = Path(os.environ.get("MATERIAL_STORAGE_ROOT", ""))
    operations = Path(os.environ.get("RESEARCH_RETENTION_LOG_DIR", ""))
    if storage != Path("/data/private-files") or operations != Path("/data/operations"):
        raise ValueError("Railway private storage and operation paths must use the /data volume")
    for path in (mount, storage, operations):
        if path.is_symlink() or (path != mount and mount.resolve() not in path.resolve().parents):
            raise ValueError("Private persistent path containment failed")
        path.mkdir(exist_ok=True, mode=0o700)
        if os.geteuid() == 0:
            os.chown(path, 10001, 10001)
        path.chmod(0o700)
    # Railpack can initialize a root-owned new volume, but the WSGI process
    # runs unprivileged. Existing upload bytes are never recursively modified.
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    os.umask(0o077)
    for directory in (storage, operations):
        probe = directory / (".startup-" + os.urandom(8).hex())
        try:
            with probe.open("xb") as handle:
                handle.write(b"")
        finally:
            probe.unlink(missing_ok=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    # Gunicorn workers do not need the separate DDL credential.
    os.environ.pop("MIGRATION_DATABASE_USER", None)
    os.environ.pop("MIGRATION_DATABASE_PASSWORD", None)


def main():
    try:
        prepare_runtime()
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        from wsgi import application
        if application.debug or application.testing:
            raise ValueError("Production WSGI debug/testing must be off")
        print("Railway private storage ready; starting one production WSGI worker", flush=True)
    except Exception as error:
        # Configuration errors are controlled messages; all other errors use
        # only the type so driver/import internals cannot leak credentials.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("Railway startup refused: " + message, file=sys.stderr, flush=True)
        return 1
    os.execvp("gunicorn", ["gunicorn", "-c", "deploy/gunicorn.conf.py", "wsgi:application"])


if __name__ == "__main__":
    raise SystemExit(main())
