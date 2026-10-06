from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Spacer, Table, TableStyle

from corpus.layouts.common import (
    BASE,
    CONTENT_WIDTH,
    INK,
    MONO,
    RULE,
    SHADE,
    TITLE_CENTER,
    build_pdf,
    paragraph,
)
from corpus.models import RenderSpec

RECEIPT_SIZE = (80 * mm, 200 * mm)
RECEIPT_MARGIN = 6 * mm


def contract(spec: RenderSpec) -> bytes:
    """Service agreement: title, numbered clauses, signature lines."""
    flowables: list[Flowable] = [paragraph(spec.title or "", TITLE_CENTER), Spacer(1, 6 * mm)]
    for clause in spec.paragraphs:
        flowables += [paragraph(clause), Spacer(1, 3 * mm)]
    signatures = Table(
        [["______________________", "______________________"], *spec.table_rows],
        colWidths=[CONTENT_WIDTH / 2] * 2,
    )
    signatures.setStyle(
        TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, 0), 24)])
    )
    flowables.append(signatures)
    return build_pdf(flowables, title=spec.title or "", author="")


def price_list(spec: RenderSpec) -> bytes:
    """Product table with a header row; no invoice number, dates or totals."""
    header, *rows = spec.table_rows
    table = Table(
        [list(header), *map(list, rows)],
        colWidths=[28 * mm, CONTENT_WIDTH - 78 * mm, 20 * mm, 30 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), ["white", SHADE]),
            ]
        )
    )
    flowables: list[Flowable] = [paragraph(spec.title or "", TITLE_CENTER), Spacer(1, 4 * mm)]
    flowables += [paragraph(line, BASE) for line in spec.paragraphs]
    flowables += [Spacer(1, 4 * mm), table]
    return build_pdf(flowables, title=spec.title or "", author="")


def receipt(spec: RenderSpec) -> bytes:
    """Narrow till receipt in a monospaced font."""
    width = RECEIPT_SIZE[0] - 2 * RECEIPT_MARGIN
    table = Table([list(row) for row in spec.table_rows], colWidths=[width - 22 * mm, 22 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Courier"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    head, *tail = spec.paragraphs
    flowables: list[Flowable] = [
        paragraph(spec.title or "", MONO),
        paragraph(head, MONO),
        Spacer(1, 3 * mm),
        table,
        Spacer(1, 3 * mm),
    ]
    flowables += [paragraph(line, MONO) for line in tail]
    return build_pdf(
        flowables, title=spec.title or "", author="", pagesize=RECEIPT_SIZE, margin=RECEIPT_MARGIN
    )
