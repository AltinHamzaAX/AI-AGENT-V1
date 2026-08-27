import json
import unicodedata
from typing import Any

from pydantic import BaseModel, ValidationError

from app.modules.posts.agents.framework import AgentExecutionContext, AgentRuntime
from app.modules.posts.domain.contracts import (
    SPECIALIST_TIMEOUT_SECONDS,
    AgentDefinition,
    RetryPolicy,
)
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.providers import LLMMessage, LLMProvider, LLMRequest, ProviderResponseError
from app.modules.posts.tools import ToolGateway
from app.shared.assets.domain import AssetRole

from .schemas import (
    AssetAttachmentInput,
    AssetIntelligenceInput,
    AssetIntelligenceLLMOutput,
    AssetIntelligenceResult,
    AssetPolicy,
    AssetRoleClassification,
    IntelligentAssetRole,
)

ASSET_INTELLIGENCE_AGENT_NAME = "asset_intelligence"

ASSET_INTELLIGENCE_DEFINITION = AgentDefinition(
    name=ASSET_INTELLIGENCE_AGENT_NAME,
    role="Classify user assets and assign immutable composition safety policies",
    input_schema=AssetIntelligenceInput,
    output_schema=AssetIntelligenceResult,
    allowed_tools=frozenset(),
    timeout_seconds=SPECIALIST_TIMEOUT_SECONDS,
    retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True, retry_on_error=True),
)

_DECLARED_ROLE_MAP = {
    AssetRole.LOGO: IntelligentAssetRole.BRAND_LOGO,
    AssetRole.PRODUCT: IntelligentAssetRole.PRIMARY_PRODUCT,
    AssetRole.VEHICLE: IntelligentAssetRole.VEHICLE,
    AssetRole.PACKAGING: IntelligentAssetRole.PACKAGING,
    AssetRole.ENVIRONMENT: IntelligentAssetRole.ENVIRONMENT,
    AssetRole.BACKGROUND: IntelligentAssetRole.BACKGROUND_REFERENCE,
    AssetRole.PERSON: IntelligentAssetRole.SUPPORTING_ASSET,
    AssetRole.STYLE_REFERENCE: IntelligentAssetRole.STYLE_REFERENCE,
    AssetRole.INSPIRATION: IntelligentAssetRole.INSPIRATION_ONLY,
    AssetRole.SUPPORTING_ASSET: IntelligentAssetRole.SUPPORTING_ASSET,
}
_AUTHORITATIVE_DECLARED_ROLES = frozenset(
    {
        AssetRole.LOGO,
        AssetRole.PRODUCT,
        AssetRole.PACKAGING,
        AssetRole.ENVIRONMENT,
        AssetRole.BACKGROUND,
        AssetRole.STYLE_REFERENCE,
        AssetRole.INSPIRATION,
    }
)
_PROTECTED_ROLES = frozenset(
    {
        IntelligentAssetRole.BRAND_LOGO,
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
)
_DOMINANCE = {
    IntelligentAssetRole.BRAND_LOGO: (0.03, 0.20),
    IntelligentAssetRole.PRIMARY_PRODUCT: (0.30, 0.85),
    IntelligentAssetRole.VEHICLE: (0.25, 0.85),
    IntelligentAssetRole.PACKAGING: (0.20, 0.80),
    IntelligentAssetRole.ENVIRONMENT: (0.10, 1.00),
    IntelligentAssetRole.BACKGROUND_REFERENCE: (0.00, 1.00),
    IntelligentAssetRole.STYLE_REFERENCE: (0.00, 0.00),
    IntelligentAssetRole.SUPPORTING_ASSET: (0.00, 0.45),
    IntelligentAssetRole.INSPIRATION_ONLY: (0.00, 0.00),
}
_PRIMARY_INTENT_MARKERS = frozenset(
    {
        "duhet te perdoret",
        "duhet të përdoret",
        "must be used",
        "primary product",
        "produkti kryesor",
        "this is the car",
        "this is the vehicle",
        "kjo eshte vetura",
        "kjo është vetura",
    }
)


class AssetIntelligenceAgent:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def execute(
        self,
        payload: BaseModel,
        _gateway: ToolGateway,
        _context: AgentExecutionContext,
    ) -> AssetIntelligenceResult:
        if not isinstance(payload, AssetIntelligenceInput):
            raise TypeError("asset intelligence received an invalid input type")
        contract = _validated_contract(payload)
        if not payload.attachments:
            return AssetIntelligenceResult(assets=[], contract_fingerprint=contract.fingerprint)

        response = await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=_system_prompt()),
                    LLMMessage(
                        role="user",
                        content=json.dumps(_classification_source(payload), sort_keys=True),
                    ),
                ),
                temperature=0,
                response_format="json",
            )
        )
        try:
            proposed = AssetIntelligenceLLMOutput.model_validate(_parse_json_object(response.text))
            policies = _build_policies(payload, contract, proposed.classifications)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError(
                "asset intelligence returned invalid structured output"
            ) from exc
        return AssetIntelligenceResult(
            assets=policies,
            contract_fingerprint=contract.fingerprint,
        )


def register_asset_intelligence_agent(runtime: AgentRuntime, llm: LLMProvider) -> None:
    agent = AssetIntelligenceAgent(llm)
    runtime.register(ASSET_INTELLIGENCE_DEFINITION, agent.execute)


def validate_asset_intelligence_input(
    payload: AssetIntelligenceInput,
) -> PostSemanticContract:
    """Fail before provider execution when immutable asset prerequisites are invalid."""
    return _validated_contract(payload)


def _validated_contract(payload: AssetIntelligenceInput) -> PostSemanticContract:
    try:
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("asset intelligence requires a valid semantic contract") from exc
    available = {attachment.id for attachment in payload.attachments}
    missing = set(contract.required_assets) - available
    if missing:
        identifiers = ", ".join(sorted(str(identifier) for identifier in missing))
        raise ValueError(f"required assets are absent from workflow context: {identifiers}")
    return contract


def _system_prompt() -> str:
    schema = json.dumps(AssetIntelligenceLLMOutput.model_json_schema(), sort_keys=True)
    return (
        "You are the Asset Intelligence specialist in a marketing-post workflow. "
        "Classify every attachment exactly once. User intent has highest priority. "
        "The declared role is authoritative for logo, product, packaging, environment, "
        "background, style-reference, and inspiration attachments. A declared vehicle may "
        "become primary_product only when an exact user quote says that this specific vehicle "
        "is the subject or must be used. Ambiguous supporting assets may be reclassified only "
        "with an exact user quote. Copy that quote verbatim into user_intent_evidence; otherwise "
        "use null. Never invent an asset, omit an asset, alter an ID, or make composition, "
        "marketing, copy, or design decisions. Return exactly one JSON object matching this "
        f"schema and no prose or markdown: {schema}"
    )


def _classification_source(payload: AssetIntelligenceInput) -> dict[str, Any]:
    return {
        "latest_message": payload.latest_message,
        "conversation_history": payload.conversation_history,
        "attachments": [
            {
                "asset_id": str(attachment.id),
                "declared_role": attachment.declared_role.value,
                "original_filename": attachment.original_filename,
                "mime_type": attachment.mime_type,
                "width": attachment.width,
                "height": attachment.height,
                "metadata": attachment.metadata,
            }
            for attachment in payload.attachments
        ],
    }


def _build_policies(
    payload: AssetIntelligenceInput,
    contract: PostSemanticContract,
    classifications: list[AssetRoleClassification],
) -> list[AssetPolicy]:
    by_id: dict[Any, AssetRoleClassification] = {}
    for classification in classifications:
        if classification.asset_id in by_id:
            raise ValueError("every asset must be classified exactly once")
        by_id[classification.asset_id] = classification
    expected_ids = {attachment.id for attachment in payload.attachments}
    if set(by_id) != expected_ids:
        raise ValueError("classification IDs must exactly match attachment IDs")

    conversation = "\n".join([*payload.conversation_history, payload.latest_message])
    policies = []
    for attachment in payload.attachments:
        proposal = by_id[attachment.id]
        role, evidence = _ground_role(attachment, proposal, conversation)
        required = attachment.id in contract.required_assets or role in {
            IntelligentAssetRole.BRAND_LOGO,
            IntelligentAssetRole.PRIMARY_PRODUCT,
        }
        preserve_identity = role in _PROTECTED_ROLES
        minimum, maximum = _DOMINANCE[role]
        policies.append(
            AssetPolicy(
                asset_id=attachment.id,
                original_filename=attachment.original_filename,
                role=role,
                required=required,
                preserve_identity=preserve_identity,
                allow_crop=role
                not in {
                    IntelligentAssetRole.BRAND_LOGO,
                    IntelligentAssetRole.STYLE_REFERENCE,
                    IntelligentAssetRole.INSPIRATION_ONLY,
                },
                allow_replace=not preserve_identity and not required,
                allow_generation=not preserve_identity
                and not required
                and role not in {IntelligentAssetRole.SUPPORTING_ASSET},
                min_dominance=minimum,
                max_dominance=maximum,
                user_intent_evidence=evidence,
                classification_reason=proposal.reason,
                contract_fingerprint=contract.fingerprint,
            )
        )
    return policies


def _ground_role(
    attachment: AssetAttachmentInput,
    proposal: AssetRoleClassification,
    conversation: str,
) -> tuple[IntelligentAssetRole, str | None]:
    declared = _DECLARED_ROLE_MAP[attachment.declared_role]
    if attachment.declared_role in _AUTHORITATIVE_DECLARED_ROLES:
        if proposal.role is not declared:
            raise ValueError("provider attempted to override an authoritative declared role")
        return declared, _ground_evidence(proposal.user_intent_evidence, conversation)

    if proposal.role is declared:
        return declared, _ground_evidence(proposal.user_intent_evidence, conversation)

    evidence = _ground_evidence(proposal.user_intent_evidence, conversation)
    if evidence is None:
        raise ValueError("role override requires exact user-intent evidence")
    if attachment.declared_role is AssetRole.VEHICLE:
        if proposal.role is not IntelligentAssetRole.PRIMARY_PRODUCT:
            raise ValueError("a vehicle can only be promoted to primary_product")
        normalized = _semantic(evidence)
        if not any(_semantic(marker) in normalized for marker in _PRIMARY_INTENT_MARKERS):
            raise ValueError("vehicle override lacks explicit primary-product intent")
        return proposal.role, evidence
    if attachment.declared_role in {AssetRole.SUPPORTING_ASSET, AssetRole.PERSON}:
        return proposal.role, evidence
    raise ValueError("declared asset role cannot be overridden")


def _ground_evidence(value: str | None, conversation: str) -> str | None:
    if value is None:
        return None
    evidence = " ".join(value.split())
    if not evidence or _semantic(evidence) not in _semantic(conversation):
        raise ValueError("user-intent evidence must be an exact conversation quote")
    return evidence


def _semantic(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(character for character in normalized if not unicodedata.combining(character))
        .replace("'", " ")
        .replace('"', " ")
        .split()
    )


def _parse_json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("provider output must be a JSON object")
    return parsed


__all__ = [
    "ASSET_INTELLIGENCE_AGENT_NAME",
    "ASSET_INTELLIGENCE_DEFINITION",
    "AssetIntelligenceAgent",
    "register_asset_intelligence_agent",
    "validate_asset_intelligence_input",
]
