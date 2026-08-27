import json
import re
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
    LLMResponse,
    ProviderResponseError,
)
from app.modules.posts.tools import ToolGateway
from app.modules.posts.tools.research import ResearchCategory

from .quality import (
    STRATEGY_SOURCE_WORDING,
    quality_gate,
    rank_candidates,
    selection_rationale,
    stabilize_repair,
    validate_exploration,
)
from .schemas import (
    CONCEPT_SELECTION_DIMENSIONS,
    QUALITY_THRESHOLDS,
    CreativeAngle,
    CreativeDirection,
    CreativeDirectorInput,
    CreativeDirectorLLMOutput,
    RejectedConcept,
    WinningConcept,
)

CREATIVE_DIRECTOR_AGENT_NAME = "creative_director"

CREATIVE_DIRECTOR_DEFINITION = AgentDefinition(
    name=CREATIVE_DIRECTOR_AGENT_NAME,
    role="Transform approved marketing strategy into bounded creative concept options",
    input_schema=CreativeDirectorInput,
    output_schema=CreativeDirection,
    allowed_tools=frozenset(),
    # The framework caps each agent at 300s; provider I/O uses the same ceiling.
    timeout_seconds=SPECIALIST_TIMEOUT_SECONDS,
    retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True, retry_on_error=True),
)

_AUDIENCE_FIELDS = (
    "needs",
    "desires",
    "pain_points",
    "objections",
    "motivation",
    "trust_triggers",
)
_RESEARCH_PREVIEW_LIMIT = 2


class CreativeDirectorAgent:
    """Explores and evaluates concepts without crossing into execution."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> CreativeDirection:
        if not isinstance(payload, CreativeDirectorInput):
            raise TypeError("creative director received an invalid input type")
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
        source, allowed_basis = _creative_source(payload, contract)
        response = await self._complete(source, allowed_basis)
        try:
            return _validated_direction(
                response.text,
                payload=payload,
                contract=contract,
                source=source,
                allowed_basis=allowed_basis,
            )
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as first_exc:
            try:
                repair, repair_limitations = await self._repair(
                    source,
                    allowed_basis,
                    previous_output=response.text,
                    validation_error=str(first_exc),
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                # Including UnsayableConcept, whose message names the fields.
                raise ProviderResponseError(
                    f"creative director could not repair its output: {exc}"
                ) from exc
        try:
            # The repaired object is held to the same bar. A fallback that only
            # strips offending tokens is how a damaged or below-bar concept
            # reaches production, so nothing is waived for being a repair.
            return _validated_direction(
                repair,
                payload=payload,
                contract=contract,
                source=source,
                allowed_basis=allowed_basis,
                extra_limitations=repair_limitations,
            )
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError(
                "creative director returned invalid structured output"
            ) from exc

    async def _repair(
        self,
        source: dict[str, Any],
        allowed_basis: set[str],
        *,
        previous_output: str,
        validation_error: str,
    ) -> tuple[str, list[str]]:
        try:
            previous = _normalize_provider_output(_parse_json_object(previous_output))
        except (json.JSONDecodeError, TypeError):
            previous = None
        if previous is None or _needs_regeneration(validation_error):
            response = await self._complete(
                source,
                allowed_basis,
                previous_output=previous_output,
                validation_error=validation_error,
            )
            try:
                repaired = _normalize_provider_output(_parse_json_object(response.text))
            except (json.JSONDecodeError, TypeError):
                return response.text, []
            stabilized, changed = stabilize_repair(repaired, source)
            return _serialized_repair(stabilized, changed)
        response = await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=_patch_correction_prompt()),
                    LLMMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "previous_output": previous,
                                "validation_error": validation_error[:5_000],
                                "correction_requirements": _correction_requirements(
                                    validation_error,
                                    source,
                                ),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ),
                ),
                temperature=0.0,
                response_format="json",
            )
        )
        repaired = _apply_provider_patch(previous, _parse_json_object(response.text))
        stabilized, changed = stabilize_repair(repaired, source)
        return _serialized_repair(stabilized, changed)

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
        temperature = 0.2
        if previous_output is not None:
            temperature = 0.0
            system += _correction_prompt()
            user["previous_output"] = previous_output[:24_000]
            user["validation_error"] = (validation_error or "invalid output")[:5_000]
            user["correction_requirements"] = _correction_requirements(
                validation_error or "invalid output",
                source,
            )
        return await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=system),
                    LLMMessage(
                        role="user",
                        content=json.dumps(user, ensure_ascii=False, sort_keys=True),
                    ),
                ),
                temperature=temperature,
                response_format="json",
            )
        )


#: Failures a field editor cannot answer. Asking for a minimal patch when the
#: whole exploration is copy returns the same copy with different punctuation,
#: so these send the work back to be thought again rather than edited.
_UNPATCHABLE_FAILURE = (
    "identical scorecards",
    "below the creative quality bar",
    "must explore different conceptual routes",
    "must be meaningfully distinct",
    "angles must be unique",
    "restates its input",
    "adds no new idea",
    "stock product shot",
    "names the advertiser",
)
#: More than this many violations at once is not a list of mistakes; it is the
#: wrong exploration.
_MAX_PATCHABLE_VIOLATIONS = 2


def _needs_regeneration(validation_error: str) -> bool:
    lowered = validation_error.casefold()
    if any(phrase in lowered for phrase in _UNPATCHABLE_FAILURE):
        return True
    return len(validation_error.split(" | ")) > _MAX_PATCHABLE_VIOLATIONS


def register_creative_director_agent(runtime: AgentRuntime, llm: LLMProvider) -> None:
    agent = CreativeDirectorAgent(llm)
    runtime.register(CREATIVE_DIRECTOR_DEFINITION, agent.execute)


def _creative_source(
    payload: CreativeDirectorInput,
    contract: PostSemanticContract,
) -> tuple[dict[str, Any], set[str]]:
    strategy = {
        name: decision.model_dump(mode="json")
        for name, decision in payload.marketing_strategy.decisions().items()
    }
    strategy["message_framework"] = payload.marketing_strategy.message_framework.model_dump(
        mode="json"
    )
    audience = {
        "target": payload.audience.target.model_dump(mode="json"),
        "customer_tension": payload.audience.customer_tension.model_dump(mode="json"),
        **{
            field: [item.insight for item in getattr(payload.audience, field)]
            for field in _AUDIENCE_FIELDS
        },
    }
    research, research_basis = _research_source(payload)
    source: dict[str, Any] = {
        "semantic_contract": {
            "company": contract.company,
            "brand": contract.brand,
            "product": contract.product,
            "primary_entity": contract.primary_entity,
            "goal": contract.goal,
            "audience": contract.audience,
            "market": contract.market,
            "location": contract.location,
            "offer": contract.offer,
            "cta_intent": contract.cta_intent,
            "platform": contract.platform,
            "language": contract.language,
            "required_facts": dict(contract.required_facts),
            "forbidden_claims": list(contract.forbidden_claims),
            "required_assets": [str(asset_id) for asset_id in contract.required_assets],
            "constraints": list(contract.constraints),
        },
        "marketing_strategy": strategy,
        "audience": audience,
        "brand": {
            "identity_summary": payload.brand.identity_summary,
            "personality_traits": list(payload.brand.personality_traits),
            "verified_facts": dict(payload.brand.verified_facts),
            "constraints": list(payload.brand.constraints),
        },
        "research": research,
        "anti_repetition": {
            "prior_rejected_concepts": list(payload.rejected_concept_memory),
            "recent_approved_creative_dna": [
                item.model_dump(mode="json") for item in payload.recent_creative_patterns
            ],
        },
    }
    allowed = {
        *(f"marketing_strategy.{name}" for name in payload.marketing_strategy.decisions()),
        "marketing_strategy.message_framework",
        "audience.target",
        "audience.customer_tension",
        *(f"audience.{field}" for field in _AUDIENCE_FIELDS),
        "brand.identity_summary",
        "brand.personality_traits",
        *(
            f"semantic_contract.{field}"
            for field in (
                "company",
                "brand",
                "product",
                "primary_entity",
                "goal",
                "audience",
                "market",
                "location",
                "offer",
                "cta_intent",
                "platform",
                "language",
            )
            if getattr(contract, field) is not None
        ),
    }
    allowed.update(f"brand.verified_facts.{key}" for key in payload.brand.verified_facts)
    allowed.update(f"semantic_contract.required_facts.{key}" for key in contract.required_facts)
    if contract.constraints:
        allowed.add("semantic_contract.constraints")
    if payload.brand.constraints:
        allowed.add("brand.constraints")
    allowed.update(research_basis)
    return source, allowed


def _research_source(payload: CreativeDirectorInput) -> tuple[dict[str, list[str]], set[str]]:
    evidence: dict[str, list[str]] = {}
    basis: set[str] = set()
    for category in ResearchCategory:
        report = getattr(payload.research, category.value)
        analysis = report.analysis
        if analysis is not None:
            for dimension in type(analysis).model_fields:
                insights = getattr(analysis, dimension, None)
                if not isinstance(insights, list) or not insights:
                    continue
                key = f"research.{category.value}.{dimension}"
                values = [
                    " ".join(item.observation.split())[:300]
                    for item in insights[:_RESEARCH_PREVIEW_LIMIT]
                    if hasattr(item, "observation")
                ]
                if values:
                    evidence[key] = values
                    basis.add(key)
        elif report.findings:
            key = f"research.{category.value}.findings"
            evidence[key] = [
                " ".join(item.statement.split())[:300]
                for item in report.findings[:_RESEARCH_PREVIEW_LIMIT]
            ]
            basis.add(key)
    return evidence, basis


def _validated_direction(
    raw_output: str,
    *,
    payload: CreativeDirectorInput,
    contract: PostSemanticContract,
    source: dict[str, Any],
    allowed_basis: set[str],
    extra_limitations: list[str] | None = None,
) -> CreativeDirection:
    provider_output = _normalize_provider_output(_parse_json_object(raw_output))
    exploration = CreativeDirectorLLMOutput.model_validate(provider_output)
    validate_exploration(
        exploration,
        payload=payload,
        contract=contract,
        source=source,
        allowed_basis=allowed_basis,
    )
    ranked = rank_candidates(exploration.big_idea_candidates)
    selected, runner_up = ranked[0], ranked[1]
    gate = quality_gate(selected)
    if gate.failures:
        raise ValueError(
            f"{selected.id} is below the creative quality bar: "
            + ", ".join(
                f"{check.dimension} {check.score} below {check.threshold}"
                for check in gate.failures
            )
        )
    territories = {item.id: item for item in exploration.creative_territories}
    hooks = {item.id: item for item in exploration.visual_hooks}
    limitations = _limitations(payload)
    limitations.extend(extra_limitations or [])
    rationale = selection_rationale(
        selected,
        runner_up,
        territories[selected.territory_id],
        hooks[selected.visual_hook_id],
    )
    return CreativeDirection(
        **exploration.model_dump(mode="json"),
        winning_concept=WinningConcept(
            candidate_id=selected.id,
            total_score=selected.evaluation.total,
            rationale=rationale,
        ),
        rejected_concepts=[
            RejectedConcept(
                candidate_id=candidate.id,
                rank=rank,
                total_score=candidate.evaluation.total,
                rejection_reason=_rejection_reason(selected, candidate),
                weakness=candidate.evaluation.weakness,
            )
            for rank, candidate in enumerate(ranked[1:], start=2)
        ],
        creative_rationale=rationale,
        quality_gate=gate,
        limitations=list(dict.fromkeys(limitations))[:20],
        contract_fingerprint=contract.fingerprint,
    )


def _rejection_reason(selected: Any, rejected: Any) -> str:
    winning_scores = selected.evaluation.selection_scores()
    rejected_scores = rejected.evaluation.selection_scores()
    deficits = sorted(
        (
            (winning_scores[name] - rejected_scores[name], name)
            for name in CONCEPT_SELECTION_DIMENSIONS
            if winning_scores[name] > rejected_scores[name]
        ),
        reverse=True,
    )
    comparison = ", ".join(
        f"{name.replace('_', ' ')} {rejected_scores[name]} versus {winning_scores[name]}"
        for _, name in deficits[:3]
    )
    if not comparison:
        comparison = "a lower total across the eight selection dimensions"
    return (
        f"Rejected behind {selected.name} because it has {comparison}. "
        f"Known weakness: {rejected.evaluation.weakness}"
    )[:1_000]


def _limitations(payload: CreativeDirectorInput) -> list[str]:
    values = [*payload.marketing_strategy.limitations, *payload.audience.limitations]
    return list(dict.fromkeys(" ".join(value.split()) for value in values if value.strip()))[:20]


def _system_prompt(allowed_basis: set[str]) -> str:
    schema = json.dumps(
        CreativeDirectorLLMOutput.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    basis = json.dumps(sorted(allowed_basis), ensure_ascii=False)
    angles = ", ".join(angle.value for angle in CreativeAngle)
    thresholds = ", ".join(f"{name} {value}" for name, value in QUALITY_THRESHOLDS.items())
    return (
        "You are the Creative Director in a marketing-post workflow. OUTPUT LANGUAGE: English. "
        "Transform the approved marketing strategy into 3-5 genuinely distinct creative "
        "territories, 3-5 visual hooks and 3-5 Big Idea candidates. Explore before converging.\n"
        "TERRITORIES. Give every territory a different angle from this list, and never repeat "
        f"an angle: {angles}. emotional_transformation follows a feeling changing state; "
        "visual_metaphor carries the strategy through one image that means something else; "
        "cultural_tension enters through a human or cultural contradiction; "
        "product_demonstration proves the capability through what it visibly does; "
        "brand_symbol builds an ownable symbol for the brand. Three routes that all argue the "
        "same promise with different adjectives are one route. Each territory needs a premise, "
        "the creative tension between the audience's current and desired state, a "
        "strategic_link saying what it does to the approved marketing angle rather than "
        "repeating it, a 2-8 word mood vocabulary and a rationale.\n"
        "BIG IDEAS. A Big Idea is a concept many executions can hang from, not this one post. "
        "It is not a headline, a call to action, a repeated USP, a benefit statement, an "
        "instruction to the audience or a paraphrase of the marketing message. Round-the-clock "
        "support is a benefit; what that support means to someone is where an idea starts. "
        "Write it in the third person, at least eight words, and never inside it name the "
        "brand or the company: a sentence that says who is advertising is a slogan, not an "
        "idea. Prove it travels by listing at least two and at most six extensions: other "
        "executions the same idea would carry, each a different one. Each candidate names one "
        "territory and one hook, a territory_link saying what the idea adds to the territory, "
        "a hook_link saying how the image carries the idea, and production_notes on making it "
        "with approved assets without becoming the layout.\n"
        "HOOKS. A visual hook must be understandable with every word removed, hold one clear "
        "transformation or symbol, stop a scroll and belong to its Big Idea. State the symbol "
        "as the single element the image turns on, in a few concrete words. The wordless_read "
        "is not a line anyone reads: it is what a viewer understands from the picture alone, "
        "so it cannot contain a brand name, a promise or a sentence aimed at the audience. A "
        "person standing next to a car is not a hook. Use every hook exactly once across the "
        "candidates; a hook no Big Idea claims is wasted work.\n"
        "CHAIN. Audience tension leads to marketing angle, angle to territory, territory to "
        "Big Idea, Big Idea to visual hook. Every link must interpret the one before it and "
        "add something it did not say. Restating a link in new words breaks the chain.\n"
        "SCORING. Rank concepts only on these eight 1-10 dimensions: strategy_fit, "
        "audience_fit, brand_fit, originality, clarity, visual_potential, platform_fit and "
        "production_feasibility. Also score the quality gates territory_differentiation, "
        "claim_safety and concept_hook_alignment, and name the candidate's real weakness. "
        "Identical scorecards "
        "are not an evaluation and a flawless scorecard is not credible: every candidate must "
        "carry at least one honest weakness, and the cards must separate the routes. The "
        f"concept that wins is held to these minimums: {thresholds}. Do not raise a score to "
        "reach them; only a stronger concept clears them.\n"
        "ANTI-REPETITION. The source may contain prior_rejected_concepts and "
        "recent_approved_creative_dna. Do not rename, rephrase or recreate rejected routes. "
        "Treat approved DNA as AVOID guidance for its repeated concept, visual hook, layout, "
        "graphic, typography, color, composition, CTA and logo-placement patterns. Preserve "
        "brand and marketing fit; do not become different merely to be different.\n"
        "EVIDENCE. Every item needs at least two exact basis identifiers from this allowlist: "
        f"{basis}. Every item must cite marketing_strategy; territories must also cite audience "
        "evidence; hooks must also cite brand, research or platform evidence. Research informs "
        "originality but never authorizes copying a competitor.\n"
        "BOUNDARIES. Preserve the semantic contract, product and brand identity, required "
        "facts, required assets and constraints. Never introduce a new brand, product, offer, "
        "statistic or guarantee. Never promise zero waiting, instant service, service without "
        "delay, permanent availability or any guarantee unless that exact claim exists "
        "upstream; a verified round-the-clock fact may only be used with its verified meaning. "
        "Do not write a headline, subheadline, caption, CTA copy, hashtag, typography, font, "
        "color code, pixel dimension, poster layout, image-generation prompt or final poster. "
        "Those belong to later specialists. Describe concepts rather than instructing the "
        "audience to book, trust, choose, ensure, get, start or transform, or asking them to "
        "count on the brand. A visual hook may describe conceptual motion or transformation "
        "but not split screens, overlays, logo or tagline placement, or layout.\n"
        "Use IDs territory_1..5, hook_1..5 and idea_1..5, and use each territory and hook "
        "exactly once. Return exactly one JSON object matching this schema and no prose or "
        "markdown. Never create a separate big_idea_candidates_evaluation field; evaluation "
        f"belongs inside each candidate. Schema: {schema}"
    )


def _correction_prompt() -> str:
    """Put local-model repair rules at the end of the system message."""
    return (
        " CORRECTION PASS: the previous response failed deterministic validation. "
        "Return the complete corrected JSON object, not a patch, and fix every listed "
        "violation without weakening strategy, identity or execution boundaries. Treat every "
        "territory or idea ID named in validation_error as mandatory to rewrite. Territory "
        "premises and Big Idea descriptions must state third-person conceptual transformations "
        "or metaphors, not address or command the audience. Never open them with Book, Choose, "
        "Discover, Enjoy, Ensure your, Experience, Get, Start, Transform, Trust, or Count on us. "
        "Do not preserve wording merely because its score is high. No reusable creative example "
        "is supplied: generate original language from the approved source. Re-check every Big "
        "Idea against the approved strategy wording and replace close paraphrases with a "
        "genuinely interpretive concept. Every corrected field must be a complete, grammatical "
        "sentence. If a concept cannot clear the quality minimums, replace the concept; raising "
        "its scores is not a correction."
    )


def _patch_correction_prompt() -> str:
    return (
        "You are a strict JSON correction editor. Return only a small JSON patch with any of "
        "these optional arrays: creative_territories, visual_hooks, big_idea_candidates. Include "
        "only invalid items, identified by their existing id, and only fields that must change. "
        "Do not regenerate valid items. Do not change IDs or references. Nested evaluation may "
        "contain only scores that need correction. Never introduce headlines, captions, CTA "
        "copy, typography, layout, overlays, split screens, image prompts, unsupported numbers, "
        "instant-service language, guarantees, or competitor imitation. Do not introduce any "
        "number that was absent from the field being replaced. Every replacement must be a "
        "complete, grammatical sentence. Fix every validation_error and obey every "
        "correction_requirement. Output one JSON object and no prose or markdown."
    )


def _correction_requirements(
    validation_error: str,
    source: dict[str, Any],
) -> list[str]:
    """Translate deterministic failures into explicit local-model edit targets."""
    requirements = [
        "Return only the JSON format required by the system message; add no commentary.",
        "Rewrite every field named by validation_error, including every named territory "
        "or idea ID.",
        "Write territory premises and Big Ideas as third-person conceptual transformations.",
        "Do not begin a premise or Big Idea with Book, Choose, Discover, Enjoy, Ensure your, "
        "Experience, Get, Start, Transform, Trust, or Count on us.",
        "Do not introduce split screens, overlays, layouts, image prompts, unsupported numbers, "
        "instant-service wording, guarantees, or competitor imitation in any replacement.",
        "Preserve all required IDs, references, valid basis identifiers, and nested evaluations.",
    ]
    blocked = re.findall(
        r"invented (?:numeric|unsupported absolute) claim:\s*([^|]+)",
        validation_error,
        flags=re.IGNORECASE,
    )
    if blocked:
        exact = ", ".join(dict.fromkeys(value.strip() for value in blocked))
        requirements.append(
            "Remove these exact unsupported tokens or phrases from every output field: "
            f"{exact}. Do not replace them with synonymous guarantees."
        )
    if "must creatively interpret" in validation_error:
        for label, decision in _named_strategy_wording(validation_error, source):
            requirements.append(
                f"The approved marketing {label} is strategic input, not reusable creative "
                f"wording: {decision!r}. Rewrite each named idea through a distinct metaphor "
                "or transformation and avoid its sentence structure."
            )
    if "restates its input" in validation_error or "adds no new idea" in validation_error:
        requirements.append(
            "Each named link must interpret the step above it and introduce at least one idea "
            "that step does not contain. Repeating it in new words is not a correction."
        )
    if "stock product shot" in validation_error or "wordless read" in validation_error:
        requirements.append(
            "Rebuild each named hook around one symbol or transformation that is understood "
            "with every word removed. A person beside the product is not a hook."
        )
    if "not a Big Idea" in validation_error or "written as a headline" in validation_error:
        requirements.append(
            "Each named Big Idea must be a concept in at least eight third-person words that "
            "further executions can hang from, with extensions that show it travelling."
        )
    if "names the advertiser" in validation_error or "written as copy" in validation_error:
        requirements.append(
            "Remove the brand and company name from every named Big Idea and wordless read, "
            "and rewrite them as what the concept means and what the picture shows."
        )
    if "identical scorecards" in validation_error:
        requirements.append(
            "Re-evaluate every candidate independently; give each route at least one credible "
            "tradeoff and do not return repeated score vectors."
        )
    if "below the creative quality bar" in validation_error:
        requirements.append(
            "The named candidate did not clear the quality minimums. Replace the weak concept "
            "with a stronger one; raising a score without changing the work is not a fix."
        )
    if "lost its grammar" in validation_error:
        requirements.append(
            "Rewrite each damaged field as a complete, grammatical sentence that keeps the "
            "original meaning instead of leaving the deleted words behind."
        )
    return requirements


def _named_strategy_wording(
    validation_error: str,
    source: dict[str, Any],
) -> list[tuple[str, str]]:
    strategy = source.get("marketing_strategy")
    if not isinstance(strategy, dict):
        return []
    named = [
        name
        for name in STRATEGY_SOURCE_WORDING
        if name.replace("_", " ") in validation_error.lower()
    ]
    wording: list[tuple[str, str]] = []
    for name in named or ["single_minded_message"]:
        entry = strategy.get(name)
        decision = entry.get("decision") if isinstance(entry, dict) else None
        if isinstance(decision, str) and decision.strip():
            wording.append((name.replace("_", " "), decision))
    return wording


#: What a correction patch is allowed to touch. The `id` is the address the
#: patch is written against, so it can never move; the references can, because
#: a missing or duplicated hook link is precisely the kind of drift a patch is
#: asked to fix, and full validation still proves they point at real items.
_PATCHABLE_FIELDS = {
    "creative_territories": {
        "angle",
        "name",
        "premise",
        "creative_tension",
        "strategic_link",
        "mood",
        "rationale",
        "basis",
    },
    "visual_hooks": {
        "description",
        "symbol",
        "wordless_read",
        "mechanism",
        "rationale",
        "basis",
    },
    "big_idea_candidates": {
        "name",
        "idea",
        "territory_id",
        "visual_hook_id",
        "territory_link",
        "hook_link",
        "extensions",
        "production_notes",
        "rationale",
        "basis",
        "evaluation",
    },
}


def _apply_provider_patch(
    previous: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge a bounded ID-addressed provider patch without permitting structural drift."""
    # Some providers ignore patch instructions and return a complete corrected object.
    if all(
        isinstance(patch.get(group_name), list) and len(patch[group_name]) >= 3
        for group_name in _PATCHABLE_FIELDS
    ):
        return patch
    unsupported_groups = set(patch) - set(_PATCHABLE_FIELDS)
    if unsupported_groups:
        raise ValueError(
            "creative correction patch contains unsupported groups: "
            + ", ".join(sorted(unsupported_groups))
        )
    merged = json.loads(json.dumps(previous))
    for group_name, allowed_fields in _PATCHABLE_FIELDS.items():
        edits = patch.get(group_name, [])
        if not isinstance(edits, list):
            raise TypeError(f"creative correction patch {group_name} must be a list")
        targets = merged.get(group_name)
        if not isinstance(targets, list):
            raise TypeError(f"previous output {group_name} must be a list")
        by_id = {
            item.get("id"): item
            for item in targets
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            target_id = _canonical_patch_id(group_name, edit.get("id"), by_id)
            if target_id is None:
                # A patch cannot create an item. Ignore provider drift and let final
                # validation decide whether the useful edits were sufficient.
                continue
            changed = set(edit) - {"id"}
            unsupported_fields = changed - allowed_fields
            if unsupported_fields:
                raise ValueError(
                    f"creative correction patch cannot change {group_name} fields: "
                    + ", ".join(sorted(unsupported_fields))
                )
            target = by_id[target_id]
            for field in changed:
                if field == "evaluation" and isinstance(edit[field], dict):
                    current = target.get(field)
                    if current is None:
                        target[field] = dict(edit[field])
                    elif not isinstance(current, dict):
                        raise TypeError("candidate evaluation must be an object")
                    else:
                        current.update(edit[field])
                else:
                    target[field] = edit[field]
    return merged


def _canonical_patch_id(
    group_name: str,
    value: Any,
    existing: dict[str, dict[str, Any]],
) -> str | None:
    if isinstance(value, str) and value in existing:
        return value
    match = re.search(r"([1-5])\s*$", str(value))
    if match is None:
        return None
    prefix = {
        "creative_territories": "territory",
        "visual_hooks": "hook",
        "big_idea_candidates": "idea",
    }[group_name]
    candidate = f"{prefix}_{match.group(1)}"
    return candidate if candidate in existing else None


def _serialized_repair(
    repaired: dict[str, Any],
    changed: bool,
) -> tuple[str, list[str]]:
    limitations = []
    if changed:
        limitations.append(
            "The local model's correction required deterministic safety and concept "
            "stabilization; human creative review is required before downstream execution."
        )
    return json.dumps(repaired, ensure_ascii=False, sort_keys=True), limitations


#: Renamings local models reach for. Only the key is corrected; the value the
#: provider wrote is never rewritten here.
_FIELD_ALIASES = {
    "creative_territories": {
        "angle_type": "angle",
        "territory_angle": "angle",
        "lens": "angle",
        "strategy_link": "strategic_link",
        "angle_link": "strategic_link",
    },
    "visual_hooks": {
        "central_symbol": "symbol",
        "visual_symbol": "symbol",
        "wordless": "wordless_read",
        "no_text_read": "wordless_read",
        "silent_read": "wordless_read",
    },
    "big_idea_candidates": {
        "territory_alignment": "territory_link",
        "hook_alignment": "hook_link",
        "series_potential": "extensions",
        "executions": "extensions",
        "feasibility_notes": "production_notes",
        "production": "production_notes",
    },
}

_ANGLE_SYNONYMS = {
    "emotion": CreativeAngle.EMOTIONAL_TRANSFORMATION,
    "emotional": CreativeAngle.EMOTIONAL_TRANSFORMATION,
    "transformation": CreativeAngle.EMOTIONAL_TRANSFORMATION,
    "metaphor": CreativeAngle.VISUAL_METAPHOR,
    "visual": CreativeAngle.VISUAL_METAPHOR,
    "cultural": CreativeAngle.CULTURAL_TENSION,
    "culture": CreativeAngle.CULTURAL_TENSION,
    "tension": CreativeAngle.CULTURAL_TENSION,
    "demonstration": CreativeAngle.PRODUCT_DEMONSTRATION,
    "product": CreativeAngle.PRODUCT_DEMONSTRATION,
    "symbol": CreativeAngle.BRAND_SYMBOL,
    "brand": CreativeAngle.BRAND_SYMBOL,
}


def _normalize_provider_output(value: dict[str, Any]) -> dict[str, Any]:
    """Repair harmless local-model serialization drift before strict validation."""
    normalized = dict(value)
    candidates = normalized.get("big_idea_candidates")
    detached = normalized.pop("big_idea_candidates_evaluation", None)
    if (
        isinstance(candidates, list)
        and isinstance(detached, list)
        and len(candidates) == len(detached)
    ):
        normalized["big_idea_candidates"] = [
            {**candidate, "evaluation": evaluation}
            if isinstance(candidate, dict) and "evaluation" not in candidate
            else candidate
            for candidate, evaluation in zip(candidates, detached, strict=True)
        ]

    groups = (
        ("creative_territories", "audience.customer_tension", "audience."),
        (
            "visual_hooks",
            "brand.identity_summary",
            ("brand.", "research.", "semantic_contract.platform"),
        ),
        ("big_idea_candidates", "audience.target", None),
    )
    aliases = {"brand.positioning": "marketing_strategy.positioning"}
    for group_name, secondary_basis, required_prefix in groups:
        group = normalized.get(group_name)
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            _rename_fields(item, _FIELD_ALIASES[group_name])
            if group_name == "creative_territories":
                _normalize_angle(item)
            if group_name == "big_idea_candidates" and isinstance(item.get("extensions"), str):
                item["extensions"] = _split_extensions(item["extensions"])
            if "basis" not in item:
                item["basis"] = [
                    "marketing_strategy.marketing_angle",
                    secondary_basis,
                ]
            if not isinstance(item.get("basis"), list):
                continue
            basis = [aliases.get(reference, reference) for reference in item["basis"]]
            if not any(
                isinstance(reference, str) and reference.startswith("marketing_strategy.")
                for reference in basis
            ):
                basis.append("marketing_strategy.marketing_angle")
            if required_prefix is not None and not any(
                isinstance(reference, str) and reference.startswith(required_prefix)
                for reference in basis
            ):
                basis.append(secondary_basis)
            if len(dict.fromkeys(basis)) < 2:
                basis.append(secondary_basis)
            item["basis"] = list(dict.fromkeys(basis))
    return normalized


def _rename_fields(item: dict[str, Any], aliases: dict[str, str]) -> None:
    for alias, field in aliases.items():
        if alias in item and field not in item:
            item[field] = item.pop(alias)


def _normalize_angle(item: dict[str, Any]) -> None:
    """Accept the label the provider used; never choose the angle for it."""
    value = item.get("angle")
    if not isinstance(value, str):
        return
    slug = "_".join(re.findall(r"[a-z]+", value.casefold()))
    if slug in {angle.value for angle in CreativeAngle}:
        item["angle"] = slug
        return
    for word in slug.split("_"):
        synonym = _ANGLE_SYNONYMS.get(word)
        if synonym is not None:
            item["angle"] = synonym.value
            return


def _split_extensions(value: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[;\n|]|(?<=[.!?])\s+", value)]
    return [part for part in parts if part]


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


__all__ = [
    "CREATIVE_DIRECTOR_AGENT_NAME",
    "CREATIVE_DIRECTOR_DEFINITION",
    "CreativeDirectorAgent",
    "register_creative_director_agent",
]
