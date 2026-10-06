from collections.abc import Sequence
from typing import Protocol

from corpus.models import GroundTruth
from eval.metrics import Prediction


class Predictor(Protocol):
    provider: str
    model: str

    def predict(self, docs: Sequence[GroundTruth]) -> list[Prediction]:
        """Process the corpus in order and return one prediction per document."""
        ...


# Answers from ground truth. Checks the eval harness; its numbers are never published.
class OraclePredictor:
    provider = "oracle"
    model = "ground-truth"

    def predict(self, docs: Sequence[GroundTruth]) -> list[Prediction]:
        """Return the expected values for every document."""
        return [
            Prediction(
                doc_id=doc.doc_id,
                document_type=doc.document_type,
                invoice=doc.expected,
                status=doc.route.status,
            )
            for doc in docs
        ]
