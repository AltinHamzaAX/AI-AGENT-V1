import json
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

from .quality import validate_and_measure_copy
from .schemas import CopyDraft, CopywriterInput, CopywriterLLMOutput

COPYWRITER_AGENT_NAME = "copywriter"

COPYWRITER_DEFINITION = AgentDefinition(
    name=COPYWRITER_AGENT_NAME,
    role="Write platform-ready copy from approved strategy and winning concept",
    input_schema=CopywriterInput,
    output_schema=CopyDraft,
    allowed_tools=frozenset(),
    timeout_seconds=SPECIALIST_TIMEOUT_SECONDS,
    retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True, retry_on_error=True),
)


class CopywriterAgent:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> CopyDraft:
        if not isinstance(payload, CopywriterInput):
            raise TypeError("copywriter received an invalid input type")
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
        source = _copy_source(payload)
        response = await self._complete(source, contract)
        try:
            return _validated_copy(response.text, payload=payload, contract=contract)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._complete(
                source,
                contract,
                previous_output=response.text,
                validation_error=str(first_exc),
            )
        try:
            return _validated_copy(repair.text, payload=payload, contract=contract)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError("copywriter returned invalid or unsupported copy") from exc

    async def _complete(
        self,
        source: dict[str, Any],
        contract: PostSemanticContract,
        *,
        previous_output: str | None = None,
        validation_error: str | None = None,
    ) -> LLMResponse:
        system = _system_prompt(contract)
        user: dict[str, Any] = {"source": source}
        if previous_output is not None:
            system += (
                " CORRECTION PASS: return the complete corrected JSON object, not a patch. "
                "Do not solve a claim violation by inventing a different claim."
            )
            user["previous_output"] = previous_output[:10_000]
            user["validation_error"] = (validation_error or "invalid copy")[:3_000]
        return await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=system),
                    LLMMessage(
                        role="user",
                        content=json.dumps(user, ensure_ascii=False, sort_keys=True),
                    ),
                ),
                temperature=0.1 if previous_output is None else 0.0,
                response_format="json",
            )
        )


def register_copywriter_agent(runtime: AgentRuntime, llm: LLMProvider) -> None:
    agent = CopywriterAgent(llm)
    runtime.register(COPYWRITER_DEFINITION, agent.execute)


def _copy_source(payload: CopywriterInput) -> dict[str, Any]:
    candidates = {item.id: item for item in payload.concept.big_idea_candidates}
    winner = candidates[payload.concept.winning_concept.candidate_id]
    territories = {item.id: item for item in payload.concept.creative_territories}
    hooks = {item.id: item for item in payload.concept.visual_hooks}
    return {
        "strategy": payload.strategy.model_dump(mode="json"),
        "winning_concept": winner.model_dump(mode="json"),
        "winning_territory": territories[winner.territory_id].model_dump(mode="json"),
        "winning_visual_hook": hooks[winner.visual_hook_id].model_dump(mode="json"),
        "selection_rationale": payload.concept.winning_concept.rationale,
        "brand_voice": {
            "identity_summary": payload.brand_voice.identity_summary,
            "personality_traits": list(payload.brand_voice.personality_traits),
            "constraints": list(payload.brand_voice.constraints),
        },
        "platform": payload.platform,
        "offer": payload.offer,
        "semantic_contract": payload.semantic_contract,
    }


def _validated_copy(
    raw_output: str,
    *,
    payload: CopywriterInput,
    contract: PostSemanticContract,
) -> CopyDraft:
    output = CopywriterLLMOutput.model_validate(_parse_json_object(raw_output))
    quality = validate_and_measure_copy(output, payload=payload, contract=contract)
    return CopyDraft(
        **output.model_dump(mode="json"),
        quality=quality,
        contract_fingerprint=contract.fingerprint,
    )


def _system_prompt(contract: PostSemanticContract) -> str:
    schema = json.dumps(CopywriterLLMOutput.model_json_schema(), sort_keys=True)
    offer_rule = (
        f"Preserve this approved offer exactly in offer_copy: {contract.offer!r}."
        if contract.offer is not None
        else "There is no approved offer; return offer_copy as null."
    )
    return (
        "You are the Copywriter in a marketing-post workflow. Write in "
        f"{contract.language}. Use only the approved strategy, winning concept, brand voice, "
        "platform and semantic contract supplied by the application. Produce headline, "
        "subheadline, supporting_copy, offer_copy, CTA, caption and optional hashtags. "
        "The headline must carry one idea in at most 12 words. CTA must be at most 6 words. "
        "Keep sentences at 30 words or fewer, supporting copy at 240 characters, and overlay "
        "copy sparse enough for mobile. Match the supplied brand personality without shouting, "
        "excessive capitalization or more than one exclamation mark. Caption and supporting "
        "copy must be complete grammatical sentences. "
        f"{offer_rule} Never invent a price, percentage, statistic, feature, guarantee, free "
        "benefit, superlative, availability claim, brand, product or offer. Never use a forbidden "
        "claim. Hashtags must be relevant single tokens and are optional. Return exactly one JSON "
        f"object matching this schema, with no prose or markdown: {schema}"
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


__all__ = [
    "COPYWRITER_AGENT_NAME",
    "COPYWRITER_DEFINITION",
    "CopywriterAgent",
    "register_copywriter_agent",
]
