"""Provider-side targeting for research requests.

Locality, freshness, and source authority are cheapest to buy at query time.
This module turns the semantic contract's free-text market and platform into
the provider parameters that actually narrow a search, and refuses to guess
when the provider cannot express the target.
"""

from .schemas import ResearchContext

# Tavily accepts a country *name*, not an ISO code, and only alongside
# topic="general". Kosovo is not in the provider's country enum, so it is mapped
# to Albania (see _COUNTRY_ALIASES) to buy Albanian-language regional targeting.
# Markets with no supported equivalent resolve to None and the parameter is
# omitted, rather than targeting an unrelated country.
SUPPORTED_COUNTRIES: frozenset[str] = frozenset(
    {
        "albania",
        "austria",
        "belgium",
        "bosnia and herzegovina",
        "bulgaria",
        "canada",
        "croatia",
        "czechia",
        "denmark",
        "finland",
        "france",
        "germany",
        "greece",
        "hungary",
        "ireland",
        "italy",
        "montenegro",
        "netherlands",
        "north macedonia",
        "norway",
        "poland",
        "portugal",
        "romania",
        "serbia",
        "slovakia",
        "slovenia",
        "spain",
        "sweden",
        "switzerland",
        "turkey",
        "united arab emirates",
        "united kingdom",
        "united states",
    }
)

# Free-text market/location values that map onto a supported country. Cities and
# demonyms are included because the semantic contract captures whatever the
# client wrote ("Prishtina airport", "Tirana", "Swiss diaspora").
_COUNTRY_ALIASES: dict[str, str] = {
    # Kosovo has no Tavily country of its own. Albania is the closest supported
    # target and shares the language, so Kosovo markets are geo-targeted there.
    # Results that are Albania-specific rather than Kosovo-specific are still
    # demoted afterwards by ResearchSource.locality_score, which scores against
    # the declared market text rather than the resolved country.
    "kosovo": "albania",
    "kosova": "albania",
    "kosove": "albania",
    "kosovë": "albania",
    "kosovës": "albania",
    "kosovar": "albania",
    "prishtina": "albania",
    "prishtinë": "albania",
    "pristina": "albania",
    "peja": "albania",
    "prizren": "albania",
    "ferizaj": "albania",
    "gjakova": "albania",
    "gjilan": "albania",
    "mitrovica": "albania",
    "shqiperi": "albania",
    "shqipëri": "albania",
    "shqiperia": "albania",
    "shqipëria": "albania",
    "albanian": "albania",
    "tirana": "albania",
    "tiranë": "albania",
    "durres": "albania",
    "durrës": "albania",
    "vlore": "albania",
    "vlorë": "albania",
    "macedonia": "north macedonia",
    "skopje": "north macedonia",
    "shkup": "north macedonia",
    "belgrade": "serbia",
    "serbian": "serbia",
    "podgorica": "montenegro",
    "sarajevo": "bosnia and herzegovina",
    "zagreb": "croatia",
    "athens": "greece",
    "greek": "greece",
    "berlin": "germany",
    "munich": "germany",
    "münchen": "germany",
    "frankfurt": "germany",
    "hamburg": "germany",
    "cologne": "germany",
    "stuttgart": "germany",
    "german": "germany",
    "deutschland": "germany",
    "zurich": "switzerland",
    "zürich": "switzerland",
    "geneva": "switzerland",
    "basel": "switzerland",
    "bern": "switzerland",
    "swiss": "switzerland",
    "vienna": "austria",
    "wien": "austria",
    "austrian": "austria",
    "stockholm": "sweden",
    "swedish": "sweden",
    "oslo": "norway",
    "copenhagen": "denmark",
    "helsinki": "finland",
    "amsterdam": "netherlands",
    "holland": "netherlands",
    "dutch": "netherlands",
    "brussels": "belgium",
    "paris": "france",
    "french": "france",
    "milan": "italy",
    "rome": "italy",
    "italian": "italy",
    "madrid": "spain",
    "barcelona": "spain",
    "spanish": "spain",
    "lisbon": "portugal",
    "london": "united kingdom",
    "manchester": "united kingdom",
    "uk": "united kingdom",
    "britain": "united kingdom",
    "british": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "usa": "united states",
    "us": "united states",
    "america": "united states",
    "american": "united states",
    "new york": "united states",
    "dubai": "united arab emirates",
    "uae": "united arab emirates",
    "istanbul": "turkey",
    "turkish": "turkey",
    "toronto": "canada",
    "canadian": "canada",
    "warsaw": "poland",
    "polish": "poland",
    "prague": "czechia",
    "czech republic": "czechia",
    "budapest": "hungary",
    "bucharest": "romania",
    "sofia": "bulgaria",
    "ljubljana": "slovenia",
    "bratislava": "slovakia",
    "dublin": "ireland",
}

# Official specification and policy sources per platform. Pinning these beats
# hoping the vendor's own documentation outranks SEO recaps of it.
_PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "instagram": (
        "help.instagram.com",
        "business.instagram.com",
        "creators.instagram.com",
        "developers.facebook.com",
        "transparency.meta.com",
    ),
    "facebook": (
        "business.facebook.com",
        "web.facebook.com",
        "developers.facebook.com",
        "transparency.meta.com",
    ),
    "linkedin": (
        "business.linkedin.com",
        "linkedin.com",
        "learn.microsoft.com",
    ),
    "tiktok": (
        "ads.tiktok.com",
        "business-api.tiktok.com",
        "support.tiktok.com",
        "developers.tiktok.com",
    ),
    "youtube": (
        "support.google.com",
        "developers.google.com",
        "blog.youtube",
    ),
    "x": (
        "business.x.com",
        "help.x.com",
        "developer.x.com",
    ),
    "pinterest": (
        "business.pinterest.com",
        "help.pinterest.com",
        "developers.pinterest.com",
    ),
}


def resolve_country(context: ResearchContext) -> str | None:
    """Return a provider-supported country name for the target market.

    Returns None when the market cannot be expressed as a supported country,
    so the caller omits the parameter instead of targeting a neighbour.
    """
    for value in (context.market, context.location):
        country = _country_from_text(value)
        if country is not None:
            return country
    return None


def platform_domains(platform: str) -> tuple[str, ...]:
    """Official documentation domains for a declared platform, if known."""
    normalized = _normalize(platform)
    for name, domains in _PLATFORM_DOMAINS.items():
        if name in normalized:
            return domains
    return ()


def _country_from_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _normalize(value)
    if not normalized:
        return None
    if normalized in SUPPORTED_COUNTRIES:
        return normalized
    if normalized in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[normalized]
    # Longest first so "north macedonia" wins over "macedonia" inside one phrase.
    for candidate in sorted(SUPPORTED_COUNTRIES, key=len, reverse=True):
        if _contains_phrase(normalized, candidate):
            return candidate
    for alias in sorted(_COUNTRY_ALIASES, key=len, reverse=True):
        if _contains_phrase(normalized, alias):
            return _COUNTRY_ALIASES[alias]
    return None


def _contains_phrase(haystack: str, needle: str) -> bool:
    tokens = haystack.split()
    needle_tokens = needle.split()
    span = len(needle_tokens)
    return any(tokens[index : index + span] == needle_tokens for index in range(len(tokens)))


def _normalize(value: str) -> str:
    return " ".join(value.replace(",", " ").replace("/", " ").split()).casefold()


__all__ = [
    "SUPPORTED_COUNTRIES",
    "platform_domains",
    "resolve_country",
]
