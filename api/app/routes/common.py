import json
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import Engine

from app.config import Settings
from app.outbox import json_default
from app.storage import DocumentStore


def exact_json(content: Any, status_code: int = 200) -> Response:
    """JSON response that keeps amounts as exact strings instead of floats."""
    return Response(
        content=json.dumps(content, default=json_default),
        status_code=status_code,
        media_type="application/json",
    )


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> DocumentStore:
    store = request.app.state.store
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "document storage is not configured"
        )
    return store
