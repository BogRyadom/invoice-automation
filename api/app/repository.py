import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.checks import CheckResult, DuplicateHit
from app.extraction.normalize import NormalizedInvoice
from app.extraction.provider import Completion
from app.vendors import VendorRecord

# Latest extraction per document; its normalized JSON carries the matched vendor and invoice.
LATEST_EXTRACTION = """
    LEFT JOIN LATERAL (
        SELECT e.id, e.normalized FROM extractions e
        WHERE e.document_id = d.id
        ORDER BY e.created_at DESC, e.id
        LIMIT 1
    ) x ON true
"""


def as_json(value: Any) -> str:
    """JSON text for a jsonb parameter; UUIDs, dates and Decimals become strings."""
    return json.dumps(value, default=str)


def load_vendors(conn: Connection) -> list[VendorRecord]:
    """All vendors with their aliases."""
    rows = conn.execute(
        text(
            """
            SELECT v.id, v.canonical_name, v.normalized_name, v.tax_id, v.date_format,
                   COALESCE(array_agg(a.normalized_alias) FILTER (WHERE a.id IS NOT NULL), '{}')
                       AS aliases
            FROM vendors v LEFT JOIN vendor_aliases a ON a.vendor_id = v.id
            GROUP BY v.id
            """
        )
    )
    return [
        VendorRecord(
            id=row.id,
            canonical_name=row.canonical_name,
            normalized_name=row.normalized_name,
            tax_id=row.tax_id,
            date_format=row.date_format,
            aliases=tuple(row.aliases),
        )
        for row in rows
    ]


def find_earlier_file(conn: Connection, document_id: UUID, sha256: str) -> UUID | None:
    """Earliest other document with the same file content."""
    return conn.execute(
        text(
            """
            SELECT id FROM documents
            WHERE sha256 = :sha256 AND id <> :id
              AND created_at <= (SELECT created_at FROM documents WHERE id = :id)
            ORDER BY created_at, id
            LIMIT 1
            """
        ),
        {"sha256": sha256, "id": document_id},
    ).scalar_one_or_none()


def find_duplicate_invoice(
    conn: Connection, document_id: UUID, vendor_id: UUID, number: str
) -> DuplicateHit | None:
    """H5: an approved invoice, or a document still in review, with this vendor and number."""
    row = conn.execute(
        text(
            """
            SELECT i.document_id, i.id AS invoice_id, i.invoice_number, d.status
            FROM invoices i JOIN documents d ON d.id = i.document_id
            WHERE i.vendor_id = :vendor_id AND i.invoice_number_normalized = :number
              AND i.document_id <> :id
            LIMIT 1
            """
        ),
        {"vendor_id": vendor_id, "number": number, "id": document_id},
    ).one_or_none()
    if row is None:
        row = conn.execute(
            text(
                f"""
                SELECT d.id AS document_id, NULL::uuid AS invoice_id,
                       x.normalized->'invoice'->>'invoice_number' AS invoice_number, d.status
                FROM documents d {LATEST_EXTRACTION}
                WHERE d.status = 'needs_review' AND d.id <> :id
                  AND x.normalized->'vendor'->>'vendor_id' = :vendor_id
                  AND x.normalized->'invoice'->>'invoice_number_normalized' = :number
                ORDER BY d.received_at, d.id
                LIMIT 1
                """
            ),
            {"vendor_id": str(vendor_id), "number": number, "id": document_id},
        ).one_or_none()
    return DuplicateHit(**row._mapping) if row else None


def find_possible_duplicate(
    conn: Connection, document_id: UUID, number: str, total: Decimal, invoice_date: date
) -> DuplicateHit | None:
    """W8: same number, total and date as an existing invoice, whoever the vendor is."""
    row = conn.execute(
        text(
            """
            SELECT i.document_id, i.id AS invoice_id, i.invoice_number, d.status
            FROM invoices i JOIN documents d ON d.id = i.document_id
            WHERE i.invoice_number_normalized = :number AND i.total = :total
              AND i.invoice_date = :invoice_date AND i.document_id <> :id
            LIMIT 1
            """
        ),
        {"number": number, "total": total, "invoice_date": invoice_date, "id": document_id},
    ).one_or_none()
    if row is None:
        row = conn.execute(
            text(
                f"""
                SELECT d.id AS document_id, NULL::uuid AS invoice_id,
                       x.normalized->'invoice'->>'invoice_number' AS invoice_number, d.status
                FROM documents d {LATEST_EXTRACTION}
                WHERE d.status = 'needs_review' AND d.id <> :id
                  AND x.normalized->'invoice'->>'invoice_number_normalized' = :number
                  AND (x.normalized->'invoice'->>'total')::numeric = :total
                  AND x.normalized->'invoice'->>'invoice_date' = :invoice_date
                ORDER BY d.received_at, d.id
                LIMIT 1
                """
            ),
            {
                "number": number,
                "total": total,
                "invoice_date": invoice_date.isoformat(),
                "id": document_id,
            },
        ).one_or_none()
    return DuplicateHit(**row._mapping) if row else None


def insert_extraction(
    conn: Connection,
    document_id: UUID,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    completions: Sequence[Completion],
    normalized: dict[str, Any] | None,
) -> UUID:
    """Store every model answer for a document plus the normalized result."""

    def total(values: list[int | None]) -> int | None:
        known = [value for value in values if value is not None]
        return sum(known) if known else None

    raw_output = {
        "completions": [
            {
                "content": c.content,
                "model": c.model,
                "finish_reason": c.finish_reason,
                "latency_ms": c.latency_ms,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
            }
            for c in completions
        ]
    }
    return conn.execute(
        text(
            """
            INSERT INTO extractions (document_id, provider, model, prompt_version, raw_output,
                                     normalized, latency_ms, input_tokens, output_tokens)
            VALUES (:document_id, :provider, :model, :prompt_version, CAST(:raw_output AS jsonb),
                    CAST(:normalized AS jsonb), :latency_ms, :input_tokens, :output_tokens)
            RETURNING id
            """
        ),
        {
            "document_id": document_id,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "raw_output": as_json(raw_output),
            "normalized": as_json(normalized) if normalized is not None else None,
            "latency_ms": total([c.latency_ms for c in completions]),
            "input_tokens": total([c.input_tokens for c in completions]),
            "output_tokens": total([c.output_tokens for c in completions]),
        },
    ).scalar_one()


def insert_check_results(
    conn: Connection, extraction_id: UUID, results: Sequence[CheckResult]
) -> None:
    """Persist the outcome of every check."""
    conn.execute(
        text(
            """
            INSERT INTO check_results (extraction_id, check_id, severity, status, field, message)
            VALUES (:extraction_id, :check_id, :severity, :status, :field, :message)
            """
        ),
        [
            {
                "extraction_id": extraction_id,
                "check_id": r.check_id,
                "severity": r.severity,
                "status": r.status,
                "field": r.field,
                "message": r.message,
            }
            for r in results
        ],
    )


def insert_invoice(
    conn: Connection,
    document_id: UUID,
    vendor_id: UUID,
    invoice: NormalizedInvoice,
    *,
    approval_mode: str,
    approved_by: str | None,
) -> UUID:
    """Create the approved invoice and its line items."""
    invoice_id = conn.execute(
        text(
            """
            INSERT INTO invoices (document_id, vendor_id, invoice_number, invoice_number_normalized,
                                  invoice_date, due_date, currency, subtotal, discount, shipping,
                                  tax_total, total, approval_mode, approved_by)
            VALUES (:document_id, :vendor_id, :invoice_number, :invoice_number_normalized,
                    :invoice_date, :due_date, :currency, :subtotal, :discount, :shipping,
                    :tax_total, :total, :approval_mode, :approved_by)
            RETURNING id
            """
        ),
        {
            "document_id": document_id,
            "vendor_id": vendor_id,
            "invoice_number": invoice.invoice_number,
            "invoice_number_normalized": invoice.invoice_number_normalized,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "currency": invoice.currency,
            "subtotal": invoice.subtotal,
            "discount": invoice.discount,
            "shipping": invoice.shipping,
            "tax_total": invoice.tax_total,
            "total": invoice.total,
            "approval_mode": approval_mode,
            "approved_by": approved_by,
        },
    ).scalar_one()
    if invoice.line_items:
        conn.execute(
            text(
                """
                INSERT INTO invoice_line_items (invoice_id, position, description, quantity,
                                                unit_price, amount)
                VALUES (:invoice_id, :position, :description, :quantity, :unit_price, :amount)
                """
            ),
            [
                {
                    "invoice_id": invoice_id,
                    "position": position,
                    "description": item.description,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "amount": item.amount,
                }
                for position, item in enumerate(invoice.line_items, start=1)
            ],
        )
    return invoice_id


def add_outbox_event(
    conn: Connection, document_id: UUID, event_type: str, payload: dict[str, Any]
) -> None:
    """Queue a notification for n8n; delivery happens outside this transaction."""
    conn.execute(
        text(
            """
            INSERT INTO outbox_events (document_id, event_type, payload)
            VALUES (:document_id, :event_type, CAST(:payload AS jsonb))
            """
        ),
        {"document_id": document_id, "event_type": event_type, "payload": as_json(payload)},
    )


def set_processing_details(
    conn: Connection,
    document_id: UUID,
    *,
    extraction_path: str | None = None,
    last_error: str | None = None,
) -> None:
    """Record how a document was read and the last error, if any."""
    conn.execute(
        text(
            """
            UPDATE documents
            SET extraction_path = COALESCE(:extraction_path, extraction_path),
                last_error = :last_error
            WHERE id = :id
            """
        ),
        {"id": document_id, "extraction_path": extraction_path, "last_error": last_error},
    )
