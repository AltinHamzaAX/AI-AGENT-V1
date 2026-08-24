from functools import lru_cache

from pydantic import Field
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

    storage_provider: str = "s3"
    s3_endpoint: str
    s3_bucket: str = "promotiva"
    s3_access_key: str = Field(repr=False)
    s3_secret_key: str = Field(repr=False)

    llm_provider: str = ""
    llm_model: str = ""
    image_provider: str = ""
    image_model: str = ""
    vision_provider: str = ""
    vision_model: str = ""
    research_provider: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
