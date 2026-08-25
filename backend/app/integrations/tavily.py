from datetime import datetime
from typing import Any

import httpx

from app.integrations.http import ProviderHTTPAdapter
from app.modules.posts.providers import (
    ProviderResponseError,
    ResearchImage,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
)


class TavilyResearchProvider:
    provider_name = "tavily"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.tavily.com",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = ProviderHTTPAdapter(client=client, timeout_seconds=timeout_seconds)

    async def search(self, request: ResearchRequest) -> ResearchResponse:
        if not request.query.strip():
            raise ValueError("Research query cannot be empty")
        if not 1 <= request.max_results <= 20:
            raise ValueError("Research max_results must be between 1 and 20")
        if request.search_depth not in {"basic", "advanced", "fast", "ultra-fast"}:
            raise ValueError("Unsupported research search_depth")
        if request.topic not in {"general", "news", "finance"}:
            raise ValueError("Unsupported research topic")
        if request.time_range not in {None, "day", "week", "month", "year"}:
            raise ValueError("Unsupported research time_range")
        payload: dict[str, Any] = {
            "query": request.query,
            "search_depth": request.search_depth,
            "max_results": request.max_results,
            "include_answer": True,
            # Markdown keeps headings and lists intact, which makes the body
            # readable as evidence instead of a wall of stripped text.
            "include_raw_content": "markdown" if request.include_raw_content else False,
            "include_images": request.include_images,
            # Descriptions are what make an image usable as a reference rather
            # than an opaque URL, so they travel with the images or not at all.
            "include_image_descriptions": request.include_images,
            "include_domains": list(request.include_domains),
            "exclude_domains": list(request.exclude_domains),
            "topic": request.topic,
        }
        if request.time_range is not None:
            payload["time_range"] = request.time_range
        if request.country is not None:
            payload["country"] = request.country
        body = await self._http.post_json(
            provider=self.provider_name,
            url=f"{self._base_url}/search",
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload=payload,
        )
        raw_results = body.get("results")
        if not isinstance(raw_results, list):
            raise ProviderResponseError("tavily returned an invalid results response")
        results = tuple(self._result(item) for item in raw_results)
        answer = body.get("answer")
        return ResearchResponse(
            results=results,
            provider=self.provider_name,
            query=str(body.get("query") or request.query),
            answer=answer if isinstance(answer, str) else None,
            images=_images(body.get("images")),
        )

    @staticmethod
    def _result(item: Any) -> ResearchResult:
        if not isinstance(item, dict):
            raise ProviderResponseError("tavily returned an invalid result item")
        title = item.get("title")
        url = item.get("url")
        content = item.get("content")
        if not all(isinstance(value, str) for value in (title, url, content)):
            raise ProviderResponseError("tavily returned an incomplete result item")
        score = item.get("score")
        published_at = _parse_published_at(item.get("published_date"))
        raw_content = item.get("raw_content")
        return ResearchResult(
            title=title,
            url=url,
            content=content,
            score=float(score) if isinstance(score, int | float) else None,
            published_at=published_at,
            raw_content=(
                raw_content.strip()
                if isinstance(raw_content, str) and raw_content.strip()
                else None
            ),
        )


def _images(value: Any) -> tuple[ResearchImage, ...]:
    """Parse the images block, tolerating both documented shapes.

    Tavily returns objects with a url and an optional description, but has
    historically returned bare URL strings; unusable entries are skipped rather
    than failing a search that already produced text evidence.
    """
    if not isinstance(value, list):
        return ()
    images: list[ResearchImage] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            url, description = item.strip(), None
        elif isinstance(item, dict):
            raw_url = item.get("url")
            raw_description = item.get("description")
            if not isinstance(raw_url, str):
                continue
            url = raw_url.strip()
            description = raw_description.strip() if isinstance(raw_description, str) else None
        else:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        images.append(ResearchImage(url=url, description=description or None))
    return tuple(images)


def _parse_published_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = ["TavilyResearchProvider"]
