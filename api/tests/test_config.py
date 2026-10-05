from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import Settings

DATABASE_URL = "postgresql+psycopg://postgres@localhost:5432/postgres"


def load() -> Settings:
    return Settings(_env_file=None, database_url=DATABASE_URL)


def test_defaults(clean_env: pytest.MonkeyPatch) -> None:
    settings = load()

    assert settings.auto_approve_enabled is False
    assert settings.amount_tolerance == Decimal("0.02")
    assert settings.auto_approve_max_total == Decimal("5000")
    assert settings.llm_provider == "groq"
    assert settings.llm_max_attempts == 3
    assert settings.outbox_max_attempts == 5


def test_money_values_are_exact_decimals(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("AMOUNT_TOLERANCE", "0.10")
    clean_env.setenv("AUTO_APPROVE_MAX_TOTAL", "1234.5678")

    settings = load()

    assert isinstance(settings.amount_tolerance, Decimal)
    assert settings.amount_tolerance == Decimal("0.10")
    assert settings.auto_approve_max_total == Decimal("1234.5678")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AMOUNT_TOLERANCE", "-0.01"),
        ("AUTO_APPROVE_MAX_TOTAL", "0"),
        ("AUTO_APPROVE_ENABLED", "maybe"),
        ("VENDOR_FUZZY_THRESHOLD", "101"),
        ("LLM_PROVIDER", "unknown"),
        ("LLM_MAX_ATTEMPTS", "0"),
        ("LLM_MAX_ATTEMPTS", "11"),
        ("WORKER_MAX_ATTEMPTS", "0"),
        ("OUTBOX_MAX_ATTEMPTS", "21"),
        ("MAX_PAGES", "0"),
        ("LOG_LEVEL", "VERBOSE"),
    ],
)
def test_invalid_values_are_rejected(clean_env: pytest.MonkeyPatch, name: str, value: str) -> None:
    clean_env.setenv(name, value)

    with pytest.raises(ValidationError):
        load()


def test_ingest_limit_cannot_be_below_processing_limit(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("MAX_FILE_MB", "20")
    clean_env.setenv("INGEST_MAX_FILE_MB", "10")

    with pytest.raises(ValidationError, match="INGEST_MAX_FILE_MB"):
        load()


def test_database_url_is_required(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secrets_are_masked(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GROQ_API_KEY", "test-secret-value")

    settings = load()

    assert "test-secret-value" not in repr(settings)
    assert settings.groq_api_key.get_secret_value() == "test-secret-value"
