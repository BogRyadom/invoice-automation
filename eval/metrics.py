from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.extraction.contract import DocumentType
from corpus.models import ExpectedInvoice, ExpectedStatus, GroundTruth

FIELDS = (
    "vendor_key",
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
)
# Required fields of check H1; auto-approve precision is measured on these.
KEY_FIELDS = ("vendor_key", "invoice_number", "invoice_date", "total", "currency")


# Same fields as ExpectedInvoice, but anything may be missing in a prediction.
class PredictedLineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class PredictedInvoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    vendor_key: str | None = None
    vendor_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    discount: Decimal | None = None
    shipping: Decimal | None = None
    tax_total: Decimal | None = None
    total: Decimal | None = None
    tax_inclusive: bool = False
    line_items: tuple[PredictedLineItem, ...] = ()


@dataclass(frozen=True)
class Prediction:
    doc_id: str
    document_type: DocumentType | None
    invoice: PredictedInvoice | None
    status: ExpectedStatus | None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    outcome: str | None = None
    raw_output: str | None = None


@dataclass(frozen=True)
class Ratio:
    hits: int
    total: int

    def percent(self) -> str:
        """Percentage with one decimal, or n/a when nothing was measured."""
        if self.total == 0:
            return "n/a"
        tenths = (self.hits * 1000 + self.total // 2) // self.total
        return f"{tenths // 10}.{tenths % 10}%"


@dataclass(frozen=True)
class Stats:
    count: int
    mean: int | None
    median: int | None


@dataclass(frozen=True)
class DocumentResult:
    doc_id: str
    expected_status: ExpectedStatus
    predicted_status: ExpectedStatus | None
    outcome: str | None
    wrong_fields: tuple[str, ...]
    raw_output: str | None


@dataclass(frozen=True)
class EvalReport:
    corpus_size: int
    field_accuracy: dict[str, Ratio]
    line_item_count_match: Ratio
    line_item_amounts_match: Ratio
    document_type_accuracy: Ratio
    review_rate: Ratio
    auto_approve_precision: Ratio
    errors_sent_to_review: Ratio
    latency_ms: Stats
    input_tokens: Stats
    output_tokens: Stats
    documents: tuple[DocumentResult, ...]


def wrong_fields(
    expected: ExpectedInvoice, predicted: PredictedInvoice | None, fields: Sequence[str] = FIELDS
) -> tuple[str, ...]:
    """Fields that differ from ground truth; all of them when nothing was predicted."""
    if predicted is None:
        return tuple(fields)
    return tuple(name for name in fields if getattr(expected, name) != getattr(predicted, name))


def stats(values: Sequence[int | None]) -> Stats:
    """Integer mean and median over the values that were reported."""
    present = sorted(value for value in values if value is not None)
    if not present:
        return Stats(count=0, mean=None, median=None)
    count = len(present)
    middle = count // 2
    median = present[middle] if count % 2 else (present[middle - 1] + present[middle] + 1) // 2
    return Stats(count=count, mean=(sum(present) + count // 2) // count, median=median)


def evaluate(docs: Sequence[GroundTruth], predictions: Sequence[Prediction]) -> EvalReport:
    """Compute every metric from docs/SPEC.md section 14."""
    by_id = {prediction.doc_id: prediction for prediction in predictions}
    missing = Prediction(doc_id="", document_type=None, invoice=None, status=None)

    field_hits = dict.fromkeys(FIELDS, 0)
    extracted = 0
    count_hits = amount_hits = 0
    type_hits = typed = 0
    reviewed = routed = 0
    precise = auto_approved = 0
    caught = with_errors = 0
    results = []

    for doc in docs:
        prediction = by_id.get(doc.doc_id, missing)
        mismatches: tuple[str, ...] = ()

        if doc.document_type is not None:
            typed += 1
            type_hits += prediction.document_type == doc.document_type

        if doc.expected is not None:
            extracted += 1
            mismatches = wrong_fields(doc.expected, prediction.invoice)
            for name in FIELDS:
                field_hits[name] += name not in mismatches
            predicted_items = prediction.invoice.line_items if prediction.invoice else ()
            count_hits += len(predicted_items) == len(doc.expected.line_items)
            amount_hits += [item.amount for item in predicted_items] == [
                item.amount for item in doc.expected.line_items
            ]
            # Routing appears in Stage 3; without a status this metric stays n/a.
            if prediction.invoice is not None and mismatches and prediction.status is not None:
                with_errors += 1
                caught += prediction.status == "needs_review"

        if prediction.status in ("auto_approved", "needs_review"):
            routed += 1
            reviewed += prediction.status == "needs_review"
        if prediction.status == "auto_approved":
            auto_approved += 1
            precise += doc.expected is not None and not wrong_fields(
                doc.expected, prediction.invoice, KEY_FIELDS
            )

        results.append(
            DocumentResult(
                doc_id=doc.doc_id,
                expected_status=doc.route.status,
                predicted_status=prediction.status,
                outcome=prediction.outcome,
                wrong_fields=mismatches,
                raw_output=prediction.raw_output,
            )
        )

    return EvalReport(
        corpus_size=len(docs),
        field_accuracy={name: Ratio(field_hits[name], extracted) for name in FIELDS},
        line_item_count_match=Ratio(count_hits, extracted),
        line_item_amounts_match=Ratio(amount_hits, extracted),
        document_type_accuracy=Ratio(type_hits, typed),
        review_rate=Ratio(reviewed, routed),
        auto_approve_precision=Ratio(precise, auto_approved),
        errors_sent_to_review=Ratio(caught, with_errors),
        latency_ms=stats([p.latency_ms for p in predictions]),
        input_tokens=stats([p.input_tokens for p in predictions]),
        output_tokens=stats([p.output_tokens for p in predictions]),
        documents=tuple(results),
    )
