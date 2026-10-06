import pytest
from dotenv import dotenv_values
from pydantic import SecretStr

from app.config import REPO_ROOT, Settings

ENV_EXAMPLE = REPO_ROOT / ".env.example"
# Read by the tests, the n8n container or its workflows, not by the API settings.
NON_API_VARIABLES = {
    "TEST_DATABASE_URL",
    "N8N_ENCRYPTION_KEY",
    "SLACK_WEBHOOK_URL",
    "GOOGLE_SHEET_ID",
}
NON_API_PREFIXES = ("NEXT_PUBLIC_",)

SETTING_NAMES = {name.upper() for name in Settings.model_fields}
SECRET_NAMES = {
    name.upper() for name, field in Settings.model_fields.items() if field.annotation is SecretStr
}


def example_values() -> dict[str, str]:
    return {key: value or "" for key, value in dotenv_values(ENV_EXAMPLE).items()}


def test_every_setting_is_documented() -> None:
    assert SETTING_NAMES - example_values().keys() == set()


def test_no_unknown_variables() -> None:
    unknown = {
        key
        for key in example_values()
        if key not in SETTING_NAMES
        and key not in NON_API_VARIABLES
        and not key.startswith(NON_API_PREFIXES)
    }
    assert unknown == set()


def test_secrets_are_empty() -> None:
    values = example_values()
    secrets = SECRET_NAMES | {"N8N_ENCRYPTION_KEY", "SLACK_WEBHOOK_URL", "GOOGLE_SHEET_ID"}
    assert SECRET_NAMES
    assert {name for name in secrets if values.get(name)} == set()


def test_example_values_are_valid(clean_env: pytest.MonkeyPatch) -> None:
    Settings(_env_file=ENV_EXAMPLE)
