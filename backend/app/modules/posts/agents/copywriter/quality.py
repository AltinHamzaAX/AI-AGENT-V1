import json
import re
import unicodedata

from app.modules.posts.domain.semantic_contract import PostSemanticContract

from .schemas import (
    CopyQuality,
    CopyQualityCheck,
    CopywriterInput,
    CopywriterLLMOutput,
)

_NUMERIC_CLAIM = re.compile(r"(?<![\w])(?:[$€£]\s*)?\d+(?:[.,]\d+)?(?:\s*%|/[\w]+)?")
_UNSUPPORTED_CLAIM = re.compile(
    r"\b(?:best|cheapest|fastest|guaranteed?|risk[- ]free|free|number one|#1|"
    r"always|never|instant(?:ly)?|zero wait(?:ing)?)\b",
    re.IGNORECASE,
)
_DAMAGED_GRAMMAR = re.compile(r"\s{2,}|[!?.,]{3,}|\b(?:and|or|the|a|an|to|for)\s*$", re.I)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")
_QUALITY_DIMENSIONS = (
    "clarity",
    "tone",
    "length",
    "grammar",
    "claim_validity",
    "text_density",
    "mobile_readability",
)


def validate_and_measure_copy(
    output: CopywriterLLMOutput,
    *,
    payload: CopywriterInput,
    contract: PostSemanticContract,
) -> CopyQuality:
    errors: list[str] = []
    values = _copy_strings(output)
    combined = " ".join(values)
    source = _semantic(
        json.dumps(
            {
                "strategy": payload.strategy.model_dump(mode="json"),
                "concept": payload.concept.model_dump(mode="json"),
                "brand": payload.brand_voice.model_dump(mode="json"),
                "semantic_contract": contract.to_dict(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    if any(len(sentence.split()) > 30 for value in values for sentence in _sentences(value)):
        errors.append("clarity: sentences must not exceed 30 words")
    if len(output.headline.split()) > 12 or len(output.cta.split()) > 6:
        errors.append("mobile_readability: headline or CTA is too long")
    if combined.count("!") > 1 or any(
        _uppercase_ratio(value) > 0.6
        for value in values
        if sum(character.isalpha() for character in value) >= 6
    ):
        errors.append("tone: copy is shouty or uses excessive capitalization")
    if any(_DAMAGED_GRAMMAR.search(value) for value in values):
        errors.append("grammar: copy contains damaged spacing or punctuation")
    if not output.supporting_copy.endswith((".", "!", "?")):
        errors.append("grammar: supporting copy must be a complete sentence")
    if not output.caption.endswith((".", "!", "?")):
        errors.append("grammar: caption must be a complete sentence")

    overlay_characters = sum(
        len(value or "")
        for value in (
            output.headline,
            output.subheadline,
            output.supporting_copy,
            output.offer_copy,
            output.cta,
        )
    )
    if overlay_characters > 420:
        errors.append("text_density: overlay copy exceeds 420 characters")
    if len(output.caption) > _caption_limit(contract.platform):
        errors.append("length: caption exceeds the platform mobile-reading limit")

    if contract.offer is None and output.offer_copy is not None:
        errors.append("claim_validity: offer copy was invented without an approved offer")
    if contract.offer is not None and (
        output.offer_copy is None
        or _semantic(contract.offer) not in _semantic(output.offer_copy)
    ):
        errors.append("claim_validity: offer copy must preserve the approved offer exactly")
    for forbidden in contract.forbidden_claims:
        if _semantic(forbidden) in _semantic(combined):
            errors.append(f"claim_validity: forbidden claim detected: {forbidden}")
    for match in _NUMERIC_CLAIM.findall(combined):
        if _semantic(match) not in source:
            errors.append(f"claim_validity: unsupported numeric claim: {match}")
    for match in _UNSUPPORTED_CLAIM.findall(combined):
        if _semantic(match) not in source:
            errors.append(f"claim_validity: unsupported absolute claim: {match}")

    if errors:
        raise ValueError(" | ".join(dict.fromkeys(errors)))
    return CopyQuality(
        checks=[
            CopyQualityCheck(
                dimension=dimension,
                passed=True,
                detail=_quality_detail(dimension, output, overlay_characters),
            )
            for dimension in _QUALITY_DIMENSIONS
        ]
    )


def _copy_strings(output: CopywriterLLMOutput) -> list[str]:
    return [
        output.headline,
        output.subheadline,
        output.supporting_copy,
        *(value for value in (output.offer_copy,) if value is not None),
        output.cta,
        output.caption,
        *output.hashtags,
    ]


def _sentences(value: str) -> list[str]:
    return [match.group().strip() for match in _SENTENCE.finditer(value) if match.group().strip()]


def _uppercase_ratio(value: str) -> float:
    letters = [character for character in value if character.isalpha()]
    return sum(character.isupper() for character in letters) / len(letters) if letters else 0.0


def _caption_limit(platform: str) -> int:
    platform_name = _semantic(platform)
    return 500 if any(name in platform_name for name in ("instagram", "facebook")) else 300


def _quality_detail(
    dimension: str,
    output: CopywriterLLMOutput,
    overlay_characters: int,
) -> str:
    details = {
        "clarity": "Every sentence is 30 words or fewer.",
        "tone": "Capitalization and emphasis remain controlled.",
        "length": f"Caption uses {len(output.caption)} characters.",
        "grammar": "Copy uses complete, undamaged sentences.",
        "claim_validity": "Claims and offer are grounded in approved inputs.",
        "text_density": f"Overlay copy uses {overlay_characters}/420 characters.",
        "mobile_readability": (
            f"Headline uses {len(output.headline.split())}/12 words; "
            f"CTA uses {len(output.cta.split())}/6 words."
        ),
    }
    return details[dimension]


def _semantic(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


__all__ = ["validate_and_measure_copy"]
