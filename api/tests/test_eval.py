import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from corpus.models import GroundTruth
from corpus.storage import load_corpus
from eval.metrics import Prediction, Ratio, Stats, evaluate, stats
from eval.predictors import OraclePredictor
from eval.report import write_results
from eval.run_eval import NOT_IMPLEMENTED, main, oracle_failures

RUN_DATE = date(2026, 10, 6)


@pytest.fixture(scope="module")
def corpus() -> list[GroundTruth]:
    return load_corpus()


@pytest.fixture
def oracle(corpus: list[GroundTruth]) -> list[Prediction]:
    return OraclePredictor().predict(corpus)


def with_change(predictions: list[Prediction], doc_id: str, **changes: object) -> list[Prediction]:
    return [replace(p, **changes) if p.doc_id == doc_id else p for p in predictions]


def wrong_total(prediction: Prediction) -> Prediction:
    assert prediction.invoice is not None
    return replace(
        prediction, invoice=prediction.invoice.model_copy(update={"total": Decimal("1")})
    )


def test_oracle_scores_perfectly(corpus: list[GroundTruth], oracle: list[Prediction]) -> None:
    report = evaluate(corpus, oracle)

    assert oracle_failures(report) == []
    assert report.field_accuracy["total"] == Ratio(28, 28)
    assert report.document_type_accuracy == Ratio(31, 31)
    assert report.review_rate == Ratio(13, 28)
    assert report.auto_approve_precision == Ratio(15, 15)
    assert report.errors_sent_to_review == Ratio(0, 0)


def test_wrong_value_that_is_auto_approved_lowers_precision(
    corpus: list[GroundTruth], oracle: list[Prediction]
) -> None:
    predictions = [wrong_total(p) if p.doc_id == "clean_01" else p for p in oracle]

    report = evaluate(corpus, predictions)

    assert report.field_accuracy["total"] == Ratio(27, 28)
    assert report.auto_approve_precision == Ratio(14, 15)
    assert report.errors_sent_to_review == Ratio(0, 1)
    assert oracle_failures(report) != []


def test_wrong_value_sent_to_review_counts_as_caught(
    corpus: list[GroundTruth], oracle: list[Prediction]
) -> None:
    predictions = [
        replace(wrong_total(p), status="needs_review") if p.doc_id == "clean_01" else p
        for p in oracle
    ]

    report = evaluate(corpus, predictions)

    assert report.errors_sent_to_review == Ratio(1, 1)
    assert report.auto_approve_precision == Ratio(14, 14)
    assert report.review_rate == Ratio(14, 28)


def test_invoice_classified_as_other(corpus: list[GroundTruth], oracle: list[Prediction]) -> None:
    predictions = with_change(
        oracle, "clean_02", document_type="other", invoice=None, status="skipped"
    )

    report = evaluate(corpus, predictions)

    assert report.document_type_accuracy == Ratio(30, 31)
    assert report.field_accuracy["invoice_number"] == Ratio(27, 28)
    assert report.review_rate == Ratio(13, 27)
    assert report.errors_sent_to_review == Ratio(0, 0)
    wrong = next(d for d in report.documents if d.doc_id == "clean_02")
    assert wrong.predicted_status == "skipped"
    assert "total" in wrong.wrong_fields


def test_missing_line_item(corpus: list[GroundTruth], oracle: list[Prediction]) -> None:
    def drop_last_item(prediction: Prediction) -> Prediction:
        assert prediction.invoice is not None
        items = prediction.invoice.line_items[:-1]
        return replace(
            prediction, invoice=prediction.invoice.model_copy(update={"line_items": items})
        )

    predictions = [drop_last_item(p) if p.doc_id == "clean_03" else p for p in oracle]

    report = evaluate(corpus, predictions)

    assert report.line_item_count_match == Ratio(27, 28)
    assert report.line_item_amounts_match == Ratio(27, 28)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], Stats(count=0, mean=None, median=None)),
        ([100, None, 300, 200], Stats(count=3, mean=200, median=200)),
        ([1, 2], Stats(count=2, mean=2, median=2)),
        ([10, 20, 31, 40], Stats(count=4, mean=25, median=26)),
    ],
)
def test_stats(values: list[int | None], expected: Stats) -> None:
    assert stats(values) == expected


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(Ratio(2, 3), "66.7%"), (Ratio(1, 1), "100.0%"), (Ratio(0, 5), "0.0%"), (Ratio(0, 0), "n/a")],
)
def test_ratio_percent(ratio: Ratio, expected: str) -> None:
    assert ratio.percent() == expected


def test_write_results_names_files_and_never_overwrites(
    corpus: list[GroundTruth], oracle: list[Prediction], tmp_path: Path
) -> None:
    report = evaluate(corpus, oracle)

    first = write_results(
        report,
        provider="groq",
        model="openai/gpt-oss-120b",
        run_date=RUN_DATE,
        results_dir=tmp_path,
    )
    second = write_results(
        report,
        provider="groq",
        model="openai/gpt-oss-120b",
        run_date=RUN_DATE,
        results_dir=tmp_path,
    )

    assert first.name == "2026-10-06_groq_openai-gpt-oss-120b.json"
    assert second.name == "2026-10-06_groq_openai-gpt-oss-120b_2.json"
    assert first.with_suffix(".md").exists()
    data = json.loads(first.read_text(encoding="utf-8"))
    assert data["corpus"] == {"size": 34, "synthetic": True}
    assert data["review_rate"] == {"hits": 13, "total": 28}
    assert len(data["documents"]) == 34


def test_oracle_results_are_never_written(
    corpus: list[GroundTruth], oracle: list[Prediction], tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        write_results(
            evaluate(corpus, oracle),
            provider="oracle",
            model="ground-truth",
            run_date=RUN_DATE,
            results_dir=tmp_path,
        )


def test_cli_oracle_run_passes_and_saves_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--predictor", "oracle"], today=RUN_DATE, results_dir=tmp_path) == 0

    output = capsys.readouterr().out
    assert "| Review rate | 46.4% (13/28) |" in output
    assert "Harness check passed" in output
    assert list(tmp_path.iterdir()) == []


def test_cli_llm_run_is_not_available_yet(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Stage 2"):
        main([], today=RUN_DATE, results_dir=tmp_path)
    assert "make eval-oracle" in NOT_IMPLEMENTED
