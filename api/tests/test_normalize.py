from datetime import date
from decimal import Decimal

import pytest

from app.extraction.normalize import (
    DateOrder,
    NumberStyle,
    compact_key,
    date_order_hint,
    detect_number_style,
    normalize_extraction,
    parse_amount,
    parse_currency,
    parse_date,
    vendor_key,
)
from corpus.models import GroundTruth
from corpus.storage import load_corpus

FLAG_FOR_ISSUE = {
    "ambiguous_date": "W1",
    "ambiguous_number_format": "W2",
    "currency_symbol_only": "W3",
}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Quorrin Office Supply Inc.", "quorrin office supply"),
        ("BREVIK SOFTWARE CORP.", "brevik software"),
        ("Ostberg Facilities Co.", "ostberg facilities"),
        ("Moss & Finch Pty Ltd", "moss finch"),
        ("Halvren  Maschinenbau GmbH", "halvren maschinenbau"),
        ("\uff31\uff55\uff4f\uff52\uff52\uff49\uff4e Inc", "quorrin"),
        ("Co-operative Bakers", "cooperative bakers"),
    ],
)
def test_vendor_key(name: str, expected: str) -> None:
    assert vendor_key(name) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("QOS-26-0412", "QOS260412"), ("vl/2026/1043", "VL20261043"), ("00451", "00451")],
)
def test_compact_key(raw: str, expected: str) -> None:
    assert compact_key(raw) == expected


@pytest.mark.parametrize(
    ("raws", "style", "ambiguous"),
    [
        (["1,320.00", "120.00"], "en", False),
        (["1.034,35", "165,15"], "de", False),
        (["1.600", "304", "1.904"], None, True),
        (["350", "95"], None, False),
        (["1,200", "12.50"], "en", False),
        (["1.234,56", "1,234.56"], "de", True),
        (["1 234,56"], "de", False),
        ([None, "7.5"], "en", False),
    ],
)
def test_detect_number_style(
    raws: list[str | None], style: NumberStyle | None, ambiguous: bool
) -> None:
    assert detect_number_style(raws) == (style, ambiguous)


@pytest.mark.parametrize(
    ("raw", "style", "expected"),
    [
        ("1,320.00", "en", "1320.00"),
        ("1.034,35", "de", "1034.35"),
        ("1.600", None, "1600"),
        ("$ 457.95", "en", "457.95"),
        ("(240.00)", "en", "-240.00"),
        ("-82.57", "en", "-82.57"),
        ("1\u00a0234,5", "de", "1234.5"),
    ],
)
def test_parse_amount(raw: str, style: NumberStyle | None, expected: str) -> None:
    assert parse_amount(raw, style) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "USD", "1.2.3,4,5"])
def test_parse_amount_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_amount(raw, "en")


@pytest.mark.parametrize(
    ("raw", "hint", "expected", "ambiguous"),
    [
        ("2026-03-04", "MDY", date(2026, 3, 4), False),
        ("14/04/2026", "MDY", date(2026, 4, 14), False),
        ("04/14/2026", "DMY", date(2026, 4, 14), False),
        ("04.03.2026", "DMY", date(2026, 3, 4), True),
        ("04/03/2026", "MDY", date(2026, 4, 3), True),
        ("4/3/26", "DMY", date(2026, 3, 4), True),
        ("March 17, 2026", "DMY", date(2026, 3, 17), False),
        ("05 May 2026", "MDY", date(2026, 5, 5), False),
        ("4. März 2026", "MDY", date(2026, 3, 4), False),
        ("Sept 2nd, 2026", "DMY", date(2026, 9, 2), False),
    ],
)
def test_parse_date(raw: str, hint: DateOrder, expected: date, ambiguous: bool) -> None:
    assert parse_date(raw, hint) == (expected, ambiguous)


@pytest.mark.parametrize("raw", ["32/01/2026", "next Tuesday", "2026-13-01", "Smarch 3, 2026"])
def test_parse_date_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_date(raw, "DMY")


@pytest.mark.parametrize(
    ("raws", "currency", "vendor_format", "expected"),
    [
        (["04/03/2026", "18/03/2026"], "USD", None, "DMY"),
        (["04.03.2026"], "USD", None, "DMY"),
        (["04/03/2026"], "USD", None, "MDY"),
        (["04/03/2026"], "GBP", None, "DMY"),
        (["04/03/2026"], "GBP", "MDY", "MDY"),
    ],
)
def test_date_order_hint(
    raws: list[str], currency: str, vendor_format: DateOrder | None, expected: DateOrder
) -> None:
    assert date_order_hint(raws, currency, vendor_format) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("USD", ("USD", False)),
        ("eur", ("EUR", False)),
        ("Currency: GBP", ("GBP", False)),
        ("$", ("USD", True)),
        ("€", ("EUR", True)),
        ("US$", ("USD", True)),
    ],
)
def test_parse_currency(raw: str, expected: tuple[str, bool]) -> None:
    assert parse_currency(raw) == expected


@pytest.mark.parametrize("raw", ["Dollars", "XYZ", "#"])
def test_parse_currency_rejects_unknown(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_currency(raw)


def test_saved_vendor_date_format_removes_ambiguity() -> None:
    corpus = {doc.doc_id: doc for doc in load_corpus()}
    printed = corpus["european_01"].printed
    assert printed is not None

    normalized = normalize_extraction(printed, vendor_date_format="DMY")

    assert normalized.invoice_date == date(2026, 3, 4)
    assert not [issue for issue in normalized.issues if issue.code == "ambiguous_date"]


@pytest.mark.parametrize(
    "doc", [doc for doc in load_corpus() if doc.expected is not None], ids=lambda doc: doc.doc_id
)
def test_printed_values_normalize_to_ground_truth(doc: GroundTruth) -> None:
    assert doc.printed is not None and doc.expected is not None
    expected = doc.expected

    normalized = normalize_extraction(doc.printed)

    assert normalized.vendor_key == expected.vendor_key
    assert normalized.vendor_tax_id == expected.vendor_tax_id
    assert normalized.invoice_number_normalized == expected.invoice_number
    assert normalized.invoice_date == expected.invoice_date
    assert normalized.due_date == expected.due_date
    assert normalized.currency == expected.currency
    assert normalized.subtotal == expected.subtotal
    assert normalized.discount == expected.discount
    assert normalized.shipping == expected.shipping
    assert normalized.tax_total == expected.tax_total
    assert normalized.total == expected.total
    assert normalized.tax_inclusive == expected.tax_inclusive
    assert [(i.quantity, i.unit_price, i.amount) for i in normalized.line_items] == [
        (i.quantity, i.unit_price, i.amount) for i in expected.line_items
    ]
    assert not [issue for issue in normalized.issues if issue.code == "unparseable"]
    issue_flags = {FLAG_FOR_ISSUE[issue.code] for issue in normalized.issues}
    assert issue_flags == set(doc.route.flags) & set(FLAG_FOR_ISSUE.values())
