from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str

    supabase_url: str = "http://127.0.0.1:54321"
    supabase_public_url: str = "http://127.0.0.1:54321"
    supabase_secret_key: SecretStr = SecretStr("")
    supabase_storage_bucket: str = "invoices"
    signed_url_ttl_seconds: int = Field(default=300, gt=0)

    ingest_shared_secret: SecretStr = SecretStr("")
    n8n_webhook_url: str = ""
    n8n_webhook_secret: SecretStr = SecretStr("")

    llm_provider: Literal["groq"] = "groq"
    llm_model_text: str = ""
    llm_model_vision: str = ""
    groq_api_key: SecretStr = SecretStr("")
    llm_reasoning_effort_text: str = "low"
    llm_reasoning_effort_vision: str = "none"
    llm_max_vision_pages: int = Field(default=3, ge=1)
    llm_timeout_seconds: int = Field(default=60, gt=0)
    llm_max_attempts: int = Field(default=3, ge=1, le=10)
    llm_max_wait_seconds: int = Field(default=60, ge=0, le=600)

    max_file_mb: int = Field(default=15, gt=0)
    ingest_max_file_mb: int = Field(default=50, gt=0)
    max_pages: int = Field(default=20, gt=0)

    worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    processing_timeout_seconds: int = Field(default=300, gt=0)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    outbox_max_attempts: int = Field(default=5, ge=1, le=20)

    amount_tolerance: Decimal = Field(default=Decimal("0.02"), ge=0)
    auto_approve_enabled: bool = False
    auto_approve_max_total: Decimal = Field(default=Decimal("5000"), gt=0)
    vendor_fuzzy_threshold: int = Field(default=85, ge=0, le=100)

    app_base_url: str = "http://localhost:3000"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def _check_file_limits(self) -> Self:
        if self.ingest_max_file_mb < self.max_file_mb:
            raise ValueError("INGEST_MAX_FILE_MB must be greater than or equal to MAX_FILE_MB")
        return self


@lru_cache
def get_settings() -> Settings:
    """Load settings from the environment and the repo-level .env file once per process."""
    return Settings()
