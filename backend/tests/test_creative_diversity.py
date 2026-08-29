from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_art_director_agent import _ArtLLM
from test_art_director_agent import _input as _art_input
from test_art_director_agent import _run as _run_art
from test_design_spec import _DesignSpecLLM
from test_design_spec import _run as _run_spec
from test_generation_pipeline import _providers, _ScriptedLLM
from test_reference_validator import _LLM as _ReferenceLLM
from test_reference_validator import _payload as _reference_payload

from app.modules.posts.agents.design_spec import DesignSpecInput
from app.modules.posts.agents.reference_validator import (
    ReferenceDecision,
    ReferenceDimension,
    ReferenceOriginalityValidator,
)
from app.modules.posts.domain.memory import SemanticMemoryKind
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.services.concept_memory import ConceptMemoryService
from app.modules.posts.tools.creative import (
    CreativeDNA,
    CreativeDNADimension,
    extract_creative_dna,
    find_repetition,
)
from app.workers.pipeline import build_stage_handlers


def _dna(**changes: str) -> CreativeDNA:
    fingerprint = str(changes.pop("fingerprint", "a" * 64))
    seed = fingerprint[0]
    dimensions = {
        dimension.value: f"{seed}only {dimension.value}"
        for dimension in CreativeDNADimension
    }
    dimensions.update(changes)
    return CreativeDNA(**dimensions, fingerprint=fingerprint)


def test_repetition_requires_a_pattern_not_one_consistent_brand_choice() -> None:
    current = _dna(color_behavior="brand navy", fingerprint="a" * 64)
    previous = _dna(color_behavior="brand navy", fingerprint="b" * 64)

    assert find_repetition(current, [previous]) == []


def test_three_repeated_execution_dimensions_are_detected() -> None:
    repeated = {
        "layout": "centered product top headline bottom CTA",
        "composition": "centered product with identical scale and shadow",
        "cta_treatment": "rounded badge at bottom",
    }
    current = _dna(**repeated, fingerprint="a" * 64)
    previous = _dna(**repeated, fingerprint="b" * 64)

    matches = find_repetition(current, [previous])

    assert len(matches) == 1
    assert set(matches[0].repeated_dimensions) == {
        CreativeDNADimension.LAYOUT,
        CreativeDNADimension.COMPOSITION,
        CreativeDNADimension.CTA_TREATMENT,
    }


@pytest.mark.asyncio
async def test_extractor_captures_all_nine_dimensions_from_approved_structures() -> None:
    art_input = await _art_input()
    art = await _run_art(art_input, _ArtLLM())
    spec_input = DesignSpecInput(
        art_direction=art,
        copy_draft=art_input.copy_draft,
        semantic_contract=art_input.semantic_contract,
    )
    spec = await _run_spec(spec_input, _DesignSpecLLM())

    dna = extract_creative_dna(
        direction=art_input.concept,
        copy=art_input.copy_draft,
        art=art,
        spec=spec,
    )

    assert dna.fingerprint and len(dna.fingerprint) == 64
    assert all(getattr(dna, dimension.value) for dimension in CreativeDNADimension)
    assert art_input.copy_draft.cta in dna.cta_treatment


class _Memory:
    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata
        self.calls: list[dict] = []

    async def retrieve(self, **values):
        self.calls.append(values)
        return (SimpleNamespace(memory=SimpleNamespace(metadata=self.metadata)),)


@pytest.mark.asyncio
async def test_only_valid_approved_creative_dna_is_recalled() -> None:
    dna = _dna()
    memory = _Memory({"creative_dna": dna.model_dump(mode="json")})
    service = ConceptMemoryService(memory)  # type: ignore[arg-type]
    scope = SimpleNamespace()

    recalled = await service.recall_approved(scope=scope, query="premium coffee")  # type: ignore[arg-type]

    assert recalled == (dna,)
    assert memory.calls[0]["kinds"] == (SemanticMemoryKind.APPROVED_CREATIVE,)


@pytest.mark.asyncio
async def test_history_repetition_overrides_high_ai_originality_scores() -> None:
    validator = ReferenceOriginalityValidator(_ReferenceLLM())
    first = await validator.review(_reference_payload())
    repeated_payload = _reference_payload().model_copy(
        update={"recent_creative_patterns": [first.creative_dna]}
    )

    report = await validator.review(repeated_payload)

    assert report.decision is ReferenceDecision.REVISE
    assert report.repetition_matches
    failed = {item.dimension for item in report.checks if not item.passed}
    assert ReferenceDimension.DIFFERENTIATION in failed
    assert ReferenceDimension.ORIGINALITY in failed


def test_worker_wires_the_same_diversity_memory_loop_into_all_three_boundaries() -> None:
    handlers = build_stage_handlers(
        async_sessionmaker(),  # type: ignore[call-overload]
        _providers(_ScriptedLLM("{}")),
    )

    creative = handlers[SupervisorStage.CREATIVE_CONCEPT]
    validation = handlers[SupervisorStage.REFERENCE_VALIDATION]
    approval = handlers[SupervisorStage.QUALITY_SCORING]
    memories = {
        creative._concept_memory,  # type: ignore[attr-defined]
        validation._concept_memory,  # type: ignore[attr-defined]
        approval._concept_memory,  # type: ignore[attr-defined]
    }
    assert None not in memories
    assert len(memories) == 1
