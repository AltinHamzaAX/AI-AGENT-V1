import json
import unicodedata
from typing import Any

from pydantic import BaseModel, ValidationError

from app.modules.posts.agents.framework import AgentExecutionContext, AgentRuntime
from app.modules.posts.domain.contracts import AgentDefinition, RetryPolicy
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    ProviderResponseError,
)
from app.modules.posts.tools import ToolGateway

from .schemas import (
    AudienceIntelligence,
    AudienceIntelligenceInput,
    AudienceIntelligenceLLMOutput,
)

AUDIENCE_INTELLIGENCE_AGENT_NAME = "audience_intelligence"

AUDIENCE_INTELLIGENCE_DEFINITION = AgentDefinition(
    name=AUDIENCE_INTELLIGENCE_AGENT_NAME,
    role="Develop grounded audience hypotheses without making marketing strategy",
    input_schema=AudienceIntelligenceInput,
    output_schema=AudienceIntelligence,
    allowed_tools=frozenset(),
    timeout_seconds=120,
    retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True, retry_on_error=True),
)

_LIMITATION = (
    "Audience insights are reasoned hypotheses until the External Research stage validates them."
)
_UNSUPPORTED_ASSUMPTION_MARKERS = (
    "affordable",
    "budget",
    "business trip",
    "cultural experience",
    "cost",
    "family reunion",
    "first-time",
    "frequent",
    "income",
    "luggage",
    "multiple times",
    "peak time",
    "price",
    "reputation",
    "weather",
    "willingness to pay",
    "willing to pay",
)


class AudienceIntelligenceAgent:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> AudienceIntelligence:
        if not isinstance(payload, AudienceIntelligenceInput):
            raise TypeError("audience intelligence received an invalid input type")
        contract = validate_audience_intelligence_input(payload)
        source, allowed_basis = _analysis_source(payload, contract)
        analysis = await self._complete_analysis(
            source=source,
            contract=contract,
            allowed_basis=allowed_basis,
        )
        return AudienceIntelligence(
            **analysis.model_dump(exclude={"segments", "situations"}),
            segments=[
                segment.model_copy(update={"parent_audience": contract.audience})
                for segment in analysis.segments
            ],
            context={
                "declared_audience": contract.audience,
                "market": contract.market,
                "location": contract.location,
                "platform": contract.platform,
                "situations": analysis.situations,
            },
            limitations=_limitations(analysis),
            contract_fingerprint=contract.fingerprint,
        )

    async def _complete_analysis(
        self,
        *,
        source: dict[str, Any],
        contract: PostSemanticContract,
        allowed_basis: set[str],
    ) -> AudienceIntelligenceLLMOutput:
        messages = [
            LLMMessage(role="system", content=_system_prompt(allowed_basis)),
            LLMMessage(
                role="user",
                content=json.dumps(source, ensure_ascii=False, sort_keys=True),
            ),
        ]
        last_error: Exception | None = None
        for repair_attempt in range(2):
            response = await self._llm.complete(
                LLMRequest(
                    messages=tuple(messages),
                    temperature=0,
                    response_format="json",
                )
            )
            try:
                analysis = AudienceIntelligenceLLMOutput.model_validate(
                    _parse_json_object(response.text)
                )
                _validate_analysis(analysis, contract, allowed_basis, source)
                return analysis
            except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
                last_error = exc
                if repair_attempt == 0:
                    messages.extend(
                        (
                            LLMMessage(role="assistant", content=response.text[:20_000]),
                            LLMMessage(
                                role="user",
                                content=(
                                    "The previous JSON was rejected by a deterministic guardrail: "
                                    f"{exc}. Correct only the rejected issue, preserve supported "
                                    "insights, and return the complete JSON object again."
                                ),
                            ),
                        )
                    )
        raise ProviderResponseError(
            "audience intelligence returned invalid structured output"
        ) from last_error


def register_audience_intelligence_agent(
    runtime: AgentRuntime,
    llm: LLMProvider,
) -> None:
    agent = AudienceIntelligenceAgent(llm)
    runtime.register(AUDIENCE_INTELLIGENCE_DEFINITION, agent.execute)


def validate_audience_intelligence_input(
    payload: AudienceIntelligenceInput,
) -> PostSemanticContract:
    try:
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("audience intelligence requires a valid semantic contract") from exc
    if (
        payload.brand.contract_fingerprint != contract.fingerprint
        or payload.product.contract_fingerprint != contract.fingerprint
    ):
        raise ValueError("brand and product analysis must match the semantic contract")
    if payload.brand.company != contract.company or payload.brand.name != contract.brand:
        raise ValueError("brand analysis changed protected identity")
    expected_product = contract.product or contract.primary_entity
    if (
        payload.product.name != expected_product
        or payload.product.primary_entity != contract.primary_entity
        or payload.product.offer != contract.offer
    ):
        raise ValueError("product analysis changed protected product facts")
    expected_facts = dict(contract.required_facts)
    combined_facts = dict(payload.brand.verified_facts)
    for key, value in payload.product.verified_facts.items():
        existing = combined_facts.get(key)
        if existing is not None and existing != value:
            raise ValueError("brand and product analysis disagree on a verified fact")
        combined_facts[key] = value
    if combined_facts != expected_facts:
        raise ValueError("brand and product analysis changed required facts")
    constraints = list(contract.constraints)
    if payload.brand.constraints != constraints or payload.product.constraints != constraints:
        raise ValueError("brand and product analysis changed constraints")
    if payload.product.forbidden_claims != list(contract.forbidden_claims):
        raise ValueError("product analysis changed forbidden claims")
    if payload.product.required_assets != list(contract.required_assets):
        raise ValueError("product analysis changed required assets")
    return contract


def _analysis_source(
    payload: AudienceIntelligenceInput,
    contract: PostSemanticContract,
) -> tuple[dict[str, Any], set[str]]:
    source: dict[str, Any] = {
        "analysis_language": "English",
        "semantic_contract": {
            "audience": contract.audience,
            "market": contract.market,
            "location": contract.location,
            "platform": contract.platform,
        },
        "brand": {
            "identity_summary": payload.brand.identity_summary,
            "verified_facts": payload.brand.verified_facts,
        },
        "product": {
            "primary_entity": payload.product.primary_entity,
            "feature_benefit_value": [
                item.model_dump(mode="json") for item in payload.product.feature_benefit_value
            ],
            "verified_facts": payload.product.verified_facts,
        },
    }
    allowed = {
        "semantic_contract.audience",
        "semantic_contract.platform",
        "brand.identity_summary",
        "product.primary_entity",
    }
    for field_name in ("market", "location"):
        if source["semantic_contract"][field_name] is not None:
            allowed.add(f"semantic_contract.{field_name}")
    allowed.update(f"brand.verified_facts.{key}" for key in payload.brand.verified_facts)
    allowed.update(f"product.verified_facts.{key}" for key in payload.product.verified_facts)
    allowed.update(
        f"product.feature_benefit_value.{item.source_fact}"
        for item in payload.product.feature_benefit_value
    )
    return source, allowed


def _system_prompt(allowed_basis: set[str]) -> str:
    schema = json.dumps(
        AudienceIntelligenceLLMOutput.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    basis = json.dumps(sorted(allowed_basis), ensure_ascii=False)
    return (
        "You are the Audience Intelligence specialist in a marketing-post workflow. "
        "STRICT EVIDENCE RULE: use only attributes explicitly present in the input. Exclude "
        "unprovided demographic, behavioral, trip-purpose, financial, and situational attributes. "
        "Go beyond demographics: identify two to five meaningfully distinct segments, select "
        "one target segment, and "
        "analyze needs, desires, pain points, objections, motivation, purchase intent, trust "
        "triggers, real-life context, and the tension between the current and desired state. "
        "Treat all derived insights as hypotheses, never as researched facts. Use low or medium "
        "confidence only; high confidence requires later external research. Build segments only "
        "from distinct need states supported by product feature-benefit values, such as urgency, "
        "certainty, or convenience when those values are present. Every segment must be a narrower "
        "subset of the declared audience, not a new audience, and must include a key "
        "declared-audience descriptor in its name or description. Leave parent_audience null; "
        "the application sets it from the immutable contract. "
        "Derive objections only as uncertainty about whether a stated feature will deliver its "
        "stated benefit. Derive trust triggers only from concrete confirmation or proof of stated "
        "features, never from unprovided brand reputation. Limit situations to arrival at the "
        "stated location and the immediate transportation need implied by the verified pickup "
        "feature; add no extra timing or travel conditions. Every segment, insight, target, "
        "purchase-intent assessment, and customer "
        "tension must cite one or more exact "
        f"basis identifiers from this allowlist: {basis}. "
        "Write generated analysis in concise English while preserving supplied proper nouns and "
        "facts exactly. Do not conduct external research, invent statistics, redefine the target "
        "outside the declared audience, choose positioning, create a USP, make marketing strategy, "
        "write copy, or propose creative/design executions. Return exactly one JSON object "
        f"matching this schema and no prose or markdown: {schema}"
    )


def _validate_analysis(
    analysis: AudienceIntelligenceLLMOutput,
    contract: PostSemanticContract,
    allowed_basis: set[str],
    source: dict[str, Any],
) -> None:
    for reference in _basis_references(analysis):
        if reference not in allowed_basis:
            raise ValueError(f"unsupported audience insight basis: {reference}")
    for segment in analysis.segments:
        if "semantic_contract.audience" not in segment.basis:
            raise ValueError("audience segment must be grounded in the declared audience")
    if "high" in _confidence_values(analysis.model_dump(mode="json")):
        raise ValueError("high-confidence audience claims require external research")
    forbidden = [_semantic(claim) for claim in contract.forbidden_claims]
    text_values = _all_strings(analysis.model_dump(mode="json"))
    source_text = _semantic(" ".join(_all_strings(source)))
    for marker in _UNSUPPORTED_ASSUMPTION_MARKERS:
        if marker not in source_text and any(marker in _semantic(value) for value in text_values):
            raise ValueError(f"unsupported audience assumption: {marker}")
    if any(claim and claim in _semantic(value) for claim in forbidden for value in text_values):
        raise ValueError("audience intelligence contains a forbidden claim")


def _basis_references(analysis: AudienceIntelligenceLLMOutput) -> list[str]:
    references: list[str] = []
    for segment in analysis.segments:
        references.extend(segment.basis)
    references.extend(analysis.target.basis)
    for collection in (
        analysis.needs,
        analysis.desires,
        analysis.pain_points,
        analysis.objections,
        analysis.motivation,
        analysis.trust_triggers,
        analysis.situations,
    ):
        for insight in collection:
            references.extend(insight.basis)
    references.extend(analysis.purchase_intent.basis)
    references.extend(analysis.customer_tension.basis)
    return references


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _all_strings(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _all_strings(nested)]
    return []


def _confidence_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values = [value["confidence"]] if isinstance(value.get("confidence"), str) else []
        return values + [item for nested in value.values() for item in _confidence_values(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _confidence_values(nested)]
    return []


def _parse_json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise TypeError("provider output must be a JSON object")
    return parsed


def _limitations(analysis: AudienceIntelligenceLLMOutput) -> list[str]:
    limitations = [_LIMITATION]
    if len(analysis.segments) == 1:
        limitations.append(
            "Only one evidence-supported segment was identified; External Research should "
            "test additional segmentation."
        )
    return limitations


def _semantic(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


__all__ = [
    "AUDIENCE_INTELLIGENCE_AGENT_NAME",
    "AUDIENCE_INTELLIGENCE_DEFINITION",
    "AudienceIntelligenceAgent",
    "register_audience_intelligence_agent",
    "validate_audience_intelligence_input",
]
