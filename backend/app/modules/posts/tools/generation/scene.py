import hashlib
import io
import warnings
from enum import StrEnum

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.providers import ImageProvider, ImageRequest, StorageProvider

from .planner import GenerationKind
from .prompt_builder import ScenePrompt


class SceneGenerationStatus(StrEnum):
    GENERATED = "generated"
    SKIPPED = "skipped"


class SceneArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SceneGenerationStatus
    kind: GenerationKind | None
    storage_key: str | None = None
    mime_type: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    checksum: str | None = Field(default=None, min_length=64, max_length=64)
    provider: str | None = None
    model: str | None = None
    prompt_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    reason: str

    @model_validator(mode="after")
    def status_matches_metadata(self) -> "SceneArtifact":
        generated_fields = (
            self.kind,
            self.storage_key,
            self.mime_type,
            self.width,
            self.height,
            self.checksum,
            self.provider,
            self.model,
            self.prompt_fingerprint,
        )
        if self.status is SceneGenerationStatus.GENERATED and any(
            value is None for value in generated_fields
        ):
            raise ValueError("generated scene artifact requires complete provenance metadata")
        if self.status is SceneGenerationStatus.SKIPPED and any(
            value is not None for value in generated_fields
        ):
            raise ValueError("skipped scene artifact cannot claim generated metadata")
        return self


class SceneGenerator:
    def __init__(self, image: ImageProvider, storage: StorageProvider) -> None:
        self._image = image
        self._storage = storage

    async def generate(self, prompt: ScenePrompt, *, storage_key: str) -> SceneArtifact:
        if not await self._storage.is_available():
            raise RuntimeError("scene storage is unavailable")
        response = await self._image.generate(
            ImageRequest(
                prompt=prompt.positive_prompt,
                negative_prompt=prompt.negative_prompt,
                width=prompt.width,
                height=prompt.height,
            )
        )
        width, height = _validate_image(
            response.image,
            response.mime_type,
            expected=(prompt.width, prompt.height),
        )
        checksum = hashlib.sha256(response.image).hexdigest()
        await self._storage.put(
            key=storage_key,
            data=response.image,
            content_type=response.mime_type,
            metadata={
                "checksum": checksum,
                "prompt_fingerprint": prompt.prompt_fingerprint,
                "provider": response.provider,
                "model": response.model,
            },
        )
        return SceneArtifact(
            status=SceneGenerationStatus.GENERATED,
            kind=prompt.kind.value,
            storage_key=storage_key,
            mime_type=response.mime_type,
            width=width,
            height=height,
            checksum=checksum,
            provider=response.provider,
            model=response.model,
            prompt_fingerprint=prompt.prompt_fingerprint,
            reason="Scene plate generated and persisted for later composition.",
        )


def _validate_image(data: bytes, mime_type: str, *, expected: tuple[int, int]) -> tuple[int, int]:
    if not data or not mime_type.startswith("image/"):
        raise ValueError("image provider returned an invalid image payload")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual_mime = image.get_format_mimetype()
                image.load()
                size = image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning) as exc:
        raise ValueError("image provider returned unreadable or unsafe bytes") from exc
    if actual_mime != mime_type:
        raise ValueError("image provider MIME type disagrees with decoded bytes")
    if size != expected:
        raise ValueError(
            f"image provider returned {size[0]}x{size[1]}, expected {expected[0]}x{expected[1]}"
        )
    return size


__all__ = ["SceneArtifact", "SceneGenerationStatus", "SceneGenerator"]
