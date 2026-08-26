import json
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

from .schemas import DesignSpec, DesignSpecBody, DesignSpecInput

DESIGN_SPEC_AGENT_NAME = "design_spec_compiler"

DESIGN_SPEC_DEFINITION = AgentDefinition(
    name=DESIGN_SPEC_AGENT_NAME,
    role="Compile approved art direction into a versioned machine-readable design spec",
    input_schema=DesignSpecInput,
    output_schema=DesignSpec,
    allowed_tools=frozenset(),
    timeout_seconds=180,
    retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True, retry_on_error=True),
)


class DesignSpecAgent:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> DesignSpec:
        if not isinstance(payload, DesignSpecInput):
            raise TypeError("design spec compiler received an invalid input type")
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
        response = await self._complete(payload, contract)
        try:
            return _validated_spec(response.text, payload=payload, contract=contract)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._complete(
                payload,
                contract,
                previous_output=response.text,
                validation_error=str(first_exc),
            )
        try:
            return _validated_spec(repair.text, payload=payload, contract=contract)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError("design spec compiler returned invalid geometry") from exc

    async def _complete(
        self,
        payload: DesignSpecInput,
        contract: PostSemanticContract,
        *,
        previous_output: str | None = None,
        validation_error: str | None = None,
    ) -> LLMResponse:
        system = _system_prompt(contract)
        user: dict[str, Any] = {
            "art_direction": payload.art_direction.model_dump(mode="json"),
            "approved_copy": payload.copy_draft.model_dump(mode="json"),
            "platform": contract.platform,
        }
        if previous_output is not None:
            system += " CORRECTION PASS: return the complete corrected DesignSpec JSON."
            user["previous_output"] = previous_output[:16_000]
            user["validation_error"] = (validation_error or "invalid spec")[:4_000]
        return await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=system),
                    LLMMessage(
                        role="user",
                        content=json.dumps(user, ensure_ascii=False, sort_keys=True),
                    ),
                ),
                temperature=0.0,
                response_format="json",
            )
        )


def register_design_spec_agent(runtime: AgentRuntime, llm: LLMProvider) -> None:
    runtime.register(DESIGN_SPEC_DEFINITION, DesignSpecAgent(llm).execute)


def _validated_spec(
    raw_output: str,
    *,
    payload: DesignSpecInput,
    contract: PostSemanticContract,
) -> DesignSpec:
    body = DesignSpecBody.model_validate(_parse_json_object(raw_output))
    has_offer = payload.copy_draft.offer_copy is not None
    has_offer_region = body.regions.offer_region is not None
    has_offer_type = any(role.role == "offer" for role in body.typography_roles)
    if has_offer != has_offer_region or has_offer != has_offer_type:
        raise ValueError("offer region and typography must match the approved copy")
    return DesignSpec(
        **body.model_dump(mode="json"), contract_fingerprint=contract.fingerprint
    )


def _system_prompt(contract: PostSemanticContract) -> str:
    schema = json.dumps(DesignSpecBody.model_json_schema(), sort_keys=True)
    return (
        "You compile approved art direction into a deterministic DesignSpec for a Composer. "
        f"Target platform: {contract.platform}. Return schema_version 1.0, pixel canvas, safe "
        "area, grid, typed regions, typography roles, color tokens, graphic elements, "
        "photography, lighting and background. Every region must fit the canvas; headline, "
        "offer, CTA and logo must fit entirely inside the safe area. Product may bleed to an "
        "edge. Include an offer region and offer typography only when approved offer copy "
        "exists. Use exact integer pixel geometry and explicit hex color values. Do not return "
        "free-form layout prose, image prompts, rendered assets, CSS, SVG or markdown. Return "
        f"exactly one JSON object matching this schema: {schema}"
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
    "DESIGN_SPEC_AGENT_NAME",
    "DESIGN_SPEC_DEFINITION",
    "DesignSpecAgent",
    "register_design_spec_agent",
]
