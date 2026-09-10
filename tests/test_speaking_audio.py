"""The Phase 4 / M06 Speaking audio policy: what is accepted, what is
refused, what is stored, and what is cleaned up.

These are unit tests over ``app/services/speaking_audio.py`` and the
shared streaming core it reuses. Nothing here claims a codec, a container
parse beyond the documented structural signature check, a real microphone
or a real browser: the fixtures are minimal *structurally valid* files
built in memory (``tests/file_fixtures.py``), which is exactly what the
validation boundary inspects.
"""

import io

import pytest

from app.services.file_validation import FileValidationError
from app.services.material_config import (
    EXTENSION_CATEGORY,
    HARD_ALLOWED_EXTENSIONS,
    resolve_material_config,
)
from app.services.speaking_audio import (
    SPEAKING_AUDIO_ACCEPT,
    SPEAKING_AUDIO_EXTENSIONS,
    SPEAKING_CONTENT_TYPE,
    SPEAKING_MIME_ALIASES,
    UNSUPPORTED_AUDIO_MESSAGE,
    candidate_extension,
    store_speaking_audio,
    validate_speaking_declared_mime,
    validate_speaking_extension,
)
from tests.file_fixtures import (
    executable_bytes,
    html_bytes,
    minimal_docx,
    minimal_mp3,
    minimal_mp3_frame_sync,
    minimal_mp4,
    minimal_pdf,
    minimal_png,
    minimal_wav,
    minimal_webm,
    not_a_real_format,
    svg_bytes,
)


class _Upload:
    """The smallest stand-in for a Werkzeug ``FileStorage``: a stream and
    a declared mimetype, which is all the storage path reads."""

    def __init__(self, data, filename, mimetype=None):
        self.stream = io.BytesIO(data)
        self.filename = filename
        self.mimetype = mimetype


def _config(app):
    return app.extensions["material_config"]


# ===========================================================================
# The allowlist and the category override
# ===========================================================================


def test_the_supported_set_is_exactly_the_four_practical_formats():
    assert SPEAKING_AUDIO_EXTENSIONS == ("webm", "mp4", "wav", "mp3")
    assert set(SPEAKING_CONTENT_TYPE) == set(SPEAKING_AUDIO_EXTENSIONS)
    assert set(SPEAKING_MIME_ALIASES) == set(SPEAKING_AUDIO_EXTENSIONS)


def test_every_speaking_extension_is_one_the_project_already_validates():
    assert set(SPEAKING_AUDIO_EXTENSIONS) <= HARD_ALLOWED_EXTENSIONS


def test_every_stored_content_type_is_an_audio_one():
    assert SPEAKING_CONTENT_TYPE == {
        "webm": "audio/webm",
        "mp4": "audio/mp4",
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
    }
    assert all(value.startswith("audio/") for value in SPEAKING_CONTENT_TYPE.values())


def test_the_material_extension_category_map_is_unchanged():
    """The whole reason this module exists: M12 still calls WebM and MP4
    video, and M06 must not have rewritten that for Materials."""
    assert EXTENSION_CATEGORY["webm"] == "video"
    assert EXTENSION_CATEGORY["mp4"] == "video"
    assert EXTENSION_CATEGORY["mp3"] == "audio"
    assert EXTENSION_CATEGORY["wav"] == "audio"


def test_the_accept_attribute_advertises_audio_only():
    assert SPEAKING_AUDIO_ACCEPT.startswith("audio/*")
    assert "video/" not in SPEAKING_AUDIO_ACCEPT


@pytest.mark.parametrize("extension", ["webm", "mp4", "wav", "mp3"])
def test_a_supported_extension_validates(extension):
    assert validate_speaking_extension(f"speaking-recording.{extension}") == extension


@pytest.mark.parametrize(
    "filename",
    [
        "notes.pdf", "photo.png", "paper.docx", "clip.avi", "clip.ogg", "clip.m4a",
        "recording", "recording.", ".webm",
    ],
)
def test_an_unsupported_extension_is_refused_with_the_shared_message(filename):
    with pytest.raises(FileValidationError) as exc:
        validate_speaking_extension(filename)
    assert str(exc.value) == UNSUPPORTED_AUDIO_MESSAGE


def test_candidate_extension_never_raises_on_a_hostile_name():
    assert candidate_extension("") == ""
    assert candidate_extension("...") == ""
    assert candidate_extension(r"C:\evil\..\..\recording.webm") == "webm"
    assert candidate_extension("/etc/passwd") == ""


# ===========================================================================
# The declared MIME type -- a hint, checked, never trusted
# ===========================================================================


@pytest.mark.parametrize(
    "extension,declared",
    [
        ("webm", "audio/webm"),
        ("webm", "audio/webm;codecs=opus"),
        ("webm", "video/webm"),
        ("mp4", "audio/mp4"),
        ("mp4", "audio/mp4;codecs=mp4a.40.2"),
        ("mp4", "video/mp4"),
        ("wav", "audio/wav"),
        ("wav", "audio/x-wav"),
        ("mp3", "audio/mpeg"),
        ("mp3", ""),
        ("mp3", "application/octet-stream"),
        ("webm", None),
    ],
)
def test_an_accepted_declared_mime_passes(extension, declared):
    validate_speaking_declared_mime(extension, declared)


@pytest.mark.parametrize(
    "extension,declared",
    [
        ("webm", "image/png"),
        ("webm", "audio/wav"),
        ("mp4", "application/pdf"),
        ("wav", "video/webm"),
        ("mp3", "text/html"),
        ("mp3", "audio/webm"),
    ],
)
def test_a_mismatched_declared_mime_is_refused(extension, declared):
    with pytest.raises(FileValidationError) as exc:
        validate_speaking_declared_mime(extension, declared)
    assert str(exc.value) == UNSUPPORTED_AUDIO_MESSAGE


# ===========================================================================
# Storing -- the accepted cases
# ===========================================================================


@pytest.mark.parametrize(
    "extension,data,declared,content_type",
    [
        ("webm", minimal_webm(), "audio/webm;codecs=opus", "audio/webm"),
        ("webm", minimal_webm(), "video/webm", "audio/webm"),
        ("mp4", minimal_mp4(), "audio/mp4", "audio/mp4"),
        ("mp4", minimal_mp4(), "video/mp4", "audio/mp4"),
        ("wav", minimal_wav(), "audio/wav", "audio/wav"),
        ("mp3", minimal_mp3(), "audio/mpeg", "audio/mpeg"),
        ("mp3", minimal_mp3_frame_sync(), "audio/mpeg", "audio/mpeg"),
    ],
)
def test_a_valid_recording_is_stored_as_audio(
    material_app, extension, data, declared, content_type
):
    with material_app.app_context():
        stored = store_speaking_audio(
            _config(material_app),
            _Upload(data, f"speaking-recording.{extension}", declared),
            f"speaking-recording.{extension}",
        )
    assert stored.extension == extension
    # The category is forced to `audio` whatever M12 would have said.
    assert stored.category == "audio"
    assert stored.content_type == content_type
    assert stored.byte_size == len(data)
    assert len(stored.sha256) == 64
    # The stored key is random and carries the validated extension -- never
    # the browser's filename.
    assert stored.storage_key.endswith(f".{extension}")
    assert "speaking-recording" not in stored.storage_key
    assert stored.original_filename == f"speaking-recording.{extension}"


def test_the_stored_file_lives_outside_the_public_static_directory(material_app):
    from pathlib import Path

    with material_app.app_context():
        config = _config(material_app)
        stored = store_speaking_audio(
            config,
            _Upload(minimal_webm(), "speaking-recording.webm", "audio/webm"),
            "speaking-recording.webm",
        )
    static_root = (Path(material_app.root_path) / "static").resolve()
    path = (config.storage_root / stored.storage_key).resolve()
    assert path.is_file()
    assert static_root not in path.parents and path != static_root


def test_two_recordings_never_share_a_storage_key(material_app):
    with material_app.app_context():
        config = _config(material_app)
        first = store_speaking_audio(
            config, _Upload(minimal_webm(), "a.webm", "audio/webm"), "a.webm"
        )
        second = store_speaking_audio(
            config, _Upload(minimal_webm(), "a.webm", "audio/webm"), "a.webm"
        )
    assert first.storage_key != second.storage_key
    # Identical bytes still hash identically -- the digest is content, the
    # key is not.
    assert first.sha256 == second.sha256


def test_a_hostile_filename_never_reaches_the_filesystem(material_app):
    with material_app.app_context():
        config = _config(material_app)
        stored = store_speaking_audio(
            config,
            _Upload(minimal_wav(), r"..\..\..\etc\passwd.wav", "audio/wav"),
            r"..\..\..\etc\passwd.wav",
        )
    assert stored.original_filename == "passwd.wav"
    assert ".." not in stored.storage_key
    assert (config.storage_root / stored.storage_key).is_file()


# ===========================================================================
# Storing -- the refused cases, and the cleanup behind each one
# ===========================================================================


def _leftovers(material_app):
    root = _config(material_app).storage_root
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_file())


@pytest.mark.parametrize(
    "filename,data,declared",
    [
        # Empty.
        ("speaking-recording.webm", b"", "audio/webm"),
        # Structurally wrong for the claimed container.
        ("speaking-recording.webm", not_a_real_format("webm"), "audio/webm"),
        ("speaking-recording.mp4", not_a_real_format("mp4"), "audio/mp4"),
        ("speaking-recording.wav", not_a_real_format("wav"), "audio/wav"),
        ("speaking-recording.mp3", not_a_real_format("mp3"), "audio/mpeg"),
        # A real file of the wrong type, renamed.
        ("speaking-recording.webm", minimal_wav(), "audio/webm"),
        ("speaking-recording.wav", minimal_webm(), "audio/wav"),
        ("speaking-recording.mp3", minimal_png(), "audio/mpeg"),
        ("speaking-recording.mp4", minimal_pdf(), "audio/mp4"),
        ("speaking-recording.webm", executable_bytes(), "audio/webm"),
        ("speaking-recording.wav", svg_bytes(), "audio/wav"),
        ("speaking-recording.mp3", html_bytes(), "audio/mpeg"),
        ("speaking-recording.mp4", minimal_docx(), "audio/mp4"),
        # An unsupported extension, whatever the bytes are.
        ("photo.png", minimal_png(), "image/png"),
        ("notes.pdf", minimal_pdf(), "application/pdf"),
        ("paper.docx", minimal_docx(), None),
        # A declared type outside the alias set.
        ("speaking-recording.webm", minimal_webm(), "image/png"),
    ],
)
def test_a_refused_upload_leaves_no_file_behind(material_app, filename, data, declared):
    with material_app.app_context():
        with pytest.raises(FileValidationError):
            store_speaking_audio(
                _config(material_app), _Upload(data, filename, declared), filename
            )
    assert _leftovers(material_app) == []


def test_an_oversized_recording_is_refused_and_cleaned_up(tmp_path):
    from app import create_app

    app = create_app(
        "testing",
        MATERIAL_STORAGE_ROOT=str(tmp_path / "materials"),
        MATERIAL_MAX_AUDIO_BYTES="64",
    )
    with app.app_context():
        config = app.extensions["material_config"]
        # The configured AUDIO limit is what bounds a Speaking recording --
        # not the (much larger) video limit M12 would use for `.webm`.
        assert config.max_bytes_for_category("audio") == 64
        payload = minimal_wav() + b"\x00" * 512
        with pytest.raises(FileValidationError) as exc:
            store_speaking_audio(
                config,
                _Upload(payload, "speaking-recording.wav", "audio/wav"),
                "speaking-recording.wav",
            )
        assert "larger than the maximum allowed size" in str(exc.value)
        root = config.storage_root
        assert not root.exists() or [p for p in root.iterdir() if p.is_file()] == []


def test_an_empty_recording_gets_its_own_message(material_app):
    with material_app.app_context():
        with pytest.raises(FileValidationError) as exc:
            store_speaking_audio(
                _config(material_app),
                _Upload(b"", "speaking-recording.webm", "audio/webm"),
                "speaking-recording.webm",
            )
    assert "empty" in str(exc.value).lower()


def test_a_signature_failure_is_reported_generically(material_app):
    """The Student is told the format is unsupported -- never which
    internal check fired, never a path, never a container detail."""
    with material_app.app_context():
        with pytest.raises(FileValidationError) as exc:
            store_speaking_audio(
                _config(material_app),
                _Upload(minimal_wav(), "speaking-recording.webm", "audio/webm"),
                "speaking-recording.webm",
            )
    message = str(exc.value)
    assert message == UNSUPPORTED_AUDIO_MESSAGE
    # It names the supported formats -- which is help, not disclosure --
    # and nothing about the check that fired, the container internals, the
    # stored file or any path.
    for leak in ("RIFF", "EBML", "ftyp", "signature", "storage", "sha256", "/", "\\"):
        assert leak not in message


def test_the_speaking_path_does_not_depend_on_the_material_extension_allowlist(tmp_path):
    """Narrowing ``MATERIAL_ALLOWED_EXTENSIONS`` governs what a Teacher may
    attach to a **Lesson Material**. It must not silently disable a
    Student's ability to hand in spoken work."""
    from app import create_app

    app = create_app(
        "testing",
        MATERIAL_STORAGE_ROOT=str(tmp_path / "materials"),
        MATERIAL_ALLOWED_EXTENSIONS="pdf",
    )
    with app.app_context():
        config = app.extensions["material_config"]
        assert config.allowed_extensions == frozenset({"pdf"})
        stored = store_speaking_audio(
            config,
            _Upload(minimal_webm(), "speaking-recording.webm", "audio/webm"),
            "speaking-recording.webm",
        )
        assert stored.category == "audio"
        assert stored.content_type == "audio/webm"


def test_the_material_upload_path_still_categorises_webm_as_video(material_app):
    """The M12 pipeline is untouched: the same bytes uploaded as a Material
    are still stored as `video`, with the video content type."""
    from app.services.file_storage import store_validated_upload

    with material_app.app_context():
        stored = store_validated_upload(
            _config(material_app),
            _Upload(minimal_webm(), "lecture.webm", "video/webm"),
            "lecture.webm",
        )
    assert stored.category == "video"
    assert stored.content_type == "video/webm"


def test_material_config_resolution_is_unchanged_by_m06(tmp_path):
    config = resolve_material_config(
        {
            "MATERIAL_STORAGE_ROOT": str(tmp_path / "storage"),
            "MATERIAL_ALLOWED_EXTENSIONS": "mp3,wav,mp4,webm",
            "MATERIAL_MAX_DOCUMENT_BYTES": "100",
            "MATERIAL_MAX_IMAGE_BYTES": "200",
            "MATERIAL_MAX_AUDIO_BYTES": "300",
            "MATERIAL_MAX_VIDEO_BYTES": "400",
        },
        tmp_path,
    )
    assert config.category_for_extension("webm") == "video"
    assert config.category_for_extension("mp3") == "audio"
    assert config.max_bytes_for_category("audio") == 300


# ===========================================================================
# The structural limitation, stated as a test rather than hidden
# ===========================================================================


def test_a_container_check_cannot_prove_the_absence_of_a_video_track(material_app):
    """M06 documents this openly: WebM and ISO-BMFF are containers, and a
    signature/structure check proves only that the file *is* one. Without
    a media parser -- which M06 does not install -- a container carrying a
    video track is indistinguishable here.

    What bounds the consequence, and is asserted: the file is stored with
    the ``audio`` category and an ``audio/*`` content type, so it is served
    and rendered as audio and never as an embedded video, and the
    configured **audio** byte limit applies to it.
    """
    with material_app.app_context():
        stored = store_speaking_audio(
            _config(material_app),
            # Structurally a valid ISO-BMFF container. Nothing here can say
            # whether its tracks are audio, video, or both.
            _Upload(minimal_mp4(), "speaking-recording.mp4", "video/mp4"),
            "speaking-recording.mp4",
        )
    assert stored.category == "audio"
    assert stored.content_type == "audio/mp4"
