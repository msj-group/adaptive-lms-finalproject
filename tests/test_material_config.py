"""M12 material configuration: fail-closed resolution + validation."""

import pytest

from app import create_app
from app.services.material_config import (
    HARD_ALLOWED_EXTENSIONS,
    MaterialConfigError,
    resolve_material_config,
)


def _base(tmp_path, **overrides):
    cfg = {
        "MATERIAL_STORAGE_ROOT": str(tmp_path / "materials"),
        "MATERIAL_ALLOWED_EXTENSIONS": "pdf,png,mp3,mp4",
        "MATERIAL_MAX_DOCUMENT_BYTES": "1000",
        "MATERIAL_MAX_IMAGE_BYTES": "1000",
        "MATERIAL_MAX_AUDIO_BYTES": "1000",
        "MATERIAL_MAX_VIDEO_BYTES": "1000",
    }
    cfg.update(overrides)
    return cfg


def test_valid_config_resolves(tmp_path):
    mc = resolve_material_config(_base(tmp_path), project_root=tmp_path)
    assert mc.storage_root == (tmp_path / "materials").resolve()
    assert mc.allowed_extensions == frozenset({"pdf", "png", "mp3", "mp4"})
    assert mc.max_upload_bytes == 1000
    assert mc.max_content_length == 1000 + 65_536


def test_relative_root_resolves_from_project_root(tmp_path):
    mc = resolve_material_config(
        _base(tmp_path, MATERIAL_STORAGE_ROOT="var/materials"), project_root=tmp_path
    )
    assert mc.storage_root == (tmp_path / "var" / "materials").resolve()


# ---------------------------------------------------------------------------
# Enabled-category request sizing (review finding 4)
# ---------------------------------------------------------------------------


def _sized(tmp_path, exts):
    return resolve_material_config(
        _base(
            tmp_path,
            MATERIAL_ALLOWED_EXTENSIONS=exts,
            MATERIAL_MAX_DOCUMENT_BYTES="100",
            MATERIAL_MAX_IMAGE_BYTES="200",
            MATERIAL_MAX_AUDIO_BYTES="400",
            MATERIAL_MAX_VIDEO_BYTES="9999",
        ),
        project_root=tmp_path,
    )


def test_size_uses_only_enabled_single_category(tmp_path):
    mc = _sized(tmp_path, "pdf")
    assert mc.enabled_categories == frozenset({"document"})
    assert mc.max_upload_bytes == 100  # document, NOT the larger disabled video limit
    assert mc.max_content_length == 100 + 65_536


def test_size_uses_only_enabled_multi_extension_one_category(tmp_path):
    mc = _sized(tmp_path, "png,jpg,jpeg,gif,webp")
    assert mc.enabled_categories == frozenset({"image"})
    assert mc.max_upload_bytes == 200


def test_size_uses_max_over_enabled_categories(tmp_path):
    mc = _sized(tmp_path, "pdf,mp3")
    assert mc.enabled_categories == frozenset({"document", "audio"})
    assert mc.max_upload_bytes == 400  # max(document=100, audio=400)


def test_disabled_category_with_larger_limit_does_not_inflate(tmp_path):
    mc = _sized(tmp_path, "pdf,png")  # video disabled though its limit is 9999
    assert "video" not in mc.enabled_categories
    assert mc.max_upload_bytes == 200  # max(document=100, image=200)


def test_disabled_size_key_still_validated_positive(tmp_path):
    # video is disabled, but a garbage video limit still fails closed.
    with pytest.raises(MaterialConfigError):
        resolve_material_config(
            _base(tmp_path, MATERIAL_ALLOWED_EXTENSIONS="pdf", MATERIAL_MAX_VIDEO_BYTES="-1"),
            project_root=tmp_path,
        )


def test_root_inside_static_rejected(tmp_path):
    with pytest.raises(MaterialConfigError):
        resolve_material_config(
            _base(tmp_path, MATERIAL_STORAGE_ROOT="app/static/uploads"), project_root=tmp_path
        )


def test_root_is_static_itself_rejected(tmp_path):
    with pytest.raises(MaterialConfigError):
        resolve_material_config(
            _base(tmp_path, MATERIAL_STORAGE_ROOT="app/static"), project_root=tmp_path
        )


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_root_rejected(tmp_path, value):
    with pytest.raises(MaterialConfigError):
        resolve_material_config(
            _base(tmp_path, MATERIAL_STORAGE_ROOT=value), project_root=tmp_path
        )


def test_extension_outside_hard_set_rejected(tmp_path):
    with pytest.raises(MaterialConfigError):
        resolve_material_config(
            _base(tmp_path, MATERIAL_ALLOWED_EXTENSIONS="pdf,exe"), project_root=tmp_path
        )


@pytest.mark.parametrize("value", ["", "   ", ",,,"])
def test_empty_extension_list_rejected(tmp_path, value):
    with pytest.raises(MaterialConfigError):
        resolve_material_config(
            _base(tmp_path, MATERIAL_ALLOWED_EXTENSIONS=value), project_root=tmp_path
        )


def test_extensions_may_only_narrow_the_hard_set(tmp_path):
    mc = resolve_material_config(
        _base(tmp_path, MATERIAL_ALLOWED_EXTENSIONS="pdf"), project_root=tmp_path
    )
    assert mc.allowed_extensions == frozenset({"pdf"})
    assert mc.allowed_extensions.issubset(HARD_ALLOWED_EXTENSIONS)


@pytest.mark.parametrize(
    "key", ["MATERIAL_MAX_DOCUMENT_BYTES", "MATERIAL_MAX_IMAGE_BYTES",
            "MATERIAL_MAX_AUDIO_BYTES", "MATERIAL_MAX_VIDEO_BYTES"]
)
@pytest.mark.parametrize("bad", ["0", "-1", "abc", "", None])
def test_non_positive_size_rejected(tmp_path, key, bad):
    with pytest.raises(MaterialConfigError):
        resolve_material_config(_base(tmp_path, **{key: bad}), project_root=tmp_path)


def test_root_pointing_at_a_file_rejected(tmp_path):
    plain_file = tmp_path / "not_a_dir"
    plain_file.write_text("x")
    with pytest.raises(MaterialConfigError):
        resolve_material_config(
            _base(tmp_path, MATERIAL_STORAGE_ROOT=str(plain_file / "sub")),
            project_root=tmp_path,
        )


def test_resolution_does_not_create_the_directory(tmp_path):
    root = tmp_path / "materials"
    resolve_material_config(_base(tmp_path), project_root=tmp_path)
    assert not root.exists()


# ---------------------------------------------------------------------------
# create_app wiring -- fail closed at start-up
# ---------------------------------------------------------------------------


def test_create_app_fails_closed_on_bad_config():
    with pytest.raises(MaterialConfigError):
        create_app("testing", MATERIAL_ALLOWED_EXTENSIONS="pdf,exe")
    with pytest.raises(MaterialConfigError):
        create_app("testing", MATERIAL_MAX_VIDEO_BYTES="0")


def test_create_app_sets_max_content_length(tmp_path):
    app = create_app("testing", MATERIAL_STORAGE_ROOT=str(tmp_path / "m"))
    mc = app.extensions["material_config"]
    assert app.config["MAX_CONTENT_LENGTH"] == mc.max_content_length
    assert app.config["MAX_CONTENT_LENGTH"] > mc.max_upload_bytes
