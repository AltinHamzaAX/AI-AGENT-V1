import json
import logging
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

from .schemas import DesignSpec, DesignSpecBody, DesignSpecInput

DESIGN_SPEC_AGENT_NAME = "design_spec_compiler"
logger = logging.getLogger(__name__)

DESIGN_SPEC_DEFINITION = AgentDefinition(
    name=DESIGN_SPEC_AGENT_NAME,
    role="Compile approved art direction into a versioned machine-readable design spec",
    input_schema=DesignSpecInput,
    output_schema=DesignSpec,
    allowed_tools=frozenset(),
    timeout_seconds=SPECIALIST_TIMEOUT_SECONDS,
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
            logger.warning("posts.design_spec.validation_failed: %s", first_exc)
            repair = await self._complete(
                payload,
                contract,
                previous_output=response.text,
                validation_error=str(first_exc),
            )
        try:
            return _validated_spec(repair.text, payload=payload, contract=contract)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            logger.warning("posts.design_spec.repair_failed: %s", exc)
            try:
                fallback = _deterministic_spec(payload)
                return _validated_spec(fallback, payload=payload, contract=contract)
            except (TypeError, ValueError, ValidationError) as final_exc:
                logger.error("posts.design_spec.fallback_failed: %s", final_exc)
                raise ProviderResponseError(
                    "design spec compiler returned invalid geometry"
                ) from final_exc

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


def _deterministic_spec(payload: DesignSpecInput) -> str:
    """Compile a conservative, composer-safe square layout from approved inputs."""
    has_offer = payload.copy_draft.offer_copy is not None
    offer_region = {"x": 640, "y": 520, "width": 280, "height": 76}
    roles: list[dict[str, Any]] = [
        {
            "role": "headline",
            "family_token": "brand-display",
            "weight": 700,
            "size_px": 58,
            "line_height": 1.05,
            "letter_spacing_px": 0,
            "max_lines": 3,
            "align": "left",
        },
        {
            "role": "supporting_copy",
            "family_token": "brand-body",
            "weight": 400,
            "size_px": 26,
            "line_height": 1.3,
            "letter_spacing_px": 0,
            "max_lines": 4,
            "align": "left",
        },
        {
            "role": "cta",
            "family_token": "brand-body",
            "weight": 700,
            "size_px": 25,
            "line_height": 1.0,
            "letter_spacing_px": 0,
            "max_lines": 1,
            "align": "center",
        },
    ]
    if has_offer:
        roles.insert(
            2,
            {
                "role": "offer",
                "family_token": "brand-display",
                "weight": 700,
                "size_px": 32,
                "line_height": 1.0,
                "letter_spacing_px": 0,
                "max_lines": 2,
                "align": "left",
            },
        )
    body = {
        "schema_version": "1.0",
        "canvas": {"width": 1080, "height": 1080, "unit": "px"},
        "safe_area": {"top": 64, "right": 64, "bottom": 64, "left": 64},
        "grid": {"columns": 12, "rows": 12, "gutter": 24, "baseline": 8},
        "regions": {
            "product_bounds": {"x": 0, "y": 230, "width": 620, "height": 760},
            "headline_region": {"x": 640, "y": 96, "width": 360, "height": 250},
            "offer_region": offer_region if has_offer else None,
            "cta_region": {"x": 640, "y": 640, "width": 280, "height": 72},
            "logo_region": {"x": 820, "y": 920, "width": 180, "height": 72},
            "legal_region": None,
        },
        "typography_roles": roles,
        "color_system": [
            {"role": "background", "value": "#F5F5F4", "source": "neutral"},
            {"role": "text", "value": "#171717", "source": "neutral"},
            {"role": "accent", "value": "#737373", "source": "neutral"},
            {"role": "cta_background", "value": "#171717", "source": "neutral"},
            {"role": "cta_text", "value": "#FFFFFF", "source": "neutral"},
        ],
        "graphic_elements": [],
        "photography": payload.art_direction.photography_direction,
        "lighting": payload.art_direction.lighting,
        "background": payload.art_direction.color_direction,
    }
    return json.dumps(body, ensure_ascii=False)


__all__ = [
    "DESIGN_SPEC_AGENT_NAME",
    "DESIGN_SPEC_DEFINITION",
    "DesignSpecAgent",
    "register_design_spec_agent",
]
