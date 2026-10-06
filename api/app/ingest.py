# Ingest of one e-mail attachment delivered by n8n (docs/SPEC.md sections 10 and 11).
# Idempotent on (gmail_message_id, sha256): a redelivery returns the existing document.

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from app.status import record_event
from app.storage import AlreadyExists, DocumentStore


@dataclass(frozen=True)
class EmailAttachment:
    gmail_message_id: str
    attachment_id: str | None
    filename: str
    sender: str | None
    subject: str | None
    received_at: datetime


@dataclass(frozen=True)
class IngestResult:
    document_id: UUID
    status: str
    created: bool


def storage_path_for(sha256: str) -> str:
    """Content-addressed path: the same file is stored once, whatever e-mail it came in."""
    return f"originals/{sha256[:2]}/{sha256}"


def _existing(conn: Connection, message_id: str, sha256: str) -> IngestResult | None:
    row = conn.execute(
        text("SELECT id, status FROM documents WHERE gmail_message_id = :m AND sha256 = :s"),
        {"m": message_id, "s": sha256},
    ).one_or_none()
    return IngestResult(row.id, row.status, created=False) if row else None


def ingest_attachment(
    engine: Engine,
    store: DocumentStore,
    attachment: EmailAttachment,
    data: bytes,
    content_type: str,
) -> IngestResult:
    """Store the original and queue it for the worker, once per message and file."""
    sha256 = hashlib.sha256(data).hexdigest()
    with engine.connect() as conn:
        existing = _existing(conn, attachment.gmail_message_id, sha256)
    if existing:
        return existing

    path = storage_path_for(sha256)
    with suppress(AlreadyExists):
        store.write(path, data, content_type)

    with engine.begin() as conn:
        try:
            with conn.begin_nested():
                document_id = conn.execute(
                    text(
                        """
                        INSERT INTO documents (gmail_message_id, attachment_id, filename, sha256,
                                               storage_path, sender, subject, received_at)
                        VALUES (:gmail_message_id, :attachment_id, :filename, :sha256,
                                :storage_path, :sender, :subject, :received_at)
                        RETURNING id
                        """
                    ),
                    {
                        "gmail_message_id": attachment.gmail_message_id,
                        "attachment_id": attachment.attachment_id,
                        "filename": attachment.filename,
                        "sha256": sha256,
                        "storage_path": path,
                        "sender": attachment.sender,
                        "subject": attachment.subject,
                        "received_at": attachment.received_at,
                    },
                ).scalar_one()
        except IntegrityError:
            # The same attachment was delivered twice at the same moment.
            existing = _existing(conn, attachment.gmail_message_id, sha256)
            assert existing is not None
            return existing
        record_event(
            conn,
            document_id,
            "received",
            "n8n",
            {"filename": attachment.filename, "size_bytes": len(data)},
        )
    return IngestResult(document_id, "received", created=True)
