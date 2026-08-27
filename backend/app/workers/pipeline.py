"""Composition root for the durable generation pipeline.

The Supervisor, its stage handlers and the durable job queue are each owned by
their own module; this is the one place that knows how to assemble them into
the executor a worker process runs.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.infrastructure.database.repositories.assets import SQLAlchemyAssetRepository
from app.infrastructure.database.repositories.supervisor import (
    SQLAlchemySupervisorCheckpointStore,
)
from app.models.posts import PostModel
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.orchestration import (
    ArtDirectionStageHandler,
    AssetIntelligenceStageHandler,
    AudienceIntelligenceStageHandler,
    BrandProductStageHandler,
    ClientUnderstandingStageHandler,
    CompositionStageHandler,
    CopywritingStageHandler,
    CreativeDirectionStageHandler,
    DesignCriticStageHandler,
    DesignSpecStageHandler,
    ExternalResearchStageHandler,
    GenerationPlanningStageHandler,
    MarketingCriticStageHandler,
    MarketingStrategyStageHandler,
    PostSupervisorExecutor,
    ProductionStageHandler,
    QualityScoringStageHandler,
    ScenePurityStageHandler,
    SemanticContractStageHandler,
    SupervisorStageContext,
    SupervisorStageHandler,
    VerificationStageHandler,
    VisionCriticStageHandler,
    WorkflowCompositionResolver,
)
from app.modules.posts.providers import ProviderBundle, StorageProvider
from app.modules.posts.tools.composition import ComposerInput
from app.shared.conversations.domain import ConversationScope


class GenerationCompositionResolver:
    """Bind the composition resolver to the tenant that owns the generation.

    The asset library is tenant-gated and a worker serves every tenant, so the
    scope is read from the generation being composed rather than fixed when the
    pipeline is assembled.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: StorageProvider,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage

    async def resolve(self, context: SupervisorStageContext) -> ComposerInput:
        async with self._session_factory() as session:
            scope = await _post_scope(session, post_id=context.post_id)
            if scope is None:
                raise LookupError(f"post '{context.post_id}' no longer exists")
            resolver = WorkflowCompositionResolver(
                assets=SQLAlchemyAssetRepository(session),
                storage=self._storage,
                scope=scope,
            )
            return await resolver.resolve(context)


def build_stage_handlers(
    session_factory: async_sessionmaker[AsyncSession],
    providers: ProviderBundle,
    *,
    settings: Settings | None = None,
    trace_recorder: ExecutionTraceRecorder | None = None,
) -> dict[SupervisorStage, SupervisorStageHandler]:
    """Every specialist stage this deployment can actually run.

    A stage left out of this mapping is not silently skipped: the Supervisor
    stops with `stage_handler:<stage>` as the missing input, which is how an
    unfinished pipeline reports itself instead of producing a partial post.
    """
    configured = settings or get_settings()
    return {
        SupervisorStage.CLIENT_UNDERSTANDING: ClientUnderstandingStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.SEMANTIC_CONTRACT: SemanticContractStageHandler(),
        SupervisorStage.ASSET_INTELLIGENCE: AssetIntelligenceStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.BRAND_PRODUCT: BrandProductStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.AUDIENCE_INTELLIGENCE: AudienceIntelligenceStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.EXTERNAL_RESEARCH: ExternalResearchStageHandler(
            providers,
            cache_ttl_seconds=configured.research_cache_ttl_seconds,
            max_concurrency=configured.research_max_concurrency,
            search_timeout_seconds=configured.research_search_timeout_seconds,
            tool_timeout_seconds=configured.research_tool_timeout_seconds,
            stage_timeout_seconds=configured.research_stage_timeout_seconds,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.MARKETING_STRATEGY: MarketingStrategyStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.CREATIVE_CONCEPT: CreativeDirectionStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.COPYWRITING: CopywritingStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.ART_DIRECTION: ArtDirectionStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.DESIGN_SPEC: DesignSpecStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.GENERATION_PLANNING: GenerationPlanningStageHandler(),
        SupervisorStage.PRODUCTION: ProductionStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.SCENE_PURITY: ScenePurityStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.COMPOSITION: CompositionStageHandler(
            GenerationCompositionResolver(session_factory, providers.storage),
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.VERIFICATION: VerificationStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.QUALITY_REVIEW: MarketingCriticStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.DESIGN_REVIEW: DesignCriticStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.VISION_REVIEW: VisionCriticStageHandler(
            providers,
            trace_recorder=trace_recorder,
        ),
        SupervisorStage.QUALITY_SCORING: QualityScoringStageHandler(),
    }


def build_generation_executor(
    session_factory: async_sessionmaker[AsyncSession],
    providers: ProviderBundle,
    *,
    settings: Settings | None = None,
    trace_recorder: ExecutionTraceRecorder | None = None,
) -> PostSupervisorExecutor:
    return PostSupervisorExecutor(
        store=SQLAlchemySupervisorCheckpointStore(session_factory),
        handlers=build_stage_handlers(
            session_factory,
            providers,
            settings=settings,
            trace_recorder=trace_recorder,
        ),
        trace_recorder=trace_recorder,
    )


async def _post_scope(session: AsyncSession, *, post_id: UUID) -> ConversationScope | None:
    row = (
        await session.execute(
            select(PostModel.user_id, PostModel.project_id).where(PostModel.id == post_id)
        )
    ).one_or_none()
    return ConversationScope(user_id=row[0], project_id=row[1]) if row else None


__all__ = [
    "GenerationCompositionResolver",
    "build_generation_executor",
    "build_stage_handlers",
]
