"""Ticket 24: Creative Director turns strategy into bounded concept exploration.

The bar is not "valid JSON". Three renamings of one promise, a benefit typed
into the Big Idea field, a hook nobody could read without the caption, or a
scorecard that likes everything equally all validate cleanly and are all worth
nothing to the Art Director. What is pinned here is the difference.
"""

import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from test_marketing_strategist import _input as _marketing_input
from test_marketing_strategist import _strategy_payload

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.creative_director import (
    CONCEPT_SELECTION_DIMENSIONS,
    CREATIVE_DIRECTOR_DEFINITION,
    QUALITY_THRESHOLDS,
    CreativeDirection,
    CreativeDirectorAgent,
    CreativeDirectorInput,
)
from app.modules.posts.agents.creative_director.quality import evidence_text
from app.modules.posts.agents.framework import AgentExecutionContext
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration.creative_direction import (
    CreativeDirectionStageHandler,
    _agent_payload,
)
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ProviderBundle,
    ProviderResponseError,
)

_TERRITORIES = (
    {
        "angle": "emotional_transformation",
        "name": "Arrival Without Pause",
        "premise": (
            "The unease of standing still after landing turns into the feeling of "
            "forward motion."
        ),
        "creative_tension": (
            "The traveller has landed but still feels suspended between the flight and "
            "the road."
        ),
        "strategic_link": (
            "Where the approved angle names convenience, this route treats stillness "
            "itself as the opponent."
        ),
        "mood": ["assured", "fluid", "welcoming"],
        "basis": ["marketing_strategy.marketing_angle", "audience.customer_tension"],
    },
    {
        "angle": "cultural_tension",
        "name": "Certainty Meets You",
        "premise": (
            "Returning home carries an expectation of welcome that a terminal rarely "
            "honours."
        ),
        "creative_tension": (
            "Diaspora arrivals expect familiarity, while transit spaces stay "
            "deliberately impersonal."
        ),
        "strategic_link": (
            "The approved angle is reopened as a question of hospitality rather than "
            "logistics."
        ),
        "mood": ["calm", "dependable", "human"],
        "basis": ["marketing_strategy.customer_tension", "audience.customer_tension"],
    },
    {
        "angle": "visual_metaphor",
        "name": "One Unbroken Route",
        "premise": (
            "A visit is pictured as a single unbroken line instead of a sequence of "
            "gaps."
        ),
        "creative_tension": (
            "Landing reads as an ending, though the visit itself has not yet begun."
        ),
        "strategic_link": (
            "The approved angle is reframed as continuity rather than as a service "
            "promise."
        ),
        "mood": ["energetic", "optimistic", "open"],
        "basis": ["marketing_strategy.positioning", "audience.customer_tension"],
    },
)

_HOOKS = (
    {
        "description": (
            "A luggage trolley leaves a continuous line behind it that widens into an "
            "open road."
        ),
        "symbol": "A rolling line that never breaks.",
        "wordless_read": "Movement continues instead of stopping at the terminal door.",
        "mechanism": "One continuous stroke carries the eye from arrival into departure.",
        "basis": ["marketing_strategy.single_minded_message", "brand.identity_summary"],
    },
    {
        "description": (
            "A doormat pattern appears on the terminal floor where the arrivals hall "
            "ends."
        ),
        "symbol": "A threshold behaving like a doorstep.",
        "wordless_read": "The city greets someone the moment they step outside.",
        "mechanism": "A domestic object placed in a transit space signals welcome.",
        "basis": ["marketing_strategy.value_proposition", "brand.identity_summary"],
    },
    {
        "description": (
            "A departure board flips its rows until they settle into a horizon "
            "stretching outward."
        ),
        "symbol": "Schedule rows resolving into landscape.",
        "wordless_read": "Timetables give way to the country beyond them.",
        "mechanism": "A familiar airport object becomes the destination it points to.",
        "basis": ["marketing_strategy.desired_reaction", "brand.identity_summary"],
    },
)

_IDEAS = (
    {
        "name": "Already Moving",
        "idea": (
            "The moment of landing is treated as motion that has quietly already "
            "started."
        ),
        "territory_link": (
            "The emotional shift of the territory is narrowed to a single instant "
            "rather than a whole arrival."
        ),
        "hook_link": (
            "The unbroken wheel line gives that instant a picture nobody has to have "
            "explained."
        ),
        "extensions": [
            "The same unbroken line can trace a hotel corridor into a mountain pass.",
            "A ferry wake can carry the line across water for a summer series.",
        ],
        "production_notes": (
            "Approved vehicle photography can be recomposed around one continuous "
            "line."
        ),
        "rationale": "It interprets the strategy as an ownable concept without becoming copy.",
        "basis": ["marketing_strategy.single_minded_message", "audience.target"],
        "evaluation": {
            "strategy_fit": 8,
            "audience_fit": 8,
            "brand_fit": 8,
            "originality": 7,
            "clarity": 8,
            "visual_potential": 8,
            "platform_fit": 8,
            "production_feasibility": 8,
            "territory_differentiation": 8,
            "claim_safety": 10,
            "concept_hook_alignment": 9,
            "weakness": (
                "The continuous-line device is common in travel work and needs unusual "
                "craft to feel new."
            ),
        },
    },
    {
        "name": "A Welcome You Can Drive",
        "idea": (
            "Hospitality is carried past the doorway of a home and into the arrivals "
            "hall."
        ),
        "territory_link": (
            "The cultural expectation of welcome is transferred onto an object nobody "
            "expects to meet at an airport."
        ),
        "hook_link": (
            "The doorstep motif turns that transfer into something recognised at a "
            "glance."
        ),
        "extensions": [
            "The motif can mark a hotel forecourt in a partner campaign.",
            "The same welcome pattern can appear at a ferry terminal in summer.",
        ],
        "production_notes": (
            "The motif can be produced as a floor graphic from approved brand assets."
        ),
        "rationale": (
            "It gives the brand a symbol that behaves like hospitality rather than "
            "service."
        ),
        "basis": ["marketing_strategy.marketing_angle", "audience.target"],
        "evaluation": {
            "strategy_fit": 9,
            "audience_fit": 9,
            "brand_fit": 9,
            "originality": 9,
            "clarity": 9,
            "visual_potential": 9,
            "platform_fit": 9,
            "production_feasibility": 9,
            "territory_differentiation": 9,
            "claim_safety": 10,
            "concept_hook_alignment": 10,
            "weakness": (
                "The doormat motif risks reading as domestic rather than premium in "
                "some placements."
            ),
        },
    },
    {
        "name": "Arrival Becomes Journey",
        "idea": (
            "The boundary between arriving somewhere and travelling through it is "
            "dissolved."
        ),
        "territory_link": (
            "The metaphor of continuity is applied to the airport's own furniture."
        ),
        "hook_link": (
            "The flipping board makes that dissolve legible in a single gesture."
        ),
        "extensions": [
            "A baggage carousel can resolve into a coastal road in a later post.",
            "Terminal signage can resolve into a mountain route for a winter series.",
        ],
        "production_notes": (
            "Departure-board imagery can be composed from approved brand assets."
        ),
        "rationale": (
            "It offers a repeatable transformation the brand can own across a series."
        ),
        "basis": ["marketing_strategy.customer_insight", "audience.target"],
        "evaluation": {
            "strategy_fit": 8,
            "audience_fit": 8,
            "brand_fit": 8,
            "originality": 8,
            "clarity": 8,
            "visual_potential": 8,
            "platform_fit": 8,
            "production_feasibility": 9,
            "territory_differentiation": 9,
            "claim_safety": 10,
            "concept_hook_alignment": 9,
            "weakness": (
                "The board transformation is harder to read at small sizes in a "
                "crowded feed."
            ),
        },
    },
)


def _territory(number: int) -> dict[str, Any]:
    values = deepcopy(_TERRITORIES[number - 1])
    values["id"] = f"territory_{number}"
    values["rationale"] = (
        "It turns the approved customer tension into a lens no other route uses."
        if number == 1
        else f"It reads the same approved tension through a {values['angle']} lens."
    )
    return values


def _hook(number: int) -> dict[str, Any]:
    values = deepcopy(_HOOKS[number - 1])
    values["id"] = f"hook_{number}"
    values["rationale"] = (
        "The mechanism makes the strategic shift understandable before any reading."
    )
    return values


def _idea(number: int, **overrides: Any) -> dict[str, Any]:
    values = deepcopy(_IDEAS[number - 1])
    values["id"] = f"idea_{number}"
    values["territory_id"] = f"territory_{number}"
    values["visual_hook_id"] = f"hook_{number}"
    values.update(overrides)
    return values


def _creative_payload() -> dict[str, Any]:
    return {
        "creative_territories": [_territory(index) for index in range(1, 4)],
        "visual_hooks": [_hook(index) for index in range(1, 4)],
        "big_idea_candidates": [_idea(index) for index in range(1, 4)],
    }


class _CreativeLLM:
    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[LLMRequest] = []
        self._responses = responses or [_creative_payload()]

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return LLMResponse(
            text=json.dumps(self._responses[index]),
            provider="test-llm",
            model="creative-director-test",
        )


async def _input() -> CreativeDirectorInput:
    upstream = await _marketing_input()
    strategy = MarketingStrategy.model_validate(
        {
            **_strategy_payload(),
            "limitations": ["External research has not yet validated these hypotheses."],
            "contract_fingerprint": upstream.audience.contract_fingerprint,
        }
    )
    return CreativeDirectorInput(
        marketing_strategy=strategy,
        audience=upstream.audience,
        brand=upstream.brand,
        research=upstream.research,
        semantic_contract=upstream.semantic_contract,
    )


def _context() -> AgentExecutionContext:
    return AgentExecutionContext(
        invocation=InvocationContext(
            correlation_id=uuid4(),
            post_id=uuid4(),
            generation_id=uuid4(),
        ),
        agent_name="creative_director",
        attempt=1,
    )


async def _run(payload: CreativeDirectorInput, llm: _CreativeLLM) -> CreativeDirection:
    return await CreativeDirectorAgent(llm).execute(payload, None, _context())


# --------------------------------------------------------------------------
# Exploration, selection and the published gate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creative_director_explores_then_selects_highest_scoring_big_idea() -> None:
    payload = await _input()
    llm = _CreativeLLM()

    result = await _run(payload, llm)

    assert len(result.creative_territories) == 3
    assert len(result.visual_hooks) == 3
    assert len(result.big_idea_candidates) == 3
    assert result.selected_big_idea_id == "idea_2"
    assert result.winning_concept.candidate_id == "idea_2"
    assert [item.candidate_id for item in result.rejected_concepts] == ["idea_3", "idea_1"]
    assert [item.rank for item in result.rejected_concepts] == [2, 3]
    assert result.creative_rationale.startswith("Selected A Welcome You Can Drive")
    assert result.contract_fingerprint == payload.marketing_strategy.contract_fingerprint
    assert result.limitations == payload.marketing_strategy.limitations
    source = json.loads(llm.requests[0].messages[-1].content)["source"]
    assert source["marketing_strategy"]["marketing_angle"]
    assert source["audience"]["customer_tension"]
    assert source["brand"]["identity_summary"]
    assert source["semantic_contract"]["product"]
    assert source["semantic_contract"]["offer"]


@pytest.mark.asyncio
async def test_selection_justifies_the_winner_against_the_runner_up() -> None:
    """A choice nobody can argue with is a choice nobody has to defend."""
    payload = await _input()

    result = await _run(payload, _CreativeLLM())

    rationale = result.creative_rationale
    assert "Arrival Becomes Journey" in rationale
    assert "72/80" in rationale and "65/80" in rationale
    assert "strategy fit 9 versus 8" in rationale
    assert "cultural tension" in rationale
    assert "doormat motif risks reading as domestic" in rationale
    assert "hotel forecourt" in rationale


@pytest.mark.asyncio
async def test_quality_gate_publishes_what_the_selected_idea_was_held_to() -> None:
    payload = await _input()

    result = await _run(payload, _CreativeLLM())

    gate = result.quality_gate
    assert gate.candidate_id == "idea_2"
    assert {check.dimension: check.threshold for check in gate.checks} == QUALITY_THRESHOLDS
    assert gate.failures == []


@pytest.mark.asyncio
async def test_a_winner_below_the_quality_bar_is_not_shipped() -> None:
    """The gate has to survive the fallback, not just the happy path."""
    payload = await _input()
    below_bar = _creative_payload()
    below_bar["big_idea_candidates"][1]["evaluation"]["originality"] = 6

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([below_bar, below_bar]))


# --------------------------------------------------------------------------
# Territories, Big Ideas, hooks and the chain between them
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_territories_must_enter_the_brief_from_different_angles() -> None:
    payload = await _input()
    repeated = _creative_payload()
    repeated["creative_territories"][1]["angle"] = repeated["creative_territories"][0]["angle"]

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([repeated, repeated]))


@pytest.mark.asyncio
async def test_a_benefit_line_is_not_accepted_as_a_big_idea() -> None:
    payload = await _input()
    benefit = _creative_payload()
    benefit["big_idea_candidates"][1]["idea"] = "Round-the-clock arrival support."

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([benefit, benefit]))


@pytest.mark.asyncio
async def test_a_big_idea_must_show_it_survives_the_next_execution() -> None:
    payload = await _input()
    trapped = _creative_payload()
    candidate = trapped["big_idea_candidates"][1]
    candidate["extensions"] = [
        candidate["idea"],
        "Hospitality is carried past the doorway of a home into the arrivals hall today.",
    ]

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([trapped, trapped]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "literal",
    [
        "A traveller stands beside the car with the keys in hand.",
        "A customer walks up to a compact vehicle at the rental desk.",
    ],
)
async def test_a_person_next_to_the_product_is_not_a_visual_hook(literal: str) -> None:
    payload = await _input()
    stock = _creative_payload()
    stock["visual_hooks"][0]["description"] = literal

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([stock, stock]))


@pytest.mark.asyncio
async def test_a_hook_that_needs_reading_is_not_a_hook() -> None:
    payload = await _input()
    captioned = _creative_payload()
    captioned["visual_hooks"][2]["wordless_read"] = (
        "The words on the board explain that the journey continues."
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([captioned, captioned]))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["idea", "wordless_read"])
async def test_advertiser_name_is_removed_from_wordless_concept_fields(field: str) -> None:
    payload = await _input()
    branded = _creative_payload()
    if field == "idea":
        branded["big_idea_candidates"][1]["idea"] = (
            "Hospitality is carried into the arrivals hall by Prishtina Drive itself."
        )
    else:
        branded["visual_hooks"][1]["wordless_read"] = (
            "Prishtina Drive greets someone the moment they step outside."
        )

    result = await _run(payload, _CreativeLLM([branded, branded]))

    if field == "idea":
        repaired = result.big_idea_candidates[1].idea
    else:
        repaired = result.visual_hooks[1].wordless_read
    assert "prishtina drive" not in repaired.casefold()
    assert any("deterministic relational repair" in item for item in result.limitations)


@pytest.mark.asyncio
async def test_saying_the_image_needs_no_words_is_not_a_dependency_on_words() -> None:
    payload = await _input()
    output = _creative_payload()
    output["visual_hooks"][2]["wordless_read"] = (
        "Without words, the picture says the timetable has become a country."
    )

    result = await _run(payload, _CreativeLLM([output]))

    assert result.visual_hooks[2].wordless_read.startswith("Without words")


@pytest.mark.asyncio
async def test_repeated_candidate_link_is_repaired_after_bounded_patches() -> None:
    payload = await _input()
    echoed = _creative_payload()
    echoed["big_idea_candidates"][0]["territory_link"] = echoed["creative_territories"][0][
        "premise"
    ]

    result = await _run(payload, _CreativeLLM([echoed, echoed]))

    assert result.big_idea_candidates[0].territory_link != result.creative_territories[0].premise
    assert any("deterministic relational repair" in item for item in result.limitations)


@pytest.mark.asyncio
async def test_a_territory_that_only_renames_the_marketing_angle_fails() -> None:
    payload = await _input()
    echoed = _creative_payload()
    echoed["creative_territories"][2]["strategic_link"] = (
        payload.marketing_strategy.marketing_angle.decision
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([echoed, echoed]))


# --------------------------------------------------------------------------
# Scoring that costs something
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_scorecards_get_explicit_distinct_tradeoffs() -> None:
    payload = await _input()
    tied = _creative_payload()
    repeated = dict(tied["big_idea_candidates"][1]["evaluation"])
    for candidate in tied["big_idea_candidates"]:
        candidate["evaluation"] = dict(repeated)

    result = await _run(payload, _CreativeLLM([tied, tied]))

    assert len(
        {
            tuple(candidate.evaluation.selection_scores().values())
            for candidate in result.big_idea_candidates
        }
    ) == 3
    assert any("deterministic relational repair" in item for item in result.limitations)


@pytest.mark.asyncio
async def test_a_flawless_scorecard_is_not_a_credible_evaluation() -> None:
    payload = await _input()
    inflated = _creative_payload()
    evaluation = inflated["big_idea_candidates"][1]["evaluation"]
    for dimension in QUALITY_THRESHOLDS:
        evaluation[dimension] = 10

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([inflated, inflated]))


@pytest.mark.asyncio
async def test_repeated_weaknesses_get_distinct_execution_tradeoffs() -> None:
    payload = await _input()
    copied = _creative_payload()
    shared = copied["big_idea_candidates"][0]["evaluation"]["weakness"]
    for candidate in copied["big_idea_candidates"]:
        candidate["evaluation"]["weakness"] = shared

    result = await _run(payload, _CreativeLLM([copied, copied]))

    assert len(
        {candidate.evaluation.weakness for candidate in result.big_idea_candidates}
    ) == 3


# --------------------------------------------------------------------------
# Correction, repair and what survives a local model
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_exploration_gets_one_complete_correction_pass() -> None:
    payload = await _input()
    invalid = _creative_payload()
    invalid["creative_territories"][1]["name"] = invalid["creative_territories"][0]["name"]
    llm = _CreativeLLM([invalid, _creative_payload()])

    result = await _run(payload, llm)

    assert result.selected_big_idea_id == "idea_2"
    assert len(llm.requests) == 2
    repair = json.loads(llm.requests[1].messages[-1].content)
    assert "creative territory names must be meaningfully distinct" in repair["validation_error"]
    assert "previous_output" in repair


@pytest.mark.asyncio
async def test_correction_pass_gives_local_model_explicit_concept_rewrite_rules() -> None:
    payload = await _input()
    invalid = _creative_payload()
    invalid["big_idea_candidates"][0]["idea"] = (
        payload.marketing_strategy.single_minded_message.decision
    )
    invalid["big_idea_candidates"][1]["idea"] = "Discover effortless mobility everywhere."
    llm = _CreativeLLM([invalid, _creative_payload()])

    await _run(payload, llm)

    request = llm.requests[1]
    repair = json.loads(request.messages[-1].content)
    assert request.temperature == 0.0
    assert "idea_2 is advertising copy" in repair["validation_error"]
    assert any(
        "third-person conceptual transformations" in requirement
        for requirement in repair["correction_requirements"]
    )
    assert "every named territory or idea ID" in " ".join(repair["correction_requirements"])


@pytest.mark.asyncio
async def test_semantic_repair_uses_bounded_patch_and_preserves_valid_content() -> None:
    payload = await _input()
    invalid = _creative_payload()
    original_name = invalid["big_idea_candidates"][1]["name"]
    invalid["big_idea_candidates"][1]["idea"] = "Trust the brand for effortless mobility."
    patch = {
        "big_idea_candidates": [
            {
                "id": "idea_2",
                "idea": "Dependable welcome becomes the first familiar signal after arrival.",
            }
        ]
    }
    llm = _CreativeLLM([invalid, patch])

    result = await _run(payload, llm)

    assert result.big_idea_candidates[1].name == original_name
    assert result.big_idea_candidates[1].idea == patch["big_idea_candidates"][0]["idea"]
    assert "strict JSON correction editor" in llm.requests[1].messages[0].content


@pytest.mark.asyncio
async def test_scorecard_and_weakness_drift_use_a_bounded_patch() -> None:
    payload = await _input()
    flat = _creative_payload()
    repeated = dict(flat["big_idea_candidates"][1]["evaluation"])
    for candidate in flat["big_idea_candidates"]:
        candidate["evaluation"] = dict(repeated)
    llm = _CreativeLLM([flat, _creative_payload()])

    await _run(payload, llm)

    system = llm.requests[1].messages[0].content
    assert "strict JSON correction editor" in system
    assert "CORRECTION PASS" not in system


def test_identical_scorecards_alone_use_a_bounded_patch() -> None:
    from app.modules.posts.agents.creative_director.agent import _needs_regeneration

    assert not _needs_regeneration(
        "identical scorecards are not an evaluation: score each route on its own merits"
    )


def test_basis_metadata_is_bounded_and_grounded_deterministically() -> None:
    from app.modules.posts.agents.creative_director.agent import _normalize_basis_references

    payload = {
        "creative_territories": [
            {"basis": ["marketing_strategy.marketing_angle", "audience.needs"] * 12}
        ],
        "visual_hooks": [
            {"basis": ["marketing_strategy.marketing_angle", "brand.identity_summary"] * 12}
        ],
        "big_idea_candidates": [
            {"basis": ["marketing_strategy.marketing_angle", "audience.needs"] * 12}
        ],
    }
    allowed = {
        "marketing_strategy.marketing_angle",
        "audience.needs",
        "brand.identity_summary",
    }

    result = _normalize_basis_references(payload, allowed)

    assert result["creative_territories"][0]["basis"] == [
        "marketing_strategy.marketing_angle",
        "audience.needs",
    ]
    assert result["visual_hooks"][0]["basis"] == [
        "marketing_strategy.marketing_angle",
        "brand.identity_summary",
    ]
    assert result["big_idea_candidates"][0]["basis"] == [
        "marketing_strategy.marketing_angle",
        "audience.needs",
    ]

    unsafe = {"creative_territories": [{"basis": ["invented.evidence"]}]}
    assert _normalize_basis_references(unsafe, allowed) == unsafe


def test_relational_stabilizer_repairs_only_named_repetition_failures() -> None:
    from app.modules.posts.agents.creative_director.agent import (
        _stabilize_relational_repair,
    )

    original = _creative_payload()
    result, changed = _stabilize_relational_repair(
        original,
        "idea_1 territory link restates its input instead of interpreting it | "
        "idea_1 hook link restates its input instead of interpreting it | "
        "hook_1 symbol must name the single element | identical scorecards | "
        "candidate weaknesses must be meaningfully distinct",
    )

    assert changed
    assert result["big_idea_candidates"][0]["territory_link"] != original[
        "big_idea_candidates"
    ][0]["territory_link"]
    assert result["big_idea_candidates"][0]["hook_link"] != original[
        "big_idea_candidates"
    ][0]["hook_link"]
    assert result["visual_hooks"][0]["symbol"] != original["visual_hooks"][0]["symbol"]
    assert len(
        {
            tuple(candidate["evaluation"][name] for name in CONCEPT_SELECTION_DIMENSIONS)
            for candidate in result["big_idea_candidates"]
        }
    ) == 3
    assert len(
        {
            candidate["evaluation"]["weakness"]
            for candidate in result["big_idea_candidates"]
        }
    ) == 3


def test_relational_stabilizer_removes_advertiser_voice_and_global_symbol_repetition() -> None:
    from app.modules.posts.agents.creative_director.agent import (
        _stabilize_relational_repair,
    )

    original = _creative_payload()
    original["big_idea_candidates"][0]["idea"] = (
        "Promotiva turns arrival into a metaphor that can continue across executions."
    )
    result, changed = _stabilize_relational_repair(
        original,
        "visual hook symbols must be meaningfully distinct | "
        "idea_1 names the advertiser, which makes it a line | "
        "idea_1 must creatively interpret, not repeat, the marketing usp",
        source={"semantic_contract": {"brand": "Promotiva", "company": "Promotiva"}},
    )

    assert changed
    assert "promotiva" not in result["big_idea_candidates"][0]["idea"].casefold()
    assert len({hook["symbol"] for hook in result["visual_hooks"]}) == 3


@pytest.mark.asyncio
async def test_patch_can_restore_missing_candidate_evaluations() -> None:
    payload = await _input()
    invalid = _creative_payload()
    evaluations = [candidate.pop("evaluation") for candidate in invalid["big_idea_candidates"]]
    patch = {
        "big_idea_candidates": [
            {"id": f"idea_{index}", "evaluation": evaluation}
            for index, evaluation in enumerate(evaluations, start=1)
        ]
    }

    result = await _run(payload, _CreativeLLM([invalid, patch]))

    assert [candidate.evaluation.total for candidate in result.big_idea_candidates] == [
        63,
        72,
        65,
    ]


@pytest.mark.asyncio
async def test_a_patch_may_repoint_a_candidate_at_the_hook_it_was_missing() -> None:
    """A dropped hook link is exactly what a patch exists to put back."""
    payload = await _input()
    invalid = _creative_payload()
    del invalid["big_idea_candidates"][2]["visual_hook_id"]
    patch = {"big_idea_candidates": [{"id": "idea_3", "visual_hook_id": "hook_3"}]}

    result = await _run(payload, _CreativeLLM([invalid, patch]))

    assert result.big_idea_candidates[2].visual_hook_id == "hook_3"
    assert result.selected_big_idea_id == "idea_2"


@pytest.mark.asyncio
async def test_a_patch_that_cannot_be_applied_fails_as_a_provider_error() -> None:
    payload = await _input()
    invalid = _creative_payload()
    invalid["big_idea_candidates"][1]["name"] = ""
    patch = {"big_idea_candidates": [{"id": "idea_2", "evaluation_notes": "not a field"}]}

    with pytest.raises(ProviderResponseError, match="could not repair its output"):
        await _run(payload, _CreativeLLM([invalid, patch]))


def test_patch_normalizes_common_ids_and_ignores_unknown_items() -> None:
    from app.modules.posts.agents.creative_director.agent import _apply_provider_patch

    previous = _creative_payload()
    patched = _apply_provider_patch(
        previous,
        {
            "creative_territories": [
                {"id": "territory-1", "premise": "A corrected conceptual premise."},
                {"id": "territory_99", "premise": "Must not be created."},
            ],
            "big_idea_candidates": [
                {"id": "idea1", "idea": "A corrected conceptual transformation."}
            ],
        },
    )

    assert patched["creative_territories"][0]["premise"] == "A corrected conceptual premise."
    assert patched["big_idea_candidates"][0]["idea"] == (
        "A corrected conceptual transformation."
    )
    assert len(patched["creative_territories"]) == 3


def test_patch_normalizes_common_group_aliases() -> None:
    from app.modules.posts.agents.creative_director.agent import _apply_provider_patch

    result = _apply_provider_patch(
        _creative_payload(),
        {"hooks": [{"id": "hook_1", "symbol": "A corrected singular symbol"}]},
    )

    assert result["visual_hooks"][0]["symbol"] == "A corrected singular symbol"


def test_patch_ignores_echoed_correction_metadata_only() -> None:
    from app.modules.posts.agents.creative_director.agent import _apply_provider_patch

    result = _apply_provider_patch(
        _creative_payload(),
        {
            "validation_error": "echoed provider context",
            "correction_requirements": ["echoed instruction"],
            "big_idea_candidates": [
                {"id": "idea_1", "hook_link": "A corrected interpretive link."}
            ],
        },
    )

    assert result["big_idea_candidates"][0]["hook_link"] == (
        "A corrected interpretive link."
    )


def test_patch_ignores_candidate_hook_link_misplaced_on_visual_hook() -> None:
    from app.modules.posts.agents.creative_director.agent import _apply_provider_patch

    result = _apply_provider_patch(
        _creative_payload(),
        {
            "visual_hooks": [
                {
                    "id": "hook_1",
                    "symbol": "A corrected symbol",
                    "hook_link": "This belongs to a candidate and is ignored here.",
                }
            ]
        },
    )

    assert result["visual_hooks"][0]["symbol"] == "A corrected symbol"
    assert "hook_link" not in result["visual_hooks"][0]


def test_three_partial_edits_per_group_are_not_mistaken_for_a_full_output() -> None:
    from app.modules.posts.agents.creative_director.agent import _apply_provider_patch

    previous = _creative_payload()
    patch = {
        "creative_territories": [
            {"id": f"territory_{index}", "premise": f"Reframed route {index}."}
            for index in range(1, 4)
        ],
        "visual_hooks": [
            {"id": f"hook_{index}", "symbol": f"Distinct symbol {index}"}
            for index in range(1, 4)
        ],
        "big_idea_candidates": [
            {"id": f"idea_{index}", "hook_link": f"Interpretive link {index}."}
            for index in range(1, 4)
        ],
    }

    result = _apply_provider_patch(previous, patch)

    assert result["creative_territories"][0]["name"] == previous[
        "creative_territories"
    ][0]["name"]
    assert result["creative_territories"][2]["premise"] == "Reframed route 3."
    assert result["visual_hooks"][1]["symbol"] == "Distinct symbol 2"
    assert result["big_idea_candidates"][0]["hook_link"] == "Interpretive link 1."


@pytest.mark.asyncio
async def test_repair_stabilizes_common_local_model_drift_before_final_validation() -> None:
    payload = await _input()
    invalid = _creative_payload()
    invalid["creative_territories"][2]["premise"] = (
        "Count on us to make the arrival feel effortless."
    )
    invalid["big_idea_candidates"][0]["idea"] = (
        payload.marketing_strategy.single_minded_message.decision
    )
    llm = _CreativeLLM([invalid, invalid])

    result = await _run(payload, llm)

    assert result.creative_territories[2].premise.startswith("One Unbroken Route explores")
    assert result.big_idea_candidates[0].idea.startswith("Arrival Without Pause becomes")
    assert any("deterministic safety" in item for item in result.limitations)


@pytest.mark.asyncio
async def test_unsayable_content_fails_closed_instead_of_shipping_a_blank_concept() -> None:
    """Nothing safe is left to say here, and a placebo sentence would hide that."""
    payload = await _input()
    invalid = _creative_payload()
    invalid["visual_hooks"][0]["description"] = (
        "A split-screen compares a 12 minute wait with instantly available transport."
    )
    invalid["creative_territories"][2]["premise"] = (
        "Guaranteed pickup arrives without delay."
    )

    with pytest.raises(ProviderResponseError, match="could not be stated"):
        await _run(payload, _CreativeLLM([invalid, invalid]))


@pytest.mark.asyncio
async def test_sanitization_drops_the_sentence_and_keeps_the_grammar() -> None:
    """Deleting the offending token in place is what produced "from: to:"."""
    payload = await _input()
    invalid = _creative_payload()
    invalid["visual_hooks"][1]["description"] = (
        "A doorstep motif meets the arrivals hall. It saves 12 minutes of hesitation."
    )
    llm = _CreativeLLM([invalid, {}])

    result = await _run(payload, llm)

    assert result.visual_hooks[1].description == "A doorstep motif meets the arrivals hall."
    assert "hesitation" not in json.dumps(result.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_text_that_lost_its_grammar_fails_closed() -> None:
    payload = await _input()
    damaged = _creative_payload()
    damaged["visual_hooks"][0]["description"] = "A clock ticks from: to:"

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([damaged, damaged]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "violation",
    [
        "Replace the product with a premium vehicle.",
        "Copy the competitor campaign concept.",
    ],
)
async def test_identity_and_competitor_boundaries_remain_hard_failures(violation: str) -> None:
    payload = await _input()
    invalid = _creative_payload()
    invalid["visual_hooks"][0]["description"] = violation
    llm = _CreativeLLM([invalid, invalid])

    with pytest.raises(ProviderResponseError):
        await _run(payload, llm)


@pytest.mark.asyncio
async def test_repair_reframes_big_idea_that_repeats_single_minded_message() -> None:
    payload = await _input()
    invalid = _creative_payload()
    invalid["big_idea_candidates"][0]["idea"] = (
        payload.marketing_strategy.single_minded_message.decision
    )

    result = await _run(payload, _CreativeLLM([invalid, invalid]))

    assert result.big_idea_candidates[0].idea != (
        payload.marketing_strategy.single_minded_message.decision
    )
    assert any("deterministic safety" in item for item in result.limitations)


@pytest.mark.asyncio
async def test_repair_reframes_audience_copy_in_a_big_idea() -> None:
    payload = await _input()
    invalid = _creative_payload()
    invalid["big_idea_candidates"][1]["idea"] = "Trust the brand for effortless mobility."

    result = await _run(payload, _CreativeLLM([invalid, invalid]))

    assert not result.big_idea_candidates[1].idea.startswith("Trust")
    assert any("deterministic safety" in item for item in result.limitations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "copy",
    [
        "Ensure your journey starts the moment you land.",
        "Count on us to make your arrival a breeze.",
    ],
)
async def test_repair_reframes_advertising_copy_in_territory_premise(copy: str) -> None:
    payload = await _input()
    invalid = _creative_payload()
    invalid["creative_territories"][0]["premise"] = copy

    result = await _run(payload, _CreativeLLM([invalid, invalid]))

    assert result.creative_territories[0].premise != copy
    assert result.creative_territories[0].premise.startswith("Arrival Without Pause explores")
    assert any("deterministic safety" in item for item in result.limitations)


@pytest.mark.asyncio
async def test_non_imperative_audience_language_can_describe_a_big_idea() -> None:
    payload = await _input()
    output = _creative_payload()
    output["big_idea_candidates"][0]["idea"] = (
        "The moment your arrival becomes freedom rather than another delay."
    )

    result = await _run(payload, _CreativeLLM([output]))

    assert "your arrival" in result.big_idea_candidates[0].idea


@pytest.mark.asyncio
async def test_unknown_basis_and_orphan_references_fail_closed() -> None:
    payload = await _input()
    invalid_basis = _creative_payload()
    invalid_basis["creative_territories"][0]["basis"] = [
        "marketing_strategy.marketing_angle",
        "invented.evidence",
    ]
    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([invalid_basis, invalid_basis]))

    invalid_reference = _creative_payload()
    invalid_reference["big_idea_candidates"][0]["territory_id"] = "territory_5"
    with pytest.raises(ProviderResponseError):
        await _run(payload, _CreativeLLM([invalid_reference, invalid_reference]))


@pytest.mark.asyncio
async def test_contract_drift_is_rejected_before_provider_call() -> None:
    payload = await _input()
    llm = _CreativeLLM()
    drifted = payload.model_copy(
        update={"brand": payload.brand.model_copy(update={"contract_fingerprint": "0" * 64})}
    )

    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        CreativeDirectorInput.model_validate(drifted.model_dump(mode="json"))
    assert llm.requests == []


@pytest.mark.asyncio
async def test_stage_payload_validates_every_ticket_input() -> None:
    payload = await _input()
    state = {
        PostWorkflowSection.MARKETING_STRATEGY.value: payload.marketing_strategy.model_dump(
            mode="json"
        ),
        PostWorkflowSection.AUDIENCE.value: payload.audience.model_dump(mode="json"),
        PostWorkflowSection.BRAND.value: payload.brand.model_dump(mode="json"),
        PostWorkflowSection.RESEARCH.value: payload.research.model_dump(mode="json"),
        PostWorkflowSection.SEMANTIC_CONTRACT.value: payload.semantic_contract,
    }

    result = _agent_payload(state)

    assert CreativeDirectorInput.model_validate(result)
    for missing in state:
        with pytest.raises((TypeError, ValueError)):
            _agent_payload({key: value for key, value in state.items() if key != missing})


@pytest.mark.asyncio
async def test_the_stage_asks_for_the_creative_model_not_the_shared_one() -> None:
    """Inventing is the work this stage does; it gets the model configured for it."""
    payload = await _input()
    extraction = _CreativeLLM()
    invention = _CreativeLLM()
    providers = ProviderBundle(
        llm=extraction,
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        creative_llm_override=invention,
    )
    state = {
        PostWorkflowSection.MARKETING_STRATEGY.value: payload.marketing_strategy.model_dump(
            mode="json"
        ),
        PostWorkflowSection.AUDIENCE.value: payload.audience.model_dump(mode="json"),
        PostWorkflowSection.BRAND.value: payload.brand.model_dump(mode="json"),
        PostWorkflowSection.RESEARCH.value: payload.research.model_dump(mode="json"),
        PostWorkflowSection.SEMANTIC_CONTRACT.value: payload.semantic_contract,
    }

    result = await CreativeDirectionStageHandler(providers).execute(
        SupervisorStageContext(
            generation_id=uuid4(),
            post_id=uuid4(),
            job_id=uuid4(),
            workflow_state=state,
            state_version=1,
            action=SupervisorAction.CONTINUE,
        )
    )

    assert len(invention.requests) == 1
    assert extraction.requests == []
    output = result.outputs[PostWorkflowSection.CREATIVE_CONCEPT]
    assert output["winning_concept"]["candidate_id"] == "idea_2"
    assert [item["candidate_id"] for item in output["rejected_concepts"]] == [
        "idea_3",
        "idea_1",
    ]


def test_supervisor_declares_the_five_creative_director_inputs() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.CREATIVE_CONCEPT)

    assert set(policy.required_sections) == {
        PostWorkflowSection.MARKETING_STRATEGY,
        PostWorkflowSection.AUDIENCE,
        PostWorkflowSection.BRAND,
        PostWorkflowSection.RESEARCH,
        PostWorkflowSection.SEMANTIC_CONTRACT,
    }
    assert policy.output_sections == (PostWorkflowSection.CREATIVE_CONCEPT,)


def test_creative_director_has_no_execution_or_approval_tools() -> None:
    assert CREATIVE_DIRECTOR_DEFINITION.allowed_tools == frozenset()
    assert CREATIVE_DIRECTOR_DEFINITION.timeout_seconds == 300


# --------------------------------------------------------------------------
# Prompts and provider drift
# --------------------------------------------------------------------------


def test_prompt_forbids_final_poster_and_requires_structured_exploration() -> None:
    from app.modules.posts.agents.creative_director.agent import _system_prompt

    prompt = _system_prompt(
        {
            "marketing_strategy.marketing_angle",
            "audience.customer_tension",
            "brand.identity_summary",
        }
    )

    assert "exactly 3 genuinely distinct creative territories" in prompt
    assert "fit within 3000 output tokens" in prompt
    assert "Big Idea" in prompt
    assert "Do not write a headline" in prompt
    assert "final poster" in prompt
    assert "evaluation belongs inside each candidate" in prompt
    assert "Identical scorecards are not an evaluation" in prompt
    assert "never repeat an angle" in prompt
    assert "understandable with every word removed" in prompt
    assert "concept_hook_alignment 9" in prompt
    assert "raise a score to reach them" in prompt


def test_correction_prompt_contains_no_copyable_creative_example() -> None:
    from app.modules.posts.agents.creative_director.agent import _correction_prompt

    prompt = _correction_prompt()

    assert "arrival threshold dissolves" not in prompt
    assert "No reusable creative example is supplied" in prompt
    assert "raising its scores is not a correction" in prompt


def test_correction_requirements_name_exact_invalid_tokens_and_message() -> None:
    from app.modules.posts.agents.creative_director.agent import _correction_requirements

    source = {
        "marketing_strategy": {
            "single_minded_message": {"decision": "The approved customer message."}
        }
    }
    requirements = _correction_requirements(
        "creative director invented numeric claim: 12 | "
        "creative director invented unsupported absolute claim: Instant Access | "
        "idea_2 must creatively interpret, not repeat, the marketing single minded message | "
        "identical scorecards are not an evaluation",
        source,
    )
    joined = " ".join(requirements)

    assert "12, Instant Access" in joined
    assert "The approved customer message." in joined
    assert "idea_2" not in joined
    assert "tradeoff" in joined


def test_correction_requirements_target_the_new_quality_failures() -> None:
    from app.modules.posts.agents.creative_director.agent import _correction_requirements

    requirements = " ".join(
        _correction_requirements(
            "territory_1 premise restates its input instead of interpreting it | "
            "hook_2 description is a stock product shot rather than a visual hook | "
            "idea_3 is a line, not a Big Idea | "
            "idea_1 is below the creative quality bar: originality 6 below 8 | "
            "creative director produced text that lost its grammar around: ticks from: to:",
            {},
        )
    )

    assert "introduce at least one idea that step does not contain" in requirements
    assert "understood with every word removed" in requirements
    assert "further executions can hang from" in requirements
    assert "raising a score without changing the work is not a fix" in requirements
    assert "complete, grammatical sentence" in requirements


def test_local_model_detached_evaluations_are_reattached_without_rewriting_content() -> None:
    from app.modules.posts.agents.creative_director.agent import _normalize_provider_output

    payload = _creative_payload()
    evaluations = [candidate.pop("evaluation") for candidate in payload["big_idea_candidates"]]
    payload["big_idea_candidates_evaluation"] = evaluations
    payload["big_idea_candidates"][0]["basis"] = ["brand.positioning"]
    payload["creative_territories"][0]["basis"] = [
        "marketing_strategy.marketing_angle",
        "semantic_contract.goal",
    ]

    normalized = _normalize_provider_output(payload)

    assert "big_idea_candidates_evaluation" not in normalized
    assert normalized["big_idea_candidates"][0]["evaluation"] == evaluations[0]
    assert "marketing_strategy.positioning" in normalized["big_idea_candidates"][0]["basis"]
    assert "audience.target" in normalized["big_idea_candidates"][0]["basis"]
    assert "audience.customer_tension" in normalized["creative_territories"][0]["basis"]


def test_local_model_field_renamings_are_accepted_without_rewriting_content() -> None:
    from app.modules.posts.agents.creative_director.agent import _normalize_provider_output

    payload = _creative_payload()
    territory = payload["creative_territories"][0]
    territory["angle_type"] = "Emotional Transformation"
    del territory["angle"]
    hook = payload["visual_hooks"][0]
    hook["central_symbol"] = hook.pop("symbol")
    candidate = payload["big_idea_candidates"][0]
    candidate["series_potential"] = "A corridor becomes a pass. A wake carries the line."
    del candidate["extensions"]

    normalized = _normalize_provider_output(payload)

    assert normalized["creative_territories"][0]["angle"] == "emotional_transformation"
    assert normalized["visual_hooks"][0]["symbol"] == "A rolling line that never breaks."
    assert normalized["big_idea_candidates"][0]["extensions"] == [
        "A corridor becomes a pass.",
        "A wake carries the line.",
    ]


def test_local_model_missing_basis_is_restored_from_safe_approved_inputs() -> None:
    from app.modules.posts.agents.creative_director.agent import _normalize_provider_output

    payload = _creative_payload()
    for group_name in ("creative_territories", "visual_hooks", "big_idea_candidates"):
        for item in payload[group_name]:
            item.pop("basis")

    normalized = _normalize_provider_output(payload)

    for territory in normalized["creative_territories"]:
        assert territory["basis"] == [
            "marketing_strategy.marketing_angle",
            "audience.customer_tension",
        ]
    for hook in normalized["visual_hooks"]:
        assert hook["basis"] == [
            "marketing_strategy.marketing_angle",
            "brand.identity_summary",
        ]
    for candidate in normalized["big_idea_candidates"]:
        assert candidate["basis"] == [
            "marketing_strategy.marketing_angle",
            "audience.target",
        ]


@pytest.mark.asyncio
async def test_visual_clock_is_not_misclassified_as_an_invented_marketing_claim() -> None:
    payload = await _input()
    output = _creative_payload()
    output["visual_hooks"][0]["description"] = (
        "A departure-board clock moves from 00:23:59 into an open onward path."
    )

    result = await _run(payload, _CreativeLLM([output]))

    assert result.visual_hooks[0].description.startswith("A departure-board clock")


@pytest.mark.asyncio
async def test_conceptual_animation_is_allowed_without_layout_instructions() -> None:
    payload = await _input()
    output = _creative_payload()
    output["visual_hooks"][0]["mechanism"] = (
        "A path animation expresses the shift from uncertainty to forward motion."
    )

    result = await _run(payload, _CreativeLLM([output]))

    assert "animation" in result.visual_hooks[0].mechanism


@pytest.mark.asyncio
async def test_explicit_rejection_of_a_guarantee_is_not_treated_as_a_claim() -> None:
    payload = await _input()
    output = _creative_payload()
    output["creative_territories"][0]["rationale"] = (
        "The concept avoids guaranteed service claims and stays with verified availability."
    )

    result = await _run(payload, _CreativeLLM([output]))

    assert "avoids guaranteed" in result.creative_territories[0].rationale


def test_an_identifier_cannot_authorise_an_invented_number() -> None:
    """A digest that happens to contain "12" is not evidence for "12 minutes"."""
    source = {
        "semantic_contract": {
            "fingerprint": "353ed42267e09552abcdef1234567890" * 2,
            "required_assets": ["11111111-1111-4111-8111-111111111112"],
            "offer": "35 EUR/day",
        }
    }
    text = evidence_text(source)

    assert "12" not in text
    assert "35 eur/day" in text
