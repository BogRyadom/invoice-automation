# Loads the fictional vendors from corpus/vendors_seed.json into the database from .env, as if
# their first invoices had already been approved, so the demo can show auto-approval.

import sys

from sqlalchemy import Engine, text

from app.config import get_settings
from app.db import create_db_engine
from corpus.storage import load_known_vendors


def seed(engine: Engine) -> int:
    """Insert the known vendors that are missing. Returns how many were added."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO vendors (canonical_name, normalized_name, tax_id)
                VALUES (:canonical_name, :normalized_name, :tax_id)
                ON CONFLICT DO NOTHING
                """
            ),
            [vendor.model_dump() for vendor in load_known_vendors()],
        )
    return result.rowcount


def main() -> int:
    """Seed the vendor registry of the local database."""
    engine = create_db_engine(get_settings().database_url)
    try:
        added = seed(engine)
    finally:
        engine.dispose()
    print(f"Added {added} known vendors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
