import json
from datetime import UTC, datetime

import pytest

from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
from app.modules.posts.agents.reference_validator import (
    ReferenceDecision,
    ReferenceDimension,
    ReferenceOriginalityValidator,
    ReferenceUse,
    ReferenceValidatorInput,
)
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.supervisor import DEFAULT_SUPERVISOR_PLAN, SupervisorStage
from app.modules.posts.providers import LLMRequest, LLMResponse
from app.modules.posts.tools.research import (
    ExternalResearchResult,
    ResearchCategory,
    ResearchReport,
    ResearchVisualReference,
)


class _LLM:
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = responses or [_readout()]
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        value = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        return LLMResponse(text=json.dumps(value), provider="test", model="reference-test")


def _contract() -> PostSemanticContract:
    return PostSemanticContract.create(
        company="Roast Lab",
        brand="Roast Lab",
        product="Coffee",
        primary_entity="Coffee cup",
        goal="orders",
        audience="city professionals",
        market="Kosovo",
        location="Prishtina",
        offer=None,
        cta_intent="Order now",
        platform="Instagram",
        language="English",
        required_facts={},
        forbidden_claims=[],
        required_assets=[],
        constraints=[],
    )


def _payload(
    *, generic: bool = False, fingerprint: str | None = None, with_reference: bool = False
) -> ReferenceValidatorInput:
    contract = _contract()
    fp = fingerprint or contract.fingerprint
    reports = {
        category.value: ResearchReport.model_construct(
            category=category, visual_references=[]
        )
        for category in ResearchCategory
    }
    if with_reference:
        reports[ResearchCategory.VISUAL_REFERENCE.value] = ResearchReport.model_construct(
            category=ResearchCategory.VISUAL_REFERENCE,
            visual_references=[
                ResearchVisualReference(
                    url="https://example.com/reference.jpg",
                    description="Competitor campaign",
                    retrieved_at=datetime.now(UTC),
                )
            ],
        )
    research = ExternalResearchResult.model_construct(
        **reports,
        researched_at=datetime.now(UTC),
        contract_fingerprint=fp,
    )
    phrase = (
        "Centered coffee cup on a brown gradient with a rounded badge and Order Now CTA"
        if generic
        else "Coffee aroma becomes a hand-drawn city map with an asymmetric editorial frame"
    )
    return ReferenceValidatorInput.model_construct(
        semantic_contract=contract.to_dict(),
        brand=BrandAnalysis.model_construct(
            identity_summary="Experimental local roaster", contract_fingerprint=fp
        ),
        research=research,
        marketing_strategy=MarketingStrategy.model_construct(contract_fingerprint=fp),
        creative_direction=CreativeDirection.model_construct(
            contract_fingerprint=fp, creative_rationale=phrase
        ),
        copy_draft=CopyDraft.model_construct(
            contract_fingerprint=fp, headline=phrase, cta="Order now"
        ),
        art_direction=ArtDirection.model_construct(
            contract_fingerprint=fp, composition=phrase
        ),
        design_spec=DesignSpec.model_construct(
            contract_fingerprint=fp, background=phrase
        ),
    )


def _readout(*, score: int = 9, copy: bool = False) -> dict:
    return {
        "checks": [
            {"dimension": dimension.value, "score": score, "evidence": "Distinct execution."}
            for dimension in ReferenceDimension
        ],
        "references": (
            [
                {
                    "reference_url": "https://example.com/reference.jpg",
                    "classification": ReferenceUse.COPY.value,
                    "learned_principles": [],
                    "copied_specifics": ["same layout"],
                    "confidence": 0.95,
                }
            ]
            if copy
            else []
        ),
        "summary": "Independent comparison complete.",
    }


@pytest.mark.asyncio
async def test_distinct_work_passes_all_eight_dimensions() -> None:
    report = await ReferenceOriginalityValidator(_LLM()).review(_payload())

    assert report.decision is ReferenceDecision.PASS
    assert [item.dimension for item in report.checks] == list(ReferenceDimension)
    assert report.issues == []
    assert report.generic_patterns == []


@pytest.mark.asyncio
async def test_generic_coffee_formula_overrides_inflated_model_scores() -> None:
    report = await ReferenceOriginalityValidator(_LLM()).review(_payload(generic=True))

    assert report.decision is ReferenceDecision.REVISE
    assert report.generic_patterns[0].pattern == "commodity_product_gradient_badge_cta"
    scores = {item.dimension: item.score for item in report.checks}
    assert scores[ReferenceDimension.ORIGINALITY] == 7
    assert scores[ReferenceDimension.DIFFERENTIATION] == 7


@pytest.mark.asyncio
async def test_copy_classification_forces_similarity_and_originality_revision() -> None:
    report = await ReferenceOriginalityValidator(_LLM([_readout(copy=True)])).review(
        _payload(with_reference=True)
    )

    assert report.decision is ReferenceDecision.REVISE
    assert report.references[0].classification is ReferenceUse.COPY
    failed = {item.dimension for item in report.checks if not item.passed}
    assert ReferenceDimension.CONCEPT_SIMILARITY in failed
    assert ReferenceDimension.LAYOUT_SIMILARITY in failed
    assert ReferenceDimension.ORIGINALITY in failed


@pytest.mark.asyncio
async def test_invalid_provider_output_uses_deterministic_guards() -> None:
    llm = _LLM([{}, {}])

    report = await ReferenceOriginalityValidator(llm).review(_payload())

    assert len(llm.requests) == 2
    assert "CORRECTION PASS" in llm.requests[1].messages[0].content
    assert report.decision is ReferenceDecision.PASS
    assert all(item.score == 8 for item in report.checks)
    assert "Deterministic" in report.summary


def test_validator_rejects_mixed_semantic_contracts() -> None:
    payload = _payload(fingerprint="f" * 64)
    with pytest.raises(ValueError, match="disagree"):
        payload.inputs_describe_one_post()


def test_supervisor_places_reference_gate_before_generation_planning() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.REFERENCE_VALIDATION)
    generation = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.GENERATION_PLANNING)

    assert policy.dependencies == (SupervisorStage.DESIGN_SPEC,)
    assert PostWorkflowSection.REFERENCE_VALIDATION in policy.output_sections
    assert SupervisorStage.REFERENCE_VALIDATION in generation.dependencies
    assert PostWorkflowSection.REFERENCE_VALIDATION in generation.required_sections
