from typing import Any

import httpx

from app.integrations.http import ProviderHTTPAdapter
from app.modules.posts.providers import (
    ProviderResponseError,
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
        body = await self._http.post_json(
            provider=self.provider_name,
            url=f"{self._base_url}/search",
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload={
                "query": request.query,
                "search_depth": request.search_depth,
                "max_results": request.max_results,
                "include_answer": True,
                "include_raw_content": False,
                "include_images": False,
                "include_domains": list(request.include_domains),
                "exclude_domains": list(request.exclude_domains),
            },
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
        return ResearchResult(
            title=title,
            url=url,
            content=content,
            score=float(score) if isinstance(score, int | float) else None,
        )


__all__ = ["TavilyResearchProvider"]
