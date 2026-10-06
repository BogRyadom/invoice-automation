# Fictional vendors. Tax ids use the non-existent country prefix ZZ and e-mail
# addresses use the reserved .example domain, so nothing can match a real entity.

from dataclasses import dataclass
from decimal import Decimal

from corpus.formatting import AmountStyle


@dataclass(frozen=True)
class Vendor:
    canonical_name: str
    key: str
    known: bool
    tax_id: str | None
    address: tuple[str, ...]
    email: str
    currency: str
    tax_label: str
    tax_rate: Decimal
    amount_style: AmountStyle
    catalog: tuple[tuple[str, Decimal], ...]


BILL_TO = (
    "Lumen Harbor Trading Ltd",
    "Accounts Payable",
    "7 Saltmarsh Row",
    "Port Elnor, ZZ 20417",
)
BILL_TO_DE = (
    "Lumen Harbor Trading Ltd",
    "Kreditorenbuchhaltung",
    "Salzwiese 7",
    "ZZ-20417 Port Elnor",
)

QUORRIN = Vendor(
    canonical_name="Quorrin Office Supply Inc.",
    key="quorrin office supply",
    known=True,
    tax_id="ZZ482910376",
    address=("1180 Tallow Creek Road", "Brandmoor, ZZ 60231"),
    email="billing@quorrin-office.example",
    currency="USD",
    tax_label="Sales Tax 8.25%",
    tax_rate=Decimal("0.0825"),
    amount_style="en",
    catalog=(
        ("A4 copy paper, 80 gsm (box of 5 reams)", Decimal("42.50")),
        ("Gel pens, black (pack of 12)", Decimal("9.80")),
        ("Stapler, heavy duty", Decimal("24.90")),
        ("Archive boxes (pack of 10)", Decimal("31.00")),
        ("Whiteboard markers (set of 4)", Decimal("7.45")),
        ("Desk organiser, mesh", Decimal("18.60")),
        ("Toner cartridge TN-450", Decimal("89.00")),
        ("Sticky notes 76x76 (12 pads)", Decimal("11.20")),
        ("Ring binders A4 (pack of 10)", Decimal("27.50")),
        ("Envelopes C4 (box of 250)", Decimal("38.90")),
        ("Lever arch files (pack of 5)", Decimal("16.75")),
        ("Highlighters, assorted (pack of 6)", Decimal("6.30")),
        ("Paper clips, 33 mm (box of 1000)", Decimal("4.95")),
        ("Laminating pouches A4 (100)", Decimal("14.40")),
        ("Correction tape (pack of 10)", Decimal("12.10")),
        ("Document wallets (pack of 50)", Decimal("19.95")),
        ("Desk calculator, 12 digit", Decimal("15.60")),
        ("Hanging file folders (pack of 25)", Decimal("21.80")),
        ("Label roll, 89x36 mm", Decimal("17.25")),
        ("Notebook A5, ruled (pack of 5)", Decimal("13.90")),
    ),
)

VELMORA = Vendor(
    canonical_name="Velmora Logistics LLC",
    key="velmora logistics",
    known=True,
    tax_id="ZZ771034592",
    address=("Unit 4, Fenwick Trade Park", "Hollins Cross, ZZ 31108"),
    email="accounts@velmora-logistics.example",
    currency="GBP",
    tax_label="VAT 20%",
    tax_rate=Decimal("0.20"),
    amount_style="en",
    catalog=(
        ("Pallet freight, Hollins Cross to Ardley", Decimal("145.00")),
        ("Same-day courier, zone 2", Decimal("38.50")),
        ("Warehouse storage (pallet-week)", Decimal("6.75")),
        ("Fuel surcharge", Decimal("22.40")),
        ("Customs documentation", Decimal("55.00")),
        ("Tail-lift delivery", Decimal("30.00")),
        ("Pick and pack (per order)", Decimal("2.85")),
        ("Shrink wrapping (per pallet)", Decimal("8.50")),
        ("Next-day parcel, up to 10 kg", Decimal("11.90")),
        ("Two-person delivery", Decimal("64.00")),
        ("Returns handling (per item)", Decimal("3.40")),
        ("Container unloading, 20 ft", Decimal("210.00")),
        ("Labelling service (per carton)", Decimal("0.95")),
        ("Pallet hire (per week)", Decimal("4.20")),
        ("Timed delivery slot", Decimal("18.00")),
        ("Weekend collection", Decimal("45.00")),
        ("Insurance cover, goods in transit", Decimal("27.50")),
        ("Cross-docking (per pallet)", Decimal("12.75")),
        ("Stock count (per hour)", Decimal("32.00")),
        ("Packaging materials", Decimal("16.60")),
    ),
)

TESSALY = Vendor(
    canonical_name="Tessaly Print Works Ltd",
    key="tessaly print works",
    known=True,
    tax_id=None,
    address=("22 Copperleaf Lane", "Wexmoor, ZZ 11873"),
    email="invoices@tessaly-print.example",
    currency="GBP",
    tax_label="VAT 20%",
    tax_rate=Decimal("0.20"),
    amount_style="en",
    catalog=(
        ("Business cards, 400 gsm (box of 500)", Decimal("34.00")),
        ("A5 flyers, gloss (pack of 1000)", Decimal("96.00")),
        ("Roll-up banner 850x2000 mm", Decimal("79.00")),
        ("Letterheads, 120 gsm (500)", Decimal("58.00")),
        ("Presentation folders (100)", Decimal("142.00")),
        ("Poster A2, matt", Decimal("12.50")),
        ("Design proof revision", Decimal("25.00")),
    ),
)

BREVIK = Vendor(
    canonical_name="Brevik Software Corp.",
    key="brevik software",
    known=True,
    tax_id="ZZ305518847",
    address=("400 Lantern Hill Avenue, Suite 12", "Cordale, ZZ 90514"),
    email="ar@brevik-software.example",
    currency="USD",
    tax_label="Sales Tax 8.25%",
    tax_rate=Decimal("0.0825"),
    amount_style="en",
    catalog=(
        ("Analytics Suite licence, annual (per seat)", Decimal("480.00")),
        ("Premium support, monthly", Decimal("350.00")),
        ("Onboarding workshop (hours)", Decimal("140.00")),
        ("Data connector add-on", Decimal("95.00")),
        ("Sandbox environment, monthly", Decimal("120.00")),
    ),
)

OSTBERG = Vendor(
    canonical_name="Ostberg Facilities Co.",
    key="ostberg facilities",
    known=True,
    tax_id=None,
    address=("58 Marrow Street", "Fenholt, ZZ 47720"),
    email="billing@ostberg-facilities.example",
    currency="USD",
    tax_label="Sales Tax 6.5%",
    tax_rate=Decimal("0.065"),
    amount_style="en",
    catalog=(
        ("Office cleaning, monthly contract", Decimal("1150.00")),
        ("Window cleaning, exterior", Decimal("240.00")),
        ("HVAC maintenance visit", Decimal("310.00")),
        ("Pest control inspection", Decimal("95.00")),
        ("Waste removal (bin lift)", Decimal("12.50")),
        ("Carpet deep clean (per room)", Decimal("85.00")),
    ),
)

HALVREN = Vendor(
    canonical_name="Halvren Maschinenbau GmbH",
    key="halvren maschinenbau",
    known=True,
    tax_id="ZZ918273645",
    address=("Eisenstraße 14", "ZZ-73012 Brakfeld"),
    email="rechnung@halvren-maschinenbau.example",
    currency="EUR",
    tax_label="MwSt. 19 %",
    tax_rate=Decimal("0.19"),
    amount_style="de",
    catalog=(
        ("Kugellager 6204-2RS", Decimal("4.80")),
        ("Antriebswelle Typ AW-40", Decimal("186.00")),
        ("Montage vor Ort (Stunden)", Decimal("78.00")),
        ("Dichtungssatz DS-12", Decimal("23.50")),
        ("Zahnriemen HTD 8M-1200", Decimal("41.20")),
        ("Wartungspauschale", Decimal("250.00")),
    ),
)

PELLUCID = Vendor(
    canonical_name="Pellucid Analytics Ltd",
    key="pellucid analytics",
    known=False,
    tax_id="ZZ640022815",
    address=("3 Harrow Quay", "Dunmere, ZZ 08861"),
    email="finance@pellucid-analytics.example",
    currency="EUR",
    tax_label="VAT 23%",
    tax_rate=Decimal("0.23"),
    amount_style="en",
    catalog=(
        ("Dashboard development (days)", Decimal("650.00")),
        ("Data quality audit", Decimal("1200.00")),
    ),
)

CORVANE = Vendor(
    canonical_name="Corvane Studio LLC",
    key="corvane studio",
    known=False,
    tax_id=None,
    address=("911 Kettle Point Drive", "Saltby, ZZ 52260"),
    email="studio@corvane.example",
    currency="USD",
    tax_label="Sales Tax 8.25%",
    tax_rate=Decimal("0.0825"),
    amount_style="en",
    catalog=(
        ("Brand identity workshop", Decimal("1450.00")),
        ("Product photography (half day)", Decimal("650.00")),
        ("Image retouching (per image)", Decimal("18.00")),
    ),
)

ALL_VENDORS = (QUORRIN, VELMORA, TESSALY, BREVIK, OSTBERG, HALVREN, PELLUCID, CORVANE)
