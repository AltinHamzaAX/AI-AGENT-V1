import json
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.dependencies.assets import get_asset_storage
from app.dependencies.providers import get_provider_bundle
from app.infrastructure.database.base import Base
from app.infrastructure.database.session import get_db_transaction
from app.main import app
from app.modules.posts.domain.chat import (
    ChatIntent,
    ChatIntentRouter,
    ContextUpdate,
    ConversationContext,
    explicit_generation_request,
    explicit_revision_request,
    extract_cta_intent,
    extract_goal,
    is_question,
)
from app.modules.posts.domain.enums import PostWorkflowSection, UnderstandingField
from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ProviderBundle,
    ProviderError,
    StorageObjectNotFoundError,
)


class ScriptedLLM:
    """Answers the classifier from a script and the responder with prose.

    The two calls are told apart the way the adapters see them: only the
    classifier asks for structured output.
    """

    def __init__(self) -> None:
        self.classifications: list[dict[str, Any]] = []
        self.default_classification: dict[str, Any] = {"intent": "GENERAL_CONVERSATION"}
        self.reply = "Në rregull."
        self.classifier_requests: list[LLMRequest] = []
        self.responder_requests: list[LLMRequest] = []
        self.failure: Exception | None = None

    def script(self, *classifications: dict[str, Any]) -> None:
        self.classifications.extend(classifications)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self.failure is not None:
            raise self.failure
        if request.response_format == "json":
            self.classifier_requests.append(request)
            payload = (
                self.classifications.pop(0)
                if self.classifications
                else dict(self.default_classification)
            )
            return LLMResponse(
                text=json.dumps(payload, ensure_ascii=False),
                provider="scripted",
                model="scripted-router",
            )
        self.responder_requests.append(request)
        return LLMResponse(text=self.reply, provider="scripted", model="scripted-writer")


class FakeAssetStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def is_available(self) -> bool:
        return True

    async def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.objects[key] = data

    async def get(self, *, key: str) -> bytes:
        if key not in self.objects:
            raise StorageObjectNotFoundError(key)
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


class _Unused:
    """Providers the chat boundary must never reach during a turn."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the chat turn must not use provider capability '{name}'")


@pytest_asyncio.fixture
async def chat_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def chat_api(
    chat_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[AsyncClient, ScriptedLLM]]:
    llm = ScriptedLLM()
    unused: Any = _Unused()
    bundle = ProviderBundle(
        llm=llm,
        vision=unused,
        image=unused,
        embedding=unused,
        research=unused,
        storage=unused,
    )

    async def transaction_override() -> AsyncIterator[AsyncSession]:
        async with chat_session_factory.begin() as session:
            yield session

    app.dependency_overrides[get_db_transaction] = transaction_override
    app.dependency_overrides[get_provider_bundle] = lambda: bundle
    app.dependency_overrides[get_asset_storage] = FakeAssetStorage
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, llm
    finally:
        app.dependency_overrides.clear()


def _headers() -> dict[str, str]:
    return {"X-User-ID": str(uuid4()), "X-Project-ID": str(uuid4())}


def _classification(
    intent: str,
    *,
    context_updates: dict[str, Any] | None = None,
    revision_instructions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "reason": "scripted",
        "context_updates": context_updates or {},
        "revision_instructions": revision_instructions or [],
    }


#: A single client message that states every fact the assertions rely on, so
#: grounding keeps them: an entity the client never typed is dropped by design.
_BRIEF_MESSAGE = (
    "Dua ta promovoj Skoda Fabia për diasporën. Ma krijo një post për Instagram."
)

_FULL_BRIEF = {
    "business": "rent a car",
    "product_service": "Skoda Fabia",
    "goal": "me shume rezervime",
    "audience": "diaspora",
    "cta_intent": "rezervo tani",
    "platform": "Instagram",
}


async def _conversation(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/posts/conversations",
        headers=headers,
        json={"title": "AtomX Rent"},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


async def _turn(
    client: AsyncClient,
    headers: dict[str, str],
    conversation_id: str,
    content: str | None = None,
    *,
    message_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"metadata": {}}
    if message_id is not None:
        payload["message_id"] = message_id
    else:
        payload["content"] = content
    response = await client.post(
        f"/api/posts/conversations/{conversation_id}/turns",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (48, 32), color=(30, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


async def _state(
    client: AsyncClient,
    headers: dict[str, str],
    conversation_id: str,
) -> dict[str, Any]:
    response = await client.get(
        f"/api/posts/conversations/{conversation_id}/state",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _workflow_context(
    client: AsyncClient,
    headers: dict[str, str],
    workflow: dict[str, Any],
) -> dict[str, Any]:
    response = await client.get(
        f"/api/posts/{workflow['post_id']}/generations/{workflow['generation_id']}/state",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["state"][PostWorkflowSection.CONVERSATION_CONTEXT.value]


# --------------------------------------------------------------------------
# Deterministic routing rules
# --------------------------------------------------------------------------


def test_explicit_wording_is_recognized_without_false_positives() -> None:
    assert explicit_generation_request("Ma krijo një post për Instagram")
    assert explicit_generation_request("gjeneroje postin tani")
    assert explicit_generation_request("Generate the post please")
    assert explicit_generation_request("Kam njÃ« kafiteri dhe dua njÃ« post.")
    assert explicit_generation_request("I want an Instagram post for my cafe.")
    assert not explicit_generation_request("Kam një rent a car në Prishtinë.")
    assert not explicit_generation_request("Si mund të krijoj një post të mirë?")
    assert not explicit_generation_request("Dua ta promovoj Skoda Fabia.")

    assert explicit_revision_request("Bëje headline më të vogël dhe CTA më premium")
    assert explicit_revision_request("Change the headline")
    assert not explicit_revision_request("Faleminderit, shumë mirë!")
    assert is_question("Çfarë duhet të promovoj?")
    assert not is_question("Krijo një post.")


def test_router_never_generates_while_critical_facts_are_missing() -> None:
    routed = ChatIntentRouter().route(
        proposed=ChatIntent.GENERATE_POST,
        message="Ma krijo një post",
        context=ConversationContext(),
    )
    assert routed.intent is ChatIntent.MISSING_INFORMATION
    assert routed.action.value == "ask"
    assert routed.questions


def test_router_generates_once_the_required_facts_are_known() -> None:
    context = ConversationContext().merge(ContextUpdate(**_FULL_BRIEF))
    routed = ChatIntentRouter().route(
        proposed=ChatIntent.GENERAL_CONVERSATION,
        message="Ma krijo një post për Instagram",
        context=context,
    )
    assert routed.intent is ChatIntent.GENERATE_POST
    assert routed.action.value == "generate"
    assert context.generation_ready is True


def test_pending_generation_rechecks_readiness_without_a_second_command() -> None:
    context = ConversationContext().with_generation_request().merge(ContextUpdate(**_FULL_BRIEF))
    routed = ChatIntentRouter().route(
        proposed=ChatIntent.CLARIFICATION,
        message="Skoda Fabia. Target diaspora. Instagram.",
        context=context,
    )

    assert routed.intent is ChatIntent.GENERATE_POST
    assert routed.action.value == "generate"


def test_pending_generation_does_not_convert_general_conversation_into_work() -> None:
    context = ConversationContext().with_generation_request().merge(ContextUpdate(**_FULL_BRIEF))
    routed = ChatIntentRouter().route(
        proposed=ChatIntent.GENERAL_CONVERSATION,
        message="Faleminderit.",
        context=context,
    )

    assert routed.intent is ChatIntent.GENERAL_CONVERSATION
    assert routed.action.value == "reply"


def test_context_keeps_earlier_facts_and_records_inference_separately() -> None:
    context = ConversationContext().merge(ContextUpdate(business="rent a car", goal="shitje"))
    context = context.merge(ContextUpdate(product_service="Skoda Fabia"))
    assert context.business == "rent a car"
    assert context.goal == "shitje"
    assert context.product_service == "Skoda Fabia"
    assert UnderstandingField.BUSINESS not in context.missing_fields

    inferred = ConversationContext().merge(ContextUpdate(product_service="Skoda Fabia"))
    inferred = inferred.with_inferred(UnderstandingField.GOAL, "promote Skoda Fabia")
    assert inferred.goal == "promote Skoda Fabia"
    assert "goal" not in inferred.project_context()
    assert inferred.project_context()["product_service"] == "Skoda Fabia"


def test_misread_scalars_are_normalized_or_dropped() -> None:
    # A platform is a closed set: anything else in that slot is a misread.
    assert ContextUpdate(platform="a post for Instagram").platform == "Instagram"
    assert ContextUpdate(platform="tiktok").platform == "TikTok"
    assert ContextUpdate(platform="në Prishtinë").platform is None
    # Language names decide the wording of clarification questions.
    assert ContextUpdate(language="alb").language == "shqip"
    assert ContextUpdate(language="Albanian").language == "shqip"
    assert ContextUpdate(language="eng").language == "english"


def test_a_stated_call_to_action_is_recovered_when_the_model_omits_it() -> None:
    assert extract_cta_intent("I want them to book now.") == "book now"
    assert extract_cta_intent("dua qe te rezervojne tani") == "rezervojne tani"
    assert extract_cta_intent("they should visit us") == "visit us"
    # A noun that merely mentions the action is not an instruction to take it.
    assert extract_cta_intent("I like the booking process") is None
    assert extract_cta_intent("Hello there!") is None


def test_a_stated_outcome_is_recovered_when_the_model_omits_it() -> None:
    assert extract_goal("promote the Fabia and get more bookings") == "get more bookings"
    assert extract_goal("dua me shume rezervime") == "me shume rezervime"
    assert extract_goal("increase traffic to the site") == "increase traffic"
    # A style word after "more" is a preference, not a business outcome.
    assert extract_goal("make the CTA more premium") is None
    assert extract_goal("Hello there!") is None


def test_an_order_survives_a_qualifier_before_the_noun() -> None:
    assert explicit_generation_request("Great, now create the Instagram post.")
    assert explicit_generation_request("make me a quick post")
    assert not explicit_generation_request("I like the post")


def test_clarification_questions_follow_the_client_language() -> None:
    albanian = ConversationContext().merge(
        ContextUpdate(product_service="Skoda Fabia", language="shqip")
    )
    question = albanian.clarification().questions[0].question
    assert question.startswith("Cili është rezultati")


# --------------------------------------------------------------------------
# Conversational turns
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_general_conversation_answers_without_starting_generation(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("GENERAL_CONVERSATION"))
    llm.reply = "Përshëndetje! Si mund të ndihmoj?"

    turn = await _turn(client, headers, conversation_id, "Përshëndetje!")

    assert turn["intent"] == "GENERAL_CONVERSATION"
    assert turn["action"] == "reply"
    assert turn["workflow"] is None
    assert turn["assistant"]["content"] == "Përshëndetje! Si mund të ndihmoj?"
    assert turn["assistant"]["metadata"]["chat"]["intent"] == "GENERAL_CONVERSATION"
    assert (await _state(client, headers, conversation_id))["post_id"] is None


@pytest.mark.asyncio
async def test_marketing_question_is_answered_and_starts_nothing(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("MARKETING_QUESTION", context_updates=_FULL_BRIEF))
    llm.reply = "Për diasporën, provo një ofertë sezonale me CTA të drejtpërdrejtë."

    turn = await _turn(
        client,
        headers,
        conversation_id,
        "Çfarë funksionon më mirë për diasporën në Instagram?",
    )

    assert turn["intent"] == "MARKETING_QUESTION"
    assert turn["action"] == "reply"
    assert turn["workflow"] is None
    assert (await _state(client, headers, conversation_id))["post_id"] is None


@pytest.mark.asyncio
async def test_missing_information_asks_instead_of_generating(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("GENERATE_POST"))

    turn = await _turn(client, headers, conversation_id, "Ma krijo një post")

    assert turn["intent"] == "MISSING_INFORMATION"
    assert turn["action"] == "ask"
    assert turn["questions"]
    assert turn["workflow"] is None
    assert turn["assistant"]["metadata"]["chat"]["questions"] == turn["questions"]
    assert turn["generation_ready"] is False
    assert turn["context"]["generation_ready"] is False


@pytest.mark.asyncio
async def test_answering_missing_facts_automatically_starts_the_pending_request(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(
        _classification(
            "GENERATE_POST",
            context_updates={"business": "rent a car", "product_service": "Skoda Fabia"},
        ),
        _classification(
            "CLARIFICATION",
            context_updates={
                "goal": "me shume rezervime",
                "audience": "diaspora",
                "cta_intent": "rezervo tani",
                "platform": "Instagram",
            },
        ),
    )

    first = await _turn(client, headers, conversation_id, "Dua njÃ« post pÃ«r Skoda Fabia.")
    second = await _turn(
        client,
        headers,
        conversation_id,
        "Target diaspora; dua mÃ« shumÃ« rezervime. Instagram. CTA rezervo tani.",
    )

    assert first["action"] == "ask"
    assert first["generation_ready"] is False
    assert second["intent"] == "GENERATE_POST"
    assert second["action"] == "generate"
    assert second["generation_ready"] is True
    assert second["workflow"] is not None


@pytest.mark.asyncio
async def test_generate_post_starts_the_workflow_with_the_conversation_brief(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(
        _classification("CLARIFICATION", context_updates={"business": "rent a car"}),
        _classification("GENERATE_POST", context_updates=_FULL_BRIEF),
    )

    await _turn(client, headers, conversation_id, "Kam një rent a car në Prishtinë.")
    turn = await _turn(
        client,
        headers,
        conversation_id,
        _BRIEF_MESSAGE,
    )

    assert turn["intent"] == "GENERATE_POST"
    assert turn["action"] == "generate"
    workflow = turn["workflow"]
    assert workflow is not None and workflow["attempt"] == 1

    seeded = await _workflow_context(client, headers, workflow)
    assert seeded["latest_message"].startswith("Dua ta promovoj")
    assert seeded["project_context"]["product_service"] == "Skoda Fabia"
    assert seeded["project_context"]["platform"] == "Instagram"
    assert [turn_item["role"] for turn_item in seeded["conversation_history"]] == [
        "user",
        "assistant",
    ]
    assert all(
        item["content"] != seeded["latest_message"]
        for item in seeded["conversation_history"]
    )


@pytest.mark.asyncio
async def test_earlier_facts_are_reused_and_never_asked_again(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(
        _classification("CLARIFICATION", context_updates={"business": "rent a car"}),
        _classification("CLARIFICATION", context_updates={"product_service": "Skoda Fabia"}),
        _classification("CLARIFICATION", context_updates={"goal": "me shume rezervime"}),
    )

    await _turn(client, headers, conversation_id, "Kam një rent a car në Prishtinë.")
    await _turn(client, headers, conversation_id, "Dua ta promovoj Skoda Fabia.")
    turn = await _turn(client, headers, conversation_id, "Synimi është më shumë rezervime.")

    context = turn["context"]
    assert context["business"] == "rent a car"
    assert context["product_service"] == "Skoda Fabia"
    assert context["goal"] == "me shume rezervime"
    assert "business" not in context["missing_fields"]

    known = json.loads(llm.classifier_requests[-1].messages[1].content)["known_context"]
    assert known["business"] == "rent a car"
    assert known["product_service"] == "Skoda Fabia"


@pytest.mark.asyncio
async def test_attachments_join_the_context_and_the_generation_brief(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    message = await client.post(
        f"/api/posts/conversations/{conversation_id}/messages",
        headers=headers,
        json={
            "content": (
                "Kjo është logoja dhe vetura Skoda Fabia. "
                "Ma krijo një post për Instagram."
            )
        },
    )
    assert message.status_code == 201
    message_id = str(message.json()["id"])
    upload = await client.post(
        "/api/assets",
        headers=headers,
        data={"message_id": message_id, "role": "logo"},
        files={"file": ("logo.png", _image_bytes(), "image/png")},
    )
    assert upload.status_code == 201, upload.text

    llm.script(_classification("GENERATE_POST", context_updates=_FULL_BRIEF))
    turn = await _turn(client, headers, conversation_id, message_id=message_id)

    assert turn["user"]["id"] == message_id
    attachments = turn["context"]["attachments"]
    assert [item["role"] for item in attachments] == ["logo"]
    assert attachments[0]["original_filename"] == "logo.png"

    seeded = await _workflow_context(client, headers, turn["workflow"])
    assert seeded["attachments"][0]["role"] == "logo"
    assert seeded["attachments"][0]["mime_type"] == "image/png"
    assert UUID(seeded["attachments"][0]["id"]) == UUID(attachments[0]["id"])

    briefing = llm.responder_requests[-1].messages[2].content
    assert "logo.png" in briefing


@pytest.mark.asyncio
async def test_revision_before_any_result_records_the_preference_only(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("REVISE_POST", revision_instructions=["Zvogëlo headline"]))

    turn = await _turn(client, headers, conversation_id, "Bëje headline më të vogël.")

    assert turn["intent"] == "CLARIFICATION"
    assert turn["action"] == "reply"
    assert turn["workflow"] is None


@pytest.mark.asyncio
async def test_revise_post_starts_a_new_attempt_that_references_the_previous(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(
        _classification("GENERATE_POST", context_updates=_FULL_BRIEF),
        _classification(
            "REVISE_POST",
            revision_instructions=["Zvogëlo headline", "Bëje CTA më premium"],
        ),
    )

    generated = await _turn(
        client,
        headers,
        conversation_id,
        "Ma krijo një post për Instagram për Skoda Fabia.",
    )
    revised = await _turn(
        client,
        headers,
        conversation_id,
        "Më pëlqen, por bëje headline më të vogël dhe CTA më premium.",
    )

    assert revised["intent"] == "REVISE_POST"
    assert revised["action"] == "revise"
    assert revised["workflow"]["post_id"] == generated["workflow"]["post_id"]
    assert revised["workflow"]["generation_id"] != generated["workflow"]["generation_id"]
    assert revised["workflow"]["attempt"] == 2
    assert (
        revised["workflow"]["revises_generation_id"] == generated["workflow"]["generation_id"]
    )
    assert revised["context"]["revision_instructions"] == [
        "Zvogëlo headline",
        "Bëje CTA më premium",
    ]

    seeded = await _workflow_context(client, headers, revised["workflow"])
    assert seeded["project_context"]["revision_instructions"] == [
        "Zvogëlo headline",
        "Bëje CTA më premium",
    ]


@pytest.mark.asyncio
async def test_classifier_cannot_start_generation_the_facts_do_not_support(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("GENERATE_POST"))

    turn = await _turn(client, headers, conversation_id, "Përshëndetje, si je?")

    assert turn["intent"] == "MISSING_INFORMATION"
    assert turn["workflow"] is None


@pytest.mark.asyncio
async def test_explicit_order_overrides_a_conversational_classification(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("GENERAL_CONVERSATION", context_updates=_FULL_BRIEF))

    turn = await _turn(client, headers, conversation_id, _BRIEF_MESSAGE)

    assert turn["intent"] == "GENERATE_POST"
    assert turn["workflow"] is not None


@pytest.mark.asyncio
async def test_repeated_explicit_order_proceeds_on_an_inferred_goal(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    # Everything a post needs except the outcome, which is what the repeated
    # order is allowed to infer.
    subject = {
        "business": "rent a car",
        "product_service": "Skoda Fabia",
        "audience": "diaspora",
        "cta_intent": "rezervo tani",
    }
    llm.script(
        _classification("GENERATE_POST", context_updates=subject),
        _classification("GENERATE_POST"),
    )

    first = await _turn(client, headers, conversation_id, "Ma krijo një post për Skoda Fabia.")
    second = await _turn(client, headers, conversation_id, "Gjeneroje postin.")

    assert first["intent"] == "MISSING_INFORMATION"
    assert first["workflow"] is None
    assert second["intent"] == "GENERATE_POST"
    assert second["workflow"] is not None

    seeded = await _workflow_context(client, headers, second["workflow"])
    assert "goal" not in seeded["project_context"]
    assert seeded["project_context"]["product_service"] == "Skoda Fabia"


@pytest.mark.asyncio
async def test_invented_entities_are_dropped_from_the_context(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(
        _classification(
            "CLARIFICATION",
            context_updates={
                "business": "rent a car",
                "brand": "Hertz Kosova",
                "goal": "më shumë rezervime",
            },
        )
    )

    turn = await _turn(client, headers, conversation_id, "Kam një rent a car në Prishtinë.")

    assert turn["context"]["business"] == "rent a car"
    assert turn["context"]["brand"] is None
    assert turn["context"]["goal"] == "më shumë rezervime"


@pytest.mark.asyncio
async def test_the_client_language_is_read_from_their_own_words(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    english = await _conversation(client, headers)
    albanian = await _conversation(client, headers)
    llm.script(
        _classification("GENERAL_CONVERSATION", context_updates={"language": "shqip"}),
        _classification("GENERAL_CONVERSATION", context_updates={"language": "english"}),
    )

    english_turn = await _turn(client, headers, english, "Hello, I run a car rental company.")
    albanian_turn = await _turn(client, headers, albanian, "Kam nje kafiteri dhe dua nje post.")

    assert english_turn["context"]["language"] is None
    assert albanian_turn["context"]["language"] == "shqip"


@pytest.mark.asyncio
async def test_a_translated_fact_is_not_the_client_fact(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    # The model answers in a script the client never typed a character of.
    llm.script(
        _classification(
            "CLARIFICATION",
            context_updates={
                "goal": "获得更多预订",
                "audience": "Albanian diaspora",
                "language": "zh",
                "constraints": ["不要更改标志", "keep the logo"],
            },
        )
    )

    turn = await _turn(
        client,
        headers,
        conversation_id,
        "I run a car rental company and want more bookings.",
    )

    context = turn["context"]
    assert context["goal"] == "want more bookings"  # recovered from the client sentence
    assert context["audience"] == "Albanian diaspora"
    assert context["constraints"] == ["keep the logo"]
    assert context["language"] is None


@pytest.mark.asyncio
async def test_turns_are_rejected_for_campaign_conversations(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, _ = chat_api
    headers = _headers()
    campaign = await client.post(
        "/api/campaigns/conversations",
        headers=headers,
        json={"title": "Summer"},
    )
    assert campaign.status_code == 201

    response = await client.post(
        f"/api/posts/conversations/{campaign.json()['id']}/turns",
        headers=headers,
        json={"content": "Ma krijo një post"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_failing_assistant_leaves_no_half_finished_turn(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.failure = ProviderError("provider down")

    response = await client.post(
        f"/api/posts/conversations/{conversation_id}/turns",
        headers=headers,
        json={"content": "Ma krijo një post për Instagram."},
    )
    assert response.status_code == 502

    history = await client.get(
        f"/api/posts/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert history.json()["items"] == []
    assert (await _state(client, headers, conversation_id))["post_id"] is None


@pytest.mark.asyncio
async def test_turn_requires_exactly_one_message_source(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, _ = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)

    both = await client.post(
        f"/api/posts/conversations/{conversation_id}/turns",
        headers=headers,
        json={"content": "Hi", "message_id": str(uuid4())},
    )
    neither = await client.post(
        f"/api/posts/conversations/{conversation_id}/turns",
        headers=headers,
        json={},
    )
    unknown = await client.post(
        f"/api/posts/conversations/{conversation_id}/turns",
        headers=headers,
        json={"message_id": str(uuid4())},
    )
    assert both.status_code == 422
    assert neither.status_code == 422
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_explicit_command_starts_and_then_reuses_the_same_generation(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("CLARIFICATION", context_updates=_FULL_BRIEF))
    await _turn(client, headers, conversation_id, "Dua ta promovoj Skoda Fabia për diasporën.")

    first = await client.post(
        f"/api/posts/conversations/{conversation_id}/generations",
        headers=headers,
    )
    second = await client.post(
        f"/api/posts/conversations/{conversation_id}/generations",
        headers=headers,
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert first.json()["generation_id"] == second.json()["generation_id"]

    seeded = await _workflow_context(client, headers, first.json())
    assert seeded["project_context"]["product_service"] == "Skoda Fabia"
    assert seeded["latest_message"].startswith("Dua ta promovoj")


@pytest.mark.asyncio
async def test_state_restores_context_and_the_latest_generation(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("GENERATE_POST", context_updates=_FULL_BRIEF))
    turn = await _turn(client, headers, conversation_id, _BRIEF_MESSAGE)

    state = await _state(client, headers, conversation_id)

    assert state["post_id"] == turn["workflow"]["post_id"]
    assert state["generation"]["id"] == turn["workflow"]["generation_id"]
    assert state["generation"]["job_status"] == "queued"
    assert state["artifacts"] == []
    assert state["context"]["product_service"] == "Skoda Fabia"
    assert state["context"]["generated_posts"][0]["generation_id"] == (
        turn["workflow"]["generation_id"]
    )


@pytest.mark.asyncio
async def test_both_sides_of_the_conversation_are_persisted_in_order(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script(_classification("GENERAL_CONVERSATION"), _classification("GENERAL_CONVERSATION"))
    llm.reply = "Sigurisht."

    await _turn(client, headers, conversation_id, "Përshëndetje")
    await _turn(client, headers, conversation_id, "Faleminderit")

    history = await client.get(
        f"/api/posts/conversations/{conversation_id}/messages",
        headers=headers,
    )
    items = history.json()["items"]
    assert [item["role"] for item in items] == ["user", "assistant", "user", "assistant"]
    assert [item["sequence"] for item in items] == [1, 2, 3, 4]
    assert items[1]["content"] == "Sigurisht."


@pytest.mark.asyncio
async def test_a_schema_echoed_back_is_not_mistaken_for_an_answer(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    # A model handed a JSON Schema sometimes answers with the schema itself.
    llm.script(
        {"$defs": {"ChatIntent": {"enum": ["GENERATE_POST"]}}, "properties": {}},
        _classification("GENERAL_CONVERSATION"),
    )

    turn = await _turn(client, headers, conversation_id, "Hello")

    assert turn["intent"] == "GENERAL_CONVERSATION"
    assert len(llm.classifier_requests) == 2


@pytest.mark.asyncio
async def test_malformed_classifier_output_is_retried_once(
    chat_api: tuple[AsyncClient, ScriptedLLM],
) -> None:
    client, llm = chat_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)
    llm.script({"intent": "NOT_AN_INTENT"}, _classification("GENERAL_CONVERSATION"))

    turn = await _turn(client, headers, conversation_id, "Përshëndetje")

    assert turn["intent"] == "GENERAL_CONVERSATION"
    assert len(llm.classifier_requests) == 2
