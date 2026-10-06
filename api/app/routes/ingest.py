import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.auth import require_ingest_secret
from app.config import Settings
from app.ingest import EmailAttachment, ingest_attachment
from app.routes.common import exact_json, get_engine, get_settings, get_store
from app.storage import DocumentStore, StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_ingest_secret)])


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
def ingest_document(
    file: Annotated[UploadFile, File()],
    gmail_message_id: Annotated[str, Form(min_length=1)],
    received_at: Annotated[datetime, Form()],
    engine: Annotated[Engine, Depends(get_engine)],
    store: Annotated[DocumentStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings)],
    attachment_id: Annotated[str | None, Form()] = None,
    sender: Annotated[str | None, Form()] = None,
    subject: Annotated[str | None, Form()] = None,
) -> Response:
    """Accept one attachment from n8n: 202 when queued, 200 when it was already delivered."""
    limit = settings.ingest_max_file_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"file is larger than {settings.ingest_max_file_mb} MB",
        )
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "file is empty")
    attachment = EmailAttachment(
        gmail_message_id=gmail_message_id,
        attachment_id=attachment_id,
        filename=file.filename or "attachment",
        sender=sender,
        subject=subject,
        received_at=received_at,
    )
    try:
        result = ingest_attachment(
            engine, store, attachment, data, file.content_type or "application/octet-stream"
        )
    except (StorageError, SQLAlchemyError) as exc:
        logger.warning("ingest failed: %s", type(exc).__name__)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "storage or database unavailable"
        ) from exc
    return exact_json(
        {"document_id": result.document_id, "status": result.status, "created": result.created},
        status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK,
    )
