import asyncio
from io import BytesIO
from typing import Any

from huggingface_hub import InferenceClient

from app.modules.posts.providers import ImageRequest, ImageResponse, ProviderError


class HuggingFaceImageProvider:
    provider_name = "huggingface"

    def __init__(
        self,
        *,
        token: str,
        model: str,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._client = client or InferenceClient(provider="auto", api_key=token)

    async def generate(self, request: ImageRequest) -> ImageResponse:
        if not request.prompt.strip():
            raise ValueError("Image prompt cannot be empty")
        parameters = {
            key: value
            for key, value in {
                "negative_prompt": request.negative_prompt,
                "width": request.width,
                "height": request.height,
                "seed": request.seed,
            }.items()
            if value is not None
        }
        try:
            image = await asyncio.to_thread(
                self._client.text_to_image,
                request.prompt,
                model=self._model,
                **parameters,
            )
            buffer = BytesIO()
            image.save(buffer, format="PNG")
        except Exception as exc:  # noqa: BLE001 - provider SDK boundary
            raise ProviderError("huggingface image request failed") from exc
        data = buffer.getvalue()
        if not data:
            raise ProviderError("huggingface returned an empty image")
        return ImageResponse(
            image=data,
            mime_type="image/png",
            provider=self.provider_name,
            model=self._model,
        )


__all__ = ["HuggingFaceImageProvider"]
