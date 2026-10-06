from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import TokenVerifier
from app.config import Settings, get_settings
from app.db import create_db_engine
from app.log import configure_logging
from app.routes import documents, health, ingest
from app.storage import DocumentStore, SupabaseDocumentStore


def create_app(
    settings: Settings | None = None,
    *,
    store: DocumentStore | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastAPI:
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
    if store is None and settings.supabase_secret_key.get_secret_value():
        store = SupabaseDocumentStore(settings)
    app.state.store = store
    app.state.token_verifier = token_verifier or TokenVerifier.for_supabase(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_base_url.rstrip("/")],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(documents.router)
    return app
