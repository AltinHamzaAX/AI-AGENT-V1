from typing import Any
from uuid import UUID, uuid4

import pytest

from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import SupervisorAction
from app.modules.posts.orchestration import SupervisorStageContext
from app.modules.posts.orchestration.semantic_contract import (
    DEFAULT_LANGUAGE,
    DEFAULT_PLATFORM,
    SemanticContractStageHandler,
)

LOGO_ID = UUID("11111111-1111-4111-8111-111111111111")
INSPIRATION_ID = UUID("22222222-2222-4222-8222-222222222222")


def _brief(**overrides: Any) -> dict[str, Any]:
    brief = {
        "business": "car rental",
        "brand": "AtomX Rent",
        "product_service": "Skoda Fabia",
        "goal": "more bookings",
        "audience": "Albanian diaspora",
        "market": None,
        "location": "Prishtina",
        "platform": "Instagram",
        "language": "English",
        "offer": "€35/day",
        "cta_intent": "book now",
        "style_preferences": [],
        "constraints": ["Do not replace the logo"],
        "assets": [
            {
                "id": str(LOGO_ID),
                "role": "logo",
                "original_filename": "logo.png",
                "preserve_identity": True,
            },
            {
                "id": str(INSPIRATION_ID),
                "role": "inspiration",
                "original_filename": "mood.png",
                "preserve_identity": False,
            },
        ],
        "missing_fields": [],
    }
    brief.update(overrides)
    return brief


def _context(brief: dict[str, Any] | None) -> SupervisorStageContext:
    state = empty_workflow_state()
    if brief is not None:
        state[PostWorkflowSection.BRIEF.value] = brief
    return SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )


async def _run(brief: dict[str, Any] | None) -> dict[str, Any]:
    result = await SemanticContractStageHandler().execute(_context(brief))
    assert set(result.outputs) == {PostWorkflowSection.SEMANTIC_CONTRACT}
    return result.outputs[PostWorkflowSection.SEMANTIC_CONTRACT]


@pytest.mark.asyncio
async def test_the_contract_freezes_exactly_what_the_client_stated() -> None:
    contract = await _run(_brief())

    assert contract["company"] == "car rental"
    assert contract["brand"] == "AtomX Rent"
    assert contract["product"] == "Skoda Fabia"
    assert contract["primary_entity"] == "Skoda Fabia"
    assert contract["goal"] == "more bookings"
    assert contract["audience"] == "Albanian diaspora"
    assert contract["location"] == "Prishtina"
    assert contract["offer"] == "€35/day"
    assert contract["cta_intent"] == "book now"
    assert contract["market"] is None
    assert contract["constraints"] == ["Do not replace the logo"]
    # Nothing is asserted about the render that the client did not claim.
    assert contract["forbidden_claims"] == []
    # The stored fingerprint verifies on load, which is how drift is caught.
    assert PostSemanticContract.from_dict(contract).fingerprint == contract["fingerprint"]


@pytest.mark.asyncio
async def test_only_identity_assets_become_required_assets() -> None:
    contract = await _run(_brief())

    assert contract["required_assets"] == [str(LOGO_ID)]


@pytest.mark.asyncio
async def test_stated_facts_are_carried_as_required_facts() -> None:
    contract = await _run(_brief())

    facts = dict(contract["required_facts"])
    assert facts["offer"] == "€35/day"
    assert facts["location"] == "Prishtina"
    assert facts["product_service"] == "Skoda Fabia"


@pytest.mark.asyncio
async def test_the_subject_falls_back_through_the_clarification_order() -> None:
    without_product = await _run(_brief(product_service=None))
    without_business = await _run(_brief(product_service=None, business=None))

    assert without_product["primary_entity"] == "car rental"
    assert without_product["product"] is None
    assert without_business["primary_entity"] == "AtomX Rent"


@pytest.mark.asyncio
async def test_delivery_fields_fall_back_to_the_declared_working_values() -> None:
    contract = await _run(_brief(platform=None, language=None))

    assert contract["platform"] == DEFAULT_PLATFORM
    assert contract["language"] == DEFAULT_LANGUAGE


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["goal", "audience", "cta_intent"])
async def test_a_fact_no_stage_can_derive_stops_the_workflow(field: str) -> None:
    with pytest.raises(NonRetryableJobError) as error:
        await _run(_brief(**{field: None}))

    assert field in str(error.value)


@pytest.mark.asyncio
async def test_a_blank_value_counts_as_missing() -> None:
    with pytest.raises(NonRetryableJobError):
        await _run(_brief(audience="   "))


@pytest.mark.asyncio
async def test_the_stage_refuses_to_run_without_a_brief() -> None:
    with pytest.raises(NonRetryableJobError):
        await _run(None)

    with pytest.raises(NonRetryableJobError):
        await _run({})


@pytest.mark.asyncio
async def test_a_brief_with_no_subject_at_all_is_refused() -> None:
    with pytest.raises(NonRetryableJobError, match="subject"):
        await _run(_brief(product_service=None, business=None, brand=None))
