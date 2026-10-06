import random
from io import BytesIO

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from corpus.layouts.common import PRODUCER
from corpus.models import ScanSpec

CORRUPT_KEEP_BYTES = 1200
CORRUPT_NOISE_BYTES = 3000


def scan(pdf: bytes, spec: ScanSpec) -> bytes:
    """Turn a PDF into an image-only PDF that looks like a skewed, noisy scan."""
    rng = random.Random(spec.seed)
    document = pdfium.PdfDocument(pdf)
    pages: list[tuple[bytes, tuple[float, float]]] = []
    try:
        for page in document:
            size = page.get_size()
            image = page.render(scale=spec.dpi / 72).to_pil().convert("L")
            image = image.rotate(
                spec.rotation_degrees, resample=Image.Resampling.BICUBIC, fillcolor=255
            )
            noise = Image.frombytes("L", image.size, rng.randbytes(image.width * image.height))
            image = Image.blend(image, noise, spec.noise).filter(ImageFilter.GaussianBlur(0.6))
            jpeg = BytesIO()
            image.save(jpeg, format="JPEG", quality=70)
            pages.append((jpeg.getvalue(), size))
    finally:
        document.close()

    output = BytesIO()
    canvas = Canvas(output, invariant=1, pageCompression=0)
    canvas.setCreator(PRODUCER)
    canvas.setProducer(PRODUCER)
    for jpeg_bytes, (width, height) in pages:
        canvas.setPageSize((width, height))
        canvas.drawImage(ImageReader(BytesIO(jpeg_bytes)), 0, 0, width=width, height=height)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def corrupt(pdf: bytes, seed: str) -> bytes:
    """Keep the PDF header so magic bytes still match, replace the body with noise."""
    rng = random.Random(seed)
    return pdf[:CORRUPT_KEEP_BYTES] + rng.randbytes(CORRUPT_NOISE_BYTES)
