"""Pure, Flask-independent file-content validation for uploaded Materials
(M12, section 10).

Nothing here touches the network or performs a malware scan -- this is a
**structural** validation boundary only (extension + declared-MIME alias
+ binary signature/container). :func:`inspect_docx_zip` is the one
function that needs a real file path (to read a ZIP central directory);
every other check operates on already-read bytes so the caller controls
exactly how much of the upload is read from disk/network.

``validate_signature`` (and, for DOCX, ``inspect_docx_zip``) is the
explicit seam where a future real malware scanner would be inserted --
nothing here claims to *be* one; it only proves the container/byte
structure matches the claimed format.
"""

import zipfile
from pathlib import PureWindowsPath, PurePosixPath

#: Small, vetted extension -> accepted declared-MIME aliases map. A
#: declared MIME that is empty or the generic fallback
#: ``application/octet-stream`` is treated as "unknown" and left entirely
#: to the signature check; any other declared value must be one of these
#: aliases.
MIME_ALIASES = {
    "pdf": {"application/pdf"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "png": {"image/png"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "gif": {"image/gif"},
    "webp": {"image/webp"},
    "mp3": {"audio/mpeg", "audio/mp3"},
    "wav": {"audio/wav", "audio/x-wav", "audio/wave"},
    "mp4": {"video/mp4"},
    "webm": {"video/webm"},
}

#: The canonical, server-determined MIME type stored for each extension --
#: never the client-declared value.
CANONICAL_CONTENT_TYPE = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "webm": "video/webm",
}

_GENERIC_MIMES = {"", "application/octet-stream", "binary/octet-stream"}

#: Minimum number of leading bytes every signature check needs.
MIN_HEADER_BYTES = 32

#: Entries a genuine DOCX package must contain.
_REQUIRED_DOCX_ENTRIES = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
#: Any entry matching this name (case-insensitive) means a macro-enabled
#: package (.docm) was renamed to .docx.
_MACRO_ENTRY_NAME = "word/vbaproject.bin"

# DOCX ZIP-bomb / structural safety limits. All are read from the module
# namespace inside ``inspect_docx_zip`` so a test can monkeypatch a
# smaller value without allocating a real large archive. A genuine Word
# document -- text, styles, a few relationship parts -- is tiny compared
# to every one of these; media belongs in a separate `file` Material.
_MAX_DOCX_ENTRIES = 5000
#: Per-member uncompressed size cap.
_MAX_DOCX_UNCOMPRESSED_ENTRY_BYTES = 100 * 1024 * 1024  # 100 MiB
#: Per-member decompression ratio cap (uncompressed / compressed).
_MAX_DOCX_COMPRESSION_RATIO = 100
#: Aggregate uncompressed size across *all* members -- bounds the total
#: cost of a fully-expanded archive even when each member is individually
#: under its own cap.
_MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MiB
#: Aggregate decompression ratio across all members.
_MAX_DOCX_TOTAL_COMPRESSION_RATIO = 100
#: ZIP general-purpose bit 0 -- the "entry is encrypted" flag.
_ZIP_ENCRYPTED_FLAG = 0x1


class FileValidationError(ValueError):
    """A file failed structural validation and must not be stored."""


def normalize_original_filename(raw_name):
    """Return a safe basename for **display and download metadata only**
    -- this value must never be used to build a filesystem path.

    Strips any directory component (Windows- and POSIX-style, so a
    traversal or absolute path supplied as the filename cannot survive),
    removes control characters (including CR/LF header-injection
    attempts), collapses surrounding whitespace, and caps the length.
    Raises :class:`FileValidationError` if nothing safe remains.
    """
    if not raw_name:
        raise FileValidationError("A filename is required.")
    # Take the final path component under both separator conventions --
    # a malicious client can send either style regardless of platform.
    name = PureWindowsPath(raw_name).name
    name = PurePosixPath(name).name
    cleaned = "".join(ch for ch in name if ch.isprintable() and ch not in "\r\n\t")
    cleaned = cleaned.strip().strip(".")
    if not cleaned:
        raise FileValidationError("The filename is not safe to store.")
    return cleaned[:255]


def extension_of(filename):
    """The lowercase extension (no dot) of an already-normalized
    filename, or ``""`` if it has none."""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def validate_extension(filename, material_config):
    """Return the validated lowercase extension, or raise
    :class:`FileValidationError` if it is missing or not in the
    configured allowlist (itself always a subset of the hard-supported
    set -- see ``app.services.material_config``)."""
    ext = extension_of(filename)
    if not ext or ext not in material_config.allowed_extensions:
        raise FileValidationError("This file type is not supported.")
    return ext


def category_for_extension(extension, material_config):
    category = material_config.category_for_extension(extension)
    if category is None:
        raise FileValidationError("This file type is not supported.")
    return category


def validate_declared_mime(extension, declared_mime):
    """Raise :class:`FileValidationError` if the browser-declared MIME
    type is present and does not match the small vetted alias set for
    `extension`. A missing/generic declared type is not itself an error
    -- the binary signature check is authoritative either way."""
    declared = (declared_mime or "").split(";", 1)[0].strip().lower()
    if declared in _GENERIC_MIMES:
        return
    if declared not in MIME_ALIASES.get(extension, set()):
        raise FileValidationError("The uploaded file's declared type does not match its extension.")


def validate_signature(extension, header):
    """Verify `header` (the first `MIN_HEADER_BYTES` bytes actually read
    from the upload) matches the binary signature/container expected for
    `extension`. Raises :class:`FileValidationError` on any mismatch,
    including a header shorter than the format's own minimum signature
    (an empty or truncated file can never match).

    DOCX is intentionally **not** handled here -- see
    :func:`inspect_docx_zip`, which needs the complete file.
    """
    if extension == "pdf":
        if not header.startswith(b"%PDF-"):
            raise FileValidationError("This does not look like a valid PDF file.")
    elif extension == "png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise FileValidationError("This does not look like a valid PNG file.")
    elif extension in ("jpg", "jpeg"):
        if not header.startswith(b"\xff\xd8\xff"):
            raise FileValidationError("This does not look like a valid JPEG file.")
    elif extension == "gif":
        if not (header.startswith(b"GIF87a") or header.startswith(b"GIF89a")):
            raise FileValidationError("This does not look like a valid GIF file.")
    elif extension == "webp":
        if not (len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] == b"WEBP"):
            raise FileValidationError("This does not look like a valid WebP file.")
    elif extension == "wav":
        if not (len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] == b"WAVE"):
            raise FileValidationError("This does not look like a valid WAV file.")
    elif extension == "mp3":
        is_id3 = header.startswith(b"ID3")
        is_frame_sync = len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        if not (is_id3 or is_frame_sync):
            raise FileValidationError("This does not look like a valid MP3 file.")
    elif extension == "mp4":
        if not (len(header) >= 8 and header[4:8] == b"ftyp"):
            raise FileValidationError("This does not look like a valid MP4 file.")
    elif extension == "webm":
        if not header.startswith(b"\x1a\x45\xdf\xa3"):
            raise FileValidationError("This does not look like a valid WebM file.")
    elif extension == "docx":
        return  # validated separately by inspect_docx_zip against the full file
    else:  # pragma: no cover -- unreachable once validate_extension has run
        raise FileValidationError("This file type is not supported.")


def inspect_docx_zip(path):
    """Safely inspect a candidate DOCX's ZIP **structure** (central
    directory metadata only -- never extracts member content) and raise
    :class:`FileValidationError` if it is not a genuine, safe DOCX
    package:

    - must actually be a ZIP container (rejects a legacy binary .DOC);
    - must contain every required DOCX part (rejects a generic ZIP or a
      different Office XML package such as .xlsx/.pptx);
    - no member name may be absolute or contain a ``..`` traversal
      segment;
    - no member may be encrypted (a plain .docx never is);
    - a non-empty member may not report a zero compressed size
      (physically impossible -- a crafted central-directory value);
    - the entry count, **and each member's *and* the whole archive's
      uncompressed size / decompression ratio**, must stay under
      conservative caps (rejects both single-entry and aggregate
      zip-bomb-style archives);
    - no entry may be a macro payload (``word/vbaProject.bin``), which
      would mean a macro-enabled .docm was renamed to .docx.
    """
    try:
        if not zipfile.is_zipfile(path):
            raise FileValidationError("This does not look like a valid DOCX file.")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_DOCX_ENTRIES:
                raise FileValidationError("This DOCX file has an unexpectedly large structure.")

            names = set()
            total_uncompressed = 0
            total_compressed = 0
            for info in infos:
                name = info.filename
                names.add(name)
                normalized = name.replace("\\", "/")
                if normalized.startswith("/") or PurePosixPath(normalized).is_absolute():
                    raise FileValidationError("This DOCX file has an unsafe internal path.")
                if any(part == ".." for part in normalized.split("/")):
                    raise FileValidationError("This DOCX file has an unsafe internal path.")
                if info.flag_bits & _ZIP_ENCRYPTED_FLAG:
                    raise FileValidationError("Encrypted DOCX files are not supported.")
                if info.file_size > 0 and info.compress_size == 0:
                    raise FileValidationError("This DOCX file has a malformed entry.")
                if info.file_size > _MAX_DOCX_UNCOMPRESSED_ENTRY_BYTES:
                    raise FileValidationError("This DOCX file has an unexpectedly large entry.")
                if info.compress_size > 0 and (
                    info.file_size / info.compress_size > _MAX_DOCX_COMPRESSION_RATIO
                ):
                    raise FileValidationError("This DOCX file has a suspicious compression ratio.")
                if normalized.lower() == _MACRO_ENTRY_NAME:
                    raise FileValidationError(
                        "Macro-enabled Office files are not supported. Save as a plain .docx "
                        "file first."
                    )
                total_uncompressed += info.file_size
                total_compressed += info.compress_size
                if total_uncompressed > _MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES:
                    raise FileValidationError("This DOCX file expands to an unexpectedly large size.")

            if total_compressed > 0 and (
                total_uncompressed / total_compressed > _MAX_DOCX_TOTAL_COMPRESSION_RATIO
            ):
                raise FileValidationError("This DOCX file has a suspicious overall compression ratio.")

            if not _REQUIRED_DOCX_ENTRIES.issubset(names):
                raise FileValidationError("This does not look like a valid DOCX file.")
    except zipfile.BadZipFile as exc:
        raise FileValidationError("This does not look like a valid DOCX file.") from exc
