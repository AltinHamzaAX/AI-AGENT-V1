from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.infrastructure.cache.redis import get_redis
from app.infrastructure.cache.research import RedisResearchCache
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.orchestration.external_research import ExternalResearchStageHandler
from app.modules.posts.providers import ProviderBundle


def create_external_research_stage_handler(
    providers: ProviderBundle,
    *,
    redis: Redis | None = None,
    settings: Settings | None = None,
    trace_recorder: ExecutionTraceRecorder | None = None,
) -> ExternalResearchStageHandler:
    configured = settings or get_settings()
    redis_client = redis if redis is not None else get_redis()
    return ExternalResearchStageHandler(
        providers,
        cache=RedisResearchCache(redis_client),
        cache_ttl_seconds=configured.research_cache_ttl_seconds,
        max_concurrency=configured.research_max_concurrency,
        search_timeout_seconds=configured.research_search_timeout_seconds,
        tool_timeout_seconds=configured.research_tool_timeout_seconds,
        stage_timeout_seconds=configured.research_stage_timeout_seconds,
        trace_recorder=trace_recorder,
    )


__all__ = ["create_external_research_stage_handler"]
