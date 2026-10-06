from collections.abc import Mapping

from corpus.effects import corrupt, scan
from corpus.layouts.invoice import render_invoice
from corpus.layouts.other import contract, price_list, receipt
from corpus.models import GroundTruth


def render_document(doc: GroundTruth, rendered: Mapping[str, bytes]) -> bytes:
    """Render one corpus document. Copies and corrupted files reuse earlier output."""
    spec = doc.render
    match spec.kind:
        case "invoice":
            if doc.printed is None:
                raise ValueError(f"{doc.doc_id}: invoice without printed values")
            pdf = render_invoice(doc.printed, spec)
        case "contract":
            pdf = contract(spec)
        case "price_list":
            pdf = price_list(spec)
        case "receipt":
            pdf = receipt(spec)
        case "exact_copy":
            return rendered[source_id(doc)]
        case "corrupted":
            return corrupt(rendered[source_id(doc)], seed=doc.doc_id)
    return scan(pdf, spec.scan) if spec.scan else pdf


def source_id(doc: GroundTruth) -> str:
    """Document this one is derived from."""
    if doc.render.source_doc_id is None:
        raise ValueError(f"{doc.doc_id}: {doc.render.kind} needs source_doc_id")
    return doc.render.source_doc_id
