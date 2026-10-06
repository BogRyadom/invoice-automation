import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any, Protocol, get_args
from uuid import UUID

from sqlalchemy import Engine, text

from app.config import Settings
from app.extraction.normalize import NormalizedInvoice
from app.extraction.provider import ExtractionProvider
from app.processing import ProviderFatal
from app.repository import LATEST_EXTRACTION
from app.storage import LocalDocumentStore
from app.worker import run_once
from corpus.models import ExpectedStatus, GroundTruth
from corpus.storage import DOCUMENTS_DIR, document_path, load_known_vendors
from eval.database import seed_vendors, temporary_database
from eval.metrics import PredictedInvoice, PredictedLineItem, Prediction

FINAL_STATUSES = frozenset(get_args(ExpectedStatus))


class Predictor(Protocol):
    provider: str
    model: str

    def predict(self, docs: Sequence[GroundTruth]) -> list[Prediction]:
        """Process the corpus in order and return one prediction per document."""
        ...


# The provider cannot answer any further request (credentials, daily quota): stop the run.
class EvalAborted(Exception):
    pass


# Answers from ground truth. Checks the eval harness; its numbers are never published.
class OraclePredictor:
    provider = "oracle"
    model = "ground-truth"

    def predict(self, docs: Sequence[GroundTruth]) -> list[Prediction]:
        """Return the expected values for every document."""
        return [
            Prediction(
                doc_id=doc.doc_id,
                document_type=doc.document_type,
                invoice=PredictedInvoice.model_validate(doc.expected.model_dump())
                if doc.expected
                else None,
                status=doc.route.status,
                flags=doc.route.flags,
            )
            for doc in docs
        ]


def predicted_invoice(normalized: NormalizedInvoice) -> PredictedInvoice:
    """Map pipeline output onto the fields eval compares."""
    return PredictedInvoice(
        vendor_key=normalized.vendor_key,
        vendor_tax_id=normalized.vendor_tax_id,
        invoice_number=normalized.invoice_number_normalized,
        invoice_date=normalized.invoice_date,
        due_date=normalized.due_date,
        currency=normalized.currency,
        subtotal=normalized.subtotal,
        discount=normalized.discount,
        shipping=normalized.shipping,
        tax_total=normalized.tax_total,
        total=normalized.total,
        tax_inclusive=normalized.tax_inclusive,
        line_items=tuple(
            PredictedLineItem.model_validate(item.model_dump()) for item in normalized.line_items
        ),
    )


def insert_document(engine: Engine, doc: GroundTruth) -> UUID:
    """Register a corpus file as if n8n had delivered it from Gmail."""
    data = document_path(doc).read_bytes()
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO documents (gmail_message_id, filename, sha256, storage_path, sender,
                                       subject, received_at)
                VALUES (:message_id, :filename, :sha256, :storage_path, :sender, :subject,
                        :received_at)
                RETURNING id
                """
            ),
            {
                "message_id": f"corpus-{doc.doc_id}",
                "filename": doc.filename,
                "sha256": hashlib.sha256(data).hexdigest(),
                "storage_path": doc.filename,
                "sender": doc.email.sender,
                "subject": doc.email.subject,
                "received_at": doc.email.received_at,
            },
        ).scalar_one()


def last_answer(raw_output: dict[str, Any] | None) -> str | None:
    """Content of the final model answer stored with an extraction."""
    completions = (raw_output or {}).get("completions") or []
    return completions[-1]["content"] if completions else None


def read_prediction(engine: Engine, doc_id: str, document_id: UUID) -> Prediction:
    """Final state of a processed document, as eval compares it with ground truth."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT d.status, d.skip_reason, d.failure_reason, x.id AS extraction_id,
                       x.normalized, e.raw_output, e.latency_ms, e.input_tokens, e.output_tokens
                FROM documents d {LATEST_EXTRACTION}
                LEFT JOIN extractions e ON e.id = x.id
                WHERE d.id = :id
                """
            ),
            {"id": document_id},
        ).one()
        flags = conn.execute(
            text(
                """
                SELECT DISTINCT check_id FROM check_results
                WHERE extraction_id = :id AND status = 'fail' ORDER BY check_id
                """
            ),
            {"id": row.extraction_id},
        ).scalars()
        flags = tuple(flags)
    normalized = row.normalized or {}
    invoice = (
        predicted_invoice(NormalizedInvoice.model_validate(normalized["invoice"]))
        if normalized.get("invoice")
        else None
    )
    reason = row.skip_reason or row.failure_reason
    outcome = row.status + (f": {reason}" if reason else "")
    raw = row.raw_output if isinstance(row.raw_output, dict) else json.loads(row.raw_output or "{}")
    return Prediction(
        doc_id=doc_id,
        document_type=normalized.get("document_type"),
        invoice=invoice,
        status=row.status if row.status in FINAL_STATUSES else None,
        latency_ms=row.latency_ms,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        outcome=outcome,
        raw_output=last_answer(raw),
        flags=flags,
        reason=reason,
    )


class PipelinePredictor:
    def __init__(
        self,
        extraction_provider: ExtractionProvider,
        settings: Settings,
        admin_url: str,
        log: Callable[[str], None] = lambda line: print(line, flush=True),
    ) -> None:
        self.extraction_provider = extraction_provider
        # docs/SPEC.md section 14: auto-approve is switched on inside eval only.
        self.settings = settings.model_copy(update={"auto_approve_enabled": True})
        self.admin_url = admin_url
        self.log = log
        self.provider = extraction_provider.name
        self.model = (
            f"{extraction_provider.model_for('text')}+{extraction_provider.model_for('vision')}"
        )

    def predict(self, docs: Sequence[GroundTruth]) -> list[Prediction]:
        """Run the full pipeline on a fresh database, one document at a time in corpus order."""
        store = LocalDocumentStore(DOCUMENTS_DIR)
        predictions = []
        with temporary_database(self.admin_url, prefix="eval") as engine:
            seed_vendors(engine, load_known_vendors())
            for index, doc in enumerate(docs, start=1):
                document_id = insert_document(engine, doc)
                try:
                    run_once(engine, store, self.extraction_provider, self.settings)
                except ProviderFatal as exc:
                    raise EvalAborted(str(exc)) from exc
                prediction = read_prediction(engine, doc.doc_id, document_id)
                self.log(f"[{index}/{len(docs)}] {doc.doc_id}: {prediction.outcome}")
                predictions.append(prediction)
        return predictions
