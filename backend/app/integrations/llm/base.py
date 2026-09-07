"""Provider-neutral LLM contracts shared by application modules.

The canonical contracts predate Campaign Mode and currently live with the
Posts provider contracts. Re-exporting them here preserves one implementation
while giving Campaign components a neutral import boundary.
"""

from app.modules.posts.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderConfigurationError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderResponseError,
)

__all__ = [
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderQuotaError",
    "ProviderRateLimitError",
    "ProviderResponseError",
]
