from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

AmountStyle = Literal["en", "de", "de_int"]
DateStyle = Literal["iso", "us_long", "day_mon", "dmy_slash", "mdy_slash", "dmy_dot"]

CENT = Decimal("0.01")
NUMERIC_DATE_STYLES: frozenset[DateStyle] = frozenset({"iso", "dmy_slash", "mdy_slash", "dmy_dot"})
# Fixed English month names so output does not depend on the process locale.
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def money(value: Decimal) -> Decimal:
    """Round to cents, half up."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _group_thousands(digits: str, separator: str) -> str:
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return separator.join(groups)


def format_amount(value: Decimal, style: AmountStyle) -> str:
    """Format a non-negative amount as printed on a document."""
    if value < 0:
        raise ValueError("amounts are printed without sign")
    if style == "de_int":
        if value != value.to_integral_value():
            raise ValueError(f"{value} is not a whole amount")
        return _group_thousands(str(int(value)), ".")
    whole, cents = f"{money(value):.2f}".split(".")
    if style == "en":
        return f"{_group_thousands(whole, ',')}.{cents}"
    return f"{_group_thousands(whole, '.')},{cents}"


def format_quantity(value: Decimal, style: AmountStyle) -> str:
    """Format a quantity without trailing zeros, e.g. 2 or 7.5."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",") if style != "en" else text


def format_date(value: date, style: DateStyle) -> str:
    """Format a date in one of the printed styles used by the corpus."""
    match style:
        case "iso":
            return value.isoformat()
        case "us_long":
            return f"{MONTHS[value.month - 1]} {value.day}, {value.year}"
        case "day_mon":
            return f"{value.day:02d} {MONTHS[value.month - 1][:3]} {value.year}"
        case "dmy_slash":
            return f"{value:%d/%m/%Y}"
        case "mdy_slash":
            return f"{value:%m/%d/%Y}"
        case "dmy_dot":
            return f"{value:%d.%m.%Y}"


def is_ambiguous_date(value: date, style: DateStyle) -> bool:
    """True when a numeric date could be read with day and month swapped (W1)."""
    return style in NUMERIC_DATE_STYLES and value.day <= 12 and value.month <= 12
