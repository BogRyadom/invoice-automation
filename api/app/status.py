# Document status machine (docs/SPEC.md section 5). Every transition is validated here and
# recorded in document_events; nothing else updates documents.status.

import json
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, text

Status = Literal[
    "received",
    "processing",
    "skipped",
    "failed",
    "needs_review",
    "auto_approved",
    "approved",
    "rejected",
    "exported",
]

TRANSITIONS: dict[str, frozenset[str]] = {
    "received": frozenset({"processing"}),
    "processing": frozenset({"skipped", "failed", "needs_review", "auto_approved"}),
    "needs_review": frozenset({"approved", "rejected"}),
    "auto_approved": frozenset({"exported"}),
    "approved": frozenset({"exported"}),
    "failed": frozenset({"processing", "approved"}),
    "skipped": frozenset(),
    "rejected": frozenset(),
    "exported": frozenset(),
}


class InvalidTransition(Exception):
    pass


def check_transition(current: str, target: str) -> None:
    """Raise InvalidTransition unless the status machine allows current -> target."""
    if target not in TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"{current} -> {target} is not allowed")


def record_event(
    conn: Connection, document_id: UUID, event_type: str, actor: str, payload: dict[str, Any]
) -> None:
    """Append an entry to the document timeline."""
    conn.execute(
        text(
            """
            INSERT INTO document_events (document_id, event_type, actor, payload)
            VALUES (:document_id, :event_type, :actor, CAST(:payload AS jsonb))
            """
        ),
        {
            "document_id": document_id,
            "event_type": event_type,
            "actor": actor,
            "payload": json.dumps(payload, default=str),
        },
    )


def change_status(
    conn: Connection,
    document_id: UUID,
    target: Status,
    *,
    actor: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    duplicate_of: UUID | None = None,
) -> str:
    """Move a document to a new status inside the caller's transaction. Returns the old status."""
    current = conn.execute(
        text("SELECT status FROM documents WHERE id = :id FOR UPDATE"), {"id": document_id}
    ).scalar_one()
    check_transition(current, target)
    conn.execute(
        text(
            """
            UPDATE documents
            SET status = :target,
                skip_reason = CASE WHEN :target = 'skipped' THEN :reason ELSE skip_reason END,
                failure_reason = CASE
                    WHEN :target = 'failed' THEN :reason
                    WHEN :target = 'processing' THEN NULL
                    ELSE failure_reason END,
                duplicate_of_document_id = COALESCE(:duplicate_of, duplicate_of_document_id),
                locked_at = CASE WHEN :target = 'processing' THEN locked_at ELSE NULL END
            WHERE id = :id
            """
        ),
        {"id": document_id, "target": target, "reason": reason, "duplicate_of": duplicate_of},
    )
    payload: dict[str, Any] = {"from": current, "to": target}
    if reason:
        payload["reason"] = reason
    if duplicate_of:
        payload["duplicate_of"] = duplicate_of
    record_event(conn, document_id, "status_changed", actor, {**payload, **(details or {})})
    return current
