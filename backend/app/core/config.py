from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Promotiva"
    app_env: str = "development"
    app_debug: bool = False

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3001,http://127.0.0.1:3001"
    )

    postgres_db: str = "promotiva"
    postgres_user: str = "promotiva"
    postgres_password: str = Field(repr=False)
    database_url: str = Field(repr=False)

    redis_url: str = Field(repr=False)
    provider_timeout_seconds: float = Field(default=120.0, gt=0)
    generation_job_max_attempts: int = Field(default=3, gt=0)
    generation_job_timeout_seconds: int = Field(default=900, gt=0)
    generation_job_lease_seconds: int = Field(default=960, gt=0)
    generation_job_retry_backoff_seconds: int = Field(default=30, ge=0)
    generation_worker_poll_seconds: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def validate_generation_job_lease(self) -> Self:
        if self.generation_job_lease_seconds <= self.generation_job_timeout_seconds:
            raise ValueError(
                "GENERATION_JOB_LEASE_SECONDS must exceed GENERATION_JOB_TIMEOUT_SECONDS"
            )
        return self

    storage_provider: str = "s3"
    s3_endpoint: str
    s3_bucket: str = "promotiva"
    s3_access_key: str = Field(repr=False)
    s3_secret_key: str = Field(repr=False)
    asset_max_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    asset_max_dimension: int = Field(default=12_000, gt=0)
    asset_max_pixels: int = Field(default=40_000_000, gt=0)

    llm_provider: str = ""
    llm_model: str = ""
    gemini_api_key: str = Field(default="", repr=False)
    #: The stages that invent rather than extract. Empty means they share
    #: LLM_MODEL; a larger model here costs nothing on the other stages.
    creative_llm_model: str = ""
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_llm_num_predict: int = Field(default=1_024, gt=0)
    # Creative exploration emits a substantially larger structured contract
    # than the extraction stages, so it needs its own output budget.
    ollama_creative_num_predict: int = Field(default=3_072, gt=0)
    ollama_vision_num_predict: int = Field(default=768, gt=0)
    ollama_keep_alive: str = "10m"
    image_provider: str = ""
    image_model: str = ""
    huggingface_api_base_url: str = "https://router.huggingface.co"
    huggingface_api_token: str = Field(default="", repr=False)
    vision_provider: str = ""
    vision_model: str = ""
    embedding_provider: str = ""
    embedding_model: str = ""
    research_provider: str = ""
    research_cache_ttl_seconds: int = Field(default=3_600, gt=0)
    research_max_concurrency: int = Field(default=4, ge=1, le=8)
    # Three nested bounds so a hung provider degrades one dimension, then one
    # category, then the stage — instead of holding the generation job's whole
    # budget. All three stay well under GENERATION_JOB_TIMEOUT_SECONDS.
    research_search_timeout_seconds: float = Field(default=30.0, gt=0)
    research_tool_timeout_seconds: float = Field(default=180.0, gt=0)
    research_stage_timeout_seconds: float = Field(default=300.0, gt=0)

    @model_validator(mode="after")
    def validate_research_timeouts(self) -> Self:
        if self.research_search_timeout_seconds > self.research_tool_timeout_seconds:
            raise ValueError(
                "RESEARCH_SEARCH_TIMEOUT_SECONDS must not exceed RESEARCH_TOOL_TIMEOUT_SECONDS"
            )
        if self.research_tool_timeout_seconds > self.research_stage_timeout_seconds:
            raise ValueError(
                "RESEARCH_TOOL_TIMEOUT_SECONDS must not exceed RESEARCH_STAGE_TIMEOUT_SECONDS"
            )
        if self.research_stage_timeout_seconds >= self.generation_job_timeout_seconds:
            raise ValueError(
                "RESEARCH_STAGE_TIMEOUT_SECONDS must be below GENERATION_JOB_TIMEOUT_SECONDS"
            )
        return self

    tavily_api_base_url: str = "https://api.tavily.com"
    tavily_api_key: str = Field(default="", repr=False)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
