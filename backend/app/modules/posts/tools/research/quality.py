import logging
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import urlparse

from pydantic import ValidationError

from app.modules.posts.providers import ResearchResult

from .schemas import (
    SOURCE_EXCERPT_LIMIT,
    ResearchConfidence,
    ResearchContext,
    ResearchSource,
    ResearchSourceType,
)

logger = logging.getLogger(__name__)

# A provider relevance score is similarity to the query, not a measure of how
# good a page is, so this is a junk filter rather than a quality bar. Measured
# against live Tavily results for this workflow, a floor of 0.5 discarded every
# result for three of the eight categories while the pages themselves were on
# topic (a Kosovo diaspora car-rental guide scored 0.41). Real selection is done
# afterwards by quality_score ranking, and weak sources are reported as low
# confidence rather than hidden.
MIN_RELEVANCE_SCORE = 0.3

_GOVERNMENT_SUFFIXES = (".gov", ".gov.uk", ".europa.eu")
_OFFICIAL_PLATFORM_DOMAINS = {
    "business.facebook.com",
    "business.instagram.com",
    "creators.facebook.com",
    "creators.instagram.com",
    "linkedin.com/help",
    "tiktok.com/business",
}
_SOCIAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "tiktok.com",
    "youtube.com",
    "x.com",
}
_MARKETPLACE_DOMAINS = {
    "booking.com",
    "expedia.com",
    "kayak.com",
    "rentalcars.com",
    "tripadvisor.com",
}
_INDUSTRY_MARKERS = (
    "alliedmarketresearch",
    "grandviewresearch",
    "lucintel",
    "mordorintelligence",
    "statista",
)
_NEWS_MARKERS = (
    "apnews",
    "bloomberg",
    "forbes",
    "reuters",
    "prnewswire",
)
_BLOG_MARKERS = ("/blog", "/guide", "medium.com", "substack.com")

# Characters that NFKD leaves alone, so they need an explicit mapping.
_FOLD_MAP = str.maketrans({"đ": "d", "Đ": "D", "ð": "d", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L"})

# Words that describe a place without identifying one. Both languages, because
# the source text is local even when the contract is written in English.
_GENERIC_PLACE_WORDS = frozenset(
    {
        "aeroport",
        "aeroporti",
        "airport",
        "area",
        "center",
        "centre",
        "city",
        "county",
        "district",
        "downtown",
        "east",
        "north",
        "province",
        "qendra",
        "qytet",
        "qyteti",
        "rajon",
        "region",
        "south",
        "state",
        "west",
        "zona",
    }
)

# Page furniture that survives markdown extraction. Images and bare URLs carry
# no prose evidence at all; link syntax and heading markers wrap text that does.
_IMAGE_MARKDOWN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_MARKDOWN = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")
_MARKDOWN_FURNITURE = re.compile(r"[#*_`>|]+|^\s*[-=]{3,}\s*$", re.MULTILINE)
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*")
#: A link is folded to this while navigation is detected, so that a link whose
#: text wraps across lines stops looking like several lines of page content.
_LINK_OPEN, _LINK_CLOSE = chr(0), chr(1)
_LINK_TOKEN = re.compile(f"{_LINK_OPEN}[^{_LINK_CLOSE}]*{_LINK_CLOSE}")
#: How many consecutive link-only lines make a menu. A linked heading stands
#: alone and is real content; language pickers and footer directories run to
#: dozens of entries.
NAVIGATION_RUN = 3

#: Width of the spans that relevant_window ranks. Wide enough to hold a claim
#: with the number that supports it, narrow enough that one dense paragraph
#: does not drag a page of boilerplate in with it.
RELEVANCE_WINDOW_CHARS = 400
#: Kept spans are not adjacent on the page, and the analyzer is told so. The
#: model is asked for verbatim quotes, and a span that reads as continuous
#: prose invites one that straddles the seam and matches nothing.
WINDOW_JOIN = " ... "
#: Smallest leftover worth filling with part of the next-best span. Below this
#: a fragment costs the model more attention than the evidence it carries.
WINDOW_TAIL_MIN = 120
_MONEY = re.compile(r"[€$£¥₹฿]|\b(?:eur|usd|gbp|chf|per day|per week|per month)\b", re.IGNORECASE)
_DIGIT = re.compile(r"\d")

#: Signal weights for the composite quality score.
RELEVANCE_WEIGHT = 0.35
AUTHORITY_WEIGHT = 0.30
LOCALITY_WEIGHT = 0.20
FRESHNESS_WEIGHT = 0.15


def source_from_result(
    result: ResearchResult,
    *,
    dimension: str,
    context: ResearchContext,
    researched_at: datetime,
) -> ResearchSource | None:
    if result.score is None or result.score < MIN_RELEVANCE_SCORE:
        return None
    title = " ".join(result.title.split())
    # The extracted page body is preferred when the provider returned one: a
    # search snippet is a few hundred characters and rarely enough to quote
    # evidence from. `content` remains the fallback.
    body = result.raw_content if (result.raw_content or "").strip() else result.content
    excerpt = readable_text(body)[:SOURCE_EXCERPT_LIMIT]
    url = result.url.strip()
    if not title or not excerpt or not url:
        return None
    source_type = classify_source(url)
    authority = authority_score(source_type)
    locality = locality_score(
        " ".join((title, excerpt, url)),
        context=context,
    )
    freshness = freshness_score(result.published_at, researched_at=researched_at)
    quality = composite_quality(
        relevance=result.score,
        authority=authority,
        locality=locality,
        freshness=freshness,
    )
    try:
        return ResearchSource(
            title=title,
            url=url,
            excerpt=excerpt,
            provider_score=result.score,
            retrieved_at=researched_at,
            published_at=result.published_at,
            source_type=source_type,
            authority_score=authority,
            locality_score=locality,
            freshness_score=freshness,
            quality_score=quality,
            confidence=confidence_for_quality(quality),
            dimensions=[dimension],
        )
    except ValidationError:
        # Search providers occasionally return relative or non-HTTP URLs. One
        # unusable result is a result to skip, not a reason to lose the
        # category, so this joins the weak-result filter above.
        logger.warning("posts.research.unusable_source", extra={"dimension": dimension})
        return None


def _best(*values: float | None) -> float | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


def readable_text(body: str) -> str:
    """Strip page furniture out of an extracted body.

    Asking the provider for markdown buys real page text, but it arrives with
    image tags, logo URLs and heading markers mixed in. Those are quotable
    characters that carry no evidence, and a model handed them will quote them,
    so they are removed before the text ever becomes an excerpt.

    Navigation goes with them. Keeping link text is right for a link inside a
    sentence and wrong for a menu, where the link is the whole line. One
    measured aggregator page opened with 1,392 characters of language picker,
    which was most of what the analyzer was ever shown of it.
    """
    text = _IMAGE_MARKDOWN.sub(" ", body)
    text = _without_navigation(text)
    text = _LINK_MARKDOWN.sub(r"\1", text)
    text = _BARE_URL.sub(" ", text)
    text = _MARKDOWN_FURNITURE.sub(" ", text)
    return " ".join(text.split())


def _without_navigation(markdown: str) -> str:
    """Drop runs of lines that carry nothing but links."""
    folded = _LINK_MARKDOWN.sub(
        lambda match: f"{_LINK_OPEN}{match.group(1).strip()}{_LINK_CLOSE}",
        markdown,
    )
    lines = folded.split("\n")
    link_only = [
        bool(line.strip())
        and not _LINK_TOKEN.sub("", _LIST_MARKER.sub("", line)).strip(" \t|*_#>-")
        for line in lines
    ]
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if link_only[index]:
            end = index
            while end < len(lines) and link_only[end]:
                end += 1
            if end - index >= NAVIGATION_RUN:
                index = end
                continue
        kept.append(lines[index])
        index += 1
    restored = "\n".join(kept)
    return restored.replace(_LINK_OPEN, "[").replace(_LINK_CLOSE, "]()")


def relevant_window(text: str, *, context: ResearchContext, limit: int) -> str:
    """The most evidence-dense spans of a page, in the order the page has them.

    A blind prefix is the wrong half of a long page. Measured over ten live
    aggregator results, the first 2,000 characters carried 19 price and market
    signals; the spans ranked here carried 24 in 1,200 characters, because the
    prefix was spending itself on headers while the numbers sat further down.

    Selection never invents text and never reorders it, so a quote taken from
    what the analyzer was shown is still verbatim in the source it is checked
    against, which holds the untrimmed excerpt.
    """
    if len(text) <= limit:
        return text
    windows = _windows(text, RELEVANCE_WINDOW_CHARS)
    locality = _locality_tokens(context)
    subject = _subject_tokens(context)
    ranked = sorted(
        range(len(windows)),
        key=lambda index: (
            -_window_score(windows[index], locality=locality, subject=subject),
            index,
        ),
    )
    chosen: dict[int, str] = {}
    budget = limit
    for index in ranked:
        window = windows[index]
        cost = len(window) + (len(WINDOW_JOIN) if chosen else 0)
        if cost > budget:
            continue
        chosen[index] = window
        budget -= cost
    # Whole spans rarely tile a budget exactly, and the leftover was largest on
    # the densest pages, where it is worth the most. A prefix of a verbatim
    # span is still verbatim, so the best span that did not fit gives what it
    # can rather than nothing.
    remaining = budget - (len(WINDOW_JOIN) if chosen else 0)
    if remaining >= WINDOW_TAIL_MIN:
        for index in ranked:
            if index in chosen:
                continue
            head = _head_of(windows[index], remaining)
            if head:
                chosen[index] = head
            break
    return WINDOW_JOIN.join(chosen[index] for index in sorted(chosen))


def _head_of(window: str, size: int) -> str:
    """As much of a span as fits, ending on a word boundary."""
    if len(window) <= size:
        return window
    cut = window[:size].rfind(" ")
    return window[:cut] if cut > 0 else ""


def _windows(text: str, size: int) -> list[str]:
    """Split into spans that end on word boundaries.

    Cutting mid-word would hand the model a fragment it cannot quote and would
    make the seam between two spans look like a real word.
    """
    windows: list[str] = []
    current: list[str] = []
    length = 0
    for word in text.split():
        if current and length + len(word) + 1 > size:
            windows.append(" ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + 1
    if current:
        windows.append(" ".join(current))
    return windows


def _window_score(window: str, *, locality: set[str], subject: set[str]) -> float:
    folded = _fold(window)
    score = 2.0 * sum(1 for token in locality if token in folded)
    score += sum(1 for token in subject if token in folded)
    # Prices and rates are what a market page is being read for, and they are
    # also the part a model can quote as hard evidence.
    score += 2.0 * len(_MONEY.findall(window)) + 0.1 * len(_DIGIT.findall(window))
    words = window.split()
    if words and sum(1 for word in words if word[:1].isupper()) / len(words) > 0.5:
        # A wall of proper nouns is a directory of places or brands, not a
        # sentence about the market.
        score *= 0.3
    return score


def _subject_tokens(context: ResearchContext) -> set[str]:
    words = f"{context.primary_entity} {context.product}".split()
    return {_fold(word) for word in words if len(word) > 3}


def confidence_for_quality(quality: float) -> ResearchConfidence:
    """Confidence follows the composite quality score, not a model's opinion."""
    if quality >= 0.8:
        return ResearchConfidence.HIGH
    if quality >= 0.5:
        return ResearchConfidence.MEDIUM
    return ResearchConfidence.LOW


def merge_source(existing: ResearchSource, incoming: ResearchSource) -> ResearchSource:
    dimensions = list(existing.dimensions)
    for dimension in incoming.dimensions:
        if dimension not in dimensions:
            dimensions.append(dimension)
    excerpt = existing.excerpt
    if incoming.excerpt not in excerpt:
        excerpt = f"{excerpt} {incoming.excerpt}"[:SOURCE_EXCERPT_LIMIT]
    scores = [
        score for score in (existing.provider_score, incoming.provider_score) if score is not None
    ]
    quality = max(existing.quality_score, incoming.quality_score)
    return existing.model_copy(
        update={
            "excerpt": excerpt,
            "provider_score": max(scores) if scores else None,
            "authority_score": max(existing.authority_score, incoming.authority_score),
            "locality_score": max(existing.locality_score, incoming.locality_score),
            "freshness_score": _best(existing.freshness_score, incoming.freshness_score),
            "quality_score": quality,
            "confidence": confidence_for_quality(quality),
            "dimensions": dimensions,
        }
    )


def classify_source(url: str) -> ResearchSourceType:
    parsed = urlparse(url)
    domain = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.casefold()
    full = f"{domain}{path}"
    if any(domain.endswith(suffix) for suffix in _GOVERNMENT_SUFFIXES):
        return ResearchSourceType.GOVERNMENT
    if any(full.startswith(value) for value in _OFFICIAL_PLATFORM_DOMAINS):
        return ResearchSourceType.OFFICIAL_PLATFORM
    if domain in _SOCIAL_DOMAINS or any(domain.endswith(f".{item}") for item in _SOCIAL_DOMAINS):
        return ResearchSourceType.SOCIAL_POST
    if domain in _MARKETPLACE_DOMAINS or any(
        domain.endswith(f".{item}") for item in _MARKETPLACE_DOMAINS
    ):
        return ResearchSourceType.MARKETPLACE
    if any(marker in domain for marker in _INDUSTRY_MARKERS):
        return ResearchSourceType.INDUSTRY_REPORT
    if any(marker in domain for marker in _NEWS_MARKERS):
        return ResearchSourceType.NEWS_OR_EDITORIAL
    if any(marker in full for marker in _BLOG_MARKERS):
        return ResearchSourceType.BLOG_OR_GUIDE
    if domain:
        return ResearchSourceType.BUSINESS_OR_ORGANIZATION
    return ResearchSourceType.UNKNOWN


def authority_score(source_type: ResearchSourceType) -> float:
    return {
        ResearchSourceType.GOVERNMENT: 0.95,
        ResearchSourceType.OFFICIAL_PLATFORM: 0.95,
        ResearchSourceType.INDUSTRY_REPORT: 0.82,
        ResearchSourceType.BUSINESS_OR_ORGANIZATION: 0.75,
        ResearchSourceType.NEWS_OR_EDITORIAL: 0.72,
        ResearchSourceType.SOCIAL_POST: 0.68,
        ResearchSourceType.MARKETPLACE: 0.62,
        ResearchSourceType.BLOG_OR_GUIDE: 0.45,
        ResearchSourceType.UNKNOWN: 0.35,
    }[source_type]


def locality_score(value: str, *, context: ResearchContext) -> float:
    """How strongly a source is tied to the declared market.

    Matching folds diacritics and compares stems rather than whole words. A
    genuinely local page writes "Prishtinë" or "Prishtinës", not "Prishtina",
    and plain substring matching scored those exactly as low as a page about
    another continent — penalising sources for being local.
    """
    tokens = _locality_tokens(context)
    if not tokens:
        return 0.5
    normalized = _fold(value)
    matched = sum(1 for token in tokens if token in normalized)
    if matched == len(tokens):
        return 1.0
    if matched:
        return 0.75
    return 0.2


def _locality_tokens(context: ResearchContext) -> set[str]:
    targets = " ".join(item for item in (context.market, context.location) if isinstance(item, str))
    tokens: set[str] = set()
    for raw in targets.split():
        token = _fold(raw.strip(".,:;()[]{}"))
        # Generic geography words describe a place without identifying one, so
        # requiring them made the top of the scale unreachable for real pages.
        if len(token) < 4 or token in _GENERIC_PLACE_WORDS:
            continue
        tokens.add(_stem(token))
    return tokens


def _stem(token: str) -> str:
    """Trim the inflected ending Albanian place names carry."""
    return token[: max(5, len(token) - 2)]


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.translate(_FOLD_MAP))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def composite_quality(
    *,
    relevance: float,
    authority: float,
    locality: float,
    freshness: float | None,
) -> float:
    """Blend the signals, scoring only on what is actually known.

    Most pages carry no publication date, and treating that absence as "not
    fresh" charged nearly every source for a measurement that was never taken —
    enough on its own to keep good sources out of high confidence. When
    freshness is unknown its weight is redistributed across the signals that
    were measured instead.
    """
    weighted = (
        relevance * RELEVANCE_WEIGHT + authority * AUTHORITY_WEIGHT + locality * LOCALITY_WEIGHT
    )
    if freshness is None:
        known = RELEVANCE_WEIGHT + AUTHORITY_WEIGHT + LOCALITY_WEIGHT
        return round(weighted / known, 4)
    return round(weighted + freshness * FRESHNESS_WEIGHT, 4)


def freshness_score(
    published_at: datetime | None,
    *,
    researched_at: datetime,
) -> float | None:
    """None when the provider gave no date; unknown is not the same as old."""
    if published_at is None:
        return None
    published = published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    age_days = max(0, (researched_at - published).days)
    if age_days <= 365:
        return 1.0
    if age_days <= 1_095:
        return 0.7
    if age_days <= 1_825:
        return 0.45
    return 0.2


__all__ = [
    "MIN_RELEVANCE_SCORE",
    "authority_score",
    "classify_source",
    "composite_quality",
    "confidence_for_quality",
    "freshness_score",
    "locality_score",
    "merge_source",
    "readable_text",
    "source_from_result",
]
