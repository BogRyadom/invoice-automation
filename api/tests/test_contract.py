import pytest
from pydantic import ValidationError

from app.extraction.contract import Extraction

SPEC_EXAMPLE = {
    "document_type": "invoice",
    "vendor_name_raw": "ACME Supplies Inc.",
    "vendor_tax_id_raw": None,
    "invoice_number_raw": "INV-2026-0142",
    "invoice_date_raw": "03/04/2026",
    "due_date_raw": None,
    "currency_raw": "$",
    "subtotal_raw": "1,200.00",
    "discount_raw": None,
    "shipping_raw": None,
    "tax_lines": [{"label": "VAT 10%", "amount_raw": "120.00"}],
    "tax_inclusive_note_raw": None,
    "total_raw": "1,320.00",
    "line_items": [
        {
            "description": "Widget",
            "quantity_raw": "2",
            "unit_price_raw": "600.00",
            "amount_raw": "1,200.00",
        }
    ],
}


def test_spec_example_is_valid() -> None:
    extraction = Extraction.model_validate(SPEC_EXAMPLE)

    assert extraction.total_raw == "1,320.00"
    assert extraction.tax_lines[0].amount_raw == "120.00"


def test_confidence_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Extraction.model_validate({**SPEC_EXAMPLE, "confidence": 0.97})


def test_unknown_document_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Extraction.model_validate({**SPEC_EXAMPLE, "document_type": "receipt"})


def test_every_field_is_required() -> None:
    payload = dict(SPEC_EXAMPLE)
    del payload["tax_inclusive_note_raw"]

    with pytest.raises(ValidationError):
        Extraction.model_validate(payload)
