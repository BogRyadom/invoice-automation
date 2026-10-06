from collections.abc import Sequence
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Table, TableStyle

MARGIN = 18 * mm
CONTENT_WIDTH = A4[0] - 2 * MARGIN
PRODUCER = "invoice-automation synthetic corpus"

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7280")
ACCENT = colors.HexColor("#1d4e5f")
SHADE = colors.HexColor("#eef2f4")
RULE = colors.HexColor("#c7ced4")

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=8, leading=10, textColor=MUTED)
TITLE_RIGHT = ParagraphStyle(
    "title_right",
    parent=BASE,
    fontName="Helvetica-Bold",
    fontSize=20,
    leading=24,
    alignment=TA_RIGHT,
)
TITLE_LEFT = ParagraphStyle("title_left", parent=TITLE_RIGHT, alignment=0)
TITLE_CENTER = ParagraphStyle("title_center", parent=TITLE_RIGHT, fontSize=14, alignment=TA_CENTER)
CENTER = ParagraphStyle("center", parent=BASE, alignment=TA_CENTER)
CENTER_SMALL = ParagraphStyle("center_small", parent=SMALL, alignment=TA_CENTER)
MONO = ParagraphStyle("mono", fontName="Courier", fontSize=9, leading=11, textColor=INK)
# Prompt-injection text: tiny and nearly invisible, but present in the text layer.
HIDDEN = ParagraphStyle(
    "hidden", parent=BASE, fontSize=4, leading=5, textColor=colors.HexColor("#f4f4f4")
)


def lines_paragraph(
    lines: Sequence[str], style: ParagraphStyle, bold_first: bool = True
) -> Paragraph:
    """Paragraph with one line per entry, optionally bolding the first one."""
    parts = [escape(line) for line in lines]
    if bold_first and parts:
        parts[0] = f"<b>{parts[0]}</b>"
    return Paragraph("<br/>".join(parts), style)


def paragraph(text: str, style: ParagraphStyle = BASE) -> Paragraph:
    """Paragraph from plain text."""
    return Paragraph(escape(text), style)


def key_value_table(
    rows: Sequence[tuple[str, str]], font: str = "Helvetica", h_align: str = "LEFT"
) -> Table:
    """Two-column label/value table."""
    table = Table([list(row) for row in rows], colWidths=[34 * mm, 46 * mm], hAlign=h_align)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), bold(font)),
                ("FONTNAME", (1, 0), (1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def bold(font: str) -> str:
    """Bold variant of a standard font."""
    return f"{font}-Bold"


def build_pdf(
    flowables: Sequence[Flowable],
    *,
    title: str,
    author: str,
    pagesize: tuple[float, float] = A4,
    margin: float = MARGIN,
    encrypt_password: str | None = None,
) -> bytes:
    """Render flowables into deterministic, uncompressed PDF bytes."""
    buffer = BytesIO()
    encrypt = (
        StandardEncryption(
            encrypt_password, ownerPassword=f"{encrypt_password}-owner", strength=128
        )
        if encrypt_password
        else None
    )
    document = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=title,
        author=author,
        creator=PRODUCER,
        producer=PRODUCER,
        invariant=1,
        # Uncompressed so the bytes do not depend on the zlib build of the platform.
        pageCompression=0,
        encrypt=encrypt,
    )
    document.build(list(flowables))
    return buffer.getvalue()
