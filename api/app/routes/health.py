import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.db import ping

logger = logging.getLogger(__name__)

router = APIRouter()


def get_db_check(request: Request) -> Callable[[], None]:
    """Return a callable that pings the database bound to the app."""
    engine = request.app.state.engine
    return lambda: ping(engine)


@router.get("/health")
def health(db_check: Annotated[Callable[[], None], Depends(get_db_check)]) -> JSONResponse:
    """Report API and database availability."""
    try:
        db_check()
    except SQLAlchemyError as exc:
        logger.warning("health check: database unreachable (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"status": "error", "database": "unreachable"})
    return JSONResponse(content={"status": "ok", "database": "ok"})
