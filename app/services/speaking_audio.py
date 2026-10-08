"""The Speaking-specific audio upload policy (Phase 4 / M06).

**Why this module exists at all.** M12's Material pipeline maps an
extension to a category with one server-owned table
(``material_config.EXTENSION_CATEGORY``), and there ``webm`` and ``mp4``
are **video** -- which is correct for a Lesson Material, where those
extensions really do carry film. A browser ``MediaRecorder``, however,
produces audio in exactly those two containers on the two dominant
engines: Chromium and Firefox negotiate ``audio/webm`` and Safari
``audio/mp4``. Refusing them would have meant refusing browser recording
altogether, and re-categorising them globally would have silently changed
what a Teacher's uploaded ``.mp4`` Material is.

So M06 adds the **smallest** explicit Speaking path instead:

- the Material extension/category map, its size keys and every existing
  Material behaviour are left **byte-for-byte unchanged**;
- this module owns one small, closed extension set, one canonical
  ``audio`` content type per extension, and one declared-MIME alias set;
- the stored :class:`~app.models.enums.FileCategory` is always ``audio``
  and the stored ``content_type`` is always the canonical *audio* MIME,
  never the browser's declared value and never M12's video mapping;
- the byte limit is the configured **audio** limit
  (``MATERIAL_MAX_AUDIO_BYTES``), whatever category M12 would have
  assigned the extension;
- the **binary signature check is the existing one**
  (:func:`app.services.file_validation.validate_signature`), reused
  rather than re-implemented, so a format's container structure has
  exactly one definition in this project.

**Honest limitation, stated rather than hidden.** A structural container
check proves that a file *is* a WebM (EBML) or an ISO-BMFF/MP4 container.
It cannot prove that such a container carries **no video track** -- that
would need a real media parser, and M06 installs no FFmpeg and no other
dependency. What bounds the consequence is everything around it: the
configured audio byte limit applies, the stored category is ``audio``,
the stored content type is an ``audio/*`` one, the file is served only
from an authorized private route with ``X-Content-Type-Options: nosniff``,
and it is rendered only inside an ``<audio>`` element. A Student who
deliberately uploaded a video file through the fallback input would get a
file stored and served as audio, not an embedded video. This is recorded
in ``docs/DECISIONS.md`` as a known boundary, not as a solved problem.

**Nothing here trusts the browser.** The filename supplies a *candidate*
extension only; the declared MIME type is checked against a small vetted
alias set and is otherwise ignored; and the binary signature is what
actually decides. A WAV renamed ``.webm`` fails, and so does an
executable, an image, a document or a plain-text file under any name.

Flask-independent except for reading the resolved
:class:`~app.services.material_config.MaterialConfig` it is handed.
"""

from app.models.enums import FileCategory
from app.services.file_storage import stream_upload_to_storage
from app.services.file_validation import (
    FileValidationError,
    extension_of,
    normalize_original_filename,
    validate_signature,
)

#: The closed set of extensions a Speaking recording may carry. Exactly
#: the practical browser recording/upload set the Part names, and
#: deliberately no wider: every member is a format
#: ``file_validation.validate_signature`` already knows how to check.
#:
#: - ``webm`` -- Chromium / Firefox ``MediaRecorder`` (``audio/webm``);
#: - ``mp4``  -- Safari ``MediaRecorder`` (``audio/mp4``);
#: - ``wav``  -- uncompressed, and the common fallback-input format;
#: - ``mp3``  -- the common fallback-input format from other tools.
SPEAKING_AUDIO_EXTENSIONS = ("webm", "mp4", "wav", "mp3")

#: The canonical, **server-determined** content type stored for each --
#: always an ``audio/*`` type, never the client-declared value and never
#: M12's ``video/webm`` / ``video/mp4`` Material mapping. This is the
#: value the authorized serving route sends.
SPEAKING_CONTENT_TYPE = {
    "webm": "audio/webm",
    "mp4": "audio/mp4",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
}

#: Accepted declared-MIME aliases per extension. A declared type that is
#: empty or a generic fallback is treated as "unknown" and left entirely
#: to the signature check; any other declared value must be one of these.
#: ``MediaRecorder`` appends a codecs parameter
#: (``audio/webm;codecs=opus``), which is stripped before comparison.
#:
#: **Why ``video/webm`` and ``video/mp4`` are accepted here.** WebM and
#: ISO-BMFF are *containers*, and their registered type is the video one:
#: a browser asked to upload an audio-only ``.webm`` file through an
#: ordinary file input declares ``video/webm``, because it is guessing
#: from the extension and nothing else. Refusing that would break the
#: no-``MediaRecorder`` fallback path while providing no protection at
#: all -- the declared type is a hint from the client, never evidence,
#: and it is not what decides anything: the extension allowlist, the
#: binary container signature and the forced ``audio`` category and
#: canonical ``audio/*`` content type are. The genuine limitation (a
#: container check cannot prove the absence of a video track without a
#: media parser) is stated in this module's docstring and in
#: ``docs/DECISIONS.md``, not papered over with a MIME check that a
#: client controls.
SPEAKING_MIME_ALIASES = {
    "webm": {"audio/webm", "video/webm"},
    "mp4": {"audio/mp4", "audio/x-m4a", "audio/m4a", "video/mp4"},
    "wav": {"audio/wav", "audio/x-wav", "audio/wave"},
    "mp3": {"audio/mpeg", "audio/mp3"},
}

_GENERIC_MIMES = {"", "application/octet-stream", "binary/octet-stream"}

#: What the fallback file input advertises. A courtesy for the file
#: picker only -- the server never trusts it, and this module re-derives
#: the extension, checks the declared MIME and checks the binary
#: signature itself. ``audio/*`` is included so a browser offers every
#: audio file the device holds rather than only these four extensions,
#: which the server then narrows.
SPEAKING_AUDIO_ACCEPT = "audio/*,.webm,.mp4,.m4a,.wav,.mp3"

#: The human list, formatted once so the form help text, the recording
#: page and the server error message cannot describe the supported set
#: differently.
SPEAKING_FORMATS_LABEL = "WebM, MP4, WAV or MP3"

#: The one Student-facing sentence for an unsupported file. Declared once
#: so the pre-check and the storage path cannot word the same rejection
#: differently, and deliberately generic -- it names formats, never a
#: path, a MIME internals detail or a validator name.
UNSUPPORTED_AUDIO_MESSAGE = (
    "That file is not a supported audio recording. Record with the button above, or choose "
    f"a {SPEAKING_FORMATS_LABEL} audio file."
)

_AUDIO_CATEGORY = FileCategory.AUDIO.value


def candidate_extension(filename):
    """The lowercase extension of an untrusted browser filename, or
    ``""``.

    A convenience for the routes' cheap pre-check, which refuses an
    obviously wrong file *before* its bytes are streamed to disk at all.
    It is deliberately **not** the authority: ``store_speaking_audio``
    re-derives the extension from the normalized name and the binary
    signature decides.
    """
    return extension_of(normalize_original_filename_safe(filename))


def normalize_original_filename_safe(raw_name):
    """:func:`~app.services.file_validation.normalize_original_filename`,
    but returning ``""`` instead of raising for an empty or unusable
    name.

    Used only by the pre-check, which wants a comparison rather than an
    exception; the storage path calls the strict function.
    """
    try:
        return normalize_original_filename(raw_name)
    except FileValidationError:
        return ""


def validate_speaking_extension(filename):
    """The validated lowercase extension for a Speaking recording, or
    :class:`~app.services.file_validation.FileValidationError`.

    The allowlist is :data:`SPEAKING_AUDIO_EXTENSIONS` -- this module's
    own closed set, deliberately **not**
    ``MaterialConfig.allowed_extensions``. Those two answer different
    questions: the configured list narrows what a Teacher may attach to a
    *Lesson Material*, and narrowing it must not silently disable a
    Student's ability to hand in spoken work. The byte limit, which is a
    genuine deployment concern, *is* read from configuration.
    """
    ext = extension_of(normalize_original_filename(filename))
    if not ext or ext not in SPEAKING_AUDIO_EXTENSIONS:
        raise FileValidationError(UNSUPPORTED_AUDIO_MESSAGE)
    return ext


def validate_speaking_declared_mime(extension, declared_mime):
    """Raise if the browser-declared MIME type is present and is not one
    of the vetted audio aliases for `extension`.

    A missing or generic declared type is not itself an error -- the
    binary signature check is authoritative either way. A declared
    ``image/png``, ``application/pdf``, ``text/html`` or any other type
    outside the alias set for `extension` is rejected. The two container
    types ``video/webm`` and ``video/mp4`` are accepted for their own
    extensions -- see :data:`SPEAKING_MIME_ALIASES` for why -- and the
    stored category and content type are ``audio`` regardless.
    """
    declared = (declared_mime or "").split(";", 1)[0].strip().lower()
    if declared in _GENERIC_MIMES:
        return
    if declared not in SPEAKING_MIME_ALIASES.get(extension, set()):
        raise FileValidationError(UNSUPPORTED_AUDIO_MESSAGE)


def store_speaking_audio(material_config, file_storage, original_filename_raw):
    """Stream, validate and store one Speaking recording.

    Returns the same :class:`~app.services.file_storage.StoredUpload`
    shape every upload path in this project returns, with ``category``
    forced to ``audio`` and ``content_type`` set to the canonical audio
    MIME for the validated extension.

    The order is exactly M12's, and the reasons are the same:

    1. normalize the untrusted filename (display metadata only -- it never
       contributes to a filesystem path);
    2. validate the extension against this module's closed set;
    3. validate the declared MIME against the vetted alias set;
    4. stream to a random temporary file under the private storage root
       in bounded chunks, enforcing the configured **audio** byte limit
       while streaming and computing SHA-256 as it goes -- the whole file
       is never held in memory;
    5. reject an empty upload;
    6. check the binary signature/container against the first bytes
       actually read;
    7. atomically publish the file under a fresh random storage key.

    **No database lock may be held while this runs** -- the same rule
    ``store_validated_upload`` states, for the same reason: streaming a
    50 MB recording inside an open write transaction is exactly what Part
    M12 forbids. On **any** failure the temporary file, and the final file
    if it was already created, have deletion attempted before the
    exception propagates.
    """
    original_filename = normalize_original_filename(original_filename_raw)
    extension = validate_speaking_extension(original_filename)
    validate_speaking_declared_mime(extension, getattr(file_storage, "mimetype", None))
    max_bytes = material_config.max_bytes_for_category(_AUDIO_CATEGORY)

    return stream_upload_to_storage(
        material_config,
        file_storage,
        original_filename=original_filename,
        extension=extension,
        category=_AUDIO_CATEGORY,
        content_type=SPEAKING_CONTENT_TYPE[extension],
        max_bytes=max_bytes,
        validate_header=lambda header: validate_signature(extension, header),
        oversize_message=(
            "That recording is larger than the maximum allowed size. Please record a shorter "
            "answer and submit again."
        ),
        empty_message="That recording is empty. Please record again before submitting.",
        signature_message=UNSUPPORTED_AUDIO_MESSAGE,
    )
