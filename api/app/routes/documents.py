import logging
from collections.abc import Callable
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app import queries, review
from app.auth import Reviewer, require_reviewer
from app.config import Settings
from app.routes.common import exact_json, get_engine, get_settings
from app.status import Status
from app.storage import StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

Reviewed = Annotated[Reviewer, Depends(require_reviewer)]
Db = Annotated[Engine, Depends(get_engine)]
Config = Annotated[Settings, Depends(get_settings)]


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1)


def review_call[T](action: Callable[[], T]) -> T:
    """Run a review action, turning domain errors into HTTP errors."""
    try:
        return action()
    except review.ReviewError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/documents")
def list_documents(
    _: Reviewed,
    engine: Db,
    status_filter: Annotated[Status | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    """Review queue, newest first."""
    with engine.connect() as conn:
        return exact_json(queries.list_documents(conn, status_filter, limit, offset))


@router.get("/documents/{document_id}")
def get_document(
    document_id: UUID, _: Reviewed, engine: Db, settings: Config, request: Request
) -> Response:
    """Document page data with a short-lived link to the original PDF."""
    with engine.connect() as conn:
        detail = queries.document_detail(conn, document_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    store = request.app.state.store
    pdf_url = None
    if store is not None:
        try:
            pdf_url = store.signed_url(
                detail["document"]["storage_path"], settings.signed_url_ttl_seconds
            )
        except StorageError:
            logger.warning("could not sign the PDF link for %s", document_id)
    return exact_json({**detail, "pdf_url": pdf_url})


@router.post("/documents/{document_id}/approve")
def approve_document(
    document_id: UUID, body: review.ApproveRequest, reviewer: Reviewed, engine: Db, settings: Config
) -> Response:
    """Approve with final values; failing hard checks need override and a comment."""
    with engine.begin() as conn:
        result = review_call(lambda: review.approve(conn, document_id, body, reviewer, settings))
    return exact_json(result)


@router.post("/documents/{document_id}/reject")
def reject_document(
    document_id: UUID, body: RejectRequest, reviewer: Reviewed, engine: Db
) -> Response:
    """Reject a document under review with a mandatory reason."""
    with engine.begin() as conn:
        new_status = review_call(lambda: review.reject(conn, document_id, body.reason, reviewer))
    return exact_json({"document_id": document_id, "status": new_status})


@router.post("/documents/{document_id}/reprocess")
def reprocess_document(document_id: UUID, reviewer: Reviewed, engine: Db) -> Response:
    """Send a failed document back to the worker."""
    with engine.begin() as conn:
        new_status = review_call(lambda: review.reprocess(conn, document_id, reviewer))
    return exact_json({"document_id": document_id, "status": new_status})


@router.get("/invoices/export.csv")
def export_invoices(
    _: Reviewed,
    engine: Db,
    date_from: date | None = None,
    date_to: date | None = None,
) -> PlainTextResponse:
    """Approved invoices as CSV with fixed columns."""
    with engine.connect() as conn:
        content = queries.export_csv(conn, date_from, date_to)
    return PlainTextResponse(
        content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="invoices.csv"'},
    )


@router.get("/stats")
def get_stats(_: Reviewed, engine: Db, settings: Config) -> Response:
    """Counts and rates for the Stats screen."""
    with engine.connect() as conn:
        return exact_json(queries.stats(conn, settings.outbox_max_attempts))
