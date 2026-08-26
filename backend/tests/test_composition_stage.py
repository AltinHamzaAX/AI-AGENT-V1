import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from test_design_spec import _input as _design_input
from test_design_spec import _spec_payload
from test_deterministic_composer import _png
from test_generation_planner import _asset

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockLLMProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.asset_intelligence import AssetPolicy, IntelligentAssetRole
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import SupervisorAction
from app.modules.posts.orchestration import (
    CompositionStageHandler,
    WorkflowCompositionResolver,
)
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import ProviderBundle, StorageObjectNotFoundError
from app.modules.posts.tools.composition import (
    ComponentKind,
    CompositionError,
    CompositionFailure,
    PostDraft,
)
from app.modules.posts.tools.generation import (
    GenerationKind,
    SceneArtifact,
    SceneGenerationStatus,
)
from app.shared.assets.domain import Asset, AssetRole
from app.shared.conversations.domain import ConversationScope

SCOPE = ConversationScope(user_id=uuid4(), project_id=uuid4())
_ASSET_ROLES = {
    IntelligentAssetRole.PRIMARY_PRODUCT: AssetRole.PRODUCT,
    IntelligentAssetRole.BRAND_LOGO: AssetRole.LOGO,
    IntelligentAssetRole.ENVIRONMENT: AssetRole.ENVIRONMENT,
}


class _FakeAssetRepository:
    def __init__(self, assets: dict[UUID, Asset]) -> None:
        self.assets = assets
        self.scopes: list[ConversationScope] = []

    async def get(self, *, asset_id: UUID, scope: ConversationScope) -> Asset | None:
        self.scopes.append(scope)
        asset = self.assets.get(asset_id)
        return asset if asset is not None and asset.scope == scope else None


class _UnavailableStorage(MockStorageProvider):
    async def is_available(self) -> bool:
        return False


class _Fixture:
    def __init__(
        self,
        *,
        state: dict,
        assets: _FakeAssetRepository,
        storage: MockStorageProvider,
        policies: dict[IntelligentAssetRole, AssetPolicy],
        scene_key: str,
    ) -> None:
        self.state = state
        self.assets = assets
        self.storage = storage
        self.policies = policies
        self.scene_key = scene_key

    def handler(self, storage: MockStorageProvider | None = None) -> CompositionStageHandler:
        storage = storage or self.storage
        return CompositionStageHandler(
            WorkflowCompositionResolver(assets=self.assets, storage=storage, scope=SCOPE),
            _providers(storage),
        )


def _providers(storage: MockStorageProvider) -> ProviderBundle:
    return ProviderBundle(
        llm=MockLLMProvider(),
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=storage,
    )


def _context(state: dict) -> SupervisorStageContext:
    return SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )


async def _fixture(
    *, scene_generated: bool = True, extra_roles: tuple[IntelligentAssetRole, ...] = ()
) -> _Fixture:
    design_input = await _design_input()
    fingerprint = design_input.copy_draft.contract_fingerprint
    spec = DesignSpec(**_spec_payload(), contract_fingerprint=fingerprint)
    storage = MockStorageProvider()
    rows: dict[UUID, Asset] = {}
    policies: dict[IntelligentAssetRole, AssetPolicy] = {}
    sources = {
        IntelligentAssetRole.PRIMARY_PRODUCT: ((600, 360), (210, 80, 25, 255)),
        IntelligentAssetRole.BRAND_LOGO: ((360, 100), (20, 20, 20, 255)),
        **{role: ((1080, 1080), (30, 55, 90, 255)) for role in extra_roles},
    }
    for role, (size, color) in sources.items():
        policy = _asset(role, fingerprint)
        policies[role] = policy
        data = _png(size, color, label=role.value)
        key = f"assets/{policy.asset_id}.png"
        await storage.put(key=key, data=data, content_type="image/png")
        rows[policy.asset_id] = Asset(
            id=policy.asset_id,
            scope=SCOPE,
            message_id=uuid4(),
            role=_ASSET_ROLES[role],
            original_filename=policy.original_filename,
            mime_type="image/png",
            width=size[0],
            height=size[1],
            size_bytes=len(data),
            storage_key=key,
            checksum=hashlib.sha256(data).hexdigest(),
            created_at=datetime.now(UTC),
        )

    scene_key = "posts/scene/plate.png"
    if scene_generated:
        scene_bytes = _png((1080, 1080), (18, 42, 74, 255))
        await storage.put(key=scene_key, data=scene_bytes, content_type="image/png")
        artifact = SceneArtifact(
            status=SceneGenerationStatus.GENERATED,
            kind=GenerationKind.SCENE,
            storage_key=scene_key,
            mime_type="image/png",
            width=1080,
            height=1080,
            checksum=hashlib.sha256(scene_bytes).hexdigest(),
            provider="test-image",
            model="scene-test",
            prompt_fingerprint="a" * 64,
            reason="generated for the composition stage test",
        )
    else:
        artifact = SceneArtifact(
            status=SceneGenerationStatus.SKIPPED,
            kind=None,
            reason="Approved assets already provide the scene; image generation skipped.",
        )

    state = {
        PostWorkflowSection.COPY.value: design_input.copy_draft.model_dump(mode="json"),
        PostWorkflowSection.DESIGN_SPEC.value: spec.model_dump(mode="json"),
        PostWorkflowSection.ASSETS.value: [
            policy.model_dump(mode="json") for policy in policies.values()
        ],
        PostWorkflowSection.GENERATION_ARTIFACTS.value: [artifact.model_dump(mode="json")],
    }
    return _Fixture(
        state=state,
        assets=_FakeAssetRepository(rows),
        storage=storage,
        policies=policies,
        scene_key=scene_key,
    )


@pytest.mark.asyncio
async def test_stage_composes_from_workflow_state_and_persists_three_renders() -> None:
    fixture = await _fixture()
    context = _context(fixture.state)

    result = await fixture.handler().execute(context)

    draft = PostDraft.model_validate(result.outputs[PostWorkflowSection.POST_DRAFT])
    prefix = (
        f"posts/{context.post_id}/generations/{context.generation_id}/"
        f"composition/{draft.render_fingerprint}"
    )
    assert draft.working_render.storage_key == f"{prefix}/working.png"
    assert draft.preview.storage_key == f"{prefix}/preview.png"
    assert draft.final_asset.storage_key == f"{prefix}/final.png"
    assert (draft.working_render.width, draft.working_render.height) == (1080, 1080)
    assert (draft.preview.width, draft.preview.height) == (720, 720)
    assert (draft.final_asset.width, draft.final_asset.height) == (2160, 2160)
    for render in (draft.working_render, draft.preview, draft.final_asset):
        stored = fixture.storage.objects[render.storage_key]
        assert hashlib.sha256(stored[0]).hexdigest() == render.checksum
        assert stored[1] == "image/png"
        assert stored[2]["checksum"] == render.checksum
    kinds = {component.kind for component in draft.components}
    assert {ComponentKind.SCENE, ComponentKind.PRODUCT, ComponentKind.LOGO}.issubset(kinds)


@pytest.mark.asyncio
async def test_originals_keep_their_library_identity_through_the_stage() -> None:
    fixture = await _fixture()

    result = await fixture.handler().execute(_context(fixture.state))

    draft = PostDraft.model_validate(result.outputs[PostWorkflowSection.POST_DRAFT])
    product = next(item for item in draft.components if item.kind is ComponentKind.PRODUCT)
    logo = next(item for item in draft.components if item.kind is ComponentKind.LOGO)
    rows = fixture.assets.assets
    product_row = rows[fixture.policies[IntelligentAssetRole.PRIMARY_PRODUCT].asset_id]
    logo_row = rows[fixture.policies[IntelligentAssetRole.BRAND_LOGO].asset_id]
    assert product.source_checksum == product_row.checksum
    assert logo.source_checksum == logo_row.checksum
    assert product.identity_preserved is True
    assert logo.identity_preserved is True
    assert set(fixture.assets.scopes) == {SCOPE}


@pytest.mark.asyncio
async def test_skipped_generation_falls_back_to_the_approved_environment_asset() -> None:
    fixture = await _fixture(
        scene_generated=False, extra_roles=(IntelligentAssetRole.ENVIRONMENT,)
    )

    result = await fixture.handler().execute(_context(fixture.state))

    draft = PostDraft.model_validate(result.outputs[PostWorkflowSection.POST_DRAFT])
    scene = next(item for item in draft.components if item.kind is ComponentKind.SCENE)
    environment = fixture.policies[IntelligentAssetRole.ENVIRONMENT]
    assert scene.source_asset_id == environment.asset_id
    assert fixture.scene_key not in fixture.storage.objects


@pytest.mark.asyncio
async def test_stage_is_idempotent_for_the_same_generation() -> None:
    fixture = await _fixture()
    context = _context(fixture.state)
    handler = fixture.handler()

    first = await handler.execute(context)
    second = await handler.execute(context)

    assert first.outputs == second.outputs
    renders = [key for key in fixture.storage.objects if "/composition/" in key]
    assert len(renders) == 3


@pytest.mark.asyncio
async def test_missing_required_asset_row_fails_before_rendering() -> None:
    fixture = await _fixture()
    fixture.assets.assets.pop(fixture.policies[IntelligentAssetRole.BRAND_LOGO].asset_id)

    with pytest.raises(CompositionError) as failure:
        await fixture.handler().execute(_context(fixture.state))

    assert failure.value.failure is CompositionFailure.MISSING_REQUIRED_ASSET


@pytest.mark.asyncio
async def test_asset_row_from_another_tenant_is_not_readable() -> None:
    fixture = await _fixture()
    policy = fixture.policies[IntelligentAssetRole.PRIMARY_PRODUCT]
    foreign = ConversationScope(user_id=uuid4(), project_id=uuid4())
    fixture.assets.assets[policy.asset_id] = replace(
        fixture.assets.assets[policy.asset_id], scope=foreign
    )

    with pytest.raises(CompositionError) as failure:
        await fixture.handler().execute(_context(fixture.state))

    assert failure.value.failure is CompositionFailure.MISSING_REQUIRED_ASSET


@pytest.mark.asyncio
async def test_missing_stored_object_is_not_swallowed() -> None:
    fixture = await _fixture()
    policy = fixture.policies[IntelligentAssetRole.PRIMARY_PRODUCT]
    del fixture.storage.objects[fixture.assets.assets[policy.asset_id].storage_key]

    with pytest.raises(StorageObjectNotFoundError):
        await fixture.handler().execute(_context(fixture.state))


@pytest.mark.asyncio
async def test_stored_bytes_that_disagree_with_the_row_are_rejected() -> None:
    fixture = await _fixture()
    policy = fixture.policies[IntelligentAssetRole.PRIMARY_PRODUCT]
    key = fixture.assets.assets[policy.asset_id].storage_key
    await fixture.storage.put(
        key=key,
        data=_png((600, 360), (10, 10, 10, 255), label="TAMPERED"),
        content_type="image/png",
    )

    with pytest.raises(CompositionError) as failure:
        await fixture.handler().execute(_context(fixture.state))

    assert failure.value.failure is CompositionFailure.CHECKSUM_MISMATCH


@pytest.mark.asyncio
async def test_unavailable_storage_stops_the_stage_before_writing() -> None:
    fixture = await _fixture()
    storage = _UnavailableStorage()
    storage.objects = fixture.storage.objects

    with pytest.raises(RuntimeError, match="composition storage is unavailable"):
        await fixture.handler(storage).execute(_context(fixture.state))

    assert not any("/composition/" in key for key in storage.objects)
