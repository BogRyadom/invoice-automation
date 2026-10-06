# Deterministic checks and routing (docs/SPEC.md section 7). Every check always produces at
# least one result so the review UI can show its state next to the field.

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.extraction.contract import Extraction
from app.extraction.normalize import NormalizedInvoice
from app.extraction.provider import ExtractionPath
from app.vendors import VendorMatch

Severity = Literal["hard", "warning"]
CheckStatus = Literal["pass", "fail", "not_run"]
Route = Literal["auto_approved", "needs_review"]

REQUIRED_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "total", "currency")
FUTURE_TOLERANCE = timedelta(days=1)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    severity: Severity
    status: CheckStatus
    field: str | None
    message: str


@dataclass(frozen=True)
class DuplicateHit:
    document_id: UUID
    invoice_id: UUID | None
    invoice_number: str
    status: str


@dataclass(frozen=True)
class CheckContext:
    extraction: Extraction
    invoice: NormalizedInvoice
    path: ExtractionPath
    text: str | None
    received_on: date
    vendor: VendorMatch
    duplicate: DuplicateHit | None
    possible_duplicate: DuplicateHit | None
    amount_tolerance: Decimal
    auto_approve_max_total: Decimal
    hidden_chars: int = 0


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _result(
    check_id: str, status: CheckStatus, message: str, field: str | None = None
) -> CheckResult:
    severity: Severity = "hard" if check_id.startswith("H") else "warning"
    return CheckResult(check_id, severity, status, field, message)


def h1_required_fields(ctx: CheckContext) -> list[CheckResult]:
    """Required fields are present and every extracted value normalized."""
    invoice = ctx.invoice
    values = {
        "vendor_name": invoice.vendor_key,
        "invoice_number": invoice.invoice_number_normalized,
        "invoice_date": invoice.invoice_date,
        "total": invoice.total,
        "currency": invoice.currency,
    }
    failures = [
        _result("H1", "fail", f"{name} is missing", name)
        for name, value in values.items()
        if not value
    ]
    failures += [
        _result("H1", "fail", f"cannot normalize: {issue.message}", issue.field)
        for issue in invoice.issues
        if issue.code == "unparseable"
    ]
    return failures or [_result("H1", "pass", "required fields present and normalized")]


def missing_for_totals(invoice: NormalizedInvoice) -> list[str]:
    """Values without which the total cannot be verified (W7)."""
    missing = [name for name in ("subtotal", "total") if getattr(invoice, name) is None]
    if not invoice.tax_lines:
        missing.append("tax")
    return missing


def h2_total(ctx: CheckContext) -> list[CheckResult]:
    """subtotal - discount + shipping + taxes = total; taxes are not added when included."""
    invoice = ctx.invoice
    if missing_for_totals(invoice) or invoice.tax_total is None:
        return [_result("H2", "not_run", "cannot verify: see W7", "total")]
    assert invoice.subtotal is not None and invoice.total is not None
    taxes = Decimal(0) if invoice.tax_inclusive else invoice.tax_total
    expected = invoice.subtotal - (invoice.discount or 0) + (invoice.shipping or 0) + taxes
    difference = abs(expected - invoice.total)
    if difference > ctx.amount_tolerance:
        message = f"components add up to {expected}, document total is {invoice.total}"
        return [_result("H2", "fail", message, "total")]
    return [_result("H2", "pass", "total matches its components")]


def h3_line_items(ctx: CheckContext) -> list[CheckResult]:
    """Line item amounts add up to the subtotal, when there are line items."""
    invoice = ctx.invoice
    if not invoice.line_items:
        return [_result("H3", "pass", "no line items printed", "line_items")]
    if invoice.subtotal is None:
        return [_result("H3", "not_run", "cannot verify: no subtotal, see W7", "line_items")]
    amounts = [item.amount for item in invoice.line_items]
    if None in amounts:
        return [_result("H3", "fail", "a line item has no amount", "line_items")]
    total = sum((amount for amount in amounts if amount is not None), Decimal(0))
    if abs(total - invoice.subtotal) > ctx.amount_tolerance:
        message = f"line items add up to {total}, subtotal is {invoice.subtotal}"
        return [_result("H3", "fail", message, "subtotal")]
    return [_result("H3", "pass", "line items match the subtotal", "line_items")]


def h4_grounding(ctx: CheckContext) -> list[CheckResult]:
    """Key raw values appear verbatim in the extracted text (text path only)."""
    if ctx.path != "text" or ctx.text is None:
        return [_result("H4", "not_run", "no text layer: see W5")]
    text = _collapse(ctx.text)
    raw = ctx.extraction
    values = {
        "invoice_number": raw.invoice_number_raw,
        "total": raw.total_raw,
        "invoice_date": raw.invoice_date_raw,
        "tax_inclusive_note": raw.tax_inclusive_note_raw,
    }
    failures = [
        _result("H4", "fail", f"{value!r} does not appear in the document text", name)
        for name, value in values.items()
        if value and _collapse(value) not in text
    ]
    return failures or [_result("H4", "pass", "key values found in the document text")]


def h5_duplicate(ctx: CheckContext) -> list[CheckResult]:
    """Same vendor and invoice number as an approved or pending invoice."""
    hit = ctx.duplicate
    if hit is None:
        return [_result("H5", "pass", "no invoice with this vendor and number", "invoice_number")]
    message = f"duplicate of document {hit.document_id} ({hit.status})"
    return [_result("H5", "fail", message, "invoice_number")]


def h6_dates(ctx: CheckContext) -> list[CheckResult]:
    """Invoice date not in the future (one day tolerance), due date not before it."""
    invoice = ctx.invoice
    if invoice.invoice_date is None:
        return [_result("H6", "not_run", "no invoice date", "invoice_date")]
    failures = []
    if invoice.invoice_date > ctx.received_on + FUTURE_TOLERANCE:
        failures.append(
            _result(
                "H6", "fail", f"invoice date is after receipt ({ctx.received_on})", "invoice_date"
            )
        )
    if invoice.due_date is not None and invoice.due_date < invoice.invoice_date:
        failures.append(_result("H6", "fail", "due date is before invoice date", "due_date"))
    return failures or [_result("H6", "pass", "dates are plausible")]


def from_issues(check_id: str, code: str, ctx: CheckContext, clean: str) -> list[CheckResult]:
    """Warnings raised by normalization issues of one kind."""
    found = [
        _result(check_id, "fail", issue.message, issue.field)
        for issue in ctx.invoice.issues
        if issue.code == code
    ]
    return found or [_result(check_id, "pass", clean)]


def w4_vendor(ctx: CheckContext) -> list[CheckResult]:
    """Vendor not identified exactly (new vendor or fuzzy candidates only)."""
    match = ctx.vendor
    if match.is_exact:
        return [_result("W4", "pass", f"known vendor (matched by {match.method})", "vendor_name")]
    if match.candidates:
        names = ", ".join(f"{c.canonical_name} ({c.score})" for c in match.candidates)
        return [_result("W4", "fail", f"vendor not found, similar: {names}", "vendor_name")]
    return [_result("W4", "fail", "new vendor", "vendor_name")]


def w5_vision(ctx: CheckContext) -> list[CheckResult]:
    """Scans skip grounding and always need a human."""
    if ctx.path == "vision":
        return [_result("W5", "fail", "read from page images, grounding was not run")]
    return [_result("W5", "pass", "read from the text layer")]


def w6_large_total(ctx: CheckContext) -> list[CheckResult]:
    """Total above the auto-approve limit."""
    total = ctx.invoice.total
    if total is not None and total > ctx.auto_approve_max_total:
        message = f"total {total} is above the limit {ctx.auto_approve_max_total}"
        return [_result("W6", "fail", message, "total")]
    return [_result("W6", "pass", "total within the auto-approve limit", "total")]


def w7_unverifiable(ctx: CheckContext) -> list[CheckResult]:
    """H2 or H3 could not run because subtotal, total or tax is not printed."""
    missing = missing_for_totals(ctx.invoice)
    if missing:
        message = f"cannot verify the total, not printed: {', '.join(missing)}"
        return [_result("W7", "fail", message, "subtotal")]
    return [_result("W7", "pass", "totals can be verified")]


def w8_possible_duplicate(ctx: CheckContext) -> list[CheckResult]:
    """Unknown vendor, but number, total and date match an existing invoice."""
    hit = ctx.possible_duplicate
    if hit is None:
        return [_result("W8", "pass", "no similar invoice", "invoice_number")]
    message = f"possible duplicate of document {hit.document_id} ({hit.status})"
    return [_result("W8", "fail", message, "invoice_number")]


def w9_hidden_text(ctx: CheckContext) -> list[CheckResult]:
    """Invisible text was removed before extraction; a possible prompt injection."""
    if ctx.hidden_chars:
        message = f"{ctx.hidden_chars} invisible characters were removed before extraction"
        return [_result("W9", "fail", message)]
    return [_result("W9", "pass", "no invisible text")]


def run_checks(ctx: CheckContext) -> list[CheckResult]:
    """All hard checks and warnings in SPEC order."""
    return [
        *h1_required_fields(ctx),
        *h2_total(ctx),
        *h3_line_items(ctx),
        *h4_grounding(ctx),
        *h5_duplicate(ctx),
        *h6_dates(ctx),
        *from_issues("W1", "ambiguous_date", ctx, "dates are unambiguous"),
        *from_issues("W2", "ambiguous_number_format", ctx, "number format is clear"),
        *from_issues("W3", "currency_symbol_only", ctx, "currency code printed"),
        *w4_vendor(ctx),
        *w5_vision(ctx),
        *w6_large_total(ctx),
        *w7_unverifiable(ctx),
        *w8_possible_duplicate(ctx),
        *w9_hidden_text(ctx),
    ]


def failed_checks(results: Sequence[CheckResult]) -> set[str]:
    """Ids of checks that failed (hard) or were raised (warnings)."""
    return {result.check_id for result in results if result.status == "fail"}


def route(results: Sequence[CheckResult], auto_approve_enabled: bool) -> Route:
    """auto_approved only when enabled, every hard check passed and no warning was raised."""
    hard_ok = all(r.status == "pass" for r in results if r.severity == "hard")
    warned = any(r.status == "fail" for r in results if r.severity == "warning")
    return "auto_approved" if auto_approve_enabled and hard_ok and not warned else "needs_review"
