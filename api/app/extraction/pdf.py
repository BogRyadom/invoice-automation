import io
from dataclasses import dataclass
from typing import Any, Literal

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pdfplumber.utils.exceptions import PdfminerException

PDF_MAGIC = b"%PDF-"
# Fewer visible characters than this means the PDF has no usable text layer (a scan).
MIN_TEXT_CHARS = 20
RENDER_DPI = 150
JPEG_QUALITY = 85
# Text smaller than this, or light text that is not on a dark background, is invisible to a
# human reader. It is removed before the LLM sees the page (prompt injection defence).
MIN_VISIBLE_FONT_SIZE = 5.0
LIGHT_TEXT_LUMINANCE = 0.9
DARK_BACKGROUND_LUMINANCE = 0.5

RejectReason = Literal["unsupported_type", "too_large", "unreadable_pdf", "encrypted_pdf"]


class FileRejected(Exception):
    def __init__(self, reason: RejectReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: RejectReason = reason


def inspect_pdf(data: bytes, *, max_bytes: int, max_pages: int) -> int:
    """Check type, size, encryption and page count before any LLM call. Returns the page count."""
    if not data.startswith(PDF_MAGIC):
        raise FileRejected("unsupported_type", "file does not start with %PDF-")
    if len(data) > max_bytes:
        raise FileRejected("too_large", f"{len(data)} bytes, limit {max_bytes}")
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        reason: RejectReason = (
            "encrypted_pdf" if exc.err_code == pdfium_c.FPDF_ERR_PASSWORD else "unreadable_pdf"
        )
        raise FileRejected(reason, str(exc)) from exc
    try:
        pages = len(document)
    finally:
        document.close()
    if pages == 0:
        raise FileRejected("unreadable_pdf", "document has no pages")
    if pages > max_pages:
        raise FileRejected("too_large", f"{pages} pages, limit {max_pages}")
    return pages


@dataclass(frozen=True)
class TextLayer:
    pages: list[str]
    hidden_chars: int


def luminance(color: Any) -> float | None:
    """Relative luminance of a PDF fill colour (gray, RGB or CMYK); None when unknown."""
    if color is None:
        return 0.0
    if isinstance(color, int | float):
        return float(color)
    if not isinstance(color, list | tuple) or not all(isinstance(c, int | float) for c in color):
        return None
    if len(color) == 1:
        return float(color[0])
    if len(color) == 3:
        red, green, blue = color
    elif len(color) == 4:
        cyan, magenta, yellow, black = color
        red, green, blue = ((1 - c) * (1 - black) for c in (cyan, magenta, yellow))
    else:
        return None
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def dark_boxes(page: Any) -> list[tuple[float, float, float, float]]:
    """Filled rectangles dark enough for light text on them to be readable."""
    boxes = []
    for rect in page.rects:
        shade = luminance(rect.get("non_stroking_color"))
        if rect.get("fill") and shade is not None and shade < DARK_BACKGROUND_LUMINANCE:
            boxes.append((rect["x0"], rect["top"], rect["x1"], rect["bottom"]))
    return boxes


def is_hidden(char: dict[str, Any], boxes: list[tuple[float, float, float, float]]) -> bool:
    """True for characters a human cannot see: tiny, or light on a light background."""
    if char["size"] < MIN_VISIBLE_FONT_SIZE:
        return True
    shade = luminance(char.get("non_stroking_color"))
    if shade is None or shade < LIGHT_TEXT_LUMINANCE:
        return False
    x = (char["x0"] + char["x1"]) / 2
    y = (char["top"] + char["bottom"]) / 2
    return not any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in boxes)


def tidy_layout(text: str) -> str:
    """Drop blank lines, trailing spaces and the common indent of layout-preserving text."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    indent = min((len(line) - len(line.lstrip()) for line in lines), default=0)
    return "\n".join(line[indent:] for line in lines)


def extract_text(data: bytes) -> TextLayer:
    """Visible text of every page via pdfplumber, columns kept apart with spaces."""
    pages = []
    hidden = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                boxes = dark_boxes(page)
                hidden += sum(
                    1 for char in page.chars if char["text"].strip() and is_hidden(char, boxes)
                )
                visible = page.filter(
                    lambda obj, boxes=boxes: (
                        obj.get("object_type") != "char" or not is_hidden(obj, boxes)
                    )
                )
                pages.append(tidy_layout(visible.extract_text(layout=True) or ""))
    except PdfminerException as exc:
        raise FileRejected("unreadable_pdf", str(exc)) from exc
    return TextLayer(pages=pages, hidden_chars=hidden)


def has_text_layer(pages: list[str]) -> bool:
    """True when the pages carry enough text to skip the vision path."""
    return sum(len("".join(page.split())) for page in pages) >= MIN_TEXT_CHARS


def render_pages(data: bytes, dpi: int = RENDER_DPI) -> list[bytes]:
    """Render every page to a grayscale JPEG for the vision path."""
    document = pdfium.PdfDocument(data)
    try:
        images = []
        for page in document:
            image = page.render(scale=dpi / 72).to_pil().convert("L")
            page.close()
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
            images.append(buffer.getvalue())
        return images
    finally:
        document.close()
