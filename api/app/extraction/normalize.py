# Deterministic normalization of raw extracted values (docs/SPEC.md sections 6 and 8).
# Ambiguities are reported as issues for the checks in Stage 3, never resolved silently.

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.extraction.contract import Extraction

NumberStyle = Literal["en", "de"]
DateOrder = Literal["DMY", "MDY"]
IssueCode = Literal[
    "unparseable", "ambiguous_date", "ambiguous_number_format", "currency_symbol_only"
]

LEGAL_SUFFIXES = frozenset({"inc", "llc", "ltd", "gmbh", "corp", "co", "sarl", "bv", "pty"})

CURRENCY_CODES = frozenset(
    [
        "USD",
        "EUR",
        "GBP",
        "CHF",
        "JPY",
        "CNY",
        "CAD",
        "AUD",
        "NZD",
        "SEK",
        "NOK",
        "DKK",
        "ISK",
        "PLN",
        "CZK",
        "HUF",
        "RON",
        "BGN",
        "TRY",
        "UAH",
        "INR",
        "SGD",
        "HKD",
        "KRW",
        "THB",
        "MYR",
        "IDR",
        "PHP",
        "ZAR",
        "MXN",
        "BRL",
        "ARS",
        "CLP",
        "COP",
        "AED",
        "SAR",
        "ILS",
        "EGP",
    ]
)
CURRENCY_SYMBOLS = {
    "$": "USD",
    "US$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
    "₺": "TRY",
    "₴": "UAH",
    "zł": "PLN",
}

MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan", "januar", "jänner"),
            ("february", "feb", "februar"),
            ("march", "mar", "märz", "maerz", "mär", "mrz"),
            ("april", "apr"),
            ("may", "mai"),
            ("june", "jun", "juni"),
            ("july", "jul", "juli"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct", "oktober", "okt"),
            ("november", "nov"),
            ("december", "dec", "dezember", "dez"),
        ],
        start=1,
    )
    for name in names
}

YEAR_FIRST = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
NUMERIC_DATE = re.compile(r"(\d{1,2})([./-])(\d{1,2})\2(\d{4}|\d{2})")
DAY_MONTH_YEAR = re.compile(r"(\d{1,2})\.?\s+([^\W\d_]+)\.?,?\s+(\d{4})")
MONTH_DAY_YEAR = re.compile(r"([^\W\d_]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")


class Issue(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    code: IssueCode
    message: str


class NormalizedLineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class NormalizedTaxLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    amount: Decimal | None


class NormalizedInvoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    vendor_name: str | None
    vendor_key: str | None
    vendor_tax_id: str | None
    invoice_number: str | None
    invoice_number_normalized: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    subtotal: Decimal | None
    discount: Decimal | None
    shipping: Decimal | None
    tax_lines: tuple[NormalizedTaxLine, ...]
    tax_total: Decimal | None
    tax_inclusive: bool
    total: Decimal | None
    line_items: tuple[NormalizedLineItem, ...]
    number_style: NumberStyle | None
    issues: tuple[Issue, ...]


def vendor_key(name: str) -> str:
    """SPEC 8: NFKC, lowercase, drop punctuation, collapse spaces, strip trailing legal suffixes."""
    text = unicodedata.normalize("NFKC", name).lower()
    text = "".join(char for char in text if not unicodedata.category(char).startswith("P"))
    words = text.split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def compact_key(raw: str) -> str:
    """SPEC 8 invoice number rule: uppercase, drop spaces and separators, keep leading zeros."""
    return "".join(char for char in unicodedata.normalize("NFKC", raw).upper() if char.isalnum())


def _amount_body(raw: str) -> tuple[bool, str]:
    text = unicodedata.normalize("NFKC", raw).strip()
    negative = (
        text.startswith("-") or text.endswith("-") or (text.startswith("(") and text.endswith(")"))
    )
    return negative, re.sub(r"[^\d.,]", "", text)


def number_evidence(raw: str) -> NumberStyle | None:
    """Which style a single printed number proves, or None if it fits both."""
    _, body = _amount_body(raw)
    if "," in body and "." in body:
        return "en" if body.rfind(".") > body.rfind(",") else "de"
    for separator, style in ((".", "en"), (",", "de")):
        count = body.count(separator)
        if count > 1:
            return "de" if separator == "." else "en"
        if count == 1 and len(body.split(separator)[1]) != 3:
            return style
    return None


def detect_number_style(raws: Iterable[str | None]) -> tuple[NumberStyle | None, bool]:
    """Number style of the whole document and whether it stayed ambiguous (W2)."""
    present = [raw for raw in raws if raw]
    votes = Counter(style for raw in present if (style := number_evidence(raw)))
    if len(votes) == 1:
        return next(iter(votes)), False
    if not votes:
        # Ambiguous only if some number has a separator that the two styles read differently.
        return None, any(re.search(r"[.,]", _amount_body(raw)[1]) for raw in present)
    return votes.most_common(1)[0][0], True


def parse_amount(raw: str, style: NumberStyle | None) -> Decimal:
    """Parse a printed number. With no known style, every separator is a thousands separator."""
    negative, body = _amount_body(raw)
    if not re.search(r"\d", body):
        raise ValueError(f"no digits in {raw!r}")
    if style is None:
        digits = body.replace(".", "").replace(",", "")
    elif style == "en":
        digits = body.replace(",", "")
    else:
        digits = body.replace(".", "").replace(",", ".")
    try:
        value = Decimal(digits)
    except InvalidOperation as exc:
        raise ValueError(f"cannot parse {raw!r}") from exc
    return -value if negative else value


def _year(text: str) -> int:
    return int(text) + 2000 if len(text) == 2 else int(text)


def _month(name: str) -> int:
    month = MONTHS.get(name.lower())
    if month is None:
        raise ValueError(f"unknown month {name!r}")
    return month


def numeric_order(raw: str) -> DateOrder | None:
    """Day/month order that a numeric date proves on its own (one part above 12)."""
    match = NUMERIC_DATE.fullmatch(unicodedata.normalize("NFKC", raw).strip())
    if not match:
        return None
    first, second = int(match.group(1)), int(match.group(3))
    if first > 12 >= second:
        return "DMY"
    if second > 12 >= first:
        return "MDY"
    return None


def parse_date(raw: str, order_hint: DateOrder) -> tuple[date, bool]:
    """Parse a printed date. Returns the date and whether day and month could be swapped (W1)."""
    text = unicodedata.normalize("NFKC", raw).strip()
    if match := YEAR_FIRST.fullmatch(text):
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))), False
    if match := NUMERIC_DATE.fullmatch(text):
        first, second, year = int(match.group(1)), int(match.group(3)), _year(match.group(4))
        proven = numeric_order(text)
        order = proven or order_hint
        day, month = (first, second) if order == "DMY" else (second, first)
        return date(year, month, day), proven is None
    if match := DAY_MONTH_YEAR.fullmatch(text):
        return date(int(match.group(3)), _month(match.group(2)), int(match.group(1))), False
    if match := MONTH_DAY_YEAR.fullmatch(text):
        return date(int(match.group(3)), _month(match.group(1)), int(match.group(2))), False
    raise ValueError(f"unrecognised date {raw!r}")


def date_order_hint(
    raws: Iterable[str | None], currency: str | None, vendor_format: DateOrder | None
) -> DateOrder:
    """Order for ambiguous numeric dates: vendor memory, then other dates, separator, currency."""
    if vendor_format:
        return vendor_format
    present = [raw for raw in raws if raw]
    for raw in present:
        if proven := numeric_order(raw):
            return proven
    if any("." in raw for raw in present if NUMERIC_DATE.fullmatch(raw.strip())):
        return "DMY"
    return "MDY" if currency == "USD" else "DMY"


def parse_currency(raw: str) -> tuple[str, bool]:
    """ISO 4217 code and whether the document showed only a symbol (W3)."""
    text = unicodedata.normalize("NFKC", raw).strip()
    for token in re.findall(r"(?<![A-Za-z])[A-Za-z]{3}(?![A-Za-z])", text):
        if token.upper() in CURRENCY_CODES:
            return token.upper(), False
    symbol = text.replace(" ", "")
    if symbol in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[symbol], True
    raise ValueError(f"unknown currency {raw!r}")


def normalize_extraction(
    extraction: Extraction, vendor_date_format: DateOrder | None = None
) -> NormalizedInvoice:
    """Turn raw printed values into typed values plus the issues found on the way."""
    issues: list[Issue] = []

    def issue(field: str, code: IssueCode, message: str) -> None:
        issues.append(Issue(field=field, code=code, message=message))

    numeric_raws = [
        extraction.subtotal_raw,
        extraction.discount_raw,
        extraction.shipping_raw,
        extraction.total_raw,
        *(line.amount_raw for line in extraction.tax_lines),
        *(raw for item in extraction.line_items for raw in (item.unit_price_raw, item.amount_raw)),
    ]
    style, ambiguous_style = detect_number_style(numeric_raws)
    if ambiguous_style:
        issue("amounts", "ambiguous_number_format", "decimal separator cannot be determined")

    def amount(field: str, raw: str | None, own_style: bool = False) -> Decimal | None:
        if raw is None:
            return None
        try:
            return parse_amount(raw, style or (number_evidence(raw) if own_style else None))
        except ValueError as exc:
            issue(field, "unparseable", str(exc))
            return None

    currency = None
    if extraction.currency_raw:
        try:
            currency, symbol_only = parse_currency(extraction.currency_raw)
        except ValueError as exc:
            issue("currency", "unparseable", str(exc))
        else:
            if symbol_only:
                issue("currency", "currency_symbol_only", f"only {extraction.currency_raw!r} shown")

    hint = date_order_hint(
        (extraction.invoice_date_raw, extraction.due_date_raw), currency, vendor_date_format
    )

    def parsed_date(field: str, raw: str | None) -> date | None:
        if raw is None:
            return None
        try:
            value, ambiguous = parse_date(raw, hint)
        except ValueError as exc:
            issue(field, "unparseable", str(exc))
            return None
        if ambiguous and vendor_date_format is None:
            issue(field, "ambiguous_date", f"{raw!r} read as {hint}")
        return value

    discount = amount("discount", extraction.discount_raw)
    tax_lines = tuple(
        NormalizedTaxLine(label=line.label, amount=amount("tax_lines", line.amount_raw))
        for line in extraction.tax_lines
    )
    tax_amounts = [line.amount for line in tax_lines]
    return NormalizedInvoice(
        vendor_name=extraction.vendor_name_raw,
        vendor_key=vendor_key(extraction.vendor_name_raw) if extraction.vendor_name_raw else None,
        vendor_tax_id=compact_key(extraction.vendor_tax_id_raw)
        if extraction.vendor_tax_id_raw
        else None,
        invoice_number=extraction.invoice_number_raw,
        invoice_number_normalized=compact_key(extraction.invoice_number_raw)
        if extraction.invoice_number_raw
        else None,
        invoice_date=parsed_date("invoice_date", extraction.invoice_date_raw),
        due_date=parsed_date("due_date", extraction.due_date_raw),
        currency=currency,
        subtotal=amount("subtotal", extraction.subtotal_raw),
        discount=abs(discount) if discount is not None else None,
        shipping=amount("shipping", extraction.shipping_raw),
        tax_lines=tax_lines,
        tax_total=sum(tax_amounts, Decimal(0)) if tax_amounts and None not in tax_amounts else None,
        tax_inclusive=extraction.tax_inclusive_note_raw is not None,
        total=amount("total", extraction.total_raw),
        line_items=tuple(
            NormalizedLineItem(
                description=item.description,
                quantity=amount("line_items", item.quantity_raw, own_style=True),
                unit_price=amount("line_items", item.unit_price_raw),
                amount=amount("line_items", item.amount_raw),
            )
            for item in extraction.line_items
        ),
        number_style=style,
        issues=tuple(issues),
    )
