import hashlib
import re
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.extraction.contract import Extraction
from corpus.build import generate
from corpus.definitions import known_vendors
from corpus.models import GroundTruth
from corpus.storage import (
    DOCUMENTS_DIR,
    GROUND_TRUTH_DIR,
    document_path,
    ground_truth_json,
    load_corpus,
    load_known_vendors,
    load_manifest,
)

# Composition required by docs/SPEC.md section 14. Year-first ISO dates are never ambiguous.
SPEC_COMPOSITION = {
    "clean": 13,
    "complex": 4,
    "european": 3,
    "multipage": 2,
    "scan": 4,
    "duplicate": 2,
    "not_invoice": 3,
    "broken": 2,
    "injection": 1,
    "arithmetic_error": 1,
}
AMOUNT_TOLERANCE = Decimal("0.02")
AUTO_APPROVE_MAX_TOTAL = Decimal("5000")
CORPUS_AS_OF = date(2026, 10, 1)
NUMERIC_DATE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")
HAS_DECIMALS = re.compile(r"[.,]\d{2}$")


@pytest.fixture(scope="module")
def corpus() -> list[GroundTruth]:
    return load_corpus()


@pytest.fixture(scope="module")
def by_id(corpus: list[GroundTruth]) -> dict[str, GroundTruth]:
    return {doc.doc_id: doc for doc in corpus}


@pytest.fixture(scope="module")
def regenerated() -> tuple[list[GroundTruth], dict[str, bytes]]:
    return generate()


def pdf_text(path: Path, password: str | None = None) -> tuple[int, str]:
    """Page count and whitespace-normalized text layer of a PDF."""
    pdf = pdfium.PdfDocument(path, password=password)
    try:
        texts = []
        for page in pdf:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
        return len(pdf), collapse(" ".join(texts))
    finally:
        pdf.close()


def collapse(text: str) -> str:
    return " ".join(text.split())


def printed_values(printed: Extraction) -> list[str]:
    values = [
        printed.vendor_name_raw,
        printed.vendor_tax_id_raw,
        printed.invoice_number_raw,
        printed.invoice_date_raw,
        printed.due_date_raw,
        printed.currency_raw,
        printed.subtotal_raw,
        printed.discount_raw,
        printed.shipping_raw,
        printed.tax_inclusive_note_raw,
        printed.total_raw,
    ]
    for line in printed.tax_lines:
        values += [line.label, line.amount_raw]
    for item in printed.line_items:
        values += [item.description, item.quantity_raw, item.unit_price_raw, item.amount_raw]
    return [value for value in values if value]


def derived_flags(doc: GroundTruth, known_keys: set[str]) -> set[str]:
    """Flags implied by the document content, computed independently of the generator."""
    assert doc.printed is not None and doc.expected is not None
    printed, expected = doc.printed, doc.expected
    flags = set()

    if expected.subtotal is None or expected.tax_total is None:
        flags.add("W7")
    else:
        tax = Decimal(0) if expected.tax_inclusive else expected.tax_total
        computed = expected.subtotal - (expected.discount or 0) + (expected.shipping or 0) + tax
        if abs(computed - expected.total) > AMOUNT_TOLERANCE:
            flags.add("H2")
    if expected.subtotal is not None and expected.line_items:
        items_sum = sum(item.amount or Decimal(0) for item in expected.line_items)
        if abs(items_sum - expected.subtotal) > AMOUNT_TOLERANCE:
            flags.add("H3")

    for raw, value in (
        (printed.invoice_date_raw, expected.invoice_date),
        (printed.due_date_raw, expected.due_date),
    ):
        if raw and value and NUMERIC_DATE.match(raw) and value.day <= 12 and value.month <= 12:
            flags.add("W1")

    amounts = [printed.subtotal_raw, printed.total_raw]
    amounts += [line.amount_raw for line in printed.tax_lines]
    amounts += [item.amount_raw for item in printed.line_items]
    if not any(HAS_DECIMALS.search(amount) for amount in amounts if amount):
        flags.add("W2")

    if printed.currency_raw and not printed.currency_raw.isalpha():
        flags.add("W3")
    if expected.vendor_key not in known_keys:
        flags.add("W4")
    if doc.extraction_path == "vision":
        flags.add("W5")
    if expected.total > AUTO_APPROVE_MAX_TOTAL:
        flags.add("W6")
    if doc.route.duplicate_of:
        flags.add("H5")
    if doc.render.hidden_text:
        flags.add("W9")
    return flags


def test_composition_matches_spec(corpus: list[GroundTruth]) -> None:
    assert Counter(doc.group for doc in corpus) == SPEC_COMPOSITION
    assert len(corpus) == 35


def test_manifest_matches_files(corpus: list[GroundTruth]) -> None:
    manifest = load_manifest()

    assert manifest["corpus"] == "synthetic"
    assert manifest["size"] == len(corpus)
    assert {path.stem for path in GROUND_TRUTH_DIR.glob("*.json")} == {d.doc_id for d in corpus}
    assert {path.name for path in DOCUMENTS_DIR.glob("*.pdf")} == {d.filename for d in corpus}
    for entry in manifest["documents"]:
        content = (DOCUMENTS_DIR / entry["filename"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], entry["filename"]


def test_generator_reproduces_ground_truth(
    corpus: list[GroundTruth], regenerated: tuple[list[GroundTruth], dict[str, bytes]]
) -> None:
    documents, _ = regenerated

    assert [ground_truth_json(doc) for doc in documents] == [
        (GROUND_TRUTH_DIR / f"{doc.doc_id}.json").read_text(encoding="utf-8") for doc in corpus
    ]
    assert load_known_vendors() == known_vendors()


def test_generator_reproduces_pdf_bytes(
    corpus: list[GroundTruth], regenerated: tuple[list[GroundTruth], dict[str, bytes]]
) -> None:
    # Scans go through PDFium rendering and JPEG encoding, which may differ across platforms.
    _, rendered = regenerated
    for doc in corpus:
        if doc.render.scan is None:
            assert rendered[doc.doc_id] == document_path(doc).read_bytes(), doc.doc_id


def test_flags_follow_from_content(corpus: list[GroundTruth]) -> None:
    known_keys = {vendor.normalized_name for vendor in load_known_vendors()}
    for doc in corpus:
        if doc.expected is None:
            continue
        assert set(doc.route.flags) == derived_flags(doc, known_keys), doc.doc_id
        assert doc.route.status == ("needs_review" if doc.route.flags else "auto_approved")


def test_dates_are_valid_for_h6(corpus: list[GroundTruth]) -> None:
    for doc in corpus:
        if doc.expected is None:
            continue
        assert doc.expected.invoice_date < CORPUS_AS_OF, doc.doc_id
        if doc.expected.due_date:
            assert doc.expected.due_date >= doc.expected.invoice_date, doc.doc_id


def test_every_route_flag_is_known(corpus: list[GroundTruth]) -> None:
    allowed = {"H2", "H5", "W1", "W2", "W3", "W4", "W5", "W6", "W7", "W9"}
    for doc in corpus:
        assert set(doc.route.flags) <= allowed, doc.doc_id


def test_duplicates_come_after_their_originals(corpus: list[GroundTruth]) -> None:
    order = [doc.doc_id for doc in corpus]
    for doc in corpus:
        if doc.route.duplicate_of:
            assert order.index(doc.route.duplicate_of) < order.index(doc.doc_id)


def test_printed_values_are_in_the_text_layer(corpus: list[GroundTruth]) -> None:
    for doc in corpus:
        if doc.extraction_path != "text" or doc.printed is None:
            continue
        _, text = pdf_text(document_path(doc))
        for value in printed_values(doc.printed):
            assert collapse(value) in text, f"{doc.doc_id}: {value!r}"


def test_injection_text_reaches_the_text_layer(by_id: dict[str, GroundTruth]) -> None:
    doc = by_id["injection_01"]
    _, text = pdf_text(document_path(doc))

    assert doc.render.hidden_text is not None
    assert collapse(doc.render.hidden_text) in text


def test_scans_have_no_text_layer(corpus: list[GroundTruth]) -> None:
    scans = [doc for doc in corpus if doc.extraction_path == "vision"]
    assert len(scans) == 4
    for doc in scans:
        pages, text = pdf_text(document_path(doc))
        assert pages >= 1
        assert text == "", doc.doc_id


def test_multipage_documents_span_pages(corpus: list[GroundTruth]) -> None:
    for doc in corpus:
        if doc.group == "multipage":
            pages, _ = pdf_text(document_path(doc))
            assert pages >= 2, doc.doc_id


def test_not_invoices_show_their_title(corpus: list[GroundTruth]) -> None:
    for doc in corpus:
        if doc.group == "not_invoice":
            assert doc.render.title is not None
            _, text = pdf_text(document_path(doc))
            assert collapse(doc.render.title) in text


def test_corrupted_pdf_keeps_magic_bytes_but_cannot_be_opened(
    by_id: dict[str, GroundTruth],
) -> None:
    path = document_path(by_id["broken_01"])

    assert path.read_bytes().startswith(b"%PDF-")
    with pytest.raises(pdfium.PdfiumError):
        pdfium.PdfDocument(path)


def test_encrypted_pdf_needs_a_password(by_id: dict[str, GroundTruth]) -> None:
    doc = by_id["broken_02"]
    assert doc.render.encrypt_password is not None and doc.printed is not None

    with pytest.raises(pdfium.PdfiumError):
        pdfium.PdfDocument(document_path(doc))
    _, text = pdf_text(document_path(doc), password=doc.render.encrypt_password)
    assert doc.printed.invoice_number_raw in text


def test_exact_duplicate_is_byte_identical(by_id: dict[str, GroundTruth]) -> None:
    copy = by_id["duplicate_01"]
    assert copy.route.duplicate_of is not None
    original = by_id[copy.route.duplicate_of]

    assert document_path(copy).read_bytes() == document_path(original).read_bytes()


def test_rerendered_duplicate_differs_only_in_bytes(by_id: dict[str, GroundTruth]) -> None:
    copy = by_id["duplicate_02"]
    assert copy.route.duplicate_of is not None
    original = by_id[copy.route.duplicate_of]

    assert document_path(copy).read_bytes() != document_path(original).read_bytes()
    assert copy.expected == original.expected


def test_vendor_seed_has_unique_keys() -> None:
    keys = [vendor.normalized_name for vendor in load_known_vendors()]

    assert len(keys) == len(set(keys))
