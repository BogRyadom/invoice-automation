# Worker steps 1-7 for one claimed document (docs/SPEC.md section 4). The LLM call runs
# outside any database transaction; results are written in one transaction afterwards.

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from app import repository as repo
from app.checks import CheckContext, CheckResult, failed_checks, route, run_checks
from app.config import Settings
from app.extraction.normalize import normalize_extraction
from app.extraction.pipeline import ExtractionError, ExtractionResult, run_extraction
from app.extraction.prompt import PROMPT_VERSION
from app.extraction.provider import ExtractionProvider
from app.status import Status, change_status
from app.storage import DocumentStore
from app.vendors import VendorMatch, match_vendor

ACTOR = "worker"
MAX_ERROR_CHARS = 1000


@dataclass(frozen=True)
class ClaimedDocument:
    id: UUID
    sha256: str
    storage_path: str
    received_at: datetime


# Raised after the document was marked failed: the provider will refuse further requests too.
class ProviderFatal(Exception):
    pass


def vendor_payload(match: VendorMatch) -> dict[str, Any]:
    """Vendor match as stored with the extraction and shown to the reviewer."""
    return {
        "vendor_id": match.vendor.id if match.vendor else None,
        "canonical_name": match.vendor.canonical_name if match.vendor else None,
        "method": match.method,
        "candidates": [
            {"vendor_id": c.vendor_id, "canonical_name": c.canonical_name, "score": c.score}
            for c in match.candidates
        ],
    }


def finish(
    engine: Engine,
    doc: ClaimedDocument,
    target: Status,
    reason: str | None,
    *,
    error: str | None = None,
    details: dict[str, Any] | None = None,
    duplicate_of: UUID | None = None,
) -> Status:
    """Close processing without an invoice: skipped or failed, with a notification."""
    with engine.begin() as conn:
        change_status(
            conn,
            doc.id,
            target,
            actor=ACTOR,
            reason=reason,
            details=details,
            duplicate_of=duplicate_of,
        )
        repo.set_processing_details(conn, doc.id, last_error=error)
        repo.add_outbox_event(conn, doc.id, target, {"document_id": doc.id, "reason": reason})
    return target


def process_document(
    engine: Engine,
    doc: ClaimedDocument,
    *,
    store: DocumentStore,
    provider: ExtractionProvider,
    settings: Settings,
) -> Status:
    """Process one claimed document and return its new status."""
    with engine.connect() as conn:
        original = repo.find_earlier_file(conn, doc.id, doc.sha256)
    if original is not None:
        return finish(engine, doc, "skipped", "duplicate_file", duplicate_of=original)

    data = store.read(doc.storage_path)
    try:
        result = run_extraction(data, provider, settings)
    except ExtractionError as exc:
        return handle_extraction_error(engine, doc, exc, provider, settings)

    document_type = result.extraction.document_type
    if document_type != "invoice":
        reason = "not_invoice" if document_type == "other" else "unsupported_type"
        with engine.begin() as conn:
            repo.insert_extraction(
                conn,
                doc.id,
                provider=result.provider,
                model=result.model,
                prompt_version=result.prompt_version,
                completions=result.completions,
                normalized={"document_type": document_type},
            )
            repo.set_processing_details(conn, doc.id, extraction_path=result.path)
        return finish(engine, doc, "skipped", reason, details={"document_type": document_type})

    return route_invoice(engine, doc, result, settings)


def handle_extraction_error(
    engine: Engine,
    doc: ClaimedDocument,
    exc: ExtractionError,
    provider: ExtractionProvider,
    settings: Settings,
) -> Status:
    """Record a file or LLM failure; raise ProviderFatal when the provider is unusable."""
    if exc.completions:
        with engine.begin() as conn:
            repo.insert_extraction(
                conn,
                doc.id,
                provider=provider.name,
                model=exc.completions[-1].model,
                prompt_version=PROMPT_VERSION,
                completions=exc.completions,
                normalized=None,
            )
    target: Status = "skipped" if exc.reason == "unsupported_type" else "failed"
    status = finish(engine, doc, target, exc.reason, error=str(exc)[:MAX_ERROR_CHARS])
    if exc.fatal:
        raise ProviderFatal(str(exc))
    return status


def route_invoice(
    engine: Engine, doc: ClaimedDocument, result: ExtractionResult, settings: Settings
) -> Status:
    """Vendor match, normalization, duplicate lookup, checks and routing in one transaction."""
    extraction = result.extraction
    with engine.begin() as conn:
        match = match_vendor(
            extraction.vendor_name_raw,
            extraction.vendor_tax_id_raw,
            repo.load_vendors(conn),
            settings.vendor_fuzzy_threshold,
        )
        invoice = normalize_extraction(
            extraction, vendor_date_format=match.vendor.date_format if match.vendor else None
        )
        number = invoice.invoice_number_normalized
        duplicate = (
            repo.find_duplicate_invoice(conn, doc.id, match.vendor.id, number)
            if match.vendor and number
            else None
        )
        possible_duplicate = (
            repo.find_possible_duplicate(conn, doc.id, number, invoice.total, invoice.invoice_date)
            if not match.vendor and number and invoice.total is not None and invoice.invoice_date
            else None
        )
        results = run_checks(
            CheckContext(
                extraction=extraction,
                invoice=invoice,
                path=result.path,
                text=result.text,
                received_on=doc.received_at.date(),
                vendor=match,
                duplicate=duplicate,
                possible_duplicate=possible_duplicate,
                amount_tolerance=settings.amount_tolerance,
                auto_approve_max_total=settings.auto_approve_max_total,
            )
        )
        target: Status = route(results, settings.auto_approve_enabled)
        extraction_id = repo.insert_extraction(
            conn,
            doc.id,
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version,
            completions=result.completions,
            normalized={
                "document_type": "invoice",
                "invoice": invoice.model_dump(mode="json"),
                "vendor": vendor_payload(match),
                "duplicate": asdict(duplicate) if duplicate else None,
                "possible_duplicate": asdict(possible_duplicate) if possible_duplicate else None,
            },
        )
        if target == "auto_approved":
            assert match.vendor is not None
            try:
                with conn.begin_nested():
                    repo.insert_invoice(
                        conn,
                        doc.id,
                        match.vendor.id,
                        invoice,
                        approval_mode="auto",
                        approved_by=None,
                    )
            except IntegrityError:
                # Another document with this vendor and number was approved meanwhile.
                race = CheckResult(
                    "H5", "hard", "fail", "invoice_number", "approved concurrently elsewhere"
                )
                results, target = [*results, race], "needs_review"
        repo.insert_check_results(conn, extraction_id, results)
        repo.set_processing_details(conn, doc.id, extraction_path=result.path)

        flags = sorted(failed_checks(results))
        change_status(conn, doc.id, target, actor=ACTOR, details={"flags": flags})
        repo.add_outbox_event(
            conn,
            doc.id,
            target,
            {
                "document_id": doc.id,
                "vendor": match.vendor.canonical_name if match.vendor else invoice.vendor_name,
                "invoice_number": invoice.invoice_number,
                "total": invoice.total,
                "currency": invoice.currency,
                "flags": flags,
            },
        )
    return target
