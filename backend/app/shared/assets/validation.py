import hashlib
import warnings
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from app.shared.assets.domain import AssetValidationError, ValidatedAssetUpload

FORMAT_DETAILS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
MIME_ALIASES = {"image/jpg": "image/jpeg"}


def validate_image_upload(
    *,
    data: bytes,
    original_filename: str,
    declared_mime_type: str | None,
    max_size_bytes: int,
    max_dimension: int,
    max_pixels: int,
) -> ValidatedAssetUpload:
    filename = _safe_filename(original_filename)
    if not data:
        raise AssetValidationError("The uploaded file is empty", code="empty_file")
    if len(data) > max_size_bytes:
        raise AssetValidationError(
            f"The uploaded file exceeds the {max_size_bytes}-byte limit",
            code="file_too_large",
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                width, height = image.size
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AssetValidationError(
            "Image dimensions are unsafe", code="unsafe_dimensions"
        ) from None
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise AssetValidationError("The uploaded image is corrupted or unsupported") from None

    if image_format not in FORMAT_DETAILS:
        raise AssetValidationError(
            "Only JPEG, PNG, and WebP images are supported",
            code="unsupported_mime_type",
        )
    detected_mime_type, extension = FORMAT_DETAILS[image_format]
    declared = (declared_mime_type or "").split(";", 1)[0].strip().lower()
    declared = MIME_ALIASES.get(declared, declared)
    if declared != detected_mime_type:
        raise AssetValidationError(
            "Declared MIME type does not match the uploaded image",
            code="mime_type_mismatch",
        )
    if width <= 0 or height <= 0:
        raise AssetValidationError("Image dimensions must be positive")
    if width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise AssetValidationError(
            "Image dimensions exceed the configured safety limits",
            code="unsafe_dimensions",
        )

    return ValidatedAssetUpload(
        original_filename=filename,
        mime_type=detected_mime_type,
        width=width,
        height=height,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        extension=extension,
        metadata={"detected_format": image_format.lower()},
    )


def _safe_filename(value: str) -> str:
    filename = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or any(ord(character) < 32 for character in filename):
        raise AssetValidationError("A valid original filename is required")
    if len(filename) > 255:
        raise AssetValidationError("The original filename exceeds 255 characters")
    return filename
