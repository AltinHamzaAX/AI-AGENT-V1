import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.infrastructure.cache.research import RedisResearchCache
from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration import ExternalResearchStageHandler
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ProviderBundle,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
)
from app.modules.posts.tools.research import (
    ExternalResearchInput,
    ExternalResearchService,
    InMemoryResearchCache,
    MarketResearchTool,
    ResearchCategory,
    ResearchFinding,
    ResearchStatus,
    default_research_tools,
)


class _ResearchProvider:
    def __init__(self, *, empty: bool = False, delay: float = 0) -> None:
        self.empty = empty
        self.delay = delay
        self.requests: list[ResearchRequest] = []
        self.active = 0
        self.max_active = 0

    async def search(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            digest = sha256(request.query.encode()).hexdigest()[:12]
            results = ()
            if not self.empty:
                results = (
                    ResearchResult(
                        title=f"Primary source {digest}",
                        url=f"https://research.example/{digest}",
                        content=f"Source-aware evidence for {request.query}",
                        score=0.91,
                    ),
                    ResearchResult(
                        title="Duplicate URL",
                        url=f"https://research.example/{digest}",
                        content="This duplicate must be discarded.",
                        score=0.50,
                    ),
                )
            return ResearchResponse(
                results=results,
                provider="test-research",
                query=request.query,
                answer="Provider summary; evidence remains attached to sources.",
            )
        finally:
            self.active -= 1


class _StructuredResearchLLM:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        prompt = request.messages[0].content
        source = json.loads(request.messages[-1].content)["sources"][0]
        insight = {
            "observation": "Observed pattern from evidence.",
            "evidence": [{"source_id": "S1", "quote": source["excerpt"]}],
        }
        if "overused_patterns" in prompt:
            payload = {
                "messaging": [insight],
                "offers": [],
                "cta": [],
                "visual_language": [],
                "differentiation": [],
                "overused_patterns": [],
            }
        elif "platform_creative_patterns" in prompt:
            payload = {
                "platform_creative_patterns": [insight],
                "text_density": [],
                "cta": [],
                "logo_placement": [],
                "photography": [],
                "graphic_systems": [],
                "compositions": [],
            }
        else:
            payload = {
                "category": [insight],
                "market_expectations": [],
                "offers": [],
                "customer_expectations": [],
                "positioning_patterns": [],
                "opportunities": [],
            }
        return LLMResponse(
            text=json.dumps(payload),
            provider="test-llm",
            model="structured-research-test",
        )


class _BrokenCache:
    async def get(self, _key: str):
        raise ConnectionError("cache unavailable")

    async def set(self, _key: str, _value, *, ttl_seconds: int) -> None:
        raise ConnectionError(f"cache unavailable for {ttl_seconds}")


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.ttls[key] = ex


def _contract() -> PostSemanticContract:
    return PostSemanticContract.create(
        company="Promotiva Mobility",
        brand="Prishtina Drive",
        product="Airport car rental",
        primary_entity="Airport car rental",
        goal="Drive bookings",
        audience="Diaspora arriving in Kosovo",
        market="Kosovo",
        location="Prishtina airport",
        offer="From EUR 35/day",
        cta_intent="Book now",
        platform="Instagram",
        language="Albanian",
        required_facts={"pickup availability": "24/7 airport pickup"},
        forbidden_claims=["cheapest rental in Kosovo"],
        required_assets=[],
        constraints=["Do not replace the product or logo"],
    )


def _audience(contract: PostSemanticContract | None = None) -> AudienceIntelligence:
    source = contract or _contract()
    basis = ["semantic_contract.audience"]
    insight = {
        "insight": "Immediate access matters after arrival.",
        "basis": basis,
        "confidence": "medium",
    }
    return AudienceIntelligence.model_validate(
        {
            "segments": [
                {
                    "name": "Arrival convenience seekers",
                    "description": "Diaspora seeking immediate transport.",
                    "parent_audience": source.audience,
                    "basis": basis,
                    "confidence": "medium",
                }
            ],
            "target": {
                "segment": "Arrival convenience seekers",
                "rationale": "Directly connected to the declared arrival context.",
                "basis": basis,
                "confidence": "medium",
            },
            "needs": [insight],
            "desires": [insight],
            "pain_points": [insight],
            "objections": [insight],
            "motivation": [insight],
            "purchase_intent": {
                "level": "unknown",
                "rationale": "External evidence is required.",
                "basis": basis,
                "confidence": "low",
            },
            "trust_triggers": [insight],
            "context": {
                "declared_audience": source.audience,
                "market": source.market,
                "location": source.location,
                "platform": source.platform,
                "situations": [insight],
            },
            "customer_tension": {
                "current_state": "No transport immediately after arrival.",
                "desired_state": "Transport ready immediately.",
                "tension": "Avoid waiting after landing.",
                "basis": basis,
                "confidence": "medium",
            },
            "limitations": ["External research has not yet validated these hypotheses."],
            "contract_fingerprint": source.fingerprint,
        }
    )


def _payload() -> ExternalResearchInput:
    contract = _contract()
    return ExternalResearchInput(
        semantic_contract=contract.to_dict(),
        audience=_audience(contract),
    )


def _providers(research, llm=None) -> ProviderBundle:
    return ProviderBundle(
        llm=llm or _StructuredResearchLLM(),
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=research,
        storage=MockStorageProvider(),
        names={
            "llm": "mock",
            "vision": "mock",
            "image": "mock",
            "embedding": "mock",
            "research": "test-research",
            "storage": "mock",
        },
    )


def _context() -> SupervisorStageContext:
    contract = _contract()
    state = empty_workflow_state()
    state[PostWorkflowSection.SEMANTIC_CONTRACT.value] = contract.to_dict()
    state[PostWorkflowSection.AUDIENCE.value] = _audience(contract).model_dump(mode="json")
    return SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=5,
        action=SupervisorAction.CONTINUE,
    )


@pytest.mark.asyncio
async def test_external_research_runs_all_tools_concurrently_and_is_source_aware() -> None:
    provider = _ResearchProvider(delay=0.01)
    now = datetime(2026, 8, 25, 8, tzinfo=UTC)
    service = ExternalResearchService(
        default_research_tools(provider),
        cache=InMemoryResearchCache(clock=lambda: now),
        cache_ttl_seconds=600,
        max_concurrency=4,
        clock=lambda: now,
    )

    result = await service.run(_payload())

    assert len(provider.requests) == 24
    assert 2 <= provider.max_active <= 4
    assert result.contract_fingerprint == _contract().fingerprint
    assert result.researched_at == now
    reports = [getattr(result, category.value) for category in ResearchCategory]
    assert {report.category for report in reports} == set(ResearchCategory)
    assert all(report.status is ResearchStatus.SUCCEEDED for report in reports)
    assert all(report.researched_at == now for report in reports)
    assert all(report.expires_at == now + timedelta(seconds=600) for report in reports)
    assert all(report.cached is False for report in reports)
    assert len(result.market.sources) == 6
    assert len(result.competitor.sources) == 6
    assert len(result.social.sources) == 7
    assert all(
        len(getattr(result, category.value).sources) == 1
        for category in ResearchCategory
        if category
        not in {
            ResearchCategory.MARKET,
            ResearchCategory.COMPETITOR,
            ResearchCategory.SOCIAL,
        }
    )
    assert all(len(report.findings) == len(report.sources) for report in reports)
    assert all(
        str(report.findings[0].source_url) == str(report.sources[0].url) for report in reports
    )
    serialized = result.model_dump(mode="json")
    assert "marketing_strategy" not in serialized
    assert "positioning" not in serialized
    assert "copy" not in serialized
    assert "creative_concept" not in serialized


@pytest.mark.asyncio
async def test_second_run_is_fully_cached_and_expiry_researches_again() -> None:
    provider = _ResearchProvider()
    llm = _StructuredResearchLLM()
    current = [datetime(2026, 8, 25, 8, tzinfo=UTC)]
    cache = InMemoryResearchCache(clock=lambda: current[0])
    service = ExternalResearchService(
        default_research_tools(provider, llm),
        cache=cache,
        cache_ttl_seconds=60,
        clock=lambda: current[0],
    )

    first = await service.run(_payload())
    second = await service.run(_payload())
    assert len(provider.requests) == 24
    assert len(llm.requests) == 3
    assert all(getattr(second, category.value).cached for category in ResearchCategory)
    assert all(not getattr(first, category.value).cached for category in ResearchCategory)

    current[0] += timedelta(seconds=61)
    third = await service.run(_payload())
    assert len(provider.requests) == 48
    assert len(llm.requests) == 6
    assert all(not getattr(third, category.value).cached for category in ResearchCategory)


@pytest.mark.asyncio
async def test_no_results_remains_structured_and_low_confidence() -> None:
    provider = _ResearchProvider(empty=True)
    result = await ExternalResearchService.from_provider(provider).run(_payload())
    reports = [getattr(result, category.value) for category in ResearchCategory]
    assert all(report.status is ResearchStatus.NO_RESULTS for report in reports)
    assert all(report.confidence.value == "low" for report in reports)
    assert all(report.sources == [] and report.findings == [] for report in reports)


@pytest.mark.asyncio
async def test_cache_failure_does_not_discard_successful_research() -> None:
    provider = _ResearchProvider()
    result = await ExternalResearchService(
        default_research_tools(provider), cache=_BrokenCache()
    ).run(_payload())
    assert result.market.status is ResearchStatus.SUCCEEDED
    assert len(provider.requests) == 24


@pytest.mark.asyncio
async def test_contract_or_audience_drift_fails_before_provider() -> None:
    provider = _ResearchProvider()
    payload = _payload()
    payload.audience.contract_fingerprint = "0" * 64

    with pytest.raises(ValueError, match="fingerprint"):
        await ExternalResearchService.from_provider(provider).run(payload)

    assert provider.requests == []


@pytest.mark.asyncio
async def test_stage_writes_exactly_the_research_section() -> None:
    provider = _ResearchProvider()
    result = await ExternalResearchStageHandler(_providers(provider)).execute(_context())
    assert set(result.outputs) == {PostWorkflowSection.RESEARCH}
    value = result.outputs[PostWorkflowSection.RESEARCH]
    assert value["contract_fingerprint"] == _contract().fingerprint
    assert value["market"]["sources"][0]["url"].startswith("https://")
    assert value["market"]["analysis"]["category"]
    assert value["competitor"]["analysis"]["safe_use"] == "differentiate_do_not_copy"
    assert value["social"]["analysis"]["platform_creative_patterns"]


@pytest.mark.asyncio
async def test_stage_rejects_missing_audience_before_provider() -> None:
    provider = _ResearchProvider()
    context = _context()
    context.workflow_state[PostWorkflowSection.AUDIENCE.value] = {}
    with pytest.raises(ValueError):
        await ExternalResearchStageHandler(_providers(provider)).execute(context)
    assert provider.requests == []


@pytest.mark.asyncio
async def test_redis_cache_adapter_round_trips_typed_report() -> None:
    provider = _ResearchProvider()
    result = await ExternalResearchService.from_providers(
        provider,
        _StructuredResearchLLM(),
    ).run(_payload())
    redis = _FakeRedis()
    cache = RedisResearchCache(redis)  # type: ignore[arg-type]
    report = result.market

    await cache.set(report.cache_key, report, ttl_seconds=321)
    loaded = await cache.get(report.cache_key)

    assert loaded == report
    assert loaded is not None and loaded.analysis is not None
    redis_key = f"posts:research:v1:{report.cache_key}"
    assert redis.ttls[redis_key] == 321


def test_external_research_requires_exactly_one_tool_per_category() -> None:
    provider = _ResearchProvider()
    tools = default_research_tools(provider)
    with pytest.raises(ValueError, match="exactly one tool per category"):
        ExternalResearchService(tools[:-1])


@pytest.mark.asyncio
async def test_finding_must_reference_a_source_in_the_same_report() -> None:
    provider = _ResearchProvider()
    result = await ExternalResearchService.from_provider(provider).run(_payload())
    report = result.market.model_dump()
    report["findings"] = [
        ResearchFinding(
            statement="Unsupported source link",
            source_url="https://unrelated.example/evidence",
            confidence="high",
        ).model_dump()
    ]
    with pytest.raises(ValueError, match="must reference a report source"):
        type(result.market).model_validate(report)


def test_cache_variant_changes_with_tool_depth_or_result_count() -> None:
    provider = _ResearchProvider()
    standard = MarketResearchTool(provider, max_results=5, search_depth="advanced")
    smaller = MarketResearchTool(provider, max_results=3, search_depth="advanced")
    faster = MarketResearchTool(provider, max_results=5, search_depth="basic")
    assert len({standard.cache_variant, smaller.cache_variant, faster.cache_variant}) == 3


def test_supervisor_external_research_requires_contract_and_audience() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.EXTERNAL_RESEARCH)
    assert policy.required_sections == (
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.AUDIENCE,
    )


def test_research_settings_are_validated() -> None:
    base = {
        "postgres_password": "test",
        "database_url": "sqlite+aiosqlite://",
        "redis_url": "redis://localhost:6379/0",
        "storage_provider": "mock",
        "s3_endpoint": "http://localhost:9000",
        "s3_access_key": "test",
        "s3_secret_key": "test",
    }
    settings = Settings(
        _env_file=None,
        research_cache_ttl_seconds=90,
        research_max_concurrency=2,
        **base,
    )
    assert settings.research_cache_ttl_seconds == 90
    assert settings.research_max_concurrency == 2
    with pytest.raises(ValueError):
        Settings(_env_file=None, research_max_concurrency=9, **base)
