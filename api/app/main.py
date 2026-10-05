from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db import create_db_engine
from app.log import configure_logging
from app.routes import health


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application. Used by uvicorn with --factory."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_db_engine(settings.database_url)
        yield
        app.state.engine.dispose()

    app = FastAPI(title="Invoice Automation API", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    return app
