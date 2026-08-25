import json
import re
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
    LLMResponse,
    ProviderResponseError,
)
from app.modules.posts.tools import ToolGateway
from app.modules.posts.tools.research import ResearchCategory

from .schemas import (
    DECISION_PRINCIPLES,
    STRATEGY_DECISIONS,
    MarketingStrategy,
    MarketingStrategyInput,
    MarketingStrategyLLMOutput,
    StrategicDecision,
)

MARKETING_STRATEGIST_AGENT_NAME = "marketing_strategist"

MARKETING_STRATEGIST_DEFINITION = AgentDefinition(
    name=MARKETING_STRATEGIST_AGENT_NAME,
    role="Decide marketing strategy from verified facts, audience intelligence and research",
    input_schema=MarketingStrategyInput,
    output_schema=MarketingStrategy,
    allowed_tools=frozenset(),
    # Everything this agent needs was gathered by earlier stages, so the whole
    # cost is one reasoning call over a large assembled context.
    timeout_seconds=180,
    retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True, retry_on_error=True),
)

#: How many observations per research dimension reach the prompt. This agent
#: is handed eight research reports, an audience profile and a brand analysis
#: at once; passing every insight would spend the context window on evidence
#: the strategy will never cite. The report keeps everything regardless.
RESEARCH_OBSERVATIONS_PER_DIMENSION = 2
#: How much of one observation is shown. Long enough to carry the finding,
#: short enough that forty dimensions still fit.
OBSERVATION_PREVIEW_CHARS = 240

#: A basis identifier that proves a decision descends from a real product
#: capability rather than from an appealing phrase.
_PRODUCT_EVIDENCE_PREFIXES = ("product.usp_candidates.", "product.feature_benefit_value.")

_AUDIENCE_EVIDENCE_PREFIXES = (
    "audience.needs",
    "audience.desires",
    "audience.pain_points",
    "audience.objections",
    "audience.motivation",
    "audience.trust_triggers",
    "audience.customer_tension",
    "audience.purchase_intent",
    "research.audience.",
)
_TARGET_EVIDENCE_PREFIXES = ("audience.target", "audience.segments.")
_PRODUCT_VALUE_PREFIXES = (
    "product.feature_benefit_value.",
    "product.usp_candidates.",
    "product.verified_facts.",
    "brand.verified_facts.",
    "research.brand_product.",
)

# Every strategic field has its own minimum evidence shape. Each inner tuple is
# an OR group; every group must have at least one matching basis identifier.
_DECISION_BASIS_REQUIREMENTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "business_objective": (("semantic_contract.goal",),),
    "segmentation": (("semantic_contract.audience", "audience.segments.", "research.audience."),),
    "targeting": (_TARGET_EVIDENCE_PREFIXES,),
    "positioning": (_TARGET_EVIDENCE_PREFIXES, _PRODUCT_VALUE_PREFIXES),
    "customer_insight": (_AUDIENCE_EVIDENCE_PREFIXES,),
    "customer_tension": (("audience.customer_tension",),),
    "usp": (_PRODUCT_EVIDENCE_PREFIXES,),
    "value_proposition": (_PRODUCT_VALUE_PREFIXES, _AUDIENCE_EVIDENCE_PREFIXES),
    "marketing_angle": (_PRODUCT_VALUE_PREFIXES, _AUDIENCE_EVIDENCE_PREFIXES),
    "single_minded_message": (_PRODUCT_VALUE_PREFIXES, _AUDIENCE_EVIDENCE_PREFIXES),
    "desired_reaction": (
        ("semantic_contract.goal", "semantic_contract.cta_intent"),
        ("audience.motivation", "audience.purchase_intent", "audience.customer_tension"),
    ),
    "cta_strategy": (("semantic_contract.cta_intent",), ("semantic_contract.goal",)),
}

_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_NUMERIC_CLAIM = re.compile(
    r"(?<![\w])(?:[$€£]\s*)?\d+(?:[.,]\d+)?(?:\s*%|/[A-Za-z0-9]+)?",
    re.IGNORECASE,
)
_UNSUPPORTED_ABSOLUTE_CLAIM = re.compile(
    r"\b(?:complimentary|guaranteed?|risk[- ]free|free upgrade|best|cheapest|newest|"
    r"number one)\b|#1",
    re.IGNORECASE,
)
_PROHIBITED_STRATEGY_ACTION = re.compile(
    r"\b(?:copy|clone|imitate|replicate)\b.{0,50}\bcompetitor\b|"
    r"\b(?:replace|substitute|swap)\b.{0,50}\b(?:brand|company|logo|product)\b",
    re.IGNORECASE,
)
_DOWNSTREAM_EXECUTION_MARKER = re.compile(
    r"\b(?:caption|color palette|font|hashtag|headline|logo placement|layout|typography)\b",
    re.IGNORECASE,
)
_CAPITALIZED_TOKEN = re.compile(r"\b[A-Z][A-Za-z0-9-]{2,}\b")
_LEADING_NAMED_ENTITY = re.compile(
    r"^\s*([A-Z][A-Za-z0-9-]{2,})\s+"
    r"(?:becomes|can|delivers|is|offers|provides|should|will)\b"
)
_KNOWN_STRATEGY_ACRONYMS = {"aida", "cta", "pas", "stp", "usp"}
_MULTI_PROMISE_MARKERS = (
    " and get ",
    " and receive ",
    " as well as ",
    " not only ",
    " plus ",
    " while also ",
)


class MarketingStrategistAgent:
    """Turns gathered evidence into a strategy where every call is answerable."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> MarketingStrategy:
        if not isinstance(payload, MarketingStrategyInput):
            raise TypeError("marketing strategist received an invalid input type")
        try:
            contract = PostSemanticContract.from_dict(payload.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("marketing strategist requires a valid semantic contract") from exc
        _require_one_contract(payload, contract)

        source, allowed_basis = _strategy_source(payload, contract)
        response = await self._complete(source, allowed_basis)
        try:
            return _validated_strategy(
                response.text,
                payload=payload,
                contract=contract,
                allowed_basis=allowed_basis,
                source=source,
            )
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._complete(
                source,
                allowed_basis,
                previous_output=response.text,
                validation_error=str(first_exc),
            )
        try:
            return _validated_strategy(
                repair.text,
                payload=payload,
                contract=contract,
                allowed_basis=allowed_basis,
                source=source,
            )
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError(
                "marketing strategist returned invalid structured output"
            ) from exc

    async def _complete(
        self,
        source: dict[str, Any],
        allowed_basis: set[str],
        *,
        previous_output: str | None = None,
        validation_error: str | None = None,
    ) -> LLMResponse:
        system = _system_prompt(allowed_basis)
        user: dict[str, Any] = {"source": source}
        if previous_output is not None:
            system += (
                " CORRECTION PASS: the previous JSON failed deterministic application "
                "validation. Return the complete corrected JSON object, not a patch. Fix every "
                "listed violation while preserving only supported decisions."
            )
            user["previous_output"] = previous_output[:20_000]
            user["validation_error"] = (validation_error or "invalid output")[:4_000]
        return await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=system),
                    LLMMessage(
                        role="user",
                        content=json.dumps(user, ensure_ascii=False, sort_keys=True),
                    ),
                ),
                temperature=0,
                response_format="json",
            )
        )


def register_marketing_strategist_agent(runtime: AgentRuntime, llm: LLMProvider) -> None:
    agent = MarketingStrategistAgent(llm)
    runtime.register(MARKETING_STRATEGIST_DEFINITION, agent.execute)


def _validated_strategy(
    raw_output: str,
    *,
    payload: MarketingStrategyInput,
    contract: PostSemanticContract,
    allowed_basis: set[str],
    source: dict[str, Any],
) -> MarketingStrategy:
    strategy = MarketingStrategyLLMOutput.model_validate(_parse_json_object(raw_output))
    strategy = _canonicalize_strategy_basis(strategy, allowed_basis=allowed_basis)
    _validate_strategy(strategy, payload, contract, allowed_basis, source)
    return _ground_strategy(strategy, payload, contract)


def _canonicalize_strategy_basis(
    strategy: MarketingStrategyLLMOutput,
    *,
    allowed_basis: set[str],
) -> MarketingStrategyLLMOutput:
    """Make provenance application-owned instead of trusting model bookkeeping.

    Exact model-selected identifiers are preserved. A prefix is expanded only
    when it identifies exactly one supplied item. Missing mandatory foundations
    are then attached from the concrete upstream allowlist. This does not relax
    strategy validation: claims, identity, principles and message rules still
    fail closed.
    """
    updates: dict[str, Any] = {}
    resolved = _resolved_basis_requirements(allowed_basis)
    for name in STRATEGY_DECISIONS:
        decision = getattr(strategy, name)
        basis = _normalized_model_basis(decision.basis, allowed_basis=allowed_basis)
        for options in resolved[name]:
            if options and not any(reference in options for reference in basis):
                basis.append(options[0])
        updates[name] = decision.model_copy(update={"basis": basis})

    framework = strategy.message_framework
    framework_basis = _normalized_model_basis(
        framework.basis,
        allowed_basis=allowed_basis,
    )
    if framework.framework.value == "pas":
        _append_first_available(
            framework_basis,
            allowed_basis,
            ("audience.pain_points", "audience.customer_tension"),
        )
    elif framework.framework.value == "aida":
        _append_first_available(framework_basis, allowed_basis, ("semantic_contract.goal",))
        _append_first_available(
            framework_basis,
            allowed_basis,
            ("audience.target", "audience.segments.", "semantic_contract.audience"),
        )
    updates["message_framework"] = framework.model_copy(update={"basis": framework_basis})
    return strategy.model_copy(update=updates)


def _normalized_model_basis(
    references: list[str],
    *,
    allowed_basis: set[str],
) -> list[str]:
    normalized: list[str] = []
    for reference in references:
        selected: str | None = reference if reference in allowed_basis else None
        if selected is None and reference.endswith("."):
            matches = sorted(item for item in allowed_basis if item.startswith(reference))
            if len(matches) == 1:
                selected = matches[0]
        if selected is not None and selected not in normalized:
            normalized.append(selected)
    return normalized


def _append_first_available(
    basis: list[str],
    allowed_basis: set[str],
    requirements: tuple[str, ...],
) -> None:
    options = [
        reference
        for requirement in requirements
        for reference in sorted(allowed_basis)
        if _basis_matches(reference, requirement)
    ]
    if options and not any(reference in options for reference in basis):
        basis.append(options[0])


def _require_one_contract(payload: MarketingStrategyInput, contract: PostSemanticContract) -> None:
    """Every input must describe the same post.

    Assembling the upstream outputs is the first point in the workflow where
    their fingerprinted contracts could silently disagree, and a strategy built
    on a stale audience profile is wrong in a way nothing downstream can detect.
    """
    sources = {
        "brand": payload.brand.contract_fingerprint,
        "product": payload.product.contract_fingerprint,
        "audience": payload.audience.contract_fingerprint,
        "research": payload.research.contract_fingerprint,
    }
    drifted = sorted(name for name, value in sources.items() if value != contract.fingerprint)
    if drifted:
        raise ValueError(f"marketing strategist inputs disagree on the contract: {drifted}")


def _strategy_source(
    payload: MarketingStrategyInput,
    contract: PostSemanticContract,
) -> tuple[dict[str, Any], set[str]]:
    optional = {
        "company": contract.company,
        "brand": contract.brand,
        "product": contract.product,
        "market": contract.market,
        "location": contract.location,
        "offer": contract.offer,
    }
    source: dict[str, Any] = {
        "analysis_language": "English",
        "brief": {
            "style_preferences": list(payload.brief.style_preferences),
            "constraints": list(payload.brief.constraints),
        },
        "semantic_contract": {
            "goal": contract.goal,
            "audience": contract.audience,
            "primary_entity": contract.primary_entity,
            "platform": contract.platform,
            "cta_intent": contract.cta_intent,
            "constraints": list(contract.constraints),
            **{name: value for name, value in optional.items() if value is not None},
        },
        "brand": {
            "identity_summary": payload.brand.identity_summary,
            "verified_facts": payload.brand.verified_facts,
        },
        "product": {
            "primary_entity": payload.product.primary_entity,
            "verified_facts": payload.product.verified_facts,
            "usp_candidates": [
                {"id": f"product.usp_candidates.{index}", "text": candidate.text}
                for index, candidate in enumerate(payload.product.usp_candidates, start=1)
            ],
            "feature_benefit_value": [
                {
                    "id": f"product.feature_benefit_value.{chain.source_fact}",
                    "feature": chain.feature,
                    "benefit": chain.benefit,
                    "customer_value": chain.customer_value,
                }
                for chain in payload.product.feature_benefit_value
            ],
        },
        "audience": _audience_source(payload),
        "research": _research_source(payload),
        "forbidden_claims": list(contract.forbidden_claims),
    }

    allowed = {
        "semantic_contract.goal",
        "semantic_contract.audience",
        "semantic_contract.primary_entity",
        "semantic_contract.platform",
        "semantic_contract.cta_intent",
        "brand.identity_summary",
        "product.primary_entity",
        "audience.target",
        "audience.customer_tension",
        "audience.purchase_intent",
    }
    allowed.update(f"semantic_contract.{name}" for name, value in optional.items() if value)
    allowed.update(f"brand.verified_facts.{key}" for key in payload.brand.verified_facts)
    allowed.update(f"product.verified_facts.{key}" for key in payload.product.verified_facts)
    allowed.update(
        f"product.usp_candidates.{index}"
        for index in range(1, len(payload.product.usp_candidates) + 1)
    )
    allowed.update(
        f"product.feature_benefit_value.{chain.source_fact}"
        for chain in payload.product.feature_benefit_value
    )
    allowed.update(f"audience.segments.{segment.name}" for segment in payload.audience.segments)
    allowed.update(f"audience.{name}" for name in _AUDIENCE_INSIGHT_FIELDS)
    if payload.brief.style_preferences:
        allowed.add("brief.style_preferences")
    if payload.brief.constraints:
        allowed.add("brief.constraints")
    if contract.constraints:
        allowed.add("semantic_contract.constraints")
    allowed.update(source["research"])
    return source, allowed


#: Audience lists a strategy may cite wholesale. Citing the field is enough:
#: the strategist reasons about a need state, not about one phrasing of it.
_AUDIENCE_INSIGHT_FIELDS = (
    "needs",
    "desires",
    "pain_points",
    "objections",
    "motivation",
    "trust_triggers",
)


def _audience_source(payload: MarketingStrategyInput) -> dict[str, Any]:
    audience = payload.audience
    tension = audience.customer_tension
    return {
        "segments": [
            {
                "id": f"audience.segments.{segment.name}",
                "name": segment.name,
                "description": segment.description,
            }
            for segment in audience.segments
        ],
        "target": {"segment": audience.target.segment, "rationale": audience.target.rationale},
        "purchase_intent": audience.purchase_intent.level.value,
        "customer_tension": {
            "current_state": tension.current_state,
            "desired_state": tension.desired_state,
            "tension": tension.tension,
        },
        **{
            name: [insight.insight for insight in getattr(audience, name)]
            for name in _AUDIENCE_INSIGHT_FIELDS
        },
    }


def _research_source(payload: MarketingStrategyInput) -> dict[str, list[str]]:
    """Research evidence, keyed by the identifier a decision must cite."""
    evidence: dict[str, list[str]] = {}
    for category in ResearchCategory:
        report = getattr(payload.research, category.value)
        analysis = report.analysis
        if analysis is not None:
            for dimension in type(analysis).model_fields:
                insights = getattr(analysis, dimension, None)
                if not isinstance(insights, list) or not insights:
                    continue
                observations = [
                    _preview(insight.observation)
                    for insight in insights[:RESEARCH_OBSERVATIONS_PER_DIMENSION]
                    if hasattr(insight, "observation")
                ]
                if observations:
                    evidence[f"research.{category.value}.{dimension}"] = observations
        elif report.findings:
            evidence[f"research.{category.value}.findings"] = [
                _preview(finding.statement)
                for finding in report.findings[:RESEARCH_OBSERVATIONS_PER_DIMENSION]
            ]
    return evidence


def _preview(value: str) -> str:
    text = " ".join(value.split())
    return text if len(text) <= OBSERVATION_PREVIEW_CHARS else text[:OBSERVATION_PREVIEW_CHARS]


def _system_prompt(allowed_basis: set[str]) -> str:
    schema = json.dumps(
        MarketingStrategyLLMOutput.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    basis = json.dumps(sorted(allowed_basis), ensure_ascii=False)
    principles = json.dumps(
        {name: principle.value for name, principle in DECISION_PRINCIPLES.items()},
        sort_keys=True,
    )
    basis_requirements = json.dumps(
        _resolved_basis_requirements(allowed_basis),
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        "You are the Marketing Strategist in a marketing-post workflow. "
        "OUTPUT LANGUAGE REQUIREMENT: write every generated field in English. "
        "Decide the strategy for this post: the business objective, segmentation, targeting and "
        "positioning; the customer insight and the tension it creates; the USP and the value "
        "proposition; the marketing angle, the single-minded message and the desired reaction; "
        "and the CTA strategy. "
        "EVERY DECISION NEEDS A RATIONALE. For each field give the decision itself, the reasoning "
        "that produced it, and one or more exact basis identifiers from this allowlist: "
        f"{basis}. A decision you cannot ground in that list is a decision you must not make. "
        "Each decision also has field-specific evidence requirements. Every inner list is an OR "
        "group and every group must be satisfied by copying ONE EXACT identifier from that list: "
        f"{basis_requirements}. These are concrete identifiers, never shorten them to a prefix "
        "such as 'audience.segments.' or 'product.usp_candidates.'. "
        "Set principle on each field to exactly the value this mapping gives it, and do not "
        f"relabel a decision as a different discipline: {principles}. "
        "Targeting must name one of the supplied audience segments. The USP must descend from a "
        "supplied product usp_candidate or feature_benefit_value: it is what this product "
        "verifiably does better, never an appealing phrase. Follow the feature to benefit to "
        "customer value chain rather than restating a feature. The value proposition states what "
        "the target gains, in their terms. The customer insight is a truth about the customer, "
        "not about the product, and the customer tension must build on the supplied audience "
        "tension rather than invent a new one. The single-minded message must be ONE sentence "
        "carrying ONE idea; if it needs an 'and', it is two messages and you must choose. The "
        "desired reaction is what the reader should think, feel or do next. The CTA strategy "
        "must serve the contract's cta_intent. "
        "Choose the message framework honestly: AIDA when attention must be earned before "
        "interest, PAS when a real and stated problem can be agitated, and none when neither "
        "fits this brief. A framework forced onto a brief that does not suit it is worse than "
        "no framework. "
        "Never repeat, paraphrase, or imply a forbidden claim. Never invent facts, statistics, "
        "prices, or guarantees, and never copy or imitate a competitor. Do not write final "
        "Treat semantic_contract as authoritative when the brief is less specific, and obey all "
        "brief and semantic-contract constraints. Never introduce a new brand, product, company, "
        "offer, named entity, numeric claim, superlative, guarantee, or free benefit. "
        "advertising copy, headlines, captions, hashtags, art direction, or design instructions: "
        "those are later stages, and your job is the thinking they will execute. Write concise "
        "English while preserving supplied brand names, product names, offers and verified fact "
        "values exactly as given. Return exactly one JSON object matching this schema and no "
        f"prose or markdown: {schema}"
    )


def _resolved_basis_requirements(
    allowed_basis: set[str],
) -> dict[str, list[list[str]]]:
    return {
        name: [
            list(
                dict.fromkeys(
                    reference
                    for requirement in alternatives
                    for reference in sorted(allowed_basis)
                    if _basis_matches(reference, requirement)
                )
            )
            for alternatives in groups
        ]
        for name, groups in _DECISION_BASIS_REQUIREMENTS.items()
    }


def _validate_strategy(
    strategy: MarketingStrategyLLMOutput,
    payload: MarketingStrategyInput,
    contract: PostSemanticContract,
    allowed_basis: set[str],
    source: dict[str, Any],
) -> None:
    decisions = {name: getattr(strategy, name) for name in STRATEGY_DECISIONS}
    errors: list[str] = []
    for name, decision in decisions.items():
        for reference in decision.basis:
            if reference not in allowed_basis:
                errors.append(f"unsupported {name} basis: {reference}")
        expected = DECISION_PRINCIPLES[name]
        if decision.principle is not expected:
            errors.append(f"{name} must apply the {expected.value} principle")
        errors.extend(_field_basis_errors(name, decision))
    for reference in strategy.message_framework.basis:
        if reference not in allowed_basis:
            errors.append(f"unsupported message framework basis: {reference}")

    segment_names = {_semantic(segment.name) for segment in payload.audience.segments}
    targeting = decisions["targeting"]
    if not any(name in _semantic(targeting.decision) for name in segment_names):
        errors.append("targeting must name one of the supplied audience segments")

    if not any(
        reference.startswith(_PRODUCT_EVIDENCE_PREFIXES) for reference in decisions["usp"].basis
    ):
        errors.append("the USP must descend from a product feature or USP candidate")
    if "audience.customer_tension" not in decisions["customer_tension"].basis:
        errors.append("the customer tension must build on the audience tension")

    message = decisions["single_minded_message"].decision
    if len(_SENTENCE_END.findall(message)) > 1:
        # "Single-minded" is the whole point of the field: two sentences are
        # two messages, and a post that carries two carries neither.
        errors.append("the single-minded message must be one sentence")
    normalized_message = f" {_semantic(message)} "
    if any(marker in normalized_message for marker in _MULTI_PROMISE_MARKERS):
        errors.append("the single-minded message must not combine multiple promises")

    errors.extend(_framework_basis_errors(strategy))
    try:
        _reject_unsupported_claims(strategy, source)
    except ValueError as exc:
        errors.append(str(exc))

    forbidden = [_semantic(claim) for claim in contract.forbidden_claims]
    text_values = _all_strings(strategy.model_dump(mode="json"))
    if any(claim and claim in _semantic(value) for claim in forbidden for value in text_values):
        errors.append("marketing strategy contains a forbidden claim")
    if errors:
        raise ValueError(" | ".join(dict.fromkeys(errors)))


def _field_basis_errors(name: str, decision: StrategicDecision) -> list[str]:
    errors: list[str] = []
    for alternatives in _DECISION_BASIS_REQUIREMENTS[name]:
        if not any(
            _basis_matches(reference, alternative)
            for reference in decision.basis
            for alternative in alternatives
        ):
            errors.append(f"{name} is missing required evidence: {alternatives}")
    return errors


def _basis_matches(reference: str, requirement: str) -> bool:
    return reference == requirement or (
        requirement.endswith(".") and reference.startswith(requirement)
    )


def _framework_basis_errors(strategy: MarketingStrategyLLMOutput) -> list[str]:
    errors: list[str] = []
    framework = strategy.message_framework
    if framework.framework.value == "pas" and not any(
        _basis_matches(reference, requirement)
        for reference in framework.basis
        for requirement in ("audience.pain_points", "audience.customer_tension")
    ):
        errors.append("PAS requires a supplied pain point or customer tension")
    if framework.framework.value == "aida":
        has_goal = "semantic_contract.goal" in framework.basis
        has_audience = any(
            _basis_matches(reference, requirement)
            for reference in framework.basis
            for requirement in (
                "semantic_contract.audience",
                "audience.target",
                "audience.segments.",
            )
        )
        if not has_goal or not has_audience:
            errors.append("AIDA requires the objective and supplied audience context")
    return errors


def _reject_unsupported_claims(
    strategy: MarketingStrategyLLMOutput,
    source: dict[str, Any],
) -> None:
    source_text = _semantic(json.dumps(source, ensure_ascii=False, sort_keys=True))
    generated = [
        text
        for decision in strategy.model_dump(mode="json").values()
        if isinstance(decision, dict)
        for key, text in decision.items()
        if key in {"decision", "rationale"} and isinstance(text, str)
    ]
    for value in generated:
        if _PROHIBITED_STRATEGY_ACTION.search(value):
            raise ValueError("marketing strategy violates identity or competitor boundaries")
        if _DOWNSTREAM_EXECUTION_MARKER.search(value):
            raise ValueError("marketing strategy attempted downstream creative execution")
        for match in _NUMERIC_CLAIM.findall(value):
            if _semantic(match) not in source_text:
                raise ValueError(f"marketing strategy invented numeric claim: {match}")
        for match in _UNSUPPORTED_ABSOLUTE_CLAIM.findall(value):
            claim = match if isinstance(match, str) else " ".join(match)
            if claim and _semantic(claim) not in source_text:
                raise ValueError(f"marketing strategy invented unsupported claim: {claim}")
        _reject_unknown_named_entities(value, source_text=source_text)


def _reject_unknown_named_entities(value: str, *, source_text: str) -> None:
    for sentence in re.split(r"(?<=[.!?])\s+", value):
        leading = _LEADING_NAMED_ENTITY.search(sentence)
        if leading is not None:
            normalized = _semantic(leading.group(1))
            if normalized not in _KNOWN_STRATEGY_ACRONYMS and normalized not in source_text:
                raise ValueError(
                    "marketing strategy introduced unknown named entity: " + leading.group(1)
                )
        tokens = _CAPITALIZED_TOKEN.findall(sentence)
        for index, token in enumerate(tokens):
            normalized = _semantic(token)
            if index == 0 and not token.isupper():
                continue
            if normalized not in _KNOWN_STRATEGY_ACRONYMS and normalized not in source_text:
                raise ValueError(f"marketing strategy introduced unknown named entity: {token}")


def _ground_strategy(
    strategy: MarketingStrategyLLMOutput,
    payload: MarketingStrategyInput,
    contract: PostSemanticContract,
) -> MarketingStrategy:
    values: dict[str, Any] = {
        name: getattr(strategy, name).model_copy() for name in STRATEGY_DECISIONS
    }
    values["message_framework"] = strategy.message_framework.model_copy()
    values["limitations"] = _limitations(payload)
    values["contract_fingerprint"] = contract.fingerprint
    return MarketingStrategy.model_validate(values)


def _limitations(payload: MarketingStrategyInput) -> list[str]:
    """What this strategy did not get to stand on.

    Audience hypotheses and thin research coverage do not stop a strategy being
    made, but a later stage that cannot see the gaps will treat every decision
    as equally solid. The gaps travel with the decisions instead.
    """
    limitations: list[str] = list(payload.audience.limitations)
    for category in ResearchCategory:
        report = getattr(payload.research, category.value)
        coverage = report.evidence_coverage
        if coverage is not None and coverage.missing_dimensions:
            limitations.append(
                f"{category.value} research found no evidence for: "
                + ", ".join(coverage.missing_dimensions)
            )
        elif report.analysis is None and not report.findings:
            limitations.append(f"{category.value} research produced no usable evidence")
    unique: list[str] = []
    for limitation in limitations:
        text = " ".join(limitation.split())
        if text and text not in unique:
            unique.append(text)
    return unique[:20]


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


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _all_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _all_strings(item)]
    return []


def _semantic(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


__all__ = [
    "MARKETING_STRATEGIST_AGENT_NAME",
    "MARKETING_STRATEGIST_DEFINITION",
    "MarketingStrategistAgent",
    "StrategicDecision",
    "register_marketing_strategist_agent",
]
