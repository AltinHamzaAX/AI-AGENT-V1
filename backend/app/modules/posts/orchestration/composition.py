from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.asset_intelligence import AssetPolicy, IntelligentAssetRole
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.providers import ProviderBundle, StorageProvider
from app.modules.posts.tools.composition import (
    ComposerInput,
    CompositionError,
    CompositionFailure,
    DeterministicComposer,
    FontLibrary,
    PostDraft,
    RenderedAsset,
    SourceVisual,
    StoredRender,
)
from app.modules.posts.tools.generation import SceneArtifact, SceneGenerationStatus
from app.shared.assets.contracts import AssetRepository
from app.shared.conversations.domain import ConversationScope

PRODUCT_ROLES = frozenset(
    {
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
)
SCENE_ASSET_ROLES = (
    IntelligentAssetRole.ENVIRONMENT,
    IntelligentAssetRole.BACKGROUND_REFERENCE,
)


class CompositionInputResolver(Protocol):
    """Resolve tenant-authorized scene and original asset bytes for one post."""

    async def resolve(self, context: SupervisorStageContext) -> ComposerInput: ...


class WorkflowCompositionResolver:
    """Load the approved originals a composition needs, bound to one tenant.

    Scope is a constructor argument rather than part of the stage context
    because the asset library is tenant-gated: a resolver instance belongs to
    the generation it was built for and must not be shared across tenants.
    """

    def __init__(
        self,
        *,
        assets: AssetRepository,
        storage: StorageProvider,
        scope: ConversationScope,
        final_scale: int = 2,
    ) -> None:
        self._assets = assets
        self._storage = storage
        self._scope = scope
        self._final_scale = final_scale

    async def resolve(self, context: SupervisorStageContext) -> ComposerInput:
        state = context.workflow_state
        policies = [
            AssetPolicy.model_validate(item)
            for item in _section(state, PostWorkflowSection.ASSETS)
        ]
        policies.sort(key=lambda policy: policy.asset_id)
        logo = next(
            (policy for policy in policies if policy.role is IntelligentAssetRole.BRAND_LOGO),
            None,
        )
        products = [
            visual
            for policy in policies
            if policy.role in PRODUCT_ROLES
            for visual in [await self._original(policy)]
            if visual is not None
        ]
        return ComposerInput(
            scene=await self._scene(state, policies),
            products=products,
            logo=await self._original(logo) if logo is not None else None,
            copy_draft=CopyDraft.model_validate(state.get(PostWorkflowSection.COPY.value)),
            design_spec=DesignSpec.model_validate(
                state.get(PostWorkflowSection.DESIGN_SPEC.value)
            ),
            asset_policies=policies,
            final_scale=self._final_scale,
        )

    async def _scene(
        self, state: dict[str, Any], policies: list[AssetPolicy]
    ) -> SourceVisual | None:
        generated = [
            artifact
            for item in _section(state, PostWorkflowSection.GENERATION_ARTIFACTS)
            for artifact in [SceneArtifact.model_validate(item)]
            if artifact.status is SceneGenerationStatus.GENERATED
        ]
        if generated:
            artifact = generated[-1]
            if artifact.storage_key is None or artifact.mime_type is None:
                raise ValueError("a generated scene artifact must carry its storage metadata")
            return SourceVisual(
                # The plate has no asset row, so its identity is its object key.
                asset_id=uuid5(NAMESPACE_URL, artifact.storage_key),
                role=IntelligentAssetRole.ENVIRONMENT,
                image_bytes=await self._storage.get(key=artifact.storage_key),
                mime_type=artifact.mime_type,
                source_checksum=artifact.checksum,
            )
        # Generation was skipped, so an approved environment asset is the scene.
        for role in SCENE_ASSET_ROLES:
            policy = next((item for item in policies if item.role is role), None)
            if policy is not None:
                return await self._original(policy)
        return None

    async def _original(self, policy: AssetPolicy) -> SourceVisual | None:
        asset = await self._assets.get(asset_id=policy.asset_id, scope=self._scope)
        if asset is None:
            if policy.required:
                raise CompositionError(
                    CompositionFailure.MISSING_REQUIRED_ASSET,
                    f"required {policy.role.value} asset {policy.asset_id} is not in the library",
                )
            return None
        return SourceVisual(
            asset_id=policy.asset_id,
            role=policy.role,
            # A stored object that disagrees with its row is a hard failure, so
            # a missing object is deliberately left to propagate.
            image_bytes=await self._storage.get(key=asset.storage_key),
            mime_type=asset.mime_type,
            source_checksum=asset.checksum,
        )


class CompositionStageHandler:
    def __init__(
        self,
        resolver: CompositionInputResolver,
        providers: ProviderBundle,
        *,
        fonts: FontLibrary | None = None,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._resolver = resolver
        self._providers = providers
        self._composer = DeterministicComposer(fonts)
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        payload = await self._resolver.resolve(context)
        result = self._composer.compose(payload)
        providers = self._providers
        if self._trace_recorder is not None:
            providers = trace_provider_bundle(
                providers,
                recorder=self._trace_recorder,
                invocation=InvocationContext(
                    correlation_id=context.job_id,
                    post_id=context.post_id,
                    generation_id=context.generation_id,
                ),
            )
        if not await providers.storage.is_available():
            raise RuntimeError("composition storage is unavailable")
        prefix = (
            f"posts/{context.post_id}/generations/{context.generation_id}/"
            f"composition/{result.render_fingerprint}"
        )
        working = await _store(
            providers.storage, result.working_render, key=f"{prefix}/working.png",
            kind="working",
        )
        preview = await _store(
            providers.storage, result.preview, key=f"{prefix}/preview.png", kind="preview",
        )
        final = await _store(
            providers.storage, result.final_asset, key=f"{prefix}/final.png", kind="final",
        )
        draft = PostDraft(
            working_render=working,
            preview=preview,
            final_asset=final,
            components=result.components,
            contract_fingerprint=result.contract_fingerprint,
            render_fingerprint=result.render_fingerprint,
        )
        return SupervisorStageResult(
            outputs={PostWorkflowSection.POST_DRAFT: draft.model_dump(mode="json")}
        )


async def _store(storage, asset: RenderedAsset, *, key: str, kind: str) -> StoredRender:
    await storage.put(
        key=key,
        data=asset.image_bytes,
        content_type=asset.mime_type,
        metadata={"checksum": asset.checksum, "render_kind": kind},
    )
    return StoredRender(
        storage_key=key,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        checksum=asset.checksum,
    )


def _section(state: dict[str, Any], section: PostWorkflowSection) -> list[Any]:
    value = state.get(section.value)
    if not isinstance(value, list):
        raise ValueError(f"{section.value} must be an array")
    return value


__all__ = [
    "CompositionInputResolver",
    "CompositionStageHandler",
    "WorkflowCompositionResolver",
]
