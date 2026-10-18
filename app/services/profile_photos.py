"""Private profile images projected from append-only account photo revisions.

Use the existing UploadedFile and AccountRevision tables: a committed photo
revision records its file's public identifier, not a path or browser metadata.
Old photos remain private history; only the current account can read its latest
photo. Storage has the same pre-commit crash window as other project uploads.
"""
from io import BytesIO
import json
import math
import warnings

from flask import g, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

from app.extensions import db
from app.models import AccountRevision, UploadedFile
from app.services.file_storage import StorageContainmentError, open_stored_file, stream_upload_to_storage
from app.services.file_validation import FileValidationError, validate_signature
from app.services.material_config import current_material_config

PROFILE_PHOTO_MAX_BYTES = 5 * 1024 * 1024
PROFILE_PHOTO_MAX_PIXELS = 16_000_000
PROFILE_PHOTO_SIZE = 512
PHOTO_ACTION = "profile_photo"
PHOTO_FILE_KEY = "profile_photo_file_public_id"


def _photo_crop_box(image, crop):
    """Validate a square in EXIF-oriented pixels; missing legacy input stays centred."""
    if crop is None or crop == "":
        return None
    invalid = "Choose a valid photo crop and try again."
    try:
        if not isinstance(crop, str) or len(crop) > 512 or not crop.lstrip().startswith("{"):
            raise ValueError
        # Duplicate keys are ambiguous and are not a valid crop instruction.
        pairs = json.loads(crop, object_pairs_hook=list)
        if not isinstance(pairs, list) or len(pairs) != 3:
            raise ValueError
        values = dict(pairs)
        if set(values) != {"x", "y", "size"}:
            raise ValueError
        if any(type(value) not in (int, float) for value in values.values()):
            raise ValueError
        x, y, size = (float(values[key]) for key in ("x", "y", "size"))
        if not all(math.isfinite(value) for value in (x, y, size)) \
                or not 0 <= x <= 1 or not 0 <= y <= 1 or not .25 <= size <= 1:
            raise ValueError
        side = min(image.size) * size
        left, top = x * image.width, y * image.height
        right, bottom = left + side, top + side
        if right > image.width + 1e-6 or bottom > image.height + 1e-6:
            raise ValueError
        return left, top, min(right, image.width), min(bottom, image.height)
    except (TypeError, ValueError, OverflowError):
        raise FileValidationError(invalid) from None


def normalized_photo(file_storage, *, crop=None):
    """Bounded real decoding, EXIF orientation and metadata-free PNG output."""
    source = file_storage.stream.read(PROFILE_PHOTO_MAX_BYTES + 1)
    if len(source) > PROFILE_PHOTO_MAX_BYTES:
        raise FileValidationError("Choose a photo no larger than 5 MB.")
    if not source:
        raise FileValidationError("The photo is empty. Choose another image.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(source), formats=("JPEG", "PNG", "WEBP")) as image:
                if image.width * image.height > PROFILE_PHOTO_MAX_PIXELS:
                    raise FileValidationError("Choose a photo with no more than 16 million pixels.")
                if getattr(image, "n_frames", 1) != 1:
                    raise FileValidationError("Choose a still photo rather than an animated image.")
                image.load()
                oriented = ImageOps.exif_transpose(image)
                box = _photo_crop_box(oriented, crop)
                cropped = (oriented.resize((PROFILE_PHOTO_SIZE, PROFILE_PHOTO_SIZE),
                                          resample=Image.Resampling.LANCZOS, box=box)
                           if box else ImageOps.fit(oriented, (PROFILE_PHOTO_SIZE, PROFILE_PHOTO_SIZE),
                                                    method=Image.Resampling.LANCZOS)).convert("RGBA")
                # A new canvas drops EXIF, ICC, text and any original container data.
                with Image.new("RGBA", cropped.size) as clean:
                    clean.paste(cropped)
                    result = BytesIO()
                    clean.save(result, format="PNG")
    except FileValidationError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise FileValidationError("Choose a photo with no more than 16 million pixels.") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise FileValidationError("Choose a valid JPG, PNG or WebP photo.") from None
    result.seek(0)
    return result


def store_profile_photo(config, file_storage, *, crop=None):
    """Decode and store before any write locks; filename/MIME are never trusted."""
    with normalized_photo(file_storage, crop=crop) as image:
        return stream_upload_to_storage(config, image, original_filename="profile-photo.png",
            extension="png", category="image", content_type="image/png",
            max_bytes=PROFILE_PHOTO_MAX_BYTES,
            validate_header=lambda header: validate_signature("png", header))


def latest_photo_reference(user_id):
    row = db.session.query(AccountRevision.after_snapshot).filter(
        AccountRevision.user_id == user_id, AccountRevision.actor_id == user_id,
        AccountRevision.action == PHOTO_ACTION,
    ).order_by(AccountRevision.version.desc()).limit(1).first()
    value = row[0].get(PHOTO_FILE_KEY) if row and isinstance(row[0], dict) else None
    return value if isinstance(value, str) and len(value) == 36 else None


def current_profile_photo(user_id):
    reference = latest_photo_reference(user_id)
    if reference is None:
        return None
    return UploadedFile.query.filter_by(public_id=reference, uploaded_by_id=user_id,
        category="image", extension="png", content_type="image/png").first()


def account_photo_url(user):
    """Lazy request memoization; no query on login/error pages or other users."""
    if not user.is_authenticated:
        return None
    if not hasattr(g, "account_photo_urls"):
        g.account_photo_urls = {}
    if user.id not in g.account_photo_urls:
        photo = current_profile_photo(user.id)
        if photo:
            try:
                open_stored_file(current_material_config(), photo.storage_key)
            except (FileNotFoundError, StorageContainmentError):
                photo = None
        g.account_photo_urls[user.id] = url_for("account.photo", v=photo.public_id) if photo else None
    return g.account_photo_urls[user.id]
