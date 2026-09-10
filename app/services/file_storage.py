"""Private, non-executable filesystem storage for uploaded Materials
(M12, section 12).

Flask-independent except for accepting a resolved
``app.services.material_config.MaterialConfig``. Every file operation
resolves its path with :func:`resolve_within_root` first and refuses to
proceed if the result would escape the configured storage root.

**Locking discipline (Part M12 section 7).** Nothing in this module
touches the database, and callers must not hold any ``SELECT ... FOR
UPDATE`` lock while calling :func:`store_validated_upload` -- streaming
and validating a large upload must never happen inside an open write
transaction. The Teacher file-Material create route stores the file
*before* acquiring the canonical lock chain, then re-validates everything
against the locked rows and deletes the just-stored file if the locked
re-check rejects the request (see ``app/blueprints/teacher/materials.py``).

One honest limitation this module does not hide: there is a small,
unavoidable crash window between the atomic ``os.replace`` that
publishes the final file and the caller's database commit. If the
process crashes in exactly that window, the physical file is left on
disk with no ``UploadedFile`` row referencing it. M12 deliberately does
not implement reconciliation/retention tooling for that; it is recorded
here and in ``docs/DECISIONS.md`` as deferred, not silently ignored.
"""

import hashlib
import logging
import os
import secrets
import stat
from pathlib import Path

from app.services.file_validation import (
    CANONICAL_CONTENT_TYPE,
    MIN_HEADER_BYTES,
    FileValidationError,
    category_for_extension,
    inspect_docx_zip,
    normalize_original_filename,
    validate_declared_mime,
    validate_extension,
    validate_signature,
)

_logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024  # 1 MiB -- an upload is never read wholly into memory
_STORED_KEY_RANDOM_BYTES = 24  # 192 bits: unguessable, collision-safe stored names


class StorageContainmentError(RuntimeError):
    """A resolved path would fall outside the configured storage root.
    Refuses to touch the filesystem rather than risk operating outside
    the sandbox -- this should be unreachable in normal operation and
    signals a configuration or programming error, not user input."""


class StoredUpload:
    """Plain result of a successful :func:`store_validated_upload` call."""

    __slots__ = (
        "storage_key",
        "original_filename",
        "extension",
        "category",
        "content_type",
        "byte_size",
        "sha256",
    )

    def __init__(self, storage_key, original_filename, extension, category, content_type,
                 byte_size, sha256):
        self.storage_key = storage_key
        self.original_filename = original_filename
        self.extension = extension
        self.category = category
        self.content_type = content_type
        self.byte_size = byte_size
        self.sha256 = sha256


def _random_key(suffix=""):
    return f"{secrets.token_hex(_STORED_KEY_RANDOM_BYTES)}{suffix}"


def resolve_within_root(material_config, name):
    """Join `name` (a single path component -- never attacker-controlled
    request data) under the storage root and verify containment. Raises
    :class:`StorageContainmentError` if the resolved path is not the
    root itself or a descendant of it."""
    root = material_config.storage_root
    candidate = (root / name).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise StorageContainmentError("Resolved path escaped the configured storage root.")
    return candidate


def _ensure_root(material_config):
    root = material_config.storage_root
    root.mkdir(parents=True, exist_ok=True)
    _apply_private_permissions(root, directory=True)
    return root


def _apply_private_permissions(path, directory=False):
    """Best-effort restrictive permissions. A no-op failure is swallowed
    -- Windows development environments largely ignore POSIX bits, and
    permission enforcement there is not the security boundary; path
    containment and application-level authorization are."""
    try:
        mode = stat.S_IRWXU if directory else (stat.S_IRUSR | stat.S_IWUSR)
        os.chmod(path, mode)
    except OSError:
        pass


def _safe_unlink(path):
    """Attempt to delete `path`, returning ``True`` on success (a
    missing file counts as success -- the desired end state) and
    ``False`` if the operating system refused the deletion.

    This function **never raises** -- an ``OSError`` (or any unexpected
    error) is logged through this module's logger with a traceback and
    swallowed, so a cleanup failure is always observable in server logs
    but can never mask the original validation/database exception or
    reach an HTTP response. Callers propagate the boolean so a
    higher-level layer can add request context if useful.
    """
    if path is None:
        return True
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError:
        _logger.exception("Could not delete stored file %s", path)
        return False
    except Exception:  # pragma: no cover -- defensive: keep the no-raise contract
        _logger.exception("Unexpected error deleting stored file %s", path)
        return False


def stream_upload_to_storage(
    material_config,
    file_storage,
    original_filename,
    extension,
    category,
    content_type,
    max_bytes,
    validate_header,
    oversize_message="This file is larger than allowed for its type.",
    empty_message="The uploaded file is empty.",
    signature_message=None,
    inspect_stored_file=None,
):
    """The shared streaming/hashing/publishing core behind **every**
    validated upload in this project.

    Extracted (Phase 4 / M06) so the Speaking recording path can reuse
    exactly this behaviour -- bounded chunked reads, a size limit enforced
    *while* streaming, SHA-256 computed in one pass, a header captured for
    the signature check, an atomic publish under a fresh random key, and
    deletion attempted on every failure -- **without** re-implementing it
    and without changing what a Material upload does. Every decision this
    function does not make (which extensions are allowed, which category
    and content type are stored, which byte limit applies, what the
    signature check is) is the caller's, and each caller states its own.

    `validate_header` is called with the first :data:`MIN_HEADER_BYTES`
    actually read once the whole stream has landed, and must raise
    :class:`~app.services.file_validation.FileValidationError` on a
    mismatch. `inspect_stored_file`, when given, is called with the
    complete temporary file's path for formats whose validation needs it
    (DOCX). `signature_message`, when given, replaces a
    `validate_header` failure's message with one safe generic sentence --
    so a Student is never shown format-internals wording.

    Returns a :class:`StoredUpload`. Raises
    :class:`FileValidationError` (bad content) or
    :class:`StorageContainmentError` (should be unreachable). **On any
    failure the temporary file, and the final file if it was already
    created, have deletion *attempted* before the exception propagates.**
    ``_safe_unlink`` never raises and logs any OS-level deletion failure
    through this module's logger, so the original exception is always the
    one that propagates and a rare undeletable-file case is visible in
    the server log rather than hidden -- the atomicity of the OS
    ``unlink`` itself is not something application code can absolutely
    guarantee.

    **No database lock may be held while this runs** -- see the module
    docstring.
    """
    _ensure_root(material_config)
    temp_path = resolve_within_root(material_config, _random_key(suffix=".part"))
    final_path = None

    hasher = hashlib.sha256()
    total = 0
    header = b""
    try:
        source = getattr(file_storage, "stream", file_storage)
        with open(temp_path, "wb") as dest:
            while True:
                chunk = source.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FileValidationError(oversize_message)
                if len(header) < MIN_HEADER_BYTES:
                    header += chunk[: MIN_HEADER_BYTES - len(header)]
                hasher.update(chunk)
                dest.write(chunk)
        _apply_private_permissions(temp_path)

        if total == 0:
            raise FileValidationError(empty_message)

        try:
            validate_header(header)
        except FileValidationError:
            if signature_message is None:
                raise
            raise FileValidationError(signature_message) from None
        if inspect_stored_file is not None:
            inspect_stored_file(temp_path)

        final_path = resolve_within_root(material_config, _random_key(suffix=f".{extension}"))
        os.replace(temp_path, final_path)  # atomic within the same directory/filesystem
        _apply_private_permissions(final_path)
    except BaseException:
        # Attempt cleanup of whatever exists, then re-raise the ORIGINAL
        # exception. _safe_unlink never raises and logs an OS-level
        # failure itself, so this can neither mask nor replace it.
        _safe_unlink(temp_path)
        _safe_unlink(final_path)
        raise

    return StoredUpload(
        storage_key=final_path.name,
        original_filename=original_filename,
        extension=extension,
        category=category,
        content_type=content_type,
        byte_size=total,
        sha256=hasher.hexdigest(),
    )


def store_validated_upload(material_config, file_storage, original_filename_raw):
    """Stream `file_storage` (a Werkzeug ``FileStorage``-like object
    exposing ``.stream`` and optionally ``.mimetype``) to a random
    temporary ``.part`` file under the configured storage root, validate
    it in full while streaming, and atomically move it to its final
    random path.

    This is the **Material** upload path (M12) and its behaviour is
    unchanged: the configured extension allowlist, the configured
    extension -> category map, the canonical Material content type and
    the per-category byte limit all still decide what is stored. Phase 4
    / M06 only moved the streaming/publishing mechanics into
    :func:`stream_upload_to_storage` so a second, explicitly different
    policy could reuse them.

    Returns a :class:`StoredUpload` on success. Raises
    :class:`FileValidationError` (bad content) or
    :class:`StorageContainmentError` (should be unreachable) on failure,
    with the same cleanup-on-failure contract described there.
    """
    original_filename = normalize_original_filename(original_filename_raw)
    extension = validate_extension(original_filename, material_config)
    category = category_for_extension(extension, material_config)
    validate_declared_mime(extension, getattr(file_storage, "mimetype", None))
    max_bytes = material_config.max_bytes_for_category(category)

    return stream_upload_to_storage(
        material_config,
        file_storage,
        original_filename=original_filename,
        extension=extension,
        category=category,
        content_type=CANONICAL_CONTENT_TYPE[extension],
        max_bytes=max_bytes,
        validate_header=lambda header: validate_signature(extension, header),
        inspect_stored_file=inspect_docx_zip if extension == "docx" else None,
    )


def delete_stored_file(material_config, storage_key):
    """Attempt permanent removal of a stored file by its key, returning
    ``True`` on success (missing file included) and ``False`` if the OS
    refused the deletion (already logged with a traceback by
    ``_safe_unlink``).

    Not called by any successful M12 route (Materials/files are never
    hard-deleted); this exists solely so a cleanup-on-failure path has a
    single, containment-checked place to remove a file it just created.
    ``resolve_within_root`` still enforces path containment -- a key that
    would escape the configured root raises
    :class:`StorageContainmentError` and nothing is deleted.
    """
    path = resolve_within_root(material_config, storage_key)
    return _safe_unlink(path)


def open_stored_file(material_config, storage_key):
    """Resolve `storage_key` to a containment-checked path.

    Raises :class:`FileNotFoundError` if the physical file is missing so
    callers can fail safely (a generic message, never the resolved path)
    instead of leaking filesystem details.
    """
    path = resolve_within_root(material_config, storage_key)
    if not path.is_file():
        raise FileNotFoundError(storage_key)
    return path
