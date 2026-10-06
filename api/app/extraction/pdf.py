import io
from typing import Literal

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pdfplumber.utils.exceptions import PdfminerException

PDF_MAGIC = b"%PDF-"
# Fewer visible characters than this means the PDF has no usable text layer (a scan).
MIN_TEXT_CHARS = 20
RENDER_DPI = 150
JPEG_QUALITY = 85

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


def tidy_layout(text: str) -> str:
    """Drop blank lines, trailing spaces and the common indent of layout-preserving text."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    indent = min((len(line) - len(line.lstrip()) for line in lines), default=0)
    return "\n".join(line[indent:] for line in lines)


def extract_text(data: bytes) -> list[str]:
    """Text layer of every page via pdfplumber, keeping columns apart with spaces."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return [tidy_layout(page.extract_text(layout=True) or "") for page in pdf.pages]
    except PdfminerException as exc:
        raise FileRejected("unreadable_pdf", str(exc)) from exc


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
