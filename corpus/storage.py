import json
from pathlib import Path
from typing import Any

from corpus.models import GroundTruth, KnownVendor

CORPUS_DIR = Path(__file__).resolve().parent
GROUND_TRUTH_DIR = CORPUS_DIR / "ground_truth"
DOCUMENTS_DIR = CORPUS_DIR / "documents"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
VENDORS_SEED_PATH = CORPUS_DIR / "vendors_seed.json"


def to_json(data: Any) -> str:
    """Stable, human-readable JSON with a trailing newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def ground_truth_json(doc: GroundTruth) -> str:
    """Serialize one ground truth record exactly as it is stored on disk."""
    return to_json(doc.model_dump(mode="json"))


def load_manifest() -> dict[str, Any]:
    """Read the corpus manifest: processing order and file hashes."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def load_corpus() -> list[GroundTruth]:
    """Ground truth records in processing order."""
    return [
        GroundTruth.model_validate_json(
            (GROUND_TRUTH_DIR / f"{entry['doc_id']}.json").read_text(encoding="utf-8")
        )
        for entry in load_manifest()["documents"]
    ]


def load_known_vendors() -> list[KnownVendor]:
    """Vendors that exist in the registry before an eval run."""
    data = json.loads(VENDORS_SEED_PATH.read_text(encoding="utf-8"))
    return [KnownVendor.model_validate(vendor) for vendor in data]


def document_path(doc: GroundTruth) -> Path:
    """Location of the generated PDF for a ground truth record."""
    return DOCUMENTS_DIR / doc.filename
