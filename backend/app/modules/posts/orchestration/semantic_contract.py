"""Freeze the understood brief into the write-once semantic contract.

Client-declared facts always win. Fields that the clarification policy resolves
without asking use conservative working defaults so a short brief can proceed;
these defaults make no offer, price, performance, or product claim.
"""

from typing import Any
from uuid import UUID

from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)

#: Working values for the fields the clarification policy classifies as
#: inferable. They shape delivery, never a claim about the client's business,
#: which is why they can be defaulted while an unstated audience cannot.
DEFAULT_PLATFORM = "Instagram"
DEFAULT_LANGUAGE = "English"
DEFAULT_CTA_INTENT = "Learn more"

#: Roles whose asset must survive into the render untouched.
_IDENTITY_ROLES = frozenset({"logo", "product", "vehicle", "packaging"})


class SemanticContractStageHandler:
    """Derive the immutable contract from the brief the client actually gave."""

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        brief = _brief(context.workflow_state)
        subject = _subject(brief)
        if subject is None:
            raise NonRetryableJobError("semantic contract has no subject to promote")
        contract = PostSemanticContract.create(
            company=_text(brief.get("business")),
            brand=_text(brief.get("brand")),
            product=_text(brief.get("product_service")),
            primary_entity=subject,
            goal=_text(brief.get("goal")) or f"Build awareness and consideration for {subject}",
            audience=_text(brief.get("audience")) or f"People interested in {subject}",
            market=_text(brief.get("market")),
            location=_text(brief.get("location")),
            offer=_text(brief.get("offer")),
            cta_intent=_text(brief.get("cta_intent")) or DEFAULT_CTA_INTENT,
            platform=_text(brief.get("platform")) or DEFAULT_PLATFORM,
            language=_text(brief.get("language")) or DEFAULT_LANGUAGE,
            required_facts=_required_facts(brief),
            forbidden_claims=[],
            required_assets=_required_assets(brief),
            constraints=_string_list(brief.get("constraints")),
        )
        return SupervisorStageResult(
            outputs={PostWorkflowSection.SEMANTIC_CONTRACT: contract.to_dict()}
        )


def _brief(workflow_state: dict[str, Any]) -> dict[str, Any]:
    brief = workflow_state.get(PostWorkflowSection.BRIEF.value)
    if not isinstance(brief, dict) or not brief:
        raise NonRetryableJobError("client brief must be an object")
    return brief


def _subject(brief: dict[str, Any]) -> str | None:
    """The promoted subject, following the clarification policy's own order."""
    for field in ("product_service", "business", "brand"):
        value = _text(brief.get(field))
        if value is not None:
            return value
    return None


def _required_facts(brief: dict[str, Any]) -> dict[str, str]:
    """Client statements the render must keep verbatim."""
    facts = {}
    for field in ("offer", "location", "product_service"):
        value = _text(brief.get(field))
        if value is not None:
            facts[field] = value
    return facts


def _required_assets(brief: dict[str, Any]) -> list[UUID]:
    assets = brief.get("assets")
    if not isinstance(assets, list):
        return []
    required = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        preserve = asset.get("preserve_identity") is True
        role = asset.get("role")
        if preserve or (isinstance(role, str) and role in _IDENTITY_ROLES):
            try:
                required.append(UUID(str(asset.get("id"))))
            except (TypeError, ValueError) as exc:
                raise NonRetryableJobError("brief asset has an invalid identifier") from exc
    return required


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for candidate in value if (item := _text(candidate)) is not None]


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


__all__ = [
    "DEFAULT_LANGUAGE",
    "DEFAULT_PLATFORM",
    "DEFAULT_CTA_INTENT",
    "SemanticContractStageHandler",
]
