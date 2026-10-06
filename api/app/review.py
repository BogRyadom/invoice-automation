# Reviewer actions (docs/SPEC.md sections 5, 7 and 8). Approval re-runs the hard checks on the
# final values; a failing check can only be overridden with a comment.

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app import repository as repo
from app.auth import Reviewer
from app.checks import (
    CheckContext,
    CheckResult,
    h1_required_fields,
    h2_total,
    h3_line_items,
    h5_duplicate,
    h6_dates,
)
from app.config import Settings
from app.extraction.contract import Extraction
from app.extraction.normalize import (
    NormalizedInvoice,
    NormalizedLineItem,
    NormalizedTaxLine,
    compact_key,
    vendor_key,
)
from app.status import Status, change_status
from app.vendors import VendorMatch, VendorRecord

ApprovalMode = Literal["human", "human_override", "manual_entry"]
EDITABLE_FIELDS = (
    "vendor_name",
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
    "line_items",
)
EMPTY_EXTRACTION = Extraction(
    document_type="invoice",
    vendor_name_raw=None,
    vendor_tax_id_raw=None,
    invoice_number_raw=None,
    invoice_date_raw=None,
    due_date_raw=None,
    currency_raw=None,
    subtotal_raw=None,
    discount_raw=None,
    shipping_raw=None,
    tax_lines=[],
    tax_inclusive_note_raw=None,
    total_raw=None,
    line_items=[],
)


class LineItemValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class InvoiceValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_name: str = Field(min_length=1)
    vendor_tax_id: str | None = None
    invoice_number: str = Field(min_length=1)
    invoice_date: date
    due_date: date | None = None
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    subtotal: Decimal | None = None
    discount: Decimal | None = None
    shipping: Decimal | None = None
    tax_total: Decimal | None = None
    tax_inclusive: bool = False
    total: Decimal
    line_items: list[LineItemValues] = []


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: InvoiceValues
    vendor_id: UUID | None = None
    date_format: Literal["DMY", "MDY"] | None = None
    override: bool = False
    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class ReviewError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def lock_document(conn: Connection, document_id: UUID) -> Any:
    """Row of the document, locked for the rest of the transaction."""
    row = conn.execute(
        text("SELECT * FROM documents WHERE id = :id FOR UPDATE"), {"id": document_id}
    ).one_or_none()
    if row is None:
        raise ReviewError(404, "document not found")
    return row


def latest_normalized(conn: Connection, document_id: UUID) -> dict[str, Any]:
    """Normalized JSON of the latest extraction, or an empty dict."""
    value = conn.execute(
        text(
            """
            SELECT normalized FROM extractions WHERE document_id = :id
            ORDER BY created_at DESC, id LIMIT 1
            """
        ),
        {"id": document_id},
    ).scalar_one_or_none()
    return value or {}


def to_normalized(values: InvoiceValues) -> NormalizedInvoice:
    """Reviewer values in the shape the checks and the invoice writer use."""
    tax_lines = (
        (NormalizedTaxLine(label="tax", amount=values.tax_total),)
        if values.tax_total is not None
        else ()
    )
    return NormalizedInvoice(
        vendor_name=values.vendor_name,
        vendor_key=vendor_key(values.vendor_name),
        vendor_tax_id=compact_key(values.vendor_tax_id) if values.vendor_tax_id else None,
        invoice_number=values.invoice_number,
        invoice_number_normalized=compact_key(values.invoice_number),
        invoice_date=values.invoice_date,
        due_date=values.due_date,
        currency=values.currency,
        subtotal=values.subtotal,
        discount=values.discount,
        shipping=values.shipping,
        tax_lines=tax_lines,
        tax_total=values.tax_total,
        tax_inclusive=values.tax_inclusive,
        total=values.total,
        line_items=tuple(NormalizedLineItem(**item.model_dump()) for item in values.line_items),
        number_style=None,
        issues=(),
    )


def resolve_vendor(conn: Connection, request: ApproveRequest) -> VendorRecord:
    """Chosen vendor (saving a new spelling as alias) or a new vendor created on approval."""
    values = request.values
    key = vendor_key(values.vendor_name)
    vendors = {vendor.id: vendor for vendor in repo.load_vendors(conn)}
    if request.vendor_id is not None:
        vendor = vendors.get(request.vendor_id)
        if vendor is None:
            raise ReviewError(404, "vendor not found")
        if key and key != vendor.normalized_name and key not in vendor.aliases:
            try:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            """
                            INSERT INTO vendor_aliases (vendor_id, normalized_alias)
                            VALUES (:vendor_id, :alias)
                            """
                        ),
                        {"vendor_id": vendor.id, "alias": key},
                    )
            except IntegrityError as exc:
                raise ReviewError(
                    409, f"{values.vendor_name!r} is an alias of another vendor"
                ) from exc
        return vendor

    existing = next((v for v in vendors.values() if v.normalized_name == key), None)
    if existing is not None:
        raise ReviewError(409, {"message": "vendor already exists", "vendor_id": str(existing.id)})
    tax_id = compact_key(values.vendor_tax_id) if values.vendor_tax_id else None
    try:
        with conn.begin_nested():
            vendor_id = conn.execute(
                text(
                    """
                    INSERT INTO vendors (canonical_name, normalized_name, tax_id)
                    VALUES (:name, :key, :tax_id)
                    RETURNING id
                    """
                ),
                {"name": values.vendor_name, "key": key, "tax_id": tax_id},
            ).scalar_one()
    except IntegrityError as exc:
        raise ReviewError(409, "a vendor with this tax id already exists") from exc
    return VendorRecord(vendor_id, values.vendor_name, key, tax_id, None)


def approval_checks(
    conn: Connection,
    document_id: UUID,
    invoice: NormalizedInvoice,
    vendor: VendorRecord,
    received_on: date,
    settings: Settings,
) -> list[CheckResult]:
    """Hard checks on the final values. Grounding and warnings do not apply to human input."""
    number = invoice.invoice_number_normalized or ""
    ctx = CheckContext(
        extraction=EMPTY_EXTRACTION,
        invoice=invoice,
        path="text",
        text=None,
        received_on=received_on,
        vendor=VendorMatch(vendor, "name"),
        duplicate=repo.find_duplicate_invoice(
            conn, document_id, vendor.id, number, include_pending=False
        ),
        possible_duplicate=None,
        amount_tolerance=settings.amount_tolerance,
        auto_approve_max_total=settings.auto_approve_max_total,
    )
    return [
        *h1_required_fields(ctx),
        *h2_total(ctx),
        *h3_line_items(ctx),
        *h5_duplicate(ctx),
        *h6_dates(ctx),
    ]


AMOUNT_FIELDS = frozenset({"subtotal", "discount", "shipping", "tax_total", "total"})
LINE_ITEM_KEYS = ("description", "quantity", "unit_price", "amount")


def _comparable(name: str, value: Any) -> Any:
    """Field value in a form where formatting differences do not count as edits."""
    if value is None:
        return None
    if name in AMOUNT_FIELDS:
        return Decimal(str(value))
    if name == "vendor_tax_id":
        return compact_key(str(value))
    if name == "line_items":
        return [
            tuple(
                item.get(key)
                if key == "description" or item.get(key) is None
                else Decimal(str(item[key]))
                for key in LINE_ITEM_KEYS
            )
            for item in value
        ]
    return str(value)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, default=str)


def edits(
    extracted: dict[str, Any], values: InvoiceValues
) -> list[tuple[str, str | None, str | None]]:
    """Fields whose final value differs from what was extracted."""
    final = values.model_dump(mode="json")
    return [
        (name, _as_text(extracted.get(name)), _as_text(final.get(name)))
        for name in EDITABLE_FIELDS
        if _comparable(name, extracted.get(name)) != _comparable(name, final.get(name))
    ]


def approve(
    conn: Connection,
    document_id: UUID,
    request: ApproveRequest,
    reviewer: Reviewer,
    settings: Settings,
) -> dict[str, Any]:
    """Approve a reviewed document, or enter a failed one manually."""
    doc = lock_document(conn, document_id)
    if doc.status not in ("needs_review", "failed"):
        raise ReviewError(409, f"cannot approve a document in status {doc.status}")

    vendor = resolve_vendor(conn, request)
    if request.date_format:
        conn.execute(
            text("UPDATE vendors SET date_format = :format WHERE id = :id"),
            {"format": request.date_format, "id": vendor.id},
        )

    invoice = to_normalized(request.values)
    results = approval_checks(conn, document_id, invoice, vendor, doc.received_at.date(), settings)
    failed = sorted({r.check_id for r in results if r.status == "fail"})
    if failed and not request.override:
        raise ReviewError(
            422,
            {
                "message": "hard checks failed; approve with override and a comment",
                "checks": [asdict(r) for r in results if r.status == "fail"],
            },
        )
    if failed and not request.comment:
        raise ReviewError(422, "an override needs a comment")

    mode: ApprovalMode = (
        "manual_entry" if doc.status == "failed" else "human_override" if failed else "human"
    )
    try:
        with conn.begin_nested():
            invoice_id = repo.insert_invoice(
                conn,
                document_id,
                vendor.id,
                invoice,
                approval_mode=mode,
                approved_by=reviewer.name,
            )
    except IntegrityError as exc:
        hit = repo.find_duplicate_invoice(
            conn,
            document_id,
            vendor.id,
            invoice.invoice_number_normalized or "",
            include_pending=False,
        )
        raise ReviewError(
            409,
            {
                "message": "this vendor already has an invoice with this number",
                "document_id": str(hit.document_id) if hit else None,
            },
        ) from exc

    extracted = latest_normalized(conn, document_id).get("invoice") or {}
    changed = edits(extracted, request.values)
    if changed:
        conn.execute(
            text(
                """
                INSERT INTO review_edits (document_id, field, extracted_value, final_value,
                                          edited_by)
                VALUES (:document_id, :field, :extracted, :final, :by)
                """
            ),
            [
                {
                    "document_id": document_id,
                    "field": field,
                    "extracted": old,
                    "final": new,
                    "by": reviewer.name,
                }
                for field, old, new in changed
            ],
        )

    change_status(
        conn,
        document_id,
        "approved",
        actor=reviewer.name,
        details={
            "approval_mode": mode,
            "failed_checks": failed,
            "comment": request.comment,
            "edited_fields": [field for field, _, _ in changed],
        },
    )
    repo.add_outbox_event(
        conn, document_id, "approved", repo.invoice_event_payload(conn, invoice_id)
    )
    return {
        "document_id": document_id,
        "status": "approved",
        "invoice_id": invoice_id,
        "approval_mode": mode,
    }


def reject(conn: Connection, document_id: UUID, reason: str, reviewer: Reviewer) -> Status:
    """Reject a document under review; the reason is mandatory."""
    if not reason.strip():
        raise ReviewError(422, "a reason is required")
    doc = lock_document(conn, document_id)
    if doc.status != "needs_review":
        raise ReviewError(409, f"cannot reject a document in status {doc.status}")
    change_status(
        conn, document_id, "rejected", actor=reviewer.name, details={"reason": reason.strip()}
    )
    return "rejected"


def reprocess(conn: Connection, document_id: UUID, reviewer: Reviewer) -> Status:
    """Send a failed document back to the worker with a fresh attempt budget."""
    doc = lock_document(conn, document_id)
    if doc.status != "failed":
        raise ReviewError(409, f"only failed documents can be reprocessed, not {doc.status}")
    change_status(conn, document_id, "processing", actor=reviewer.name)
    conn.execute(
        text(
            """
            UPDATE documents SET attempts = 0, locked_at = NULL, next_attempt_at = NULL,
                                 last_error = NULL
            WHERE id = :id
            """
        ),
        {"id": document_id},
    )
    return "processing"
