import json
from uuid import uuid4

import pytest

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.asset_intelligence import (
    AssetIntelligenceInput,
    AssetPolicy,
    AssetPolicyHardFail,
    AssetUsageAssertion,
    IntelligentAssetRole,
    enforce_asset_usage,
    evaluate_asset_usage,
)
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.exceptions import InvocationFailedError
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import SupervisorAction
from app.modules.posts.orchestration import AssetIntelligenceStageHandler
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import LLMRequest, LLMResponse, ProviderBundle


class _SequenceLLM:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=response, provider="test", model="test")


def _providers(llm: _SequenceLLM) -> ProviderBundle:
    return ProviderBundle(
        llm=llm,
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        names={
            "llm": "test",
            "vision": "mock",
            "image": "mock",
            "embedding": "mock",
            "research": "mock",
            "storage": "mock",
        },
    )


def _contract(*, required_assets=None) -> PostSemanticContract:
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
        required_facts={"pickup": "24/7 airport pickup"},
        forbidden_claims=["cheapest rental in Kosovo"],
        required_assets=list(required_assets or []),
        constraints=["Do not replace the product or logo"],
    )


def _attachment(asset_id, role: str, filename: str) -> dict:
    return {
        "id": str(asset_id),
        "role": role,
        "original_filename": filename,
        "mime_type": "image/png",
        "width": 1200,
        "height": 800,
        "metadata": {"source": "client"},
    }


def _classification(asset_id, role: str, *, evidence=None, reason="Declared by user") -> dict:
    return {
        "asset_id": str(asset_id),
        "role": role,
        "user_intent_evidence": evidence,
        "reason": reason,
    }


def _response(*classifications: dict) -> str:
    return json.dumps({"classifications": list(classifications)})


def _context(*, attachments: list[dict], contract: PostSemanticContract, message: str):
    state = empty_workflow_state()
    state[PostWorkflowSection.SEMANTIC_CONTRACT.value] = contract.to_dict()
    state[PostWorkflowSection.CONVERSATION_CONTEXT.value] = {
        "conversation_history": [],
        "latest_message": message,
        "attachments": attachments,
        "project_context": {},
    }
    return SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=2,
        action=SupervisorAction.CONTINUE,
    )


@pytest.mark.asyncio
async def test_logo_and_product_receive_deterministic_identity_policies() -> None:
    logo_id = uuid4()
    product_id = uuid4()
    llm = _SequenceLLM(
        _response(
            _classification(logo_id, "brand_logo"),
            _classification(product_id, "primary_product"),
        )
    )
    handler = AssetIntelligenceStageHandler(_providers(llm))
    result = await handler.execute(
        _context(
            attachments=[
                _attachment(logo_id, "logo", "logo.png"),
                _attachment(product_id, "product", "coffee.png"),
            ],
            contract=_contract(required_assets=[product_id]),
            message="This is our logo and this is the product that must be used.",
        )
    )

    assets = result.outputs[PostWorkflowSection.ASSETS]
    assert [asset["role"] for asset in assets] == ["brand_logo", "primary_product"]
    assert all(asset["required"] for asset in assets)
    assert all(asset["preserve_identity"] for asset in assets)
    assert all(not asset["allow_replace"] for asset in assets)
    assert all(not asset["allow_generation"] for asset in assets)
    assert assets[0]["allow_crop"] is False
    assert assets[0]["contract_fingerprint"] == _contract(required_assets=[product_id]).fingerprint


@pytest.mark.asyncio
async def test_explicit_vehicle_intent_has_priority_and_becomes_primary_product() -> None:
    vehicle_id = uuid4()
    evidence = "Kjo është vetura që duhet të përdoret."
    llm = _SequenceLLM(
        _response(
            _classification(
                vehicle_id,
                "primary_product",
                evidence=evidence,
                reason="The client explicitly made this vehicle the promoted subject.",
            )
        )
    )
    result = await AssetIntelligenceStageHandler(_providers(llm)).execute(
        _context(
            attachments=[_attachment(vehicle_id, "vehicle", "car.png")],
            contract=_contract(),
            message=evidence,
        )
    )

    policy = result.outputs[PostWorkflowSection.ASSETS][0]
    assert policy["role"] == "primary_product"
    assert policy["required"] is True
    assert policy["preserve_identity"] is True
    assert policy["user_intent_evidence"] == evidence


@pytest.mark.asyncio
async def test_vehicle_override_requires_grounded_explicit_intent() -> None:
    vehicle_id = uuid4()
    invalid = _response(
        _classification(
            vehicle_id,
            "primary_product",
            evidence="This vehicle must be used",
        )
    )
    llm = _SequenceLLM(invalid, invalid)

    with pytest.raises(InvocationFailedError):
        await AssetIntelligenceStageHandler(_providers(llm)).execute(
            _context(
                attachments=[_attachment(vehicle_id, "vehicle", "car.png")],
                contract=_contract(),
                message="Here is a vehicle reference.",
            )
        )

    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_provider_cannot_downgrade_authoritative_logo_role() -> None:
    logo_id = uuid4()
    invalid = _response(_classification(logo_id, "style_reference"))
    llm = _SequenceLLM(invalid, invalid)

    with pytest.raises(InvocationFailedError):
        await AssetIntelligenceStageHandler(_providers(llm)).execute(
            _context(
                attachments=[_attachment(logo_id, "logo", "logo.png")],
                contract=_contract(),
                message="This is the logo.",
            )
        )


@pytest.mark.asyncio
async def test_every_attachment_must_be_classified_exactly_once() -> None:
    logo_id = uuid4()
    background_id = uuid4()
    invalid = _response(_classification(logo_id, "brand_logo"))
    llm = _SequenceLLM(invalid, invalid)

    with pytest.raises(InvocationFailedError):
        await AssetIntelligenceStageHandler(_providers(llm)).execute(
            _context(
                attachments=[
                    _attachment(logo_id, "logo", "logo.png"),
                    _attachment(background_id, "background", "city.png"),
                ],
                contract=_contract(),
                message="Use both attachments.",
            )
        )


@pytest.mark.asyncio
async def test_missing_required_asset_fails_before_provider_call() -> None:
    missing_id = uuid4()
    llm = _SequenceLLM()

    with pytest.raises(ValueError, match="required assets are absent"):
        await AssetIntelligenceStageHandler(_providers(llm)).execute(
            _context(
                attachments=[],
                contract=_contract(required_assets=[missing_id]),
                message="Create the post.",
            )
        )

    assert llm.requests == []


@pytest.mark.asyncio
async def test_no_attachments_returns_empty_policy_without_provider() -> None:
    llm = _SequenceLLM()
    result = await AssetIntelligenceStageHandler(_providers(llm)).execute(
        _context(attachments=[], contract=_contract(), message="Create the post.")
    )

    assert result.outputs == {PostWorkflowSection.ASSETS: []}
    assert llm.requests == []


def _policy(role: IntelligentAssetRole, **overrides) -> AssetPolicy:
    protected = role in {
        IntelligentAssetRole.BRAND_LOGO,
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
    values = {
        "asset_id": uuid4(),
        "original_filename": "asset.png",
        "role": role,
        "required": protected,
        "preserve_identity": protected,
        "allow_crop": role is not IntelligentAssetRole.BRAND_LOGO,
        "allow_replace": not protected,
        "allow_generation": not protected,
        "min_dominance": 0.05,
        "max_dominance": 0.8,
        "classification_reason": "test",
        "contract_fingerprint": "a" * 64,
    }
    values.update(overrides)
    return AssetPolicy.model_validate(values)


def test_wrong_product_replacement_is_a_hard_fail() -> None:
    policy = _policy(IntelligentAssetRole.PRIMARY_PRODUCT)
    assertion = AssetUsageAssertion(
        asset_id=policy.asset_id,
        used=True,
        identity_preserved=False,
        replaced_by=uuid4(),
        dominance=0.5,
    )

    result = evaluate_asset_usage([policy], [assertion])
    assert result.decision == "HARD_FAIL"
    assert any("cannot be replaced" in violation for violation in result.violations)
    assert any("identity was not preserved" in violation for violation in result.violations)
    with pytest.raises(AssetPolicyHardFail) as failure:
        enforce_asset_usage([policy], [assertion])
    assert failure.value.code == "ASSET_POLICY_HARD_FAIL"


def test_missing_required_logo_is_a_hard_fail() -> None:
    policy = _policy(IntelligentAssetRole.BRAND_LOGO)
    result = evaluate_asset_usage(
        [policy],
        [AssetUsageAssertion(asset_id=policy.asset_id, used=False)],
    )
    assert result.valid is False
    assert "missing from the composition" in result.violations[0]


def test_forbidden_crop_and_invalid_dominance_are_hard_failures() -> None:
    policy = _policy(IntelligentAssetRole.BRAND_LOGO)
    assertion = AssetUsageAssertion(
        asset_id=policy.asset_id,
        used=True,
        identity_preserved=True,
        cropped=True,
        dominance=0.95,
    )
    result = evaluate_asset_usage([policy], [assertion])
    assert any("cannot be cropped" in violation for violation in result.violations)
    assert any("dominance must be between" in violation for violation in result.violations)


def test_replaceable_background_can_continue() -> None:
    policy = _policy(
        IntelligentAssetRole.BACKGROUND_REFERENCE,
        required=False,
        preserve_identity=False,
        allow_crop=True,
        allow_replace=True,
        allow_generation=True,
        min_dominance=0,
        max_dominance=1,
    )
    assertion = AssetUsageAssertion(
        asset_id=policy.asset_id,
        used=True,
        identity_preserved=False,
        replaced_by=uuid4(),
        generated_substitute=True,
        dominance=1,
    )
    result = enforce_asset_usage([policy], [assertion])
    assert result.valid is True
    assert result.decision == "CONTINUE"


def test_asset_intelligence_input_rejects_duplicate_ids() -> None:
    asset_id = uuid4()
    contract = _contract()
    attachment = {
        "id": asset_id,
        "declared_role": "logo",
        "original_filename": "logo.png",
        "mime_type": "image/png",
    }
    with pytest.raises(ValueError, match="asset IDs must be unique"):
        AssetIntelligenceInput(
            semantic_contract=contract.to_dict(),
            latest_message="This is the logo.",
            attachments=[attachment, attachment],
        )
