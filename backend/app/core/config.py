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
    cors_origins: str = "http://localhost:3000"

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
    ollama_base_url: str = "http://host.docker.internal:11434"
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
    tavily_api_base_url: str = "https://api.tavily.com"
    tavily_api_key: str = Field(default="", repr=False)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
