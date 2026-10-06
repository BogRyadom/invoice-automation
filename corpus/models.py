from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.extraction.contract import DocumentType, Extraction

Group = Literal[
    "clean",
    "complex",
    "european",
    "multipage",
    "scan",
    "duplicate",
    "not_invoice",
    "broken",
    "injection",
    "arithmetic_error",
]
Layout = Literal["classic", "band", "compact", "split", "ledger"]
RenderKind = Literal["invoice", "contract", "price_list", "receipt", "exact_copy", "corrupted"]
ExpectedStatus = Literal["auto_approved", "needs_review", "skipped", "failed"]


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmailMeta(Frozen):
    sender: str
    subject: str
    received_at: datetime


class ScanSpec(Frozen):
    seed: int
    rotation_degrees: float
    noise: float
    dpi: int


class RenderSpec(Frozen):
    kind: RenderKind
    layout: Layout | None = None
    language: Literal["en", "de"] = "en"
    vendor_address: tuple[str, ...] = ()
    bill_to: tuple[str, ...] = ()
    currency_display: Literal["code", "symbol"] = "code"
    notes: tuple[str, ...] = ()
    hidden_text: str | None = None
    title: str | None = None
    paragraphs: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()
    scan: ScanSpec | None = None
    encrypt_password: str | None = None
    source_doc_id: str | None = None


class NormalizedLineItem(Frozen):
    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class NormalizedInvoice(Frozen):
    vendor_key: str
    vendor_tax_id: str | None
    invoice_number: str
    invoice_date: date
    due_date: date | None
    currency: str
    subtotal: Decimal | None
    discount: Decimal | None
    shipping: Decimal | None
    tax_total: Decimal | None
    total: Decimal
    tax_inclusive: bool
    line_items: tuple[NormalizedLineItem, ...]


class ExpectedRoute(Frozen):
    status: ExpectedStatus
    reason: str | None = None
    flags: tuple[str, ...] = ()
    duplicate_of: str | None = None


class GroundTruth(Frozen):
    doc_id: str
    filename: str
    group: Group
    description: str
    email: EmailMeta
    extraction_path: Literal["text", "vision"] | None
    document_type: DocumentType | None
    printed: Extraction | None
    expected: NormalizedInvoice | None
    route: ExpectedRoute
    render: RenderSpec


class KnownVendor(Frozen):
    canonical_name: str
    normalized_name: str
    tax_id: str | None
