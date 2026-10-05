from sqlalchemy import Engine, create_engine, text


def create_db_engine(database_url: str) -> Engine:
    """Create an engine that fails fast when the database is unreachable."""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def ping(engine: Engine) -> None:
    """Run a trivial query. Raises SQLAlchemyError if the database is unreachable."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
