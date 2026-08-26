"""Turn a vision readout into a verdict without asking the model to judge.

The model is only ever a witness. Every PASS / REGENERATE_SCENE decision is
made here, from fixed thresholds and from evidence the model enumerated, so the
same readout always yields the same verdict and a human can audit why.
"""

import re
from dataclasses import dataclass

from app.modules.posts.agents.asset_intelligence import IntelligentAssetRole
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.tools.generation import GenerationDecision

from .schemas import (
    ContaminationKind,
    ScenePurityCheck,
    ScenePurityFinding,
    ScenePurityInput,
    ScenePurityVerdict,
    SceneReadout,
)

#: How sure the model must be before its own judgement blocks a plate. Artifacts,
#: duplicates and distortion are the classes vision models over-report, so they
#: need a firmer call; anything that writes words, marks or interface chrome into
#: the plate blocks at the halfway mark.
CONFIDENCE_THRESHOLD: dict[ContaminationKind, float] = {
    ContaminationKind.FAKE_TEXT: 0.5,
    ContaminationKind.FAKE_LOGO: 0.5,
    ContaminationKind.WATERMARK: 0.5,
    ContaminationKind.AI_ARTIFACT: 0.6,
    ContaminationKind.DUPLICATE_OBJECT: 0.6,
    ContaminationKind.WRONG_PRODUCT: 0.5,
    ContaminationKind.UNEXPECTED_BRAND: 0.5,
    ContaminationKind.DISTORTION: 0.6,
    ContaminationKind.UNWANTED_UI: 0.5,
}

CLEAN_DETAIL: dict[ContaminationKind, str] = {
    ContaminationKind.FAKE_TEXT: "No legible text was rendered into the plate.",
    ContaminationKind.FAKE_LOGO: "No brand mark was drawn into the plate.",
    ContaminationKind.WATERMARK: "No watermark or signature overlays the plate.",
    ContaminationKind.AI_ARTIFACT: "No synthesis artifacts were reported above threshold.",
    ContaminationKind.DUPLICATE_OBJECT: "No object is repeated inside the plate.",
    ContaminationKind.WRONG_PRODUCT: "The plate depicts no product the composition must own.",
    ContaminationKind.UNEXPECTED_BRAND: "Only the post's own identity appears in the plate.",
    ContaminationKind.DISTORTION: "No warped or melted geometry was reported above threshold.",
    ContaminationKind.UNWANTED_UI: "No interface chrome, cursor or device frame is present.",
}

_PRODUCT_ROLES = frozenset(
    {
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
)
_TOKEN = re.compile(r"[a-z0-9]{3,}")
#: A single stray glyph is noise in a photograph; two or more is a word.
_MIN_TEXT_LENGTH = 2


@dataclass(frozen=True, slots=True)
class ScenePurityAssessment:
    verdict: ScenePurityVerdict
    findings: tuple[ScenePurityFinding, ...]
    checks: tuple[ScenePurityCheck, ...]


def decide_scene_purity(
    readout: SceneReadout, *, payload: ScenePurityInput
) -> ScenePurityAssessment:
    contract = payload.contract()
    reasons: dict[ContaminationKind, list[str]] = {kind: [] for kind in ContaminationKind}
    confidence: dict[ContaminationKind, float] = dict.fromkeys(ContaminationKind, 0.0)

    def flag(kind: ContaminationKind, detail: str, weight: float) -> None:
        reasons[kind].append(detail)
        confidence[kind] = max(confidence[kind], weight)

    for kind in ContaminationKind:
        reported = readout.confidence_for(kind)
        if reported >= CONFIDENCE_THRESHOLD[kind]:
            flag(
                kind,
                f"the vision model reported it at confidence {reported:.2f} "
                f"({readout.evidence_for(kind)})",
                reported,
            )

    _apply_text_evidence(readout, flag)
    _apply_brand_evidence(readout, contract, flag)
    _apply_product_evidence(readout, payload, contract, flag)

    findings = tuple(
        ScenePurityFinding(
            kind=kind,
            confidence=confidence[kind],
            detail=_detail(kind, reasons[kind]),
        )
        for kind in ContaminationKind
        if reasons[kind]
    )
    checks = tuple(
        ScenePurityCheck(
            kind=kind,
            passed=not reasons[kind],
            detail=_detail(kind, reasons[kind]) if reasons[kind] else CLEAN_DETAIL[kind],
        )
        for kind in ContaminationKind
    )
    verdict = (
        ScenePurityVerdict.REGENERATE_SCENE if findings else ScenePurityVerdict.PASS
    )
    return ScenePurityAssessment(verdict=verdict, findings=findings, checks=checks)


def _apply_text_evidence(readout: SceneReadout, flag) -> None:
    words = [item for item in readout.visible_text if len(item) >= _MIN_TEXT_LENGTH]
    if words:
        flag(
            ContaminationKind.FAKE_TEXT,
            f"the plate renders legible text {_quote(words)}; all copy belongs to composition",
            1.0,
        )


def _apply_brand_evidence(readout: SceneReadout, contract: PostSemanticContract, flag) -> None:
    if not readout.visible_brands:
        return
    # Any generated mark is a fake logo, including the post's own: the real one
    # is composited from the approved asset, never synthesised.
    flag(
        ContaminationKind.FAKE_LOGO,
        f"the plate draws brand marks {_quote(readout.visible_brands)}",
        1.0,
    )
    own = _identity_terms(contract.company, contract.brand)
    foreign = [name for name in readout.visible_brands if not _matches(name, own)]
    if foreign:
        flag(
            ContaminationKind.UNEXPECTED_BRAND,
            f"the plate shows third-party identity {_quote(foreign)}",
            1.0,
        )


def _apply_product_evidence(
    readout: SceneReadout,
    payload: ScenePurityInput,
    contract: PostSemanticContract,
    flag,
) -> None:
    if not readout.depicted_products:
        return
    plan = payload.generation_plan
    protected = any(directive.role in _PRODUCT_ROLES for directive in plan.preserve)
    if protected or plan.decision is GenerationDecision.GENERATE_BACKGROUND:
        flag(
            ContaminationKind.WRONG_PRODUCT,
            f"the plate depicts {_quote(readout.depicted_products)} while the approved "
            "original owns the product region",
            1.0,
        )
        return
    approved = _identity_terms(contract.product, contract.primary_entity)
    unrelated = [name for name in readout.depicted_products if not _matches(name, approved)]
    if unrelated:
        flag(
            ContaminationKind.WRONG_PRODUCT,
            f"the plate depicts {_quote(unrelated)}, which is not the approved subject",
            1.0,
        )


def _identity_terms(*values: str | None) -> set[str]:
    terms: set[str] = set()
    for value in values:
        if not value:
            continue
        terms.add(value.casefold().strip())
        terms.update(_TOKEN.findall(value.casefold()))
    return terms


def _matches(candidate: str, terms: set[str]) -> bool:
    if not terms:
        return False
    normalized = candidate.casefold().strip()
    if normalized in terms:
        return True
    return bool(set(_TOKEN.findall(normalized)).intersection(terms))


def _quote(values: list[str]) -> str:
    shown = [f"'{value}'" for value in values[:5]]
    if len(values) > 5:
        shown.append(f"and {len(values) - 5} more")
    return ", ".join(shown)


def _detail(kind: ContaminationKind, reasons: list[str]) -> str:
    joined = "; ".join(dict.fromkeys(reasons))
    return f"{kind.value}: {joined}"[:600]


__all__ = [
    "CLEAN_DETAIL",
    "CONFIDENCE_THRESHOLD",
    "ScenePurityAssessment",
    "decide_scene_purity",
]
