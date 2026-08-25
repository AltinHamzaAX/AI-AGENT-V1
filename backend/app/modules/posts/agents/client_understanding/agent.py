import json
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ValidationError

from app.modules.posts.agents.framework import AgentExecutionContext, AgentRuntime
from app.modules.posts.domain.contracts import AgentDefinition, RetryPolicy
from app.modules.posts.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    ProviderResponseError,
)
from app.modules.posts.tools import ToolGateway
from app.shared.assets.domain import AssetRole

from .schemas import (
    ClientUnderstandingBrief,
    ClientUnderstandingInput,
    ClientUnderstandingLLMOutput,
    UnderstandingField,
    UnderstoodAsset,
)

CLIENT_UNDERSTANDING_AGENT_NAME = "client_understanding"

CLIENT_UNDERSTANDING_DEFINITION = AgentDefinition(
    name=CLIENT_UNDERSTANDING_AGENT_NAME,
    role="Extract a factual structured client brief without creating strategy",
    input_schema=ClientUnderstandingInput,
    output_schema=ClientUnderstandingBrief,
    allowed_tools=frozenset(),
    timeout_seconds=120,
    retry_policy=RetryPolicy(
        max_attempts=2,
        retry_on_timeout=True,
        retry_on_error=True,
    ),
)

_SCALAR_FIELDS = tuple(field.value for field in UnderstandingField)
_PROJECT_CONTEXT_ALIASES: dict[str, tuple[str, ...]] = {
    "business": ("business", "company"),
    "product_service": ("product_service", "product", "service"),
}
_IDENTITY_ROLES = frozenset(
    {
        AssetRole.LOGO,
        AssetRole.PRODUCT,
        AssetRole.VEHICLE,
        AssetRole.PACKAGING,
    }
)
_NULL_LIKE_VALUES = frozenset(
    {
        "null",
        "none",
        "unknown",
        "n/a",
        "not available",
        "not provided",
        "not specified",
    }
)
_GOAL_MARKERS = frozenset(
    {
        "boost",
        "drive",
        "grow",
        "increase",
        "more",
        "rrit",
        "shto",
        "shume",
        "shumë",
    }
)
_CTA_MARKERS = frozenset(
    {
        "apliko",
        "book",
        "blej",
        "buy",
        "call",
        "contact",
        "kontakto",
        "kliko",
        "order",
        "porosit",
        "rezervo",
        "shop",
        "telefono",
        "visit",
        "vizito",
    }
)
_BUSINESS_TYPES = frozenset(
    {
        "agency",
        "agjenci",
        "cafe",
        "company",
        "dyqan",
        "hotel",
        "kafiteri",
        "kafiteria",
        "klinik",
        "kompani",
        "restaurant",
        "restorant",
        "shop",
    }
)
_PLATFORMS = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "pinterest": "Pinterest",
    "threads": "Threads",
    "tiktok": "TikTok",
    "twitter": "X",
    "youtube": "YouTube",
}
_ALBANIAN_MARKERS = frozenset(
    {
        "audienca",
        "brandi",
        "dhe",
        "dua",
        "eshte",
        "gjuhen",
        "nje",
        "oferta",
        "per",
        "quhet",
        "rezervo",
        "shume",
    }
)


class ClientUnderstandingAgent:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> ClientUnderstandingBrief:
        if not isinstance(payload, ClientUnderstandingInput):
            raise TypeError("client understanding received an invalid input type")
        response = await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=_system_prompt()),
                    LLMMessage(
                        role="user",
                        content=json.dumps(
                            payload.model_dump(mode="json"),
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
            extracted = ClientUnderstandingLLMOutput.model_validate(
                _parse_json_object(response.text)
            )
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError(
                "client understanding returned invalid structured output"
            ) from exc
        return _build_brief(payload, extracted)


def register_client_understanding_agent(
    runtime: AgentRuntime,
    llm: LLMProvider,
) -> None:
    agent = ClientUnderstandingAgent(llm)
    runtime.register(CLIENT_UNDERSTANDING_DEFINITION, agent.execute)


def _system_prompt() -> str:
    schema = json.dumps(
        ClientUnderstandingLLMOutput.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        "You are the Client Understanding specialist for a marketing post workflow. "
        "Extract only facts and explicit preferences supported by the conversation, latest "
        "message, attachments, or verified project context. The latest message has priority "
        "over older conversation turns. Assistant messages are dialogue context only: never "
        "copy examples, alternatives, or assumptions from an assistant question into the "
        "brief unless the client explicitly confirms them. Treat an explicit desired outcome "
        "such as 'more "
        "visits', 'more bookings', or 'more sales' as the goal. CTA intent is a distinct "
        "audience action and must remain null when the client did not request one. A stated "
        "business type such as cafe is valid business context. Detect the message language "
        "when it is clear, but never infer the audience, market, or location from language. "
        "When the client names a business or venue (for example, 'it is called LUMMA'), use "
        "that explicit name as the brand. Preserve the client's wording whenever practical. "
        "When the promoted subject is the venue or business itself and no separate product is "
        "named, use the explicit business type as product_service. A desired result such as "
        "more visits belongs only in goal, never in cta_intent. "
        "For every non-null field and every extracted list, add an evidence entry containing "
        "an exact verbatim quote from a user message that supports it. Never use assistant "
        "messages as evidence. If no exact client quote supports a field, return null or an "
        "empty list for that field and omit its evidence entry. "
        "Use null when a fact is unknown. Never invent a "
        "business, brand, product, offer, audience, location, or asset. Do not create market "
        "research, positioning, USP, campaign strategy, marketing angle, creative concept, "
        "copy, art direction, or design decisions. Return exactly one JSON object matching "
        f"this schema and no prose or markdown: {schema}"
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


def _build_brief(
    source: ClientUnderstandingInput,
    extracted: ClientUnderstandingLLMOutput,
) -> ClientUnderstandingBrief:
    values = extracted.model_dump()
    evidence = values.pop("evidence")
    for field_name in _SCALAR_FIELDS:
        verified = _verified_context_value(source.project_context, field_name)
        if isinstance(verified, str) and verified.strip():
            values[field_name] = verified.strip()
        else:
            values[field_name] = _grounded_optional(
                source,
                value=values[field_name],
                evidence=evidence.get(field_name),
            )

    if (
        values["product_service"] is not None
        and values["brand"] is not None
        and _normalize_text(values["product_service"]) == _normalize_text(values["brand"])
        and not _evidence_declares_product(evidence.get("product_service"))
    ):
        values["product_service"] = None
    if (
        values["business"] is not None
        and values["brand"] is not None
        and _normalize_text(values["business"]) == _normalize_text(values["brand"])
        and not _evidence_declares_business(evidence.get("business"))
    ):
        values["business"] = None

    if values["goal"] is None and _has_marker(values["cta_intent"], _GOAL_MARKERS):
        values["goal"] = values["cta_intent"]
        values["cta_intent"] = None
    elif values["cta_intent"] is not None and not _has_marker(values["cta_intent"], _CTA_MARKERS):
        values["cta_intent"] = None

    values["platform"] = values["platform"] or _extract_platform(source)
    values["language"] = values["language"] or _detect_language(source)
    values["brand"] = values["brand"] or _extract_named_brand(source)
    values["business"] = values["business"] or _extract_business_type(source)
    extracted_goal = _extract_goal(source)
    if extracted_goal is not None and (
        values["goal"] is None or _normalize_text(extracted_goal) in _normalize_text(values["goal"])
    ):
        values["goal"] = extracted_goal
    values["cta_intent"] = values["cta_intent"] or _extract_cta(source)
    if (
        values["product_service"] is None
        and isinstance(values["business"], str)
        and _normalize_text(values["business"]) in _BUSINESS_TYPES
    ):
        values["product_service"] = values["business"]

    values["style_preferences"] = _merge_string_lists(
        _grounded_string_list(
            source,
            values["style_preferences"],
            evidence.get("style_preferences"),
        ),
        source.project_context.get("style_preferences"),
    )
    values["constraints"] = _merge_string_lists(
        _grounded_string_list(
            source,
            values["constraints"],
            evidence.get("constraints"),
        ),
        source.project_context.get("constraints"),
    )
    values["assets"] = [
        UnderstoodAsset(
            id=attachment.id,
            role=attachment.role,
            original_filename=attachment.original_filename,
            preserve_identity=attachment.role in _IDENTITY_ROLES,
        )
        for attachment in source.attachments
    ]
    values["missing_fields"] = [
        UnderstandingField(field_name)
        for field_name in _SCALAR_FIELDS
        if values[field_name] is None
    ]
    return ClientUnderstandingBrief.model_validate(values)


def _verified_context_value(project_context: dict[str, Any], field_name: str) -> Any:
    aliases = _PROJECT_CONTEXT_ALIASES.get(field_name, (field_name,))
    for alias in aliases:
        value = project_context.get(alias)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _normalized_optional(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.casefold() in _NULL_LIKE_VALUES:
        return None
    return normalized


def _grounded_optional(
    source: ClientUnderstandingInput,
    *,
    value: Any,
    evidence: Any,
) -> str | None:
    normalized = _normalized_optional(value)
    if normalized is None:
        return None
    normalized_value = _normalize_text(normalized)
    if any(normalized_value in _normalize_text(message) for message in _client_messages(source)):
        return normalized
    if (
        isinstance(evidence, str)
        and _is_client_quote(source, evidence)
        and normalized_value in _normalize_text(evidence)
    ):
        return normalized
    return None


def _grounded_string_list(
    source: ClientUnderstandingInput,
    values: Any,
    evidence: Any,
) -> list[str]:
    if not isinstance(values, list) or not isinstance(evidence, str):
        return []
    if not _is_client_quote(source, evidence):
        return []
    normalized_evidence = _normalize_text(evidence)
    return [
        normalized
        for value in values
        if (normalized := _normalized_optional(value)) is not None
        and _normalize_text(normalized) in normalized_evidence
    ]


def _is_client_quote(source: ClientUnderstandingInput, evidence: str) -> bool:
    normalized_evidence = _normalize_text(evidence)
    if not normalized_evidence:
        return False
    return any(
        normalized_evidence in _normalize_text(message) for message in _client_messages(source)
    )


def _client_messages(source: ClientUnderstandingInput) -> list[str]:
    return [
        source.latest_message,
        *(turn.content for turn in source.conversation_history if turn.role == "user"),
    ]


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_marks.split())


def _has_marker(value: Any, markers: frozenset[str]) -> bool:
    if not isinstance(value, str):
        return False
    tokens = {token.strip(".,!?;:()[]{}\"'") for token in _normalize_text(value).split()}
    return bool(tokens & markers)


def _evidence_declares_product(evidence: Any) -> bool:
    if not isinstance(evidence, str):
        return False
    tokens = set(_normalize_text(evidence).split())
    return bool(
        tokens
        & {
            "biznes",
            "business",
            "product",
            "produkti",
            "produkt",
            "service",
            "sherbim",
            "sherbimi",
        }
    )


def _evidence_declares_business(evidence: Any) -> bool:
    if not isinstance(evidence, str):
        return False
    tokens = set(_normalize_text(evidence).split())
    return bool(
        tokens
        & {
            "agency",
            "agjenci",
            "biznes",
            "biznesi",
            "business",
            "company",
            "kompani",
            "kompania",
        }
    )


def _extract_platform(source: ClientUnderstandingInput) -> str | None:
    tokens = {
        _normalize_text(token)
        for token in re.findall(
            r"[\w-]+",
            " ".join(_client_messages(source)),
            flags=re.UNICODE,
        )
    }
    for platform, canonical_name in _PLATFORMS.items():
        if platform in tokens:
            return canonical_name
    return None


def _detect_language(source: ClientUnderstandingInput) -> str | None:
    tokens = {
        token.strip(".,!?;:()[]{}\"'")
        for token in _normalize_text(" ".join(_client_messages(source))).split()
    }
    if len(tokens & _ALBANIAN_MARKERS) >= 2:
        return "shqip"
    return None


def _extract_named_brand(source: ClientUnderstandingInput) -> str | None:
    for message in _client_messages(source):
        match = re.search(
            r"\b(?:brandi\s+)?quhet\s+([\w-]+)",
            message,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1)
    return None


def _extract_business_type(source: ClientUnderstandingInput) -> str | None:
    for message in reversed(_client_messages(source)):
        for token in re.findall(r"[\w-]+", message, flags=re.UNICODE):
            normalized = _normalize_text(token)
            if normalized in _BUSINESS_TYPES:
                return normalized
    return None


def _extract_goal(source: ClientUnderstandingInput) -> str | None:
    patterns = (
        r"\bm[eë]\s+shum[eë]\s+[\w-]+",
        r"\bmore\s+[\w-]+",
        r"\b(?:increase|boost|grow|drive)\s+[\w-]+",
        r"\b(?:rrit|shto)\w*\s+[\w-]+",
    )
    for message in reversed(_client_messages(source)):
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return match.group(0).strip()
    return None


def _extract_cta(source: ClientUnderstandingInput) -> str | None:
    marker_pattern = "|".join(
        re.escape(marker) for marker in sorted(_CTA_MARKERS, key=len, reverse=True)
    )
    suffix_pattern = r"(?:\s+(?:here|ketu|now|sot|tani|today|us))?"
    pattern = rf"\b(?:{marker_pattern}){suffix_pattern}\b"
    for message in reversed(_client_messages(source)):
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def _merge_string_lists(primary: Any, verified: Any) -> list[str]:
    candidates: list[Any] = list(primary) if isinstance(primary, list) else []
    if isinstance(verified, list):
        candidates.extend(verified)
    result: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = candidate.strip()
        if (
            normalized
            and normalized.casefold() not in _NULL_LIKE_VALUES
            and normalized not in result
        ):
            result.append(normalized)
    return result


__all__ = [
    "CLIENT_UNDERSTANDING_AGENT_NAME",
    "CLIENT_UNDERSTANDING_DEFINITION",
    "ClientUnderstandingAgent",
    "register_client_understanding_agent",
]
