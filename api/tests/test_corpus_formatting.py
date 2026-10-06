from datetime import date
from decimal import Decimal

import pytest

from corpus.definitions import normalize_invoice_number
from corpus.formatting import (
    AmountStyle,
    DateStyle,
    format_amount,
    format_date,
    format_quantity,
    is_ambiguous_date,
)


@pytest.mark.parametrize(
    ("value", "style", "expected"),
    [
        ("0.5", "en", "0.50"),
        ("1320", "en", "1,320.00"),
        ("12470.4", "en", "12,470.40"),
        ("1234567.891", "en", "1,234,567.89"),
        ("1034.35", "de", "1.034,35"),
        ("94", "de", "94,00"),
        ("1600", "de_int", "1.600"),
        ("304", "de_int", "304"),
    ],
)
def test_format_amount(value: str, style: AmountStyle, expected: str) -> None:
    assert format_amount(Decimal(value), style) == expected


@pytest.mark.parametrize(("value", "style"), [("-1", "en"), ("12.5", "de_int")])
def test_format_amount_rejects_unprintable_values(value: str, style: AmountStyle) -> None:
    with pytest.raises(ValueError):
        format_amount(Decimal(value), style)


@pytest.mark.parametrize(
    ("value", "style", "expected"),
    [("2", "en", "2"), ("7.5", "en", "7.5"), ("3.5", "de", "3,5"), ("40", "de", "40")],
)
def test_format_quantity(value: str, style: AmountStyle, expected: str) -> None:
    assert format_quantity(Decimal(value), style) == expected


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("iso", "2026-03-04"),
        ("us_long", "March 4, 2026"),
        ("day_mon", "04 Mar 2026"),
        ("dmy_slash", "04/03/2026"),
        ("mdy_slash", "03/04/2026"),
        ("dmy_dot", "04.03.2026"),
    ],
)
def test_format_date(style: DateStyle, expected: str) -> None:
    assert format_date(date(2026, 3, 4), style) == expected


@pytest.mark.parametrize(
    ("value", "style", "expected"),
    [
        (date(2026, 3, 4), "dmy_dot", True),
        (date(2026, 3, 14), "dmy_dot", False),
        (date(2026, 3, 4), "us_long", False),
        (date(2026, 12, 12), "iso", True),
    ],
)
def test_is_ambiguous_date(value: date, style: DateStyle, expected: bool) -> None:
    assert is_ambiguous_date(value, style) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("QOS-26-0412", "QOS260412"),
        ("VL/2026/1043", "VL20261043"),
        ("TPW 2026 088", "TPW2026088"),
        ("bsc-inv-00731", "BSCINV00731"),
    ],
)
def test_normalize_invoice_number(raw: str, expected: str) -> None:
    assert normalize_invoice_number(raw) == expected
