class ProviderError(RuntimeError):
    """Safe provider failure that never includes credentials or raw response bodies."""


class ProviderConfigurationError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    """Too many requests for now. Retrying later is the correct response."""


class ProviderQuotaError(ProviderError):
    """The plan's usage allowance is spent.

    Distinct from a rate limit: waiting does not help within a run, so callers
    should stop asking rather than retry. Kept separate from a generic failure
    because the operational response is to top up a plan, not to investigate a
    bug.
    """


__all__ = [
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderQuotaError",
    "ProviderRateLimitError",
    "ProviderResponseError",
]
