import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, get_origin

from pydantic import ValidationError

from app.modules.posts.chat.schemas import ConversationRouterOutput
from app.modules.posts.domain.chat import (
    QUOTED_FIELDS,
    ChatIntent,
    ContextAsset,
    ContextUpdate,
    ConversationContext,
    is_foreign_script,
    normalize_text,
)
from app.modules.posts.domain.enums import UnderstandingField
from app.modules.posts.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    ProviderResponseError,
)

#: Only the tail of the conversation is classified. Older turns are already
#: folded into the accumulated context, so re-reading them buys nothing.
CLASSIFIER_HISTORY_TURNS = 12
CLASSIFIER_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class ChatExchange:
    role: str
    content: str


class ConversationRouter:
    """Classify one client message and extract the facts it carries."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def classify(
        self,
        *,
        message: str,
        history: Sequence[ChatExchange],
        context: ConversationContext,
        attachments: Sequence[ContextAsset] = (),
    ) -> ConversationRouterOutput:
        payload = {
            "latest_message": message,
            "conversation": [
                {"role": exchange.role, "content": exchange.content}
                for exchange in history[-CLASSIFIER_HISTORY_TURNS:]
            ],
            "known_context": context.summary(),
            "attachments": [
                {"role": asset.role.value, "filename": asset.original_filename}
                for asset in attachments
            ],
            "post_already_generated": bool(context.generated_posts),
        }
        request = LLMRequest(
            messages=(
                LLMMessage(role="system", content=_system_prompt()),
                LLMMessage(
                    role="user",
                    content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            ),
            temperature=0,
            response_format="json",
        )
        output = await self._classify_with_retry(request)
        client_texts = [message, *(item.content for item in history if item.role == "user")]
        return output.model_copy(
            update={"context_updates": _ground(output.context_updates, client_texts)}
        )

    async def _classify_with_retry(self, request: LLMRequest) -> ConversationRouterOutput:
        """One retry, because a malformed object is usually not reproducible.

        Transport failures are not retried here: the caller is a user waiting on
        a reply, and a second timeout only doubles the wait.
        """
        last_error: Exception | None = None
        for _ in range(CLASSIFIER_ATTEMPTS):
            response = await self._llm.complete(request)
            try:
                payload = _normalize_payload(_parse_json_object(response.text))
                return ConversationRouterOutput.model_validate(payload)
            except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
                last_error = exc
        raise ProviderResponseError(
            "conversation router returned invalid structured output"
        ) from last_error


def _ground(update: ContextUpdate, client_texts: Sequence[str]) -> ContextUpdate:
    """Drop what the client never said, and what they never said in this script.

    Interpretive fields (goal, audience, market, CTA intent, style, constraints)
    are the classifier's reading of the dialogue and are kept as written. Named
    entities are not: a business, brand, product, location, platform or offer
    survives only when it appears in the client's own words. On top of that, no
    field may come back translated: a value written in a script the client never
    used is the model's invention of a language, not the client's fact.
    """
    normalized_sources = [normalize_text(text) for text in client_texts]
    values: dict[str, Any] = {}
    for name in _SCALAR_UPDATE_FIELDS:
        candidate = getattr(update, name)
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        if name != UnderstandingField.LANGUAGE.value and is_foreign_script(
            candidate, client_texts
        ):
            values[name] = None
            continue
        quoted = name in {field.value for field in QUOTED_FIELDS}
        if quoted and not any(
            normalize_text(candidate) in source for source in normalized_sources
        ):
            values[name] = None
    for name in _LIST_UPDATE_FIELDS:
        items = getattr(update, name)
        kept = [item for item in items if not is_foreign_script(item, client_texts)]
        if len(kept) != len(items):
            values[name] = kept
    return update.model_copy(update=values) if values else update


_NULL_LIKE_VALUES = frozenset(
    {"", "null", "none", "unknown", "n/a", "not provided", "not specified", "-"}
)
_LIST_UPDATE_FIELDS = frozenset(
    name
    for name, info in ContextUpdate.model_fields.items()
    if get_origin(info.annotation) is list
)
_SCALAR_UPDATE_FIELDS = frozenset(ContextUpdate.model_fields) - _LIST_UPDATE_FIELDS


def _normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Reshape a plausible answer into the exact object the schema accepts.

    Small models put the right values in slightly wrong places: an extra key
    inside the updates, a bare string where a list belongs, a lowercase intent.
    Reshaping those costs one pass; a second provider round trip costs seconds.
    """
    if "$defs" in raw or "$schema" in raw or ("properties" in raw and "intent" not in raw):
        raise ValueError("provider echoed a schema instead of answering")
    intent = raw.get("intent")
    payload: dict[str, Any] = {
        "intent": intent.strip().upper() if isinstance(intent, str) else intent,
        "reason": raw.get("reason") if isinstance(raw.get("reason"), str) else "",
        "context_updates": _normalize_updates(raw.get("context_updates")),
        "revision_instructions": _as_string_list(raw.get("revision_instructions")),
    }
    return payload


def _normalize_updates(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    updates: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _SCALAR_UPDATE_FIELDS:
            if isinstance(value, str) and value.strip().casefold() not in _NULL_LIKE_VALUES:
                updates[key] = value.strip()
        elif key in _LIST_UPDATE_FIELDS:
            updates[key] = _as_string_list(value)
    return updates


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip().casefold() not in _NULL_LIKE_VALUES
    ]


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


def _answer_shape() -> str:
    """Describe the answer literally rather than as a JSON Schema.

    A generated schema for this output nests `$defs` and `$ref`, and a model
    handed one answers with the schema itself often enough to break the turn.
    A worked example of the object is unambiguous and costs fewer tokens.
    """
    intents = " | ".join(intent.value for intent in ChatIntent)
    scalars = ", ".join(sorted(_SCALAR_UPDATE_FIELDS))
    lists = ", ".join(sorted(_LIST_UPDATE_FIELDS))
    return (
        'Return exactly one JSON object shaped like {"intent": ..., "reason": ..., '
        '"context_updates": {...}, "revision_instructions": [...]} and nothing else.\n'
        f'"intent" is one of: {intents}.\n'
        '"reason" is one short sentence explaining the choice.\n'
        f'"context_updates" holds only the keys you actually learned. {scalars} take a '
        f"string; {lists} take an array of strings. Omit every key you did not learn.\n"
        '"revision_instructions" is an array of short imperative sentences, empty unless '
        "the intent is REVISE_POST.\n"
        "Answer with the object itself. Never return a schema, a type definition, prose or "
        "markdown fences."
    )


def _system_prompt() -> str:
    return (
        "You are the Conversation Router for Promotiva's Posts assistant. You decide what the "
        "client's latest message is asking for, and you extract the facts it carries. You never "
        "write the reply and you never produce marketing work.\n"
        "Choose exactly one intent:\n"
        "GENERAL_CONVERSATION - greetings, small talk, thanks, or anything not about producing "
        "or improving a post.\n"
        "MARKETING_QUESTION - the client asks for advice, an opinion, ideas, or an explanation "
        "about marketing, and is not ordering a post.\n"
        "MISSING_INFORMATION - the client wants a post but the message and known context still "
        "leave the promoted subject or the desired outcome unknown.\n"
        "GENERATE_POST - the client instructs you to produce the post now.\n"
        "REVISE_POST - the client asks to change, fix or improve a post that was already "
        "generated. Only valid when post_already_generated is true.\n"
        "CLARIFICATION - the client is answering a question or refining an earlier request "
        "without ordering production.\n"
        "Describing a business, naming a product, or stating a target audience is not by itself "
        "an order to generate: choose CLARIFICATION or MISSING_INFORMATION instead.\n"
        "In context_updates return only facts stated by the client in this conversation, "
        "including the latest message. Copy the client's own wording for business, brand, "
        "product_service, location, platform and offer. Use null for anything the client has "
        "not stated; never repeat a value that is already identical in known_context, and never "
        "infer a fact from the language of the message or from an assistant turn. A desired "
        "result such as more bookings or more visits is the goal; cta_intent is only an explicit "
        "action the audience should take.\n"
        "For REVISE_POST put each requested change in revision_instructions as one short "
        "imperative sentence in the client's language; leave the list empty otherwise.\n"
        "Report every extracted value in the language the client wrote it in; never translate "
        "a fact into another language.\n"
        f"{_answer_shape()}"
    )


__all__ = [
    "CLASSIFIER_ATTEMPTS",
    "CLASSIFIER_HISTORY_TURNS",
    "ChatExchange",
    "ConversationRouter",
]
