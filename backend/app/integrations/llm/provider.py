from app.integrations.llm.base import LLMProvider


def create_llm_provider() -> LLMProvider:
    raise NotImplementedError("No LLM provider is configured")
