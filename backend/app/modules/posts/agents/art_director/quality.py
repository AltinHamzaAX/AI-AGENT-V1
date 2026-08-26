import re

from app.modules.posts.agents.asset_intelligence import IntelligentAssetRole

from .schemas import (
    ArtDirectionCheck,
    ArtDirectionLLMOutput,
    ArtDirectionQuality,
    ArtDirectorInput,
    HierarchyElement,
)

_IDENTITY_VIOLATION = re.compile(
    r"\b(?:replace|redesign|recreate|generate|invent|substitute)\b.{0,50}"
    r"\b(?:brand|logo|product|vehicle|packaging)\b",
    re.IGNORECASE,
)
_HEX_COLOR = re.compile(r"#[0-9a-f]{3,8}\b", re.IGNORECASE)
_PRODUCT_ROLES = {
    IntelligentAssetRole.PRIMARY_PRODUCT,
    IntelligentAssetRole.VEHICLE,
    IntelligentAssetRole.PACKAGING,
}


def validate_and_measure_art_direction(
    output: ArtDirectionLLMOutput,
    *,
    payload: ArtDirectorInput,
) -> ArtDirectionQuality:
    errors: list[str] = []
    hierarchy = [step.element for step in output.visual_hierarchy]
    required = {
        HierarchyElement.PRODUCT,
        HierarchyElement.HEADLINE,
        HierarchyElement.CTA,
        HierarchyElement.LOGO,
    }
    missing = required - set(hierarchy)
    if missing:
        errors.append(
            "hierarchy: missing " + ", ".join(sorted(item.value for item in missing))
        )
    if hierarchy and hierarchy[0] is not HierarchyElement.PRODUCT:
        errors.append("hierarchy: product must be the primary focal level")
    if hierarchy and hierarchy[-1] is not HierarchyElement.LOGO:
        errors.append("hierarchy: logo must close the hierarchy")
    if payload.copy_draft.offer_copy is not None and HierarchyElement.OFFER not in hierarchy:
        errors.append("copy_fit: approved offer is missing from hierarchy")
    if payload.copy_draft.offer_copy is None and HierarchyElement.OFFER in hierarchy:
        errors.append("copy_fit: hierarchy contains an offer that does not exist")
    if all(item in hierarchy for item in required):
        if not (
            hierarchy.index(HierarchyElement.PRODUCT)
            < hierarchy.index(HierarchyElement.HEADLINE)
            < hierarchy.index(HierarchyElement.CTA)
            < hierarchy.index(HierarchyElement.LOGO)
        ):
            errors.append("hierarchy: product, headline, CTA and logo order is invalid")

    if not 0.3 <= output.product_dominance <= 0.75:
        errors.append("asset_fidelity: product dominance must be between 0.3 and 0.75")
    for policy in payload.assets.assets:
        if policy.role in _PRODUCT_ROLES and not (
            policy.min_dominance <= output.product_dominance <= policy.max_dominance
        ):
            errors.append(
                "asset_fidelity: product dominance violates the approved asset policy"
            )
    generated_text = " ".join(_direction_strings(output))
    if _IDENTITY_VIOLATION.search(generated_text):
        errors.append("asset_fidelity: direction attempts to replace protected identity")
    source_text = " ".join(
        (
            payload.brand.identity_summary,
            *payload.brand.verified_facts.values(),
            *payload.brand.constraints,
        )
    )
    for color in _HEX_COLOR.findall(generated_text):
        if color.casefold() not in source_text.casefold():
            errors.append(f"brand_fit: invented color value {color}")

    if not re.search(r"\b(?:headline|copy|text|cta|offer)\b", output.negative_space, re.I):
        errors.append("copy_fit: negative space must reserve room for approved copy")
    if not re.search(r"\b(?:contrast|readab|legib|mobile|thumb)\w*\b", output.cta_treatment, re.I):
        errors.append("mobile_readability: CTA treatment must define readable contrast")
    if not re.search(r"\b(?:safe|clear|quiet|unclutter|breath)\w*\b", output.logo_region, re.I):
        errors.append("mobile_readability: logo region must remain clear and protected")

    winner = next(
        item
        for item in payload.concept.big_idea_candidates
        if item.id == payload.concept.winning_concept.candidate_id
    )
    hook = next(
        item for item in payload.concept.visual_hooks if item.id == winner.visual_hook_id
    )
    concept_tokens = _meaningful_tokens(f"{winner.idea} {hook.symbol} {hook.description}")
    direction_tokens = _meaningful_tokens(
        f"{output.focal_point} {output.composition} {output.graphic_language}"
    )
    if not concept_tokens.intersection(direction_tokens):
        errors.append("concept_alignment: direction loses the winning concept and visual hook")

    if errors:
        raise ValueError(" | ".join(dict.fromkeys(errors)))
    return ArtDirectionQuality(
        checks=[
            ArtDirectionCheck(dimension=name, passed=True, detail=detail)
            for name, detail in (
                ("hierarchy", "Product leads; logo closes the ordered visual flow."),
                ("concept_alignment", "Direction carries the winning concept and hook."),
                ("asset_fidelity", "Product dominance and identity policies are respected."),
                ("copy_fit", "Hierarchy and negative space fit the approved copy."),
                ("mobile_readability", "CTA and logo regions remain readable and protected."),
            )
        ]
    )


def _direction_strings(output: ArtDirectionLLMOutput) -> list[str]:
    return [
        output.focal_point,
        output.composition,
        *(step.reason for step in output.visual_hierarchy),
        output.negative_space,
        output.photography_direction,
        output.lighting,
        output.typography_direction,
        output.color_direction,
        output.graphic_language,
        output.cta_treatment,
        output.logo_region,
    ]


def _meaningful_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z]{4,}", value.casefold())
        if token not in {"that", "this", "with", "from", "into", "rather", "than"}
    }


__all__ = ["validate_and_measure_art_direction"]
