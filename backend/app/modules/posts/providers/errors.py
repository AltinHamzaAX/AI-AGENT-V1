class ProviderError(RuntimeError):
    """Safe provider failure that never includes credentials or raw response bodies."""


class ProviderConfigurationError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


__all__ = ["ProviderConfigurationError", "ProviderError", "ProviderResponseError"]
