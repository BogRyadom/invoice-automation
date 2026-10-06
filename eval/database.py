from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from sqlalchemy import URL, Engine, create_engine, make_url, text

from app.config import REPO_ROOT
from corpus.models import KnownVendor

MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"


def apply_migrations(url: URL) -> None:
    """Apply every SQL migration in filename order."""
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conninfo) as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))


@contextmanager
def temporary_database(admin_url: str, prefix: str = "tmp") -> Iterator[Engine]:
    """Throwaway database with all migrations applied; dropped on exit."""
    admin = make_url(admin_url)
    name = f"{prefix}_{uuid4().hex[:12]}"
    admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(admin.set(database=name))
    try:
        apply_migrations(admin.set(database=name))
        yield engine
    finally:
        engine.dispose()
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin_engine.dispose()


def seed_vendors(engine: Engine, vendors: Sequence[KnownVendor]) -> None:
    """Preload the vendor registry for an eval run."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO vendors (canonical_name, normalized_name, tax_id)
                VALUES (:canonical_name, :normalized_name, :tax_id)
                """
            ),
            [vendor.model_dump() for vendor in vendors],
        )
