import hashlib
from collections import Counter

from corpus.definitions import build_documents, known_vendors
from corpus.models import GroundTruth
from corpus.render import render_document
from corpus.storage import (
    DOCUMENTS_DIR,
    GROUND_TRUTH_DIR,
    MANIFEST_PATH,
    VENDORS_SEED_PATH,
    ground_truth_json,
    to_json,
)


def generate() -> tuple[list[GroundTruth], dict[str, bytes]]:
    """Build ground truth, then render every PDF from its serialized JSON form."""
    documents = [
        GroundTruth.model_validate_json(ground_truth_json(doc)) for doc in build_documents()
    ]
    ids = Counter(doc.doc_id for doc in documents)
    if duplicates := [doc_id for doc_id, count in ids.items() if count > 1]:
        raise ValueError(f"duplicate doc ids: {duplicates}")
    rendered: dict[str, bytes] = {}
    for doc in documents:
        rendered[doc.doc_id] = render_document(doc, rendered)
    return documents, rendered


def manifest(documents: list[GroundTruth], rendered: dict[str, bytes]) -> dict[str, object]:
    """Processing order and SHA-256 of every generated file."""
    return {
        "corpus": "synthetic",
        "size": len(documents),
        "documents": [
            {
                "doc_id": doc.doc_id,
                "filename": doc.filename,
                "sha256": hashlib.sha256(rendered[doc.doc_id]).hexdigest(),
            }
            for doc in documents
        ],
    }


def main() -> None:
    """Regenerate ground truth JSON, PDFs, manifest and the vendor seed."""
    documents, rendered = generate()
    GROUND_TRUTH_DIR.mkdir(exist_ok=True)
    DOCUMENTS_DIR.mkdir(exist_ok=True)
    for doc in documents:
        (GROUND_TRUTH_DIR / f"{doc.doc_id}.json").write_text(
            ground_truth_json(doc), encoding="utf-8", newline="\n"
        )
        (DOCUMENTS_DIR / doc.filename).write_bytes(rendered[doc.doc_id])
    MANIFEST_PATH.write_text(to_json(manifest(documents, rendered)), encoding="utf-8", newline="\n")
    VENDORS_SEED_PATH.write_text(
        to_json([vendor.model_dump(mode="json") for vendor in known_vendors()]),
        encoding="utf-8",
        newline="\n",
    )
    groups = Counter(doc.group for doc in documents)
    print(f"Generated {len(documents)} documents: {dict(groups)}")


if __name__ == "__main__":
    main()
