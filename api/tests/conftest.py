import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from dotenv import dotenv_values
from sqlalchemy import URL, Connection, Engine, create_engine, make_url, text

from app.config import REPO_ROOT, Settings

MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
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


def apply_migrations(url: URL) -> None:
    """Apply every SQL migration in filename order."""
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conninfo) as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def migrated_engine(admin_database_url: str) -> Iterator[Engine]:
    """Create a throwaway database with all migrations applied and drop it afterwards."""
    admin_url = make_url(admin_database_url)
    name = f"test_{uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(admin_url.set(database=name))
    try:
        apply_migrations(admin_url.set(database=name))
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def db(migrated_engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is rolled back after the test."""
    with migrated_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()
