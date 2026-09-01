"""M12 secure file validation + storage.

Every filesystem test uses an isolated ``tmp_path`` -- never the real
development ``storage/materials`` tree.
"""

import hashlib
import io
import logging
import os
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app.services.file_validation import (
    FileValidationError,
    inspect_docx_zip,
    normalize_original_filename,
    validate_declared_mime,
    validate_signature,
)
from app.services.file_storage import (
    StorageContainmentError,
    _safe_unlink,
    delete_stored_file,
    resolve_within_root,
    store_validated_upload,
)
from app.services.material_config import resolve_material_config
from tests import file_fixtures as ff

SUPPORTED = ["pdf", "docx", "png", "jpg", "jpeg", "gif", "webp", "mp3", "wav", "mp4", "webm"]


def _config(tmp_path, root_name="materials"):
    return resolve_material_config(
        {
            "MATERIAL_STORAGE_ROOT": str(tmp_path / root_name),
            "MATERIAL_ALLOWED_EXTENSIONS": ",".join(SUPPORTED),
            "MATERIAL_MAX_DOCUMENT_BYTES": "26214400",
            "MATERIAL_MAX_IMAGE_BYTES": "10485760",
            "MATERIAL_MAX_AUDIO_BYTES": "52428800",
            "MATERIAL_MAX_VIDEO_BYTES": "104857600",
        },
        project_root=tmp_path,
    )


def _fs(data, filename, content_type=None):
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type=content_type)


# ===========================================================================
# Filename normalisation -- never a path
# ===========================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("/absolute/path/file.png", "file.png"),
        ("C:\\Windows\\System32\\evil.docx", "evil.docx"),
        ("folder\\sub\\name.mp3", "name.mp3"),
        ("  spaced name .pdf  ", "spaced name .pdf"),
    ],
)
def test_normalize_original_filename(raw, expected):
    assert normalize_original_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "\r\n", "...", "//", "\x00\x01"])
def test_normalize_original_filename_rejects_unsafe(raw):
    with pytest.raises(FileValidationError):
        normalize_original_filename(raw)


def test_crlf_stripped_from_filename():
    assert "\r" not in normalize_original_filename("na\rme\n.pdf")
    assert "\n" not in normalize_original_filename("na\rme\n.pdf")


# ===========================================================================
# Signature checks -- each supported format's minimal sample passes
# ===========================================================================


@pytest.mark.parametrize("ext", SUPPORTED)
def test_minimal_valid_sample_passes_signature(ext):
    data = ff.MINIMAL_VALID_BY_EXTENSION[ext]()
    from app.services.file_validation import MIN_HEADER_BYTES

    validate_signature(ext, data[:MIN_HEADER_BYTES])  # no exception


@pytest.mark.parametrize("ext", [e for e in SUPPORTED if e != "docx"])
def test_signature_mismatch_rejected(ext):
    with pytest.raises(FileValidationError):
        validate_signature(ext, ff.not_a_real_format(ext)[:32])


@pytest.mark.parametrize("ext", [e for e in SUPPORTED if e != "docx"])
def test_empty_header_rejected_by_signature(ext):
    with pytest.raises(FileValidationError):
        validate_signature(ext, b"")


def test_declared_mime_mismatch_rejected():
    with pytest.raises(FileValidationError):
        validate_declared_mime("pdf", "image/png")


def test_declared_mime_generic_is_allowed():
    validate_declared_mime("pdf", "application/octet-stream")
    validate_declared_mime("pdf", "")
    validate_declared_mime("pdf", None)


def test_declared_mime_alias_accepted():
    validate_declared_mime("mp3", "audio/mp3")
    validate_declared_mime("wav", "audio/x-wav")


# ===========================================================================
# DOCX ZIP inspection (via store, which calls inspect_docx_zip)
# ===========================================================================


def test_docx_zip_helpers(tmp_path):
    good = tmp_path / "good.docx"
    good.write_bytes(ff.minimal_docx())
    inspect_docx_zip(str(good))  # no exception

    for builder in (
        ff.docx_with_macro,
        ff.docx_missing_required_entry,
        ff.docx_with_traversal_entry,
        ff.docx_with_absolute_entry,
        ff.generic_zip,
        ff.legacy_doc,
        ff.docx_with_encrypted_entry,
        ff.docx_with_zero_compress_size,
    ):
        bad = tmp_path / "bad.docx"
        bad.write_bytes(builder())
        with pytest.raises(FileValidationError):
            inspect_docx_zip(str(bad))


def test_docx_aggregate_uncompressed_cap(tmp_path, monkeypatch):
    """Every member individually fits, but their aggregate uncompressed
    size exceeds the total cap -- rejected. Uses a tiny monkeypatched cap
    so nothing large is allocated."""
    import app.services.file_validation as fv

    monkeypatch.setattr(fv, "_MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES", 1024)
    bomb = tmp_path / "bomb.docx"
    bomb.write_bytes(ff.docx_aggregate_bomb(entry_count=12, entry_uncompressed_bytes=400))
    with pytest.raises(FileValidationError):
        inspect_docx_zip(str(bomb))


def test_docx_aggregate_compression_ratio_cap(tmp_path, monkeypatch):
    import app.services.file_validation as fv

    monkeypatch.setattr(fv, "_MAX_DOCX_TOTAL_COMPRESSION_RATIO", 2)
    # Highly compressible filler -> large aggregate ratio, tiny bytes.
    bomb = tmp_path / "ratio.docx"
    bomb.write_bytes(ff.docx_aggregate_bomb(entry_count=6, entry_uncompressed_bytes=2000))
    with pytest.raises(FileValidationError):
        inspect_docx_zip(str(bomb))


def test_docx_aggregate_bomb_rejected_through_store_and_cleaned(tmp_path, monkeypatch):
    import app.services.file_validation as fv

    monkeypatch.setattr(fv, "_MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES", 1024)
    cfg = _config(tmp_path)
    with pytest.raises(FileValidationError):
        store_validated_upload(
            cfg,
            _fs(ff.docx_aggregate_bomb(), "b.docx", ff.CANONICAL_MIME_BY_EXTENSION["docx"]),
            "b.docx",
        )
    assert not cfg.storage_root.exists() or os.listdir(cfg.storage_root) == []


# ===========================================================================
# store_validated_upload -- happy path per format
# ===========================================================================


@pytest.mark.parametrize("ext", SUPPORTED)
def test_store_each_supported_format(tmp_path, ext):
    cfg = _config(tmp_path)
    data = ff.MINIMAL_VALID_BY_EXTENSION[ext]()
    result = store_validated_upload(cfg, _fs(data, f"sample.{ext}", ff.CANONICAL_MIME_BY_EXTENSION[ext]), f"sample.{ext}")
    assert result.extension == ext
    assert result.byte_size == len(data)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.content_type == ff.CANONICAL_MIME_BY_EXTENSION[ext]
    stored = cfg.storage_root / result.storage_key
    assert stored.is_file()
    # random unguessable stored name (not the original)
    assert "sample" not in result.storage_key
    assert result.storage_key.endswith(f".{ext}")


def test_stored_name_is_random_and_unique(tmp_path):
    cfg = _config(tmp_path)
    keys = set()
    for _ in range(5):
        r = store_validated_upload(cfg, _fs(ff.minimal_png(), "a.png", "image/png"), "a.png")
        keys.add(r.storage_key)
    assert len(keys) == 5


def test_original_filename_never_becomes_a_path(tmp_path):
    cfg = _config(tmp_path)
    r = store_validated_upload(
        cfg, _fs(ff.minimal_pdf(), "../../../secret.pdf", "application/pdf"), "../../../secret.pdf"
    )
    assert r.original_filename == "secret.pdf"
    assert "secret.pdf" not in r.storage_key
    assert set(os.listdir(cfg.storage_root)) == {r.storage_key}


# ===========================================================================
# store_validated_upload -- rejections + cleanup
# ===========================================================================


def test_empty_file_rejected_and_cleaned(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(FileValidationError):
        store_validated_upload(cfg, _fs(b"", "e.pdf", "application/pdf"), "e.pdf")
    assert os.listdir(cfg.storage_root) == []


def test_unsupported_extension_rejected(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(FileValidationError):
        store_validated_upload(cfg, _fs(ff.minimal_pdf(), "x.exe", None), "x.exe")
    assert not cfg.storage_root.exists() or os.listdir(cfg.storage_root) == []


def test_mime_mismatch_rejected_and_cleaned(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(FileValidationError):
        store_validated_upload(cfg, _fs(ff.minimal_pdf(), "x.pdf", "image/png"), "x.pdf")
    assert not cfg.storage_root.exists() or os.listdir(cfg.storage_root) == []


def test_signature_mismatch_rejected_and_cleaned(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(FileValidationError):
        store_validated_upload(cfg, _fs(ff.executable_bytes(), "x.pdf", "application/pdf"), "x.pdf")
    assert os.listdir(cfg.storage_root) == []


@pytest.mark.parametrize(
    "data,name,mime",
    [
        (b"", "e.png", "image/png"),
        (b"<svg/>", "x.svg", "image/svg+xml"),
        (b"<html></html>", "x.html", "text/html"),
        (None, "x.doc", None),
    ],
)
def test_rejected_formats_leave_no_file(tmp_path, data, name, mime):
    cfg = _config(tmp_path)
    payload = ff.legacy_doc() if data is None else data
    with pytest.raises(FileValidationError):
        store_validated_upload(cfg, _fs(payload, name, mime), name)
    assert not cfg.storage_root.exists() or os.listdir(cfg.storage_root) == []


def test_macro_docx_rejected_and_cleaned(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(FileValidationError):
        store_validated_upload(
            cfg,
            _fs(ff.docx_with_macro(), "m.docx", ff.CANONICAL_MIME_BY_EXTENSION["docx"]),
            "m.docx",
        )
    assert os.listdir(cfg.storage_root) == []


def test_generic_zip_as_docx_rejected(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(FileValidationError):
        store_validated_upload(cfg, _fs(ff.generic_zip(), "z.docx", None), "z.docx")
    assert os.listdir(cfg.storage_root) == []


def test_oversize_file_rejected_early(tmp_path):
    cfg = resolve_material_config(
        {
            "MATERIAL_STORAGE_ROOT": str(tmp_path / "materials"),
            "MATERIAL_ALLOWED_EXTENSIONS": "png",
            "MATERIAL_MAX_DOCUMENT_BYTES": "1024",
            "MATERIAL_MAX_IMAGE_BYTES": "64",  # tiny cap
            "MATERIAL_MAX_AUDIO_BYTES": "1024",
            "MATERIAL_MAX_VIDEO_BYTES": "1024",
        },
        project_root=tmp_path,
    )
    big = ff.minimal_png() + b"\x00" * 5000
    with pytest.raises(FileValidationError):
        store_validated_upload(cfg, _fs(big, "big.png", "image/png"), "big.png")
    assert os.listdir(cfg.storage_root) == []


def test_chunked_hash_and_size_match_for_large_input(tmp_path):
    cfg = _config(tmp_path)
    # A few MiB of PNG (valid header, padded) -- exercises multi-chunk streaming.
    data = ff.minimal_png() + b"\x00" * (3 * 1024 * 1024)
    r = store_validated_upload(cfg, _fs(data, "big.png", "image/png"), "big.png")
    assert r.byte_size == len(data)
    assert r.sha256 == hashlib.sha256(data).hexdigest()
    assert (cfg.storage_root / r.storage_key).stat().st_size == len(data)


# ===========================================================================
# Containment
# ===========================================================================


def test_resolve_within_root_blocks_escape(tmp_path):
    cfg = _config(tmp_path)
    cfg.storage_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(StorageContainmentError):
        resolve_within_root(cfg, "../escape.txt")
    with pytest.raises(StorageContainmentError):
        resolve_within_root(cfg, "../../etc/passwd")


def test_resolve_within_root_allows_plain_name(tmp_path):
    cfg = _config(tmp_path)
    cfg.storage_root.mkdir(parents=True, exist_ok=True)
    p = resolve_within_root(cfg, "abc.pdf")
    assert p.parent == cfg.storage_root.resolve()


def test_no_part_files_left_after_success(tmp_path):
    cfg = _config(tmp_path)
    store_validated_upload(cfg, _fs(ff.minimal_pdf(), "a.pdf", "application/pdf"), "a.pdf")
    assert not any(name.endswith(".part") for name in os.listdir(cfg.storage_root))


def test_no_part_files_left_after_failure(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(FileValidationError):
        store_validated_upload(cfg, _fs(ff.executable_bytes(), "a.pdf", "application/pdf"), "a.pdf")
    assert not any(name.endswith(".part") for name in os.listdir(cfg.storage_root))


# ===========================================================================
# _safe_unlink / delete_stored_file -- observable, non-masking cleanup failure
# ===========================================================================


def _patch_unlink_to_raise(monkeypatch):
    """Make Path.unlink raise OSError. Safe because `monkeypatch` is
    finalized before the `tmp_path` fixture's own cleanup runs."""
    real_unlink = Path.unlink

    def boom(self, *a, **k):
        raise OSError(13, "Permission denied (simulated)")

    monkeypatch.setattr(Path, "unlink", boom)
    return real_unlink


def test_safe_unlink_returns_true_on_success(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"x")
    assert _safe_unlink(f) is True
    assert not f.exists()
    assert _safe_unlink(f) is True  # already gone -> still success
    assert _safe_unlink(None) is True


def test_safe_unlink_returns_false_and_logs_on_oserror(tmp_path, monkeypatch, caplog):
    f = tmp_path / "locked.bin"
    f.write_bytes(b"x")
    _patch_unlink_to_raise(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="app.services.file_storage"):
        result = _safe_unlink(f)  # must NOT raise
    assert result is False
    assert "Could not delete stored file" in caplog.text
    assert "locked.bin" in caplog.text


def test_delete_stored_file_propagates_success_flag(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    r = store_validated_upload(cfg, _fs(ff.minimal_pdf(), "a.pdf", "application/pdf"), "a.pdf")
    assert delete_stored_file(cfg, r.storage_key) is True

    r2 = store_validated_upload(cfg, _fs(ff.minimal_pdf(), "b.pdf", "application/pdf"), "b.pdf")
    _patch_unlink_to_raise(monkeypatch)
    assert delete_stored_file(cfg, r2.storage_key) is False  # logged, not raised


def test_delete_stored_file_keeps_containment_check(tmp_path):
    cfg = _config(tmp_path)
    cfg.storage_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(StorageContainmentError):
        delete_stored_file(cfg, "../escape.bin")


def test_store_cleanup_failure_is_logged_and_original_exception_preserved(tmp_path, monkeypatch, caplog):
    """A validation failure whose temp-file cleanup then fails: the
    ORIGINAL FileValidationError still propagates, and the cleanup
    failure is visible in the log."""
    cfg = _config(tmp_path)
    _patch_unlink_to_raise(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="app.services.file_storage"):
        with pytest.raises(FileValidationError) as exc_info:
            store_validated_upload(
                cfg, _fs(ff.executable_bytes(), "a.pdf", "application/pdf"), "a.pdf"
            )
    assert "does not look like a valid PDF" in str(exc_info.value)  # original, not an OSError
    assert "Could not delete stored file" in caplog.text
