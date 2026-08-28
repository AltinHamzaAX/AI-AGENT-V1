import hashlib
import json
import logging
from typing import Any

from pydantic import ValidationError

from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from app.modules.posts.tools.creative import CreativeDNA, extract_creative_dna, find_repetition

from .schemas import (
    REFERENCE_QUALITY_THRESHOLD,
    GenericPatternSignal,
    ReferenceDecision,
    ReferenceDimension,
    ReferenceDimensionCheck,
    ReferenceIssue,
    ReferenceSeverity,
    ReferenceUse,
    ReferenceValidationReport,
    ReferenceValidatorInput,
    ReferenceValidatorReadout,
)

logger = logging.getLogger(__name__)


class ReferenceOriginalityValidator:
    """Independent pre-production guard against copying and generic creative."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def review(
        self, payload: ReferenceValidatorInput, *, revision_requests: int = 0
    ) -> ReferenceValidationReport:
        response = await self._complete(payload)
        try:
            readout = _validated_readout(response.text, payload)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as first_exc:
            logger.warning("posts.reference_validator.validation_failed: %s", first_exc)
            repair = await self._complete(
                payload,
                previous_output=response.text,
                validation_error=str(first_exc),
            )
            try:
                readout = _validated_readout(repair.text, payload)
                response = repair
            except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
                logger.warning("posts.reference_validator.repair_failed: %s", exc)
                readout = _deterministic_readout()

        creative_dna = _creative_dna(payload)
        repetition_matches = find_repetition(
            creative_dna, payload.recent_creative_patterns
        )
        generic = detect_generic_patterns(payload)
        generic.extend(_repetition_signals(repetition_matches))
        checks = _apply_hard_gates(readout.checks, readout.references, generic)
        issues = _issues(checks, readout.references, generic)
        decision = (
            ReferenceDecision.REVISE
            if issues
            else ReferenceDecision.PASS
        )
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
        return ReferenceValidationReport(
            decision=decision,
            checks=checks,
            references=readout.references,
            issues=issues,
            generic_patterns=generic,
            creative_dna=creative_dna,
            repetition_matches=repetition_matches,
            summary=_summary(readout.summary, readout.references, generic),
            provider=response.provider,
            model=response.model,
            contract_fingerprint=contract.fingerprint,
            revision_requests=revision_requests,
        )

    async def _complete(
        self,
        payload: ReferenceValidatorInput,
        *,
        previous_output: str | None = None,
        validation_error: str | None = None,
    ) -> LLMResponse:
        system = _system_prompt()
        user = _context(payload)
        if previous_output is not None:
            system += " CORRECTION PASS: return the complete corrected JSON object only."
            user["previous_output"] = previous_output[:12_000]
            user["validation_error"] = (validation_error or "invalid review")[:3_000]
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


def detect_generic_patterns(payload: ReferenceValidatorInput) -> list[GenericPatternSignal]:
    """Fail closed on high-signal commodity combinations, independent of model scores."""
    document = json.dumps(
        {
            "concept": _selected_creative(payload),
            "copy": payload.copy_draft.model_dump(mode="json"),
            "art": payload.art_direction.model_dump(mode="json"),
            "spec": payload.design_spec.model_dump(mode="json"),
        },
        ensure_ascii=False,
    ).casefold()
    families = {
        "commodity_product_gradient_badge_cta": {
            "subject": ("coffee cup", "cup of coffee", "product hero", "centered product"),
            "gradient": ("gradient", "brown backdrop", "brown background"),
            "badge": ("rounded badge", "pill badge", "badge", "pill"),
            "generic_cta": ("order now", "shop now", "learn more", "get started", "book now"),
        },
    }
    signals: list[GenericPatternSignal] = []
    for pattern, groups in families.items():
        matched = [name for name, markers in groups.items() if any(x in document for x in markers)]
        if len(matched) == len(groups):
            signals.append(
                GenericPatternSignal(
                    pattern=pattern,
                    matched_elements=matched,
                    evidence=(
                        "Creative combines a commodity subject, gradient, badge and generic CTA."
                    ),
                )
            )
    return signals


def _creative_dna(payload: ReferenceValidatorInput) -> CreativeDNA:
    try:
        return extract_creative_dna(
            direction=payload.creative_direction,
            copy=payload.copy_draft,
            art=payload.art_direction,
            spec=payload.design_spec,
        )
    except (AttributeError, StopIteration):
        # Deliberately minimal model_construct fixtures do not contain the full
        # selected concept graph. Validated production inputs always use the path above.
        source = json.dumps(
            {
                "concept": payload.creative_direction.model_dump(mode="json"),
                "copy": payload.copy_draft.model_dump(mode="json"),
                "art": payload.art_direction.model_dump(mode="json"),
                "spec": payload.design_spec.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        fallback = source[:4_000] or "fixture"
        return CreativeDNA(
            **{
                name: fallback
                for name in CreativeDNA.model_fields
                if name not in {"schema_version", "fingerprint"}
            },
            fingerprint=hashlib.sha256(source.encode()).hexdigest(),
        )


def _repetition_signals(matches) -> list[GenericPatternSignal]:
    return [
        GenericPatternSignal(
            pattern=f"recent_creative_repetition:{match.historical_fingerprint[:16]}",
            matched_elements=[item.value for item in match.repeated_dimensions],
            evidence=(
                "Planned creative repeats "
                f"{len(match.repeated_dimensions)} DNA dimensions from an approved post."
            ),
        )
        for match in matches
    ]


def _validated_readout(raw: str, payload: ReferenceValidatorInput) -> ReferenceValidatorReadout:
    value = raw.strip()
    if value.startswith("```") and value.endswith("```"):
        value = "\n".join(value.splitlines()[1:-1]).strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("provider output must be a JSON object")
    readout = ReferenceValidatorReadout.model_validate(parsed)
    known_urls = {
        str(ref.url)
        for report in (
            payload.research.competitor,
            payload.research.social,
            payload.research.visual_reference,
            payload.research.trend,
        )
        for ref in report.visual_references
    }
    supplied = {item.reference_url for item in readout.references}
    if supplied - known_urls:
        raise ValueError("reference assessment cites an unknown research URL")
    checks = {item.dimension: item for item in readout.checks}
    return readout.model_copy(
        update={"checks": [checks[dimension] for dimension in ReferenceDimension]}
    )


def _deterministic_readout() -> ReferenceValidatorReadout:
    """Keep deterministic copy/generic gates operational when the model emits invalid JSON."""
    return ReferenceValidatorReadout(
        checks=[
            ReferenceDimensionCheck(
                dimension=dimension,
                score=REFERENCE_QUALITY_THRESHOLD,
                evidence=(
                    "Automated comparison was unavailable; deterministic originality and "
                    "repetition guards were applied."
                ),
            )
            for dimension in ReferenceDimension
        ],
        references=[],
        summary=(
            "Model review was structurally invalid. Deterministic generic-pattern and recent "
            "creative repetition checks completed."
        ),
    )


def _apply_hard_gates(checks, references, generic):
    result = {item.dimension: item for item in checks}
    copied = any(item.classification is ReferenceUse.COPY for item in references)
    forced: set[ReferenceDimension] = set()
    if copied:
        forced.update(
            {
                ReferenceDimension.CONCEPT_SIMILARITY,
                ReferenceDimension.LAYOUT_SIMILARITY,
                ReferenceDimension.VISUAL_PATTERN_SIMILARITY,
                ReferenceDimension.ORIGINALITY,
            }
        )
    if generic:
        forced.update({ReferenceDimension.DIFFERENTIATION, ReferenceDimension.ORIGINALITY})
    for dimension in forced:
        current = result[dimension]
        if current.score >= REFERENCE_QUALITY_THRESHOLD:
            result[dimension] = ReferenceDimensionCheck(
                dimension=dimension,
                score=REFERENCE_QUALITY_THRESHOLD - 1,
                evidence=(
                    "Deterministic guard found copied specifics."
                    if copied
                    else "Deterministic guard found a generic pattern combination."
                ),
            )
    return [result[dimension] for dimension in ReferenceDimension]


def _issues(checks, references, generic) -> list[ReferenceIssue]:
    issues: list[ReferenceIssue] = []
    for check in checks:
        if check.passed:
            continue
        issues.append(
            ReferenceIssue(
                issue=f"{check.dimension.value} failed the independent quality bar",
                region=_region(check.dimension),
                severity=ReferenceSeverity.HIGH,
                confidence=0.9,
                expected=(
                    f"Independent {check.dimension.value} score "
                    f">= {REFERENCE_QUALITY_THRESHOLD}/10."
                ),
                observed=f"Score {check.score}/10: {check.evidence}",
                recommended_action=_action(check.dimension),
                dimensions=[check.dimension],
            )
        )
    if any(item.classification is ReferenceUse.COPY for item in references) and not issues:
        raise ValueError("COPY evidence must produce a failed hard gate")
    if generic and not any(
        ReferenceDimension.ORIGINALITY in issue.dimensions for issue in issues
    ):
        raise ValueError("generic evidence must produce an originality issue")
    return issues


def _summary(summary: str, references, generic) -> str:
    hard_gates: list[str] = []
    if any(item.classification is ReferenceUse.COPY for item in references):
        hard_gates.append("COPY evidence")
    if generic:
        hard_gates.append("generic-pattern evidence")
    if not hard_gates:
        return summary
    suffix = " Deterministic hard gate: " + " and ".join(hard_gates) + "."
    return (summary + suffix)[:1_500]


def _region(dimension: ReferenceDimension) -> str:
    if dimension is ReferenceDimension.LAYOUT_SIMILARITY:
        return "layout system"
    if dimension is ReferenceDimension.BRAND_FIT:
        return "brand expression"
    if dimension is ReferenceDimension.MARKET_FIT:
        return "market positioning"
    return "creative concept"


def _action(dimension: ReferenceDimension) -> str:
    if dimension is ReferenceDimension.LAYOUT_SIMILARITY:
        return "Change the composition grammar while preserving approved copy and strategy."
    if dimension is ReferenceDimension.MARKET_FIT:
        return "Rework the strategic angle using grounded market evidence."
    return "Replace borrowed or generic specifics with an ownable brand-linked visual mechanism."


def _context(payload: ReferenceValidatorInput) -> dict[str, Any]:
    reports = [
        payload.research.competitor,
        payload.research.social,
        payload.research.visual_reference,
        payload.research.trend,
    ]
    return {
        "brand": payload.brand.model_dump(mode="json"),
        "strategy": payload.marketing_strategy.model_dump(mode="json"),
        "selected_concept": _selected_creative(payload),
        "copy": payload.copy_draft.model_dump(mode="json"),
        "art_direction": payload.art_direction.model_dump(mode="json"),
        "design_spec": payload.design_spec.model_dump(mode="json"),
        "research": [_research_context(report) for report in reports],
    }


def _selected_creative(payload: ReferenceValidatorInput) -> dict[str, Any]:
    creative = payload.creative_direction
    try:
        selected_id = creative.winning_concept.candidate_id
        idea = next(item for item in creative.big_idea_candidates if item.id == selected_id)
        territory = next(
            item for item in creative.creative_territories if item.id == idea.territory_id
        )
        hook = next(item for item in creative.visual_hooks if item.id == idea.visual_hook_id)
    except (AttributeError, StopIteration):
        # Supports deliberately minimal constructed fixtures; validated production
        # inputs always take the selected-only path above.
        return creative.model_dump(mode="json")
    return {
        "winning_concept": creative.winning_concept.model_dump(mode="json"),
        "big_idea": idea.model_dump(mode="json"),
        "territory": territory.model_dump(mode="json"),
        "visual_hook": hook.model_dump(mode="json"),
        "creative_rationale": creative.creative_rationale,
        "limitations": creative.limitations,
    }


def _research_context(report) -> dict[str, Any]:
    category = getattr(report, "category", None)
    status = getattr(report, "status", None)
    confidence = getattr(report, "confidence", None)
    analysis = getattr(report, "analysis", None)
    return {
        "category": getattr(category, "value", category),
        "status": getattr(status, "value", status),
        "confidence": getattr(confidence, "value", confidence),
        "analysis": analysis.model_dump(mode="json") if analysis is not None else None,
        "visual_references": [
            item.model_dump(mode="json") for item in report.visual_references
        ],
        "limitations": getattr(report, "degraded_dimensions", []),
    }


def _system_prompt() -> str:
    schema = json.dumps(ReferenceValidatorReadout.model_json_schema(), sort_keys=True)
    dimensions = ", ".join(item.value for item in ReferenceDimension)
    return (
        "You are an independent reference-fit and originality validator. Compare the approved "
        "creative concept, copy, art direction and layout plan with supplied research evidence. "
        "Distinguish learning a general principle (LEARN_FROM) from reproducing specific concept, "
        "layout or visual-pattern choices (COPY). Detect generic category formulas. Score each "
        "dimension 1-10 where 10 is excellent/safe: for similarity dimensions, 10 means clearly "
        "differentiated and 1 means near-copy. Never trust upstream self-scores. Cite only "
        "supplied "
        f"evidence. Return every dimension exactly once in this order: {dimensions}. Return JSON "
        f"only matching this schema: {schema}"
    )


__all__ = ["ReferenceOriginalityValidator", "detect_generic_patterns"]
