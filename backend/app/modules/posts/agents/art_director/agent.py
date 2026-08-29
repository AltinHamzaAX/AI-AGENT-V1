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

from .quality import validate_and_measure_art_direction
from .schemas import ArtDirection, ArtDirectionLLMOutput, ArtDirectorInput

ART_DIRECTOR_AGENT_NAME = "art_director"

ART_DIRECTOR_DEFINITION = AgentDefinition(
    name=ART_DIRECTOR_AGENT_NAME,
    role="Transform approved concept and copy into a production-ready visual direction",
    input_schema=ArtDirectorInput,
    output_schema=ArtDirection,
    allowed_tools=frozenset(),
    timeout_seconds=SPECIALIST_TIMEOUT_SECONDS,
    retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True, retry_on_error=True),
)


class ArtDirectorAgent:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> ArtDirection:
        if not isinstance(payload, ArtDirectorInput):
            raise TypeError("art director received an invalid input type")
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
        source = _art_source(payload)
        response = await self._complete(source, contract)
        try:
            return _validated_direction(response.text, payload=payload, contract=contract)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._complete(
                source,
                contract,
                previous_output=response.text,
                validation_error=str(first_exc),
            )
        try:
            return _validated_direction(repair.text, payload=payload, contract=contract)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError("art director returned invalid visual direction") from exc

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
                " CORRECTION PASS: return the complete corrected JSON object. Do not patch by "
                "changing approved copy, product identity, logo identity or semantic facts."
            )
            user["previous_output"] = previous_output[:14_000]
            user["validation_error"] = (validation_error or "invalid direction")[:4_000]
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


def register_art_director_agent(runtime: AgentRuntime, llm: LLMProvider) -> None:
    agent = ArtDirectorAgent(llm)
    runtime.register(ART_DIRECTOR_DEFINITION, agent.execute)


def _art_source(payload: ArtDirectorInput) -> dict[str, Any]:
    candidates = {item.id: item for item in payload.concept.big_idea_candidates}
    winner = candidates[payload.concept.winning_concept.candidate_id]
    territory = next(
        item for item in payload.concept.creative_territories if item.id == winner.territory_id
    )
    hook = next(
        item for item in payload.concept.visual_hooks if item.id == winner.visual_hook_id
    )
    return {
        "winning_concept": winner.model_dump(mode="json"),
        "creative_territory": territory.model_dump(mode="json"),
        "visual_hook": hook.model_dump(mode="json"),
        "copy": payload.copy_draft.model_dump(mode="json"),
        "brand": payload.brand.model_dump(mode="json"),
        "asset_policies": [item.model_dump(mode="json") for item in payload.assets.assets],
        "platform": payload.platform,
        "semantic_contract": payload.semantic_contract,
    }


def _validated_direction(
    raw_output: str,
    *,
    payload: ArtDirectorInput,
    contract: PostSemanticContract,
) -> ArtDirection:
    output = ArtDirectionLLMOutput.model_validate(_parse_json_object(raw_output))
    quality = validate_and_measure_art_direction(output, payload=payload)
    return ArtDirection(
        **output.model_dump(mode="json"),
        quality=quality,
        contract_fingerprint=contract.fingerprint,
    )


def _system_prompt(contract: PostSemanticContract) -> str:
    schema = json.dumps(ArtDirectionLLMOutput.model_json_schema(), sort_keys=True)
    return (
        "You are the Art Director in a marketing-post workflow. Transform the approved winning "
        "concept and copy into visual direction for "
        f"{contract.platform}. Return focal_point, composition, visual_hierarchy, "
        "product_dominance, negative_space, photography_direction, lighting, "
        "typography_direction, color_direction, graphic_language, CTA_treatment and "
        "logo_region. Product must rank first, headline next, approved offer before CTA when "
        "one exists, and logo last. Ranks must be consecutive. Reserve explicit negative space "
        "for approved text. Express product dominance as a 0-1 share of visual attention and "
        "obey every supplied asset-policy range. Preserve original product and logo identity; "
        "never replace, redesign, regenerate or synthesize them. Carry the winning visual hook "
        "rather than inventing a second concept. Define photographic framing and lighting, not "
        "an image-generation prompt. Typography direction describes hierarchy and character, "
        "not rewritten copy. Color direction uses verified brand language and must not invent "
        "hex values. CTA needs readable contrast on mobile. Logo region must be clear, quiet and "
        "inside a protected safe area. Do not generate an image, final layout, final SVG, CSS, "
        "headline, offer, CTA or logo. Return exactly one JSON object and no markdown. Schema: "
        f"{schema}"
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
    "ART_DIRECTOR_AGENT_NAME",
    "ART_DIRECTOR_DEFINITION",
    "ArtDirectorAgent",
    "register_art_director_agent",
]
