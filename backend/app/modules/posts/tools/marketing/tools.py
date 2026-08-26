from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.modules.posts.domain.contracts import (
    ToolCapability,
    ToolCategory,
    ToolDefinition,
    ToolSecurityPolicy,
)
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.tools import ToolExecutionContext, ToolRegistry

from .schemas import (
    CTAFrameResult,
    EvidenceOption,
    FeatureBenefitMapResult,
    FeatureBenefitValueOption,
    FrameworkKind,
    MarketingFrameworkInput,
    MessageStrategyFrameResult,
    PositioningFrameResult,
    STPResult,
    USPExtractionResult,
    ValuePropositionFrameResult,
)

MARKETING_STRATEGIST_AGENT = "marketing_strategist"
STP_ENGINE = "stp_engine"
POSITIONING_BUILDER = "positioning_builder"
FEATURE_BENEFIT_MAPPER = "feature_benefit_mapper"
USP_EXTRACTOR = "usp_extractor"
VALUE_PROPOSITION_BUILDER = "value_proposition_builder"
MESSAGE_STRATEGY_ENGINE = "message_strategy_engine"
CTA_ENGINE = "cta_engine"

MARKETING_FRAMEWORK_TOOL_NAMES = frozenset(
    {
        STP_ENGINE,
        POSITIONING_BUILDER,
        FEATURE_BENEFIT_MAPPER,
        USP_EXTRACTOR,
        VALUE_PROPOSITION_BUILDER,
        MESSAGE_STRATEGY_ENGINE,
        CTA_ENGINE,
    }
)


def _contract(payload: MarketingFrameworkInput) -> PostSemanticContract:
    return PostSemanticContract.from_dict(payload.semantic_contract)


def _basis(*references: str) -> list[str]:
    return list(dict.fromkeys(reference for reference in references if reference))[:20]


def run_stp_engine(payload: MarketingFrameworkInput) -> STPResult:
    contract = _contract(payload)
    return STPResult(
        objective=EvidenceOption(value=contract.goal, basis=["semantic_contract.goal"]),
        segments=[
            EvidenceOption(
                value=segment.name,
                basis=_basis(f"audience.segments.{segment.name}", *segment.basis),
            )
            for segment in payload.audience.segments
        ],
        target=EvidenceOption(
            value=payload.audience.target.segment,
            basis=_basis(
                "audience.target",
                f"audience.segments.{payload.audience.target.segment}",
                *payload.audience.target.basis,
            ),
        ),
    )


def run_feature_benefit_mapper(payload: MarketingFrameworkInput) -> FeatureBenefitMapResult:
    mappings: list[FeatureBenefitValueOption] = []
    for chain in payload.product.feature_benefit_value:
        if chain.source_fact not in payload.product.verified_facts:
            raise ValueError(
                f"feature-benefit mapping references unknown fact: {chain.source_fact}"
            )
        mappings.append(
            FeatureBenefitValueOption(
                feature=chain.feature,
                benefit=chain.benefit,
                customer_value=chain.customer_value,
                basis=_basis(
                    f"product.feature_benefit_value.{chain.source_fact}",
                    f"product.verified_facts.{chain.source_fact}",
                ),
            )
        )
    return FeatureBenefitMapResult(mappings=mappings)


def run_usp_extractor(payload: MarketingFrameworkInput) -> USPExtractionResult:
    candidates: list[EvidenceOption] = []
    for index, candidate in enumerate(payload.product.usp_candidates, start=1):
        missing = [
            fact for fact in candidate.source_facts if fact not in payload.product.verified_facts
        ]
        if missing:
            raise ValueError("USP candidate references unknown facts: " + ", ".join(missing))
        candidates.append(
            EvidenceOption(
                value=candidate.text,
                basis=_basis(
                    f"product.usp_candidates.{index}",
                    *(f"product.verified_facts.{fact}" for fact in candidate.source_facts),
                ),
            )
        )
    return USPExtractionResult(candidates=candidates)


def _differentiators(payload: MarketingFrameworkInput) -> list[EvidenceOption]:
    chains = run_feature_benefit_mapper(payload).mappings
    usps = run_usp_extractor(payload).candidates
    values = [
        EvidenceOption(value=chain.customer_value, basis=chain.basis) for chain in chains
    ]
    result = [*usps, *values]
    if not result:
        raise ValueError("positioning requires a verified product differentiator")
    return result


def run_positioning_builder(payload: MarketingFrameworkInput) -> PositioningFrameResult:
    return PositioningFrameResult(
        target=run_stp_engine(payload).target,
        customer_tension=EvidenceOption(
            value=payload.audience.customer_tension.tension,
            basis=_basis(
                "audience.customer_tension", *payload.audience.customer_tension.basis
            ),
        ),
        differentiators=_differentiators(payload),
    )


def _audience_options(payload: MarketingFrameworkInput) -> list[EvidenceOption]:
    options: list[EvidenceOption] = []
    for field in ("needs", "desires"):
        for insight in getattr(payload.audience, field):
            options.append(
                EvidenceOption(
                    value=insight.insight,
                    basis=_basis(f"audience.{field}", *insight.basis),
                )
            )
    return options


def run_value_proposition_builder(
    payload: MarketingFrameworkInput,
) -> ValuePropositionFrameResult:
    customer_needs = _audience_options(payload)
    values = [
        EvidenceOption(value=chain.customer_value, basis=chain.basis)
        for chain in run_feature_benefit_mapper(payload).mappings
    ]
    if not values:
        values = run_usp_extractor(payload).candidates
    if not customer_needs:
        raise ValueError("value proposition requires a supplied audience need or desire")
    if not values:
        raise ValueError("value proposition requires a verified customer value")
    return ValuePropositionFrameResult(
        target=run_stp_engine(payload).target,
        customer_needs=customer_needs,
        customer_values=values,
    )


def run_message_strategy_engine(
    payload: MarketingFrameworkInput,
) -> MessageStrategyFrameResult:
    contract = _contract(payload)
    pain_basis = [
        basis
        for insight in payload.audience.pain_points
        for basis in insight.basis
    ]
    tension = payload.audience.customer_tension
    has_problem = bool(payload.audience.pain_points or tension.tension)
    eligible = [FrameworkKind.PAS] if has_problem else []
    if contract.goal and payload.audience.target.segment:
        eligible.append(FrameworkKind.AIDA)
    eligible.append(FrameworkKind.NONE)
    return MessageStrategyFrameResult(
        objective=EvidenceOption(value=contract.goal, basis=["semantic_contract.goal"]),
        customer_tension=EvidenceOption(
            value=tension.tension,
            basis=_basis("audience.customer_tension", *tension.basis, *pain_basis),
        ),
        value_options=run_value_proposition_builder(payload).customer_values,
        eligible_frameworks=list(dict.fromkeys(eligible)),
    )


def run_cta_engine(payload: MarketingFrameworkInput) -> CTAFrameResult:
    contract = _contract(payload)
    return CTAFrameResult(
        intent=EvidenceOption(
            value=contract.cta_intent,
            basis=["semantic_contract.cta_intent"],
        ),
        objective=EvidenceOption(value=contract.goal, basis=["semantic_contract.goal"]),
        constraints=list(dict.fromkeys([*contract.constraints, *payload.product.constraints])),
        forbidden_claims=list(contract.forbidden_claims),
    )


_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    STP_ENGINE: STPResult,
    POSITIONING_BUILDER: PositioningFrameResult,
    FEATURE_BENEFIT_MAPPER: FeatureBenefitMapResult,
    USP_EXTRACTOR: USPExtractionResult,
    VALUE_PROPOSITION_BUILDER: ValuePropositionFrameResult,
    MESSAGE_STRATEGY_ENGINE: MessageStrategyFrameResult,
    CTA_ENGINE: CTAFrameResult,
}

_RUNNERS: dict[str, Callable[[MarketingFrameworkInput], BaseModel]] = {
    STP_ENGINE: run_stp_engine,
    POSITIONING_BUILDER: run_positioning_builder,
    FEATURE_BENEFIT_MAPPER: run_feature_benefit_mapper,
    USP_EXTRACTOR: run_usp_extractor,
    VALUE_PROPOSITION_BUILDER: run_value_proposition_builder,
    MESSAGE_STRATEGY_ENGINE: run_message_strategy_engine,
    CTA_ENGINE: run_cta_engine,
}


def marketing_framework_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(
        ToolDefinition(
            tool_name=name,
            category=ToolCategory.MARKETING,
            input_schema=MarketingFrameworkInput,
            output_schema=_OUTPUT_SCHEMAS[name],
            allowed_agents=frozenset({MARKETING_STRATEGIST_AGENT}),
            timeout_seconds=5,
            security=ToolSecurityPolicy(
                capabilities=frozenset({ToolCapability.READ_CONTEXT})
            ),
        )
        for name in sorted(MARKETING_FRAMEWORK_TOOL_NAMES)
    )


def register_marketing_framework_tools(registry: ToolRegistry) -> None:
    for definition in marketing_framework_tool_definitions():
        runner = _RUNNERS[definition.tool_name]

        async def handler(
            payload: BaseModel,
            _context: ToolExecutionContext,
            *,
            run: Callable[[MarketingFrameworkInput], BaseModel] = runner,
        ) -> BaseModel:
            if not isinstance(payload, MarketingFrameworkInput):
                raise TypeError("marketing tool received an invalid input type")
            return run(payload)

        registry.register(definition, handler)


class DirectMarketingFrameworkGateway:
    """Test-only-compatible local gateway with the same validated contracts."""

    async def invoke(self, tool_name: str, payload: BaseModel | dict[str, Any]) -> BaseModel:
        try:
            runner = _RUNNERS[tool_name]
            output_schema = _OUTPUT_SCHEMAS[tool_name]
        except KeyError as exc:
            raise ValueError(f"unknown marketing framework tool: {tool_name}") from exc
        validated = MarketingFrameworkInput.model_validate(payload)
        return output_schema.model_validate(runner(validated))


__all__ = [
    "CTA_ENGINE",
    "FEATURE_BENEFIT_MAPPER",
    "MARKETING_FRAMEWORK_TOOL_NAMES",
    "MESSAGE_STRATEGY_ENGINE",
    "POSITIONING_BUILDER",
    "STP_ENGINE",
    "USP_EXTRACTOR",
    "VALUE_PROPOSITION_BUILDER",
    "DirectMarketingFrameworkGateway",
    "marketing_framework_tool_definitions",
    "register_marketing_framework_tools",
    "run_cta_engine",
    "run_feature_benefit_mapper",
    "run_message_strategy_engine",
    "run_positioning_builder",
    "run_stp_engine",
    "run_usp_extractor",
    "run_value_proposition_builder",
]
