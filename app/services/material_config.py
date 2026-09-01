"""Fail-closed resolution and validation of the M12 Material storage /
upload configuration.

Flask-independent except for the tiny ``current_material_config()``
accessor at the bottom. ``resolve_material_config`` is called exactly
once, at application start-up (``create_app``), and its result is stashed
on ``app.extensions["material_config"]`` -- an invalid configuration
raises :class:`MaterialConfigError` and the application refuses to start
rather than silently narrowing, repairing, or guessing a value.
"""

from dataclasses import dataclass
from pathlib import Path

from flask import current_app

#: The hard maximum supported extension set (Part M12, section 10).
#: Configuration may narrow this set; it must never expand it.
HARD_ALLOWED_EXTENSIONS = frozenset(
    {"pdf", "docx", "png", "jpg", "jpeg", "gif", "webp", "mp3", "wav", "mp4", "webm"}
)

#: Server-determined extension -> category map. Never trust a
#: client-declared category.
EXTENSION_CATEGORY = {
    "pdf": "document",
    "docx": "document",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "mp3": "audio",
    "wav": "audio",
    "mp4": "video",
    "webm": "video",
}

_CATEGORY_SIZE_CONFIG_KEY = {
    "document": "MATERIAL_MAX_DOCUMENT_BYTES",
    "image": "MATERIAL_MAX_IMAGE_BYTES",
    "audio": "MATERIAL_MAX_AUDIO_BYTES",
    "video": "MATERIAL_MAX_VIDEO_BYTES",
}

#: Small multipart/form overhead added on top of the largest enabled
#: category limit when computing Flask's MAX_CONTENT_LENGTH (section 11).
#: Covers the multipart boundary framing plus the handful of small text
#: fields on a Material create form (title, CSRF token, signed create
#: token) with generous margin -- those total ~1 KiB in practice.
MULTIPART_OVERHEAD_BYTES = 65_536  # 64 KiB


class MaterialConfigError(RuntimeError):
    """The M12 material configuration is invalid. The application must
    fail closed -- refuse to start -- rather than run with a guessed or
    partially-valid storage configuration."""


@dataclass(frozen=True)
class MaterialConfig:
    storage_root: Path
    allowed_extensions: frozenset
    #: Validated positive byte limit for every category (all four keys are
    #: always present so a lookup never fails), regardless of which are
    #: actually enabled.
    max_bytes_by_category: dict
    #: The categories that have at least one enabled extension. Only these
    #: drive the global request-size limit -- a configured-but-disabled
    #: category with a larger limit must never inflate it.
    enabled_categories: frozenset

    def category_for_extension(self, extension):
        """The server-determined category for a lowercase extension
        (without a leading dot), or ``None`` if it is not in
        ``allowed_extensions``."""
        ext = extension.lower().lstrip(".")
        if ext not in self.allowed_extensions:
            return None
        return EXTENSION_CATEGORY.get(ext)

    def max_bytes_for_category(self, category):
        return self.max_bytes_by_category[category]

    @property
    def max_upload_bytes(self):
        """The largest per-file limit among the **enabled** categories --
        e.g. with only ``pdf`` enabled this is the document limit, never
        the (disabled) video limit."""
        return max(self.max_bytes_by_category[c] for c in self.enabled_categories)

    @property
    def max_content_length(self):
        """Flask ``MAX_CONTENT_LENGTH``: the largest enabled-category
        limit plus a small fixed multipart/form overhead."""
        return self.max_upload_bytes + MULTIPART_OVERHEAD_BYTES


def resolve_material_config(config, project_root):
    """Build and validate a :class:`MaterialConfig` from a Flask
    ``app.config``-like mapping and the project's root directory.

    Raises :class:`MaterialConfigError` on the first invalid setting.
    Never creates the storage directory -- that happens lazily, on first
    write, in ``app/services/file_storage.py`` -- so importing/starting
    the application never mutates a fresh checkout's filesystem.
    """
    project_root = Path(project_root).resolve()

    raw_root = config.get("MATERIAL_STORAGE_ROOT")
    if not raw_root or not str(raw_root).strip():
        raise MaterialConfigError("MATERIAL_STORAGE_ROOT must be set to a non-empty path.")
    root_path = Path(str(raw_root).strip())
    storage_root = root_path if root_path.is_absolute() else (project_root / root_path)
    storage_root = storage_root.resolve()

    static_root = (project_root / "app" / "static").resolve()
    if storage_root == static_root or static_root in storage_root.parents:
        raise MaterialConfigError(
            "MATERIAL_STORAGE_ROOT must not be app/static or a path inside it -- that "
            "directory is served publicly."
        )

    raw_extensions = config.get("MATERIAL_ALLOWED_EXTENSIONS")
    if not raw_extensions or not str(raw_extensions).strip():
        raise MaterialConfigError("MATERIAL_ALLOWED_EXTENSIONS must be set to a non-empty list.")
    extensions = frozenset(
        ext.strip().lower().lstrip(".")
        for ext in str(raw_extensions).split(",")
        if ext.strip()
    )
    if not extensions:
        raise MaterialConfigError("MATERIAL_ALLOWED_EXTENSIONS must not be empty.")
    unknown = extensions - HARD_ALLOWED_EXTENSIONS
    if unknown:
        raise MaterialConfigError(
            "MATERIAL_ALLOWED_EXTENSIONS contains extension(s) outside the vetted set "
            f"{sorted(HARD_ALLOWED_EXTENSIONS)}: {sorted(unknown)}."
        )

    # Validate every size key as a positive integer (fail closed on a
    # bad value even for a currently-disabled category), but only the
    # categories with an enabled extension drive max_upload_bytes.
    max_bytes_by_category = {}
    for category, key in _CATEGORY_SIZE_CONFIG_KEY.items():
        raw_value = config.get(key)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise MaterialConfigError(f"{key} must be a positive integer.") from None
        if value <= 0:
            raise MaterialConfigError(f"{key} must be a positive integer.")
        max_bytes_by_category[category] = value

    enabled_categories = frozenset(EXTENSION_CATEGORY[ext] for ext in extensions)

    # A fresh checkout must never gain a filesystem side effect just from
    # starting the application -- the root is created lazily on first
    # upload. The only start-up check is that the nearest *existing*
    # ancestor is actually usable as a directory (fail closed on an
    # obviously broken path, e.g. MATERIAL_STORAGE_ROOT pointed at a
    # plain file).
    probe = storage_root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if probe.exists() and not probe.is_dir():
        raise MaterialConfigError(
            f"MATERIAL_STORAGE_ROOT ({storage_root}) is not usable: '{probe}' exists and is "
            "not a directory."
        )

    return MaterialConfig(
        storage_root=storage_root,
        allowed_extensions=extensions,
        max_bytes_by_category=max_bytes_by_category,
        enabled_categories=enabled_categories,
    )


def current_material_config():
    """The current app's resolved :class:`MaterialConfig` -- stashed on
    ``app.extensions`` by ``create_app`` at start-up."""
    return current_app.extensions["material_config"]
