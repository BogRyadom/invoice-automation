from dataclasses import dataclass, replace

from corpus.models import Layout


@dataclass(frozen=True)
class Labels:
    title: str
    invoice_number: str
    invoice_date: str
    due_date: str
    currency: str
    bill_to: str
    sender: str
    item_no: str
    description: str
    quantity: str
    unit_price: str
    amount: str
    subtotal: str
    discount: str
    shipping: str
    total: str
    tax_id: str


ENGLISH = Labels(
    title="INVOICE",
    invoice_number="Invoice No.",
    invoice_date="Invoice date",
    due_date="Due date",
    currency="Currency",
    bill_to="Bill to",
    sender="From",
    item_no="#",
    description="Description",
    quantity="Qty",
    unit_price="Unit price",
    amount="Amount",
    subtotal="Subtotal",
    discount="Discount",
    shipping="Shipping",
    total="Total",
    tax_id="Tax ID",
)

GERMAN = Labels(
    title="RECHNUNG",
    invoice_number="Rechnungsnummer",
    invoice_date="Rechnungsdatum",
    due_date="Fällig am",
    currency="Währung",
    bill_to="Rechnungsempfänger",
    sender="Absender",
    item_no="Pos.",
    description="Bezeichnung",
    quantity="Menge",
    unit_price="Einzelpreis",
    amount="Betrag",
    subtotal="Zwischensumme",
    discount="Rabatt",
    shipping="Versand",
    total="Gesamtbetrag",
    tax_id="USt-IdNr.",
)

# English layouts use different wording so extraction cannot rely on one fixed label set.
ENGLISH_OVERRIDES: dict[Layout, dict[str, str]] = {
    "band": {"invoice_number": "Invoice #", "invoice_date": "Issue date", "total": "Total due"},
    "compact": {
        "quantity": "QTY",
        "unit_price": "PRICE",
        "amount": "AMOUNT",
        "subtotal": "SUBTOTAL",
        "discount": "DISCOUNT",
        "shipping": "SHIPPING",
        "total": "TOTAL",
    },
    "ledger": {
        "invoice_number": "Document No.",
        "invoice_date": "Date of issue",
        "due_date": "Payment due",
        "quantity": "Quantity",
        "total": "Amount due",
    },
}


def labels_for(language: str, layout: Layout) -> Labels:
    """Labels for a language, with per-layout wording for English."""
    if language == "de":
        return GERMAN
    return replace(ENGLISH, **ENGLISH_OVERRIDES.get(layout, {}))
