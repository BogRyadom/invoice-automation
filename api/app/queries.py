# Read models for the review UI: queue, document page, stats and CSV export
# (docs/SPEC.md sections 11 and 13).

import csv
import io
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Connection, text

from app.extraction.contract import Extraction
from app.repository import AMOUNT_COLUMNS, LATEST_EXTRACTION, money_text

EXPORT_COLUMNS = (
    "invoice_id",
    "document_id",
    "vendor",
    "vendor_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "discount",
    "shipping",
    "tax_total",
    "total",
    "approval_mode",
    "approved_by",
    "approved_at",
    "exported_at",
)


def rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in result]


def list_documents(
    conn: Connection, statuses: Sequence[str] | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    """Queue rows: vendor, number, amount, received date and number of raised checks."""
    return rows(
        conn.execute(
            text(
                f"""
                SELECT d.id, d.status, d.skip_reason, d.failure_reason, d.filename, d.sender,
                       d.received_at,
                       COALESCE(x.normalized->'vendor'->>'canonical_name',
                                x.normalized->'invoice'->>'vendor_name') AS vendor,
                       x.normalized->'invoice'->>'invoice_number' AS invoice_number,
                       x.normalized->'invoice'->>'total' AS total,
                       x.normalized->'invoice'->>'currency' AS currency,
                       (SELECT count(DISTINCT c.check_id) FROM check_results c
                        WHERE c.extraction_id = x.id AND c.status = 'fail') AS flag_count
                FROM documents d {LATEST_EXTRACTION}
                WHERE (CAST(:statuses AS text[]) IS NULL OR d.status = ANY(:statuses))
                ORDER BY d.received_at DESC, d.id
                LIMIT :limit OFFSET :offset
                """
            ),
            {"statuses": list(statuses) if statuses else None, "limit": limit, "offset": offset},
        )
    )


def document_detail(conn: Connection, document_id: UUID) -> dict[str, Any] | None:
    """Everything the document page shows, except the signed PDF URL."""
    document = conn.execute(
        text(
            """
            SELECT id, status, skip_reason, failure_reason, duplicate_of_document_id, filename,
                   sender, subject, received_at, extraction_path, attempts, last_error,
                   storage_path, created_at, updated_at
            FROM documents WHERE id = :id
            """
        ),
        {"id": document_id},
    ).one_or_none()
    if document is None:
        return None
    extraction = conn.execute(
        text(
            """
            SELECT id, provider, model, prompt_version, raw_output, normalized, latency_ms,
                   input_tokens, output_tokens, created_at
            FROM extractions WHERE document_id = :id
            ORDER BY created_at DESC, id LIMIT 1
            """
        ),
        {"id": document_id},
    ).one_or_none()
    checks = (
        rows(
            conn.execute(
                text(
                    """
                    SELECT check_id, severity, status, field, message FROM check_results
                    WHERE extraction_id = :id ORDER BY check_id, field
                    """
                ),
                {"id": extraction.id},
            )
        )
        if extraction
        else []
    )
    events = rows(
        conn.execute(
            text(
                """
                SELECT event_type, actor, payload, created_at FROM document_events
                WHERE document_id = :id ORDER BY created_at, id
                """
            ),
            {"id": document_id},
        )
    )
    invoice = conn.execute(
        text(
            """
            SELECT i.*, v.canonical_name AS vendor, v.tax_id AS vendor_tax_id FROM invoices i
            JOIN vendors v ON v.id = i.vendor_id WHERE i.document_id = :id
            """
        ),
        {"id": document_id},
    ).one_or_none()
    line_items = (
        rows(
            conn.execute(
                text(
                    """
                    SELECT position, description, quantity, unit_price, amount
                    FROM invoice_line_items WHERE invoice_id = :id ORDER BY position
                    """
                ),
                {"id": invoice.id},
            )
        )
        if invoice
        else []
    )
    invoice_values = None
    if invoice:
        invoice_values = {
            key: money_text(value) if key in AMOUNT_COLUMNS else value
            for key, value in invoice._mapping.items()
        }
        invoice_values["line_items"] = [
            {
                **item,
                "quantity": plain_number(item["quantity"]),
                "unit_price": money_text(item["unit_price"]),
                "amount": money_text(item["amount"]),
            }
            for item in line_items
        ]
    return {
        "document": dict(document._mapping),
        "extraction": dict(extraction._mapping) if extraction else None,
        "raw_values": raw_values(extraction.raw_output) if extraction else None,
        "checks": checks,
        "events": events,
        "invoice": invoice_values,
    }


def plain_number(value: Decimal | None) -> str | None:
    """Quantity without storage padding: 4.0000 -> 4, 7.5000 -> 7.5."""
    return None if value is None else format(value.normalize(), "f")


def raw_values(raw_output: dict[str, Any] | None) -> dict[str, Any] | None:
    """Values exactly as the model read them from the document, if its answer was valid."""
    completions = (raw_output or {}).get("completions") or []
    if not completions:
        return None
    try:
        return Extraction.model_validate_json(completions[-1]["content"]).model_dump()
    except ValidationError:
        return None


def list_vendors(conn: Connection) -> list[dict[str, Any]]:
    """Vendor registry for the reviewer's vendor picker."""
    return rows(
        conn.execute(
            text(
                """
                SELECT id, canonical_name, normalized_name, tax_id, date_format
                FROM vendors ORDER BY canonical_name
                """
            )
        )
    )


def ratio(hits: int, total: int) -> dict[str, int]:
    return {"hits": hits, "total": total}


def stats(conn: Connection, outbox_max_attempts: int) -> dict[str, Any]:
    """Data for the Stats screen."""
    by_status = dict(
        conn.execute(text("SELECT status, count(*) FROM documents GROUP BY status")).all()
    )
    routed = dict(
        conn.execute(
            text(
                """
                SELECT payload->>'to', count(*) FROM document_events
                WHERE event_type = 'status_changed' AND payload->>'from' = 'processing'
                  AND payload->>'to' IN ('needs_review', 'auto_approved')
                GROUP BY payload->>'to'
                """
            )
        ).all()
    )
    reviewed = conn.execute(
        text("SELECT count(*) FROM invoices WHERE approval_mode IN ('human', 'human_override')")
    ).scalar_one()
    edits = dict(
        conn.execute(
            text(
                """
                SELECT e.field, count(DISTINCT e.document_id) FROM review_edits e
                JOIN invoices i ON i.document_id = e.document_id
                WHERE i.approval_mode IN ('human', 'human_override')
                GROUP BY e.field
                """
            )
        ).all()
    )
    failures = dict(
        conn.execute(
            text(
                """
                SELECT failure_reason, count(*) FROM documents
                WHERE status = 'failed' GROUP BY failure_reason
                """
            )
        ).all()
    )
    outbox = conn.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE delivered_at IS NULL AND attempts < :max) AS pending,
                   count(*) FILTER (WHERE delivered_at IS NULL AND attempts >= :max)
                       AS undeliverable
            FROM outbox_events
            """
        ),
        {"max": outbox_max_attempts},
    ).one()
    review_total = routed.get("needs_review", 0) + routed.get("auto_approved", 0)
    return {
        "documents_by_status": by_status,
        "review_rate": ratio(routed.get("needs_review", 0), review_total),
        "edit_rate_by_field": {field: ratio(count, reviewed) for field, count in edits.items()},
        "failures_by_reason": failures,
        "outbox": {"pending": outbox.pending, "undeliverable": outbox.undeliverable},
    }


def export_csv(conn: Connection, date_from: date | None, date_to: date | None) -> str:
    """Approved invoices with fixed columns, filtered by invoice date."""
    result = conn.execute(
        text(
            """
            SELECT i.id AS invoice_id, i.document_id, v.canonical_name AS vendor,
                   v.tax_id AS vendor_tax_id, i.invoice_number, i.invoice_date, i.due_date,
                   i.currency, i.subtotal, i.discount, i.shipping, i.tax_total, i.total,
                   i.approval_mode, i.approved_by, i.approved_at, i.exported_at
            FROM invoices i JOIN vendors v ON v.id = i.vendor_id
            WHERE (CAST(:date_from AS date) IS NULL OR i.invoice_date >= :date_from)
              AND (CAST(:date_to AS date) IS NULL OR i.invoice_date <= :date_to)
            ORDER BY i.invoice_date, i.invoice_number
            """
        ),
        {"date_from": date_from, "date_to": date_to},
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in result:
        values = row._mapping
        writer.writerow(
            [
                money_text(values[c])
                if c in AMOUNT_COLUMNS
                else ("" if values[c] is None else values[c])
                for c in EXPORT_COLUMNS
            ]
        )
    return buffer.getvalue()
