import hashlib
from collections.abc import Callable, Sequence
from typing import Protocol

from app.config import Settings
from app.extraction.normalize import NormalizedInvoice, normalize_extraction
from app.extraction.pipeline import ExtractionError, run_extraction
from app.extraction.provider import Completion, ExtractionProvider
from corpus.models import GroundTruth
from corpus.storage import document_path
from eval.metrics import PredictedInvoice, PredictedLineItem, Prediction


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


def usage(completions: Sequence[Completion]) -> dict[str, int | None]:
    """Latency and tokens summed over all model calls for one document."""

    def total(values: list[int | None]) -> int | None:
        known = [value for value in values if value is not None]
        return sum(known) if known else None

    return {
        "latency_ms": total([c.latency_ms for c in completions]),
        "input_tokens": total([c.input_tokens for c in completions]),
        "output_tokens": total([c.output_tokens for c in completions]),
    }


class LlmPredictor:
    def __init__(
        self,
        extraction_provider: ExtractionProvider,
        settings: Settings,
        log: Callable[[str], None] = lambda line: print(line, flush=True),
    ) -> None:
        self.extraction_provider = extraction_provider
        self.settings = settings
        self.log = log
        self.provider = extraction_provider.name
        self.model = (
            f"{extraction_provider.model_for('text')}+{extraction_provider.model_for('vision')}"
        )

    def predict(self, docs: Sequence[GroundTruth]) -> list[Prediction]:
        """Run the extraction pipeline on every document in corpus order."""
        seen: set[str] = set()
        predictions = []
        for index, doc in enumerate(docs, start=1):
            prediction = self._predict_one(doc, seen)
            self.log(f"[{index}/{len(docs)}] {doc.doc_id}: {prediction.outcome}")
            predictions.append(prediction)
        return predictions

    def _predict_one(self, doc: GroundTruth, seen: set[str]) -> Prediction:
        data = document_path(doc).read_bytes()
        # The worker skips byte-identical files before any LLM call; do the same here.
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            return Prediction(doc.doc_id, None, None, None, outcome="skipped: duplicate_file")
        seen.add(digest)

        try:
            result = run_extraction(data, self.extraction_provider, self.settings)
        except ExtractionError as exc:
            if exc.fatal:
                raise EvalAborted(str(exc)) from exc
            return Prediction(
                doc.doc_id,
                None,
                None,
                None,
                outcome=f"stopped: {exc.reason}",
                raw_output=exc.completions[-1].content if exc.completions else None,
                **usage(exc.completions),
            )

        extraction = result.extraction
        invoice = (
            predicted_invoice(normalize_extraction(extraction))
            if extraction.document_type == "invoice"
            else None
        )
        return Prediction(
            doc.doc_id,
            extraction.document_type,
            invoice,
            None,
            outcome=f"extracted ({result.path}, {len(result.completions)} call(s))",
            raw_output=result.completions[-1].content,
            **usage(result.completions),
        )
