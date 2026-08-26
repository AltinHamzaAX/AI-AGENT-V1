"""The deterministic bar a creative direction has to clear.

Every rule here exists because the failure it catches survives a schema. A
territory that renames the promise, a benefit typed into the Big Idea field, a
hook nobody could read without the caption and a scorecard that likes every
route equally all produce valid JSON and none of them are worth an Art
Director's time. Keeping the bar out of the prompt means it is enforced rather
than requested.
"""

import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from app.modules.posts.domain.semantic_contract import PostSemanticContract

from .schemas import (
    CONCEPT_SELECTION_DIMENSIONS,
    QUALITY_THRESHOLDS,
    BigIdeaCandidate,
    CreativeDirectorInput,
    CreativeDirectorLLMOutput,
    CreativeQualityGate,
    CreativeTerritory,
    QualityCheck,
    VisualHook,
)

_DOWNSTREAM_EXECUTION = re.compile(
    r"\b(?:caption|font|headline|hashtag|hex code|image prompt|logo placement|"
    r"overlay|pixel|poster layout|render the|side[- ]by[- ]side|split[- ]screen|"
    r"subheadline|tagline|typography)\b",
    re.IGNORECASE,
)
_PROHIBITED_ACTION = re.compile(
    r"\b(?:copy|clone|imitate|replicate)\b.{0,60}\bcompetitor\b|"
    r"\b(?:replace|substitute|swap)\b.{0,60}\b(?:brand|company|logo|product)\b",
    re.IGNORECASE,
)
_NUMERIC_CLAIM = re.compile(
    r"(?<![\w])(?:[$EURGBP]+\s*)?\d+(?:[.,]\d+)?(?:\s*%|/[A-Za-z0-9]+)?",
    re.IGNORECASE,
)
_TIME_LITERAL = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_UNSUPPORTED_ABSOLUTE = re.compile(
    r"\b(?:never wait|no waiting|zero wait(?:ing)?|instantly|"
    r"instant (?:access|availability|pickup|service)|without delay|guaranteed?|"
    r"always (?:available|waiting|ready)|whenever you (?:want|need)|"
    r"any time you (?:want|need)|car (?:is )?(?:always )?waiting|"
    r"immediate(?:ly)? available)\b",
    re.IGNORECASE,
)
_NEGATED_CLAIM_CONTEXT = re.compile(
    r"\b(?:avoid(?:s|ed|ing)?|does not|do not|not|without)\b[^.!?]{0,32}$",
    re.IGNORECASE,
)
_AUDIENCE_FACING_COPY = re.compile(
    r"^\s*(?:book|choose|discover|enjoy|ensure\s+your|experience|get|start|"
    r"transform|trust|count\s+on\s+us)\b",
    re.IGNORECASE,
)
#: The stock rent-a-car frame: a person and the product in the same shot, with
#: nothing transformed. It scores as a visual because it is describable, but it
#: gives the Art Director no idea and stops no scroll.
_LITERAL_HOOK = re.compile(
    r"\b(?:person|people|customer|client|traveller|traveler|passenger|man|woman|"
    r"driver|family|guest)\b[^.!?]{0,48}?\b(?:sees|seeing|looks? at|looking at|"
    r"stands?|standing|waits?|waiting|holds?|holding|receives?|receiving|"
    r"smiles?|smiling|walks? (?:up )?to|approach(?:es|ing)?)\b"
    r"[^.!?]{0,48}?\b(?:car|vehicle|keys?|product|counter|desk|van|suv)\b",
    re.IGNORECASE,
)
#: A hook that has to be read is not a hook. Words carrying the meaning is the
#: failure; saying the image works without them is the point, so the negation
#: is checked before the match counts.
_TEXT_DEPENDENT = re.compile(
    r"\b(?:text|wording|words?|slogan|lettering|writing)\b[^.!?]{0,40}?"
    r"\b(?:says?|said|reads?|explains?|tells?|announces?|states?|spells?)\b"
    r"|\b(?:says?|reads?|explains?|tells?)\b[^.!?]{0,24}?"
    r"\b(?:text|wording|words?|slogan|lettering)\b",
    re.IGNORECASE,
)
#: Grammar wreckage left behind when a token is deleted out of a sentence, e.g.
#: "A clock ticks from: to:". Sanitization has to preserve meaning, so a field
#: that arrives in this state fails rather than shipping. A verb before a colon
#: is left out: "The tension is: arrival without motion" is a sentence someone
#: wrote on purpose.
_DANGLING = "from|to|of|by|with|between|for|into|than|and|or|the|a|an"
_DAMAGED_TEXT = re.compile(
    rf"\b(?:{_DANGLING})\s*[,;:]"
    r"|\(\s*\)|[,;:]\s*[.!?]|[,;:]{2,}"
    rf"|\b(?:{_DANGLING}|is|was|becomes)\s*$",
    re.IGNORECASE,
)
#: Below this a Big Idea is a tagline wearing a concept's clothes.
_MINIMUM_IDEA_WORDS = 8
_PARAPHRASE_RATIO = 0.68
_RESTATEMENT_RATIO = 0.72
_VARIETY_RATIO = 0.82
_MINIMUM_NEW_CONCEPTS = 2
#: Compared in this order when totals tie, so a winner is never picked by
#: position in the list.
_SELECTION_PRIORITY = CONCEPT_SELECTION_DIMENSIONS
#: The strategy is the brief, not the wording. A Big Idea that lands close to
#: any of these has summarized the strategy instead of interpreting it.
STRATEGY_SOURCE_WORDING = (
    "single_minded_message",
    "usp",
    "value_proposition",
    "positioning",
    "marketing_angle",
)
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "against",
        "already",
        "also",
        "another",
        "any",
        "are",
        "around",
        "because",
        "become",
        "becomes",
        "been",
        "before",
        "being",
        "between",
        "both",
        "but",
        "can",
        "could",
        "does",
        "each",
        "even",
        "every",
        "feel",
        "feels",
        "for",
        "from",
        "has",
        "have",
        "her",
        "his",
        "how",
        "into",
        "its",
        "just",
        "like",
        "made",
        "make",
        "makes",
        "more",
        "most",
        "much",
        "must",
        "not",
        "now",
        "one",
        "only",
        "onto",
        "other",
        "our",
        "out",
        "over",
        "own",
        "rather",
        "should",
        "since",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "too",
        "turn",
        "turns",
        "under",
        "until",
        "upon",
        "very",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "will",
        "with",
        "within",
        "without",
        "would",
        "you",
        "your",
    }
)


def _ranking_key(candidate: BigIdeaCandidate) -> tuple[int, ...]:
    """Total first, then the dimensions that matter most.

    Two candidates can only reach the same key by carrying the same scorecard,
    which validation already rejects. The winner is therefore always separated
    by a judgement a reviewer can argue with, never by list position.
    """
    scores = candidate.evaluation.selection_scores()
    return (
        -candidate.evaluation.total,
        *(-scores[dimension] for dimension in _SELECTION_PRIORITY),
    )


def quality_gate(candidate: BigIdeaCandidate) -> CreativeQualityGate:
    scores = candidate.evaluation.scores()
    return CreativeQualityGate(
        candidate_id=candidate.id,
        checks=[
            QualityCheck(dimension=name, score=scores[name], threshold=threshold)
            for name, threshold in QUALITY_THRESHOLDS.items()
        ],
    )


def selection_rationale(
    selected: BigIdeaCandidate,
    runner_up: BigIdeaCandidate,
    territory: CreativeTerritory,
    hook: VisualHook,
) -> str:
    """Say why this one won, in terms the loser can be compared against."""
    selected_scores = selected.evaluation.selection_scores()
    runner_scores = runner_up.evaluation.selection_scores()
    leads = [
        f"{dimension.replace('_', ' ')} {selected_scores[dimension]} versus "
        f"{runner_scores[dimension]}"
        for dimension in _SELECTION_PRIORITY
        if selected_scores[dimension] > runner_scores[dimension]
    ]
    comparison = (
        f"It beats {runner_up.name} on {'; '.join(leads[:3])}."
        if leads
        else f"It carries the strongest overall scorecard against {runner_up.name}."
    )
    return _clip(
        f"Selected {selected.name} at {selected.evaluation.total}/80 over "
        f"{runner_up.name} at {runner_up.evaluation.total}/80. {comparison} "
        f"It is the least generic route because it enters through "
        f"{territory.angle.value.replace('_', ' ')} rather than restating the offer. "
        f"It works the primary tension: {_sentence(territory.creative_tension)} "
        f"It belongs to this brand because "
        f"{_sentence(_lower_first(selected.rationale))} "
        f"It survives without words: {_sentence(hook.wordless_read)} "
        f"Its accepted cost is {_sentence(_lower_first(selected.evaluation.weakness))} "
        f"It outlives this post: {_sentence(selected.extensions[0])}",
        2_000,
    )


def validate_exploration(
    exploration: CreativeDirectorLLMOutput,
    *,
    payload: CreativeDirectorInput,
    contract: PostSemanticContract,
    source: dict[str, Any],
    allowed_basis: set[str],
) -> None:
    errors: list[str] = []
    _validate_evidence(exploration, allowed_basis, errors)
    _validate_variety(exploration, errors)
    _validate_causal_chain(exploration, payload, errors)
    _validate_hooks(exploration, errors)
    _validate_big_ideas(exploration, payload, errors)
    _validate_advertiser_voice(exploration, contract, errors)
    _validate_scorecards(exploration, errors)
    _validate_safety(exploration, contract, source, errors)
    if errors:
        raise ValueError(" | ".join(dict.fromkeys(errors)))


def _validate_evidence(
    exploration: CreativeDirectorLLMOutput,
    allowed_basis: set[str],
    errors: list[str],
) -> None:
    items = [
        *exploration.creative_territories,
        *exploration.visual_hooks,
        *exploration.big_idea_candidates,
    ]
    for item in items:
        unsupported = [reference for reference in item.basis if reference not in allowed_basis]
        if unsupported:
            errors.append(f"{item.id} contains unsupported basis: {', '.join(unsupported)}")
        if not any(reference.startswith("marketing_strategy.") for reference in item.basis):
            errors.append(f"{item.id} must descend from marketing strategy")
    for territory in exploration.creative_territories:
        if not any(reference.startswith("audience.") for reference in territory.basis):
            errors.append(f"{territory.id} requires audience evidence")
    for hook in exploration.visual_hooks:
        if not any(
            reference.startswith(("brand.", "research.", "semantic_contract.platform"))
            for reference in hook.basis
        ):
            errors.append(f"{hook.id} requires brand, research or platform evidence")
    territory_ids = {item.territory_id for item in exploration.big_idea_candidates}
    hook_ids = {item.visual_hook_id for item in exploration.big_idea_candidates}
    if territory_ids != {item.id for item in exploration.creative_territories}:
        errors.append("every creative territory must have a big idea candidate")
    if hook_ids != {item.id for item in exploration.visual_hooks}:
        errors.append("every visual hook must be used by a big idea candidate")


def _validate_variety(exploration: CreativeDirectorLLMOutput, errors: list[str]) -> None:
    _require_semantic_variety(
        [item.name for item in exploration.creative_territories],
        "creative territory names",
        errors,
    )
    _require_semantic_variety(
        [item.premise for item in exploration.creative_territories],
        "creative territory premises",
        errors,
    )
    _require_semantic_variety(
        [item.idea for item in exploration.big_idea_candidates],
        "big ideas",
        errors,
    )
    _require_semantic_variety(
        [item.description for item in exploration.visual_hooks],
        "visual hooks",
        errors,
    )
    _require_semantic_variety(
        [item.symbol for item in exploration.visual_hooks],
        "visual hook symbols",
        errors,
    )


def _validate_causal_chain(
    exploration: CreativeDirectorLLMOutput,
    payload: CreativeDirectorInput,
    errors: list[str],
) -> None:
    """Audience tension to angle to territory to Big Idea to hook.

    Every link has to say something its parent did not. A chain of synonyms
    looks like reasoning in a review and collapses the moment anyone asks what
    the concept adds to the strategy.
    """
    strategy = payload.marketing_strategy
    territories = {item.id: item for item in exploration.creative_territories}
    hooks = {item.id: item for item in exploration.visual_hooks}
    for territory in exploration.creative_territories:
        _require_interpretation(
            territory.strategic_link,
            strategy.marketing_angle.decision,
            f"{territory.id} strategic link",
            errors,
        )
        _require_interpretation(
            territory.premise,
            strategy.customer_tension.decision,
            f"{territory.id} premise",
            errors,
        )
    for candidate in exploration.big_idea_candidates:
        territory = territories.get(candidate.territory_id)
        hook = hooks.get(candidate.visual_hook_id)
        if territory is None or hook is None:
            continue
        _require_interpretation(
            candidate.territory_link,
            territory.premise,
            f"{candidate.id} territory link",
            errors,
        )
        _require_interpretation(
            candidate.idea,
            territory.premise,
            f"{candidate.id} big idea",
            errors,
        )
        _require_interpretation(
            candidate.hook_link,
            hook.description,
            f"{candidate.id} hook link",
            errors,
        )


def _validate_hooks(exploration: CreativeDirectorLLMOutput, errors: list[str]) -> None:
    for hook in exploration.visual_hooks:
        for field in ("description", "symbol", "wordless_read", "mechanism"):
            value = getattr(hook, field)
            if _LITERAL_HOOK.search(value):
                errors.append(
                    f"{hook.id} {field.replace('_', ' ')} is a stock product shot rather "
                    "than a visual hook with a transformation or symbol"
                )
        if _depends_on_text(hook.wordless_read) is not None:
            errors.append(f"{hook.id} wordless read must work with every word removed")
        if _similar(hook.symbol, hook.description, _VARIETY_RATIO):
            errors.append(
                f"{hook.id} symbol must name the single element the image turns on, "
                "not repeat the description"
            )


def _validate_big_ideas(
    exploration: CreativeDirectorLLMOutput,
    payload: CreativeDirectorInput,
    errors: list[str],
) -> None:
    """A Big Idea is a concept many executions can hang from.

    A benefit, a promise or an instruction can all be typed into this field and
    will pass a schema. What separates them is that a Big Idea keeps working in
    the next post, so the candidate has to bring executions with it.
    """
    for territory in exploration.creative_territories:
        if _AUDIENCE_FACING_COPY.search(territory.premise):
            errors.append(
                f"{territory.id} premise is advertising copy, not a creative territory"
            )
    wording = {
        name: getattr(payload.marketing_strategy, name).decision
        for name in STRATEGY_SOURCE_WORDING
    }
    for candidate in exploration.big_idea_candidates:
        if len(candidate.idea.split()) < _MINIMUM_IDEA_WORDS:
            errors.append(f"{candidate.id} is a line, not a Big Idea")
        if _AUDIENCE_FACING_COPY.search(candidate.idea):
            errors.append(f"{candidate.id} is advertising copy, not a creative concept")
        if candidate.idea.endswith("!"):
            errors.append(f"{candidate.id} is written as a headline, not a concept")
        for name, decision in wording.items():
            if _similar(candidate.idea, decision, _PARAPHRASE_RATIO):
                errors.append(
                    f"{candidate.id} must creatively interpret, not repeat, the marketing "
                    f"{name.replace('_', ' ')}"
                )
        for index, extension in enumerate(candidate.extensions, start=1):
            if _AUDIENCE_FACING_COPY.search(extension):
                errors.append(f"{candidate.id} extension {index} is advertising copy")
            if _similar(extension, candidate.idea, _VARIETY_RATIO):
                errors.append(
                    f"{candidate.id} extension {index} repeats the Big Idea instead of "
                    "showing it carrying another execution"
                )


def _validate_scorecards(exploration: CreativeDirectorLLMOutput, errors: list[str]) -> None:
    """Scoring only earns its place when it separates the candidates."""
    vectors = [
        tuple(candidate.evaluation.selection_scores().values())
        for candidate in exploration.big_idea_candidates
    ]
    if len(set(vectors)) != len(vectors):
        errors.append(
            "identical scorecards are not an evaluation: score each route on its own merits"
        )
    _require_semantic_variety(
        [candidate.evaluation.weakness for candidate in exploration.big_idea_candidates],
        "candidate weaknesses",
        errors,
    )


def _validate_advertiser_voice(
    exploration: CreativeDirectorLLMOutput,
    contract: PostSemanticContract,
    errors: list[str],
) -> None:
    """The two fields that decide whether this is a concept or a slogan.

    A Big Idea that says the advertiser's name is a line someone wrote for the
    poster, and an image cannot pronounce a brand name at all, so a wordless
    read containing one is describing copy. Both are named everywhere else in
    the output; only these two fields have to survive without them.
    """
    names = [
        _semantic(name)
        for name in (contract.brand, contract.company)
        if isinstance(name, str) and name.strip()
    ]
    if not names:
        return
    for candidate in exploration.big_idea_candidates:
        if any(name in _semantic(candidate.idea) for name in names):
            errors.append(
                f"{candidate.id} names the advertiser, which makes it a line rather than "
                "a concept other executions can carry"
            )
    for hook in exploration.visual_hooks:
        if any(name in _semantic(hook.wordless_read) for name in names):
            errors.append(
                f"{hook.id} wordless read is written as copy: an image cannot say a "
                "brand name"
            )


def _validate_safety(
    exploration: CreativeDirectorLLMOutput,
    contract: PostSemanticContract,
    source: dict[str, Any],
    errors: list[str],
) -> None:
    generated_strings = _all_strings(exploration.model_dump(mode="json"))
    source_text = _semantic(json.dumps(source, ensure_ascii=False, sort_keys=True))
    for value in generated_strings:
        downstream = _DOWNSTREAM_EXECUTION.search(value)
        if downstream:
            errors.append(
                "creative director attempted final poster or design execution with: "
                + downstream.group()
            )
        if _PROHIBITED_ACTION.search(value):
            errors.append("creative director violated identity or competitor boundaries")
        absolute = _unsupported_absolute(value)
        if absolute and _semantic(absolute) not in source_text:
            errors.append(f"creative director invented unsupported absolute claim: {absolute}")
        numeric = _invented_numeric(value, source_text)
        if numeric is not None:
            errors.append(f"creative director invented numeric claim: {numeric}")
        damage = _DAMAGED_TEXT.search(value)
        if damage is not None:
            errors.append(
                "creative director produced text that lost its grammar around: "
                + value[max(0, damage.start() - 24) : damage.end() + 8].strip()
            )
    forbidden = [_semantic(claim) for claim in contract.forbidden_claims]
    if any(
        claim and claim in _semantic(value) for claim in forbidden for value in generated_strings
    ):
        errors.append("creative direction contains a forbidden claim")


def _require_semantic_variety(values: list[str], name: str, errors: list[str]) -> None:
    normalized = [_semantic(value) for value in values]
    if len(normalized) != len(set(normalized)):
        errors.append(f"{name} must be meaningfully distinct")
    for index, left in enumerate(normalized):
        if any(
            SequenceMatcher(None, left, right).ratio() >= _VARIETY_RATIO
            for right in normalized[index + 1 :]
        ):
            errors.append(f"{name} must explore different conceptual routes")
            return


def _require_interpretation(
    child: str,
    parent: str,
    label: str,
    errors: list[str],
) -> None:
    if _similar(child, parent, _RESTATEMENT_RATIO):
        errors.append(f"{label} restates its input instead of interpreting it")
        return
    if len(_content_words(child) - _content_words(parent)) < _MINIMUM_NEW_CONCEPTS:
        errors.append(f"{label} adds no new idea to the step it comes from")


def _content_words(value: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z]+", _semantic(value))
        if len(word) > 2 and word not in _STOPWORDS
    }


def _similar(left: str, right: str, ratio: float) -> bool:
    return SequenceMatcher(None, _semantic(left), _semantic(right)).ratio() >= ratio


def _unsupported_absolute(value: str) -> str | None:
    for match in _UNSUPPORTED_ABSOLUTE.finditer(value):
        if not _negated(value, match.start()):
            return match.group()
    return None


def _depends_on_text(value: str) -> str | None:
    """Words carrying the meaning is the fault; saying there are none is not."""
    for match in _TEXT_DEPENDENT.finditer(value):
        if not _negated(value, match.start()):
            return match.group()
    return None


def _negated(value: str, start: int) -> bool:
    return bool(_NEGATED_CLAIM_CONTEXT.search(value[max(0, start - 40) : start]))


def _invented_numeric(value: str, source_text: str) -> str | None:
    """Clock faces and countdowns are imagery; quantities are claims."""
    for numeric in _NUMERIC_CLAIM.findall(_TIME_LITERAL.sub("", value)):
        if _semantic(numeric) not in source_text:
            return numeric
    return None


def _sentence(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return stripped
    return stripped if stripped[-1] in ".!?" else f"{stripped}."


def _lower_first(value: str) -> str:
    stripped = value.strip()
    if not stripped or stripped[:2].isupper():
        return stripped
    return stripped[0].lower() + stripped[1:]


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rsplit(" ", 1)[0] + "."


_SENTENCES = re.compile(r"[^.!?]+[.!?]*")


#: Content that carries meaning, per group, and is therefore sanitized rather
#: than trusted after a local-model repair.
_STABILIZED_FIELDS = {
    "creative_territories": ("name", "premise", "creative_tension", "strategic_link", "rationale"),
    "visual_hooks": ("description", "symbol", "wordless_read", "mechanism", "rationale"),
    "big_idea_candidates": (
        "name",
        "idea",
        "territory_link",
        "hook_link",
        "production_notes",
        "rationale",
    ),
}


class UnsayableConcept(ValueError):
    """Nothing safe was left of a field the concept cannot do without.

    Raised instead of returning an empty field, so the failure names the work
    that could not be stated rather than surfacing as a blank string three
    layers later.
    """

    def __init__(self, fields: list[str]) -> None:
        self.fields = tuple(fields)
        super().__init__(
            "creative content could not be stated within the approved claims: "
            + ", ".join(self.fields)
        )


def stabilize_repair(
    value: dict[str, Any],
    source: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Remove repair drift and reframe copy-like concepts without altering identity fields."""
    stabilized = json.loads(json.dumps(value))
    source_text = _semantic(json.dumps(source, ensure_ascii=False, sort_keys=True))
    contract = source.get("semantic_contract")
    raw_forbidden = contract.get("forbidden_claims", []) if isinstance(contract, dict) else []
    forbidden = [_semantic(claim) for claim in raw_forbidden if isinstance(claim, str)]
    changed = False
    unsayable: list[str] = []
    for group_name, fields in _STABILIZED_FIELDS.items():
        group = stabilized.get(group_name)
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            for field in fields:
                current = item.get(field)
                if not isinstance(current, str):
                    continue
                safe = _sanitize_repair_text(current, source_text, forbidden)
                if safe != current:
                    item[field] = safe
                    changed = True
                if not safe:
                    unsayable.append(f"{item.get('id', group_name)}.{field}")
            for field in ("mood", "extensions"):
                values = item.get(field)
                if not isinstance(values, list):
                    continue
                safe_values = _sanitize_list(values, source_text, forbidden)
                if safe_values != values:
                    item[field] = safe_values
                    changed = True

    territories = {
        item.get("id"): item
        for item in stabilized.get("creative_territories", [])
        if isinstance(item, dict)
    }
    hooks = {
        item.get("id"): item
        for item in stabilized.get("visual_hooks", [])
        if isinstance(item, dict)
    }
    for territory in territories.values():
        premise = territory.get("premise")
        if isinstance(premise, str) and _AUDIENCE_FACING_COPY.search(premise):
            name = str(territory.get("name", "The territory"))
            tension = str(territory.get("creative_tension", "the audience tension"))
            territory["premise"] = _sanitize_repair_text(
                f"{name} explores a symbolic shift within this tension: {_sentence(tension)}",
                source_text,
                forbidden,
            )
            changed = True
            if not territory["premise"]:
                unsayable.append(f"{territory.get('id', 'territory')}.premise")

    strategy = source.get("marketing_strategy")
    candidates = stabilized.get("big_idea_candidates", [])
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("idea"), str):
                continue
            idea = candidate["idea"]
            if not _repeats_strategy_wording(idea, strategy) and not _AUDIENCE_FACING_COPY.search(
                idea
            ):
                continue
            territory = territories.get(candidate.get("territory_id"), {})
            hook = hooks.get(candidate.get("visual_hook_id"), {})
            territory_name = str(territory.get("name", "The strategic tension"))
            symbol = str(
                hook.get("symbol") or hook.get("mechanism") or "a symbolic visual transformation"
            )
            candidate["idea"] = _sanitize_repair_text(
                f"{territory_name} becomes a metaphor for the audience tension, carried by "
                f"{_lower_first(symbol).rstrip('.')}.",
                source_text,
                forbidden,
            )
            changed = True
            if not candidate["idea"]:
                unsayable.append(f"{candidate.get('id', 'idea')}.idea")
    if unsayable:
        raise UnsayableConcept(list(dict.fromkeys(unsayable)))
    return stabilized, changed


def _repeats_strategy_wording(value: str, strategy: Any) -> bool:
    if not isinstance(strategy, dict):
        return False
    for name in STRATEGY_SOURCE_WORDING:
        entry = strategy.get(name)
        decision = entry.get("decision") if isinstance(entry, dict) else None
        if isinstance(decision, str) and decision.strip():
            if _similar(value, decision, _PARAPHRASE_RATIO):
                return True
    return False


def _sanitize_repair_text(
    value: str,
    source_text: str,
    forbidden_claims: list[str],
) -> str:
    """Drop what cannot be said; keep what is left readable.

    Deleting the offending words in place is what produces output like "A clock
    ticks from: to:" - safe, and meaningless. Whole sentences go instead, so
    every sentence that survives is one the provider actually wrote.

    A field with nothing left comes back empty rather than filled with a
    placebo sentence. Nothing safe can be said here, and the Art Director is
    better served by a failed run than by a concept that says nothing.
    """
    text = _DOWNSTREAM_EXECUTION.sub("conceptual contrast", value)
    kept: list[str] = []
    for sentence in _SENTENCES.findall(text):
        stripped = sentence.strip()
        if not stripped:
            continue
        absolute = _unsupported_absolute(stripped)
        if absolute is not None and _semantic(absolute) not in source_text:
            continue
        if _invented_numeric(stripped, source_text) is not None:
            continue
        if any(claim and claim in _semantic(stripped) for claim in forbidden_claims):
            continue
        kept.append(stripped)
    result = " ".join(" ".join(kept).split())
    return "" if _DAMAGED_TEXT.search(result) else result


def _sanitize_list(
    values: list[Any],
    source_text: str,
    forbidden_claims: list[str],
) -> list[Any]:
    """Words and short phrases are dropped, never rewritten into a sentence."""
    safe: list[Any] = []
    for value in values:
        if not isinstance(value, str):
            safe.append(value)
            continue
        cleaned = _sanitize_repair_text(value, source_text, forbidden_claims)
        if cleaned:
            safe.append(cleaned)
    return safe


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _all_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _all_strings(item)]
    return []


def _semantic(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def rank_candidates(candidates: list[BigIdeaCandidate]) -> list[BigIdeaCandidate]:
    """Strongest first, by a comparison a reviewer can reopen."""
    return sorted(candidates, key=_ranking_key)


__all__ = [
    "STRATEGY_SOURCE_WORDING",
    "UnsayableConcept",
    "quality_gate",
    "rank_candidates",
    "selection_rationale",
    "stabilize_repair",
    "validate_exploration",
]
