"""Conversational Posts chat: intent vocabulary, accumulated context, routing rules.

This module is deliberately free of providers, SQLAlchemy and transport types.
It owns the decisions that must stay identical whether a language model is
available or not: what an intent may do, when the workflow is allowed to start,
and what the assistant already knows about the client.
"""

import re
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.posts.domain.clarification import ClarificationEngine, ClarificationPlan
from app.modules.posts.domain.enums import UnderstandingField
from app.shared.assets.domain import AssetRole

CONVERSATION_CONTEXT_SCHEMA_VERSION = 1

#: Facts the client states and the assistant must never invent. A value for one
#: of these is kept only when the client's own words support it.
QUOTED_FIELDS = frozenset(
    {
        UnderstandingField.BUSINESS,
        UnderstandingField.BRAND,
        UnderstandingField.PRODUCT_SERVICE,
        UnderstandingField.LOCATION,
        UnderstandingField.PLATFORM,
        UnderstandingField.OFFER,
    }
)


class ChatIntent(StrEnum):
    GENERAL_CONVERSATION = "GENERAL_CONVERSATION"
    MARKETING_QUESTION = "MARKETING_QUESTION"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    GENERATE_POST = "GENERATE_POST"
    REVISE_POST = "REVISE_POST"
    CLARIFICATION = "CLARIFICATION"


class ChatAction(StrEnum):
    """What the turn is allowed to do once the intent is known."""

    REPLY = "reply"
    ASK = "ask"
    GENERATE = "generate"
    REVISE = "revise"


class ContextAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    message_id: UUID
    role: AssetRole
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=100)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


class GeneratedPostRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_id: UUID
    generation_id: UUID
    attempt: int = Field(ge=1)
    requested_at: datetime
    revises_generation_id: UUID | None = None
    instruction: str | None = Field(default=None, max_length=2_000)


class ContextUpdate(BaseModel):
    """One turn's worth of newly learned facts. Absent values change nothing."""

    model_config = ConfigDict(extra="forbid")

    business: str | None = Field(default=None, max_length=500)
    brand: str | None = Field(default=None, max_length=500)
    product_service: str | None = Field(default=None, max_length=500)
    goal: str | None = Field(default=None, max_length=500)
    audience: str | None = Field(default=None, max_length=500)
    market: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=100)
    offer: str | None = Field(default=None, max_length=500)
    cta_intent: str | None = Field(default=None, max_length=500)
    style_preferences: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        return canonical_language(value)

    @field_validator("platform")
    @classmethod
    def normalize_platform(cls, value: str | None) -> str | None:
        return canonical_platform(value)


class ConversationContext(BaseModel):
    """Everything the assistant remembers about one Posts conversation.

    The client is never asked twice for the same fact: a value survives every
    later turn unless the client replaces it.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CONVERSATION_CONTEXT_SCHEMA_VERSION
    business: str | None = Field(default=None, max_length=500)
    brand: str | None = Field(default=None, max_length=500)
    product_service: str | None = Field(default=None, max_length=500)
    goal: str | None = Field(default=None, max_length=500)
    audience: str | None = Field(default=None, max_length=500)
    market: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=100)
    offer: str | None = Field(default=None, max_length=500)
    cta_intent: str | None = Field(default=None, max_length=500)
    style_preferences: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    assets: list[ContextAsset] = Field(default_factory=list, max_length=200)
    previous_requests: list[str] = Field(default_factory=list, max_length=100)
    generated_posts: list[GeneratedPostRef] = Field(default_factory=list, max_length=100)
    revision_instructions: list[str] = Field(default_factory=list, max_length=100)
    #: How many times the client has asked for generation in plain words. The
    #: second explicit request is allowed to proceed on an inferred goal so one
    #: unextractable field cannot deadlock the conversation.
    explicit_generation_requests: int = Field(default=0, ge=0)
    inferred_fields: list[UnderstandingField] = Field(default_factory=list, max_length=20)

    @property
    def missing_fields(self) -> list[UnderstandingField]:
        return [field for field in UnderstandingField if getattr(self, field.value) is None]

    @property
    def latest_generation(self) -> GeneratedPostRef | None:
        return self.generated_posts[-1] if self.generated_posts else None

    def clarification(self) -> ClarificationPlan:
        return ClarificationEngine().evaluate(self)

    def merge(self, update: ContextUpdate) -> Self:
        values: dict[str, Any] = {}
        for field in UnderstandingField:
            candidate = getattr(update, field.value)
            if isinstance(candidate, str) and candidate.strip():
                values[field.value] = candidate.strip()
        values["style_preferences"] = _extend(self.style_preferences, update.style_preferences)
        values["constraints"] = _extend(self.constraints, update.constraints)
        values["inferred_fields"] = [
            field for field in self.inferred_fields if field.value not in values
        ]
        return self.model_copy(deep=True, update=values)

    def with_assets(self, assets: list[ContextAsset]) -> Self:
        return self.model_copy(deep=True, update={"assets": list(assets)})

    def with_request(self, request: str) -> Self:
        return self.model_copy(
            deep=True,
            update={"previous_requests": _extend(self.previous_requests, [request])},
        )

    def with_generation_request(self) -> Self:
        return self.model_copy(
            deep=True,
            update={"explicit_generation_requests": self.explicit_generation_requests + 1},
        )

    def with_inferred(self, field: UnderstandingField, value: str) -> Self:
        inferred = [item for item in self.inferred_fields if item is not field]
        return self.model_copy(
            deep=True,
            update={field.value: value, "inferred_fields": [*inferred, field]},
        )

    def with_generated_post(self, reference: GeneratedPostRef) -> Self:
        return self.model_copy(
            deep=True,
            update={
                "generated_posts": [*self.generated_posts, reference][-100:],
                "explicit_generation_requests": 0,
            },
        )

    def with_revision_instructions(self, instructions: list[str]) -> Self:
        return self.model_copy(
            deep=True,
            update={"revision_instructions": _extend(self.revision_instructions, instructions)},
        )

    def project_context(self) -> dict[str, Any]:
        """Verified facts handed to the generation workflow as project context.

        Inferred values are withheld: the workflow re-derives its own grounded
        brief and must never receive an assumption as a client statement.
        """
        context: dict[str, Any] = {
            field.value: getattr(self, field.value)
            for field in UnderstandingField
            if getattr(self, field.value) is not None and field not in self.inferred_fields
        }
        if self.style_preferences:
            context["style_preferences"] = list(self.style_preferences)
        if self.constraints:
            context["constraints"] = list(self.constraints)
        return context

    def summary(self) -> dict[str, Any]:
        """A compact card for prompting: known facts only, no empty keys."""
        summary: dict[str, Any] = {
            field.value: getattr(self, field.value)
            for field in UnderstandingField
            if getattr(self, field.value) is not None
        }
        if self.style_preferences:
            summary["style_preferences"] = list(self.style_preferences)
        if self.constraints:
            summary["constraints"] = list(self.constraints)
        if self.assets:
            summary["attachments"] = [
                {"role": asset.role.value, "filename": asset.original_filename}
                for asset in self.assets
            ]
        if self.generated_posts:
            summary["generated_posts"] = len(self.generated_posts)
        if self.revision_instructions:
            summary["revision_instructions"] = list(self.revision_instructions[-5:])
        return summary


class RoutedTurn(BaseModel):
    """The single decision the rest of the turn is executed against."""

    model_config = ConfigDict(extra="forbid")

    intent: ChatIntent
    action: ChatAction
    reason: str = Field(min_length=1, max_length=400)
    questions: list[str] = Field(default_factory=list, max_length=4)


_QUESTION_OPENERS = frozenset(
    {
        "a",
        "cfare",
        "cila",
        "cili",
        "cka",
        "how",
        "is",
        "kur",
        "pse",
        "qysh",
        "should",
        "si",
        "what",
        "when",
        "which",
        "who",
        "why",
        "would",
    }
)

#: Up to two qualifiers may sit between the article and the noun, so
#: "create the Instagram post" reads as the same order as "create the post".
_QUALIFIER = r"(?:\w+\s+){0,2}"
_GENERATE_PATTERNS = (
    r"\bgjenero(je|ni|jani)?\b",
    r"\bgenerate\b",
    rf"\b(krijo|krijoje|bej|beje|dizajno|punoje|ndertoje)\s+(nje\s+|ni\s+|kete\s+)?{_QUALIFIER}"
    r"(post|postim|postimin|kreativ|reklame)\w*",
    rf"\b(create|make|design|build|draft)\s+(me\s+|us\s+)?(a\s+|an\s+|the\s+|this\s+)?"
    rf"{_QUALIFIER}(post|creative|ad)\b",
    r"\bma\s+(krijo|bej|beje|jep|nis)\b",
    r"\bnise?\s+gjenerimin\b",
    r"\bstart\s+(the\s+)?generation\b",
)

_REVISE_PATTERNS = (
    r"\b(ndrysho|ndryshoje|rishiko|rishikoje|korrigjo|korrigjoje|zvogelo|zvogeloje|zmadho"
    r"|zmadhoje|hiqe|hiq|zevendeso|perditeso|rregullo|rregulloje|ribeje)\b",
    r"\b(revise|revision|change|adjust|tweak|fix|replace|remove|resize|redo|update)\b",
    r"\b(make|bej|beje)\s+(it|the|headline|cta|titullin|tekstin)\b",
    r"\b(me|more|less|pak)\s+(e\s+)?(vogel|madh|premium|smaller|bigger|larger|subtle)\w*\b",
)


#: Language names a model may return for the same language. The clarification
#: engine picks its wording from an exact name, so the aliases are collapsed
#: before the value is ever stored.
_LANGUAGE_ALIASES = {
    "al": "shqip",
    "alb": "shqip",
    "albanian": "shqip",
    "albanisht": "shqip",
    "gjuha shqipe": "shqip",
    "shqip": "shqip",
    "shqipe": "shqip",
    "sq": "shqip",
    "sqi": "shqip",
    "anglisht": "english",
    "en": "english",
    "eng": "english",
    "english": "english",
}


#: The platforms a social post can target. A value outside this set is a misread
#: rather than a preference, so it is dropped instead of stored: the generation
#: workflow re-derives the platform from the client's own words anyway.
_PLATFORMS = {
    "facebook": "Facebook",
    "fb": "Facebook",
    "ig": "Instagram",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "pinterest": "Pinterest",
    "reddit": "Reddit",
    "snapchat": "Snapchat",
    "telegram": "Telegram",
    "threads": "Threads",
    "tiktok": "TikTok",
    "twitter": "X",
    "whatsapp": "WhatsApp",
    "x": "X",
    "youtube": "YouTube",
}


def canonical_platform(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value)
    if not normalized:
        return None
    if normalized in _PLATFORMS:
        return _PLATFORMS[normalized]
    tokens = set(re.findall(r"\w+", normalized))
    for name, canonical in _PLATFORMS.items():
        if name in tokens:
            return canonical
    return None


def canonical_language(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return _LANGUAGE_ALIASES.get(normalize_text(normalized), normalized)


def normalize_text(value: str) -> str:
    """Casefold, strip diacritics and collapse whitespace for stable matching."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_marks.split())


def is_question(message: str) -> bool:
    normalized = normalize_text(message)
    if not normalized.endswith("?"):
        return False
    opener = normalized.split(" ", 1)[0].strip(".,!?;:")
    return opener in _QUESTION_OPENERS


def explicit_generation_request(message: str) -> bool:
    """True only for an unambiguous instruction to produce the post."""
    if is_question(message):
        return False
    normalized = normalize_text(message)
    return any(re.search(pattern, normalized) for pattern in _GENERATE_PATTERNS)


def explicit_revision_request(message: str) -> bool:
    """True only for an unambiguous instruction to change what was produced."""
    if is_question(message):
        return False
    normalized = normalize_text(message)
    return any(re.search(pattern, normalized) for pattern in _REVISE_PATTERNS)


class ChatIntentRouter:
    """Turn a proposed intent into the single action the turn may perform.

    The language model proposes; this router decides. Generation and revision
    are reachable only through their own intents, and only when the workflow
    would actually have something to work with.
    """

    def route(
        self,
        *,
        proposed: ChatIntent,
        message: str,
        context: ConversationContext,
    ) -> RoutedTurn:
        intent = self._deterministic_intent(proposed, message=message, context=context)
        if intent is ChatIntent.REVISE_POST:
            if context.latest_generation is None:
                return RoutedTurn(
                    intent=ChatIntent.CLARIFICATION,
                    action=ChatAction.REPLY,
                    reason="A change was requested before any post existed to change.",
                )
            return RoutedTurn(
                intent=intent,
                action=ChatAction.REVISE,
                reason="The client asked to change the generated result.",
            )
        if intent is ChatIntent.GENERATE_POST:
            return self._route_generation(context)
        if intent is ChatIntent.MISSING_INFORMATION:
            plan = context.clarification()
            return RoutedTurn(
                intent=intent,
                action=ChatAction.ASK,
                reason="Required client facts are still unknown.",
                questions=[question.question for question in plan.questions],
            )
        return RoutedTurn(
            intent=intent,
            action=ChatAction.REPLY,
            reason="The turn is conversational and starts no workflow.",
        )

    def _deterministic_intent(
        self,
        proposed: ChatIntent,
        *,
        message: str,
        context: ConversationContext,
    ) -> ChatIntent:
        if explicit_revision_request(message) and context.latest_generation is not None:
            return ChatIntent.REVISE_POST
        if explicit_generation_request(message):
            return ChatIntent.GENERATE_POST
        if proposed is ChatIntent.GENERATE_POST and is_question(message):
            return ChatIntent.MARKETING_QUESTION
        return proposed

    def _route_generation(self, context: ConversationContext) -> RoutedTurn:
        plan = context.clarification()
        if not plan.requires_user_input:
            return RoutedTurn(
                intent=ChatIntent.GENERATE_POST,
                action=ChatAction.GENERATE,
                reason="The client asked for the post and the required facts are known.",
            )
        return RoutedTurn(
            intent=ChatIntent.MISSING_INFORMATION,
            action=ChatAction.ASK,
            reason="Generation was requested while critical client facts are missing.",
            questions=[question.question for question in plan.questions],
        )


#: Business outcomes a client asks for in plain words. The list is closed on
#: purpose: "more bookings" is a goal, "more premium" is a style preference, and
#: only an explicit outcome noun tells the two apart without a model.
_OUTCOME_NOUNS = (
    "bookings",
    "calls",
    "clients",
    "conversions",
    "customers",
    "downloads",
    "engagement",
    "followers",
    "inquiries",
    "leads",
    "orders",
    "reach",
    "rentals",
    "reservations",
    "revenue",
    "sales",
    "signups",
    "subscribers",
    "traffic",
    "visits",
    "klient",
    "ndjekes",
    "porosi",
    "rezervime",
    "shitje",
    "vizita",
)
_GOAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\b(?:get|want|need|drive|increase|boost|grow|generate)\s+(?:more\s+|new\s+)?"
        rf"(?:{'|'.join(_OUTCOME_NOUNS)})\w*",
        rf"\bmore\s+(?:{'|'.join(_OUTCOME_NOUNS)})\w*",
        rf"\b(?:me|më)\s+shum[eë]\s+(?:{'|'.join(_OUTCOME_NOUNS)})\w*",
        rf"\b(?:rrit|shto)\w*\s+(?:{'|'.join(_OUTCOME_NOUNS)})\w*",
    )
)


#: Common Albanian function words. Two of them in the client's own text is a
#: stronger signal than a model's claim about which language it is reading.
_ALBANIAN_MARKERS = frozenset(
    {
        "dhe",
        "dua",
        "eshte",
        "jam",
        "kam",
        "kete",
        "ketu",
        "me",
        "nje",
        "nga",
        "per",
        "posti",
        "postin",
        "qe",
        "shume",
        "te",
        "une",
        "yne",
    }
)


#: Writing systems a value may be reported in. Anything outside this set is
#: punctuation, digits or spacing, which say nothing about language.
_SCRIPT_FAMILIES = frozenset(
    {
        "ARABIC",
        "ARMENIAN",
        "BENGALI",
        "CJK",
        "CYRILLIC",
        "DEVANAGARI",
        "ETHIOPIC",
        "GEORGIAN",
        "GREEK",
        "HANGUL",
        "HEBREW",
        "HIRAGANA",
        "KATAKANA",
        "LATIN",
        "TAMIL",
        "THAI",
    }
)


def scripts_used(text: str) -> frozenset[str]:
    families = {
        family
        for character in text
        if (family := unicodedata.name(character, "").split(" ", 1)[0]) in _SCRIPT_FAMILIES
    }
    return frozenset(families)


def is_foreign_script(value: str, client_texts: Sequence[str]) -> bool:
    """True when a value is written in a script the client never used.

    A model asked to extract facts sometimes translates them instead. The
    translation is not what the client said, and a post built from it would be
    written in the wrong language, so the value is dropped rather than stored.
    """
    client_scripts = frozenset().union(*(scripts_used(text) for text in client_texts)) or frozenset(
        {"LATIN"}
    )
    return bool(scripts_used(value) - client_scripts)


def detects_albanian(texts: Sequence[str]) -> bool:
    tokens = {
        token.strip(".,!?;:()[]{}\"'")
        for text in texts
        for token in normalize_text(text).split()
    }
    return len(tokens & _ALBANIAN_MARKERS) >= 2


#: Actions a client asks their audience to take. Closed on purpose, for the
#: same reason as the outcome nouns: only an explicit verb distinguishes a call
#: to action from an ordinary sentence.
#: English verbs match bare, so "the booking process" is not read as a call to
#: action; Albanian verbs carry their conjugation and match with it.
_CTA_VERBS_EN = ("book", "buy", "call", "contact", "order", "shop", "subscribe", "visit")
_CTA_VERBS_SQ = (
    "apliko",
    "blej",
    "kliko",
    "kontakto",
    "porosit",
    "rezervo",
    "telefono",
    "vizito",
)
_CTA_SUFFIX = r"(?:\s+(?:now|today|here|us|tani|sot|ketu))?"
_CTA_PATTERN = re.compile(
    rf"\b(?:(?:{'|'.join(_CTA_VERBS_EN)})|(?:{'|'.join(_CTA_VERBS_SQ)})\w*)\b{_CTA_SUFFIX}",
    re.IGNORECASE,
)


def extract_cta_intent(message: str) -> str | None:
    """The audience action the client named, in the client's own words.

    Read deterministically for the same reason as the goal: the marketing
    strategy grounds its call to action in this field, and asking again for
    something the client just said is the fastest way to lose them.
    """
    match = _CTA_PATTERN.search(message)
    return " ".join(match.group(0).split()) if match else None


def extract_goal(message: str) -> str | None:
    """The business outcome the client asked for, in the client's own words.

    A second, deterministic reading of the same message the classifier saw.
    Extraction models drop the outcome often enough that losing it would stall
    the conversation on a question the client has already answered.
    """
    for pattern in _GOAL_PATTERNS:
        match = pattern.search(message)
        if match:
            return " ".join(match.group(0).split())
    return None


def inferable_goal(context: ConversationContext) -> str | None:
    """A goal derived from the promoted subject, used only after a repeated ask.

    The client has asked twice in plain words and only the outcome is unknown;
    promoting the named subject is the honest reading of that request. The
    inference is recorded so no later stage mistakes it for a quoted fact.
    """
    subject = context.product_service or context.business or context.brand
    return f"promote {subject}" if subject else None


def _extend(existing: list[str], additions: list[str], *, limit: int = 100) -> list[str]:
    result = list(existing)
    for candidate in additions:
        normalized = candidate.strip() if isinstance(candidate, str) else ""
        if normalized and normalized not in result:
            result.append(normalized)
    return result[-limit:]


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "CONVERSATION_CONTEXT_SCHEMA_VERSION",
    "QUOTED_FIELDS",
    "ChatAction",
    "ChatIntent",
    "ChatIntentRouter",
    "ContextAsset",
    "ContextUpdate",
    "ConversationContext",
    "GeneratedPostRef",
    "RoutedTurn",
    "canonical_language",
    "canonical_platform",
    "detects_albanian",
    "explicit_generation_request",
    "explicit_revision_request",
    "extract_cta_intent",
    "extract_goal",
    "inferable_goal",
    "is_question",
    "normalize_text",
    "utcnow",
]
