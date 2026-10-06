import os
from collections.abc import Iterator

import pytest
from dotenv import dotenv_values
from sqlalchemy import Connection, Engine

from app.config import REPO_ROOT, Settings
from eval.database import temporary_database

UNREACHABLE_DATABASE_URL = "postgresql+psycopg://postgres@127.0.0.1:1/postgres"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove every settings variable from the process environment."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


@pytest.fixture
def settings(clean_env: pytest.MonkeyPatch) -> Settings:
    return Settings(_env_file=None, database_url=UNREACHABLE_DATABASE_URL)


@pytest.fixture(scope="session")
def admin_database_url() -> str:
    """Admin URL used to create throwaway databases. DB tests fail in CI when it is missing."""
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(REPO_ROOT / ".env").get(
        "TEST_DATABASE_URL"
    )
    if not url:
        message = "TEST_DATABASE_URL is not set"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return url


@pytest.fixture(scope="module")
def migrated_engine(admin_database_url: str) -> Iterator[Engine]:
    """Throwaway database with all migrations applied, shared by one test module."""
    with temporary_database(admin_database_url, prefix="test") as engine:
        yield engine


@pytest.fixture
def db(migrated_engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is rolled back after the test."""
    with migrated_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()
