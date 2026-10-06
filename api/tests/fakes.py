from typing import Any

from app.extraction.contract import Extraction
from app.extraction.provider import Completion, ExtractionPath


# Extraction provider that replays prepared answers or raises prepared errors.
class ScriptedProvider:
    name = "fake"

    def __init__(self, answers: list[str | Exception], max_images: int = 3) -> None:
        self.answers = list(answers)
        self.max_images = max_images
        self.calls: list[tuple[ExtractionPath, list[dict[str, Any]]]] = []

    def model_for(self, path: ExtractionPath) -> str:
        """Fake model id per path."""
        return f"fake-{path}"

    def complete(self, messages: list[dict[str, Any]], path: ExtractionPath) -> Completion:
        """Return the next prepared answer."""
        self.calls.append((path, messages))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Completion(
            content=answer,
            model=self.model_for(path),
            latency_ms=5,
            input_tokens=100,
            output_tokens=50,
            finish_reason="stop",
        )


NOT_AN_INVOICE = Extraction(
    document_type="other",
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
).model_dump_json()
