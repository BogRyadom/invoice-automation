from collections.abc import Callable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, HRFlowable, Paragraph, Spacer, Table, TableStyle

from app.extraction.contract import Extraction
from corpus.layouts.common import (
    ACCENT,
    BASE,
    CENTER_SMALL,
    CONTENT_WIDTH,
    HIDDEN,
    INK,
    MONO,
    MUTED,
    RULE,
    SHADE,
    SMALL,
    TITLE_CENTER,
    TITLE_LEFT,
    TITLE_RIGHT,
    bold,
    build_pdf,
    key_value_table,
    lines_paragraph,
    paragraph,
)
from corpus.layouts.labels import Labels, labels_for
from corpus.models import Layout, RenderSpec

BAND_NAME = ParagraphStyle(
    "band_name",
    parent=BASE,
    fontName="Helvetica-Bold",
    fontSize=15,
    leading=18,
    textColor=colors.white,
)
BAND_TITLE = ParagraphStyle("band_title", parent=TITLE_RIGHT, fontSize=16, textColor=colors.white)
MONO_CELL = ParagraphStyle("mono_cell", parent=MONO)


def vendor_lines(printed: Extraction, spec: RenderSpec, labels: Labels) -> list[str]:
    """Vendor name, address and tax id as printed."""
    lines = [printed.vendor_name_raw or "", *spec.vendor_address]
    if printed.vendor_tax_id_raw:
        lines.append(f"{labels.tax_id}: {printed.vendor_tax_id_raw}")
    return lines


def meta_rows(printed: Extraction, spec: RenderSpec, labels: Labels) -> list[tuple[str, str]]:
    """Invoice number, dates and, unless shown as a symbol, the currency code."""
    rows = [
        (labels.invoice_number, printed.invoice_number_raw or ""),
        (labels.invoice_date, printed.invoice_date_raw or ""),
    ]
    if printed.due_date_raw:
        rows.append((labels.due_date, printed.due_date_raw))
    if spec.currency_display == "code":
        rows.append((labels.currency, printed.currency_raw or ""))
    return rows


def with_symbol(label: str, printed: Extraction, spec: RenderSpec) -> str:
    """Append the currency symbol to a label when the document shows no currency code."""
    return f"{label} ({printed.currency_raw})" if spec.currency_display == "symbol" else label


def totals_rows(printed: Extraction, spec: RenderSpec, labels: Labels) -> list[tuple[str, str]]:
    """Totals block rows in printed order."""
    rows = []
    if printed.subtotal_raw:
        rows.append((labels.subtotal, printed.subtotal_raw))
    if printed.discount_raw:
        rows.append((labels.discount, printed.discount_raw))
    if printed.shipping_raw:
        rows.append((labels.shipping, printed.shipping_raw))
    rows.extend((line.label, line.amount_raw) for line in printed.tax_lines)
    rows.append((with_symbol(labels.total, printed, spec), printed.total_raw or ""))
    return rows


def items_table(
    printed: Extraction,
    spec: RenderSpec,
    labels: Labels,
    *,
    font: str = "Helvetica",
    numbered: bool = False,
    zebra: bool = False,
    header_background: colors.Color | None = None,
) -> Table:
    """Line items table that repeats its header on every page."""
    cell_style = MONO_CELL if font == "Courier" else BASE
    header = [
        labels.description,
        labels.quantity,
        labels.unit_price,
        with_symbol(labels.amount, printed, spec),
    ]
    fixed = [18 * mm, 28 * mm, 30 * mm]
    widths = [CONTENT_WIDTH - sum(fixed), *fixed]
    if numbered:
        header = [labels.item_no, *header]
        widths = [10 * mm, widths[0] - 10 * mm, *fixed]
    rows: list[list[object]] = [list(header)]
    for index, item in enumerate(printed.line_items, start=1):
        row: list[object] = [
            Paragraph(escape(item.description or ""), cell_style),
            item.quantity_raw or "",
            item.unit_price_raw or "",
            item.amount_raw or "",
        ]
        rows.append([str(index), *row] if numbered else row)

    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), bold(font)),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("ALIGN", (-3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
    ]
    if zebra:
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SHADE]))
    if header_background is not None:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), header_background),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ]
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(style))
    return table


def totals_table(
    printed: Extraction,
    spec: RenderSpec,
    labels: Labels,
    *,
    font: str = "Helvetica",
    boxed: bool = False,
) -> Table:
    """Right-aligned totals with the grand total in bold."""
    table = Table(
        [list(row) for row in totals_rows(printed, spec, labels)],
        colWidths=[58 * mm, 32 * mm],
        hAlign="RIGHT",
    )
    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, -1), (-1, -1), bold(font)),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
    ]
    if boxed:
        style += [("BOX", (0, 0), (-1, -1), 0.8, ACCENT), ("BACKGROUND", (0, 0), (-1, -1), SHADE)]
    table.setStyle(TableStyle(style))
    return table


def footer(printed: Extraction, spec: RenderSpec) -> list[Flowable]:
    """Tax inclusion note, free-text notes and hidden text."""
    flowables: list[Flowable] = [Spacer(1, 6 * mm)]
    if printed.tax_inclusive_note_raw:
        flowables.append(paragraph(printed.tax_inclusive_note_raw))
    flowables.extend(paragraph(note, SMALL) for note in spec.notes)
    if spec.hidden_text:
        flowables += [Spacer(1, 4 * mm), paragraph(spec.hidden_text, HIDDEN)]
    return flowables


def two_columns(left: Flowable, right: Flowable, split: float = 0.5) -> Table:
    """Place two flowables side by side, top-aligned."""
    table = Table([[left, right]], colWidths=[CONTENT_WIDTH * split, CONTENT_WIDTH * (1 - split)])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def stacked_meta(rows: list[tuple[str, str]]) -> Table:
    """Meta fields as columns with a small label above each value."""
    cells = [
        Paragraph(
            f'<font size="7" color="#6b7280">{escape(label)}</font><br/><b>{escape(value)}</b>',
            BASE,
        )
        for label, value in rows
    ]
    table = Table([cells], colWidths=[CONTENT_WIDTH / len(cells)] * len(cells))
    table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, RULE),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def classic(printed: Extraction, spec: RenderSpec, labels: Labels) -> list[Flowable]:
    """Vendor top left, title top right, meta table, grid items."""
    meta = key_value_table(meta_rows(printed, spec, labels), h_align="RIGHT")
    return [
        two_columns(
            lines_paragraph(vendor_lines(printed, spec, labels), BASE),
            Paragraph(labels.title, TITLE_RIGHT),
            split=0.6,
        ),
        Spacer(1, 8 * mm),
        two_columns(lines_paragraph([labels.bill_to, *spec.bill_to], BASE), meta),
        Spacer(1, 8 * mm),
        items_table(printed, spec, labels),
        Spacer(1, 4 * mm),
        totals_table(printed, spec, labels),
        *footer(printed, spec),
    ]


def band(printed: Extraction, spec: RenderSpec, labels: Labels) -> list[Flowable]:
    """Coloured header band, stacked meta, striped items, boxed totals."""
    head = Table(
        [
            [
                Paragraph(escape(printed.vendor_name_raw or ""), BAND_NAME),
                Paragraph(labels.title, BAND_TITLE),
            ]
        ],
        colWidths=[CONTENT_WIDTH * 0.65, CONTENT_WIDTH * 0.35],
    )
    head.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    details = vendor_lines(printed, spec, labels)[1:]
    return [
        head,
        Spacer(1, 2 * mm),
        paragraph(" | ".join(details), SMALL),
        Spacer(1, 6 * mm),
        stacked_meta(meta_rows(printed, spec, labels)),
        Spacer(1, 6 * mm),
        lines_paragraph([labels.bill_to, *spec.bill_to], BASE),
        Spacer(1, 6 * mm),
        items_table(printed, spec, labels, zebra=True, header_background=ACCENT),
        Spacer(1, 4 * mm),
        totals_table(printed, spec, labels, boxed=True),
        *footer(printed, spec),
    ]


def compact(printed: Extraction, spec: RenderSpec, labels: Labels) -> list[Flowable]:
    """Monospaced, plain-text style invoice."""
    rule = paragraph("-" * 88, MONO)
    meta = [f"{label}: {value}" for label, value in meta_rows(printed, spec, labels)]
    return [
        lines_paragraph(vendor_lines(printed, spec, labels), MONO),
        Spacer(1, 4 * mm),
        paragraph(labels.title, MONO),
        lines_paragraph(meta, MONO, bold_first=False),
        Spacer(1, 3 * mm),
        lines_paragraph([labels.bill_to, *spec.bill_to], MONO),
        rule,
        items_table(printed, spec, labels, font="Courier"),
        rule,
        totals_table(printed, spec, labels, font="Courier"),
        *footer(printed, spec),
    ]


def split(printed: Extraction, spec: RenderSpec, labels: Labels) -> list[Flowable]:
    """Title first, sender and recipient side by side, meta strip."""
    return [
        Paragraph(labels.title, TITLE_LEFT),
        Spacer(1, 6 * mm),
        two_columns(
            lines_paragraph([labels.sender, *vendor_lines(printed, spec, labels)], BASE),
            lines_paragraph([labels.bill_to, *spec.bill_to], BASE),
        ),
        Spacer(1, 6 * mm),
        stacked_meta(meta_rows(printed, spec, labels)),
        Spacer(1, 6 * mm),
        items_table(printed, spec, labels),
        Spacer(1, 4 * mm),
        totals_table(printed, spec, labels),
        *footer(printed, spec),
    ]


def ledger(printed: Extraction, spec: RenderSpec, labels: Labels) -> list[Flowable]:
    """Centred letterhead, numbered items."""
    details = vendor_lines(printed, spec, labels)[1:]
    return [
        Paragraph(f"<b>{escape(printed.vendor_name_raw or '')}</b>", TITLE_CENTER),
        paragraph(" · ".join(details), CENTER_SMALL),
        HRFlowable(width="100%", thickness=0.8, color=MUTED, spaceBefore=4, spaceAfter=6),
        Paragraph(labels.title, TITLE_CENTER),
        Spacer(1, 6 * mm),
        two_columns(
            key_value_table(meta_rows(printed, spec, labels)),
            lines_paragraph([labels.bill_to, *spec.bill_to], BASE),
        ),
        Spacer(1, 6 * mm),
        items_table(printed, spec, labels, numbered=True),
        Spacer(1, 4 * mm),
        totals_table(printed, spec, labels),
        *footer(printed, spec),
    ]


LAYOUTS: dict[Layout, Callable[[Extraction, RenderSpec, Labels], list[Flowable]]] = {
    "classic": classic,
    "band": band,
    "compact": compact,
    "split": split,
    "ledger": ledger,
}


def render_invoice(printed: Extraction, spec: RenderSpec) -> bytes:
    """Render an invoice PDF whose text shows exactly the printed values."""
    if spec.layout is None:
        raise ValueError("invoice render spec needs a layout")
    labels = labels_for(spec.language, spec.layout)
    flowables = LAYOUTS[spec.layout](printed, spec, labels)
    return build_pdf(
        flowables,
        title=f"{labels.title} {printed.invoice_number_raw}",
        author=printed.vendor_name_raw or "",
        encrypt_password=spec.encrypt_password,
    )
