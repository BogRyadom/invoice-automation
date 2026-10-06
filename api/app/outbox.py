# Outbox delivery to the n8n webhook (docs/SPEC.md sections 4 and 10). Events are written in the
# same transaction as the status change and delivered here with bounded retries. A failed
# delivery never changes the document status. n8n answers {"exported": true} once the Sheets
# row is written; only then does an approved invoice become exported.

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import Engine, text

from app.config import Settings
from app.status import change_status

BACKOFF_BASE_SECONDS = 30
EXPORTED_ON_DELIVERY = frozenset({"approved", "auto_approved"})
REQUEST_TIMEOUT_SECONDS = 15


def json_default(value: Any) -> Any:
    """JSON encoding that keeps money exact and dates in ISO format."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal | UUID):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def deliver_next(engine: Engine, client: httpx.Client, settings: Settings) -> bool:
    """Try to deliver the oldest due event. Returns False when nothing is due."""
    if not settings.n8n_webhook_url:
        return False
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, document_id, event_type, payload, attempts FROM outbox_events
                WHERE delivered_at IS NULL AND attempts < :max AND next_attempt_at <= now()
                ORDER BY created_at, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"max": settings.outbox_max_attempts},
        ).one_or_none()
        if row is None:
            return False

        body = {
            "event_id": row.id,
            "event_type": row.event_type,
            "document_id": row.document_id,
            "review_url": f"{settings.app_base_url.rstrip('/')}/documents/{row.document_id}",
            "payload": row.payload,
        }
        error = None
        exported = False
        try:
            response = client.post(
                settings.n8n_webhook_url,
                content=json.dumps(body, default=json_default),
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Secret": settings.n8n_webhook_secret.get_secret_value(),
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if not response.is_success:
                error = f"HTTP {response.status_code}"
            else:
                exported = confirms_export(response)
        except httpx.HTTPError as exc:
            error = type(exc).__name__

        if error:
            conn.execute(
                text(
                    """
                    UPDATE outbox_events
                    SET attempts = attempts + 1, last_error = :error,
                        next_attempt_at = now() + make_interval(secs => :delay)
                    WHERE id = :id
                    """
                ),
                {"id": row.id, "error": error, "delay": BACKOFF_BASE_SECONDS * 2**row.attempts},
            )
            return True

        conn.execute(
            text(
                """
                UPDATE outbox_events SET attempts = attempts + 1, delivered_at = now(),
                                         last_error = NULL
                WHERE id = :id
                """
            ),
            {"id": row.id},
        )
        if exported and row.event_type in EXPORTED_ON_DELIVERY and row.document_id is not None:
            mark_exported(conn, row.document_id)
    return True


def confirms_export(response: httpx.Response) -> bool:
    """True when n8n reports that the Sheets row was written."""
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("exported") is True


def mark_exported(conn: Any, document_id: UUID) -> None:
    """n8n confirmed the Sheets row: the approved invoice is exported."""
    status = conn.execute(
        text("SELECT status FROM documents WHERE id = :id FOR UPDATE"), {"id": document_id}
    ).scalar_one()
    if status not in EXPORTED_ON_DELIVERY:
        return
    conn.execute(
        text("UPDATE invoices SET exported_at = now() WHERE document_id = :id"),
        {"id": document_id},
    )
    change_status(conn, document_id, "exported", actor="n8n")


def deliver_batch(engine: Engine, client: httpx.Client, settings: Settings, limit: int = 20) -> int:
    """Deliver up to limit due events. Returns how many were attempted."""
    attempted = 0
    while attempted < limit and deliver_next(engine, client, settings):
        attempted += 1
    return attempted
