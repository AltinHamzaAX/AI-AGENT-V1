import json
import unicodedata
from typing import Any

from pydantic import BaseModel, ValidationError

from app.modules.posts.agents.framework import AgentExecutionContext, AgentRuntime
from app.modules.posts.domain.contracts import (
    SPECIALIST_TIMEOUT_SECONDS,
    AgentDefinition,
    RetryPolicy,
)
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    ProviderResponseError,
)
from app.modules.posts.tools import ToolGateway

from .schemas import (
    BrandAnalysis,
    BrandProductAnalysis,
    BrandProductInput,
    BrandProductLLMOutput,
    ProductAnalysis,
)

BRAND_PRODUCT_AGENT_NAME = "brand_product_strategist"

BRAND_PRODUCT_DEFINITION = AgentDefinition(
    name=BRAND_PRODUCT_AGENT_NAME,
    role="Analyze verified brand and product facts without creating campaign strategy",
    input_schema=BrandProductInput,
    output_schema=BrandProductAnalysis,
    allowed_tools=frozenset(),
    timeout_seconds=SPECIALIST_TIMEOUT_SECONDS,
    retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True, retry_on_error=True),
)


class BrandProductStrategistAgent:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> BrandProductAnalysis:
        if not isinstance(payload, BrandProductInput):
            raise TypeError("brand product strategist received an invalid input type")
        try:
            contract = PostSemanticContract.from_dict(payload.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("brand product strategist requires a valid semantic contract") from exc

        response = await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=_system_prompt()),
                    LLMMessage(
                        role="user",
                        content=json.dumps(
                            _analysis_source(contract),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ),
                ),
                temperature=0,
                response_format="json",
            )
        )
        try:
            analysis = BrandProductLLMOutput.model_validate(_parse_json_object(response.text))
            return _ground_analysis(contract, analysis)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError(
                "brand product strategist returned invalid structured output"
            ) from exc


def register_brand_product_agent(runtime: AgentRuntime, llm: LLMProvider) -> None:
    agent = BrandProductStrategistAgent(llm)
    runtime.register(BRAND_PRODUCT_DEFINITION, agent.execute)


def _system_prompt() -> str:
    schema = json.dumps(
        BrandProductLLMOutput.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        "You are the Brand & Product Strategist specialist in a marketing-post workflow. "
        "OUTPUT LANGUAGE REQUIREMENT: write every generated field in English. "
        "Analyze only the verified semantic contract. Describe brand identity and cautious "
        "personality traits, then map every supported product FEATURE to a BENEFIT and a "
        "CUSTOMER VALUE. Every mapping must name source_fact exactly as a key present in "
        "required_facts; never invent a feature. Benefits and customer values may explain "
        "the consequence of that fact, but must not introduce new factual claims. USP values "
        "are candidates, not approved advertising claims, and each must cite one or more "
        "required_facts keys in source_facts. Classify every required_facts key into "
        "brand_fact_keys or product_fact_keys (or both when genuinely applicable). Never repeat "
        "or paraphrase a forbidden claim. Do not perform audience research, "
        "market research, positioning, marketing strategy, campaign planning, creative strategy, "
        "copywriting, art direction, or design. Write all generated analytical prose in clear, "
        "concise English regardless of the contract's requested content language. Preserve brand "
        "names, product names, offers, proper nouns, and verified fact values exactly as supplied; "
        "do not translate or rewrite those authoritative values. Even when an authoritative "
        "value is in another language, explanations must still be English. Return exactly one "
        "JSON object "
        "matching this "
        f"schema and no prose or markdown: {schema}"
    )


def _analysis_source(contract: PostSemanticContract) -> dict[str, Any]:
    """Expose only Ticket 15 facts, excluding downstream strategy context."""
    return {
        "analysis_language": "English",
        "company": contract.company,
        "brand": contract.brand,
        "product": contract.product,
        "primary_entity": contract.primary_entity,
        "offer": contract.offer,
        "required_facts": dict(contract.required_facts),
        "forbidden_claims": list(contract.forbidden_claims),
        "required_assets": [str(asset_id) for asset_id in contract.required_assets],
        "constraints": list(contract.constraints),
        "contract_fingerprint": contract.fingerprint,
    }


def _ground_analysis(
    contract: PostSemanticContract,
    analysis: BrandProductLLMOutput,
) -> BrandProductAnalysis:
    facts = dict(contract.required_facts)
    normalized_facts = {_semantic(key): (key, value) for key, value in facts.items()}
    brand_fact_keys = _ground_fact_keys(analysis.brand_fact_keys, normalized_facts)
    product_fact_keys = _ground_fact_keys(analysis.product_fact_keys, normalized_facts)
    assigned = {_semantic(key) for key in (*brand_fact_keys, *product_fact_keys)}
    if assigned != set(normalized_facts):
        raise ValueError("every required fact must be classified as brand or product")
    grounded_chains = []
    for chain in analysis.feature_benefit_value:
        source = normalized_facts.get(_semantic(chain.source_fact))
        if source is None:
            raise ValueError(f"unsupported source fact: {chain.source_fact}")
        if _semantic(source[1]) not in _semantic(chain.feature):
            raise ValueError(f"feature is not grounded in source fact: {source[0]}")
        grounded_chains.append(chain.model_copy(update={"source_fact": source[0]}))

    forbidden = tuple(_semantic(claim) for claim in contract.forbidden_claims)
    candidate_text = [analysis.identity_summary, *analysis.personality_traits]
    candidate_text.extend(candidate.text for candidate in analysis.usp_candidates)
    candidate_text.extend(
        text
        for chain in grounded_chains
        for text in (chain.feature, chain.benefit, chain.customer_value)
    )
    if any(
        forbidden_claim and forbidden_claim in _semantic(text)
        for forbidden_claim in forbidden
        for text in candidate_text
    ):
        raise ValueError("analysis contains a forbidden claim")

    grounded_usps = []
    for candidate in analysis.usp_candidates:
        source_facts = _ground_fact_keys(candidate.source_facts, normalized_facts)
        grounded_usps.append(candidate.model_copy(update={"source_facts": source_facts}))

    # A fact may describe the brand at classification time and still be the
    # factual basis of a product promise (for example, a hotel's location).
    # Downstream marketing tools only receive ProductAnalysis, so every fact
    # referenced by a product chain or USP must travel with that object too.
    referenced_product_facts = {
        chain.source_fact for chain in grounded_chains
    } | {
        fact for candidate in grounded_usps for fact in candidate.source_facts
    }
    product_verified_fact_keys = list(
        dict.fromkeys([*product_fact_keys, *sorted(referenced_product_facts)])
    )

    brand_name = contract.brand
    product_name = contract.product or contract.primary_entity
    return BrandProductAnalysis(
        brand=BrandAnalysis(
            company=contract.company,
            name=brand_name,
            identity_summary=analysis.identity_summary,
            personality_traits=analysis.personality_traits,
            verified_facts={key: facts[key] for key in brand_fact_keys},
            constraints=list(contract.constraints),
            contract_fingerprint=contract.fingerprint,
        ),
        product=ProductAnalysis(
            name=product_name,
            primary_entity=contract.primary_entity,
            offer=contract.offer,
            feature_benefit_value=grounded_chains,
            usp_candidates=grounded_usps,
            verified_facts={key: facts[key] for key in product_verified_fact_keys},
            forbidden_claims=list(contract.forbidden_claims),
            constraints=list(contract.constraints),
            required_assets=list(contract.required_assets),
            contract_fingerprint=contract.fingerprint,
        ),
    )


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


def _semantic(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _ground_fact_keys(
    keys: list[str],
    facts: dict[str, tuple[str, str]],
) -> list[str]:
    grounded: list[str] = []
    for key in keys:
        source = facts.get(_semantic(key))
        if source is None:
            raise ValueError(f"unsupported source fact: {key}")
        if source[0] not in grounded:
            grounded.append(source[0])
    return grounded


__all__ = [
    "BRAND_PRODUCT_AGENT_NAME",
    "BRAND_PRODUCT_DEFINITION",
    "BrandProductStrategistAgent",
    "register_brand_product_agent",
]
