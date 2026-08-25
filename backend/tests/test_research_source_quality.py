"""How useful a search result is judged to be.

Confidence is derived from a composite of relevance, authority, locality and
freshness. Two defects kept it pinned below high: local pages written in the
market's own language scored as foreign, and a missing publication date was
charged as though the page were stale. These tests pin both.
"""

from datetime import UTC, datetime

import pytest

from app.modules.posts.providers import ResearchResult
from app.modules.posts.tools.research import ResearchConfidence, ResearchContext
from app.modules.posts.tools.research.quality import (
    AUTHORITY_WEIGHT,
    FRESHNESS_WEIGHT,
    LOCALITY_WEIGHT,
    RELEVANCE_WEIGHT,
    composite_quality,
    confidence_for_quality,
    freshness_score,
    locality_score,
    merge_source,
    readable_text,
    relevant_window,
    source_from_result,
)

NOW = datetime(2026, 8, 25, tzinfo=UTC)


def _context(**overrides) -> ResearchContext:
    values = {
        "company": "Promotiva Mobility",
        "brand": "Prishtina Drive",
        "product": "Airport car rental",
        "primary_entity": "Airport car rental",
        "audience": "Diaspora arriving in Kosovo",
        "target_segment": "Arrival convenience seekers",
        "market": "Kosovo",
        "location": "Prishtina airport",
        "platform": "Instagram",
        "language": "Albanian",
        "required_facts": {},
        "contract_fingerprint": "a" * 64,
    }
    values.update(overrides)
    return ResearchContext(**values)


def _source(content: str, *, url: str = "https://rentacar-ks.example/x", score: float = 0.9):
    return source_from_result(
        ResearchResult(title="Qira veturash", url=url, content=content, score=score),
        dimension="offers",
        context=_context(),
        researched_at=NOW,
    )


# --------------------------------------------------------------------------
# Locality
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Airport car rental in Prishtina, Kosovo.",
        "Qira veturash ne Prishtine me marrje ne aeroport, Kosove.",
        "Qira veturash në Prishtinë, aeroporti i Kosovës.",
        "Rezervo makinen tende ne Prishtinen e Kosoves.",
    ],
)
def test_a_local_page_scores_as_local_in_either_language(text: str) -> None:
    assert locality_score(text, context=_context()) == 1.0


def test_a_foreign_page_still_scores_low() -> None:
    assert locality_score("Car rental in Tokyo, Japan.", context=_context()) == 0.2


def test_generic_place_words_are_not_required() -> None:
    """ "airport" describes a place without identifying one."""
    # Mentions the market and the city but not the word "airport".
    assert locality_score("Qira veturash Prishtinë, Kosovë.", context=_context()) == 1.0


def test_a_partial_match_sits_between() -> None:
    context = _context(market="Kosovo", location="Prishtina airport")
    assert locality_score("Rental market across Kosovo.", context=context) == 0.75


def test_locality_is_neutral_without_a_declared_place() -> None:
    context = _context(market=None, location=None)
    assert locality_score("Anything at all.", context=context) == 0.5


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------


def test_an_unknown_date_is_unknown_not_stale() -> None:
    assert freshness_score(None, researched_at=NOW) is None


def test_a_known_date_still_decays_with_age() -> None:
    recent = freshness_score(datetime(2026, 6, 1, tzinfo=UTC), researched_at=NOW)
    old = freshness_score(datetime(2020, 1, 1, tzinfo=UTC), researched_at=NOW)
    assert recent == 1.0
    assert old is not None and old < recent


def test_unknown_freshness_is_redistributed_not_penalised() -> None:
    known = dict(relevance=0.9, authority=0.75, locality=1.0)
    unknown = composite_quality(**known, freshness=None)
    stale = composite_quality(**known, freshness=0.2)
    fresh = composite_quality(**known, freshness=1.0)

    assert stale < unknown < fresh, "an unmeasured signal must not act as a bad one"
    # Scoring only on what is known leaves the other signals' balance intact.
    assert unknown == pytest.approx(0.8706, abs=0.001)


def test_weights_are_a_whole() -> None:
    total = RELEVANCE_WEIGHT + AUTHORITY_WEIGHT + LOCALITY_WEIGHT + FRESHNESS_WEIGHT
    assert total == pytest.approx(1.0)
    assert composite_quality(relevance=1, authority=1, locality=1, freshness=1) == 1.0
    assert composite_quality(relevance=1, authority=1, locality=1, freshness=None) == 1.0
    assert composite_quality(relevance=0, authority=0, locality=0, freshness=None) == 0.0


# --------------------------------------------------------------------------
# The end result: high confidence is reachable
# --------------------------------------------------------------------------


def test_a_strong_local_source_reaches_high_confidence() -> None:
    albanian = _source("Qira veturash në Prishtinë, aeroporti i Kosovës. Nga 35 euro në ditë.")
    english = _source("Airport car rental Prishtina Kosovo, from EUR 35 per day.")

    for source in (albanian, english):
        assert source is not None
        assert source.confidence is ResearchConfidence.HIGH
        assert source.freshness_score is None, "no date was published"


def test_an_authoritative_source_scores_above_a_business_page() -> None:
    government = _source(
        "Prishtina airport Kosovo rental market report.",
        url="https://transport.gov/kosovo-rentals",
    )
    business = _source(
        "Qira veturash në Prishtinë, Kosovë.",
        url="https://rentacar-ks.example/cmimet",
    )

    assert government is not None and business is not None
    assert government.quality_score > business.quality_score


def test_a_weak_off_market_source_stays_low() -> None:
    blog = _source(
        "Some general thoughts about renting cars.",
        url="https://medium.com/blog/rentals",
        score=0.6,
    )
    assert blog is not None
    assert blog.confidence is ResearchConfidence.LOW


def test_the_confidence_bar_was_not_lowered() -> None:
    assert confidence_for_quality(0.8) is ResearchConfidence.HIGH
    assert confidence_for_quality(0.7999) is ResearchConfidence.MEDIUM
    assert confidence_for_quality(0.5) is ResearchConfidence.MEDIUM
    assert confidence_for_quality(0.4999) is ResearchConfidence.LOW


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


def test_merging_keeps_the_known_date_over_the_unknown_one() -> None:
    dated = source_from_result(
        ResearchResult(
            title="T",
            url="https://a.example",
            content="Prishtinë Kosovë evidence.",
            score=0.9,
            published_at=datetime(2026, 6, 1, tzinfo=UTC),
        ),
        dimension="offers",
        context=_context(),
        researched_at=NOW,
    )
    undated = _source("Prishtinë Kosovë evidence.", url="https://a.example")

    assert dated is not None and undated is not None
    assert merge_source(undated, dated).freshness_score == 1.0
    assert merge_source(dated, undated).freshness_score == 1.0


def test_merging_two_undated_sources_stays_unknown() -> None:
    first = _source("Prishtinë Kosovë evidence one.", url="https://a.example")
    second = _source("Prishtinë Kosovë evidence two.", url="https://a.example")

    assert first is not None and second is not None
    assert merge_source(first, second).freshness_score is None


def test_the_relevance_floor_drops_junk_but_keeps_on_topic_pages() -> None:
    """Calibrated against live provider scores, not a round number.

    A provider relevance score measures similarity to the query. A floor of 0.5
    discarded genuinely on-topic pages — a Kosovo diaspora car-rental guide
    scored 0.41 — and emptied three categories completely.
    """
    on_topic = _source("Qira veturash në Prishtinë, Kosovë.", score=0.41)
    junk = _source("Unrelated page about something else.", score=0.2)

    assert on_topic is not None, "an on-topic page must survive the floor"
    assert on_topic.confidence is not ResearchConfidence.HIGH, "weak matches stay modest"
    assert junk is None, "genuinely unrelated results are still dropped"


# --------------------------------------------------------------------------
# Page furniture
# --------------------------------------------------------------------------

#: Shaped like the language picker that opens a real aggregator page: list
#: items whose whole content is a link, with the label wrapped onto its own
#: line inside the link text.
NAVIGATION = "\n".join(
    f"* [![{name}]()\n\n  {name}](https://example.test/{index})"
    for index, name in enumerate(
        ("Argentina", "Australia", "Belgique", "Brasil", "Canada", "Chile", "Danmark")
    )
)


def test_navigation_menus_are_not_quotable_evidence() -> None:
    """A menu is characters a model can quote and evidence it cannot.

    One measured page spent its first 1,392 characters on a language picker,
    which was most of everything the analyzer was ever shown of it.
    """
    body = f"{NAVIGATION}\n\nRental rates at Prishtina airport start from EUR 29 per day."

    cleaned = readable_text(body)

    assert "Argentina" not in cleaned
    assert "Danmark" not in cleaned
    assert cleaned.startswith("Rental rates at Prishtina airport")


def test_a_link_inside_a_sentence_survives() -> None:
    """Only runs of link-only lines are menus; one link is a citation."""
    body = "Rates are published on the [official price list](https://example.test/prices) page."

    cleaned = readable_text(body)

    assert "official price list" in cleaned


def test_a_single_linked_heading_is_content_not_navigation() -> None:
    body = "# [Car rental at Prishtina airport](https://example.test/prn)\n\nRates from EUR 29."

    cleaned = readable_text(body)

    assert "Car rental at Prishtina airport" in cleaned


def test_the_window_prefers_priced_local_spans_over_the_page_opening() -> None:
    filler = "Generic corporate boilerplate about mobility and travel worldwide. " * 40
    buried = "Rental rates at Prishtina airport start from EUR 29 per day in November."

    window = relevant_window(filler + buried, context=_context(), limit=600)

    assert buried in window
    assert len(window) <= 600


def test_a_short_page_is_shown_whole() -> None:
    text = "Rental rates at Prishtina airport start from EUR 29 per day."

    assert relevant_window(text, context=_context(), limit=600) == text
